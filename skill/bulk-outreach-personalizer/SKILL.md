---
name: bulk-outreach-personalizer
description: Validate and run deterministic bulk public-website enrichment for Smartlead or ListKit CSVs using offer-agnostic campaign JSON. Use for high-volume outreach personalization without per-row LLM calls, campaign configuration, cached enrichment runs, or import-ready output review.
---

# Bulk Outreach Personalizer

Use the repository CLI. Do not write one-off per-row prompts or dispatch an agent for each lead.

## Workflow

1. Confirm the input CSV, output path, and client campaign JSON.
2. For a new client, copy both `campaigns/campaign-template.json` and `campaigns/campaign-template-focus.csv` into `campaigns/local/`. Rename both, point `personalization.focus_rules_file` at the adjacent CSV, and add only client-approved offer claims, restrictions, sender, CTA variants, copy, and target-market mappings.
3. Run `--validate-only` and report row count, missing required values, unique domains, duplicate-domain savings, and campaign status.
4. Run a small test with `--allow-test-campaign`; compare the source focus with the compressed focus and buyer phrase, then inspect the selected CTA variant, rule, angle, template, evidence, source, confidence, quality flags, and final emails.
5. Do not mark a campaign `approved` without the user's copy approval.
6. Run the full list, then report the output CSV, manifest, and counts for `ready`, `review`, `blank`, and `error`.
7. Treat only `ready` rows as upload-ready unless the user explicitly reviews the others.

When configured, the engine uses input-CSV company intelligence only after website facts fail to map safely. Keep `personalization.row_fallback.fields` generic and campaign-approved, assign lower confidence than first-party website evidence, and let the campaign focus CSV convert those fields into market-specific buyer language. The audit source must remain `input:<header>` so website and supplied-data evidence are never confused.

Keep direct HTTP as the default. When Firecrawl is configured, enable `--firecrawl-fallback` only as a second pass for failed or weak pages. The CLI reads a self-hosted `FIRECRAWL_API_URL` from the repository's ignored `.env` file; shell variables take precedence. Hosted use reads `FIRECRAWL_API_KEY` from the shell environment. Never request, print, persist, or place API keys in campaign files or CLI arguments. Report the direct and Firecrawl request/cache counts separately from the manifest.

The engine accesses public websites only, blocks local/private network targets, caches per domain, preserves input row order, and never sends campaigns. It can also consume explicitly configured company-description fields already present in the supplied CSV. It keeps evidence for audit but uses one short commercial category in the email. It does not treat random synonym changes as personalization.

Do not add niche or client logic to Python. M&A, CFO, recruitment, and other market terms belong only in the campaign's declared focus-rule CSV. Universal engine checks may reject vague, copied, incomplete, stacked, or semantically awkward language without naming a niche.

Read `references/usage.md` when creating a campaign or interpreting output fields.
Read `references/copy-quality.md` when writing or reviewing campaign copy.
