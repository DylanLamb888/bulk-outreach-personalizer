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
from urllib.parse import urlsplit

from bulk_enrich import __version__
from bulk_enrich.cache import JsonCache
from bulk_enrich.config import (
    CampaignConfig,
    CopyAngle,
    CopyTemplate,
    CtaVariant,
    OfferLineVariant,
    forbidden_copy_character,
)
from bulk_enrich.csv_io import (
    context_for_row,
    domain_for_row,
    load_csv,
    summarize_csv,
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
from bulk_enrich.llm_focus import (
    RULE_ID as LLM_RULE_ID,
    LlmFocusClassifier,
    LlmFocusDecision,
    LlmFocusError,
    LlmFocusItem,
    PhraseLimits,
    build_transport,
)
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
    llm_mode: str = "sync"
    llm_concurrency: int = 2
    llm_poll_seconds: float = 30.0
    llm_cache_ttl_hours: float = 720.0
    llm_batch_ids: tuple[str, ...] = ()
    llm_budget_usd: float | None = None


LogCallback = Callable[[str], None]


@dataclass(frozen=True)
class RenderedCopy:
    angle_id: str
    template_id: str
    pitch: str
    subject: str
    body: str
    cta_variant_id: str
    cta: str
    offer_variant_id: str
    offer_line: str


@dataclass
class _RenderJob:
    row: dict[str, str]
    context: dict[str, object]
    assignment_key: str
    signal_type: str
    source_evidence: str
    focus_rule: str
    angle: CopyAngle | None = None
    max_pitch_words: int | None = None


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


def _ctas_for_focus_rule(
    variants: tuple[CtaVariant, ...],
    focus_rule: str,
) -> tuple[CtaVariant, ...]:
    exact = tuple(
        variant for variant in variants if focus_rule and focus_rule in variant.focus_rules
    )
    if exact:
        return exact
    fallback = tuple(variant for variant in variants if "*" in variant.focus_rules)
    if fallback:
        return fallback
    raise ValueError(f"no CTA variant is configured for focus rule '{focus_rule}'")


def _balanced_ctas_for_rendered_domains(
    variants: tuple[CtaVariant, ...],
    domain_focus_rules: list[tuple[str, str]],
) -> dict[str, CtaVariant]:
    """Balance CTAs within each applicable focus-rule pool.

    A domain is assigned exactly once, from its first-seen focus rule's pool;
    rows matching another rule fall back to per-row selection in copy building.
    """
    first_rule: dict[str, str] = {}
    for domain, focus_rule in domain_focus_rules:
        first_rule.setdefault(domain, focus_rule)
    grouped: dict[tuple[str, ...], tuple[tuple[CtaVariant, ...], list[str]]] = {}
    for domain, focus_rule in first_rule.items():
        applicable = _ctas_for_focus_rule(variants, focus_rule)
        key = tuple(variant.variant_id for variant in applicable)
        grouped.setdefault(key, (applicable, []))[1].append(domain)
    assigned: dict[str, CtaVariant] = {}
    for applicable, domains in grouped.values():
        assigned.update(_balanced_ctas(applicable, domains))
    return assigned


def _stable_offer_line(
    variants: tuple[OfferLineVariant, ...], domain: str
) -> OfferLineVariant:
    identity = f"offer-line:{domain}"
    index = int(hashlib.sha256(identity.encode("utf-8")).hexdigest(), 16) % len(variants)
    return variants[index]


def _balanced_offer_lines(
    variants: tuple[OfferLineVariant, ...],
    domains: list[str],
) -> dict[str, OfferLineVariant]:
    """Assign approved offer lines evenly and deterministically across one batch."""
    unique_domains = list(dict.fromkeys(domain for domain in domains if domain))
    ordered = sorted(
        unique_domains,
        key=lambda domain: hashlib.sha256(
            f"offer-line-balance:{domain}".encode("utf-8")
        ).hexdigest(),
    )
    return {
        domain: variants[index % len(variants)]
        for index, domain in enumerate(ordered)
    }


def _offer_lines_for_focus_rule(
    variants: tuple[OfferLineVariant, ...],
    focus_rule: str,
) -> tuple[OfferLineVariant, ...]:
    exact = tuple(
        variant for variant in variants if focus_rule and focus_rule in variant.focus_rules
    )
    if exact:
        return exact
    fallback = tuple(variant for variant in variants if "*" in variant.focus_rules)
    if fallback:
        return fallback
    raise ValueError(f"no offer-line variant is configured for focus rule '{focus_rule}'")


def _balanced_offer_lines_for_rendered_domains(
    variants: tuple[OfferLineVariant, ...],
    domain_focus_rules: list[tuple[str, str]],
) -> dict[str, OfferLineVariant]:
    """Balance offer lines within each applicable focus-rule pool.

    A domain is assigned exactly once, from its first-seen focus rule's pool;
    rows matching another rule fall back to per-row selection in copy building.
    """
    first_rule: dict[str, str] = {}
    for domain, focus_rule in domain_focus_rules:
        first_rule.setdefault(domain, focus_rule)
    grouped: dict[
        tuple[str, ...], tuple[tuple[OfferLineVariant, ...], list[str]]
    ] = {}
    for domain, focus_rule in first_rule.items():
        applicable = _offer_lines_for_focus_rule(variants, focus_rule)
        key = tuple(variant.variant_id for variant in applicable)
        grouped.setdefault(key, (applicable, []))[1].append(domain)
    assigned: dict[str, OfferLineVariant] = {}
    for applicable, domains in grouped.values():
        assigned.update(_balanced_offer_lines(applicable, domains))
    return assigned


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
    focus_rule: str = "",
    cta_variant: CtaVariant | None = None,
    offer_variant: OfferLineVariant | None = None,
    angle: CopyAngle | None = None,
    max_pitch_words: int | None = None,
) -> RenderedCopy:
    angle = angle or _angle_for_signal(campaign, signal_type)
    pitch_limit = max_pitch_words or campaign.max_words
    failures: list[str] = []
    applicable_ctas = _ctas_for_focus_rule(campaign.cta_variants, focus_rule)
    if cta_variant not in applicable_ctas:
        cta_variant = _stable_cta(applicable_ctas, domain)
    applicable_offer_lines = _offer_lines_for_focus_rule(
        campaign.offer_line_variants,
        focus_rule,
    )
    if offer_variant not in applicable_offer_lines:
        offer_variant = _stable_offer_line(applicable_offer_lines, domain)
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
    forbidden_cta = forbidden_copy_character(cta)
    if forbidden_cta:
        raise ValueError(
            f"CTA {cta_variant.variant_id} contains forbidden character: {forbidden_cta}"
        )
    try:
        offer_line = " ".join(
            render_template(offer_variant.text, context).strip().split()
        )
    except KeyError as exc:
        raise ValueError(f"offer line {offer_variant.variant_id}: {exc}") from exc
    if offer_line and word_count(offer_line) > campaign.max_offer_line_words:
        raise ValueError(
            f"offer line {offer_variant.variant_id} exceeds "
            f"{campaign.max_offer_line_words} words"
        )
    banned_offer_line = _banned_phrase(offer_line, campaign.banned_phrases)
    if banned_offer_line:
        raise ValueError(
            f"offer line {offer_variant.variant_id} contains banned phrase "
            f"'{banned_offer_line}'"
        )
    forbidden_offer_line = forbidden_copy_character(offer_line)
    if forbidden_offer_line:
        raise ValueError(
            f"offer line {offer_variant.variant_id} contains forbidden character: "
            f"{forbidden_offer_line}"
        )
    render_context = dict(context)
    render_context["cta"] = cta
    render_context["risk_reversal"] = offer_line
    for template in _stable_template_order(angle.templates, domain):
        try:
            pitch = " ".join(render_template(template.pitch, context).strip().split())
            if word_count(pitch) > pitch_limit:
                failures.append(
                    f"{template.template_id}: pitch exceeds {pitch_limit} words"
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

        if not subject.strip():
            failures.append(f"{template.template_id}: rendered subject is empty")
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
        forbidden_character = forbidden_copy_character("\n".join((subject, body)))
        if forbidden_character:
            failures.append(
                f"{template.template_id}: rendered copy contains forbidden character: "
                f"{forbidden_character}"
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
            offer_variant_id=offer_variant.variant_id,
            offer_line=offer_line,
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


_LLM_CLASSIFIABLE_STATUSES = frozenset({"ok", "no_signal", "redirect_mismatch"})


def _llm_evidence_text(signal: SiteSignal | None) -> str:
    """Website text a model may classify: fetched pages only, never CSV fields."""
    if signal is None or signal.status not in _LLM_CLASSIFIABLE_STATUSES:
        return ""
    if signal.page_digest.strip():
        return signal.page_digest
    if signal.status != "ok":
        return ""
    parts = [fact.evidence for fact in signal.facts if fact.evidence]
    if not parts and signal.evidence:
        parts.append(signal.evidence)
    return "\n".join(dict.fromkeys(parts))


def _llm_note(signal: SiteSignal) -> str:
    if signal.status == "redirect_mismatch":
        host = urlsplit(signal.source_url).hostname or "another domain"
        return (
            f"this domain redirected to {host}. Decide whether that page describes the "
            "same company under a new name. If it looks like a parked domain, a registrar "
            "page, or an unrelated business, answer exclude."
        )
    return ""


def _company_context_by_domain(
    rows: list[dict[str, str]],
    domains: list[str],
    column_map: dict[str, str],
    campaign: CampaignConfig,
) -> dict[str, tuple[str, str]]:
    """First company name and the highest-priority contact title per domain."""
    names: dict[str, str] = {}
    titles: dict[str, tuple[int, str]] = {}
    name_header = column_map.get("company_name", "")
    title_header = column_map.get("job_title", "")
    for row, domain in zip(rows, domains, strict=True):
        if not domain:
            continue
        name = row.get(name_header, "").strip() if name_header else ""
        if name and not names.get(domain):
            names[domain] = name
        title = " ".join(row.get(title_header, "").split()) if title_header else ""
        if title:
            rank = _title_priority_rank(title, campaign)
            if domain not in titles or rank > titles[domain][0]:
                titles[domain] = (rank, title)
    return {
        domain: (names.get(domain, ""), titles.get(domain, (0, ""))[1])
        for domain in set(names) | set(titles)
    }


def _classify_domains_with_llm(
    campaign: CampaignConfig,
    options: RunOptions,
    domain_results: dict[str, SiteSignal],
    company_context: dict[str, tuple[str, str]],
    *,
    classifier: LlmFocusClassifier | None = None,
    cache: JsonCache | None = None,
    log: LogCallback | None = None,
) -> tuple[dict[str, LlmFocusDecision], dict[str, object]]:
    """Run the opt-in model classifier once per readable unique domain."""
    settings = campaign.llm_focus
    if settings is None:
        return {}, {"enabled": False}
    if classifier is None:
        if cache is None:
            cache = JsonCache(options.cache_dir)
        if options.llm_mode == "batch" and settings.provider != "api":
            raise LlmFocusError(
                "--llm-mode batch requires personalization.llm_focus.provider 'api'; "
                f"provider '{settings.provider}' packs several domains per call instead"
            )
        transport = build_transport(
            settings,
            mode=options.llm_mode,
            concurrency=options.llm_concurrency,
            poll_seconds=options.llm_poll_seconds,
            existing_batch_ids=tuple(options.llm_batch_ids),
            log=log,
            max_nominal_usd=options.llm_budget_usd,
        )
        offer = campaign.data["offer"]
        classifier = LlmFocusClassifier(
            settings,
            cache=cache,
            transport=transport,
            offer_service=str(offer["service"]),
            offer_audience=str(offer["audience"]),
            limits=PhraseLimits(
                max_focus_words=campaign.max_focus_words,
                max_buyer_phrase_words=campaign.max_buyer_phrase_words,
                banned_phrases=campaign.banned_phrases,
                max_pitch_words=settings.max_pitch_words,
                max_source_phrase_words=campaign.max_source_phrase_words,
            ),
            cache_ttl_hours=options.llm_cache_ttl_hours,
            approved_claims=tuple(str(item) for item in offer.get("approved_claims", [])),
            forbidden_claims=tuple(str(item) for item in offer.get("forbidden_claims", [])),
            blocked_evidence_phrases=campaign.blocked_evidence_phrases,
        )
    items = [
        LlmFocusItem(
            domain=domain,
            company_name=company_context.get(domain, ("", ""))[0],
            evidence_text=text,
            source_url=signal.source_url,
            note=_llm_note(signal),
            job_title=company_context.get(domain, ("", ""))[1],
        )
        for domain, signal in domain_results.items()
        if (text := _llm_evidence_text(signal))
    ]
    if log is not None and items:
        log(
            f"classifying {len(items)} readable domains with {settings.model} "
            f"via {settings.provider}"
        )
    decisions = classifier.classify(items) if items else {}
    return decisions, classifier.stats()


def _llm_candidate(
    decision: LlmFocusDecision,
    signal: SiteSignal,
) -> tuple[CompanyFact, CommercialFocusResult]:
    fact = CompanyFact(
        signal_type=decision.signal_type,
        focus=decision.focus,
        observation=f"your site describes {decision.focus}",
        evidence=decision.evidence,
        source_url=signal.source_url,
        confidence=round(decision.confidence, 2),
    )
    focus = CommercialFocusResult(
        source_focus=decision.focus,
        focus=decision.focus,
        buyer_phrase=decision.buyer_phrase,
        rule_id=LLM_RULE_ID,
        priority=-1,
        fit_tier=decision.fit_tier,
    )
    return fact, focus


def _resolve_focus_candidates(
    facts: tuple[CompanyFact, ...],
    *,
    company_name: str,
    commercial_focuses: CommercialFocusTable,
    campaign: CampaignConfig,
    llm_candidate: tuple[CompanyFact, CommercialFocusResult] | None = None,
) -> tuple[
    list[tuple[int, int, CompanyFact, CommercialFocusResult]],
    list[str],
]:
    """Map every candidate fact to a focus; a usable model decision ranks first."""
    resolved: list[tuple[int, int, CompanyFact, CommercialFocusResult]] = []
    focus_errors: list[str] = []
    for candidate_index, candidate in enumerate(facts):
        if llm_candidate is not None and candidate is llm_candidate[0]:
            resolved.append(
                (llm_candidate[1].priority, candidate_index, candidate, llm_candidate[1])
            )
            continue
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
        resolved.append((resolved_focus.priority, candidate_index, candidate, resolved_focus))
    return resolved, focus_errors


LLM_PITCH_ANGLE_ID = "llm-pitch"


def _llm_pitch_angle(
    campaign: CampaignConfig,
    signal_type: str,
    domain: str,
) -> CopyAngle:
    """One template whose pitch is the model's own sentence; subject stays approved."""
    base = _angle_for_signal(campaign, signal_type)
    subject = _stable_template_order(base.templates, domain)[0].subject
    return CopyAngle(
        angle_id=LLM_PITCH_ANGLE_ID,
        signal_types=(signal_type,),
        templates=(
            CopyTemplate(template_id=LLM_PITCH_ANGLE_ID, subject=subject, pitch="{{llm_pitch}}"),
        ),
    )


def _annotate_llm_reason(
    result: QualificationResult,
    decision: LlmFocusDecision | None,
    selected_rule: str,
) -> QualificationResult:
    """Keep the model's own explanation, or its rejection, in the audit reason."""
    if decision is None:
        return result
    if decision.usable and selected_rule == LLM_RULE_ID:
        note = f"model: {decision.reason}" if decision.reason else "model decision"
    elif not decision.usable:
        note = f"model decision {decision.status}: {decision.error}"
    else:
        return result
    return QualificationResult(
        status=result.status,
        rule=result.rule,
        reason=f"{result.reason}; {note}" if result.reason else note,
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

    blocked = campaign.blocked_evidence_phrases
    if blocked:
        candidates = [
            candidate
            for candidate in candidates
            if not any(
                phrase in candidate.evidence.casefold() for phrase in blocked
            )
        ]

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
    *,
    website_available: bool = False,
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
        return min(
            website_mapped,
            key=lambda item: (item[0], -item[2].confidence, item[1]),
        ), ()

    website_any = [
        item for item in candidates if not item[2].source_url.startswith("input:")
    ]
    if website_any:
        # A readable first-party site with no campaign match is negative evidence.
        # CSV enrichment may only rescue an unavailable site, never an unclear one.
        return min(
            website_any,
            key=lambda item: (item[0], -item[2].confidence, item[1]),
        ), ()

    if website_available:
        # Blocking unusable first-party facts must not silently turn a readable
        # website into a CSV-fallback case.
        return None, ()

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


def _fallback_company_decision(
    company_result: QualificationResult,
    fact: CompanyFact | None,
    focus: CommercialFocusResult | None,
    campaign: CampaignConfig,
) -> tuple[QualificationResult, str] | None:
    """Return an opt-in fallback, including exclusions only when explicitly allowed."""
    if not campaign.fallback_copy_enabled:
        return None

    fallback_status = (
        "qualified" if campaign.fallback_copy_status == "ready" else "review"
    )
    if (
        company_result.status == "review"
        and campaign.fallback_promote_company_review
        and focus is not None
        and focus.fit_tier != "exclude"
    ):
        return (
            QualificationResult(
                status=fallback_status,
                rule=focus.rule_id,
                reason=(
                    "campaign permits outreach from review-level company evidence"
                ),
            ),
            "mapped",
        )

    if company_result.status != "excluded":
        return None
    if (
        focus is not None
        and focus.fit_tier == "exclude"
        and focus.rule_id != "generic-compression"
    ):
        if not campaign.fallback_allow_explicit_company_exclusions:
            return None
        return (
            QualificationResult(
                status=fallback_status,
                rule="title-fallback",
                reason=(
                    "campaign permits title-based outreach despite explicit company "
                    f"rule '{focus.rule_id}'"
                ),
            ),
            "title",
        )
    if (
        fact is not None
        and focus is not None
        and fact.source_url.startswith("input:")
        and focus.rule_id != "generic-compression"
        and focus.fit_tier != "exclude"
        and campaign.fallback_allow_single_csv_field
    ):
        return (
            QualificationResult(
                status=fallback_status,
                rule=focus.rule_id,
                reason="campaign permits outreach from one approved CSV company field",
            ),
            "mapped",
        )
    if (
        company_result.rule in {"no-company-evidence", "generic-compression"}
        and campaign.fallback_allow_unmatched_company
    ):
        return (
            QualificationResult(
                status=fallback_status,
                rule="title-fallback",
                reason=(
                    "campaign permits title-based outreach when company evidence is "
                    "unmatched"
                ),
            ),
            "title",
        )
    return None


_COPY_OUTPUT_FIELDS = (
    "personalized_subject",
    "personalized_email",
    "personalization_angle",
    "personalization_template",
    "personalization_cta_variant",
    "personalization_cta",
    "personalization_offer_variant",
    "personalization_offer_line",
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
    if (
        row.get("company_contact_status") == "later-wave"
        and status in {"ready", "review"}
    ):
        status = "review"
        sequencing_reason = row.get(
            "company_contact_reason",
            "additional eligible contact at the same company",
        )
        if sequencing_reason and sequencing_reason not in reason:
            reason = f"{reason}; sequencing: {sequencing_reason}"
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


def _seniority_rank(value: str, campaign: CampaignConfig) -> int:
    normalized = value.strip().casefold()
    ordered = (*campaign.ready_seniorities, *campaign.review_seniorities)
    for index, configured in enumerate(ordered):
        if normalized == configured.casefold():
            return len(ordered) - index
    return 0


def _title_priority_rank(value: str, campaign: CampaignConfig) -> int:
    for index, pattern in enumerate(campaign.contact_priority_patterns):
        if re.search(pattern, value, re.I):
            return len(campaign.contact_priority_patterns) - index
    return 0


def _sequence_company_contacts(
    rows: list[dict[str, str]],
    domains: list[str],
    campaign: CampaignConfig,
    *,
    seniority_header: str,
    title_header: str,
) -> dict[str, int]:
    """Keep one strongest contact per company ready and hold the rest for later waves."""
    grouped: dict[str, list[int]] = {}
    for index, (row, domain) in enumerate(zip(rows, domains, strict=True)):
        row["company_contact_status"] = "not-eligible"
        row["company_contact_rank"] = ""
        row["company_contact_count"] = ""
        row["company_contact_reason"] = "row is not eligible for contact sequencing"
        if (
            domain
            and row.get("outreach_status") in {"ready", "review"}
            and row.get("personalized_email")
        ):
            grouped.setdefault(domain, []).append(index)

    outreach_rank = {"ready": 2, "review": 1}
    fit_rank = {"qualified": 2, "review": 1, "excluded": 0}
    copy_rank = {"ready": 2, "review": 1, "blank": 0, "error": -1}
    later_wave_rows = 0
    multi_contact_companies = 0
    for indexes in grouped.values():
        if len(indexes) > 1:
            multi_contact_companies += 1
        ranked = sorted(
            indexes,
            key=lambda index: (
                -outreach_rank.get(rows[index].get("outreach_status", "review"), 0),
                -fit_rank.get(rows[index].get("contact_fit_status", "excluded"), 0),
                -fit_rank.get(rows[index].get("email_fit_status", "excluded"), 0),
                -fit_rank.get(rows[index].get("company_fit_status", "excluded"), 0),
                -copy_rank.get(rows[index].get("personalization_status", "error"), -1),
                -_title_priority_rank(rows[index].get(title_header, ""), campaign),
                -_seniority_rank(rows[index].get(seniority_header, ""), campaign),
                index,
            ),
        )
        contact_count = len(ranked)
        for rank, index in enumerate(ranked, start=1):
            row = rows[index]
            row["company_contact_rank"] = str(rank)
            row["company_contact_count"] = str(contact_count)
            if rank == 1:
                row["company_contact_status"] = "primary"
                row["company_contact_reason"] = (
                    "highest-ranked eligible contact for this company"
                )
            else:
                later_wave_rows += 1
                row["company_contact_status"] = "later-wave"
                row["company_contact_reason"] = (
                    f"additional eligible contact at the same company; hold for wave {rank}"
                )
            _refresh_outreach_status(row)

    return {
        "eligible_company_groups": len(grouped),
        "multi_contact_companies": multi_contact_companies,
        "later_wave_rows": later_wave_rows,
    }


def _clean_first_name(value: str) -> str:
    """Keep only the conversational given name from noisy provider fields."""
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        return ""
    return cleaned.split()[0].strip(" ,;:")


def _company_assignment_key(
    row: dict[str, str],
    domain: str,
    column_map: dict[str, str],
) -> str:
    """Return a stable company key even when the input has no usable website."""
    if domain:
        return domain
    company_header = column_map.get("company_name", "")
    company_name = re.sub(
        r"[^a-z0-9]+",
        " ",
        row.get(company_header, "").casefold(),
    ).strip()
    if company_name:
        identity = f"company-name:{company_name}"
    else:
        email_header = column_map.get("email", "")
        email = normalize_email(row.get(email_header, ""))
        identity = f"contact:{email}" if email else json.dumps(row, sort_keys=True)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"fallback:{digest}"


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


def _overflow_domains(
    kind: str, value: str, affected: set[str], allowed: int
) -> tuple[str, ...]:
    """Deterministically pick the domains beyond a share cap for review routing."""
    ordered = sorted(
        affected,
        key=lambda domain: hashlib.sha256(
            f"quality-overflow:{kind}:{value}:{domain}".encode("utf-8")
        ).hexdigest(),
    )
    return tuple(ordered[max(allowed, 1):])


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
    offer_line_domains: dict[str, set[str]] = {}
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
        offer_line = row.get("personalization_offer_line", "").casefold()
        if offer_line:
            offer_line_domains.setdefault(offer_line, set()).add(domain)

    flags_by_domain: dict[str, list[str]] = {}
    warnings: list[dict[str, object]] = []
    for opening, affected in sorted(opening_domains.items()):
        share = len(affected) / total
        if share > float(quality["max_opening_share"]):
            allowed = int(total * float(quality["max_opening_share"]))
            overflow = _overflow_domains("opening", opening, affected, allowed)
            message = (
                f"opening '{opening}' appears on {share:.1%} of rendered domains; "
                "holding the overflow for review"
            )
            warnings.append(
                {
                    "type": "opening_share",
                    "opening": opening,
                    "count": len(affected),
                    "share": round(share, 4),
                    "flagged": len(overflow),
                }
            )
            for domain in overflow:
                flags_by_domain.setdefault(domain, []).append(message)

    angle_domain_counts = Counter(
        row.get("personalization_angle", "") for row in representatives.values()
    )
    angle_template_counts = {
        angle.angle_id: len(angle.templates) for angle in campaign.angles
    }
    for pitch, affected in exact_domains.items():
        share = len(affected) / total
        representative = next(
            row
            for row in representatives.values()
            if row.get(campaign.output_field, "").casefold() == pitch
        )
        angle_id = representative.get("personalization_angle", "")
        template_count = max(1, angle_template_counts.get(angle_id, 1))
        angle_domain_count = angle_domain_counts.get(angle_id, total)
        balanced_capacity = (
            angle_domain_count + template_count - 1
        ) // template_count
        balanced_floor = balanced_capacity / total
        configured_limit = float(quality["max_exact_pitch_share"])
        effective_limit = max(configured_limit, balanced_floor)
        configured_capacity = int(total * configured_limit + 1e-12)
        allowed = max(configured_capacity, balanced_capacity)
        if len(affected) > max(allowed, 1):
            overflow = _overflow_domains("exact-pitch", pitch, affected, allowed)
            message = (
                f"exact pitch appears on {share:.1%} of rendered domains; "
                "holding the overflow for review"
            )
            warnings.append(
                {
                    "type": "exact_pitch_share",
                    "count": len(affected),
                    "share": round(share, 4),
                    "configured_limit": quality["max_exact_pitch_share"],
                    "effective_limit": round(effective_limit, 4),
                    "flagged": len(overflow),
                }
            )
            for domain in overflow:
                flags_by_domain.setdefault(domain, []).append(message)

    buyer_phrase_limit = quality["max_buyer_phrase_share"]
    if buyer_phrase_limit is not None:
        for buyer_phrase, affected in sorted(buyer_phrase_domains.items()):
            share = len(affected) / total
            if share > float(buyer_phrase_limit):
                allowed = int(total * float(buyer_phrase_limit))
                overflow = _overflow_domains(
                    "buyer-phrase", buyer_phrase, affected, allowed
                )
                message = (
                    f"buyer phrase '{buyer_phrase}' appears on {share:.1%} "
                    "of rendered domains; holding the overflow for review"
                )
                warnings.append(
                    {
                        "type": "buyer_phrase_share",
                        "buyer_phrase": buyer_phrase,
                        "count": len(affected),
                        "share": round(share, 4),
                        "flagged": len(overflow),
                    }
                )
                for domain in overflow:
                    flags_by_domain.setdefault(domain, []).append(message)

    for cta, affected in sorted(cta_domains.items()):
        share = len(affected) / total
        if share > float(quality["max_cta_share"]):
            allowed = int(total * float(quality["max_cta_share"]))
            overflow = _overflow_domains("cta", cta, affected, allowed)
            message = (
                f"CTA '{cta}' appears on {share:.1%} of rendered domains; "
                "holding the overflow for review"
            )
            warnings.append(
                {
                    "type": "cta_share",
                    "cta": cta,
                    "count": len(affected),
                    "share": round(share, 4),
                    "flagged": len(overflow),
                }
            )
            for domain in overflow:
                flags_by_domain.setdefault(domain, []).append(message)

    for offer_line, affected in sorted(offer_line_domains.items()):
        share = len(affected) / total
        if share > float(quality["max_offer_line_share"]):
            allowed = int(total * float(quality["max_offer_line_share"]))
            overflow = _overflow_domains("offer-line", offer_line, affected, allowed)
            message = (
                f"offer line '{offer_line}' appears on {share:.1%} of rendered "
                "domains; holding the overflow for review"
            )
            warnings.append(
                {
                    "type": "offer_line_share",
                    "offer_line": offer_line,
                    "count": len(affected),
                    "share": round(share, 4),
                    "flagged": len(overflow),
                }
            )
            for domain in overflow:
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


DIGEST_FIELDS = [
    "company_enrichment_status",
    "company_enrichment_error",
    "company_page_source",
    "company_page_digest",
]


def run_digests(
    *,
    input_path: str | Path,
    output_path: str | Path,
    options: RunOptions,
    progress: ProgressCallback | None = None,
    domain_enricher: DomainEnricher | None = None,
    max_digest_chars: int = 1500,
) -> dict[str, object]:
    """Fetch and summarise company pages with no campaign and no model.

    Used by the setup interview to look at a sample of the list before an ICP
    exists. Writes one row per input row with the page digest attached.
    """
    started_at = datetime.now(UTC)
    started_monotonic = time.monotonic()
    data = load_csv(input_path, company_only=True)
    output_resolved = Path(output_path).expanduser().resolve()
    if data.path == output_resolved:
        raise ValueError("--output must be different from --input")
    domains = [domain_for_row(row, data.column_map) for row in data.rows]
    unique_domains = list(dict.fromkeys(domain for domain in domains if domain))
    fetcher: HttpFetcher | None = None
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
        domain_enricher = SiteEnricher(
            fetcher,
            cache,
            max_pages=options.max_pages,
            cache_ttl_hours=options.cache_ttl_hours,
            refresh_cache=options.refresh_cache,
        )
    domain_results = _enrich_domains(
        unique_domains, domain_enricher, concurrency=options.concurrency, progress=progress
    )
    output_rows: list[dict[str, str]] = []
    for row, domain in zip(data.rows, domains, strict=True):
        signal = domain_results.get(domain) if domain else None
        digest = _llm_evidence_text(signal) if signal is not None else ""
        output_row = dict(row)
        output_row.update(
            {
                "company_enrichment_status": signal.status if signal else "missing_domain",
                "company_enrichment_error": signal.error if signal else "",
                "company_page_source": signal.source_url if signal else "",
                "company_page_digest": " ".join(digest.split())[:max_digest_chars],
            }
        )
        output_rows.append(output_row)
    write_enriched_csv(output_resolved, data.headers, output_rows, DIGEST_FIELDS)
    status_counts = Counter(signal.status for signal in domain_results.values())
    manifest: dict[str, object] = {
        "tool": {"name": "bulk-enrich", "version": __version__},
        "mode": "digest_only",
        "run": {
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "duration_seconds": round(time.monotonic() - started_monotonic, 3),
        },
        "input": {**summarize_csv(data).to_dict(), "sha256": sha256_file(data.path)},
        "output": {
            "path": str(output_resolved),
            "sha256": sha256_file(output_resolved),
            "appended_fields": DIGEST_FIELDS,
            "row_count": len(output_rows),
        },
        "domains": {
            "unique": len(unique_domains),
            "status_counts": dict(sorted(status_counts.items())),
            "with_page_text": sum(
                1 for signal in domain_results.values() if _llm_evidence_text(signal)
            ),
        },
        "http": fetcher.stats() if fetcher is not None else {"test_double": True},
    }
    manifest_path = options.manifest_path or output_resolved.with_suffix(
        output_resolved.suffix + ".manifest.json"
    )
    _atomic_json(manifest_path, manifest)
    return manifest


COMPANY_QUALIFICATION_FIELDS = [
    "company_qualification_status",
    "company_fit_status",
    "company_fit_tier",
    "company_fit_rule",
    "company_fit_source",
    "company_fit_evidence",
    "company_fit_confidence",
    "company_fit_reason",
    "company_enrichment_status",
    "company_enrichment_error",
]


def _company_output_paths(
    input_path: Path,
    output_path: str | Path,
    options: RunOptions,
) -> tuple[Path, Path | None, Path | None]:
    output_resolved = Path(output_path).expanduser().resolve()
    fit_output_resolved = (
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
        for path in (output_resolved, fit_output_resolved, review_output_resolved)
        if path is not None
    ]
    if input_path in requested_outputs:
        raise ValueError("output paths must differ from --input")
    if len(requested_outputs) != len(set(requested_outputs)):
        raise ValueError("--output, --ready-output, and --review-output must differ")
    return output_resolved, fit_output_resolved, review_output_resolved


def run_company_qualification(
    *,
    input_path: str | Path,
    output_path: str | Path,
    campaign: CampaignConfig,
    commercial_focuses: CommercialFocusTable,
    options: RunOptions,
    progress: ProgressCallback | None = None,
    domain_enricher: DomainEnricher | None = None,
    llm_classifier: LlmFocusClassifier | None = None,
    log: LogCallback | None = None,
) -> dict[str, object]:
    """Classify companies without invoking contact, email, or copy processing."""
    started_at = datetime.now(UTC)
    started_monotonic = time.monotonic()
    data = load_csv(input_path, company_only=True)
    input_resolved = data.path
    output_resolved, fit_output_resolved, review_output_resolved = (
        _company_output_paths(input_resolved, output_path, options)
    )
    domains = [domain_for_row(row, data.column_map) for row in data.rows]
    unique_domains = list(dict.fromkeys(domain for domain in domains if domain))

    fetcher: HttpFetcher | None = None
    firecrawl_fetcher: FirecrawlFetcher | None = None
    cache: JsonCache | None = None
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
    llm_decisions, llm_stats = _classify_domains_with_llm(
        campaign,
        options,
        domain_results,
        _company_context_by_domain(data.rows, domains, data.column_map, campaign),
        classifier=llm_classifier,
        cache=cache,
        log=log,
    )

    output_rows: list[dict[str, str]] = []
    fit_counts: Counter[str] = Counter()
    company_fit_counts: Counter[str] = Counter()
    company_rule_counts: Counter[str] = Counter()
    unmatched_samples: list[dict[str, str]] = []
    excluded_samples: list[dict[str, str]] = []
    unmatched_domains: set[str] = set()
    excluded_domains: set[str] = set()
    reported_domains: set[str] = set()

    for row, domain in zip(data.rows, domains, strict=True):
        output_row = dict(row)
        context = context_for_row(row, data.column_map)
        company_name = str(context.get("company_name", "")).strip() or domain
        signal = domain_results.get(domain) if domain else None
        fact: CompanyFact | None = None
        commercial_focus: CommercialFocusResult | None = None
        company_result = QualificationResult(
            status="review",
            rule="missing-company-domain",
            reason="company domain or website is missing",
        )

        llm_decision = llm_decisions.get(domain) if domain else None
        if domain and signal is not None:
            facts = _candidate_facts(signal, row, campaign)
            llm_candidate = None
            if llm_decision is not None and llm_decision.usable:
                llm_candidate = _llm_candidate(llm_decision, signal)
                facts = (llm_candidate[0], *facts)
            resolved_candidates, _focus_errors = _resolve_focus_candidates(
                facts,
                company_name=company_name,
                commercial_focuses=commercial_focuses,
                campaign=campaign,
                llm_candidate=llm_candidate,
            )
            selected, corroborating_fields = _select_company_candidate(
                resolved_candidates,
                campaign,
                website_available=signal.status == "ok" or llm_candidate is not None,
            )
            if selected is not None:
                _priority, _index, fact, commercial_focus = selected
                company_result = _annotate_llm_reason(
                    qualify_company(
                        fact,
                        commercial_focus,
                        corroborating_fields=corroborating_fields,
                        fallback_min_agreeing_fields=campaign.fallback_min_agreeing_fields,
                        min_confidence=campaign.min_confidence,
                    ),
                    llm_decision,
                    commercial_focus.rule_id,
                )
            elif signal.status == "ok":
                company_result = QualificationResult(
                    status="excluded",
                    rule="no-company-evidence",
                    reason="no campaign-mapped company evidence was found",
                )
            else:
                company_result = QualificationResult(
                    status="review",
                    rule="site-unavailable",
                    reason=(
                        "company website could not be qualified automatically"
                        + (f": {signal.error}" if signal.error else "")
                    ),
                )

        public_status = {
            "qualified": "fit",
            "review": "needs_review",
            "excluded": "not_fit",
        }[company_result.status]
        fit_counts[public_status] += 1
        company_fit_counts[company_result.status] += 1
        company_rule_counts[company_result.rule] += 1
        tier = commercial_focus.fit_tier if commercial_focus is not None else "none"
        source = fact.source_url if fact is not None else (signal.source_url if signal else "")
        evidence = fact.evidence if fact is not None else (signal.evidence if signal else "")
        confidence = fact.confidence if fact is not None else (signal.confidence if signal else 0.0)
        output_row.update(
            {
                "company_qualification_status": public_status,
                "company_fit_status": company_result.status,
                "company_fit_tier": tier,
                "company_fit_rule": company_result.rule,
                "company_fit_source": source,
                "company_fit_evidence": evidence,
                "company_fit_confidence": f"{confidence:.2f}",
                "company_fit_reason": company_result.reason,
                "company_enrichment_status": signal.status if signal else "missing_domain",
                "company_enrichment_error": signal.error if signal else "",
            }
        )
        output_rows.append(output_row)

        if domain and domain not in reported_domains and public_status == "not_fit":
            reported_domains.add(domain)
            sample = {
                "domain": domain,
                "rule": company_result.rule,
                "tier": tier,
                "source": source,
                "evidence": evidence[:240],
            }
            if tier == "exclude" and company_result.rule != "generic-compression":
                excluded_domains.add(domain)
                if len(excluded_samples) < 25:
                    excluded_samples.append(sample)
            else:
                unmatched_domains.add(domain)
                if len(unmatched_samples) < 25:
                    unmatched_samples.append(sample)

    fit_rows = [
        row for row in output_rows if row["company_qualification_status"] == "fit"
    ]
    review_rows = [
        row
        for row in output_rows
        if row["company_qualification_status"] == "needs_review"
    ]
    write_enriched_csv(
        output_resolved,
        data.headers,
        output_rows,
        COMPANY_QUALIFICATION_FIELDS,
    )
    if fit_output_resolved is not None:
        write_enriched_csv(
            fit_output_resolved,
            data.headers,
            fit_rows,
            COMPANY_QUALIFICATION_FIELDS,
        )
    if review_output_resolved is not None:
        write_enriched_csv(
            review_output_resolved,
            data.headers,
            review_rows,
            COMPANY_QUALIFICATION_FIELDS,
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
    manifest: dict[str, object] = {
        "tool": {"name": "bulk-enrich", "version": __version__},
        "mode": "company_qualification_only",
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
            **summarize_csv(data).to_dict(),
            "sha256": sha256_file(input_resolved),
        },
        "output": {
            "path": str(output_resolved),
            "sha256": sha256_file(output_resolved),
            "manifest_path": str(manifest_path),
            "appended_fields": COMPANY_QUALIFICATION_FIELDS,
            "status_counts": dict(sorted(fit_counts.items())),
            "fit_output": (
                {
                    "path": str(fit_output_resolved),
                    "sha256": sha256_file(fit_output_resolved),
                    "row_count": len(fit_rows),
                }
                if fit_output_resolved is not None
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
            "page_cache_hits": sum(
                signal.http_cache_hits for signal in domain_results.values()
            ),
            "pages_fetched": sum(
                signal.pages_fetched for signal in domain_results.values()
            ),
        },
        "qualification": {
            "status_counts": dict(sorted(fit_counts.items())),
            "company_status_counts": dict(sorted(company_fit_counts.items())),
            "company_rule_counts": dict(sorted(company_rule_counts.items())),
            "contact_email_and_copy_skipped": True,
        },
        "focus_gaps": {
            "unmatched_domains": len(unmatched_domains),
            "unmatched_samples": unmatched_samples,
            "excluded_domains": len(excluded_domains),
            "excluded_samples": excluded_samples,
        },
        "llm_focus": llm_stats,
        "settings": {
            **asdict(options),
            "cache_dir": str(options.cache_dir),
            "manifest_path": str(manifest_path),
            "ready_output_path": (
                str(fit_output_resolved) if fit_output_resolved is not None else None
            ),
            "fit_output_path": (
                str(fit_output_resolved) if fit_output_resolved is not None else None
            ),
            "review_output_path": (
                str(review_output_resolved) if review_output_resolved is not None else None
            ),
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
    llm_classifier: LlmFocusClassifier | None = None,
    log: LogCallback | None = None,
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
    company_keys = [
        _company_assignment_key(row, domain, data.column_map)
        for row, domain in zip(data.rows, domains, strict=True)
    ]
    unique_domains = list(dict.fromkeys(domain for domain in domains if domain))

    fetcher: HttpFetcher | None = None
    firecrawl_fetcher: FirecrawlFetcher | None = None
    cache: JsonCache | None = None
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
    llm_decisions, llm_stats = _classify_domains_with_llm(
        campaign,
        options,
        domain_results,
        _company_context_by_domain(data.rows, domains, data.column_map, campaign),
        classifier=llm_classifier,
        cache=cache,
        log=log,
    )

    append_fields = list(campaign.data["output"]["append_fields"])
    output_rows: list[dict[str, str]] = []
    render_jobs: list[_RenderJob] = []
    for row, domain, company_key in zip(
        data.rows,
        domains,
        company_keys,
        strict=True,
    ):
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
        company_name = str(context.get("company_name", "")).strip()
        if company_name:
            company_result = QualificationResult(
                status="excluded",
                rule="no-company-evidence",
                reason="no campaign-mapped company evidence was found",
            )
        else:
            company_result = QualificationResult(
                status="excluded",
                rule="missing-company-name",
                reason="company name is missing",
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
            "personalization_offer_variant": "",
            "personalization_offer_line": "",
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
            "company_contact_status": "not-eligible",
            "company_contact_rank": "",
            "company_contact_count": "",
            "company_contact_reason": "row is not eligible for contact sequencing",
            "outreach_status": "excluded",
            "outreach_reason": "",
        }
        errors: list[str] = []
        facts: tuple[CompanyFact, ...] = ()
        fact: CompanyFact | None = None
        commercial_focus: CommercialFocusResult | None = None
        corroborating_fields: tuple[str, ...] = ()
        fallback_basis = ""
        fallback_angle: CopyAngle | None = None
        pitch_word_limit: int | None = None
        hook = None

        llm_decision = llm_decisions.get(domain) if domain else None
        if not company_name:
            errors.append("missing company name")
        elif not domain:
            errors.append("missing company domain or website")
        else:
            facts = _candidate_facts(signal, row, campaign)
            llm_candidate = None
            if llm_decision is not None and llm_decision.usable:
                llm_candidate = _llm_candidate(llm_decision, signal)
                facts = (llm_candidate[0], *facts)
            if not facts:
                errors.append(
                    signal.error
                    if signal is not None and signal.error
                    else "domain enrichment did not return a usable result"
                )
            else:
                resolved_candidates, focus_errors = _resolve_focus_candidates(
                    facts,
                    company_name=company_name,
                    commercial_focuses=commercial_focuses,
                    campaign=campaign,
                    llm_candidate=llm_candidate,
                )
                selected_candidate, corroborating_fields = _select_company_candidate(
                    resolved_candidates,
                    campaign,
                    website_available=(
                        (signal is not None and signal.status == "ok")
                        or llm_candidate is not None
                    ),
                )
                if selected_candidate is not None:
                    _priority, _index, fact, commercial_focus = selected_candidate
                    company_result = _annotate_llm_reason(
                        qualify_company(
                            fact,
                            commercial_focus,
                            corroborating_fields=corroborating_fields,
                            fallback_min_agreeing_fields=campaign.fallback_min_agreeing_fields,
                            min_confidence=campaign.min_confidence,
                        ),
                        llm_decision,
                        commercial_focus.rule_id,
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

        fallback_decision = _fallback_company_decision(
            company_result,
            fact,
            commercial_focus,
            campaign,
        )
        if fallback_decision is not None and company_name:
            fallback_company_result, candidate_fallback_basis = fallback_decision
            if candidate_fallback_basis == "title":
                title = str(context.get("job_title", "")).strip()
                try:
                    hook = title_hooks.match(title)
                except ValueError as exc:
                    errors.append(f"title hook: {exc}")
                if hook is not None:
                    fallback_templates = campaign.fallback_templates_for_persona(
                        hook.persona
                    )
                    if not fallback_templates:
                        errors.append(
                            f"no fallback copy template for persona '{hook.persona}'"
                        )
                    else:
                        company_result = fallback_company_result
                        fallback_basis = candidate_fallback_basis
                        errors = []
                        fact = CompanyFact(
                            signal_type="title",
                            focus=title,
                            observation=f"contact title: {title}",
                            evidence=title,
                            source_url="input:Job title",
                            confidence=0.60,
                        )
                        facts = (fact, *facts)
                        commercial_focus = CommercialFocusResult(
                            source_focus=title,
                            focus=hook.persona,
                            buyer_phrase="",
                            rule_id="title-fallback",
                            priority=1_000_001,
                            fit_tier="core",
                        )
                        fallback_angle = CopyAngle(
                            angle_id="title-fallback",
                            signal_types=("title",),
                            templates=fallback_templates,
                        )
            else:
                company_result = fallback_company_result
                fallback_basis = candidate_fallback_basis
                errors = []

        values.update(
            {
                "company_fit_status": company_result.status,
                "company_fit_tier": (
                    "fallback"
                    if fallback_basis
                    else commercial_focus.fit_tier if commercial_focus else "none"
                ),
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
        low_confidence = (
            not fallback_basis
            and fact is not None
            and fact.confidence < campaign.min_confidence
        )
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
        if should_render and hook is None:
            title = str(context.get("job_title", ""))
            try:
                hook = title_hooks.match(title)
            except ValueError as exc:
                # One unmatched title must fail its own row, not the whole run.
                errors.append(f"title hook: {exc}")
                values["personalization_status"] = "error"
                should_render = False
        if should_render:
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
                    "personalization_source": fact.source_url,
                    "title_hook": hook.hook,
                    "persona": hook.persona,
                    "service": campaign.data["offer"]["service"],
                    "audience": campaign.data["offer"]["audience"],
                }
            )
            # A blank company name must fail closed as a missing merge field,
            # never render into an empty subject or greeting.
            short_name = _short_company_name(company_name)
            if short_name:
                context["company_short_name"] = short_name
            if low_confidence:
                errors.append(
                    f"signal confidence {fact.confidence:.2f} is below "
                    f"{campaign.min_confidence:.2f}"
                )
            if fallback_basis:
                values["personalization_status"] = campaign.fallback_copy_status
            else:
                values["personalization_status"] = "review" if low_confidence else "ready"
            if (
                fallback_angle is None
                and llm_decision is not None
                and llm_decision.usable
                and llm_decision.pitch
                and commercial_focus.rule_id == LLM_RULE_ID
                and campaign.llm_focus is not None
                and campaign.llm_focus.write_pitch
            ):
                context["llm_pitch"] = llm_decision.pitch
                fallback_angle = _llm_pitch_angle(campaign, fact.signal_type, company_key)
                pitch_word_limit = campaign.llm_focus.max_pitch_words
            elif llm_decision is not None and llm_decision.usable and llm_decision.pitch_error:
                errors.append(f"model pitch rejected: {llm_decision.pitch_error}")
            if (
                llm_decision is not None and llm_decision.usable
                and llm_decision.pitch_review_reason
                and campaign.llm_focus is not None and campaign.llm_focus.write_pitch
                and commercial_focus.rule_id == LLM_RULE_ID
            ):
                values["personalization_status"] = "review"
                errors.append(llm_decision.pitch_review_reason)

        values["personalization_error"] = "; ".join(error for error in errors if error)
        output_row.update(values)
        output_rows.append(output_row)
        if should_render:
            render_jobs.append(
                _RenderJob(
                    row=output_row,
                    context=context,
                    assignment_key=company_key,
                    signal_type=fact.signal_type,
                    source_evidence=fact.evidence,
                    focus_rule=commercial_focus.rule_id,
                    angle=fallback_angle,
                    max_pitch_words=pitch_word_limit,
                )
            )

    company_focus_rules = [
        (job.assignment_key, job.focus_rule) for job in render_jobs
    ]
    cta_by_company = _balanced_ctas_for_rendered_domains(
        campaign.cta_variants,
        company_focus_rules,
    )
    offer_line_by_company = _balanced_offer_lines_for_rendered_domains(
        campaign.offer_line_variants,
        company_focus_rules,
    )
    for job in render_jobs:
        try:
            rendered = _build_copy(
                campaign,
                job.context,
                job.assignment_key,
                job.signal_type,
                job.source_evidence,
                focus_rule=job.focus_rule,
                cta_variant=cta_by_company[job.assignment_key],
                offer_variant=offer_line_by_company[job.assignment_key],
                angle=job.angle,
                max_pitch_words=job.max_pitch_words,
            )
            job.row["personalized_subject"] = rendered.subject
            job.row[campaign.output_field] = rendered.pitch
            job.row["personalized_email"] = rendered.body
            job.row["personalization_angle"] = rendered.angle_id
            job.row["personalization_template"] = rendered.template_id
            job.row["personalization_cta_variant"] = rendered.cta_variant_id
            job.row["personalization_cta"] = rendered.cta
            job.row["personalization_offer_variant"] = rendered.offer_variant_id
            job.row["personalization_offer_line"] = rendered.offer_line
        except (KeyError, ValueError) as exc:
            job.row["personalization_status"] = "error"
            existing = job.row.get("personalization_error", "").strip()
            job.row["personalization_error"] = "; ".join(
                item for item in (existing, str(exc)) if item
            )

    for row in output_rows:
        _refresh_outreach_status(row)
        if row["outreach_status"] == "excluded":
            _blank_rendered_copy(row, campaign)

    duplicate_email_rows = _deduplicate_emails(
        output_rows,
        data.column_map["email"],
        campaign,
    )
    quality_report = _apply_batch_quality(output_rows, company_keys, campaign)
    for row in output_rows:
        _refresh_outreach_status(row)
    sequencing_report = _sequence_company_contacts(
        output_rows,
        company_keys,
        campaign,
        seniority_header=data.column_map.get("job_seniority", ""),
        title_header=data.column_map.get("job_title", ""),
    )
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
    # Keep genuine mapping gaps separate from intentional exclusions so the
    # operator can iterate rules without losing the exclusion audit trail.
    unmatched_samples: list[dict[str, str]] = []
    excluded_samples: list[dict[str, str]] = []
    unmatched_domains: set[str] = set()
    excluded_domains: set[str] = set()
    reported_domains: set[str] = set()
    for row, domain in zip(output_rows, domains, strict=True):
        if not domain or domain in reported_domains:
            continue
        rule = row.get("company_fit_rule", "")
        tier = row.get("company_fit_tier", "")
        sample = {
            "domain": domain,
            "rule": rule,
            "tier": tier,
            "source_focus": row.get("personalization_source_focus", ""),
            "evidence": row.get("company_fit_evidence", "")[:240],
        }
        if rule in {"no-company-evidence", "generic-compression"}:
            reported_domains.add(domain)
            unmatched_domains.add(domain)
            if len(unmatched_samples) < 25:
                unmatched_samples.append(sample)
        elif tier == "exclude":
            reported_domains.add(domain)
            excluded_domains.add(domain)
            if len(excluded_samples) < 25:
                excluded_samples.append(sample)
    rendered_company_rows: dict[str, dict[str, str]] = {}
    for row, company_key in zip(output_rows, company_keys, strict=True):
        if (
            company_key
            and row.get("personalization_offer_variant")
            and row.get("personalization_status") in {"ready", "review"}
        ):
            rendered_company_rows.setdefault(company_key, row)
    offer_test_counts = Counter(
        row["personalization_offer_variant"]
        for row in rendered_company_rows.values()
    )
    manifest: dict[str, object] = {
        "tool": {"name": "bulk-enrich", "version": __version__},
        "mode": "outreach_personalization",
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
            **summarize_csv(data).to_dict(),
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
            "company_contact_sequencing": sequencing_report,
        },
        "script_test": {
            "assignment_field": "personalization_offer_variant",
            "cohort_counts": dict(sorted(offer_test_counts.items())),
            "unique_rendered_companies": len(rendered_company_rows),
        },
        "quality": quality_report,
        "focus_gaps": {
            "unmatched_domains": len(unmatched_domains),
            "unmatched_samples": unmatched_samples,
            "excluded_domains": len(excluded_domains),
            "excluded_samples": excluded_samples,
        },
        "llm_focus": llm_stats,
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
