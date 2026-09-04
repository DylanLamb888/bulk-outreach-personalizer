---
name: bulk-outreach-personalizer
description: Turn a lead CSV into upload-ready, prospect-specific cold email for Smartlead or ListKit. Interviews the operator about the offer, reads a sample of the companies, drafts the campaign, then runs a cached, budgeted, one-call-per-company personalisation engine on the operator's own Claude or ChatGPT subscription. Use when someone hands over a lead list and wants it personalised, when a client campaign needs setting up, or when reviewing ready and review outputs.
---

# Bulk Outreach Personalizer

The operator hands you a CSV. You run the conversation, the engine runs the list. Never write per-row emails yourself, never dispatch an agent per lead, and never send anything.

Resolve the repository root as two directories above this `SKILL.md`, including when reached through a symlink. The entrypoint is `<repository-root>/scripts/enrich.py`; do not assume the current working directory is the repository.

## How it works in one breath

Each unique company website is fetched once and cached. For each company, one model call, packed several companies at a time through the operator's Claude Code login, decides whether the company fits the plain-English target, quotes the sentence that proves it, and writes one personalised opening pitch. The engine checks the quote is really on the page, runs every phrase through the deterministic copy gates, drops the approved offer line and call to action under the pitch, qualifies the contact and email, balances variants across the batch, and writes ready, review, and audit files. Rejected model output falls back to the regex focus rules, never to invented copy.

## Workflow

1. **Take the CSV.** Confirm the path and that it carries email, first name, job title, company name, and a website or domain. Ask whether this is for the operator's own agency or a client, and who the sender is.
2. **Interview the operator.** Follow `references/interview.md`: the offer in one sentence, who buys it, approved proof, the risk reversal, a value-based call to action, and anything never to be said. Confirm each answer before moving on.
3. **Read the market before describing it.** Run digest mode on the first 20 to 40 rows so you see what the companies say about themselves, then draft the plain-English target, exclusions, and three to six examples. Read the draft back and change it until the operator approves.
4. **Write the campaign file.** Copy `campaigns/campaign-template.json` and `campaigns/campaign-template-focus.csv` into `campaigns/local/`, rename them, and fill every interview answer into the JSON. Set `personalization.llm_focus.enabled` to true. Leave `provider` as `claude-code` unless the operator wants their ChatGPT subscription (`codex`). Keep `status` as `test_only`.
5. **Choose model and budget together.** Default `claude-opus-5` at low effort; offer `claude-sonnet-5` for very large lists. Never set a Fable or Mythos model without an explicit request; it needs `allow_expensive_models: true` and drains usage several times faster. Run `--validate-only`, show `llm_focus.estimated_nominal_usd_for_list` and `provider_ready`, and agree `max_nominal_usd` per run.
6. **Run a sample.** Choose slot-filled direct pitches (`write_pitch: false`) or factual model openings (`true`). Quantities in examples are not approved production promises; agree any prospect-dependent claim variants. Take 20 to 50 rows with `--allow-test-campaign`, then read complete emails aloud: subject, pitch, offer line, CTA. Check `company_fit_evidence` is a real sentence from the site, `company_fit_reason` makes sense, and `personalization_error` for rejected pitches. Fix the brief, examples, or copy limits and rerun; unchanged requests reuse cached decisions; editing the brief, claims, context, or validation limits requires fresh calls.
7. **Get approval, then run the list.** Only after the operator approves the target, claims, offer line, CTA, and sample emails, set `status` to `approved` and run with separate audit, ready, and review outputs. Report `ready`, `review`, `excluded`, and `error` counts, the `llm_focus` counts, `nominal_cost_usd`, and whether the budget was exhausted. An exhausted budget means rerun; it continues from the cache. Completed chunks are checkpointed during the run, including successful peers of a failed call; interrupted in-flight calls may need repeating.
8. **Hand over.** Only `outreach_status=ready` rows are upload-ready, one contact per company. Review rows stay out until a human approves them; later-wave contacts wait until the first contact finishes the sequence. Map `personalized_subject` and `personalized_email` in Smartlead or ListKit.

## Modes and flags

- `--digest-only`: fetch pages and write `company_page_digest` per row, no campaign, no model. Use it for step 3.
- `--validate-only`: check CSV, campaign, provider readiness, and the usage estimate without fetching.
- `--company-qualification-only`: fit, needs_review, and not_fit per company with no contacts or copy, when the operator only wants to know who belongs on the list.
- `--llm-budget-usd`: override the campaign's per-run budget. `--llm-concurrency` defaults to 2 calls at a time.
- `--firecrawl-fallback`: second pass for JavaScript-only sites when Firecrawl is configured.

## Rules that do not bend

- The model classifies and writes the opening only. The offer line, CTA, claims, and subject come from approved variants. It never invents results, numbers, customers, or prospect intent.
- Every model decision needs at least four words of evidence from the page. Blocked phrases, navigation-only labels, and cookie notices are rejected. Presence and junk filters do not prove semantic relevance: inspect the quote.
- Never place API keys or tokens in campaign files or CLI arguments. Providers use the operator's own CLI login.
- Do not add niche or client logic to Python. Market terms live in the campaign JSON and focus CSV.
- CSV company intelligence is used only when the website is unavailable, and it stays review-only unless the campaign explicitly broadens it.

Read `references/interview.md` for the question script, `references/usage.md` for every campaign field and output column, and `references/copy-quality.md` when judging copy.

Model openings with unapproved numeric or commercial promises fall back to approved templates. Openings discussing offer mechanics also require review, even when approved; ordinary style failures can still become ready through safe fallback copy.

If subscription throttling persists after the bounded retries, stop and tell the operator to wait for the reset before resuming. Do not disable the model or switch providers to bypass the limit. Completed chunks remain cached.

Codex remains untested live: the local npm wrapper cannot start its missing native executable (checked 2026-09-04). A successful PATH lookup is not proof of provider readiness. Do not describe a Codex run as verified or free based on its current zero-valued usage fields.
