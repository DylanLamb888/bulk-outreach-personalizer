import unittest
from pathlib import Path

from bulk_enrich.hooks import TitleHookTable


ROOT = Path(__file__).resolve().parents[1]


class HookTableTests(unittest.TestCase):
    def test_matches_specific_title_then_fallback(self) -> None:
        table = TitleHookTable.load(ROOT / "config" / "title-hooks.csv")
        self.assertEqual(table.match("Founder & CEO").persona, "owner")
        self.assertEqual(table.match("Head of Operations").persona, "general")


if __name__ == "__main__":
    unittest.main()
