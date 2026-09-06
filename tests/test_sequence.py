import copy
import unittest
from pathlib import Path

from bulk_enrich.config import CampaignConfig, load_campaign
from bulk_enrich.renderer import render_email
from bulk_enrich.sequence import (
    FOLLOWUPS,
    editorial_context,
    render_sequence,
    validate_sequence,
)

ROOT = Path(__file__).resolve().parents[1]


def sequence_config():
    data = copy.deepcopy(
        load_campaign(ROOT / "campaigns/examples/scale-olympus.json").data
    )
    data["sequence"] = {
        "greeting": "inline",
        "max_followup_words": 55,
        "followups": {
            k: "Would an outline for {{company_focus}} help?" for k in FOLLOWUPS
        },
        "neutral_followups": {k: "Would an outline help?" for k in FOLLOWUPS},
        "ps_variants": [
            'p.s. if this isn’t of interest, reply "no thanks" and I’ll take you off my list.',
            'p.s. if you’d prefer not to hear from me, reply "no thanks" and I’ll take you off my list.',
        ],
    }
    return CampaignConfig(ROOT / "campaigns/examples/scale-olympus.json", data)


class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.config = sequence_config()
        self.context = {
            "first_name": "Ana",
            "company_name": "Brand Potential",
            "company_focus": "ai research",
            "buyer_phrase": "technology companies",
        }
        self.body = "Hi Ana - we can start a conversation.\n\nDylan"

    def test_inline_greeting_preserves_names_i_and_acronyms(self):
        for pitch, expected in [
            ("We can help.", "we can help."),
            ("I can help.", "I can help."),
            ("IBM uses this.", "IBM uses this."),
        ]:
            _, body = render_email(self.config, self.context, pitch, "{{company_name}}")
            self.assertTrue(body.startswith("Hi Ana - " + expected))

    def test_complete_sequence_is_deterministic_and_non_mutating(self):
        before = dict(self.context)
        a = render_sequence(self.config, self.context, "example.test", self.body)
        b = render_sequence(self.config, self.context, "example.test", self.body)
        self.assertEqual(a, b)
        self.assertEqual(self.context, before)
        for k in ("personalized_email", *FOLLOWUPS):
            self.assertEqual(a[k].count("p.s."), 1)
            self.assertEqual(a[k].splitlines().count("Dylan"), 1)
            self.assertIn('"no thanks"', a[k])

    def test_explicit_neutral_fallback_for_missing_slots(self):
        self.context["company_focus"] = ""
        result = render_sequence(self.config, self.context, "example.test", self.body)
        self.assertTrue(result["followup_2a"].startswith("Would an outline help?"))
        del self.config.data["sequence"]["neutral_followups"]
        with self.assertRaisesRegex(ValueError, "missing sequence slots"):
            render_sequence(self.config, self.context, "example.test", self.body)

    def test_first_only_ps_preserves_followup_signature_and_length_budget(self):
        self.config.data["sequence"]["ps_scope"] = "first_only"
        self.config.data["sequence"]["max_followup_words"] = 10
        validate_sequence(self.config.data)
        result = render_sequence(self.config, self.context, "example.test", self.body)
        self.assertEqual(result["personalized_email"].count("p.s."), 1)
        for field in FOLLOWUPS:
            self.assertNotIn("p.s.", result[field])
            self.assertTrue(result[field].endswith("\n\nDylan"))
        self.assertEqual(
            result, render_sequence(self.config, self.context, "example.test", self.body)
        )

    def test_invalid_ps_scope_rejected(self):
        for scope in (None, "followups", False):
            self.config.data["sequence"]["ps_scope"] = scope
            with self.assertRaisesRegex(ValueError, "ps_scope"):
                validate_sequence(self.config.data)

    def test_one_offer_buyer_angle_and_neutral_sequence(self):
        seq = self.config.data["sequence"]
        seq["ps_scope"] = "first_only"
        seq["followups"] = {
            "followup_2a": "We can reach {{buyer_phrase}} to discuss {{company_focus}}. {{cta}}",
            "followup_2b": "The list gives you prospects to approach about {{company_focus}}. {{cta}}",
            "followup_3a": "{{first_name}}, shall I send the list?",
            "followup_3b": "{{cta}}",
        }
        seq["neutral_followups"] = {field: "{{cta}}" for field in FOLLOWUPS}
        self.context["cta"] = "Shall I send you the list?"
        validate_sequence(self.config.data)
        result = render_sequence(self.config, self.context, "example.test", self.body)
        self.assertIn("technology companies", result["followup_2a"])
        self.assertIn("ai research", result["followup_2b"])
        self.assertEqual(result["followup_3a"], "Ana, shall I send the list?\n\nDylan")
        self.context["buyer_phrase"] = ""
        neutral = render_sequence(self.config, self.context, "example.test", self.body)
        for field in FOLLOWUPS:
            self.assertEqual(neutral[field], "Shall I send you the list?\n\nDylan")

    def test_bad_followup_blocks_delivery(self):
        self.config.data["sequence"]["followups"]["followup_3b"] = (
            "We guarantee five qualified meetings every week."
        )
        with self.assertRaisesRegex(ValueError, "unapproved"):
            render_sequence(self.config, self.context, "example.test", self.body)

    def test_ps_counts_toward_length_and_cannot_be_appended_twice(self):
        self.config.data["sequence"]["max_followup_words"] = 10
        with self.assertRaisesRegex(ValueError, "including P.S."):
            render_sequence(self.config, self.context, "example.test", self.body)
        with self.assertRaisesRegex(ValueError, "signature|P.S."):
            render_sequence(
                self.config, self.context, "example.test", self.body + "\n\np.s. stop"
            )

    def test_editorial_data_does_not_change_evidence_or_source(self):
        self.config.data["sequence"]["editorial_replacements"] = {
            "ai research": {"text": "ai consulting", "reason": "approved wording"}
        }
        self.config.data["sequence"]["company_name_overrides"] = {
            "example.test": {"text": "Example", "reason": "verified homepage"}
        }
        self.context["evidence"] = "ai research"
        self.context["source"] = "https://example.test/"
        result = editorial_context(self.config, self.context, "example.test")
        self.assertEqual(result["company_focus"], "ai consulting")
        self.assertEqual(result["evidence"], "ai research")
        self.assertEqual(self.context["company_name"], "Brand Potential")

    def test_configuration_rejects_unknown_empty_and_duplicate_fields(self):
        for edit in [
            lambda s: s.update(greeting="broken"),
            lambda s: s["followups"].update(followup_2a="{{unknown}}"),
            lambda s: s["followups"].update(followup_2a="{{sender_name}}"),
            lambda s: s.update(ps_variants=["p.s. hello", "p.s. hello"]),
        ]:
            cfg = sequence_config()
            edit(cfg.data["sequence"])
            with self.assertRaises(ValueError):
                validate_sequence(cfg.data)

    def test_first_email_promise_requires_the_complete_approved_claim(self):
        body = "Hi Ana - we guarantee five qualified meetings every week.\n\nDylan"
        with self.assertRaisesRegex(ValueError, "unapproved"):
            render_sequence(self.config, self.context, "example.test", body)
        self.config.data["offer"]["approved_claims"] = [
            "We guarantee five qualified meetings every week."
        ]
        result = render_sequence(self.config, self.context, "example.test", body)
        self.assertEqual(result["sequence_status"], "ready")

    def test_greeting_removes_badges_and_titles_without_losing_name(self):
        from bulk_enrich.pipeline import _clean_first_name

        for raw, expected in [
            ("🐧 Martin", "Martin"),
            ("Dr Peter", "Peter"),
            ("Élodie", "Élodie"),
            ("🐧", ""),
        ]:
            self.assertEqual(_clean_first_name(raw), expected)

    def test_literal_promises_fail_campaign_preflight(self):
        self.config.data["sequence"]["followups"]["followup_2a"] = (
            "We guarantee five meetings every week."
        )
        with self.assertRaisesRegex(ValueError, "unapproved"):
            validate_sequence(self.config.data)

    def test_direct_validation_rejects_null_sequence(self):
        from bulk_enrich.config import validate_campaign_data, CampaignConfigError

        self.config.data["sequence"] = None
        with self.assertRaises(CampaignConfigError):
            validate_campaign_data(self.config.data)

    def test_legacy_configuration_unchanged(self):
        del self.config.data["sequence"]
        self.assertEqual(
            render_sequence(self.config, {}, "", self.body),
            {"personalized_email": self.body},
        )
        _, body = render_email(self.config, self.context, "We can help.", "Hello")
        self.assertTrue(body.startswith("Hi Ana,\n\nWe can help."))
