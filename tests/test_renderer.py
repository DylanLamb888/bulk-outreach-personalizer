import unittest
from pathlib import Path

from bulk_enrich.config import load_campaign
from bulk_enrich.renderer import render_email, render_template


ROOT = Path(__file__).resolve().parents[1]


class RendererTests(unittest.TestCase):
    def test_preserves_spintax_and_renders_double_braces(self) -> None:
        rendered = render_template("{Hi|Hello} {{first_name}}", {"first_name": "Ana"})
        self.assertEqual(rendered, "{Hi|Hello} Ana")

    def test_renders_complete_email(self) -> None:
        config = load_campaign(ROOT / "campaigns" / "examples" / "scale-olympus.json")
        subject, body = render_email(
            config,
            {
                "first_name": "Ana",
                "company_name": "Northstar Finance",
                "cta": "Should I send the audience I'd start with?",
            },
            "Could outbound around financial reporting open more conversations?",
            "{{company_name}} outreach",
        )
        self.assertEqual(subject, "Northstar Finance outreach")
        self.assertIn("Could outbound", body)
        self.assertIn("small tech fee", body)
        self.assertIn("fee for each qualified call", body)
        self.assertIn("Should I send the audience I'd start with?", body)
        self.assertTrue(body.endswith("Dylan"))


if __name__ == "__main__":
    unittest.main()
