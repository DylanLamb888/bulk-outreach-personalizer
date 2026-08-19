"""Deterministic company facts sourced from configured input CSV fields."""

from __future__ import annotations

from bulk_enrich.config import RowFallbackField
from bulk_enrich.csv_io import normalize_header
from bulk_enrich.extract import clean_text, normalize_company_signal
from bulk_enrich.models import CompanyFact


_EMPTY_VALUES = {
    "-",
    "n/a",
    "na",
    "none",
    "not available",
    "not revealed",
    "unknown",
}


def facts_from_row(
    row: dict[str, str],
    fields: tuple[RowFallbackField, ...],
) -> tuple[CompanyFact, ...]:
    """Build ordered, auditable facts from campaign-approved CSV columns."""
    normalized_headers = {
        normalize_header(header): header
        for header in row
    }
    facts: list[CompanyFact] = []
    seen: set[tuple[str, str]] = set()
    for field in fields:
        actual_header = normalized_headers.get(normalize_header(field.header))
        if actual_header is None:
            continue
        evidence = clean_text(row.get(actual_header, ""))
        if not evidence or evidence.casefold() in _EMPTY_VALUES:
            continue
        normalized = normalize_company_signal(evidence)
        if not normalized.focus:
            continue
        identity = (normalized.focus.casefold(), evidence.casefold())
        if identity in seen:
            continue
        seen.add(identity)
        facts.append(
            CompanyFact(
                signal_type=normalized.signal_type,
                focus=normalized.focus,
                observation=normalized.observation,
                evidence=evidence,
                source_url=f"input:{actual_header}",
                confidence=field.confidence,
            )
        )
    return tuple(facts)
