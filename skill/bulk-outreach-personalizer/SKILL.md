---
name: bulk-outreach-personalizer
description: Validate and run deterministic bulk public-website enrichment for Smartlead or ListKit CSVs using offer-agnostic campaign JSON. Use for high-volume outreach personalization without per-row LLM calls, campaign configuration, cached enrichment runs, or import-ready output review.
---

# Bulk Outreach Personalizer

Use the repository CLI. Do not write one-off per-row prompts or dispatch an agent for each lead.

Resolve the repository root as two directories above this `SKILL.md`, including when this Skill is reached through a symlink. Run the deterministic entrypoint at `<repository-root>/scripts/enrich.py`; do not assume the user's current working directory is the repository.

## Workflow

1. Confirm the input CSV, output path, and client campaign JSON.
2. For a new client, inspect 10–20 representative development domains plus adjacent negative examples. Draft the campaign's `core`, `secondary`, and `exclude` company rules, required contact titles/seniorities, accepted email statuses, and approved CSV fallback fields. Show these rules to the user and obtain approval before freezing them. This setup assistance may use Claude or Codex; the bulk CLI must never make per-row AI calls.
3. Copy both `campaigns/campaign-template.json` and `campaigns/campaign-template-focus.csv` into `campaigns/local/`. Rename both, point `personalization.focus_rules_file` at the adjacent CSV, and add only approved qualification rules, offer claims, restrictions, sender, offer-line variants, CTA variants, and copy.
4. Run `--validate-only` and report row count, detected qualification columns, missing verification values, duplicate emails, unique domains, duplicate-domain savings, rule tiers, and campaign status.
5. Run a small test with `--allow-test-campaign`; inspect company, contact, email, copy, and final outreach statuses alongside their rules, evidence, reasons, and final emails. Include unseen holdout and adjacent-negative examples.
6. Review the manifest's `focus_gaps.unmatched_samples` first and add or adjust focus rules for genuinely in-market domains. Audit `focus_gaps.excluded_samples` separately for false exclusions, put site-specific junk sentences in `personalization.blocked_evidence_phrases`, and re-run the test; cached pages make re-runs cheap.
7. Do not mark a campaign `approved` without the user's qualification and copy approval.
8. Run the full list with separate audit, ready, and review outputs. Report `ready`, `review`, `excluded`, and `error` counts plus duplicate exclusions and later-wave company contacts.
9. Treat only `outreach_status=ready` rows as upload-ready. The ready file must contain no more than one contact per company domain; additional eligible contacts belong in review for later waves. Never upload or send automatically.

When configured, the engine uses input-CSV company intelligence only when first-party website evidence is unavailable. A readable website with no campaign match is excluded rather than rescued by CSV enrichment. CSV-only company evidence must map to the same rule in at least two approved fields and remains review-only. The audit source must remain `input:<header>` so website and supplied-data evidence are never confused.

Keep direct HTTP as the default. When Firecrawl is configured, enable `--firecrawl-fallback` only as a second pass for failed or weak pages. The CLI reads a self-hosted `FIRECRAWL_API_URL` from the repository's ignored `.env` file; shell variables take precedence. Hosted use reads `FIRECRAWL_API_KEY` from the shell environment. Never request, print, persist, or place API keys in campaign files or CLI arguments. Report the direct and Firecrawl request/cache counts separately from the manifest.

The engine accesses public websites only, blocks local/private network targets, caches per domain, preserves input row order, and never sends campaigns. It can also consume explicitly configured company-description fields already present in the supplied CSV. It keeps evidence for audit but uses one short commercial category in the email. It does not treat random synonym changes as personalization.

Do not add niche or client logic to Python. M&A, CFO, recruitment, and other market terms belong only in the campaign's declared focus-rule CSV. Universal engine checks may reject vague, copied, incomplete, stacked, or semantically awkward language without naming a niche.

Read `references/usage.md` when creating a campaign or interpreting output fields.
Read `references/copy-quality.md` when writing or reviewing campaign copy.
