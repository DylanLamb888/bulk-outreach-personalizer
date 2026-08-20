# Bulk Enrich

Deterministic website enrichment, qualification, and outreach-email personalization for large CSV lead lists. It makes no per-row LLM calls. An optional Firecrawl fallback can render difficult pages without changing the campaign or output contract.

The engine fetches each unique public company domain once, caches the result, extracts auditable company facts, and applies campaign-specific company, contact, and email gates before rendering copy. A row is upload-ready only when all qualification and copy gates pass.

## What is included

- portable Claude Code and Codex Skill entrypoints;
- offer-agnostic campaign JSON files;
- parallel public website fetching;
- optional cached Firecrawl fallback for failed, JavaScript-rendered, or weak pages;
- ordered input-CSV evidence fallback with configurable confidence scores;
- persistent HTTP and extracted-signal caching;
- duplicate-domain reuse;
- private/local-network blocking;
- deterministic multi-fact HTML evidence extraction with safe secondary-fact fallback;
- campaign-scoped website-signal to commercial-focus rules;
- schema-v3 `core`, `secondary`, and `exclude` company-fit tiers;
- campaign-specific title, seniority, and email-status qualification;
- two-field corroboration for CSV-only company evidence;
- deterministic duplicate-email resolution;
- deterministic company-contact ranking with one upload-ready contact per company and later contacts held for review;
- fail-closed rejection of slogans, testimonials, company-name fragments, incomplete clauses, service stacks, years, promotional adjectives, and “& more” language;
- signal-routed subject and pitch templates;
- campaign-approved offer-line and CTA variants selected independently and deterministically;
- focus-rule-specific offer lines for distinct buyer types, with a required campaign fallback;
- focus-rule-specific CTAs with concrete numbered assets where the sender can fulfil them;
- even deterministic offer-line and CTA distribution across each batch;
- conversational greeting and subject-safe company-name cleanup;
- review-only CSV fallbacks used only when first-party evidence is unavailable and two approved fields agree;
- campaign-level banned phrases, word limits, and source-copy overlap limits;
- batch repetition QA for openings, exact pitches, buyer phrases, offer lines, and CTAs across unique domains;
- a hard copy gate that rejects em dashes from configured or rendered outreach;
- editable title-to-hook rules;
- separate copy status and final `ready`, `review`, `excluded`, or `error` outreach status;
- atomic CSV output and a checksummed run manifest;
- immutable campaign and focus-rule snapshots beside every audit output;
- no prospect data committed to Git.

## 1. Install the shared Skill

The repository contains one Skill source that can be discovered by both Codex and Claude Code. Preview the personal Skill links, then install them:

```bash
python scripts/install_skill.py --target both --dry-run
python scripts/install_skill.py --target both
```

This creates symlinks under `~/.codex/skills/` and `~/.claude/skills/`. It is idempotent and refuses to overwrite an existing file, directory, or link that points elsewhere. Both tools therefore use the same tracked Skill files and cannot drift into separate copies.

## 2. Create a campaign

Copy `campaigns/campaign-template.json` and `campaigns/campaign-template-focus.csv` into `campaigns/local/`, rename both, and update `personalization.focus_rules_file` to the adjacent CSV filename. Replace the offer, qualification policy, copy angles, offer-line variants, CTA variants, sender, and market-specific focus rules. Label every focus rule `core`, `secondary`, or `exclude`, and keep the campaign `test_only` until its rules and copy are reviewed.

The Python engine contains no M&A, CFO, recruitment, or client-specific mapping. Those rules live only in the focus CSV declared by each campaign JSON. `--focus-rules` remains available as an explicit one-run override.

The Scale Olympus and Chapman files under `campaigns/examples/` are test examples only.

## 3. Validate the lead file

```bash
python scripts/enrich.py \
  --input /absolute/path/leads.csv \
  --output outputs/campaign-name/enriched.csv \
  --campaign campaigns/local/campaign-name.json \
  --validate-only
```

Validation reports qualification columns, missing verification values, duplicate emails, unique domains, rule tiers, and campaign approval state. It does not access websites or write output.

## 4. Run a controlled test

Use a small CSV first. The flag below is deliberately required while a campaign is marked `test_only`.

```bash
python scripts/enrich.py \
  --input /absolute/path/test-leads.csv \
  --output outputs/campaign-name/test-enriched.csv \
  --campaign campaigns/local/campaign-name.json \
  --allow-test-campaign
```

Review the final email alongside `personalization_source_focus`, `personalization_focus`, `personalization_buyer_phrase`, `personalization_focus_rule`, `personalization_cta_variant`, evidence, source, angle, template, and quality flags. The source focus preserves the selected website or configured input-row fact; the commercial focus and buyer phrase are the shorter language used in the email. Unsafe unmatched facts are left blank instead of being forced into copy. Approve the campaign only after the copy looks right.

## 5. Run the full list

```bash
python scripts/enrich.py \
  --input /absolute/path/leads.csv \
  --output outputs/campaign-name/enriched.csv \
  --ready-output outputs/campaign-name/smartlead-ready.csv \
  --review-output outputs/campaign-name/manual-review.csv \
  --campaign campaigns/local/campaign-name.json \
  --concurrency 24
```

The audit output preserves every original row and appends evidence, copy, gate decisions, and reasons. `--ready-output` contains only final `outreach_status=ready` rows; `--review-output` contains reviewable rows with rendered copy. Excluded rows remain auditable but their send copy is blank. The manifest and exact campaign/focus snapshots are written beside the audit CSV.

The default cache lives under `var/cache/` for seven days. Re-running the same domains uses the extracted-signal cache and normally makes no HTTP requests. Use `--refresh-cache` only when fresh website evidence is required.

## Optional Firecrawl fallback

Direct HTTP remains the fast first pass. Enable Firecrawl only for pages that fail or do not produce a strong usable signal:

```bash
python scripts/enrich.py \
  --input /absolute/path/leads.csv \
  --output outputs/campaign-name/enriched.csv \
  --campaign campaigns/local/campaign-name.json \
  --firecrawl-fallback
```

The CLI automatically reads supported Firecrawl settings from the repository's ignored `.env` file. This checkout is connected to the local service with `FIRECRAWL_API_URL=http://localhost:3002`; `.env.example` is the safe template. Shell variables still take precedence. For hosted Firecrawl, remove the local URL and set `FIRECRAWL_API_KEY` in the shell environment. Never put the key in a campaign JSON, CSV, command argument, `.env`, or tracked file. `--firecrawl-api-url`, `--firecrawl-timeout`, and `--firecrawl-concurrency` can override the endpoint, timeout, and fallback request limit for one run. Firecrawl concurrency defaults to 4 even when direct HTTP uses 24 workers, preventing a burst of browser-rendered requests from overwhelming a self-hosted service.

Firecrawl results have their own successful and negative local caches. The manifest reports direct HTTP and Firecrawl requests separately, so it is clear how often the slower fallback was needed. Self-hosted Firecrawl's browser service should be isolated behind a secure proxy that blocks private and link-local destinations.

## Quality gate

Upload only rows with `outreach_status=ready`. Inspect `review` rows before use. Excluded rows intentionally contain no send-ready personalization. When several eligible contacts share a company domain, only the strongest contact remains ready and later contacts are ranked for later waves. For batches above the configured minimum size, repeated openings, exact pitches, offer lines, or CTAs can move affected rows to review; buyer-phrase concentration can be disabled for a deliberately narrow segment.

```bash
env PYTHONPATH=src python3 -B -m unittest discover -s tests -v
```

See [docs/OUTPUTS.md](docs/OUTPUTS.md) for the output contract and [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) for current boundaries.
