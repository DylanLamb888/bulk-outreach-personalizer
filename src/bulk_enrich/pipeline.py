"""Bulk deterministic enrichment pipeline and output manifest."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Protocol

from bulk_enrich import __version__
from bulk_enrich.cache import JsonCache
from bulk_enrich.config import CampaignConfig, CopyAngle, CopyTemplate, CtaVariant
from bulk_enrich.csv_io import (
    context_for_row,
    domain_for_row,
    inspect_csv,
    load_csv,
    write_enriched_csv,
)
from bulk_enrich.fetcher import FetcherSettings, HttpFetcher
from bulk_enrich.firecrawl import (
    DEFAULT_FIRECRAWL_API_URL,
    FirecrawlFetcher,
    FirecrawlSettings,
)
from bulk_enrich.extract import normalize_company_signal
from bulk_enrich.focus import CommercialFocusError, CommercialFocusTable, longest_shared_phrase_words
from bulk_enrich.hooks import TitleHookTable
from bulk_enrich.models import CompanyFact, SiteSignal
from bulk_enrich.row_fallback import facts_from_row
from bulk_enrich.renderer import render_email, render_template, word_count
from bulk_enrich.site import SiteEnricher


class DomainEnricher(Protocol):
    def enrich(self, domain: str) -> SiteSignal: ...


ProgressCallback = Callable[[int, int, str, str], None]


@dataclass(frozen=True)
class RunOptions:
    cache_dir: Path
    concurrency: int = 24
    timeout: float = 12.0
    retries: int = 1
    max_pages: int = 2
    max_response_bytes: int = 750_000
    cache_ttl_hours: float = 168.0
    refresh_cache: bool = False
    firecrawl_fallback: bool = False
    firecrawl_api_url: str = DEFAULT_FIRECRAWL_API_URL
    firecrawl_timeout: float = 30.0
    firecrawl_concurrency: int = 4
    manifest_path: Path | None = None
    ready_output_path: Path | None = None


@dataclass(frozen=True)
class RenderedCopy:
    angle_id: str
    template_id: str
    pitch: str
    subject: str
    body: str
    cta_variant_id: str
    cta: str


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(value, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def _stable_template_order(
    templates: tuple[CopyTemplate, ...], domain: str
) -> tuple[CopyTemplate, ...]:
    start = int(hashlib.sha256(domain.encode("utf-8")).hexdigest(), 16) % len(templates)
    return templates[start:] + templates[:start]


def _stable_cta(variants: tuple[CtaVariant, ...], domain: str) -> CtaVariant:
    identity = f"cta:{domain}"
    index = int(hashlib.sha256(identity.encode("utf-8")).hexdigest(), 16) % len(variants)
    return variants[index]


def _angle_for_signal(campaign: CampaignConfig, signal_type: str) -> CopyAngle:
    fallback: CopyAngle | None = None
    for angle in campaign.angles:
        if "*" in angle.signal_types:
            fallback = angle
        if signal_type in angle.signal_types:
            return angle
    assert fallback is not None
    return fallback


def _banned_phrase(value: str, banned_phrases: tuple[str, ...]) -> str:
    lowered = value.casefold()
    return next((phrase for phrase in banned_phrases if phrase in lowered), "")


def _build_copy(
    campaign: CampaignConfig,
    context: dict[str, object],
    domain: str,
    signal_type: str,
    source_evidence: str,
) -> RenderedCopy:
    angle = _angle_for_signal(campaign, signal_type)
    failures: list[str] = []
    cta_variant = _stable_cta(campaign.cta_variants, domain)
    try:
        cta = " ".join(render_template(cta_variant.text, context).strip().split())
    except KeyError as exc:
        raise ValueError(f"CTA {cta_variant.variant_id}: {exc}") from exc
    if word_count(cta) > campaign.max_cta_words:
        raise ValueError(
            f"CTA {cta_variant.variant_id} exceeds {campaign.max_cta_words} words"
        )
    banned_cta = _banned_phrase(cta, campaign.banned_phrases)
    if banned_cta:
        raise ValueError(
            f"CTA {cta_variant.variant_id} contains banned phrase '{banned_cta}'"
        )
    render_context = dict(context)
    render_context["cta"] = cta
    for template in _stable_template_order(angle.templates, domain):
        try:
            pitch = " ".join(render_template(template.pitch, context).strip().split())
            if word_count(pitch) > campaign.max_words:
                failures.append(
                    f"{template.template_id}: pitch exceeds {campaign.max_words} words"
                )
                continue
            shared_words = longest_shared_phrase_words(pitch, source_evidence)
            if shared_words > campaign.max_source_phrase_words:
                failures.append(
                    f"{template.template_id}: pitch copies {shared_words} consecutive "
                    "words from the website evidence"
                )
                continue
            subject, body = render_email(
                campaign,
                render_context,
                pitch,
                template.subject,
            )
        except KeyError as exc:
            failures.append(f"{template.template_id}: {exc}")
            continue

        if word_count(subject) > campaign.max_subject_words:
            failures.append(
                f"{template.template_id}: subject exceeds {campaign.max_subject_words} words"
            )
            continue
        if word_count(body) > campaign.max_body_words:
            failures.append(
                f"{template.template_id}: email exceeds {campaign.max_body_words} words"
            )
            continue
        banned = _banned_phrase("\n".join((subject, body)), campaign.banned_phrases)
        if banned:
            failures.append(
                f"{template.template_id}: rendered copy contains banned phrase '{banned}'"
            )
            continue
        return RenderedCopy(
            angle_id=angle.angle_id,
            template_id=template.template_id,
            pitch=pitch,
            subject=subject,
            body=body,
            cta_variant_id=cta_variant.variant_id,
            cta=cta,
        )
    raise ValueError("no safe copy template rendered: " + "; ".join(failures))


def _primary_fact(signal: SiteSignal) -> CompanyFact:
    if signal.facts:
        return signal.facts[0]
    normalized = normalize_company_signal(signal.evidence or signal.observation)
    return CompanyFact(
        signal_type=signal.signal_type or normalized.signal_type,
        focus=signal.focus or normalized.focus,
        observation=signal.observation or normalized.observation,
        evidence=signal.evidence,
        source_url=signal.source_url,
        confidence=signal.confidence,
    )


def _candidate_facts(
    signal: SiteSignal | None,
    row: dict[str, str],
    campaign: CampaignConfig,
) -> tuple[CompanyFact, ...]:
    candidates: list[CompanyFact] = []
    if signal is not None and signal.status == "ok":
        candidates.extend(signal.facts or (_primary_fact(signal),))
    candidates.extend(facts_from_row(row, campaign.row_fallback_fields))

    unique: list[CompanyFact] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        identity = (candidate.focus.casefold(), candidate.evidence.casefold())
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(candidate)
    return tuple(unique)


def _short_company_name(value: str, max_words: int = 4) -> str:
    words = value.split()
    suffixes = {"inc", "inc.", "llc", "ltd", "ltd.", "limited", "corp", "corp."}
    while len(words) > 1 and words[-1].casefold() in suffixes:
        words.pop()
    return " ".join(words[:max_words])


def _opening_key(value: str, word_limit: int) -> str:
    words = re.findall(r"[a-z0-9']+", value.casefold())
    return " ".join(words[:word_limit])


def _apply_batch_quality(
    rows: list[dict[str, str]],
    domains: list[str],
    campaign: CampaignConfig,
) -> dict[str, object]:
    quality = campaign.data["quality"]
    representatives: dict[str, dict[str, str]] = {}
    for row, domain in zip(rows, domains, strict=True):
        if (
            domain
            and row.get("personalization_status") in {"ready", "review"}
            and row.get(campaign.output_field)
        ):
            representatives.setdefault(domain, row)

    total = len(representatives)
    report: dict[str, object] = {
        "evaluated": total >= int(quality["min_rows"]),
        "unique_rendered_domains": total,
        "minimum_domains": int(quality["min_rows"]),
        "warnings": [],
        "flagged_rows": 0,
    }
    if total < int(quality["min_rows"]):
        return report

    opening_domains: dict[str, set[str]] = {}
    exact_domains: dict[str, set[str]] = {}
    for domain, row in representatives.items():
        pitch = row[campaign.output_field]
        opening = _opening_key(pitch, int(quality["opening_words"]))
        opening_domains.setdefault(opening, set()).add(domain)
        exact_domains.setdefault(pitch.casefold(), set()).add(domain)

    flags_by_domain: dict[str, list[str]] = {}
    warnings: list[dict[str, object]] = []
    for opening, affected in sorted(opening_domains.items()):
        share = len(affected) / total
        if share > float(quality["max_opening_share"]):
            message = (
                f"opening '{opening}' appears on {share:.1%} of rendered domains"
            )
            warnings.append(
                {"type": "opening_share", "opening": opening, "count": len(affected), "share": round(share, 4)}
            )
            for domain in affected:
                flags_by_domain.setdefault(domain, []).append(message)

    for affected in exact_domains.values():
        share = len(affected) / total
        if len(affected) > 1 and share > float(quality["max_exact_pitch_share"]):
            message = f"exact pitch appears on {share:.1%} of rendered domains"
            warnings.append(
                {"type": "exact_pitch_share", "count": len(affected), "share": round(share, 4)}
            )
            for domain in affected:
                flags_by_domain.setdefault(domain, []).append(message)

    flagged_rows = 0
    for row, domain in zip(rows, domains, strict=True):
        flags = flags_by_domain.get(domain, [])
        if not flags:
            continue
        flagged_rows += 1
        existing = row.get("personalization_quality_flags", "").strip()
        row["personalization_quality_flags"] = "; ".join(
            [item for item in (existing, *flags) if item]
        )
        if (
            quality["repetition_action"] == "review"
            and row.get("personalization_status") == "ready"
        ):
            row["personalization_status"] = "review"

    report["warnings"] = warnings
    report["flagged_rows"] = flagged_rows
    return report


def _failed_signal(domain: str, error: str) -> SiteSignal:
    return SiteSignal(
        domain=domain,
        observation="",
        evidence="",
        source_url="",
        confidence=0.0,
        status="error",
        error=error,
    )


def _enrich_domains(
    domains: list[str],
    enricher: DomainEnricher,
    *,
    concurrency: int,
    progress: ProgressCallback | None,
) -> dict[str, SiteSignal]:
    results: dict[str, SiteSignal] = {}
    if not domains:
        return results
    with ThreadPoolExecutor(max_workers=min(concurrency, len(domains))) as executor:
        futures = {executor.submit(enricher.enrich, domain): domain for domain in domains}
        for completed, future in enumerate(as_completed(futures), start=1):
            domain = futures[future]
            try:
                signal = future.result()
            except Exception as exc:  # A single broken site must not abort the CSV.
                signal = _failed_signal(domain, f"unexpected domain error: {exc}")
            results[domain] = signal
            if progress is not None:
                progress(completed, len(domains), domain, signal.status)
    return results


def run_enrichment(
    *,
    input_path: str | Path,
    output_path: str | Path,
    campaign: CampaignConfig,
    title_hooks: TitleHookTable,
    commercial_focuses: CommercialFocusTable,
    options: RunOptions,
    progress: ProgressCallback | None = None,
    domain_enricher: DomainEnricher | None = None,
) -> dict[str, object]:
    started_at = datetime.now(UTC)
    started_monotonic = time.monotonic()
    data = load_csv(input_path)
    input_resolved = data.path
    output_resolved = Path(output_path).expanduser().resolve()
    if input_resolved == output_resolved:
        raise ValueError("--output must be different from --input")
    ready_output_resolved = (
        options.ready_output_path.expanduser().resolve()
        if options.ready_output_path is not None
        else None
    )
    if ready_output_resolved in {input_resolved, output_resolved}:
        raise ValueError("--ready-output must differ from --input and --output")

    domains = [domain_for_row(row, data.column_map) for row in data.rows]
    unique_domains = list(dict.fromkeys(domain for domain in domains if domain))

    fetcher: HttpFetcher | None = None
    firecrawl_fetcher: FirecrawlFetcher | None = None
    if domain_enricher is None:
        cache = JsonCache(options.cache_dir)
        fetcher = HttpFetcher(
            cache,
            FetcherSettings(
                timeout=options.timeout,
                retries=options.retries,
                max_response_bytes=options.max_response_bytes,
                cache_ttl_hours=options.cache_ttl_hours,
                refresh_cache=options.refresh_cache,
            ),
        )
        if options.firecrawl_fallback:
            firecrawl_fetcher = FirecrawlFetcher(
                cache,
                FirecrawlSettings(
                    api_url=options.firecrawl_api_url,
                    api_key=os.environ.get("FIRECRAWL_API_KEY", ""),
                    timeout=options.firecrawl_timeout,
                    max_concurrency=options.firecrawl_concurrency,
                    cache_ttl_hours=options.cache_ttl_hours,
                    refresh_cache=options.refresh_cache,
                ),
            )
        domain_enricher = SiteEnricher(
            fetcher,
            cache,
            max_pages=options.max_pages,
            cache_ttl_hours=options.cache_ttl_hours,
            refresh_cache=options.refresh_cache,
            fallback_fetcher=firecrawl_fetcher,
        )

    domain_results = _enrich_domains(
        unique_domains,
        domain_enricher,
        concurrency=options.concurrency,
        progress=progress,
    )

    append_fields = list(campaign.data["output"]["append_fields"])
    output_rows: list[dict[str, str]] = []
    for row, domain in zip(data.rows, domains, strict=True):
        output_row = dict(row)
        signal = domain_results.get(domain) if domain else None
        values = {
            "personalized_subject": "",
            campaign.output_field: "",
            "personalized_email": "",
            "personalization_angle": "",
            "personalization_template": "",
            "personalization_signal_type": "",
            "personalization_source_focus": "",
            "personalization_focus": "",
            "personalization_buyer_phrase": "",
            "personalization_focus_rule": "",
            "personalization_cta_variant": "",
            "personalization_cta": "",
            "personalization_facts": "",
            "personalization_source": signal.source_url if signal else "",
            "personalization_evidence": signal.evidence if signal else "",
            "personalization_confidence": f"{signal.confidence:.2f}" if signal else "0.00",
            "personalization_quality_flags": "",
            "personalization_status": "error",
            "personalization_error": "",
        }
        errors: list[str] = []

        if not domain:
            errors.append("missing company domain or website")
        else:
            facts = _candidate_facts(signal, row, campaign)
            if not facts:
                errors.append(
                    signal.error
                    if signal is not None and signal.error
                    else "domain enrichment did not return a usable result"
                )
                output_row.update(values)
                email_value = row.get(data.column_map.get("email", ""), "").strip()
                first_name_value = row.get(
                    data.column_map.get("first_name", ""), ""
                ).strip()
                if not email_value:
                    errors.append("missing email")
                if not first_name_value:
                    errors.append("missing first name")
                values["personalization_error"] = "; ".join(errors)
                output_row.update(values)
                output_rows.append(output_row)
                continue

            fact = facts[0]
            context = context_for_row(row, data.column_map)
            title = str(context.get("job_title", ""))
            hook = title_hooks.match(title)
            company_name = str(context.get("company_name", ""))
            commercial_focus = None
            focus_errors: list[str] = []
            for candidate in facts:
                try:
                    resolved_focus = commercial_focuses.resolve(
                        company_name=company_name,
                        signal_type=candidate.signal_type,
                        source_focus=candidate.focus,
                        evidence=candidate.evidence,
                        max_focus_words=campaign.max_focus_words,
                        max_buyer_phrase_words=campaign.max_buyer_phrase_words,
                    )
                except CommercialFocusError as exc:
                    focus_errors.append(str(exc))
                    continue
                fact = candidate
                commercial_focus = resolved_focus
                break
            if commercial_focus is None:
                unique_focus_errors = list(dict.fromkeys(focus_errors))
                errors.append(
                    "no safe commercial focus found: "
                    + " | ".join(unique_focus_errors[:3])
                )

            values.update(
                {
                    "personalization_signal_type": fact.signal_type,
                    "personalization_source_focus": fact.focus,
                    "personalization_focus": commercial_focus.focus if commercial_focus else "",
                    "personalization_buyer_phrase": (
                        commercial_focus.buyer_phrase if commercial_focus else ""
                    ),
                    "personalization_focus_rule": (
                        commercial_focus.rule_id if commercial_focus else ""
                    ),
                    "personalization_facts": json.dumps(
                        [
                            item.to_dict()
                            for item in (
                                fact,
                                *(item for item in facts if item != fact),
                            )[:3]
                        ],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "personalization_source": fact.source_url,
                    "personalization_evidence": fact.evidence,
                    "personalization_confidence": f"{fact.confidence:.2f}",
                }
            )
            if commercial_focus is not None:
                context.update(
                    {
                        "company_observation": fact.observation,
                        "company_source_focus": commercial_focus.source_focus,
                        "company_focus": commercial_focus.focus,
                        "company_focus_sentence": (
                            commercial_focus.focus[:1].upper() + commercial_focus.focus[1:]
                        ),
                        "buyer_phrase": commercial_focus.buyer_phrase,
                        "company_evidence": fact.evidence,
                        "company_short_name": _short_company_name(company_name),
                        "personalization_source": fact.source_url,
                        "title_hook": hook.hook,
                        "persona": hook.persona,
                        "service": campaign.data["offer"]["service"],
                        "audience": campaign.data["offer"]["audience"],
                    }
                )
            low_confidence = fact.confidence < campaign.min_confidence
            if low_confidence:
                errors.append(
                    f"signal confidence {fact.confidence:.2f} is below "
                    f"{campaign.min_confidence:.2f}"
                )

            should_render = commercial_focus is not None and (not low_confidence or (
                campaign.data["personalization"]["low_confidence_action"] == "review"
            ))
            if should_render:
                try:
                    rendered = _build_copy(
                        campaign,
                        context,
                        domain,
                        fact.signal_type,
                        fact.evidence,
                    )
                    values["personalized_subject"] = rendered.subject
                    values[campaign.output_field] = rendered.pitch
                    values["personalized_email"] = rendered.body
                    values["personalization_angle"] = rendered.angle_id
                    values["personalization_template"] = rendered.template_id
                    values["personalization_cta_variant"] = rendered.cta_variant_id
                    values["personalization_cta"] = rendered.cta
                    values["personalization_status"] = "review" if low_confidence else "ready"
                except (KeyError, ValueError) as exc:
                    errors.append(str(exc))
            else:
                values["personalization_status"] = "blank"

        email_value = row.get(data.column_map.get("email", ""), "").strip()
        first_name_value = row.get(data.column_map.get("first_name", ""), "").strip()
        if not email_value:
            errors.append("missing email")
        if not first_name_value:
            errors.append("missing first name")
        if values["personalization_status"] == "ready" and errors:
            values["personalization_status"] = "review"
        values["personalization_error"] = "; ".join(error for error in errors if error)
        output_row.update(values)
        output_rows.append(output_row)

    quality_report = _apply_batch_quality(output_rows, domains, campaign)
    status_counts = Counter(
        row.get("personalization_status", "error") for row in output_rows
    )

    write_enriched_csv(output_resolved, data.headers, output_rows, append_fields)
    ready_rows = [
        row for row in output_rows if row.get("personalization_status") == "ready"
    ]
    if ready_output_resolved is not None:
        write_enriched_csv(
            ready_output_resolved,
            data.headers,
            ready_rows,
            append_fields,
        )

    finished_at = datetime.now(UTC)
    manifest_path = options.manifest_path or output_resolved.with_suffix(
        output_resolved.suffix + ".manifest.json"
    )
    domain_status_counts = Counter(signal.status for signal in domain_results.values())
    manifest: dict[str, object] = {
        "tool": {"name": "bulk-enrich", "version": __version__},
        "campaign": {
            "id": campaign.campaign_id,
            "status": campaign.status,
            "path": str(campaign.path),
            "sha256": sha256_file(campaign.path),
        },
        "run": {
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": round(time.monotonic() - started_monotonic, 3),
        },
        "input": {
            **inspect_csv(input_resolved).to_dict(),
            "sha256": sha256_file(input_resolved),
        },
        "output": {
            "path": str(output_resolved),
            "sha256": sha256_file(output_resolved),
            "manifest_path": str(manifest_path),
            "appended_fields": append_fields,
            "status_counts": dict(sorted(status_counts.items())),
            "ready_upload": (
                {
                    "path": str(ready_output_resolved),
                    "sha256": sha256_file(ready_output_resolved),
                    "row_count": len(ready_rows),
                }
                if ready_output_resolved is not None
                else None
            ),
        },
        "domains": {
            "unique": len(unique_domains),
            "duplicate_rows_avoided": len([domain for domain in domains if domain])
            - len(unique_domains),
            "status_counts": dict(sorted(domain_status_counts.items())),
            "signal_cache_hits": sum(
                int(signal.signal_cache_hit) for signal in domain_results.values()
            ),
            "page_cache_hits": sum(signal.http_cache_hits for signal in domain_results.values()),
            "pages_fetched": sum(signal.pages_fetched for signal in domain_results.values()),
        },
        "quality": quality_report,
        "settings": {
            **asdict(options),
            "cache_dir": str(options.cache_dir),
            "manifest_path": str(manifest_path),
            "ready_output_path": (
                str(ready_output_resolved) if ready_output_resolved is not None else None
            ),
            "title_hooks_path": str(title_hooks.path),
            "title_hooks_sha256": sha256_file(title_hooks.path),
            "commercial_focus_path": str(commercial_focuses.path),
            "commercial_focus_sha256": sha256_file(commercial_focuses.path),
            "commercial_focus_rules": len(commercial_focuses.rules),
        },
        "http": fetcher.stats() if fetcher is not None else {"test_double": True},
        "firecrawl": (
            firecrawl_fetcher.stats()
            if firecrawl_fetcher is not None
            else {"enabled": False}
        ),
    }
    _atomic_json(manifest_path, manifest)
    return manifest
