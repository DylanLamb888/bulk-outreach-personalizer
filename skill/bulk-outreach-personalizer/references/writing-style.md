# Conversational writing defaults

Use these as starting preferences for new campaigns; a client's approved style
can override them. Commercial terms and claims never come from this reference.

## First email

Use an inline greeting and direct opening:

> Hi Ana - we can introduce you to retail brands to discuss your packaging design.

Keep proper names, I and acronyms intact. Prefer ordinary contractions and short
paragraphs. Preserve campaign-approved lowercase service terms such as tiktok,
b2b and ai. Use company/companies where appropriate; do not alter a brand just
because its name contains a disliked word. Avoid hedging such as "potential" in
selling copy, except where it belongs to a proper name.

A conversational message must still be truthful: do not imply an existing
relationship or claim an asset is finished without confirmation it exists.
Distinguish prospects/leads from paying clients. A service description shows
what a company does, not current buying intent or a guaranteed outcome.

## P.S. — plain sentences

Good:

> p.s. if this isn’t a priority right now, reply "no thanks" and I’ll take you off my list.

> p.s. if this doesn’t fit your plans, reply "no thanks" and I’ll take you off my list.

> p.s. if this isn’t relevant to your company, reply "no thanks" and I’ll take you off my list.

Rejected:

> p.s. not for you? reply "no thanks" and I’ll remove you from my list.

> p.s. if you’d rather I didn’t email you again, just reply "no thanks" and I’ll take you off my list.

The question fragment sounds like an advertisement; the second example leads
with avoiding the sender rather than relevance to the business. Avoid
"not relevant?", "not a fit?" and similar question fragments. Simple variations
are enough. The P.S. follows the sender and promises an action the operator must
actually honour; exporting copy does not implement that action. Default to
`sequence.ps_scope: first_only`; do not add a P.S. to follow-ups.

## Subjects

For a clear, evidenced offer, prefer a short service- or buyer-specific subject.
For broad or uncertain positioning, use an approved simple introduction or
question instead. For example, a campaign may choose `{{first_name}} x Taylor - intro`
or `Quick question, {{first_name}}`; the sender comes from its own brief.
Store subjects in campaign angle templates. Preserve proper company names and
approved lowercase terms. Do not invent buyer demand to make a subject specific,
and do not import mandatory subjects from a standalone writing framework.
Reconcile approved subject choices with campaign banned phrases during setup;
the generic scaffold permits "Quick question". Existing campaign bans remain
explicit policy and require a deliberate campaign decision to change.

## Sequence flow

The first email states the offer and a useful next step. Follow-ups clarify the
target buyers or the commercial opportunity, then make a short direct offer
to send the same approved asset. Explain the process only when requested. Do not repeat pricing or just restate the same question at every step.
Keep step 3A as the final offer and step 3B as a referral breakup unless the
campaign explicitly chooses otherwise: "Who handles [relevant function] at
[company]?" followed, when useful, by a brief restatement of the offer. Replace
the bracketed descriptions with approved campaign wording; they are not merge
fields. Apply the same message roles to neutral alternatives.
Use the prospect's service language without implying the sender delivers that
service. For example, an outreach agency books meetings for a PR agency; it does
not offer to run that prospect's PR campaigns.

Read full messages, including fees, CTA, signature and P.S. Look for clumsy joins
such as "book meetings for your [abstract noun]" and "leads among [buyer]".
Prefer complete constructions: "meetings to discuss your [service]" or "a list
focused on [buyer]". Add punctuation to short lists where justified; use one
clear category instead of copying a website catalogue. Preserve source evidence.

## Review honestly

Read every unique service/buyer/template combination plus complete sequences for
every template and evidence/fallback mode. Automated checks cover mechanics;
human editorial review covers meaning, flow and tone. Save what was reviewed and
its file hash. Never promise reply rates or label grammatical copy a proven winner.

## Approved commercial frame

One offer, different angles. Lead with the opportunity to win business; never imply leads are already interested or guaranteed clients. Offer to send the asset instead of asking permission to research it. Do not assert it is already completed unless verified. Keep the asset quantity in the campaign, never in generic defaults. Default to first-email-only P.S. (`sequence.ps_scope: first_only`).

Read service phrases aloud inside the actual sentence. Prefer "branding and marketing campaigns" over "branding and marketing creative", "brand campaigns" over "brand campaign creative", and "email design" over "crm email creative" only when website evidence supports design. Save exact supported corrections in the campaign.
