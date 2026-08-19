import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from bulk_enrich.cli import _load_local_env, main


ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_loads_supported_local_env_without_overriding_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "FIRECRAWL_API_URL=http://localhost:3002\n"
                "FIRECRAWL_API_KEY=local-key\n"
                "IGNORED_VALUE=not-loaded\n",
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {"FIRECRAWL_API_KEY": "shell-key"},
                clear=True,
            ):
                _load_local_env(env_file)
                self.assertEqual(
                    os.environ["FIRECRAWL_API_URL"],
                    "http://localhost:3002",
                )
                self.assertEqual(os.environ["FIRECRAWL_API_KEY"], "shell-key")
                self.assertNotIn("IGNORED_VALUE", os.environ)

    def test_validate_only_prints_plan_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "enriched.csv"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "--input",
                        str(ROOT / "tests" / "fixtures" / "leads.csv"),
                        "--output",
                        str(output),
                        "--campaign",
                        str(ROOT / "campaigns" / "examples" / "scale-olympus.json"),
                        "--validate-only",
                    ]
                )
            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["input"]["unique_domain_count"], 2)
            self.assertGreaterEqual(payload["commercial_focus_rules"], 7)
            self.assertIn("company_fit_tiers", payload["qualification"])
            self.assertEqual(
                payload["qualification"]["missing_email_status_action"],
                "syntax",
            )
            self.assertFalse(payload["input"]["email_status_present"])
            self.assertFalse(payload["execution_ready"])
            self.assertIn("test_only", payload["execution_blocker"])
            self.assertFalse(output.exists())

    def test_production_run_refuses_unapproved_test_campaign(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(
                    [
                        "--input",
                        str(ROOT / "tests" / "fixtures" / "leads.csv"),
                        "--output",
                        str(Path(tmp) / "enriched.csv"),
                        "--campaign",
                        str(ROOT / "campaigns" / "examples" / "scale-olympus.json"),
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIn("test_only", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
