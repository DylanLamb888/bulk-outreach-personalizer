import csv
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from bulk_enrich.config import load_campaign
from bulk_enrich.focus import CommercialFocusTable
from bulk_enrich.hooks import TitleHookTable
from bulk_enrich.models import CompanyFact, SiteSignal
from bulk_enrich.pipeline import RunOptions, _build_copy, run_enrichment


ROOT = Path(__file__).resolve().parents[1]


class FakeDomainEnricher:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def enrich(self, domain: str) -> SiteSignal:
        with self.lock:
            self.calls.append(domain)
        confidence = 0.90 if domain == "northstar.example" else 0.68
        return SiteSignal(
            domain=domain,
            observation="your team helps owners make clearer financial decisions",
            evidence="We help owners make clearer financial decisions",
            source_url=f"https://{domain}/",
            confidence=confidence,
            status="ok",
            pages_fetched=1,
        )


class SecondaryFactEnricher:
    def enrich(self, domain: str) -> SiteSignal:
        facts = (
            CompanyFact(
                signal_type="specialism",
                focus="so we understand the territory as well as anyone",
                observation="your site highlights a slogan",
                evidence="So we understand the territory as well as anyone",
                source_url=f"https://{domain}/",
                confidence=0.92,
            ),
            CompanyFact(
                signal_type="service",
                focus="financing for the lower middle market",
                observation="your team provides financing for the lower middle market",
                evidence="What we do is simple - financing for the lower middle market",
                source_url=f"https://{domain}/services",
                confidence=0.84,
            ),
        )
        return SiteSignal(
            domain=domain,
            observation=facts[0].observation,
            evidence=facts[0].evidence,
            source_url=facts[0].source_url,
            confidence=facts[0].confidence,
            status="ok",
            focus=facts[0].focus,
            signal_type=facts[0].signal_type,
            facts=facts,
            pages_fetched=1,
        )


class FailedDomainEnricher:
    def enrich(self, domain: str) -> SiteSignal:
        return SiteSignal(
            domain=domain,
            observation="",
            evidence="",
            source_url=f"https://{domain}/",
            confidence=0.0,
            status="fetch_error",
            error="homepage unavailable",
        )


class PipelineTests(unittest.TestCase):
    def test_wires_optional_firecrawl_fallback_and_manifest_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake = FakeDomainEnricher()
            with patch("bulk_enrich.pipeline.SiteEnricher") as site_enricher:
                site_enricher.return_value.enrich.side_effect = fake.enrich
                manifest = run_enrichment(
                    input_path=ROOT / "tests" / "fixtures" / "leads.csv",
                    output_path=tmp_path / "enriched.csv",
                    campaign=load_campaign(
                        ROOT / "campaigns" / "examples" / "scale-olympus.json"
                    ),
                    title_hooks=TitleHookTable.load(
                        ROOT / "config" / "title-hooks.csv"
                    ),
                    commercial_focuses=CommercialFocusTable.load(
                        ROOT / "campaigns" / "examples" / "scale-olympus-focus.csv"
                    ),
                    options=RunOptions(
                        cache_dir=tmp_path / "cache",
                        firecrawl_fallback=True,
                        firecrawl_api_url="http://localhost:3002",
                    ),
                )

            fallback = site_enricher.call_args.kwargs["fallback_fetcher"]
            self.assertIsNotNone(fallback)
            self.assertTrue(manifest["firecrawl"]["enabled"])
            self.assertEqual(manifest["firecrawl"]["requests"], 0)
            self.assertEqual(manifest["firecrawl"]["max_concurrency"], 4)

    def test_cta_selection_is_stable_and_varied_by_domain(self) -> None:
        campaign = load_campaign(
            ROOT / "campaigns" / "examples" / "scale-olympus.json"
        )
        context = {
            "company_short_name": "Example",
            "company_focus": "financial guidance",
            "buyer_phrase": "business owners seeking financial guidance",
            "first_name": "Ana",
        }
        domains = (
            "lindebladpiano.com",
            "auroramills.com",
            "vanwykconfections.com",
            "balancepointcapital.com",
            "coadydiemar.com",
            "synteraadvisors.com",
        )
        rendered = [
            _build_copy(campaign, context, domain, "service", "Unrelated evidence")
            for domain in domains
        ]
        self.assertEqual(len({item.cta_variant_id for item in rendered}), 4)
        repeated = _build_copy(
            campaign,
            context,
            domains[0],
            "service",
            "Unrelated evidence",
        )
        self.assertEqual(rendered[0].cta_variant_id, repeated.cta_variant_id)
        self.assertEqual(rendered[0].cta, repeated.cta)

    def test_copy_gate_rejects_verbatim_source_phrases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = json.loads(
                (ROOT / "campaigns" / "examples" / "scale-olympus.json").read_text()
            )
            payload["quality"]["max_source_phrase_words"] = 2
            campaign_path = Path(tmp) / "campaign.json"
            campaign_path.write_text(json.dumps(payload), encoding="utf-8")
            campaign = load_campaign(campaign_path)
            context = {
                "company_short_name": "Example",
                "company_focus": "financial decisions",
                "buyer_phrase": "owners making clearer financial decisions",
                "first_name": "Ana",
            }
            with self.assertRaisesRegex(ValueError, "consecutive words"):
                _build_copy(
                    campaign,
                    context,
                    "example.com",
                    "service",
                    "We help owners making clearer financial decisions every day",
                )

    def test_writes_import_ready_csv_and_manifest_once_per_domain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output = tmp_path / "enriched.csv"
            ready_output = tmp_path / "smartlead-ready.csv"
            fake = FakeDomainEnricher()
            manifest = run_enrichment(
                input_path=ROOT / "tests" / "fixtures" / "leads.csv",
                output_path=output,
                campaign=load_campaign(
                    ROOT / "campaigns" / "examples" / "scale-olympus.json"
                ),
                title_hooks=TitleHookTable.load(ROOT / "config" / "title-hooks.csv"),
                commercial_focuses=CommercialFocusTable.load(
                    load_campaign(
                        ROOT / "campaigns" / "examples" / "scale-olympus.json"
                    ).focus_rules_path
                ),
                options=RunOptions(
                    cache_dir=tmp_path / "cache",
                    concurrency=4,
                    ready_output_path=ready_output,
                ),
                domain_enricher=fake,
            )

            self.assertEqual(sorted(fake.calls), ["harbour.example", "northstar.example"])
            self.assertEqual(manifest["domains"]["duplicate_rows_avoided"], 1)
            self.assertTrue(output.is_file())
            manifest_path = output.with_suffix(".csv.manifest.json")
            self.assertTrue(manifest_path.is_file())
            self.assertTrue(ready_output.is_file())

            with output.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["Email"], "ana@example.com")
            self.assertEqual(rows[0]["personalization_status"], "ready")
            self.assertIn("business owners seeking financial guidance", rows[0]["personalized_pitch"])
            self.assertIn("only charge per qualified call", rows[0]["personalized_email"])
            self.assertIn("Dylan", rows[0]["personalized_email"])
            self.assertEqual(rows[0]["personalization_signal_type"], "audience")
            self.assertTrue(rows[0]["personalization_angle"])
            self.assertTrue(rows[0]["personalization_template"])
            self.assertEqual(rows[0]["personalization_source_focus"], "helping owners make clearer financial decisions")
            self.assertEqual(rows[0]["personalization_focus"], "financial guidance")
            self.assertEqual(
                rows[0]["personalization_buyer_phrase"],
                "business owners seeking financial guidance",
            )
            self.assertEqual(rows[0]["personalization_focus_rule"], "owner-financial-guidance")
            self.assertEqual(
                rows[0]["personalization_cta_variant"],
                "show-opening-line",
            )
            self.assertEqual(
                rows[0]["personalization_cta"],
                "Want to see the opening line I'd test?",
            )
            self.assertIn(rows[0]["personalization_cta"], rows[0]["personalized_email"])
            self.assertEqual(rows[0]["personalization_quality_flags"], "")
            self.assertEqual(rows[2]["personalization_status"], "review")
            self.assertIn("missing email", rows[2]["personalization_error"])

            with ready_output.open("r", encoding="utf-8-sig", newline="") as handle:
                ready_rows = list(csv.DictReader(handle))
            self.assertEqual(len(ready_rows), 2)
            self.assertTrue(all(row["personalization_status"] == "ready" for row in ready_rows))
            self.assertTrue(all(row["personalization_cta"] for row in ready_rows))

            saved_manifest = json.loads(manifest_path.read_text())
            self.assertEqual(saved_manifest["output"]["status_counts"], {"ready": 2, "review": 1})
            self.assertEqual(saved_manifest["output"]["ready_upload"]["row_count"], 2)
            self.assertFalse(saved_manifest["quality"]["evaluated"])
            self.assertEqual(saved_manifest["quality"]["unique_rendered_domains"], 2)

    def test_uses_a_safe_secondary_fact_when_primary_is_website_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "lead.csv"
            input_path.write_text(
                "Email,First name,Job title,Company name,Website\n"
                "ana@example.com,Ana,Founder,Northstar Capital,northstar.example\n",
                encoding="utf-8",
            )
            output = tmp_path / "enriched.csv"
            run_enrichment(
                input_path=input_path,
                output_path=output,
                campaign=load_campaign(
                    ROOT / "campaigns" / "examples" / "scale-olympus.json"
                ),
                title_hooks=TitleHookTable.load(ROOT / "config" / "title-hooks.csv"),
                commercial_focuses=CommercialFocusTable.load(
                    load_campaign(
                        ROOT / "campaigns" / "examples" / "scale-olympus.json"
                    ).focus_rules_path
                ),
                options=RunOptions(cache_dir=tmp_path / "cache"),
                domain_enricher=SecondaryFactEnricher(),
            )
            with output.open("r", encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["personalization_status"], "ready")
            self.assertEqual(
                row["personalization_source_focus"],
                "financing for the lower middle market",
            )
            self.assertEqual(row["personalization_focus_rule"], "middle-market-financing")
            self.assertIn("companies seeking flexible financing", row["personalized_pitch"])

    def test_uses_configured_input_fact_when_website_fetch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "lead.csv"
            input_path.write_text(
                "Email,First name,Job title,Company name,Website,Company description\n"
                "ana@example.com,Ana,Founder,Northstar Capital,northstar.example,"
                '"Northstar provides sell-side M&A advisory to private companies."\n',
                encoding="utf-8",
            )
            output = tmp_path / "enriched.csv"
            run_enrichment(
                input_path=input_path,
                output_path=output,
                campaign=load_campaign(
                    ROOT / "campaigns" / "examples" / "scale-olympus.json"
                ),
                title_hooks=TitleHookTable.load(ROOT / "config" / "title-hooks.csv"),
                commercial_focuses=CommercialFocusTable.load(
                    ROOT / "campaigns" / "examples" / "scale-olympus-focus.csv"
                ),
                options=RunOptions(cache_dir=tmp_path / "cache"),
                domain_enricher=FailedDomainEnricher(),
            )
            with output.open("r", encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["personalization_status"], "ready")
            self.assertEqual(row["personalization_source"], "input:Company description")
            self.assertEqual(row["personalization_confidence"], "0.82")
            self.assertEqual(row["personalization_focus_rule"], "ma-sell-side")
            self.assertEqual(row["personalization_error"], "")

    def test_batch_repetition_gate_counts_unique_domains_and_marks_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload = json.loads(
                (ROOT / "campaigns" / "examples" / "scale-olympus.json").read_text()
            )
            payload["quality"].update(
                {
                    "min_rows": 2,
                    "max_opening_share": 0.49,
                    "max_exact_pitch_share": 0.49,
                }
            )
            for angle in payload["personalization"]["angles"]:
                angle["templates"] = angle["templates"][:1]
            campaign_path = tmp_path / "campaign.json"
            campaign_path.write_text(json.dumps(payload))
            output = tmp_path / "enriched.csv"

            manifest = run_enrichment(
                input_path=ROOT / "tests" / "fixtures" / "leads.csv",
                output_path=output,
                campaign=load_campaign(campaign_path),
                title_hooks=TitleHookTable.load(ROOT / "config" / "title-hooks.csv"),
                commercial_focuses=CommercialFocusTable.load(
                    ROOT / "campaigns" / "examples" / "scale-olympus-focus.csv"
                ),
                options=RunOptions(cache_dir=tmp_path / "cache", concurrency=2),
                domain_enricher=FakeDomainEnricher(),
            )

            with output.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(manifest["quality"]["unique_rendered_domains"], 2)
            self.assertTrue(manifest["quality"]["evaluated"])
            self.assertEqual(manifest["quality"]["flagged_rows"], 3)
            self.assertTrue(all(row["personalization_status"] == "review" for row in rows))
            self.assertTrue(all(row["personalization_quality_flags"] for row in rows))


if __name__ == "__main__":
    unittest.main()
