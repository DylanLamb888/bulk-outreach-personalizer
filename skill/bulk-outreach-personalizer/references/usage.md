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
- `personalization.fallback_copy`: optional campaign-approved title fallback for broad outreach campaigns;
- `personalization.llm_focus`: optional model classification and pitch writing for each unique domain with `enabled`, `provider` (`claude-code` by default, `codex`, or `api`), `domains_per_call`, plain-English `icp` and `exclusions`, `model`, `effort`, `write_pitch`, `max_pitch_words`, `max_nominal_usd`, `allow_expensive_models`, `max_evidence_chars`, and up to 12 `examples` of `{site, fit_tier, focus, buyer_phrase}`;
- `quality`: subject, pitch, offer-line, CTA, focus, source-overlap, full-email, and batch-repetition gates;
- `email`: subject and complete body template;
- `output.append_fields`: required audit and upload columns.

Files under `campaigns/examples/` are tests. Put client-specific files in `campaigns/local/`; the folder is excluded from Git.

The input must contain email, first name, and company name columns. A company domain or website is strongly recommended for first-party personalisation, but it may be absent when an approved broad campaign enables title fallback. Strict campaigns will exclude rows without company evidence. Job title is required by the contact gate; email-verification status is optional only when the campaign permits syntax validation or review.

Exception: `--company-qualification-only` accepts a company-domain or company-website column without lead/contact fields. It preserves any company name or LinkedIn URL columns supplied, but it does not use LinkedIn as evidence or scrape it.

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

`personalization.fallback_copy` is disabled by default. When enabled, it can render approved persona-based templates for unmatched companies, accept one mapped CSV company field, or promote review-level company evidence. `allow_explicit_company_exclusions` is a separate opt-in for broad campaigns and must remain false when the campaign has genuine off-target company types. Title fallback records `personalization_signal_type=title`, `personalization_focus_rule=title-fallback`, `company_fit_tier=fallback`, and `personalization_source=input:Job title`. Contact and email qualification still apply. When no domain value exists, the engine uses a hashed company-name identity for deterministic variation, batch QA, and one-contact-per-company sequencing; it never treats that identity as website evidence.

`personalization.llm_focus` runs once per readable unique domain, before regex matching, using the fetched title, description, headings, and paragraphs, including the redirected page when a domain moved to another company domain. The default `claude-code` provider runs the local Claude Code CLI headlessly on the operator's subscription login with all tools, MCP servers, hooks, and project files disabled, packing `domains_per_call` companies into each call; `codex` does the same through the Codex CLI; `api` uses the Anthropic SDK with a key. The model returns `fit_tier`, `signal_type`, `focus`, `buyer_phrase`, a verbatim `evidence` quote, a personalised `pitch`, `reason`, and `confidence` under a fixed JSON schema. The engine rejects any answer whose quote is not in the fetched text or whose phrases fail the standard gates, then falls back to the focus CSV. Accepted decisions record `company_fit_rule=llm-focus`, the quote as evidence, and the model's reason inside `company_fit_reason`. When `write_pitch` is on and the pitch passes its gates (word limit, no research openers, no company name, no unapproved figures, no more than `quality.max_source_phrase_words` consecutive words from the site, no banned phrases or dashes), the row renders with `personalization_template=llm-pitch` and the pitch as the opening; otherwise it renders from the approved templates and `personalization_error` records `model pitch rejected`. `quality.max_body_words` must leave room for `max_pitch_words` plus the offer line and CTA. Fable and Mythos models require `allow_expensive_models`. Each run stops new calls at `max_nominal_usd` and the manifest reports `nominal_cost_usd`. Decisions are cached by domain, provider, the full rendered system/company prompts, and validation inputs. Changes to offer, claims, contact context, evidence, prompt text, or copy limits require fresh calls. Old partial-brief cache entries are not reused. `--llm-mode batch` and `--llm-batch-id` apply to the `api` provider only.

`offer.cta_variants` contains approved `{id, text}` pairs. The engine orders unique domains by a stable hash and assigns variants round-robin, independently from the pitch template. This keeps an identical batch reproducible while preventing one CTA from dominating. A variant can declare `focus_rules`; exact matches take precedence over the required `*` fallback. If the array is absent or empty, the engine uses `offer.cta`. The audit CSV records both the selected ID and rendered CTA.

`offer.risk_reversal_variants` follows the same deterministic distribution. Keep each line short and natural, vary the sentence structure rather than swapping synonyms, and describe only the approved commercial model. A variant can declare `focus_rules` to target one or more campaign focus-rule IDs. Exact matches take precedence over the required `*` fallback. If the array is absent or empty, the engine uses `offer.risk_reversal`. The audit CSV records the selected ID and exact rendered line. Use `personalization_offer_variant` as the script-test cohort, and use the manifest's `script_test.cohort_counts` to verify the distribution.

## Commands

Read a sample of the list before describing the target (no campaign, no model):

```bash
python <repository-root>/scripts/enrich.py --input LEADS.csv --output DIGESTS.csv --digest-only
```

Validation:

```bash
python <repository-root>/scripts/enrich.py --input LEADS.csv --output OUTPUT.csv --campaign CAMPAIGN.json --validate-only
```

Company qualification only:

```bash
python <repository-root>/scripts/enrich.py --input COMPANIES.csv --output QUALIFICATION-AUDIT.csv --ready-output FIT.csv --review-output NEEDS-REVIEW.csv --campaign CAMPAIGN.json --company-qualification-only
```

Controlled test:

```bash
python <repository-root>/scripts/enrich.py --input TEST.csv --output OUTPUT.csv --campaign CAMPAIGN.json --allow-test-campaign
```

Production:

```bash
python <repository-root>/scripts/enrich.py --input LEADS.csv --output AUDIT.csv --ready-output SMARTLEAD-READY.csv --review-output REVIEW.csv --campaign APPROVED.json --concurrency 24
```

Production with model classification enabled in the campaign (no extra flags; the provider comes from the campaign):

```bash
python <repository-root>/scripts/enrich.py --input LEADS.csv --output AUDIT.csv --ready-output SMARTLEAD-READY.csv --review-output REVIEW.csv --campaign APPROVED.json --llm-concurrency 2
```

Optional Firecrawl fallback for failed or weak pages:

```bash
python <repository-root>/scripts/enrich.py --input LEADS.csv --output AUDIT.csv --campaign APPROVED.json --firecrawl-fallback
```

The repository's ignored `.env` can set `FIRECRAWL_API_URL=http://localhost:3002`; shell variables take precedence. For hosted Firecrawl, leave the local URL unset and configure `FIRECRAWL_API_KEY` in the shell environment. Firecrawl is never called for a page whose direct extraction is already strong. Its local cache and manifest counters are separate from direct HTTP.

## Output review

For `--company-qualification-only`, review `company_qualification_status`, `company_fit_rule`, `company_fit_source`, `company_fit_evidence`, and `company_fit_reason`. `fit` means core first-party evidence passed; `needs_review` covers secondary evidence, low confidence, unavailable sites, missing domains, and corroborated CSV fallback; `not_fit` means readable evidence matched an exclusion or no approved target rule. No contact, email, personalisation, sequencing, or outreach fields are produced.

The output keeps original rows and columns. Audit each line through company, contact, email, copy, and final outreach decisions. Only `outreach_status=ready` rows enter the ready output; review rows retain copy, while excluded rows keep evidence and reasons but blank send copy. The manifest records content-addressed, immutable snapshots and hashes of the campaign and focus rules.

The manifest's `focus_gaps` object keeps `unmatched_domains`/`unmatched_samples` separate from `excluded_domains`/`excluded_samples`, with up to 25 evidence samples per group. Use unmatched samples to decide which focus rules to add, audit excluded samples for false exclusions, and use the evidence to decide which junk sentences to block. Batch-quality warnings report `count` (all domains sharing the value) and `flagged` (the deterministic over-cap overflow demoted to review); rows within the cap stay ready. Pass `--prune-cache` to delete cache entries older than `--cache-ttl-hours` before a run.

Company-contact sequencing ranks eligible contacts within each normalized company domain using qualification status, campaign title priority, configured seniority order, and original input order. Only rank 1 can remain ready. Later ranks retain their personalised copy but move to review with an explicit wave reason.

`qualification.contact.priority_title_patterns` is an ordered list of campaign-specific regular expressions used before provider seniority when selecting rank 1. Put the most commercially relevant decision-maker pattern first.
