---
name: bulk-outreach-personalizer
description: Build or revise researched cold-email CSV campaigns with complete sequences and audited Smartlead delivery files. Use for campaign setup, bulk copy revisions and held-prospect review; standalone email writing does not need this workflow.
---

# Bulk Outreach Personalizer

One conversation, one campaign JSON, one lead CSV. Use the existing engine;
keep reusable corrections in campaign data, not campaign-specific CSV scripts.
Resolve the installed skill directory through its symlink. Its parent's parent
is the repository; run `<repository>/scripts/enrich.py` with explicit file paths.

## Choose the work already requested

- **New campaign or changed targeting:** use [interview.md](references/interview.md).
  Recover known answers, inspect the CSV, research a representative sample,
  agree targeting and draft the complete sequence before the full run.
- **Copy revision:** use [sequences.md](references/sequences.md). Read the current
  campaign and audit, then use `--render-only` with its genuine sidecar. Skip
  the interview, website fetching and provider preflight when research inputs
  are unchanged. Missing sidecars require the recovery path in that reference.
- **Held prospects:** use [recovery.md](references/recovery.md). Distinguish
  copy defects from targeting or contact exceptions before changing anything.

Read [writing-style.md](references/writing-style.md) when writing or reviewing
copy, [copy-quality.md](references/copy-quality.md) for batch review, and
[usage.md](references/usage.md) for field/output details when needed.

## Authority and decisions

The user's current instructions and approved campaign choices take precedence
over generic skill examples and writing formulas. An explicitly confirmed lack
of proof is a complete answer. Fees, payment conditions, claims, quantities and
asset availability come from this campaign's brief; never infer them from an old
client, project instruction or persuasive example. Surface conflicting facts
that affect the offer rather than silently choosing one.

This skill's writing reference is sufficient for bulk copy. If the installed
cold-email-generator references offer useful examples, read only the relevant
parts as inspiration. Their fixed subjects, variant counts and proof requirements
do not govern this workflow. Keep that standalone skill unchanged.

Reuse authorization already given. Complete authorized local edits, checks and
repairs without asking after each step. Ask only for missing decisions that
materially change the campaign or permissions. Prepare complete samples before
requesting approval for the full run. If an instruction blocks progress, link
the exact file and explain the requirement and the remaining decision.

## Run and finish

For a new researched run, use `--validate-only`, agree the nominal usage budget,
and produce a controlled 20–50-row sample with `--allow-test-campaign`. Reuse an
existing budget approval within its scope. Nominal USD estimates subscription
usage; it is not an API bill. Show complete first emails and both alternatives
for each follow-up step. Do not call a sample validated if too few rendered
companies exercised the repetition gate. On sequence/target approval, set the
campaign to `approved` and run the same CLI with `--smartlead-output`.

Read every distinct service/buyer/template combination in `.copy-review.csv`
and complete `.previews.md` sequences covering each template and fallback mode.
Fix awkward joins in campaign templates or justified editorial replacements,
then render again. Preserve subjects, message roles and other approved copy
outside the requested edit. Recheck affected messages and complete-sequence
gates; do not restart research for a wording correction.

Completion means the requested copy is corrected and checked, and the import,
held exceptions, disposition ledger, previews, mapping guide and manifest are
delivered. Record editorial review with the final upload SHA-256 and unresolved
concerns. Report automated checks, editorial judgment and platform verification
separately. If blocked, preserve completed work and explain what remains.
Uploading, sending and configuring opt-out handling require their own authority;
a P.S. does not configure suppression.

## Engine boundaries

- Use subscription CLI logins. Default to `claude-code`, `claude-opus-5`, low
  effort and five companies per call. Keep model/usage guards and checkpointing.
  No API keys, per-lead agents or per-row model calls in this workflow.
- Cache one model decision per unique domain. Follow-ups and presentation-only
  revisions reuse research. Never bypass throttle stops by switching providers.
- Preserve source evidence, qualification and genuine exceptions. Fluent neutral
  copy does not establish eligibility; fallback needs approved campaign policy.
- Tests use fake runners or classifiers. Run relevant checks and the repository's
  required suites for engine/test changes. Live samples spend subscription usage
  and need authorization. Report only provider execution actually verified.
