---
name: bulk-outreach-personalizer
description: Turn a lead CSV and a confirmed campaign brief into researched first emails, follow-up alternatives and an audited Smartlead import. Use for bulk cold outreach campaign setup, copy revisions or review of held prospects. Uses cached company research through local subscription CLI logins; does not upload or send.
---

# Bulk Outreach Personalizer

One conversation, one campaign JSON, one lead CSV. Use the existing engine for
research and rendering; do not invent a campaign-specific CSV patching script.
Resolve this file's real path through any symlink, then go up two directories
to the repository. Run `<repository>/scripts/enrich.py` from any working directory.

## Brief → research → sample → approval → run → review → delivery

1. **Recover the brief.** Read the supplied CSV, existing campaign and conversation.
   Ask only for missing offer, audience, sender, approved claims, commercial terms,
   CTA or exclusions. Explicitly having no proof is complete. Use
   [interview.md](references/interview.md); the operator should not edit JSON.
2. **Read the market.** Use `--digest-only` on 20–40 sample rows before proposing
   targeting. Explain core fit, exceptions and examples in plain language.
   Do not infer prospect buying intent from service descriptions.
3. **Write one campaign.** Copy the repository campaign template into ignored
   `campaigns/local/`. Draft the whole sequence in its optional `sequence` object.
   For copy, read [writing-style.md](references/writing-style.md) and use the
   installed cold-email-generator's writing rules with the confirmed brief.
   User preferences override its generic formats. Standalone cold-email-generator
   remains available; do not modify or replace it.
4. **Preflight and sample.** Use `--validate-only`. Default to `claude-code`,
   `claude-opus-5`, low effort, five companies per call and the existing nominal
   usage guard. Explain nominal usage as a subscription usage estimate, not an API
   bill. Agree the run budget. Generate 20–50 sample rows with
   `--allow-test-campaign` and `--smartlead-output`; show complete first emails
   and both alternatives for each follow-up step. Do not call a sample validated
   if too few rendered companies exercised the repetition gate.
5. **Approve, then run.** Reuse approval already given for the brief, target,
   claims and sequence. Otherwise obtain it from the complete sample, then set
   campaign status to `approved`. Run audit, review and Smartlead outputs with the
   same script. Only one strongest eligible contact per company enters delivery.
6. **Review the actual writing.** Read every distinct service/buyer/template
   combination in the generated `.copy-review.csv`, then complete `.previews.md`
   sequences covering every template and neutral/evidence mode. Check relevance,
   grammatical joins, progression and factual meaning, not just word counts.
   Correct templates or exact editorial replacements in the campaign and use
   `--render-only` to rebuild; do not patch the export. Keep evidence untouched.
7. **Hand over precisely.** Report included and held counts, actual model usage,
   automation checks, editorial review and platform verification separately.
   Save a human-readable editorial review with the reviewed upload SHA-256 and
   remaining concerns. The generated manifest's initial `editorial_review: pending`
   must not be described as completed. Provide the import CSV, held exceptions,
   disposition ledger, previews and mapping guide. Upload/send requires separate
   authorization; a P.S. does not configure unsubscribe processing.

## Copy and delivery defaults

New campaigns start with `Hi {{first_name}} - ...`, ordinary sentences and short
paragraphs. Follow-ups remain direct replies without repeated greetings. Use
natural sentence-style opt-outs after the sender, not advertising-style questions.
Keep these choices overridable per campaign. Fees, quantities, claims and service
language always come from that campaign, never a previous client's example.

Read [sequences.md](references/sequences.md) for configuration and command usage,
[copy-quality.md](references/copy-quality.md) for quality checks and
[recovery.md](references/recovery.md) when viable prospects have been held.
Full field and output contracts are in [usage.md](references/usage.md).

## Preserve the engine's boundaries

- Cache one decision per unique company and batch several companies per call.
  Never dispatch an agent or make a model call per lead. Follow-ups and copy-only
  revisions reuse research without model calls.
- Use local Claude/Codex CLI subscription logins. No API keys in this workflow.
  Preserve model defaults, expensive-model refusal and usage guards. Codex was
  last reported untested live; do not claim fresh verification without running it.
- Evidence must be substantive and relevant, not merely present on a page.
  Keep evidence gates, contact/email qualification, checkpointing and rate-limit
  stops intact. Do not bypass a subscription throttle by switching providers.
- A held company is not made eligible by a fluent template. Neutral copy requires
  established eligibility and approved campaign fallback settings. Preserve all
  targeting exceptions and distinguish source-list evidence from website evidence.
- Tests use fake runners or classifiers. A live model sample needs authorization.
