import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bulk_enrich.cache import JsonCache
from bulk_enrich.firecrawl import (
    FirecrawlFetcher,
    FirecrawlSettings,
    scrape_endpoint,
)


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.payload


class FakeOpener:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[tuple[object, float]] = []

    def open(self, request: object, timeout: float) -> FakeResponse:
        self.calls.append((request, timeout))
        return FakeResponse(self.payload)


class FirecrawlFetcherTests(unittest.TestCase):
    def test_hosted_firecrawl_requires_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "FIRECRAWL_API_KEY"):
                FirecrawlFetcher(
                    JsonCache(Path(tmp)),
                    FirecrawlSettings(api_url="https://api.firecrawl.dev"),
                )

    def test_builds_v2_endpoint_without_duplicating_version(self) -> None:
        self.assertEqual(
            scrape_endpoint("http://localhost:3002"),
            "http://localhost:3002/v2/scrape",
        )
        self.assertEqual(
            scrape_endpoint("http://localhost:3002/v2"),
            "http://localhost:3002/v2/scrape",
        )

    @patch("bulk_enrich.firecrawl.validate_public_url")
    def test_scrapes_clean_html_then_uses_local_cache(self, _validate: object) -> None:
        payload = {
            "success": True,
            "data": {
                "html": "<main><p>We provide working capital to manufacturers.</p></main>",
                "metadata": {
                    "sourceURL": "https://example.com/",
                    "statusCode": 200,
                },
            },
        }
        opener = FakeOpener(payload)
        with tempfile.TemporaryDirectory() as tmp:
            fetcher = FirecrawlFetcher(
                JsonCache(Path(tmp)),
                FirecrawlSettings(api_url="http://localhost:3002"),
                opener=opener,
            )
            first = fetcher.fetch("https://example.com")
            second = fetcher.fetch("https://example.com/")

        self.assertTrue(first.success)
        self.assertEqual(first.provider, "firecrawl")
        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)
        self.assertEqual(len(opener.calls), 1)
        request = opener.calls[0][0]
        self.assertEqual(request.full_url, "http://localhost:3002/v2/scrape")
        request_payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request_payload["url"], "https://example.com/")
        self.assertEqual(request_payload["formats"], ["html"])
        self.assertFalse(request_payload["onlyMainContent"])
        self.assertNotIn("authorization", {key.casefold() for key in request.headers})
        self.assertEqual(fetcher.stats()["requests"], 1)
        self.assertEqual(fetcher.stats()["cache_hits"], 1)


if __name__ == "__main__":
    unittest.main()
