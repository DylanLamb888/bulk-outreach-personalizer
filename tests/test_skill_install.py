import tempfile
import unittest
from pathlib import Path

from bulk_enrich.skill_install import (
    SKILL_NAME,
    SkillInstallError,
    install_for_products,
)


class SkillInstallTests(unittest.TestCase):
    def test_installs_one_source_for_both_products_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "repository" / "skill" / SKILL_NAME
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text("---\nname: example\n---\n")
            home = root / "home"

            first = install_for_products(
                products=("codex", "claude"),
                home=home,
                source=source,
            )
            self.assertEqual([item.status for item in first], ["installed", "installed"])
            for item in first:
                self.assertTrue(item.destination.is_symlink())
                self.assertEqual(item.destination.resolve(), source.resolve())

            second = install_for_products(
                products=("codex", "claude"),
                home=home,
                source=source,
            )
            self.assertEqual(
                [item.status for item in second],
                ["already-installed", "already-installed"],
            )

    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "skill"
            source.mkdir()
            (source / "SKILL.md").write_text("---\nname: example\n---\n")
            home = root / "home"

            results = install_for_products(
                products=("codex", "claude"),
                home=home,
                source=source,
                dry_run=True,
            )
            self.assertEqual([item.status for item in results], ["planned", "planned"])
            self.assertFalse(home.exists())

    def test_refuses_to_replace_existing_skill_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "skill"
            source.mkdir()
            (source / "SKILL.md").write_text("---\nname: example\n---\n")
            destination = root / "home" / ".codex" / "skills" / SKILL_NAME
            destination.mkdir(parents=True)

            with self.assertRaisesRegex(SkillInstallError, "refusing to replace"):
                install_for_products(
                    products=("codex",),
                    home=root / "home",
                    source=source,
                )


if __name__ == "__main__":
    unittest.main()
