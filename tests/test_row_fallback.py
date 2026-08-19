import unittest

from bulk_enrich.config import RowFallbackField
from bulk_enrich.row_fallback import facts_from_row


class RowFallbackTests(unittest.TestCase):
    def test_builds_ordered_auditable_facts_from_configured_headers(self) -> None:
        row = {
            "Company description": "We provide sell-side M&A advisory services.",
            "AI One-Liner": "Investment bank for privately held companies.",
        }
        facts = facts_from_row(
            row,
            (
                RowFallbackField("Company description", 0.82),
                RowFallbackField("AI One-Liner", 0.78),
            ),
        )
        self.assertEqual(len(facts), 2)
        self.assertEqual(facts[0].source_url, "input:Company description")
        self.assertEqual(facts[0].confidence, 0.82)
        self.assertEqual(facts[1].source_url, "input:AI One-Liner")

    def test_skips_missing_and_placeholder_values(self) -> None:
        facts = facts_from_row(
            {"Company description": "Unknown"},
            (
                RowFallbackField("Company description", 0.82),
                RowFallbackField("AI One-Liner", 0.78),
            ),
        )
        self.assertEqual(facts, ())


if __name__ == "__main__":
    unittest.main()
