# Campaign and output reference

## Campaign JSON

Each campaign defines:

- `status`: keep `test_only` until the copy is approved;
- `sender`: sender name used in the final email;
- `offer`: service, audience, fallback risk reversal, offer-line variants, fallback CTA, CTA variants, approved claims, and forbidden claims;
- `qualification`: company fallback corroboration, contact title/seniority rules, and email-status policy;
- `personalization.objective`: human-readable campaign intent;
- `personalization.focus_rules_file`: campaign-relative path to the market-specific mapping CSV;
- `personalization.banned_phrases`: phrases rejected in configured or rendered copy;
- `personalization.blocked_evidence_phrases`: optional site-specific junk sentences that must never become company evidence (checked case-insensitively against candidate evidence before selection);
- `personalization.angles`: signal types mapped to approved subject/pitch pairs;
- `personalization.min_confidence`: threshold for a `ready` row;
- `personalization.low_confidence_action`: render for `review` or leave `blank`;
- `personalization.row_fallback`: ordered input CSV headers and confidence scores used only when first-party website evidence is unavailable;
- `quality`: subject, pitch, offer-line, CTA, focus, source-overlap, full-email, and batch-repetition gates;
- `email`: subject and complete body template;
- `output.append_fields`: required audit and upload columns.

Files under `campaigns/examples/` are tests. Put client-specific files in `campaigns/local/`; the folder is excluded from Git.

## Merge fields

Use double braces for engine fields, including:

- `{{first_name}}`, `{{company_name}}`, and `{{job_title}}`;
- `{{company_observation}}`, `{{company_source_focus}}`, `{{company_focus}}`, `{{company_focus_sentence}}`, `{{buyer_phrase}}`, `{{company_evidence}}`, and `{{personalization_source}}`;
- `{{company_short_name}}`, `{{title_hook}}`, and `{{persona}}`;
- `{{personalized_pitch}}`, `{{risk_reversal}}`, `{{cta}}`, and `{{sender_name}}`.

Smartlead spintax such as `{Hi|Hello}` remains unchanged.

Every pitch template must contain `{{company_focus}}` or `{{buyer_phrase}}`. Signal type selects the angle first; a stable domain hash selects among that angle's approved subject/pitch pairs. Include one `*` fallback angle. `{{title_hook}}` is optional when role language materially improves relevance.

The CSV declared by `personalization.focus_rules_file` maps evidence to a short category, buyer phrase, and `core`, `secondary`, or `exclude` fit tier. Core first-party evidence can qualify, secondary evidence requires review, and exclusion or unmatched evidence cannot enter the ready file.

`personalization.row_fallback.fields` is an ordered list of `{header, confidence}` objects. Missing columns are ignored. CSV fields are considered only when first-party website evidence is unavailable. At least the configured number of approved fields must match the same focus rule; the result remains review-only. A selected fallback records `input:<header>` as its source and retains the original cell as evidence.

`offer.cta_variants` contains approved `{id, text}` pairs. The engine orders unique domains by a stable hash and assigns variants round-robin, independently from the pitch template. This keeps an identical batch reproducible while preventing one CTA from dominating. A variant can declare `focus_rules`; exact matches take precedence over the required `*` fallback. If the array is absent or empty, the engine uses `offer.cta`. The audit CSV records both the selected ID and rendered CTA.

`offer.risk_reversal_variants` follows the same deterministic distribution. Keep each line short and natural, vary the sentence structure rather than swapping synonyms, and describe only the approved commercial model. A variant can declare `focus_rules` to target one or more campaign focus-rule IDs. Exact matches take precedence over the required `*` fallback. If the array is absent or empty, the engine uses `offer.risk_reversal`. The audit CSV records the selected ID and exact rendered line. Use `personalization_offer_variant` as the script-test cohort, and use the manifest's `script_test.cohort_counts` to verify the distribution.

## Commands

Validation:

```bash
python scripts/enrich.py --input LEADS.csv --output OUTPUT.csv --campaign CAMPAIGN.json --validate-only
```

Controlled test:

```bash
python scripts/enrich.py --input TEST.csv --output OUTPUT.csv --campaign CAMPAIGN.json --allow-test-campaign
```

Production:

```bash
python scripts/enrich.py --input LEADS.csv --output AUDIT.csv --ready-output SMARTLEAD-READY.csv --review-output REVIEW.csv --campaign APPROVED.json --concurrency 24
```

Optional Firecrawl fallback for failed or weak pages:

```bash
python scripts/enrich.py --input LEADS.csv --output AUDIT.csv --campaign APPROVED.json --firecrawl-fallback
```

The repository's ignored `.env` can set `FIRECRAWL_API_URL=http://localhost:3002`; shell variables take precedence. For hosted Firecrawl, leave the local URL unset and configure `FIRECRAWL_API_KEY` in the shell environment. Firecrawl is never called for a page whose direct extraction is already strong. Its local cache and manifest counters are separate from direct HTTP.

## Output review

The output keeps original rows and columns. Audit each line through company, contact, email, copy, and final outreach decisions. Only `outreach_status=ready` rows enter the ready output; review rows retain copy, while excluded rows keep evidence and reasons but blank send copy. The manifest records content-addressed, immutable snapshots and hashes of the campaign and focus rules.

The manifest's `focus_gaps` object lists unmatched or excluded-tier domains with up to 25 evidence samples. Use it after each test run to decide which focus rules to add and which junk sentences to block. Batch-quality warnings report `count` (all domains sharing the value) and `flagged` (the deterministic over-cap overflow demoted to review); rows within the cap stay ready. Pass `--prune-cache` to delete cache entries older than `--cache-ttl-hours` before a run.

Company-contact sequencing ranks eligible contacts within each normalized company domain using qualification status, campaign title priority, configured seniority order, and original input order. Only rank 1 can remain ready. Later ranks retain their personalised copy but move to review with an explicit wave reason.

`qualification.contact.priority_title_patterns` is an ordered list of campaign-specific regular expressions used before provider seniority when selecting rank 1. Put the most commercially relevant decision-maker pattern first.
