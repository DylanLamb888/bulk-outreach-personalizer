import unittest
from pathlib import Path

from bulk_enrich.csv_io import inspect_csv


ROOT = Path(__file__).resolve().parents[1]


class CsvInspectionTests(unittest.TestCase):
    def test_maps_headers_and_deduplicates_domains(self) -> None:
        summary = inspect_csv(ROOT / "tests" / "fixtures" / "leads.csv")
        self.assertEqual(summary.row_count, 3)
        self.assertEqual(summary.unique_domain_count, 2)
        self.assertEqual(summary.duplicate_domain_rows, 1)
        self.assertEqual(summary.missing_required["email"], 1)
        self.assertEqual(summary.column_map["company_name"], "Company name")


if __name__ == "__main__":
    unittest.main()
