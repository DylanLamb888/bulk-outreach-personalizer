# Deterministic copy quality

Use website facts inside the commercial question or offer. Do not lead with a separate research announcement such as “Saw that”, “I noticed”, or “I came across”. Accurate observation plus generic pitch is still mail merge.

## Campaign copy rules

- Keep the email short enough to scan in seconds; set explicit subject, pitch, and body limits in campaign JSON.
- Preserve the full website phrase in `personalization_source_focus`, but write from one compressed commercial category.
- Try the site's other extracted facts when the first snippet is generic metadata, a slogan, testimonial, company name, or incomplete clause; do not force the first fact into copy. Preserve at least one substantive body fact when available.
- Use `{{buyer_phrase}}` when the offer depends on reaching prospective buyers; use `{{company_focus}}` when the offer genuinely needs the category itself.
- Keep a focus to roughly seven words and a pitch to roughly 8–14 words.
- Prefer concrete people and actions over abstract labels. For example, use "companies looking to sell a division" instead of "corporate teams considering a carve-out".
- Reject dates, heritage claims, “& more”, promotional adjectives, long catalogues, and stacked USPs from send copy.
- Do not copy more than the configured number of consecutive words from the source evidence.
- Give each signal type an appropriate commercial angle. Surface-level synonym rotation is not a substitute for angle routing.
- Keep market-specific buyer categories in the campaign's focus-rule CSV. Never make one campaign's niche terminology a global engine rule.
- Use a concrete CTA the sender can actually fulfil, such as buyer segments, sample copy, a teardown, or a relevant outline.
- When a numbered asset makes the next step clearer, state the honest quantity directly, such as 3 segments, 5 criteria, or a 20-account sample. Never invent performance, guarantee, volume, or proof numbers.
- Keep several approved CTA structures when the campaign has a genuinely useful asset to offer. Balance them deterministically across each batch, independently from the pitch template; do not use random synonym spinning.
- Keep several approved offer-line structures when the email includes a commercial model or risk reversal. Write them as a colleague would, distribute them deterministically, and avoid repeating one polished sales sentence across the batch.
- Scope offer lines to campaign focus rules when different buyer types need materially different language. Do not describe corporate teams, advisers, or other non-owner audiences as owners.
- Scope CTAs to campaign focus rules when the promised asset uses buyer-specific language.
- Do not use em dashes in configured or rendered outreach copy.
- Never offer to show the opening line after the email has already displayed it. Each CTA must offer a distinct, deliverable next asset.
- Clean provider-added middle initials from greetings and legal suffixes, parentheticals, separators, or dangling connectors from subject-line company names.
- Prefer the most specific configured focus rule supported by first-party evidence. Use CSV fallback only when website evidence is unavailable, require the approved number of fields to agree on the same rule, and keep the result review-only. A company name may corroborate another approved field but must never qualify alone.
- Do not repeat the same opening structure in the personalized pitch and CTA within one email.
- Prefer low-friction, value-based CTAs that offer something specific. Do not ask for a call unless the campaign explicitly requires that CTA.
- Include only client-approved claims. Never infer prospect intent, pain, growth plans, sale intent, performance, or customer relationships.
- In strict campaigns, leave the row blank when no rule or complete neutral category can safely express buyer intent.
- In an approved broad campaign, unmatched company evidence may use `personalization.fallback_copy` with a permitted contact-title persona. Keep the language general and relevant to the role; never pretend the title proves a company-specific need.
- Treat generic compression as audit context only. It is not proof of niche fit unless the campaign explicitly permits broad title fallback.
- Put overused openings and client-specific exclusions in `banned_phrases`.
- Keep multiple approved sentence structures per common angle. Select them deterministically by domain and monitor opening frequency across the batch.
- Do not place several contacts from the same company in one ready file. Keep the strongest contact ready and hold the rest for later waves.

## Complete sequence review

Review the generated `.copy-review.csv` for every distinct service/buyer/template
combination and `.previews.md` for full sequence flow. Preserve evidence while
applying justified exact replacements in campaign settings, then use render-only.
Full body limits include signatures and P.S.; repetition metrics continue to
measure selling copy, not shared opt-out language. Check the distinction between
prospects and customers and avoid suggesting an existing relationship.

## Batch gate

The engine measures opening, exact-pitch, buyer-phrase, offer-line, and CTA frequency across unique domains after rendering. Duplicate contacts at the same company count once. CTA and offer-line variants are balanced only across rendered companies and within each applicable focus-rule pool, so excluded domains cannot skew the distribution. The exact-pitch gate respects the lowest concentration mathematically possible from the approved template count. Above the configured minimum batch size, genuine excessive repetition is written to `personalization_quality_flags` and can move affected rows from `ready` to `review`.

Review the manifest's `quality` object before uploading the ready-only CSV.
