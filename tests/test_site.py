import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from bulk_enrich.cache import JsonCache
from bulk_enrich.models import FetchResult
from bulk_enrich.site import SiteEnricher


ROOT = Path(__file__).resolve().parents[1]


class FakeFetcher:
    def __init__(
        self,
        body: str,
        *,
        succeeds: bool = True,
        provider: str = "http",
        final_url: str = "",
    ) -> None:
        self.body = body
        self.succeeds = succeeds
        self.provider = provider
        self.final_url = final_url
        self.calls: list[str] = []

    def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        return FetchResult(
            url=url,
            final_url=self.final_url or url,
            status_code=200 if self.succeeds else 0,
            content_type="text/html",
            body=self.body if self.succeeds else "",
            fetched_at=datetime.now(UTC).isoformat(),
            error="" if self.succeeds else "HTTP 403",
            provider=self.provider,
        )


class RoutingFetcher:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        body = self.pages.get(url, "")
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200 if body else 404,
            content_type="text/html",
            body=body,
            fetched_at=datetime.now(UTC).isoformat(),
            error="" if body else "HTTP 404",
            provider="http",
        )


class SiteEnricherTests(unittest.TestCase):
    def test_retains_substantive_detail_page_evidence_when_homepage_metadata_is_generic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = """
                <html><head><meta name="description" content="A leading private equity firm focused on fostering industry leaders."></head>
                <body><a href="/about">About</a></body></html>
            """
            about = """
                <html><head><meta name="description" content="A leading private equity firm focused on fostering industry leaders."></head>
                <body><p>We focus on acquiring private companies and partner with owners seeking a pathway to an exit transaction.</p></body></html>
            """
            fetcher = RoutingFetcher(
                {
                    "https://darkalpha.example/": home,
                    "https://darkalpha.example/about": about,
                }
            )
            enricher = SiteEnricher(
                fetcher,  # type: ignore[arg-type]
                JsonCache(Path(tmp)),
                max_pages=2,
            )

            signal = enricher.enrich("darkalpha.example")

            self.assertEqual(
                fetcher.calls,
                ["https://darkalpha.example/", "https://darkalpha.example/about"],
            )
            self.assertTrue(
                any("acquiring private companies" in fact.evidence for fact in signal.facts)
            )

    def test_extracts_once_then_uses_signal_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fetcher = FakeFetcher((ROOT / "tests" / "fixtures" / "site.html").read_text())
            enricher = SiteEnricher(
                fetcher,  # type: ignore[arg-type]
                JsonCache(Path(tmp)),
                max_pages=1,
            )
            first = enricher.enrich("northstar.example")
            second = enricher.enrich("northstar.example")
            self.assertEqual(first.status, "ok")
            self.assertGreaterEqual(first.confidence, 0.90)
            self.assertFalse(first.signal_cache_hit)
            self.assertEqual(first.signal_type, "audience")
            self.assertIn("helping owner-led businesses", first.focus)
            self.assertGreaterEqual(len(first.facts), 1)
            self.assertEqual(first.facts[0].evidence, first.evidence)
            self.assertTrue(second.signal_cache_hit)
            self.assertEqual(second.pages_fetched, 0)
            self.assertEqual(len(fetcher.calls), 1)

    def test_uses_firecrawl_after_direct_homepage_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            direct = FakeFetcher("", succeeds=False)
            firecrawl = FakeFetcher(
                (ROOT / "tests" / "fixtures" / "site.html").read_text(),
                provider="firecrawl",
            )
            enricher = SiteEnricher(
                direct,  # type: ignore[arg-type]
                JsonCache(Path(tmp)),
                max_pages=1,
                fallback_fetcher=firecrawl,
            )
            signal = enricher.enrich("northstar.example")

            self.assertEqual(signal.status, "ok")
            self.assertEqual(len(direct.calls), 3)
            self.assertEqual(firecrawl.calls, ["https://northstar.example/"])

    def test_prefers_firecrawl_when_direct_html_has_no_usable_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            direct = FakeFetcher("<html><body><p>Welcome</p></body></html>")
            firecrawl = FakeFetcher(
                (ROOT / "tests" / "fixtures" / "site.html").read_text(),
                provider="firecrawl",
            )
            enricher = SiteEnricher(
                direct,  # type: ignore[arg-type]
                JsonCache(Path(tmp)),
                max_pages=1,
                fallback_fetcher=firecrawl,
            )
            signal = enricher.enrich("northstar.example")

            self.assertEqual(signal.status, "ok")
            self.assertEqual(len(direct.calls), 1)
            self.assertEqual(firecrawl.calls, ["https://northstar.example/"])

    def test_rejects_redirect_to_an_unrelated_company_domain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fetcher = FakeFetcher(
                (ROOT / "tests" / "fixtures" / "site.html").read_text(),
                final_url="https://different-company.example/",
            )
            enricher = SiteEnricher(
                fetcher,  # type: ignore[arg-type]
                JsonCache(Path(tmp)),
                max_pages=1,
            )

            signal = enricher.enrich("northstar.example")

            self.assertEqual(signal.status, "redirect_mismatch")
            self.assertEqual(signal.facts, ())
            self.assertIn("different-company.example", signal.error)

    def test_allows_brand_preserving_cross_tld_redirect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fetcher = FakeFetcher(
                (ROOT / "tests" / "fixtures" / "site.html").read_text(),
                final_url="https://northstar.ai/",
            )
            enricher = SiteEnricher(
                fetcher,  # type: ignore[arg-type]
                JsonCache(Path(tmp)),
                max_pages=1,
            )

            signal = enricher.enrich("northstar.example")

            self.assertEqual(signal.status, "ok")
            self.assertTrue(signal.facts)


if __name__ == "__main__":
    unittest.main()
