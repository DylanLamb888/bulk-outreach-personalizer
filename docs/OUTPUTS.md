# Output contract

The CLI preserves every input column and row order, then appends the campaign's configured output fields.

| Field | Purpose |
| --- | --- |
| `personalized_subject` | Rendered subject line |
| `personalized_pitch` | Short company-specific offer bridge |
| `personalized_email` | Complete Smartlead/ListKit-ready email |
| `personalization_angle` | Signal-routed campaign angle ID |
| `personalization_template` | Exact approved template ID used |
| `personalization_signal_type` | `product`, `service`, `audience`, `specialism`, or `positioning` |
| `personalization_source_focus` | Original normalized website phrase retained for audit |
| `personalization_focus` | One compressed commercial category safe for copy |
| `personalization_buyer_phrase` | Natural buyer description inserted into lead-generation copy |
| `personalization_focus_rule` | Mapping rule ID, or `generic-compression` |
| `personalization_cta_variant` | Stable campaign-approved CTA variant ID |
| `personalization_cta` | Exact CTA rendered in the final email |
| `personalization_facts` | Compact JSON array of up to three source-backed facts |
| `personalization_source` | Public URL or `input:<header>` used for the company signal |
| `personalization_evidence` | Factual text extracted from that URL or supplied CSV field |
| `personalization_confidence` | Deterministic score from `0.00` to `1.00` |
| `personalization_quality_flags` | Batch repetition warnings or other copy-QA flags |
| `personalization_status` | `ready`, `review`, `blank`, or `error` |
| `personalization_error` | Missing data, weak evidence, fetch, or render issue |
| `company_fit_status` | `qualified`, `review`, or `excluded` company decision |
| `company_fit_tier` | Selected `core`, `secondary`, `exclude`, or `none` tier |
| `company_fit_rule` | Campaign rule responsible for the company decision |
| `company_fit_source` | First-party URL or approved `input:<header>` evidence source |
| `company_fit_evidence` | Evidence used for company qualification |
| `company_fit_reason` | Deterministic explanation of the company decision |
| `contact_fit_status` | Contact-title and seniority decision |
| `contact_fit_rule` | Matching title/seniority rule |
| `contact_fit_reason` | Deterministic explanation of the contact decision |
| `email_fit_status` | Email syntax/provider-status decision |
| `email_fit_rule` | Matching email policy rule |
| `email_fit_reason` | Deterministic explanation of the email decision |
| `outreach_status` | Final `ready`, `review`, `excluded`, or `error` decision |
| `outreach_reason` | Combined explanation for the final decision |

## Status rules

- `ready`: company, contact, email, and copy all passed their campaign gates.
- `review`: no gate failed, but at least one gate or copy check requires human review.
- `excluded`: at least one company, contact, email, or duplicate gate failed; send copy is blank.
- `error`: technical rendering failed after the qualification gates passed.

Only `ready` rows should be uploaded without review.

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
- copy-quality evaluation, warning counts, and flagged-row counts;
- opening, exact-pitch, buyer-phrase, and CTA concentration warnings;
- the exact non-secret run settings.
- immutable copies and hashes of the campaign JSON and focus CSV used for the run.

Generated CSVs belong under `outputs/`; cache data belongs under `var/`. Both are excluded from Git because they can contain prospect or client information.
