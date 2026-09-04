import tempfile
import unittest
from pathlib import Path

from bulk_enrich.csv_io import inspect_csv, load_csv


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

    def test_accepts_a_title_only_list_without_a_website_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "title-only.csv"
            path.write_text(
                "Email,First name,Job title,Company name\n"
                "ana@example.com,Ana,Founder,Northstar\n",
                encoding="utf-8",
            )
            summary = inspect_csv(path)
            self.assertEqual(summary.row_count, 1)
            self.assertEqual(summary.domains_present, 0)
            self.assertEqual(summary.unique_domain_count, 0)
            self.assertNotIn("company_domain", summary.column_map)
            self.assertNotIn("company_website", summary.column_map)

    def test_company_only_mode_accepts_domain_and_linkedin_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "companies.csv"
            path.write_text(
                "company_domain,Company LinkedIn URL\n"
                "one.example,https://www.linkedin.com/company/one\n"
                "two.example,https://www.linkedin.com/company/two\n",
                encoding="utf-8",
            )
            summary = inspect_csv(path, company_only=True)
            self.assertEqual(summary.row_count, 2)
            self.assertEqual(summary.unique_domain_count, 2)
            self.assertEqual(summary.unique_email_count, 0)
            self.assertFalse(summary.email_status_present)
            with self.assertRaisesRegex(ValueError, "email, first_name, company_name"):
                load_csv(path)

    def test_company_only_mode_still_requires_a_domain_or_website_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "companies.csv"
            path.write_text("Company name\nOne\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "domain or website"):
                inspect_csv(path, company_only=True)


if __name__ == "__main__":
    unittest.main()
