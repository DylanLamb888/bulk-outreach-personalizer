import csv
import json
import tempfile
import unittest
from pathlib import Path

from bulk_enrich.config import load_campaign
from bulk_enrich.focus import CommercialFocusTable
from bulk_enrich.hooks import TitleHookTable
from bulk_enrich.models import SiteSignal
from bulk_enrich.pipeline import RunOptions, run_enrichment


ROOT = Path(__file__).resolve().parents[1]


class FailedWebsiteEnricher:
    def enrich(self, domain: str) -> SiteSignal:
        return SiteSignal(
            domain=domain,
            observation="",
            evidence="",
            source_url=f"https://{domain}/",
            confidence=0.0,
            status="fetch_error",
            error="controlled clean-room website failure",
        )


class OfferAgnosticTests(unittest.TestCase):
    def test_fresh_non_ma_campaign_uses_only_its_own_offer_and_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            focus_path = root / "bookkeeping-focus.csv"
            focus_path.write_text(
                "id,priority,pattern,signal_types,fit_tier,focus,buyer_phrase\n"
                "property-bookkeeping,10,commercial property management,*,core,"
                "property-management bookkeeping,property managers needing bookkeeping\n",
                encoding="utf-8",
            )

            payload = json.loads(
                (ROOT / "campaigns" / "campaign-template.json").read_text()
            )
            payload.update(
                {
                    "campaign_id": "bookkeeping-clean-room",
                    "client_name": "Ledger Example",
                    "sender": {"name": "Alex"},
                }
            )
            payload["offer"].update(
                {
                    "service": "Monthly bookkeeping for property managers.",
                    "audience": "Commercial property managers.",
                    "risk_reversal": "The first month is free if the books are not current.",
                    "risk_reversal_variants": [
                        {
                            "id": "first-month-free",
                            "text": "The first month is free if the books are not current.",
                        }
                    ],
                    "cta": "Want me to send a sample month-end report?",
                    "cta_variants": [
                        {
                            "id": "sample-report",
                            "text": "Want me to send a sample month-end report?",
                        }
                    ],
                    "approved_claims": ["The first-month offer is client-approved."],
                    "forbidden_claims": ["Do not invent tax savings or client results."],
                }
            )
            payload["personalization"].update(
                {
                    "objective": "Frame bookkeeping around a verified operating model.",
                    "focus_rules_file": focus_path.name,
                    "angles": [
                        {
                            "id": "bookkeeping-fit",
                            "signal_types": ["*"],
                            "templates": [
                                {
                                    "id": "direct-fit",
                                    "subject": "{{company_short_name}} bookkeeping",
                                    "pitch": "Could we help you reach more {{buyer_phrase}}?",
                                }
                            ],
                        }
                    ],
                }
            )
            campaign_path = root / "bookkeeping.json"
            campaign_path.write_text(json.dumps(payload), encoding="utf-8")

            input_path = root / "leads.csv"
            input_path.write_text(
                "Email,First name,Last name,Job title,Company name,Website,"
                "Company description,Company keywords\n"
                "sam@example.com,Sam,Lee,Founder,Property Ledger,property.example,"
                '"Commercial property management services for regional owners",'
                '"commercial property management"\n',
                encoding="utf-8",
            )
            output_path = root / "enriched.csv"
            campaign = load_campaign(campaign_path)
            run_enrichment(
                input_path=input_path,
                output_path=output_path,
                campaign=campaign,
                title_hooks=TitleHookTable.load(ROOT / "config" / "title-hooks.csv"),
                commercial_focuses=CommercialFocusTable.load(focus_path),
                options=RunOptions(cache_dir=root / "cache"),
                domain_enricher=FailedWebsiteEnricher(),
            )

            with output_path.open(encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["personalization_status"], "ready")
            self.assertEqual(row["outreach_status"], "review")
            self.assertEqual(row["personalization_focus_rule"], "property-bookkeeping")
            self.assertEqual(
                row["personalization_source"], "input:Company description"
            )
            self.assertIn(
                "property managers needing bookkeeping",
                row["personalized_pitch"],
            )
            self.assertIn("first month is free", row["personalized_email"])
            self.assertIn("sample month-end report", row["personalized_email"])
            self.assertNotIn("qualified call", row["personalized_email"])
            self.assertNotIn("M&A", row["personalized_email"])


if __name__ == "__main__":
    unittest.main()
