"""One-time-per-domain website signal enrichment."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from bulk_enrich.cache import JsonCache
from bulk_enrich.extract import (
    TextCandidate,
    discover_internal_links,
    normalize_company_signal,
    page_candidates,
    parse_html,
)
from bulk_enrich.fetcher import HttpFetcher
from bulk_enrich.models import CompanyFact, FetchResult, SiteSignal


SIGNAL_ENGINE_VERSION = "5"


class PageFetcher(Protocol):
    def fetch(self, url: str) -> FetchResult: ...


class SiteEnricher:
    def __init__(
        self,
        fetcher: HttpFetcher,
        cache: JsonCache,
        *,
        max_pages: int = 2,
        cache_ttl_hours: float = 168.0,
        refresh_cache: bool = False,
        fallback_fetcher: PageFetcher | None = None,
        fallback_min_score: float = 0.86,
    ) -> None:
        self.fetcher = fetcher
        self.cache = cache
        self.max_pages = max(1, max_pages)
        self.cache_ttl_hours = cache_ttl_hours
        self.refresh_cache = refresh_cache
        self.fallback_fetcher = fallback_fetcher
        self.fallback_min_score = fallback_min_score

    def enrich(self, domain: str) -> SiteSignal:
        fallback_mode = "firecrawl" if self.fallback_fetcher is not None else "direct"
        cache_key = (
            f"{SIGNAL_ENGINE_VERSION}|{self.max_pages}|{fallback_mode}|{domain}"
        )
        if not self.refresh_cache:
            cached = self.cache.get("site-signals", cache_key, ttl_hours=self.cache_ttl_hours)
            if cached is not None:
                return SiteSignal.from_dict(cached).cached_copy()

        signal = self._enrich_uncached(domain)
        self.cache.put("site-signals", cache_key, signal.to_dict())
        return signal

    def _enrich_uncached(self, domain: str) -> SiteSignal:
        home = self._fetch_homepage(domain)
        if not home.success:
            return SiteSignal(
                domain=domain,
                observation="",
                evidence="",
                source_url=home.final_url or home.url,
                confidence=0.0,
                status="fetch_error",
                error=home.error or f"HTTP {home.status_code}",
                pages_fetched=0,
                http_cache_hits=int(home.from_cache),
            )

        pages: list[tuple[FetchResult, object]] = []
        home, home_page, candidates = self._prefer_rendered_page(home)
        pages.append((home, home_page))

        best_confidence = candidates[0].score if candidates else 0.0
        if best_confidence < 0.86 and self.max_pages > 1:
            links = discover_internal_links(
                home_page,
                home.final_url,
                domain,
                limit=self.max_pages - 1,
            )
            for url in links:
                result = self._fetch_page(url)
                if not result.success:
                    continue
                result, page, page_facts = self._prefer_rendered_page(result)
                pages.append((result, page))
                candidates.extend(page_facts)

        candidates.sort(key=lambda item: (-item.score, len(item.text)))
        if not candidates:
            return SiteSignal(
                domain=domain,
                observation="",
                evidence="",
                source_url=home.final_url,
                confidence=0.0,
                status="no_signal",
                error="no usable company description found on fetched pages",
                pages_fetched=len(pages),
                http_cache_hits=sum(int(result.from_cache) for result, _page in pages),
            )

        facts: list[CompanyFact] = []
        seen_focuses: set[str] = set()
        for candidate in candidates:
            normalized = normalize_company_signal(candidate.text)
            identity = normalized.focus.casefold()
            if not normalized.focus or identity in seen_focuses:
                continue
            seen_focuses.add(identity)
            facts.append(
                CompanyFact(
                    signal_type=normalized.signal_type,
                    focus=normalized.focus,
                    observation=normalized.observation,
                    evidence=candidate.text,
                    source_url=candidate.source_url,
                    confidence=round(candidate.score, 2),
                )
            )
            if len(facts) == 3:
                break

        best: CompanyFact = facts[0]
        return SiteSignal(
            domain=domain,
            observation=best.observation,
            evidence=best.evidence,
            source_url=best.source_url,
            confidence=best.confidence,
            status="ok",
            focus=best.focus,
            signal_type=best.signal_type,
            facts=tuple(facts),
            pages_fetched=len(pages),
            http_cache_hits=sum(int(result.from_cache) for result, _page in pages),
        )

    def _fetch_homepage(self, domain: str) -> FetchResult:
        attempts = (
            f"https://{domain}/",
            f"https://www.{domain}/",
            f"http://{domain}/",
        )
        last: FetchResult | None = None
        for url in attempts:
            result = self.fetcher.fetch(url)
            last = result
            if result.success:
                return result
        if self.fallback_fetcher is not None:
            for url in attempts[:2]:
                result = self.fallback_fetcher.fetch(url)
                last = result
                if result.success:
                    return result
        assert last is not None
        return replace(last, error="; ".join(filter(None, [last.error, "homepage unavailable"])))

    def _fetch_page(self, url: str) -> FetchResult:
        result = self.fetcher.fetch(url)
        if result.success or self.fallback_fetcher is None:
            return result
        return self.fallback_fetcher.fetch(url)

    def _prefer_rendered_page(
        self,
        result: FetchResult,
    ) -> tuple[FetchResult, object, list[TextCandidate]]:
        page = parse_html(result.body)
        candidates = page_candidates(page, result.final_url)
        best_score = candidates[0].score if candidates else 0.0
        if (
            self.fallback_fetcher is None
            or result.provider == "firecrawl"
            or best_score >= self.fallback_min_score
        ):
            return result, page, candidates

        rendered = self.fallback_fetcher.fetch(result.final_url or result.url)
        if not rendered.success:
            return result, page, candidates
        rendered_page = parse_html(rendered.body)
        rendered_candidates = page_candidates(rendered_page, rendered.final_url)
        rendered_score = rendered_candidates[0].score if rendered_candidates else 0.0
        if rendered_score > best_score:
            return rendered, rendered_page, rendered_candidates
        return result, page, candidates
