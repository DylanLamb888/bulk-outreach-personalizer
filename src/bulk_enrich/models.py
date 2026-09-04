"""Shared data models for deterministic website enrichment."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    body: str
    fetched_at: str
    from_cache: bool = False
    error: str = ""
    provider: str = "http"

    @property
    def success(self) -> bool:
        return 200 <= self.status_code < 300 and bool(self.body) and not self.error

    def cached_copy(self) -> "FetchResult":
        return replace(self, from_cache=True)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FetchResult":
        return cls(
            url=str(data.get("url", "")),
            final_url=str(data.get("final_url", "")),
            status_code=int(data.get("status_code", 0)),
            content_type=str(data.get("content_type", "")),
            body=str(data.get("body", "")),
            fetched_at=str(data.get("fetched_at", "")),
            from_cache=bool(data.get("from_cache", False)),
            error=str(data.get("error", "")),
            provider=str(data.get("provider", "http")),
        )


@dataclass(frozen=True)
class CompanyFact:
    """One normalized, source-backed fact that can drive campaign copy."""

    signal_type: str
    focus: str
    observation: str
    evidence: str
    source_url: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CompanyFact":
        return cls(
            signal_type=str(data.get("signal_type", "specialism")),
            focus=str(data.get("focus", "")),
            observation=str(data.get("observation", "")),
            evidence=str(data.get("evidence", "")),
            source_url=str(data.get("source_url", "")),
            confidence=float(data.get("confidence", 0.0)),
        )


@dataclass(frozen=True)
class SiteSignal:
    domain: str
    observation: str
    evidence: str
    source_url: str
    confidence: float
    status: str
    error: str = ""
    focus: str = ""
    signal_type: str = ""
    facts: tuple[CompanyFact, ...] = ()
    pages_fetched: int = 0
    http_cache_hits: int = 0
    signal_cache_hit: bool = False
    page_digest: str = ""

    def cached_copy(self) -> "SiteSignal":
        return replace(
            self,
            signal_cache_hit=True,
            pages_fetched=0,
            http_cache_hits=0,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SiteSignal":
        raw_facts = data.get("facts", [])
        facts = tuple(
            CompanyFact.from_dict(item)
            for item in raw_facts
            if isinstance(item, dict)
        ) if isinstance(raw_facts, list) else ()
        return cls(
            domain=str(data.get("domain", "")),
            observation=str(data.get("observation", "")),
            evidence=str(data.get("evidence", "")),
            source_url=str(data.get("source_url", "")),
            confidence=float(data.get("confidence", 0.0)),
            status=str(data.get("status", "error")),
            error=str(data.get("error", "")),
            focus=str(data.get("focus", "")),
            signal_type=str(data.get("signal_type", "")),
            facts=facts,
            pages_fetched=int(data.get("pages_fetched", 0)),
            http_cache_hits=int(data.get("http_cache_hits", 0)),
            signal_cache_hit=bool(data.get("signal_cache_hit", False)),
            page_digest=str(data.get("page_digest", "")),
        )
