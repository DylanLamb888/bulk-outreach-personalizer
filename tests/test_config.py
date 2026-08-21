import json
import tempfile
import unittest
from pathlib import Path

from bulk_enrich.config import CampaignConfigError, load_campaign


ROOT = Path(__file__).resolve().parents[1]


class CampaignConfigTests(unittest.TestCase):
    def test_schema_v2_is_rejected_with_a_migration_message(self) -> None:
        payload = json.loads((ROOT / "campaigns" / "campaign-template.json").read_text())
        payload["schema_version"] = "2.0"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v2.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(CampaignConfigError, "migrate.*schema 3.0"):
                load_campaign(path)

    def test_campaign_template_is_valid(self) -> None:
        config = load_campaign(ROOT / "campaigns" / "campaign-template.json")
        self.assertEqual(config.campaign_id, "replace-me")
        self.assertEqual(
            config.focus_rules_path,
            (ROOT / "campaigns" / "campaign-template-focus.csv").resolve(),
        )

    def test_example_campaigns_are_valid(self) -> None:
        for path in sorted((ROOT / "campaigns" / "examples").glob("*.json")):
            with self.subTest(path=path.name):
                config = load_campaign(path)
                self.assertEqual(config.status, "test_only")

    def test_scale_olympus_uses_three_approved_offer_test_versions(self) -> None:
        config = load_campaign(
            ROOT / "campaigns" / "examples" / "scale-olympus.json"
        )
        self.assertEqual(
            [variant.variant_id for variant in config.offer_line_variants],
            ["v1-performance", "v2-done-for-you", "v3-small-upfront"],
        )
        self.assertGreater(config.data["quality"]["max_offer_line_share"], 1 / 3)
        for variant in config.offer_line_variants:
            with self.subTest(variant=variant.variant_id):
                self.assertIn("small tech fee", variant.text.casefold())
                self.assertIn("qualified call", variant.text.casefold())
                self.assertNotIn("the model is", variant.text.casefold())
                self.assertNotIn("guarantee", variant.text.casefold())
                self.assertNotIn("client result", variant.text.casefold())
        for variant in config.cta_variants:
            with self.subTest(cta=variant.variant_id):
                self.assertIn("25", variant.text)
                self.assertIn("compan", variant.text.casefold())

    def test_missing_output_merge_field_is_rejected(self) -> None:
        payload = json.loads((ROOT / "campaigns" / "campaign-template.json").read_text())
        payload["email"]["body"] = "Hi {{first_name}}"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.json"
            path.write_text(json.dumps(payload))
            with self.assertRaises(CampaignConfigError):
                load_campaign(path)

    def test_banned_phrase_in_copy_is_rejected(self) -> None:
        payload = json.loads((ROOT / "campaigns" / "campaign-template.json").read_text())
        payload["personalization"]["angles"][0]["templates"][0]["pitch"] = (
            "Saw that {{company_focus}}. Could the approved offer help?"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(CampaignConfigError, "banned phrase"):
                load_campaign(path)

    def test_blocked_evidence_phrases_must_be_strings(self) -> None:
        payload = json.loads((ROOT / "campaigns" / "campaign-template.json").read_text())
        payload["personalization"]["blocked_evidence_phrases"] = [1]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "campaign.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(
                CampaignConfigError, "blocked_evidence_phrases"
            ):
                load_campaign(path)

    def test_blocked_evidence_phrases_default_to_empty(self) -> None:
        config = load_campaign(ROOT / "campaigns" / "campaign-template.json")
        self.assertEqual(config.blocked_evidence_phrases, ())

    def test_missing_fallback_angle_is_rejected(self) -> None:
        payload = json.loads((ROOT / "campaigns" / "campaign-template.json").read_text())
        payload["personalization"]["angles"][0]["signal_types"] = ["service"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(CampaignConfigError, "fallback"):
                load_campaign(path)

    def test_duplicate_cta_variant_id_is_rejected(self) -> None:
        payload = json.loads((ROOT / "campaigns" / "campaign-template.json").read_text())
        payload["offer"]["cta_variants"][1]["id"] = payload["offer"]["cta_variants"][0]["id"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(CampaignConfigError, "duplicate CTA"):
                load_campaign(path)

    def test_duplicate_offer_line_variant_id_is_rejected(self) -> None:
        payload = json.loads((ROOT / "campaigns" / "campaign-template.json").read_text())
        payload["offer"]["risk_reversal_variants"][1]["id"] = (
            payload["offer"]["risk_reversal_variants"][0]["id"]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(CampaignConfigError, "duplicate offer-line"):
                load_campaign(path)

    def test_em_dash_in_configured_copy_is_rejected(self) -> None:
        payload = json.loads((ROOT / "campaigns" / "campaign-template.json").read_text())
        payload["offer"]["risk_reversal_variants"][0]["text"] = (
            "You pay only for results\u2014not activity."
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(CampaignConfigError, "em dash"):
                load_campaign(path)

    def test_offer_line_variants_require_a_default_focus_fallback(self) -> None:
        payload = json.loads((ROOT / "campaigns" / "campaign-template.json").read_text())
        for variant in payload["offer"]["risk_reversal_variants"]:
            variant["focus_rules"] = ["one-specific-rule"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(CampaignConfigError, "focus-rule fallback"):
                load_campaign(path)

    def test_cta_variants_require_a_default_focus_fallback(self) -> None:
        payload = json.loads((ROOT / "campaigns" / "campaign-template.json").read_text())
        for variant in payload["offer"]["cta_variants"]:
            variant["focus_rules"] = ["one-specific-rule"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(CampaignConfigError, "focus-rule fallback"):
                load_campaign(path)

    def test_focus_rules_file_is_required(self) -> None:
        payload = json.loads((ROOT / "campaigns" / "campaign-template.json").read_text())
        del payload["personalization"]["focus_rules_file"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(CampaignConfigError, "focus_rules_file"):
                load_campaign(path)

    def test_row_fallback_field_confidence_is_validated(self) -> None:
        payload = json.loads((ROOT / "campaigns" / "campaign-template.json").read_text())
        payload["personalization"]["row_fallback"]["fields"][0]["confidence"] = 1.5
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(CampaignConfigError, "confidence"):
                load_campaign(path)

    def test_qualification_fallback_fields_must_be_approved_sources(self) -> None:
        payload = json.loads((ROOT / "campaigns" / "campaign-template.json").read_text())
        payload["qualification"]["company"]["fallback_fields"].append("Unknown field")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(CampaignConfigError, "fallback fields"):
                load_campaign(path)

    def test_invalid_contact_qualification_regex_is_rejected(self) -> None:
        payload = json.loads((ROOT / "campaigns" / "campaign-template.json").read_text())
        payload["qualification"]["contact"]["ready_title_patterns"] = ["("]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(CampaignConfigError, "contact pattern"):
                load_campaign(path)


if __name__ == "__main__":
    unittest.main()
