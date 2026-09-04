# Campaign interview

Run this conversation before any full run. Every answer maps to a field in
`campaigns/local/<client>.json`, so the operator never edits JSON by hand.
Ask one block at a time, in plain language, and confirm what you heard before
moving on. Pull anything already known from earlier messages, the CSV, or
existing campaigns instead of asking again.

## 1. The list

- Confirm the CSV path and run `--validate-only` style checks mentally: does it
  have email, first name, job title, company name, and a website or domain?
- Ask who the list is for: the operator's own agency or a client. The client
  name becomes `client_name`; the sender name becomes `sender.name`.

## 2. The offer

Ask, in this order:

1. "What are you selling, in one sentence?" -> `offer.service`
2. "Who buys it? Which roles, at what kind of company?" -> `offer.audience`
   and the contact rules in `qualification.contact` (owners, founders, CEOs,
   or a department head).
3. "Do you have proof you're allowed to use: a result, a client name, a
   number?" -> `offer.approved_claims`. If they have none, say that no result
   will be claimed anywhere, and add that to `offer.forbidden_claims`.
4. "How do you take the risk away? Pay per result, a guarantee, a small
   upfront fee?" -> `offer.risk_reversal` and two or three
   `offer.risk_reversal_variants` written as a colleague would say them.
5. "What do you want them to reply to? Something you can send, not a call." ->
   `offer.cta` and three to six `offer.cta_variants` offering a concrete asset
   such as a sample list, an outline, or examples. Only ask for a call if the
   operator insists.
6. "Anything you never want said?" -> `personalization.banned_phrases` and
   `offer.forbidden_claims`.

## 3. The target market

1. Run digest mode on the first 20 to 40 rows so you can read what these
   companies actually say about themselves:

   ```bash
   python <repository-root>/scripts/enrich.py --input LEADS.csv --output outputs/<client>/digests.csv --digest-only --quiet
   ```

2. Read `company_page_digest` for those rows and draft, in plain English:
   - `llm_focus.icp`: what a target company must sell and to whom;
   - `llm_focus.exclusions`: the company types on the list that must never be
     contacted, named the way they describe themselves;
   - three to six `llm_focus.examples`, each a short website line from the
     digests plus the `focus` and `buyer_phrase` you would want back.
3. Read the draft back to the operator and change it until they approve.
4. Keep the focus CSV as the regex fallback: copy the template, add only
   obvious exclusions if any, and leave the rest to the model.

## 4. Model and budget

- Default `llm_focus.provider` is `claude-code`; use `codex` only if the
  operator says the run should use their ChatGPT subscription.
- Codex is untested live on this machine (2026-09-04: missing native
  executable). A PATH check alone does not verify login or execution.
- Default `llm_focus.model` is `claude-opus-5` at `effort` `low`. Offer
  `claude-sonnet-5` when the list is large and usage matters. Never set a
  Fable or Mythos model unless the operator explicitly asks and accepts that
  it drains usage several times faster; that also requires
  `allow_expensive_models: true`.
- Show the `estimated_nominal_usd_for_list` figure from `--validate-only` and
  agree a `max_nominal_usd` per run. Explain that an exhausted budget stops new
  calls and the next run continues from the cache. Completed model chunks
  are saved before the next wave, so an interruption preserves completed work. Changed prompts, claims,
  contact context, evidence, or validation limits require fresh model calls.

Subscription limits trigger three bounded retries (5, 15, 30 seconds), then a
clear stop. Resume after the subscription resets; do not treat it as a completed
run or quietly fall back to regex for the unprocessed list.

## 5. The email

- Keep the body template from the example: greeting, personalised pitch,
  approved offer line, approved CTA, sender name.
- Choose `llm_focus.write_pitch: false` for approved direct-pitch templates
  filled with company and buyer slots, or `true` for a factual model opening.
  Agree any quantities and prospect-dependent claim variants; example figures
  are not production defaults. Keep commercial mechanics in the approved offer
  line. Model openings repeating them require review; unapproved promises are
  removed. Match word limits to the operator's style.
- Check model quotes contain substantive business evidence, not just text
  present on the page. Blocked phrases, short quotes, and obvious site junk
  are rejected automatically; relevance still needs sample review.
- Read two or three finished emails from the sample run aloud, in full, before
  asking for approval.

## 6. Approval

Only after the operator has approved the target description, the claims, the
offer line, the CTA, and the sample emails, change `status` to `approved` and
run the full list. Record the approval in the conversation.

When follow-ups are requested, reuse this confirmed brief with cold-email-generator via `sequences.md`. Preserve approved first-email copy and review complete follow-up previews before sending-platform setup.
