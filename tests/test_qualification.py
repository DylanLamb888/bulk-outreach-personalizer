import unittest
from pathlib import Path

from bulk_enrich.config import load_campaign
from bulk_enrich.focus import CommercialFocusResult
from bulk_enrich.models import CompanyFact
from bulk_enrich.qualification import (
    compose_outreach_status,
    qualify_company,
    qualify_contact,
    qualify_email,
    valid_email_syntax,
)


ROOT = Path(__file__).resolve().parents[1]


class QualificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.campaign = load_campaign(
            ROOT / "campaigns" / "examples" / "scale-olympus.json"
        )

    def test_company_fit_uses_tier_and_first_party_evidence(self) -> None:
        fact = CompanyFact(
            signal_type="service",
            focus="sell-side M&A advisory",
            observation="sell-side M&A advisory",
            evidence="Sell-side M&A advisory for private companies",
            source_url="https://adviser.example/services",
            confidence=0.90,
        )
        core = qualify_company(
            fact,
            CommercialFocusResult(
                source_focus=fact.focus,
                focus="sell-side M&A",
                buyer_phrase="owners considering a business sale",
                rule_id="ma-sell-side",
                priority=10,
                fit_tier="core",
            ),
            min_confidence=0.72,
        )
        secondary = qualify_company(
            fact,
            CommercialFocusResult(
                source_focus=fact.focus,
                focus="business valuation",
                buyer_phrase="owners needing a valuation",
                rule_id="business-valuation",
                priority=20,
                fit_tier="secondary",
            ),
            min_confidence=0.72,
        )
        self.assertEqual(core.status, "qualified")
        self.assertEqual(secondary.status, "review")

    def test_csv_company_evidence_requires_two_agreeing_fields(self) -> None:
        fact = CompanyFact(
            signal_type="service",
            focus="cost segregation studies",
            observation="cost segregation studies",
            evidence="Cost segregation studies for real estate owners",
            source_url="input:Company description",
            confidence=0.82,
        )
        focus = CommercialFocusResult(
            source_focus=fact.focus,
            focus="cost segregation studies",
            buyer_phrase="real estate owners considering cost segregation",
            rule_id="cost-segregation",
            priority=5,
            fit_tier="core",
        )
        single = qualify_company(
            fact,
            focus,
            corroborating_fields=("Company description",),
            fallback_min_agreeing_fields=2,
        )
        corroborated = qualify_company(
            fact,
            focus,
            corroborating_fields=("Company description", "Company keywords"),
            fallback_min_agreeing_fields=2,
        )
        self.assertEqual(single.status, "excluded")
        self.assertEqual(corroborated.status, "review")

    def test_contact_requires_an_approved_title_and_respects_seniority(self) -> None:
        ready = qualify_contact(
            first_name="Sam",
            job_title="Managing Director",
            job_seniority="Director",
            campaign=self.campaign,
        )
        review = qualify_contact(
            first_name="Sam",
            job_title="Director of Business Development",
            job_seniority="Director",
            campaign=self.campaign,
        )
        excluded = qualify_contact(
            first_name="Sam",
            job_title="Account Manager",
            job_seniority="Manager",
            campaign=self.campaign,
        )
        self.assertEqual(ready.status, "qualified")
        self.assertEqual(review.status, "review")
        self.assertEqual(excluded.status, "excluded")

    def test_president_is_not_mistaken_for_a_generic_vice_president(self) -> None:
        president = qualify_contact(
            first_name="Nick",
            job_title="President",
            job_seniority="Vice President",
            campaign=self.campaign,
        )
        vice_president = qualify_contact(
            first_name="Pat",
            job_title="Vice President",
            job_seniority="Vice President",
            campaign=self.campaign,
        )

        self.assertEqual(president.status, "qualified")
        self.assertEqual(vice_president.status, "review")

    def test_email_status_policy_and_syntax_fallback(self) -> None:
        self.assertTrue(valid_email_syntax("sam@example.com"))
        self.assertFalse(valid_email_syntax("not-an-email"))
        self.assertFalse(valid_email_syntax("sam,invalid@example.com"))
        self.assertFalse(valid_email_syntax("sam..invalid@example.com"))
        self.assertEqual(
            qualify_email("sam@example.com", "VERIFIED", self.campaign).status,
            "qualified",
        )
        self.assertEqual(
            qualify_email("sam@example.com", "catch all", self.campaign).status,
            "review",
        )
        self.assertEqual(
            qualify_email("sam@example.com", "invalid", self.campaign).status,
            "excluded",
        )
        self.assertEqual(
            qualify_email("sam@example.com", "", self.campaign).rule,
            "syntax-only",
        )

    def test_final_status_requires_all_gates_and_safe_copy(self) -> None:
        company = qualify_company(
            CompanyFact("service", "M&A", "M&A", "M&A advisory", "https://x.example", 0.9),
            CommercialFocusResult("M&A", "M&A advisory", "owners considering a sale", "ma", 1),
        )
        contact = qualify_contact(
            first_name="Sam",
            job_title="Founder",
            job_seniority="Founder/Owner",
            campaign=self.campaign,
        )
        email = qualify_email("sam@example.com", "verified", self.campaign)
        ready, _reason = compose_outreach_status(company, contact, email, "ready")
        review, _reason = compose_outreach_status(company, contact, email, "review")
        self.assertEqual(ready, "ready")
        self.assertEqual(review, "review")


if __name__ == "__main__":
    unittest.main()
