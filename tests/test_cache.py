import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
