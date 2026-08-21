import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bulk_enrich.cache import JsonCache


class JsonCacheTests(unittest.TestCase):
    def test_round_trip_and_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = JsonCache(Path(tmp))
            cache.put("signals", "northstar.example", {"value": "saved"})
            self.assertEqual(
                cache.get("signals", "northstar.example", ttl_hours=1),
                {"value": "saved"},
            )
            self.assertIsNone(cache.get("signals", "northstar.example", ttl_hours=0))

    def test_prune_removes_only_expired_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = JsonCache(Path(tmp))
            with patch("bulk_enrich.cache.time.time", return_value=1_000.0):
                cache.put("http-success", "old", {"value": 1})
            cache.put("http-success", "fresh", {"value": 2})
            removed = cache.prune(ttl_hours=1.0)
            self.assertEqual(removed, 1)
            self.assertIsNone(cache.get("http-success", "old", ttl_hours=1e9))
            self.assertEqual(
                cache.get("http-success", "fresh", ttl_hours=1.0),
                {"value": 2},
            )


if __name__ == "__main__":
    unittest.main()
