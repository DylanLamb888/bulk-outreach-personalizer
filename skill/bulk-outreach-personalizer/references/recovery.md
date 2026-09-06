# Recovering an upload without hiding targeting exceptions

Use this workflow when the operator wants viable prospects included despite website or copy failures. It is an assistant-led recovery and packaging step; the CLI does not automatically implement it.

## Separate three decisions

Track targeting eligibility, evidence source and copy quality independently. A comma-separated phrase, company-name overlap or source-copy limit can block copy without proving that a company is unsuitable. Missing website evidence is also not a negative fit decision.

Confirm whether the operator wants genuine non-targets and additional company contacts included. Preserve their decision. Keep original audit files and statuses unchanged; export a separate delivery disposition with the reason for every inclusion or hold.

## Recover in stages

1. Preserve existing ready emails and their evidence.
2. Hold known non-target businesses, disallowed contact roles, invalid email addresses and extra company contacts according to the approved policy.
3. Assess remaining companies using cached website text or supplied company descriptions. Label CSV evidence as `input:Company description`; never describe it as a newly verified website. Keep company decisions cached, batch several companies per subscription call and stay within the remaining approved usage guard.
4. For a supported core fit, use validated service and buyer slots in approved templates. If evidence and targeting passed but phrase formatting failed, use a campaign-approved neutral offer. Do not invent a niche, buyer intent or proof to fill the gap.
5. Keep evidence failures, ambiguous fit and genuine exclusions held with actionable reasons. Do not globally disable gates or relabel every audit row as ready.

Keep commercial claims, neutral pitch variants, eligibility thresholds and follow-up variants in the local campaign. Retain recovery settings, source mapping, decisions, usage and the genuine audit/sidecar bundle for the standard CLI. Follow `sequences.md` when saved state is missing; do not create a campaign-specific packaging script or invent qualification provenance.

## Delivery checks

Account for every input row exactly once as included or held. Check unique emails and company-contact policy, complete bodies, unresolved placeholders, banned wording, length and repetition. Review examples from each recovery mode. Preserve source fields and verbatim quotations when making editorial substitutions in generated copy.

Deliver an import CSV, held-exceptions CSV and full disposition ledger. Report how many original ready rows were preserved, how many were recovered, and which use source-list personalisation or neutral copy. Include first-email and follow-up variants where requested. A/B follow-ups are alternatives within each step. Preparing an import is not permission to upload, schedule or send it.

For audits produced by the current engine, fix sequence-only failures in the
campaign and use `--render-only` with the saved audit sidecar. This can restore a
row held only by a sequence failure without repeating research. It cannot change
company eligibility. Missing/ambiguous evidence still needs the staged assessment
above; do not edit an audit/sidecar to bypass the classification fingerprint.
Use `--smartlead-output` for the native delivery bundle instead of writing a new
CSV packaging script. Historical, manually recovered deliveries remain archived;
they are not evidence that fresh classification can be skipped.
