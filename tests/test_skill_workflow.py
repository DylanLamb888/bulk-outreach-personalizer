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
                        "subject": "Quick question, {{first_name}}",
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
            approved_opt_outs = {
                'p.s. if this isn’t a priority right now, reply "no thanks" and I’ll take you off my list.',
                'p.s. if this doesn’t fit your plans, reply "no thanks" and I’ll take you off my list.',
                'p.s. if this isn’t relevant to your company, reply "no thanks" and I’ll take you off my list.',
            }
            for row in rows:
                self.assertEqual(
                    row["personalized_subject"], f"Quick question, {row['first_name']}"
                )
                self.assertIn(" - we can ", row["personalized_email"])
                self.assertEqual(row["personalized_email"].count("p.s."), 1)
                self.assertIn(
                    row["personalized_email"].split("\n\n")[-1], approved_opt_outs
                )
                self.assertTrue(row["followup_3b"].startswith("Who handles "))
                for field in ("followup_2a", "followup_2b", "followup_3a", "followup_3b"):
                    self.assertNotIn("p.s.", row[field])
                    self.assertEqual(row[field].splitlines().count("Taylor"), 1)
                self.assertNotIn("100", row["personalized_email"])
                self.assertNotIn("tech fee", row["personalized_email"])
            self.assertTrue(rows[3]["personalized_email"].startswith("Hi Peter - "))
            self.assertTrue(rows[4]["personalized_email"].startswith("Hi Martin - "))

    def test_fresh_campaign_ps_revision_replays_twice_without_research(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            original = run_fresh_campaign(directory)
            campaign_path = directory / "campaign.json"
            campaign = json.loads(campaign_path.read_text())
            chosen_ps = campaign["sequence"]["ps_variants"][1]
            campaign["sequence"]["ps_variants"] = [chosen_ps]
            campaign_path.write_text(json.dumps(campaign))
            source = directory / "audit.csv"
            for name in ("revised", "repeated"):
                with (
                    patch("bulk_enrich.cli._load_local_env"),
                    patch(
                        "bulk_enrich.cli.provider_ready",
                        side_effect=AssertionError("provider preflight called"),
                    ),
                    patch(
                        "bulk_enrich.pipeline._enrich_domains",
                        side_effect=AssertionError("research called"),
                    ),
                    patch(
                        "bulk_enrich.fetcher.HttpFetcher.fetch",
                        side_effect=AssertionError("network called"),
                    ),
                    patch("subprocess.run", side_effect=AssertionError("model binary called")),
                    redirect_stdout(io.StringIO()),
                    redirect_stderr(io.StringIO()) as err,
                ):
                    code = main([
                        "--render-only", "--input", str(source),
                        "--campaign", str(campaign_path),
                        "--output", str(directory / f"{name}-audit.csv"),
                        "--smartlead-output", str(directory / f"{name}.csv"),
                        "--allow-test-campaign", "--quiet",
                    ])
                self.assertEqual(code, 0, err.getvalue())
                source = directory / f"{name}-audit.csv"
            with (directory / "revised.csv").open(encoding="utf-8-sig", newline="") as f:
                revised = list(csv.DictReader(f))
            self.assertEqual(len(original), len(revised))
            for before, after in zip(original, revised):
                for field in before:
                    if field == "personalized_email":
                        self.assertEqual(
                            before[field].rsplit("\n\np.s.", 1)[0],
                            after[field].rsplit("\n\np.s.", 1)[0],
                        )
                        self.assertTrue(after[field].endswith(chosen_ps))
                        self.assertEqual(after[field].count("p.s."), 1)
                    else:
                        self.assertEqual(before[field], after[field])
            self.assertEqual(
                (directory / "revised.csv").read_bytes(),
                (directory / "repeated.csv").read_bytes(),
            )
