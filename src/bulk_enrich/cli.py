"""Command-line interface for Bulk Enrich."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from bulk_enrich import __version__
from bulk_enrich.config import CampaignConfigError, load_campaign
from bulk_enrich.csv_io import inspect_csv
from bulk_enrich.focus import CommercialFocusError, CommercialFocusTable
from bulk_enrich.firecrawl import DEFAULT_FIRECRAWL_API_URL, FirecrawlSettings
from bulk_enrich.hooks import TitleHookTable
from bulk_enrich.pipeline import RunOptions, run_enrichment


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ENV_KEYS = frozenset({"FIRECRAWL_API_URL", "FIRECRAWL_API_KEY"})


def _load_local_env(path: Path = REPOSITORY_ROOT / ".env") -> None:
    """Load supported local settings without overriding the shell environment."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            raise ValueError(f"invalid local environment entry at {path}:{line_number}")
        key, value = (part.strip() for part in line.split("=", 1))
        if key not in LOCAL_ENV_KEYS:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bulk-enrich",
        description="Validate and run deterministic bulk outreach personalisation.",
    )
    parser.add_argument("--input", required=True, help="Source lead CSV")
    parser.add_argument("--output", required=True, help="Destination enriched CSV")
    parser.add_argument("--campaign", required=True, help="Campaign JSON configuration")
    parser.add_argument(
        "--title-hooks",
        default=str(REPOSITORY_ROOT / "config" / "title-hooks.csv"),
        help="Editable title-to-hook CSV",
    )
    parser.add_argument(
        "--focus-rules",
        help=(
            "Optional commercial-focus CSV override; by default the file declared "
            "by the campaign JSON is used"
        ),
    )
    parser.add_argument(
        "--cache-dir",
        default=str(REPOSITORY_ROOT / "var" / "cache"),
        help="Persistent HTTP and site-signal cache directory",
    )
    parser.add_argument(
        "--manifest",
        help="Optional run-manifest path (default: <output>.manifest.json)",
    )
    parser.add_argument(
        "--ready-output",
        help="Optional second CSV containing only rows marked outreach-ready",
    )
    parser.add_argument(
        "--review-output",
        help="Optional second CSV containing only rows requiring manual review",
    )
    parser.add_argument(
        "--concurrency",
        type=_positive_int,
        default=24,
        help="Number of unique company domains processed in parallel (default: 24)",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=12.0,
        help="HTTP timeout per request in seconds (default: 12)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        choices=range(0, 4),
        default=1,
        help="Retries for transient HTTP failures, from 0 to 3 (default: 1)",
    )
    parser.add_argument(
        "--max-pages",
        type=_positive_int,
        default=2,
        help="Maximum public pages fetched per unique domain (default: 2)",
    )
    parser.add_argument(
        "--max-response-bytes",
        type=_positive_int,
        default=750_000,
        help="Maximum HTML bytes retained per page (default: 750000)",
    )
    parser.add_argument(
        "--firecrawl-fallback",
        action="store_true",
        help=(
            "Use Firecrawl only when direct fetching fails or yields weak content; "
            "requires FIRECRAWL_API_URL for self-hosting or FIRECRAWL_API_KEY for hosted use"
        ),
    )
    parser.add_argument(
        "--firecrawl-api-url",
        default=os.environ.get("FIRECRAWL_API_URL", DEFAULT_FIRECRAWL_API_URL),
        help=(
            "Firecrawl API base URL (default: FIRECRAWL_API_URL or hosted Firecrawl)"
        ),
    )
    parser.add_argument(
        "--firecrawl-timeout",
        type=_positive_float,
        default=30.0,
        help="Firecrawl request timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--firecrawl-concurrency",
        type=_positive_int,
        default=4,
        help="Maximum simultaneous Firecrawl requests (default: 4)",
    )
    parser.add_argument(
        "--cache-ttl-hours",
        type=_positive_float,
        default=168.0,
        help="Successful cache lifetime in hours (default: 168)",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Ignore existing cache entries and fetch fresh public pages",
    )
    parser.add_argument(
        "--allow-test-campaign",
        action="store_true",
        help="Allow a production output using a campaign marked test_only",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress progress updates")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate inputs and print the domain plan without fetching websites",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _validation_payload(
    input_path: str,
    output_path: str,
    campaign_path: str,
    title_hooks_path: str,
    focus_rules_path: str | None,
    *,
    allow_test_campaign: bool,
    firecrawl_fallback: bool = False,
    firecrawl_api_url: str = DEFAULT_FIRECRAWL_API_URL,
) -> dict[str, object]:
    campaign = load_campaign(campaign_path)
    csv_summary = inspect_csv(input_path)
    hooks = TitleHookTable.load(title_hooks_path)
    configured_focus_path = (
        Path(focus_rules_path).expanduser().resolve()
        if focus_rules_path
        else campaign.focus_rules_path
    )
    focuses = CommercialFocusTable.load(configured_focus_path)
    if firecrawl_fallback:
        FirecrawlSettings(
            api_url=firecrawl_api_url,
            api_key=os.environ.get("FIRECRAWL_API_KEY", ""),
        ).validate()
    execution_ready = campaign.status == "approved" or allow_test_campaign
    template_count = sum(len(angle.templates) for angle in campaign.angles)
    return {
        "status": "validated",
        "campaign_id": campaign.campaign_id,
        "campaign_status": campaign.status,
        "input": csv_summary.to_dict(),
        "title_hook_rules": len(hooks.rules),
        "commercial_focus_rules": len(focuses.rules),
        "commercial_focus_path": str(focuses.path),
        "qualification": {
            "company_fit_tiers": dict(
                sorted(Counter(rule.fit_tier for rule in focuses.rules).items())
            ),
            "fallback_min_agreeing_fields": campaign.fallback_min_agreeing_fields,
            "fallback_fields": list(campaign.fallback_qualification_fields),
            "ready_title_patterns": len(campaign.ready_title_patterns),
            "review_title_patterns": len(campaign.review_title_patterns),
            "exclude_title_patterns": len(campaign.excluded_title_patterns),
            "missing_email_status_action": campaign.missing_email_status_action,
        },
        "copy_angles": len(campaign.angles),
        "copy_templates": template_count,
        "banned_phrases": len(campaign.banned_phrases),
        "quality_gate": campaign.data["quality"],
        "firecrawl": {
            "enabled": firecrawl_fallback,
            "api_url": firecrawl_api_url if firecrawl_fallback else "",
            "api_key_configured": bool(os.environ.get("FIRECRAWL_API_KEY", "").strip()),
        },
        "requested_output": str(Path(output_path).expanduser().resolve()),
        "execution_ready": execution_ready,
        "execution_blocker": (
            "campaign is marked test_only; approve it or pass --allow-test-campaign"
            if not execution_ready
            else ""
        ),
    }


def main(argv: list[str] | None = None) -> int:
    try:
        _load_local_env()
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        campaign = load_campaign(args.campaign)
        if args.validate_only:
            payload = _validation_payload(
                args.input,
                args.output,
                args.campaign,
                args.title_hooks,
                args.focus_rules,
                allow_test_campaign=args.allow_test_campaign,
                firecrawl_fallback=args.firecrawl_fallback,
                firecrawl_api_url=args.firecrawl_api_url,
            )
        else:
            if campaign.status == "test_only" and not args.allow_test_campaign:
                raise ValueError(
                    "campaign is marked test_only; change status to approved after review "
                    "or pass --allow-test-campaign for a controlled test"
                )
            hooks = TitleHookTable.load(args.title_hooks)
            focus_rules_path = (
                Path(args.focus_rules).expanduser().resolve()
                if args.focus_rules
                else campaign.focus_rules_path
            )
            focuses = CommercialFocusTable.load(focus_rules_path)

            def progress(completed: int, total: int, domain: str, status: str) -> None:
                if args.quiet:
                    return
                if completed == 1 or completed == total or completed % 25 == 0:
                    print(
                        f"domains {completed}/{total}: {domain} [{status}]",
                        file=sys.stderr,
                        flush=True,
                    )

            manifest = run_enrichment(
                input_path=args.input,
                output_path=args.output,
                campaign=campaign,
                title_hooks=hooks,
                commercial_focuses=focuses,
                options=RunOptions(
                    cache_dir=Path(args.cache_dir).expanduser().resolve(),
                    concurrency=args.concurrency,
                    timeout=args.timeout,
                    retries=args.retries,
                    max_pages=args.max_pages,
                    max_response_bytes=args.max_response_bytes,
                    cache_ttl_hours=args.cache_ttl_hours,
                    refresh_cache=args.refresh_cache,
                    firecrawl_fallback=args.firecrawl_fallback,
                    firecrawl_api_url=args.firecrawl_api_url,
                    firecrawl_timeout=args.firecrawl_timeout,
                    firecrawl_concurrency=args.firecrawl_concurrency,
                    manifest_path=(
                        Path(args.manifest).expanduser().resolve() if args.manifest else None
                    ),
                    ready_output_path=(
                        Path(args.ready_output).expanduser().resolve()
                        if args.ready_output
                        else None
                    ),
                    review_output_path=(
                        Path(args.review_output).expanduser().resolve()
                        if args.review_output
                        else None
                    ),
                ),
                progress=progress,
            )
            payload = {
                "status": "completed",
                "campaign_id": campaign.campaign_id,
                "output": manifest["output"],
                "domains": manifest["domains"],
                "http": manifest["http"],
                "firecrawl": manifest["firecrawl"],
                "qualification": manifest["qualification"],
                "duration_seconds": manifest["run"]["duration_seconds"],
            }
    except (
        CampaignConfigError,
        CommercialFocusError,
        FileNotFoundError,
        OSError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0
