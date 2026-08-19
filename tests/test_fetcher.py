import gzip
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from bulk_enrich.cache import JsonCache
from bulk_enrich.fetcher import (
    FetcherSettings,
    HttpFetcher,
    UnsafeURLError,
    decode_http_body,
    validate_public_url,
)
from bulk_enrich.models import FetchResult


class FetcherTests(unittest.TestCase):
    def test_decodes_gzip_with_a_hard_output_limit(self) -> None:
        payload = gzip.compress(b"website evidence" * 100)
        self.assertEqual(
            decode_http_body(payload, "gzip", 16),
            b"website evidence",
        )

    def test_blocks_private_network_targets(self) -> None:
        with self.assertRaises(UnsafeURLError):
            validate_public_url("http://127.0.0.1/admin")
        with self.assertRaises(UnsafeURLError):
            validate_public_url("http://localhost/")

    def test_second_fetch_uses_disk_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fetcher = HttpFetcher(JsonCache(Path(tmp)), FetcherSettings())
            calls: list[str] = []

            def network_fetch(url: str) -> FetchResult:
                calls.append(url)
                return FetchResult(
                    url=url,
                    final_url=url,
                    status_code=200,
                    content_type="text/html",
                    body="<html><p>We help growing firms plan cash flow.</p></html>",
                    fetched_at=datetime.now(UTC).isoformat(),
                )

            fetcher._network_fetch = network_fetch  # type: ignore[method-assign]
            first = fetcher.fetch("https://example.com")
            second = fetcher.fetch("https://example.com/")
            self.assertFalse(first.from_cache)
            self.assertTrue(second.from_cache)
            self.assertEqual(calls, ["https://example.com/"])


if __name__ == "__main__":
    unittest.main()
