# Complete sequences and repeatable delivery

## One approved campaign

Draft first-email angles and all follow-ups together from the confirmed brief.
Use [writing-style.md](writing-style.md) for bulk copy. Optional standalone
cold-email examples supply inspiration, not mandatory formats or commercial terms.
Every follow-up should advance the conversation: a relevant buyer angle, then a short direct offer to send the same asset. Avoid
process explanations when the approved angle is new-business opportunity. A/B alternatives
belong within steps 2 and 3, not four consecutive messages.
Preserve each approved message's role during revisions: step 3A offers the asset;
step 3B asks who handles the relevant function, including in neutral alternatives.

Configure `sequence.followups` using `followup_2a`, `followup_2b`, `followup_3a`,
`followup_3b`. Templates contain body text only; the engine adds the sender and
a P.S. according to `ps_scope`. Literal commercial claims must be approved in `offer.approved_claims`;
selected approved CTA/offer sentences can also be reused. The engine checks every
rendered message, including the first email. Avoid pricing repetition in follow-ups.

Use internal fields `company_focus`, `buyer_phrase`, `company_name`,
`company_short_name`, `first_name`, `cta` and `risk_reversal`. The renderer provides
these from validated research and approved campaign variants. Exported research
fields have different names (`personalization_focus`, `personalization_buyer_phrase`).
Do not put those names into internal templates.

Optional `neutral_followups` must contain all four alternatives and may be used
only when required personalized fields are missing. They never grant company
eligibility. `greeting` is `inline` by default, or `paragraph`. Inline mode expects
the standard `Hi {{first_name}},\n\n{{personalized_pitch}}` campaign body prefix
(using the configured pitch field); only safe sentence starters are lowercased.
`ps_variants` contains literal one-line opt-out sentences beginning `p.s. `.
The stable company/message assignment never stacks P.S. lines on a rerun.
Use enough approved variants for variety without forced synonym spinning.

`max_followup_words` defaults to 55 including signature/P.S. First email uses
`quality.max_body_words`, also including P.S. Keep it shorter where practical;
do not silently relax limits to force rows through.

## Standard commands

```bash
python3 <repository>/scripts/enrich.py --input leads.csv --campaign campaigns/local/client.json --output outputs/client/audit.csv --review-output outputs/client/review.csv --smartlead-output outputs/client/smartlead.csv
python3 <repository>/scripts/enrich.py --render-only --input outputs/client/audit.csv --campaign campaigns/local/client.json --output outputs/client/revised-audit.csv --smartlead-output outputs/client/revised-smartlead.csv
```

Add `--allow-test-campaign` for controlled samples. Keep the audit's
`.render-state.json` sidecar. Render-only uses no network/model calls and does not
need a provider preflight. It rejects changed targeting, claims, classification
limits, fallback policy or research rule files. Run normal enrichment when those
change; unchanged requests still use the existing model cache.

### Missing or incompatible saved state

First look for the original audit and matching sidecar in the delivery bundle.
An upload CSV or saved editorial context is not a qualification record. Preserve
legacy deliveries without claiming offline replay or manufacturing a sidecar.
Prepare campaign edits and validation locally; if genuine state cannot be found,
explain that a normal enrichment run is needed and confirm any additional live
usage not already authorized. Do not quietly fetch websites or call a provider
to complete a copy-only request. If classification inputs changed, use the same
normal-run path rather than weakening the fingerprint check.

## Copy corrections and review

Exact `sequence.editorial_replacements` map a service or buyer phrase to
`{"text": "approved wording", "reason": "why this correction is supported"}`.
Domain-keyed `company_name_overrides` use the same shape. Correct known branding
from evidence; do not blindly strip punctuation from proper names. Neither map
modifies original source fields or quotes. Use examples/brief changes and fresh
classification for substantive changes that cannot be justified editorially.

Read every distinct combination in `.copy-review.csv` and complete sequences in
`.previews.md`. Record editorial review separately with the upload's SHA-256.
Check source relevance, sentence joins and whether follow-ups add anything.
Automated checks cannot certify persuasive writing. True exceptions remain held.

## Smartlead handoff

Use the compact CSV's existing column names and generated `.mapping.md`.
Map email 1 subject/body to `{{personalized_subject}}` / `{{personalized_email}}`;
map follow-up bodies to their exact exported column names. Do not add another
signature or P.S. Review live paragraph breaks, sequencing, timing, stop-on-reply
and opt-out suppression before launch. The file does not configure these settings
and no upload/send is implied by approval of the copy.

Use `ps_scope: "first_only"` for new campaigns unless the user chooses otherwise.
Omission means `all` for backward compatibility. Follow-ups still include the sender.
