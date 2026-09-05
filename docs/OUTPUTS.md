# Output contract

The CLI generates email 1 and, when the campaign configures `sequence`, all four follow-up alternatives. `--smartlead-output` requires every exported message to pass automated checks; editorial review and platform preview remain separate. See `skill/bulk-outreach-personalizer/references/sequences.md` for the complete workflow.

The CLI preserves every input column and row order, then appends the campaign's configured output fields.

## Digest-only output

With `--digest-only` and no campaign, the CLI appends `company_enrichment_status`, `company_enrichment_error`, `company_page_source`, and `company_page_digest` (up to 1,500 characters of fetched title, description, headings, and paragraphs). Use it to read a sample of a list before writing the target description.

## Company-qualification-only output

With `--company-qualification-only`, the CLI appends only company evidence and decisions. It does not append or calculate contact, email, sequencing, personalisation, or outreach fields.

| Field | Purpose |
| --- | --- |
| `company_qualification_status` | User-facing decision: `fit`, `needs_review`, or `not_fit` |
| `company_fit_status` | Engine decision: `qualified`, `review`, or `excluded` |
| `company_fit_tier` | Selected `core`, `secondary`, `exclude`, or `none` tier |
| `company_fit_rule` | Campaign focus rule responsible for the decision, or `llm-focus` for a model decision |
| `company_fit_source` | First-party URL or approved `input:<header>` evidence source |
| `company_fit_evidence` | Evidence used for company qualification |
| `company_fit_confidence` | Deterministic evidence score from `0.00` to `1.00` |
| `company_fit_reason` | Deterministic explanation of the decision |
| `company_enrichment_status` | Website extraction result, or `missing_domain` |
| `company_enrichment_error` | Technical website error, when present |

In this mode, `--ready-output` contains `fit` rows and `--review-output` contains `needs_review` rows. `not_fit` rows remain in the complete audit. Website failures and missing domain values are routed to `needs_review`; a readable site that is explicitly excluded or does not match the approved focus rules is `not_fit`.

## Full outreach-personalisation output

| Field | Purpose |
| --- | --- |
| `personalized_subject` | Rendered subject line |
| `personalized_pitch` | Short company-specific offer bridge |
| `personalized_email` | Complete Smartlead/ListKit-ready email |
| `personalization_angle` | Signal-routed campaign angle ID |
| `personalization_template` | Exact approved template ID used, or `llm-pitch` when the model wrote the opening |
| `personalization_signal_type` | `product`, `service`, `audience`, `specialism`, `positioning`, or broad-campaign `title` fallback |
| `personalization_source_focus` | Original normalized website phrase retained for audit |
| `personalization_focus` | One compressed commercial category safe for copy |
| `personalization_buyer_phrase` | Natural buyer description inserted into lead-generation copy |
| `personalization_focus_rule` | Mapping rule ID, or `generic-compression` |
| `personalization_cta_variant` | Stable campaign-approved CTA variant ID |
| `personalization_cta` | Exact CTA rendered in the final email |
| `personalization_offer_variant` | Stable campaign-approved offer-line variant ID and script-test cohort |
| `personalization_offer_line` | Exact offer line rendered in the final email |
| `personalization_facts` | Compact JSON array of up to three source-backed facts |
| `personalization_source` | Public URL or `input:<header>` used for the company signal |
| `personalization_evidence` | Factual text extracted from that URL or supplied CSV field |
| `personalization_confidence` | Deterministic score from `0.00` to `1.00` |
| `personalization_quality_flags` | Batch repetition warnings or other copy-QA flags |
| `personalization_status` | `ready`, `review`, `blank`, or `error` |
| `personalization_error` | Missing data, weak evidence, fetch, or render issue |
| `company_fit_status` | `qualified`, `review`, or `excluded` company decision |
| `company_fit_tier` | Selected `core`, `secondary`, `exclude`, `fallback`, or `none` tier |
| `company_fit_rule` | Campaign rule responsible for the company decision |
| `company_fit_source` | First-party URL or approved `input:<header>` evidence source |
| `company_fit_evidence` | Evidence used for company qualification |
| `company_fit_reason` | Deterministic explanation of the company decision; with `llm_focus` enabled it also carries the model's one-line reason, or why its answer was rejected |
| `contact_fit_status` | Contact-title and seniority decision |
| `contact_fit_rule` | Matching title/seniority rule |
| `contact_fit_reason` | Deterministic explanation of the contact decision |
| `email_fit_status` | Email syntax/provider-status decision |
| `email_fit_rule` | Matching email policy rule |
| `email_fit_reason` | Deterministic explanation of the email decision |
| `company_contact_status` | `primary`, `later-wave`, or `not-eligible` sequencing decision |
| `company_contact_rank` | Deterministic contact rank within the company domain |
| `company_contact_count` | Number of eligible contacts found for the company domain |
| `company_contact_reason` | Explanation of the company-contact sequencing decision |
| `outreach_status` | Final `ready`, `review`, `excluded`, or `error` decision |
| `outreach_reason` | Combined explanation for the final decision |

## Status rules

- `ready`: company, contact, email, and copy all passed their campaign gates.
- `review`: no gate failed, but at least one gate, copy check, or company-contact sequencing rule requires human review.
- `excluded`: at least one company, contact, email, or duplicate gate failed; send copy is blank.
- `error`: technical rendering failed after the qualification gates passed.

Model openings mentioning offer mechanics require `review`, with the reason in `personalization_error`. Unapproved numeric/commercial promises are removed and approved fallback copy is rendered. Style-only failures may still become ready through fallback; slot mode ignores returned model pitches. Company, contact, email, and duplicate exclusions still take precedence.

Model quotes are rejected if under four words, blocked by campaign phrases, navigation-only, or common cookie notices. Rejection falls back to regex rules and does not establish semantic relevance of remaining quotes.

Only `ready` rows should be uploaded without review.

Only one qualified contact per company domain can be `ready` in a single run. Additional eligible contacts retain their copy, receive a deterministic rank, and move to `review` for later outreach waves.

Pass `--ready-output /path/to/smartlead-ready.csv` for upload-safe rows and `--review-output /path/to/manual-review.csv` for reviewable rendered rows while retaining the complete audit CSV.

## Run manifest

Every output receives a JSON manifest containing:

- input, campaign, focus-rule table, hook-table, and output SHA-256 hashes;
- start time, finish time, and duration;
- row and unique-domain counts;
- duplicate requests avoided;
- page and signal cache usage;
- status counts and fetch statistics;
- company, contact, email, final-status, duplicate-email, and rule-usage counts;
- company-contact group, multi-contact company, and later-wave counts;
- script-test cohort counts across unique rendered companies;
- copy-quality evaluation, warning counts, and flagged-row counts;
- opening, exact-pitch, buyer-phrase, offer-line, and CTA concentration warnings, each reporting `count` (all affected domains) and `flagged` (the deterministic over-cap overflow demoted to review);
- a `focus_gaps` object with separate unmatched and intentionally excluded domain counts, plus up to 25 evidence samples for each group;
- an `llm_focus` object: `enabled`, and when enabled the provider, model, effort, domains per call, CLI call count, `retry_count`, prompt version, brief digest, batch IDs, requested/sent/cache-hit counts, ok/rejected/error counts, pitches written and rejected, input, output, and cache-read token totals, `nominal_cost_usd`, `budget_usd`, and `budget_exhausted`;
- the exact non-secret run settings.
- immutable copies and hashes of the campaign JSON and focus CSV used for the run.

Completed model chunks are checkpointed before the next wave. A fatal interruption does not publish new final CSVs or a manifest; existing output files may belong to an earlier run. Rerun unchanged inputs to resume from saved decisions. Cached decisions contribute no new tokens or nominal cost; in-flight calls lost during termination may need repeating.

Cache hits require identical rendered model prompts and validation inputs. The manifest brief digest is a summary, not the complete cache identity; prompt or claim edits can trigger fresh calls. Legacy partial-brief model entries are not reused.

Generated CSVs belong under `outputs/`; cache data belongs under `var/`. Both are excluded from Git because they can contain prospect or client information.

A persistent subscription rate limit stops the run before new output CSVs or a manifest are published. Existing outputs may be from an older run. Retry delays are bounded to 5, 15, and 30 seconds; completed decisions remain cached. Retry tokens and nominal costs are counted where reported by the CLI.

Codex live execution is untested as of 2026-09-04 because the installed CLI cannot start. Its current parser returns zero token fields, which means unavailable telemetry, not a measured free run. Do not use those zeros to validate a Codex budget. The failed local startup check made no model calls.

URL-only page digests are not sent for model classification. They contain no readable company evidence; existing fallback and qualification gates still apply.

Assistant-led upload recovery can produce a separate import and disposition ledger; it does not change the original CLI audit statuses. Keep original-ready, source-list personalisation, neutral-offer recovery and held exceptions distinguishable. See `skill/bulk-outreach-personalizer/references/recovery.md`.

## Optional complete sequences

Campaigns may configure `sequence.followups` with `followup_2a`, `followup_2b`,
`followup_3a`, and `followup_3b`, plus optional `neutral_followups` with the same
keys. Templates contain body copy only: the renderer appends the sender and one
`sequence.ps_variants` entry. A/B variants are alternatives within each step.
`sequence.greeting` defaults to `inline`; `paragraph` preserves the original
first-email layout. Campaigns without `sequence` retain their previous output.
`max_followup_words` defaults to 55 including the signature and P.S.; first-email
limits still use `quality.max_body_words`, also including the P.S.

Exact `editorial_replacements` map original service/buyer phrases to objects
containing `text` and `reason`. `company_name_overrides` uses company domains as
keys and the same objects. These affect rendered copy only, never evidence or
source fields. Unknown placeholders, empty required slots without an approved
neutral template, duplicate signatures/P.S., banned wording and unapproved
quantities/promises reject the sequence. Quantitative follow-up claims must
match approved claim sentences or the selected approved CTA/offer sentence.

### Complete delivery and offline revisions

`--smartlead-output outputs/<campaign>/smartlead.csv` adds a compact UTF-8 CSV:
`email`, `first_name`, `company_name`, `personalized_subject`, `personalized_email`,
and (when sequence is configured) the four follow-up columns. Every configured
message must pass before inclusion. Failed sequence copy is review, not proof
of poor company fit. Source audit columns are retained unchanged.

Companion files use the requested upload stem: `.held.csv`, `.disposition.csv`,
`.previews.md`, `.copy-review.csv`, `.mapping.md`, `.manifest.json`. Every input
row has a disposition. The review queue covers distinct service/buyer/template
combinations; previews cover templates and evidence modes. Manifests separate
successful automated checks from pending editorial review and unverified
platform rendering. No file claims that messages were uploaded or sent.

New enrichment audits also have `<audit>.render-state.json`. Preserve this
sidecar with its audit. `--render-only --input audit.csv --output revised.csv`
rebuilds copy using saved contexts and qualification, with no provider preflight,
HTTP requests or model calls. It supports presentation, approved template,
CTA/offer-line, sequence and output-limit changes. Changed targeting, claims,
classification limits, fallback policy, focus rules or title hooks require a new
enrichment run. Modified audits and legacy audits without a sidecar cannot be
replayed. Source evidence remains unchanged; outputs must use distinct paths.
