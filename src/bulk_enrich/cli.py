"""Command-line interface for Bulk Outreach Personalizer."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from bulk_enrich import __version__
from bulk_enrich.cache import JsonCache
from bulk_enrich.config import CampaignConfigError, load_campaign
from bulk_enrich.csv_io import inspect_csv
from bulk_enrich.focus import CommercialFocusError, CommercialFocusTable
from bulk_enrich.firecrawl import DEFAULT_FIRECRAWL_API_URL, FirecrawlSettings
from bulk_enrich.hooks import TitleHookTable
from bulk_enrich.llm_focus import (
    LlmFocusError,
    estimate_nominal_usd,
    provider_ready,
    supports_effort,
)
from bulk_enrich.pipeline import (
    RunOptions,
    run_company_qualification,
    run_digests,
    run_enrichment,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ENV_KEYS = frozenset(
    {
        "FIRECRAWL_API_URL",
        "FIRECRAWL_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_PROFILE",
    }
)


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
        prog="bulk-outreach-personalizer",
        description="Validate and run deterministic bulk outreach personalisation.",
    )
    parser.add_argument("--input", required=True, help="Source lead CSV")
    parser.add_argument("--smartlead-output", help="Compact complete-sequence CSV and delivery bundle")
    parser.add_argument("--render-only", action="store_true", help="Render a saved audit without research or model calls")
    parser.add_argument("--output", required=True, help="Destination enriched CSV")
    parser.add_argument(
        "--campaign",
        help="Campaign JSON configuration (required except with --digest-only)",
    )
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
        "--digest-only",
        action="store_true",
        help=(
            "Fetch each company's public pages and write their text digests with no "
            "campaign and no model, for looking at a list before defining the target"
        ),
    )
    parser.add_argument(
        "--company-qualification-only",
        action="store_true",
        help=(
            "Qualify company domains from public website evidence and skip contact, "
            "email, sequencing, and outreach-copy processing"
        ),
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
        "--prune-cache",
        action="store_true",
        help="Delete cache entries older than --cache-ttl-hours before running",
    )
    parser.add_argument(
        "--llm-mode",
        choices=("sync", "batch"),
        default="sync",
        help=(
            "How campaign llm_focus calls run; batch uses the Message Batches API and "
            "is only valid with provider 'api' (default: sync)"
        ),
    )
    parser.add_argument(
        "--llm-concurrency",
        type=_positive_int,
        default=2,
        help="Simultaneous model calls, CLI or API (default: 2)",
    )
    parser.add_argument(
        "--llm-poll-seconds",
        type=_positive_float,
        default=30.0,
        help="Seconds between batch status checks in batch mode (default: 30)",
    )
    parser.add_argument(
        "--llm-cache-ttl-hours",
        type=_positive_float,
        default=720.0,
        help="Lifetime of cached model decisions per domain (default: 720)",
    )
    parser.add_argument(
        "--llm-budget-usd",
        type=_positive_float,
        help="Override the campaign's per-run nominal usage budget for model calls",
    )
    parser.add_argument(
        "--llm-batch-id",
        action="append",
        default=[],
        metavar="ID",
        help="Reuse the results of an already submitted batch (repeatable)",
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
    company_qualification_only: bool = False,
    firecrawl_fallback: bool = False,
    firecrawl_api_url: str = DEFAULT_FIRECRAWL_API_URL,
) -> dict[str, object]:
    campaign = load_campaign(campaign_path)
    csv_summary = inspect_csv(
        input_path,
        company_only=company_qualification_only,
    )
    hooks = None if company_qualification_only else TitleHookTable.load(title_hooks_path)
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
    llm_settings = campaign.llm_focus
    llm_payload: dict[str, object] = {"enabled": False}
    if llm_settings is not None:
        ready, blocker = provider_ready(llm_settings, dict(os.environ))
        llm_payload = {
            "enabled": True,
            "provider": llm_settings.provider,
            "model": llm_settings.model,
            "effort": llm_settings.effort if supports_effort(llm_settings.model) else None,
            "domains_per_call": llm_settings.domains_per_call,
            "write_pitch": llm_settings.write_pitch,
            "max_pitch_words": llm_settings.max_pitch_words,
            "examples": len(llm_settings.examples),
            "max_evidence_chars": llm_settings.max_evidence_chars,
            "estimated_nominal_usd_for_list": estimate_nominal_usd(
                llm_settings.model, csv_summary.unique_domain_count
            ),
            "budget_usd_per_run": llm_settings.max_nominal_usd,
            "provider_ready": ready,
            "provider_blocker": blocker,
        }
    return {
        "status": "validated",
        "mode": (
            "company_qualification_only"
            if company_qualification_only
            else "outreach_personalization"
        ),
        "campaign_id": campaign.campaign_id,
        "campaign_status": campaign.status,
        "input": csv_summary.to_dict(),
        "title_hook_rules": len(hooks.rules) if hooks is not None else 0,
        "commercial_focus_rules": len(focuses.rules),
        "commercial_focus_path": str(focuses.path),
        "qualification": {
            "company_fit_tiers": dict(
                sorted(Counter(rule.fit_tier for rule in focuses.rules).items())
            ),
            "fallback_min_agreeing_fields": campaign.fallback_min_agreeing_fields,
            "fallback_fields": list(campaign.fallback_qualification_fields),
            "contact_and_email_gates_skipped": company_qualification_only,
            **(
                {}
                if company_qualification_only
                else {
                    "priority_title_patterns": len(campaign.contact_priority_patterns),
                    "ready_title_patterns": len(campaign.ready_title_patterns),
                    "review_title_patterns": len(campaign.review_title_patterns),
                    "exclude_title_patterns": len(campaign.excluded_title_patterns),
                    "missing_email_status_action": campaign.missing_email_status_action,
                }
            ),
        },
        "copy_skipped": company_qualification_only,
        "copy_angles": 0 if company_qualification_only else len(campaign.angles),
        "copy_templates": 0 if company_qualification_only else template_count,
        "offer_line_variants": (
            0 if company_qualification_only else len(campaign.offer_line_variants)
        ),
        "cta_variants": 0 if company_qualification_only else len(campaign.cta_variants),
        "banned_phrases": 0 if company_qualification_only else len(campaign.banned_phrases),
        "quality_gate": None if company_qualification_only else campaign.data["quality"],
        "firecrawl": {
            "enabled": firecrawl_fallback,
            "api_url": firecrawl_api_url if firecrawl_fallback else "",
            "api_key_configured": bool(os.environ.get("FIRECRAWL_API_KEY", "").strip()),
        },
        "llm_focus": llm_payload,
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
        if args.prune_cache:
            removed = JsonCache(
                Path(args.cache_dir).expanduser().resolve()
            ).prune(ttl_hours=args.cache_ttl_hours)
            print(f"pruned {removed} expired cache entries", file=sys.stderr)
        if args.digest_only and (args.render_only or args.smartlead_output):
            raise ValueError("digest-only cannot render or export Smartlead copy")
        if args.digest_only:
            manifest = run_digests(
                input_path=args.input,
                output_path=args.output,
                options=RunOptions(
                    cache_dir=Path(args.cache_dir).expanduser().resolve(),
                    concurrency=args.concurrency,
                    timeout=args.timeout,
                    retries=args.retries,
                    max_pages=args.max_pages,
                    max_response_bytes=args.max_response_bytes,
                    cache_ttl_hours=args.cache_ttl_hours,
                    refresh_cache=args.refresh_cache,
                    manifest_path=(
                        Path(args.manifest).expanduser().resolve() if args.manifest else None
                    ),
                ),
                progress=None if args.quiet else (
                    lambda completed, total, domain, status: print(
                        f"domains {completed}/{total}: {domain} [{status}]",
                        file=sys.stderr,
                        flush=True,
                    )
                    if completed == 1 or completed == total or completed % 25 == 0
                    else None
                ),
            )
            print(json.dumps(manifest, indent=2, sort_keys=True))
            return 0
        if not args.campaign:
            raise ValueError("--campaign is required unless --digest-only is used")
        campaign = load_campaign(args.campaign)
        if args.render_only:
            if args.validate_only or args.company_qualification_only or args.digest_only or args.focus_rules:
                raise ValueError('--render-only cannot combine with other modes or --focus-rules')
            if campaign.status == 'test_only' and not args.allow_test_campaign:
                raise ValueError('test_only campaign requires --allow-test-campaign')
            from bulk_enrich.delivery import run_render_only
            result = run_render_only(input_path=args.input, output_path=args.output, campaign=campaign,
                options=RunOptions(cache_dir=Path(args.cache_dir),
                    smartlead_output_path=Path(args.smartlead_output) if args.smartlead_output else None,
                    ready_output_path=Path(args.ready_output) if args.ready_output else None,
                    review_output_path=Path(args.review_output) if args.review_output else None,
                    manifest_path=Path(args.manifest) if args.manifest else None))
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.smartlead_output and args.company_qualification_only:
            raise ValueError('--smartlead-output requires contact enrichment')

        if args.validate_only:
            payload = _validation_payload(
                args.input,
                args.output,
                args.campaign,
                args.title_hooks,
                args.focus_rules,
                allow_test_campaign=args.allow_test_campaign,
                company_qualification_only=args.company_qualification_only,
                firecrawl_fallback=args.firecrawl_fallback,
                firecrawl_api_url=args.firecrawl_api_url,
            )
        else:
            if campaign.status == "test_only" and not args.allow_test_campaign:
                raise ValueError(
                    "campaign is marked test_only; change status to approved after review "
                    "or pass --allow-test-campaign for a controlled test"
                )
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

            def log(message: str) -> None:
                if not args.quiet:
                    print(message, file=sys.stderr, flush=True)

            if campaign.llm_focus is not None:
                ready, blocker = provider_ready(campaign.llm_focus, dict(os.environ))
                if not ready:
                    raise LlmFocusError(
                        "campaign enables personalization.llm_focus but its provider "
                        f"'{campaign.llm_focus.provider}' cannot run: {blocker}"
                    )

            options = RunOptions(
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
                llm_mode=args.llm_mode,
                llm_concurrency=args.llm_concurrency,
                llm_poll_seconds=args.llm_poll_seconds,
                llm_cache_ttl_hours=args.llm_cache_ttl_hours,
                llm_batch_ids=tuple(args.llm_batch_id),
                llm_budget_usd=args.llm_budget_usd,
            )
            if args.smartlead_output:
                from dataclasses import replace
                options = replace(options, smartlead_output_path=Path(args.smartlead_output).expanduser().resolve())
            if args.company_qualification_only:
                manifest = run_company_qualification(
                    input_path=args.input,
                    output_path=args.output,
                    campaign=campaign,
                    commercial_focuses=focuses,
                    options=options,
                    progress=progress,
                    log=log,
                )
            else:
                hooks = TitleHookTable.load(args.title_hooks)
                manifest = run_enrichment(
                    input_path=args.input,
                    output_path=args.output,
                    campaign=campaign,
                    title_hooks=hooks,
                    commercial_focuses=focuses,
                    options=options,
                    progress=progress,
                    log=log,
                )
            payload = {
                "delivery": manifest.get("delivery"),
                "status": "completed",
                "mode": manifest["mode"],
                "campaign_id": campaign.campaign_id,
                "output": manifest["output"],
                "domains": manifest["domains"],
                "http": manifest["http"],
                "firecrawl": manifest["firecrawl"],
                "llm_focus": manifest["llm_focus"],
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
