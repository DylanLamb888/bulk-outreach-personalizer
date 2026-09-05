"""Fresh campaign through the public CLI; no old output or revision scripts."""

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from bulk_enrich.cli import main
from bulk_enrich.models import CompanyFact, SiteSignal

ROOT = Path(__file__).resolve().parents[1]


class SyntheticSites:
    def enrich(self, domain):
        fact = CompanyFact(
            "service",
            "thermal modelling",
            "engineering services",
            "Our engineers provide thermal modelling for new developments.",
            "https://" + domain + "/",
            0.94,
        )
        return SiteSignal(
            domain,
            fact.observation,
            fact.evidence,
            fact.source_url,
            0.94,
            "ok",
            focus=fact.focus,
            signal_type="service",
            facts=(fact,),
        )


def run_fresh_campaign(directory, repository=ROOT):
    directory.mkdir(parents=True, exist_ok=True)
    data = json.loads((repository / "campaigns/campaign-template.json").read_text())
    data.update(
        campaign_id="synthetic-engineering",
        client_name="Synthetic Studio",
        sender={"name": "Taylor"},
    )
    data["offer"].update(
        service="Campaign planning for engineering consultancies.",
        audience="Engineering company owners.",
        risk_reversal="",
        risk_reversal_variants=[],
        cta="Want me to share an outline?",
        cta_variants=[],
        approved_claims=[],
    )
    data["personalization"].update(
        focus_rules_file="focus.csv",
        max_words=24,
        angles=[
            {
                "id": "direct",
                "signal_types": ["*"],
                "templates": [
                    {
                        "id": "intro",
                        "subject": "New conversations",
                        "pitch": "We can introduce you to {{buyer_phrase}} to discuss {{company_focus}}.",
                    }
                ],
            }
        ],
    )
    data["sequence"]["followups"].update(
        followup_2a="I’d focus on {{buyer_phrase}}.\n\nWould an outline help?",
        followup_2b="Want me to share the approach?",
        followup_3a="Would that be useful for you?",
        followup_3b="Who handles new business at {{company_short_name}}?",
    )
    (directory / "campaign.json").write_text(json.dumps(data))
    (directory / "focus.csv").write_text(
        "id,priority,pattern,signal_types,fit_tier,focus,buyer_phrase\nthermal,1,thermal modelling,*,core,thermal modelling,building developers\n"
    )
    with (directory / "leads.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "Email",
                "Email status",
                "First name",
                "Job title",
                "Company name",
                "Website",
            ]
        )
        for i, name in enumerate(["Ana", "Ben", "Élodie", "Dr Peter", "🐧 Martin"]):
            w.writerow(
                [
                    f"owner{i}@example.test",
                    "verified",
                    name,
                    "Founder",
                    f"Studio {i}",
                    f"studio{i}.example",
                ]
            )
    args = [
        "--input",
        str(directory / "leads.csv"),
        "--output",
        str(directory / "audit.csv"),
        "--campaign",
        str(directory / "campaign.json"),
        "--smartlead-output",
        str(directory / "smartlead.csv"),
        "--cache-dir",
        str(directory / "cache"),
        "--allow-test-campaign",
        "--quiet",
    ]
    with (
        patch("bulk_enrich.pipeline.SiteEnricher", return_value=SyntheticSites()),
        patch(
            "bulk_enrich.fetcher.HttpFetcher.fetch",
            side_effect=AssertionError("network called"),
        ),
        patch("subprocess.run", side_effect=AssertionError("model binary called")),
        patch("bulk_enrich.cli._load_local_env"),
        redirect_stdout(io.StringIO()),
        redirect_stderr(io.StringIO()) as err,
    ):
        code = main(args)
    if code:
        raise AssertionError(err.getvalue())
    with (directory / "smartlead.csv").open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


class FreshSkillWorkflowTests(unittest.TestCase):
    def test_new_campaign_uses_standard_entrypoint_without_revision_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = run_fresh_campaign(Path(tmp))
            self.assertEqual(len(rows), 5)
            for row in rows:
                self.assertIn(" - we can ", row["personalized_email"])
                self.assertEqual(row["personalized_email"].count("p.s."), 1)
                self.assertIn('"no thanks"', row["personalized_email"])
                self.assertIn("Taylor", row["followup_3b"])
                self.assertNotIn("100", row["personalized_email"])
                self.assertNotIn("tech fee", row["personalized_email"])
            self.assertTrue(rows[3]["personalized_email"].startswith("Hi Peter - "))
            self.assertTrue(rows[4]["personalized_email"].startswith("Hi Martin - "))
