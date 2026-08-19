# Bulk Enrich

Deterministic website enrichment and outreach-email personalization for large CSV lead lists. It makes no per-row LLM calls. An optional Firecrawl fallback can render difficult pages without changing the campaign or output contract.

The engine fetches each unique public company domain once, caches the result, extracts auditable company facts, and selects the first fact that maps safely to buyer language. If website evidence fails, it can try campaign-configured company fields already present in the input CSV before routing the result through approved copy angles and writing a Smartlead/ListKit-ready CSV in the original row order.

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
- fail-closed rejection of slogans, testimonials, company-name fragments, incomplete clauses, service stacks, years, promotional adjectives, and “& more” language;
- signal-routed subject and pitch templates;
- campaign-approved CTA variants selected independently and deterministically;
- even deterministic CTA distribution across each batch;
- conversational greeting and subject-safe company-name cleanup;
- confidence-bounded CSV fallbacks that cannot displace materially stronger website evidence;
- campaign-level banned phrases, word limits, and source-copy overlap limits;
- batch repetition QA for openings, exact pitches, buyer phrases, and CTAs across unique domains;
- editable title-to-hook rules;
- confidence-based `ready`, `review`, `blank`, and `error` handling;
- atomic CSV output and a checksummed run manifest;
- no prospect data committed to Git.

## 1. Create a campaign

Copy `campaigns/campaign-template.json` and `campaigns/campaign-template-focus.csv` into `campaigns/local/`, rename both, and update `personalization.focus_rules_file` to the adjacent CSV filename. Replace the offer, approved claims, copy angles, CTA variants, quality thresholds, sender, and market-specific focus rules. Keep the campaign marked `test_only` until the copy has been reviewed.

The Python engine contains no M&A, CFO, recruitment, or client-specific mapping. Those rules live only in the focus CSV declared by each campaign JSON. `--focus-rules` remains available as an explicit one-run override.

The Scale Olympus and Chapman files under `campaigns/examples/` are test examples only.

## 2. Validate the lead file

```bash
python scripts/enrich.py \
  --input /absolute/path/leads.csv \
  --output outputs/campaign-name/enriched.csv \
  --campaign campaigns/local/campaign-name.json \
  --validate-only
```

Validation reports the detected columns, missing values, unique domains, duplicate-domain savings, hook rules, and campaign approval state. It does not access websites or write output.

## 3. Run a controlled test

Use a small CSV first. The flag below is deliberately required while a campaign is marked `test_only`.

```bash
python scripts/enrich.py \
  --input /absolute/path/test-leads.csv \
  --output outputs/campaign-name/test-enriched.csv \
  --campaign campaigns/local/campaign-name.json \
  --allow-test-campaign
```

Review the final email alongside `personalization_source_focus`, `personalization_focus`, `personalization_buyer_phrase`, `personalization_focus_rule`, `personalization_cta_variant`, evidence, source, angle, template, and quality flags. The source focus preserves the selected website or configured input-row fact; the commercial focus and buyer phrase are the shorter language used in the email. Unsafe unmatched facts are left blank instead of being forced into copy. Approve the campaign only after the copy looks right.

## 4. Run the full list

```bash
python scripts/enrich.py \
  --input /absolute/path/leads.csv \
  --output outputs/campaign-name/enriched.csv \
  --ready-output outputs/campaign-name/smartlead-ready.csv \
  --campaign campaigns/local/campaign-name.json \
  --concurrency 24
```

The audit output preserves every original column and appends the personalized subject, pitch, selected CTA, full email, source and compressed focus, buyer phrase, mapping rule, routed angle, template, structured facts, evidence, confidence, quality flags, status, and errors. `--ready-output` creates a second CSV containing only rows marked `ready`. A manifest is written beside the audit CSV as `<output>.manifest.json`.

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

Upload only rows marked `ready`. Inspect `review` rows before use. `blank` and `error` rows intentionally contain no send-ready personalization. For batches above the configured minimum size, repeated openings or exact pitches can automatically move affected rows to `review`.

```bash
env PYTHONPATH=src python3 -B -m unittest discover -s tests -v
```

See [docs/OUTPUTS.md](docs/OUTPUTS.md) for the output contract and [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) for current boundaries.
