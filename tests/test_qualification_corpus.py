import unittest
from pathlib import Path

from bulk_enrich.focus import CommercialFocusTable


ROOT = Path(__file__).resolve().parents[1]


class QualificationCorpusTests(unittest.TestCase):
    def test_anonymised_niche_positive_secondary_and_negative_cases(self) -> None:
        cases = (
            ("ma", "sell-side M&A advisory", "core"),
            ("ma", "business valuations for private owners", "secondary"),
            ("ma", "commercial washing services", "exclude"),
            ("customs", "customs clearance for importers", "core"),
            ("customs", "international freight forwarding", "secondary"),
            ("customs", "a marketing agency", "exclude"),
            ("costseg", "cost segregation studies", "core"),
            ("costseg", "energy tax incentives", "secondary"),
            ("costseg", "property tax appeals", "exclude"),
            ("costseg", "accounting and tax services", "exclude"),
        )
        tables = {
            niche: CommercialFocusTable.load(
                ROOT / "tests" / "fixtures" / f"{niche}-qualification-focus.csv"
            )
            for niche in {item[0] for item in cases}
        }
        for niche, evidence, expected in cases:
            with self.subTest(niche=niche, evidence=evidence):
                result = tables[niche].resolve(
                    company_name="Example Company",
                    signal_type="service",
                    source_focus=evidence,
                    evidence=evidence,
                    max_focus_words=7,
                    max_buyer_phrase_words=8,
                )
                self.assertEqual(result.fit_tier, expected)


if __name__ == "__main__":
    unittest.main()
