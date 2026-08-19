# Campaign and output reference

## Campaign JSON

Each campaign defines:

- `status`: keep `test_only` until the copy is approved;
- `sender`: sender name used in the final email;
- `offer`: service, audience, risk reversal, fallback CTA, CTA variants, approved claims, and forbidden claims;
- `personalization.objective`: human-readable campaign intent;
- `personalization.focus_rules_file`: campaign-relative path to the market-specific mapping CSV;
- `personalization.banned_phrases`: phrases rejected in configured or rendered copy;
- `personalization.angles`: signal types mapped to approved subject/pitch pairs;
- `personalization.min_confidence`: threshold for a `ready` row;
- `personalization.low_confidence_action`: render for `review` or leave `blank`;
- `personalization.row_fallback`: ordered input CSV headers and confidence scores used only when website facts fail to produce safe copy;
- `quality`: subject, pitch, CTA, focus, source-overlap, full-email, and batch-repetition gates;
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

The CSV declared by `personalization.focus_rules_file` maps website or configured input-row evidence to a short category and buyer phrase. Rules are evaluated by numeric priority, can be limited to signal types, and stay isolated to that campaign. Unmatched signals go through conservative deterministic compression. Use `--focus-rules` only when deliberately overriding the campaign file for one run.

`personalization.row_fallback.fields` is an ordered list of `{header, confidence}` objects. Missing columns are ignored. A selected fallback records `input:<header>` as its source, retains the original cell as evidence, and still passes through the same focus, copy-overlap, length, repetition, and confidence gates as website content.

`personalization.max_candidate_confidence_drop` controls how far below the strongest mapped website fact a CSV fallback may be and still compete. Use a small value such as `0.10`: it allows a nearby, more specific description to improve the angle while blocking a weak generated field from displacing stronger public evidence. Website facts from additional pages can still compete with one another.

`offer.cta_variants` contains approved `{id, text}` pairs. The engine orders unique domains by a stable hash and assigns variants round-robin, independently from the pitch template. This keeps an identical batch reproducible while preventing one CTA from dominating. If the array is absent or empty, the engine uses `offer.cta`. The audit CSV records both the selected ID and rendered CTA.

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
python scripts/enrich.py --input LEADS.csv --output AUDIT.csv --ready-output SMARTLEAD-READY.csv --campaign APPROVED.json --concurrency 24
```

Optional Firecrawl fallback for failed or weak pages:

```bash
python scripts/enrich.py --input LEADS.csv --output AUDIT.csv --campaign APPROVED.json --firecrawl-fallback
```

The repository's ignored `.env` can set `FIRECRAWL_API_URL=http://localhost:3002`; shell variables take precedence. For hosted Firecrawl, leave the local URL unset and configure `FIRECRAWL_API_KEY` in the shell environment. Firecrawl is never called for a page whose direct extraction is already strong. Its local cache and manifest counters are separate from direct HTTP.

## Output review

The output keeps original rows and columns. Audit each line through its source focus, compressed focus, buyer phrase, focus rule, CTA variant, signal type, angle, template, facts, source, evidence, confidence, quality flags, status, and errors. Batch repetition is measured once per unique domain. Only `ready` rows are automatically upload-ready. A checksummed manifest beside the CSV records the run without containing full prospect rows.
