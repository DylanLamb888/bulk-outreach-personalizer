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
from bulk_enrich.focus import (
    CommercialFocusError,
    CommercialFocusResult,
    CommercialFocusTable,
    longest_shared_phrase_words,
)
from bulk_enrich.hooks import TitleHookTable
from bulk_enrich.models import CompanyFact, SiteSignal
from bulk_enrich.qualification import (
    QualificationResult,
    compose_outreach_status,
    normalize_email,
    qualify_company,
    qualify_contact,
    qualify_email,
)
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
    review_output_path: Path | None = None


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


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(source.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def _immutable_snapshot(
    source: Path,
    output_path: Path,
    label: str,
) -> tuple[Path, str]:
    """Create a content-addressed configuration snapshot beside the audit."""
    digest = sha256_file(source)
    destination = output_path.with_name(
        f"{output_path.name}.{label}.{digest}{source.suffix}"
    )
    if destination.exists():
        if sha256_file(destination) != digest:
            raise OSError(f"immutable snapshot hash mismatch: {destination}")
    else:
        _atomic_copy(source, destination)
    return destination, digest


def _stable_template_order(
    templates: tuple[CopyTemplate, ...], domain: str
) -> tuple[CopyTemplate, ...]:
    start = int(hashlib.sha256(domain.encode("utf-8")).hexdigest(), 16) % len(templates)
    return templates[start:] + templates[:start]


def _stable_cta(variants: tuple[CtaVariant, ...], domain: str) -> CtaVariant:
    identity = f"cta:{domain}"
    index = int(hashlib.sha256(identity.encode("utf-8")).hexdigest(), 16) % len(variants)
    return variants[index]


def _balanced_ctas(
    variants: tuple[CtaVariant, ...],
    domains: list[str],
) -> dict[str, CtaVariant]:
    """Assign approved CTAs evenly and deterministically across one batch."""
    unique_domains = list(dict.fromkeys(domain for domain in domains if domain))
    ordered = sorted(
        unique_domains,
        key=lambda domain: hashlib.sha256(
            f"cta-balance:{domain}".encode("utf-8")
        ).hexdigest(),
    )
    return {
        domain: variants[index % len(variants)]
        for index, domain in enumerate(ordered)
    }


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
    cta_variant: CtaVariant | None = None,
) -> RenderedCopy:
    angle = _angle_for_signal(campaign, signal_type)
    failures: list[str] = []
    cta_variant = cta_variant or _stable_cta(campaign.cta_variants, domain)
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
            if _opening_key(pitch, 2) == _opening_key(cta, 2):
                failures.append(
                    f"{template.template_id}: pitch repeats the CTA opening"
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


def _select_company_candidate(
    candidates: list[tuple[int, int, CompanyFact, CommercialFocusResult]],
    campaign: CampaignConfig,
) -> tuple[
    tuple[int, int, CompanyFact, CommercialFocusResult] | None,
    tuple[str, ...],
]:
    """Use first-party evidence when available; otherwise require CSV corroboration."""
    website_mapped = [
        item
        for item in candidates
        if not item[2].source_url.startswith("input:")
        and item[3].rule_id != "generic-compression"
    ]
    if website_mapped:
        return min(website_mapped, key=lambda item: (item[0], item[1])), ()

    website_any = [
        item for item in candidates if not item[2].source_url.startswith("input:")
    ]
    if website_any:
        # A readable first-party site with no campaign match is negative evidence.
        # CSV enrichment may only rescue an unavailable site, never an unclear one.
        return min(website_any, key=lambda item: (item[0], item[1])), ()

    approved_headers = {
        item.casefold() for item in campaign.fallback_qualification_fields
    }
    input_mapped = [
        item
        for item in candidates
        if item[2].source_url.startswith("input:")
        and item[2].source_url.removeprefix("input:").casefold() in approved_headers
        and item[3].rule_id != "generic-compression"
        and item[3].fit_tier != "exclude"
    ]
    by_rule: dict[str, list[tuple[int, int, CompanyFact, CommercialFocusResult]]] = {}
    for item in input_mapped:
        by_rule.setdefault(item[3].rule_id, []).append(item)
    corroborated = [
        group
        for group in by_rule.values()
        if len({item[2].source_url.casefold() for item in group})
        >= campaign.fallback_min_agreeing_fields
    ]
    if corroborated:
        selected_group = min(
            corroborated,
            key=lambda group: (
                min(item[1] for item in group),
                min(item[0] for item in group),
            ),
        )
        selected = min(selected_group, key=lambda item: (item[1], item[0]))
        fields = tuple(
            dict.fromkeys(
                item[2].source_url.removeprefix("input:")
                for item in sorted(selected_group, key=lambda item: item[1])
            )
        )
        return selected, fields

    if candidates:
        selected = min(candidates, key=lambda item: (item[1], item[0]))
        field = selected[2].source_url.removeprefix("input:")
        return selected, (field,) if selected[2].source_url.startswith("input:") else ()
    return None, ()


_COPY_OUTPUT_FIELDS = (
    "personalized_subject",
    "personalized_email",
    "personalization_angle",
    "personalization_template",
    "personalization_cta_variant",
    "personalization_cta",
)


def _blank_rendered_copy(row: dict[str, str], campaign: CampaignConfig) -> None:
    for field in (*_COPY_OUTPUT_FIELDS, campaign.output_field):
        row[field] = ""
    row["personalization_status"] = "blank"


def _result_from_row(row: dict[str, str], prefix: str) -> QualificationResult:
    return QualificationResult(
        status=row[f"{prefix}_fit_status"],
        rule=row[f"{prefix}_fit_rule"],
        reason=row[f"{prefix}_fit_reason"],
    )


def _refresh_outreach_status(row: dict[str, str]) -> None:
    status, reason = compose_outreach_status(
        _result_from_row(row, "company"),
        _result_from_row(row, "contact"),
        _result_from_row(row, "email"),
        row.get("personalization_status", "error"),
    )
    row["outreach_status"] = status
    row["outreach_reason"] = reason


def _deduplicate_emails(
    rows: list[dict[str, str]],
    email_header: str,
    campaign: CampaignConfig,
) -> int:
    grouped: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        email = normalize_email(row.get(email_header, ""))
        if email:
            grouped.setdefault(email, []).append(index)
    status_rank = {"ready": 3, "review": 2, "excluded": 1, "error": 0}
    fit_rank = {"qualified": 2, "review": 1, "excluded": 0}
    copy_rank = {"ready": 2, "review": 1, "blank": 0, "error": -1}
    duplicate_rows = 0
    for indexes in grouped.values():
        if len(indexes) < 2:
            continue
        winner = max(
            indexes,
            key=lambda index: (
                status_rank.get(rows[index].get("outreach_status", "error"), 0),
                fit_rank.get(rows[index].get("email_fit_status", "excluded"), 0),
                fit_rank.get(rows[index].get("company_fit_status", "excluded"), 0),
                fit_rank.get(rows[index].get("contact_fit_status", "excluded"), 0),
                copy_rank.get(rows[index].get("personalization_status", "error"), -1),
                -index,
            ),
        )
        for index in indexes:
            if index == winner:
                continue
            duplicate_rows += 1
            row = rows[index]
            row["email_fit_status"] = "excluded"
            row["email_fit_rule"] = "duplicate-email"
            row["email_fit_reason"] = f"duplicate of retained input row {winner + 2}"
            _blank_rendered_copy(row, campaign)
            _refresh_outreach_status(row)
    return duplicate_rows


def _clean_first_name(value: str) -> str:
    """Keep only the conversational given name from noisy provider fields."""
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        return ""
    return cleaned.split()[0].strip(" ,;:")


def _short_company_name(value: str, max_words: int = 4) -> str:
    """Create a subject-safe brand name without legal or export noise."""
    acronym = re.search(r"\(([A-Z][A-Z0-9&.-]{1,9})\)", value)
    if acronym:
        return acronym.group(1).strip(" ,.;:&/-")
    cleaned = re.split(r"\s*(?:\||\(|\[|[–—]|\s-\s)", value, maxsplit=1)[0]
    words = cleaned.split()
    suffixes = {
        "corp",
        "corporation",
        "inc",
        "incorporated",
        "llc",
        "ltd",
        "limited",
    }
    connectors = {"&", "and"}
    while len(words) > 1:
        final = words[-1].strip(" ,.;:-").casefold()
        if final in suffixes or final in connectors:
            words.pop()
            continue
        break
    result = " ".join(words[:max_words]).strip(" ,.;:&/-")
    return re.sub(r"\s+", " ", result)


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
    buyer_phrase_domains: dict[str, set[str]] = {}
    cta_domains: dict[str, set[str]] = {}
    for domain, row in representatives.items():
        pitch = row[campaign.output_field]
        opening = _opening_key(pitch, int(quality["opening_words"]))
        opening_domains.setdefault(opening, set()).add(domain)
        exact_domains.setdefault(pitch.casefold(), set()).add(domain)
        buyer_phrase = row.get("personalization_buyer_phrase", "").casefold()
        if buyer_phrase:
            buyer_phrase_domains.setdefault(buyer_phrase, set()).add(domain)
        cta = row.get("personalization_cta", "").casefold()
        if cta:
            cta_domains.setdefault(cta, set()).add(domain)

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

    buyer_phrase_limit = quality["max_buyer_phrase_share"]
    if buyer_phrase_limit is not None:
        for buyer_phrase, affected in sorted(buyer_phrase_domains.items()):
            share = len(affected) / total
            if share > float(buyer_phrase_limit):
                message = (
                    f"buyer phrase '{buyer_phrase}' appears on {share:.1%} "
                    "of rendered domains"
                )
                warnings.append(
                    {
                        "type": "buyer_phrase_share",
                        "buyer_phrase": buyer_phrase,
                        "count": len(affected),
                        "share": round(share, 4),
                    }
                )
                for domain in affected:
                    flags_by_domain.setdefault(domain, []).append(message)

    for cta, affected in sorted(cta_domains.items()):
        share = len(affected) / total
        if share > float(quality["max_cta_share"]):
            message = f"CTA '{cta}' appears on {share:.1%} of rendered domains"
            warnings.append(
                {
                    "type": "cta_share",
                    "cta": cta,
                    "count": len(affected),
                    "share": round(share, 4),
                }
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
    review_output_resolved = (
        options.review_output_path.expanduser().resolve()
        if options.review_output_path is not None
        else None
    )
    requested_outputs = [
        path
        for path in (output_resolved, ready_output_resolved, review_output_resolved)
        if path is not None
    ]
    if input_resolved in requested_outputs:
        raise ValueError("output paths must differ from --input")
    if len(requested_outputs) != len(set(requested_outputs)):
        raise ValueError("--output, --ready-output, and --review-output must differ")

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
    cta_by_domain = _balanced_ctas(campaign.cta_variants, unique_domains)
    output_rows: list[dict[str, str]] = []
    for row, domain in zip(data.rows, domains, strict=True):
        output_row = dict(row)
        signal = domain_results.get(domain) if domain else None
        context = context_for_row(row, data.column_map)
        context["first_name"] = _clean_first_name(str(context.get("first_name", "")))
        email_value = str(context.get("email", "")).strip()
        contact_result = qualify_contact(
            first_name=str(context.get("first_name", "")),
            job_title=str(context.get("job_title", "")),
            job_seniority=str(context.get("job_seniority", "")),
            campaign=campaign,
        )
        email_result = qualify_email(
            email_value,
            str(context.get("email_status", "")),
            campaign,
        )
        company_result = QualificationResult(
            status="excluded",
            rule="no-company-evidence",
            reason="no campaign-mapped company evidence was found",
        )
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
            "personalization_status": "blank",
            "personalization_error": "",
            "company_fit_status": company_result.status,
            "company_fit_tier": "none",
            "company_fit_rule": company_result.rule,
            "company_fit_source": signal.source_url if signal else "",
            "company_fit_evidence": signal.evidence if signal else "",
            "company_fit_reason": company_result.reason,
            "contact_fit_status": contact_result.status,
            "contact_fit_rule": contact_result.rule,
            "contact_fit_reason": contact_result.reason,
            "email_fit_status": email_result.status,
            "email_fit_rule": email_result.rule,
            "email_fit_reason": email_result.reason,
            "outreach_status": "excluded",
            "outreach_reason": "",
        }
        errors: list[str] = []
        facts: tuple[CompanyFact, ...] = ()
        fact: CompanyFact | None = None
        commercial_focus: CommercialFocusResult | None = None
        corroborating_fields: tuple[str, ...] = ()

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
            else:
                company_name = str(context.get("company_name", ""))
                focus_errors: list[str] = []
                resolved_candidates: list[
                    tuple[int, int, CompanyFact, CommercialFocusResult]
                ] = []
                for candidate_index, candidate in enumerate(facts):
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
                    resolved_candidates.append(
                        (
                            resolved_focus.priority,
                            candidate_index,
                            candidate,
                            resolved_focus,
                        )
                    )
                selected_candidate, corroborating_fields = _select_company_candidate(
                    resolved_candidates,
                    campaign,
                )
                if selected_candidate is not None:
                    _priority, _index, fact, commercial_focus = selected_candidate
                    company_result = qualify_company(
                        fact,
                        commercial_focus,
                        corroborating_fields=corroborating_fields,
                        fallback_min_agreeing_fields=campaign.fallback_min_agreeing_fields,
                        min_confidence=campaign.min_confidence,
                    )
                else:
                    unique_focus_errors = list(dict.fromkeys(focus_errors))
                    errors.append(
                        "no safe commercial focus found"
                        + (
                            ": " + " | ".join(unique_focus_errors[:3])
                            if unique_focus_errors
                            else ""
                        )
                    )

        values.update(
            {
                "company_fit_status": company_result.status,
                "company_fit_tier": commercial_focus.fit_tier if commercial_focus else "none",
                "company_fit_rule": company_result.rule,
                "company_fit_source": fact.source_url if fact else values["company_fit_source"],
                "company_fit_evidence": fact.evidence if fact else values["company_fit_evidence"],
                "company_fit_reason": company_result.reason,
            }
        )
        if fact is not None:
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

        gate_excluded = any(
            result.status == "excluded"
            for result in (company_result, contact_result, email_result)
        )
        low_confidence = fact is not None and fact.confidence < campaign.min_confidence
        should_render = (
            not gate_excluded
            and fact is not None
            and commercial_focus is not None
            and commercial_focus.fit_tier != "exclude"
            and (
                not low_confidence
                or campaign.data["personalization"]["low_confidence_action"] == "review"
            )
        )
        if should_render:
            title = str(context.get("job_title", ""))
            hook = title_hooks.match(title)
            company_name = str(context.get("company_name", ""))
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
            if low_confidence:
                errors.append(
                    f"signal confidence {fact.confidence:.2f} is below "
                    f"{campaign.min_confidence:.2f}"
                )
            try:
                rendered = _build_copy(
                    campaign,
                    context,
                    domain,
                    fact.signal_type,
                    fact.evidence,
                    cta_variant=cta_by_domain.get(domain),
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
                values["personalization_status"] = "error"
                errors.append(str(exc))

        values["personalization_error"] = "; ".join(error for error in errors if error)
        output_row.update(values)
        _refresh_outreach_status(output_row)
        if output_row["outreach_status"] == "excluded":
            _blank_rendered_copy(output_row, campaign)
        output_rows.append(output_row)

    duplicate_email_rows = _deduplicate_emails(
        output_rows,
        data.column_map["email"],
        campaign,
    )
    quality_report = _apply_batch_quality(output_rows, domains, campaign)
    for row in output_rows:
        _refresh_outreach_status(row)
    personalization_status_counts = Counter(
        row.get("personalization_status", "error") for row in output_rows
    )
    outreach_status_counts = Counter(
        row.get("outreach_status", "error") for row in output_rows
    )

    write_enriched_csv(output_resolved, data.headers, output_rows, append_fields)
    ready_rows = [
        row for row in output_rows if row.get("outreach_status") == "ready"
    ]
    review_rows = [
        row for row in output_rows if row.get("outreach_status") == "review"
    ]
    if ready_output_resolved is not None:
        write_enriched_csv(
            ready_output_resolved,
            data.headers,
            ready_rows,
            append_fields,
        )
    if review_output_resolved is not None:
        write_enriched_csv(
            review_output_resolved,
            data.headers,
            review_rows,
            append_fields,
        )

    finished_at = datetime.now(UTC)
    manifest_path = options.manifest_path or output_resolved.with_suffix(
        output_resolved.suffix + ".manifest.json"
    )
    campaign_snapshot_path, campaign_sha256 = _immutable_snapshot(
        campaign.path,
        output_resolved,
        "campaign",
    )
    focus_snapshot_path, focus_sha256 = _immutable_snapshot(
        commercial_focuses.path,
        output_resolved,
        "focus",
    )
    domain_status_counts = Counter(signal.status for signal in domain_results.values())
    company_fit_counts = Counter(row["company_fit_status"] for row in output_rows)
    contact_fit_counts = Counter(row["contact_fit_status"] for row in output_rows)
    email_fit_counts = Counter(row["email_fit_status"] for row in output_rows)
    company_rule_counts = Counter(
        row["company_fit_rule"] for row in output_rows if row["company_fit_rule"]
    )
    manifest: dict[str, object] = {
        "tool": {"name": "bulk-enrich", "version": __version__},
        "campaign": {
            "id": campaign.campaign_id,
            "status": campaign.status,
            "path": str(campaign.path),
            "sha256": campaign_sha256,
            "snapshot_path": str(campaign_snapshot_path),
            "snapshot_sha256": campaign_sha256,
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
            "status_counts": dict(sorted(outreach_status_counts.items())),
            "personalization_status_counts": dict(
                sorted(personalization_status_counts.items())
            ),
            "ready_upload": (
                {
                    "path": str(ready_output_resolved),
                    "sha256": sha256_file(ready_output_resolved),
                    "row_count": len(ready_rows),
                }
                if ready_output_resolved is not None
                else None
            ),
            "review_output": (
                {
                    "path": str(review_output_resolved),
                    "sha256": sha256_file(review_output_resolved),
                    "row_count": len(review_rows),
                }
                if review_output_resolved is not None
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
        "qualification": {
            "company_status_counts": dict(sorted(company_fit_counts.items())),
            "contact_status_counts": dict(sorted(contact_fit_counts.items())),
            "email_status_counts": dict(sorted(email_fit_counts.items())),
            "outreach_status_counts": dict(sorted(outreach_status_counts.items())),
            "company_rule_counts": dict(sorted(company_rule_counts.items())),
            "duplicate_email_rows_excluded": duplicate_email_rows,
        },
        "quality": quality_report,
        "settings": {
            **asdict(options),
            "cache_dir": str(options.cache_dir),
            "manifest_path": str(manifest_path),
            "ready_output_path": (
                str(ready_output_resolved) if ready_output_resolved is not None else None
            ),
            "review_output_path": (
                str(review_output_resolved) if review_output_resolved is not None else None
            ),
            "title_hooks_path": str(title_hooks.path),
            "title_hooks_sha256": sha256_file(title_hooks.path),
            "commercial_focus_path": str(commercial_focuses.path),
            "commercial_focus_sha256": focus_sha256,
            "commercial_focus_snapshot_path": str(focus_snapshot_path),
            "commercial_focus_snapshot_sha256": focus_sha256,
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
