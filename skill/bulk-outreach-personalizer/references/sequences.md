# Sequences with cold-email-generator

Use the installed `cold-email-generator` skill to draft campaign-level sequences when the operator wants follow-ups. This is an assistant workflow, not an automatic call from the Python CLI.

## Share one brief

Reuse the offer, audience, sender, approved claims, commercial terms, CTA, exclusions, and copy preferences already confirmed in the conversation and campaign. Explicitly having no proof is a complete answer; do not ask for it again or invent it. Read the generator's `references/WRITING_RULES.md` on each invocation and its relevant examples for new sequences. Keep those references as the writing source of truth instead of copying their rules here.

If email 1 is already approved, retain it and draft only the requested follow-ups. For a new sequence, use the generator's sequence format. User-approved style takes precedence over generic examples. Never say an asset was already prepared unless it exists.

## Reuse company research

Draft shared templates once, with fields populated from the existing ready CSV:

| Template field | CSV source |
| --- | --- |
| `{{company_name}}` | Map the original company-name column to the sending tool's company field |
| `{{personalization_focus}}` | Agency service or company commercial focus |
| `{{personalization_buyer_phrase}}` | Businesses or people buying that service |

The Python campaign renderer uses `{{company_focus}}` and `{{buyer_phrase}}` internally. Those names differ from the exported fields above; do not mix them in the handoff. Preserve lowercase slot values when the operator prefers them.

Draft email 2 variants A/B and email 3 variants A/B as alternatives within their respective steps, not four consecutive follow-ups. Follow-ups are body-only. Explain proposed timing separately; scheduling and reply-stop behavior must be checked in the sending platform before launch.

## Review and handoff

Save campaign-specific drafts in ignored `outputs/<campaign>/sequence-draft.md`. Render each variant against actual ready rows locally, check missing fields, word counts, unsupported claims, and awkward slot grammar, and show complete examples to the operator. No new website or per-company model calls are needed to reuse existing fields.

The current CLI renders only email 1. It does not automatically export follow-up columns, validate follow-up bodies, upload a sequence, or schedule messages. A row's `ready` status applies to its generated first email; it does not approve newly drafted follow-ups. Hand over the approved shared follow-up templates alongside the ready CSV and verify imported-field previews before launch. Never upload, launch, or send without explicit authorization.
