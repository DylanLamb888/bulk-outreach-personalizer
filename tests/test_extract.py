import unittest
from pathlib import Path

from bulk_enrich.extract import (
    discover_internal_links,
    normalize_company_signal,
    observation_from_evidence,
    page_candidates,
    parse_html,
)


ROOT = Path(__file__).resolve().parents[1]


class ExtractTests(unittest.TestCase):
    def test_extracts_auditable_company_signal(self) -> None:
        page = parse_html((ROOT / "tests" / "fixtures" / "site.html").read_text())
        candidates = page_candidates(page, "https://northstar.example/")
        self.assertTrue(candidates)
        self.assertEqual(
            candidates[0].text,
            "We help owner-led businesses understand cash flow and make better financial decisions",
        )
        self.assertEqual(
            observation_from_evidence(candidates[0].text),
            "your team helps owner-led businesses understand cash flow and make better financial decisions",
        )
        self.assertEqual(
            discover_internal_links(
                page,
                "https://northstar.example/",
                "northstar.example",
                limit=2,
            ),
            ["https://northstar.example/services"],
        )

    def test_ignores_footer_and_navigation_boilerplate(self) -> None:
        page = parse_html((ROOT / "tests" / "fixtures" / "site.html").read_text())
        all_text = " ".join((*page.headings, *page.paragraphs))
        self.assertNotIn("Privacy", all_text)
        self.assertNotIn("All rights reserved", all_text)

    def test_normalizes_real_world_business_descriptions_without_cutting_clauses(self) -> None:
        examples = {
            "World-class restoration, repair, refinishing, and sale of pianos since 1920":
                "your site highlights restoration, repair, refinishing, and sale of pianos since 1920",
            "Supplier to unique and delicious confectionery candy items for fundraising":
                "your team supplies unique and delicious confectionery candy items for fundraising",
            "Discover reclaimed fireplace mantels, stained glass windows, vintage cabinets & more at Aurora Mills in Aurora, Oregon":
                "your team offers reclaimed fireplace mantels, stained glass windows, vintage cabinets & more",
            "Balance Point’s investment model is rooted in its ability to provide flexible capital solutions":
                "your team provides flexible capital solutions",
            "Coady Diemar Partners is a boutique investment bank that provides M&A and strategic advisory":
                "your team provides M&A and strategic advisory",
            "Syntera Advisors helps forward-thinking companies connect, collaborate, and grow with confidence":
                "your team helps forward-thinking companies connect, collaborate, and grow with confidence",
        }
        for evidence, expected in examples.items():
            with self.subTest(evidence=evidence):
                self.assertEqual(observation_from_evidence(evidence), expected)

    def test_extracts_commercial_focus_and_signal_type(self) -> None:
        examples = {
            "Supplier to unique confectionery items for fundraising": (
                "product",
                "unique confectionery items for fundraising",
            ),
            "Balance Point provides flexible capital solutions": (
                "service",
                "flexible capital solutions",
            ),
            "We help owner-led firms understand cash flow": (
                "audience",
                "helping owner-led firms understand cash flow",
            ),
            "World-class restoration, repair, refinishing, and sale of pianos": (
                "specialism",
                "restoration, repair, refinishing, and piano sales",
            ),
        }
        for evidence, expected in examples.items():
            with self.subTest(evidence=evidence):
                signal = normalize_company_signal(evidence)
                self.assertEqual((signal.signal_type, signal.focus), expected)


if __name__ == "__main__":
    unittest.main()
