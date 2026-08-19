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

## Status rules

- `ready`: source evidence passed the campaign confidence threshold and required lead fields are present.
- `review`: a line was rendered but the evidence, lead fields, or batch copy QA needs human review.
- `blank`: website evidence exists, but no safe commercial focus passed the configured rules.
- `error`: neither the domain nor configured input fields produced usable evidence.

Only `ready` rows should be uploaded without review.

Pass `--ready-output /path/to/smartlead-ready.csv` to write a second file containing only those rows while retaining the full audit CSV separately.

## Run manifest

Every output receives a JSON manifest containing:

- input, campaign, focus-rule table, hook-table, and output SHA-256 hashes;
- start time, finish time, and duration;
- row and unique-domain counts;
- duplicate requests avoided;
- page and signal cache usage;
- status counts and fetch statistics;
- copy-quality evaluation, warning counts, and flagged-row counts;
- opening, exact-pitch, buyer-phrase, and CTA concentration warnings;
- the exact non-secret run settings.

Generated CSVs belong under `outputs/`; cache data belongs under `var/`. Both are excluded from Git because they can contain prospect or client information.
