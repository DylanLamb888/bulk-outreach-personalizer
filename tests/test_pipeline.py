import csv
import json
import tempfile
import threading
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from bulk_enrich.config import CtaVariant, OfferLineVariant, load_campaign
from bulk_enrich.focus import CommercialFocusResult, CommercialFocusTable
from bulk_enrich.hooks import TitleHookTable
from bulk_enrich.models import CompanyFact, SiteSignal
from bulk_enrich.pipeline import (
    RunOptions,
    _apply_batch_quality,
    _balanced_ctas,
    _balanced_ctas_for_rendered_domains,
    _balanced_offer_lines,
    _balanced_offer_lines_for_rendered_domains,
    _build_copy,
    _candidate_facts,
    _clean_first_name,
    _immutable_snapshot,
    _select_company_candidate,
    _sequence_company_contacts,
    _short_company_name,
    run_enrichment,
)


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
            observation="your team advises private owners on sell-side M&A",
            evidence="We provide sell-side M&A advisory to private company owners",
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


class MappingDomainEnricher:
    focuses = {
        "off.example": "commercial washing and disinfection",
        "core.example": "sell-side M&A advisory for private company owners",
        "secondary.example": "flexible capital solutions",
    }

    def enrich(self, domain: str) -> SiteSignal:
        focus = self.focuses[domain]
        fact = CompanyFact(
            signal_type="service",
            focus=focus,
            observation=focus,
            evidence=focus,
            source_url=f"https://{domain}/services",
            confidence=0.90,
        )
        return SiteSignal(
            domain=domain,
            observation=focus,
            evidence=focus,
            source_url=fact.source_url,
            confidence=fact.confidence,
            status="ok",
            facts=(fact,),
        )


class PipelineTests(unittest.TestCase):
    def _template_copy_context(self) -> dict[str, object]:
        return {
            "first_name": "Ana",
            "buyer_phrase": "owners planning an exit",
            "company_focus": "sell-side advisory",
            "company_short_name": "Northstar",
        }

    def test_build_copy_rejects_empty_rendered_subject(self) -> None:
        campaign = load_campaign(ROOT / "campaigns" / "campaign-template.json")
        context = self._template_copy_context()
        context["company_short_name"] = ""
        with self.assertRaisesRegex(ValueError, "rendered subject is empty"):
            _build_copy(campaign, context, "northstar.example", "service", "")

    def test_build_copy_reports_missing_company_short_name(self) -> None:
        campaign = load_campaign(ROOT / "campaigns" / "campaign-template.json")
        context = self._template_copy_context()
        del context["company_short_name"]
        with self.assertRaisesRegex(ValueError, "company_short_name"):
            _build_copy(campaign, context, "northstar.example", "service", "")

    def test_configuration_snapshots_are_content_addressed_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "campaign.json"
            output = tmp_path / "audit.csv"
            source.write_text('{"version": 1}\n', encoding="utf-8")

            first_path, first_hash = _immutable_snapshot(source, output, "campaign")
            repeated_path, repeated_hash = _immutable_snapshot(
                source,
                output,
                "campaign",
            )
            self.assertEqual((first_path, first_hash), (repeated_path, repeated_hash))
            self.assertIn(first_hash, first_path.name)

            source.write_text('{"version": 2}\n', encoding="utf-8")
            second_path, second_hash = _immutable_snapshot(source, output, "campaign")
            self.assertNotEqual(first_path, second_path)
            self.assertNotEqual(first_hash, second_hash)
            self.assertEqual(first_path.read_text(encoding="utf-8"), '{"version": 1}\n')

    def test_qualification_outputs_deduplicate_and_snapshot_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "leads.csv"
            input_path.write_text(
                "Email,Email status,First name,Job title,Job seniority,Company name,Website\n"
                "shared@example.com,VERIFIED,Amy,Owner,Founder/Owner,Off Niche,off.example\n"
                "shared@example.com,VERIFIED,Ben,Founder,Founder/Owner,Core Adviser,core.example\n"
                "review@example.com,VERIFIED,Casey,Director of Business Development,Director,Secondary Capital,secondary.example\n",
                encoding="utf-8",
            )
            output = tmp_path / "audit.csv"
            ready_output = tmp_path / "ready.csv"
            review_output = tmp_path / "review.csv"
            campaign = load_campaign(
                ROOT / "campaigns" / "examples" / "scale-olympus.json"
            )
            manifest = run_enrichment(
                input_path=input_path,
                output_path=output,
                campaign=campaign,
                title_hooks=TitleHookTable.load(ROOT / "config" / "title-hooks.csv"),
                commercial_focuses=CommercialFocusTable.load(campaign.focus_rules_path),
                options=RunOptions(
                    cache_dir=tmp_path / "cache",
                    ready_output_path=ready_output,
                    review_output_path=review_output,
                ),
                domain_enricher=MappingDomainEnricher(),
            )

            with output.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            with ready_output.open(encoding="utf-8-sig", newline="") as handle:
                ready_rows = list(csv.DictReader(handle))
            with review_output.open(encoding="utf-8-sig", newline="") as handle:
                review_rows = list(csv.DictReader(handle))

            self.assertEqual([row["outreach_status"] for row in rows], ["excluded", "ready", "review"])
            self.assertEqual(rows[0]["email_fit_rule"], "duplicate-email")
            self.assertEqual(rows[0]["personalized_email"], "")
            self.assertEqual(rows[1]["company_fit_tier"], "core")
            self.assertTrue(rows[1]["personalized_email"])
            self.assertEqual(rows[2]["company_fit_tier"], "secondary")
            self.assertTrue(rows[2]["personalized_email"])
            self.assertEqual(len(ready_rows), 1)
            self.assertEqual(len(review_rows), 1)
            self.assertEqual(manifest["qualification"]["duplicate_email_rows_excluded"], 1)
            self.assertTrue(Path(manifest["campaign"]["snapshot_path"]).is_file())
            self.assertTrue(
                Path(manifest["settings"]["commercial_focus_snapshot_path"]).is_file()
            )

    def test_candidate_facts_drop_blocked_evidence(self) -> None:
        campaign = load_campaign(ROOT / "campaigns" / "campaign-template.json")
        campaign.data["personalization"]["blocked_evidence_phrases"] = [
            "works with the trade community"
        ]
        signal = SiteSignal(
            domain="example.com",
            observation="",
            evidence="",
            source_url="https://example.com/",
            confidence=0.9,
            status="ok",
            facts=(
                CompanyFact(
                    signal_type="service",
                    focus="the trade community programme",
                    observation="your team works with the trade community",
                    evidence="Through this program, CBP works with the trade community",
                    source_url="https://example.com/",
                    confidence=0.9,
                ),
                CompanyFact(
                    signal_type="service",
                    focus="customs brokerage for importers",
                    observation="your team provides customs brokerage for importers",
                    evidence="We provide customs brokerage for importers",
                    source_url="https://example.com/about",
                    confidence=0.85,
                ),
            ),
        )
        facts = _candidate_facts(signal, {}, campaign)
        self.assertEqual(len(facts), 1)
        self.assertIn("customs brokerage", facts[0].evidence)

    def test_title_hook_gap_fails_only_the_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            hooks_path = tmp_path / "hooks.csv"
            hooks_path.write_text(
                "priority,title_pattern,persona,hook\n"
                '10,chief financial officer,finance,"From finance, timing matters"\n',
                encoding="utf-8",
            )
            input_path = tmp_path / "leads.csv"
            input_path.write_text(
                "Email,Email status,First name,Job title,Job seniority,Company name,Website\n"
                "ben@core.example,VERIFIED,Ben,Founder,Founder/Owner,Core Adviser,core.example\n",
                encoding="utf-8",
            )
            output = tmp_path / "audit.csv"
            campaign = load_campaign(
                ROOT / "campaigns" / "examples" / "scale-olympus.json"
            )
            manifest = run_enrichment(
                input_path=input_path,
                output_path=output,
                campaign=campaign,
                title_hooks=TitleHookTable.load(hooks_path),
                commercial_focuses=CommercialFocusTable.load(campaign.focus_rules_path),
                options=RunOptions(cache_dir=tmp_path / "cache"),
                domain_enricher=MappingDomainEnricher(),
            )
            with output.open(encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["personalization_status"], "error")
            self.assertIn("no fallback rule", row["personalization_error"])
            self.assertEqual(row["outreach_status"], "error")
            self.assertEqual(manifest["output"]["status_counts"], {"error": 1})

    def test_manifest_reports_focus_gaps_for_unmatched_domains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "leads.csv"
            input_path.write_text(
                "Email,Email status,First name,Job title,Job seniority,Company name,Website\n"
                "ben@core.example,VERIFIED,Ben,Founder,Founder/Owner,Core Adviser,core.example\n",
                encoding="utf-8",
            )
            output = tmp_path / "audit.csv"
            campaign = load_campaign(
                ROOT / "campaigns" / "examples" / "scale-olympus.json"
            )
            manifest = run_enrichment(
                input_path=input_path,
                output_path=output,
                campaign=campaign,
                title_hooks=TitleHookTable.load(ROOT / "config" / "title-hooks.csv"),
                commercial_focuses=CommercialFocusTable.load(campaign.focus_rules_path),
                options=RunOptions(cache_dir=tmp_path / "cache"),
                domain_enricher=FailedDomainEnricher(),
            )
            gaps = manifest["focus_gaps"]
            self.assertEqual(gaps["domains"], 1)
            sample = gaps["samples"][0]
            self.assertEqual(sample["domain"], "core.example")
            self.assertEqual(sample["rule"], "no-company-evidence")
            self.assertIn("tier", sample)
            self.assertIn("source_focus", sample)
            self.assertIn("evidence", sample)

    def test_company_selection_prefers_first_party_and_corroborated_csv(self) -> None:
        def candidate(
            *,
            priority: int,
            index: int,
            source_url: str,
            confidence: float,
            rule_id: str,
            fit_tier: str = "core",
        ) -> tuple[int, int, CompanyFact, CommercialFocusResult]:
            fact = CompanyFact(
                signal_type="service",
                focus=rule_id,
                observation=rule_id,
                evidence=rule_id,
                source_url=source_url,
                confidence=confidence,
            )
            focus = CommercialFocusResult(
                source_focus=rule_id,
                focus=rule_id,
                buyer_phrase="companies considering a transaction",
                rule_id=rule_id,
                priority=priority,
                fit_tier=fit_tier,
            )
            return priority, index, fact, focus

        campaign = load_campaign(
            ROOT / "campaigns" / "examples" / "scale-olympus.json"
        )
        website = candidate(
            priority=48,
            index=0,
            source_url="https://example.com/",
            confidence=0.88,
            rule_id="ma-advisory",
        )
        csv_description = candidate(
            priority=30,
            index=1,
            source_url="input:Company description",
            confidence=0.82,
            rule_id="ma-sell-side",
        )
        csv_keywords = candidate(
            priority=30,
            index=2,
            source_url="input:Company keywords",
            confidence=0.72,
            rule_id="ma-sell-side",
        )

        selected, fields = _select_company_candidate(
            [website, csv_description, csv_keywords],
            campaign,
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected[2].source_url, "https://example.com/")
        self.assertEqual(fields, ())

        stronger_website = candidate(
            priority=48,
            index=3,
            source_url="https://example.com/services",
            confidence=0.94,
            rule_id="ma-advisory",
        )
        selected, fields = _select_company_candidate(
            [website, stronger_website, csv_description, csv_keywords],
            campaign,
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected[2].source_url, "https://example.com/services")
        self.assertEqual(fields, ())

        unmatched_website = candidate(
            priority=1000,
            index=0,
            source_url="https://unclear.example/",
            confidence=0.88,
            rule_id="generic-compression",
            fit_tier="exclude",
        )
        selected, fields = _select_company_candidate(
            [unmatched_website, csv_description, csv_keywords],
            campaign,
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected[3].rule_id, "generic-compression")
        self.assertEqual(fields, ())

        selected, fields = _select_company_candidate(
            [csv_description, csv_keywords],
            campaign,
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected[3].rule_id, "ma-sell-side")
        self.assertEqual(fields, ("Company description", "Company keywords"))

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

    def test_pitch_does_not_repeat_cta_opening(self) -> None:
        campaign = load_campaign(
            ROOT / "campaigns" / "examples" / "scale-olympus.json"
        )
        context = {
            "company_short_name": "Example",
            "company_focus": "financial guidance",
            "buyer_phrase": "business owners seeking financial guidance",
            "first_name": "Ana",
        }
        open_cta = next(
            item
            for item in campaign.cta_variants
            if item.variant_id == "show-first-25"
        )
        for index in range(20):
            rendered = _build_copy(
                campaign,
                context,
                f"company-{index}.example",
                "service",
                "Unrelated evidence",
                cta_variant=open_cta,
            )
            pitch_opening = " ".join(rendered.pitch.casefold().split()[:2])
            cta_opening = " ".join(rendered.cta.casefold().split()[:2])
            self.assertNotEqual(pitch_opening, cta_opening)

    def test_batch_cta_assignment_is_deterministic_and_balanced(self) -> None:
        campaign = load_campaign(
            ROOT / "campaigns" / "examples" / "scale-olympus.json"
        )
        domains = [f"company-{index}.example" for index in range(50)]
        first = _balanced_ctas(campaign.cta_variants, domains)
        second = _balanced_ctas(campaign.cta_variants, domains)
        self.assertEqual(first, second)
        counts = Counter(item.variant_id for item in first.values())
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)

    def test_batch_offer_line_assignment_is_deterministic_and_balanced(self) -> None:
        variants = (
            OfferLineVariant("one", "First approved line."),
            OfferLineVariant("two", "Second approved line."),
            OfferLineVariant("three", "Third approved line."),
        )
        domains = [f"company-{index}.example" for index in range(10)]
        first = _balanced_offer_lines(variants, domains)
        second = _balanced_offer_lines(variants, list(reversed(domains)))
        self.assertEqual(first, second)
        counts = Counter(item.variant_id for item in first.values())
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)

    def test_domain_with_two_focus_rules_is_assigned_once_from_first_rule_pool(self) -> None:
        variants = (
            CtaVariant(variant_id="x-only", text="Want the 3 X ideas?", focus_rules=("rule-x",)),
            CtaVariant(variant_id="y-only", text="Want the 3 Y ideas?", focus_rules=("rule-y",)),
            CtaVariant(variant_id="default", text="Want the outline?", focus_rules=("*",)),
        )
        assigned = _balanced_ctas_for_rendered_domains(
            variants,
            [("a.example", "rule-x"), ("a.example", "rule-y"), ("b.example", "rule-y")],
        )
        self.assertEqual(assigned["a.example"].variant_id, "x-only")
        self.assertEqual(assigned["b.example"].variant_id, "y-only")

    def test_rendered_domain_variants_balance_within_focus_pools(self) -> None:
        ctas = (
            CtaVariant("general-one", "General one."),
            CtaVariant("general-two", "General two."),
            CtaVariant("general-three", "General three."),
            CtaVariant("special-one", "Special one.", ("special",)),
            CtaVariant("special-two", "Special two.", ("special",)),
        )
        offer_lines = (
            OfferLineVariant("offer-one", "Offer one."),
            OfferLineVariant("offer-two", "Offer two."),
            OfferLineVariant("special-offer", "Special offer.", ("special",)),
        )
        domain_focus_rules = [
            *((f"general-{index}.example", "general") for index in range(11)),
            *((f"special-{index}.example", "special") for index in range(3)),
        ]

        cta_assignment = _balanced_ctas_for_rendered_domains(
            ctas, domain_focus_rules
        )
        repeated_cta_assignment = _balanced_ctas_for_rendered_domains(
            ctas, list(reversed(domain_focus_rules))
        )
        self.assertEqual(cta_assignment, repeated_cta_assignment)
        general_cta_counts = Counter(
            cta_assignment[domain].variant_id
            for domain, focus_rule in domain_focus_rules
            if focus_rule == "general"
        )
        special_cta_counts = Counter(
            cta_assignment[domain].variant_id
            for domain, focus_rule in domain_focus_rules
            if focus_rule == "special"
        )
        self.assertLessEqual(
            max(general_cta_counts.values()) - min(general_cta_counts.values()), 1
        )
        self.assertLessEqual(
            max(special_cta_counts.values()) - min(special_cta_counts.values()), 1
        )

        offer_assignment = _balanced_offer_lines_for_rendered_domains(
            offer_lines, domain_focus_rules
        )
        general_offer_counts = Counter(
            offer_assignment[domain].variant_id
            for domain, focus_rule in domain_focus_rules
            if focus_rule == "general"
        )
        self.assertLessEqual(
            max(general_offer_counts.values()) - min(general_offer_counts.values()), 1
        )
        self.assertTrue(
            all(
                offer_assignment[domain].variant_id == "special-offer"
                for domain, focus_rule in domain_focus_rules
                if focus_rule == "special"
            )
        )

    def test_exact_pitch_gate_allows_the_balanced_template_floor(self) -> None:
        campaign = load_campaign(
            ROOT / "campaigns" / "examples" / "scale-olympus.json"
        )
        rows = []
        domains = []
        for index in range(28):
            domains.append(f"company-{index}.example")
            rows.append(
                {
                    "personalization_status": "ready",
                    "personalized_pitch": f"Approved pitch {index % 8}",
                    "personalization_angle": "buyer-conversation-angle",
                    "personalization_buyer_phrase": "qualified buyers",
                    "personalization_cta": f"CTA {index}",
                    "personalization_offer_line": (
                        f"Approved offer line {index % 3}"
                    ),
                    "personalization_quality_flags": "",
                }
            )

        report = _apply_batch_quality(rows, domains, campaign)

        self.assertFalse(
            any(
                warning["type"] == "exact_pitch_share"
                for warning in report["warnings"]
            )
        )

    def test_batch_quality_demotes_only_the_overflow(self) -> None:
        def build_batch() -> tuple[list[dict[str, str]], list[str]]:
            rows: list[dict[str, str]] = []
            domains: list[str] = []
            for index in range(10):
                pitch = (
                    "Could a campaign start more conversations here?"
                    if index < 6
                    else f"Different opening number {index} for this domain?"
                )
                rows.append(
                    {
                        "personalized_pitch": pitch,
                        "personalization_status": "ready",
                        "personalization_quality_flags": "",
                    }
                )
                domains.append(f"domain{index}.example")
            return rows, domains

        campaign = load_campaign(ROOT / "campaigns" / "campaign-template.json")
        campaign.data["quality"]["min_rows"] = 10

        rows, domains = build_batch()
        report = _apply_batch_quality(rows, domains, campaign)

        flagged = [row for row in rows if row["personalization_quality_flags"]]
        demoted = [row for row in rows if row["personalization_status"] == "review"]
        # 6 of 10 domains share the opening; the 0.45 cap allows 4, so only the
        # 2 overflow rows are flagged and demoted.
        self.assertEqual(len(flagged), 2)
        self.assertEqual(len(demoted), 2)
        self.assertEqual(report["flagged_rows"], 2)
        opening_warnings = [
            warning
            for warning in report["warnings"]
            if warning["type"] == "opening_share"
        ]
        self.assertEqual(len(opening_warnings), 1)
        self.assertEqual(opening_warnings[0]["count"], 6)
        self.assertEqual(opening_warnings[0]["flagged"], 2)

        repeat_rows, repeat_domains = build_batch()
        _apply_batch_quality(repeat_rows, repeat_domains, campaign)
        self.assertEqual(
            [row["personalization_status"] for row in rows],
            [row["personalization_status"] for row in repeat_rows],
        )

    def test_offer_line_selection_prefers_an_exact_focus_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = json.loads(
                (ROOT / "campaigns" / "examples" / "scale-olympus.json").read_text()
            )
            payload["offer"]["risk_reversal_variants"] = [
                {
                    "id": "owner-fallback",
                    "text": "You only pay for qualified owner conversations.",
                },
                {
                    "id": "corporate-seller",
                    "text": "You only pay for qualified conversations with potential sellers.",
                    "focus_rules": ["corporate-carve-outs"],
                },
            ]
            campaign_path = Path(tmp) / "campaign.json"
            campaign_path.write_text(json.dumps(payload), encoding="utf-8")
            campaign = load_campaign(campaign_path)
            rendered = _build_copy(
                campaign,
                {
                    "company_short_name": "Example",
                    "company_focus": "corporate carve-outs",
                    "buyer_phrase": "companies looking to sell a division",
                    "first_name": "Ana",
                },
                "example.com",
                "service",
                "We acquire non-core corporate divisions",
                focus_rule="corporate-carve-outs",
            )

            self.assertEqual(rendered.offer_variant_id, "corporate-seller")
            self.assertIn("potential sellers", rendered.body)
            self.assertNotIn("owner conversations", rendered.body)

    def test_cta_selection_prefers_an_exact_focus_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = json.loads(
                (ROOT / "campaigns" / "examples" / "scale-olympus.json").read_text()
            )
            payload["offer"]["cta_variants"] = [
                {
                    "id": "owner-segments",
                    "text": "Want me to send the 3 owner segments I'd test?",
                },
                {
                    "id": "carveout-criteria",
                    "text": "Open to seeing the 5 carve-out criteria I'd use?",
                    "focus_rules": ["corporate-carve-outs"],
                },
            ]
            campaign_path = Path(tmp) / "campaign.json"
            campaign_path.write_text(json.dumps(payload), encoding="utf-8")
            campaign = load_campaign(campaign_path)
            rendered = _build_copy(
                campaign,
                {
                    "company_short_name": "Example",
                    "company_focus": "corporate carve-outs",
                    "buyer_phrase": "companies looking to sell a division",
                    "first_name": "Ana",
                },
                "example.com",
                "service",
                "We acquire non-core corporate divisions",
                focus_rule="corporate-carve-outs",
            )

            self.assertEqual(rendered.cta_variant_id, "carveout-criteria")
            self.assertIn("5 carve-out criteria", rendered.body)
            self.assertNotIn("owner segments", rendered.body)

    def test_cleans_recipient_and_company_names_for_rendering(self) -> None:
        self.assertEqual(_clean_first_name("Carlos M."), "Carlos")
        self.assertEqual(_clean_first_name("Steven Michael"), "Steven")
        self.assertEqual(_clean_first_name("Anne-Marie"), "Anne-Marie")
        examples = {
            "Portage M&A Advisory (Mergers & Acquisitions)": "Portage M&A Advisory",
            "CapEQ™ | B Corp": "CapEQ™",
            "Antares International Partners, Inc": "Antares International Partners",
            "Berkery, Noyes &": "Berkery, Noyes",
            "Canadian Society of Customs Brokers (CSCB)": "CSCB",
            "SPIRIT CHB - Trusted Customs Brokerage": "SPIRIT CHB",
        }
        for raw, expected in examples.items():
            with self.subTest(raw=raw):
                self.assertEqual(_short_company_name(raw), expected)

    def test_company_contact_sequencing_prefers_campaign_title_priority(self) -> None:
        campaign = load_campaign(
            ROOT / "campaigns" / "examples" / "scale-olympus.json"
        )

        def eligible_row(title: str, seniority: str) -> dict[str, str]:
            return {
                "Job title": title,
                "Job seniority": seniority,
                "personalized_email": "Hi there",
                "personalization_status": "ready",
                "company_fit_status": "qualified",
                "company_fit_rule": "core",
                "company_fit_reason": "core fit",
                "contact_fit_status": "qualified",
                "contact_fit_rule": "approved-title",
                "contact_fit_reason": "approved contact",
                "email_fit_status": "qualified",
                "email_fit_rule": "verified",
                "email_fit_reason": "verified email",
                "outreach_status": "ready",
                "outreach_reason": "all gates passed",
            }

        rows = [
            eligible_row("Managing Partner", "Partner"),
            eligible_row("Managing Director", "C-Suite"),
        ]
        report = _sequence_company_contacts(
            rows,
            ["same-company.example", "same-company.example"],
            campaign,
            seniority_header="Job seniority",
            title_header="Job title",
        )

        self.assertEqual(rows[0]["company_contact_status"], "primary")
        self.assertEqual(rows[0]["outreach_status"], "ready")
        self.assertEqual(rows[0]["company_contact_rank"], "1")
        self.assertEqual(rows[1]["company_contact_status"], "later-wave")
        self.assertEqual(rows[1]["outreach_status"], "review")
        self.assertEqual(rows[1]["company_contact_rank"], "2")
        self.assertEqual(report["multi_contact_companies"], 1)
        self.assertEqual(report["later_wave_rows"], 1)

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
            self.assertIn("private-company owners considering a sale", rows[0]["personalized_pitch"])
            self.assertTrue(rows[0]["personalization_offer_variant"])
            self.assertTrue(rows[0]["personalization_offer_line"])
            self.assertIn(
                rows[0]["personalization_offer_line"], rows[0]["personalized_email"]
            )
            self.assertIn("Dylan", rows[0]["personalized_email"])
            self.assertEqual(rows[0]["personalization_signal_type"], "service")
            self.assertTrue(rows[0]["personalization_angle"])
            self.assertTrue(rows[0]["personalization_template"])
            self.assertEqual(rows[0]["personalization_source_focus"], "sell-side M&A advisory to private company owners")
            self.assertEqual(rows[0]["personalization_focus"], "private-company sales")
            self.assertEqual(
                rows[0]["personalization_buyer_phrase"],
                "private-company owners considering a sale",
            )
            self.assertEqual(rows[0]["personalization_focus_rule"], "private-company-sale")
            self.assertIn(
                rows[0]["personalization_cta_variant"],
                {item.variant_id for item in load_campaign(
                    ROOT / "campaigns" / "examples" / "scale-olympus.json"
                ).cta_variants},
            )
            self.assertIn(rows[0]["personalization_cta"], rows[0]["personalized_email"])
            self.assertEqual(rows[0]["personalization_quality_flags"], "")
            self.assertEqual(rows[2]["personalization_status"], "blank")
            self.assertEqual(rows[2]["outreach_status"], "excluded")
            self.assertIn("email is missing", rows[2]["outreach_reason"])

            with ready_output.open("r", encoding="utf-8-sig", newline="") as handle:
                ready_rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["company_contact_status"], "primary")
            self.assertEqual(rows[0]["company_contact_rank"], "1")
            self.assertEqual(rows[1]["company_contact_status"], "later-wave")
            self.assertEqual(rows[1]["company_contact_rank"], "2")
            self.assertEqual(rows[1]["outreach_status"], "review")
            self.assertIn("hold for wave 2", rows[1]["outreach_reason"])
            self.assertEqual(len(ready_rows), 1)
            self.assertTrue(all(row["outreach_status"] == "ready" for row in ready_rows))
            self.assertTrue(all(row["personalization_cta"] for row in ready_rows))

            saved_manifest = json.loads(manifest_path.read_text())
            self.assertEqual(
                saved_manifest["output"]["status_counts"],
                {"excluded": 1, "ready": 1, "review": 1},
            )
            self.assertEqual(saved_manifest["output"]["ready_upload"]["row_count"], 1)
            self.assertEqual(
                saved_manifest["qualification"]["company_contact_sequencing"],
                {
                    "eligible_company_groups": 1,
                    "later_wave_rows": 1,
                    "multi_contact_companies": 1,
                },
            )
            self.assertFalse(saved_manifest["quality"]["evaluated"])
            self.assertEqual(saved_manifest["quality"]["unique_rendered_domains"], 1)
            self.assertEqual(
                saved_manifest["script_test"],
                {
                    "assignment_field": "personalization_offer_variant",
                    "cohort_counts": {rows[0]["personalization_offer_variant"]: 1},
                    "unique_rendered_companies": 1,
                },
            )

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
                "Email,First name,Job title,Company name,Website,Company description,Company keywords\n"
                "ana@example.com,Ana,Founder,Northstar Capital,northstar.example,"
                '"Northstar provides sell-side M&A advisory to private companies.",'
                '"private company sell-side M&A advisory"\n',
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
            self.assertEqual(row["outreach_status"], "review")
            self.assertEqual(row["personalization_source"], "input:Company description")
            self.assertEqual(row["personalization_confidence"], "0.82")
            self.assertEqual(row["personalization_focus_rule"], "private-company-sale")
            self.assertEqual(
                row["personalization_buyer_phrase"],
                "private-company owners considering a sale",
            )
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
            payload["offer"]["risk_reversal_variants"] = [
                {
                    "id": "single-offer-line",
                    "text": "You only pay when we deliver a qualified conversation.",
                }
            ]
            payload["quality"]["max_offer_line_share"] = 0.49
            for angle in payload["personalization"]["angles"]:
                angle["templates"] = angle["templates"][:1]
            campaign_path = tmp_path / "campaign.json"
            campaign_path.write_text(json.dumps(payload))
            output = tmp_path / "enriched.csv"
            input_path = tmp_path / "batch.csv"
            input_path.write_text(
                (ROOT / "tests" / "fixtures" / "leads.csv")
                .read_text(encoding="utf-8")
                .replace(
                    ",Morgan,Jones,Owner,Harbour Advisory,harbour.example",
                    "morgan@example.com,Morgan,Jones,Owner,Harbour Advisory,harbour.example",
                ),
                encoding="utf-8",
            )

            manifest = run_enrichment(
                input_path=input_path,
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
            self.assertTrue(
                any(
                    warning["type"] == "offer_line_share"
                    for warning in manifest["quality"]["warnings"]
                )
            )
            self.assertEqual(manifest["quality"]["flagged_rows"], 3)
            self.assertTrue(all(row["personalization_status"] == "review" for row in rows))
            self.assertTrue(all(row["personalization_quality_flags"] for row in rows))


if __name__ == "__main__":
    unittest.main()
