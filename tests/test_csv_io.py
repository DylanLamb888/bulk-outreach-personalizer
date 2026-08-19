import tempfile
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
        self.assertFalse(summary.email_status_present)
        self.assertEqual(summary.missing_email_status_values, 3)

    def test_detects_qualification_columns_and_duplicate_emails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "leads.csv"
            path.write_text(
                "Email,Email status,First name,Job title,Job seniority,Job department,Company name,Website\n"
                "SAM@example.com,VERIFIED,Sam,Founder,Founder/Owner,Leadership,One,one.example\n"
                "sam@example.com,VERIFIED,Sam,Founder,Founder/Owner,Leadership,One,one.example\n",
                encoding="utf-8",
            )
            summary = inspect_csv(path)
            self.assertEqual(summary.column_map["email_status"], "Email status")
            self.assertEqual(summary.column_map["job_seniority"], "Job seniority")
            self.assertEqual(summary.column_map["job_department"], "Job department")
            self.assertTrue(summary.email_status_present)
            self.assertEqual(summary.duplicate_email_rows, 1)


if __name__ == "__main__":
    unittest.main()
