"""Install one repository-owned Skill for Codex and Claude Code."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


SKILL_NAME = "bulk-outreach-personalizer"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SKILL_SOURCE = REPOSITORY_ROOT / "skill" / SKILL_NAME


class SkillInstallError(RuntimeError):
    """Raised when a Skill target exists but points somewhere else."""


@dataclass(frozen=True)
class InstallResult:
    product: str
    destination: Path
    source: Path
    status: str


def _destinations(home: Path) -> dict[str, Path]:
    return {
        "codex": home / ".codex" / "skills" / SKILL_NAME,
        "claude": home / ".claude" / "skills" / SKILL_NAME,
    }


def install_skill_link(
    *,
    product: str,
    source: Path,
    destination: Path,
    dry_run: bool = False,
) -> InstallResult:
    """Create an idempotent symlink without overwriting an existing Skill."""
    source = source.expanduser().resolve()
    destination = destination.expanduser()
    if not (source / "SKILL.md").is_file():
        raise SkillInstallError(f"Skill entrypoint not found: {source / 'SKILL.md'}")

    if destination.is_symlink():
        if destination.resolve() == source:
            return InstallResult(product, destination, source, "already-installed")
        raise SkillInstallError(
            f"refusing to replace existing {product} Skill link: {destination}"
        )
    if destination.exists():
        raise SkillInstallError(
            f"refusing to replace existing {product} Skill directory: {destination}"
        )
    if dry_run:
        return InstallResult(product, destination, source, "planned")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(source, target_is_directory=True)
    return InstallResult(product, destination, source, "installed")


def install_for_products(
    *,
    products: tuple[str, ...],
    home: Path,
    source: Path = SKILL_SOURCE,
    dry_run: bool = False,
) -> tuple[InstallResult, ...]:
    destinations = _destinations(home.expanduser())
    return tuple(
        install_skill_link(
            product=product,
            source=source,
            destination=destinations[product],
            dry_run=dry_run,
        )
        for product in products
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="install-bulk-outreach-skill",
        description=(
            "Link the repository Skill into Codex, Claude Code, or both without "
            "creating duplicate copies."
        ),
    )
    parser.add_argument(
        "--target",
        choices=("both", "codex", "claude"),
        default="both",
        help="Skill host to configure (default: both)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the links that would be created without writing them",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    products = ("codex", "claude") if args.target == "both" else (args.target,)
    try:
        results = install_for_products(
            products=products,
            home=Path.home(),
            dry_run=args.dry_run,
        )
    except (OSError, SkillInstallError) as exc:
        print(f"error: {exc}")
        return 2

    for result in results:
        print(
            f"{result.product}: {result.status}: "
            f"{result.destination} -> {result.source}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
