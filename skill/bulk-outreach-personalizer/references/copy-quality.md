# Deterministic copy quality

Use website facts inside the commercial question or offer. Do not lead with a separate research announcement such as “Saw that”, “I noticed”, or “I came across”. Accurate observation plus generic pitch is still mail merge.

## Campaign copy rules

- Keep the email short enough to scan in seconds; set explicit subject, pitch, and body limits in campaign JSON.
- Preserve the full website phrase in `personalization_source_focus`, but write from one compressed commercial category.
- Try the site's other extracted facts when the first snippet is a slogan, testimonial, company name, or incomplete clause; do not force the first fact into copy.
- Use `{{buyer_phrase}}` when the offer depends on reaching prospective buyers; use `{{company_focus}}` when the offer genuinely needs the category itself.
- Keep a focus to roughly seven words and a pitch to roughly 8–14 words.
- Reject dates, heritage claims, “& more”, promotional adjectives, comma-separated catalogues, and stacked USPs from send copy.
- Do not copy more than the configured number of consecutive words from the source evidence.
- Give each signal type an appropriate commercial angle. Surface-level synonym rotation is not a substitute for angle routing.
- Keep market-specific buyer categories in the campaign's focus-rule CSV. Never make one campaign's niche terminology a global engine rule.
- Use a concrete CTA the sender can actually fulfil, such as buyer segments, sample copy, a teardown, or a relevant outline.
- Keep several approved CTA structures when the campaign has a genuinely useful asset to offer. Select by domain, independently from the pitch template; do not use random synonym spinning.
- Prefer low-friction, value-based CTAs that offer something specific. Do not ask for a call unless the campaign explicitly requires that CTA.
- Include only client-approved claims. Never infer prospect intent, pain, growth plans, sale intent, performance, or customer relationships.
- Leave the row blank when no rule or complete neutral category can safely express buyer intent.
- Put overused openings and client-specific exclusions in `banned_phrases`.
- Keep multiple approved sentence structures per common angle. Select them deterministically by domain and monitor opening frequency across the batch.

## Batch gate

The engine measures opening and exact-pitch frequency across unique domains after rendering. Duplicate contacts at the same company count once. Above the configured minimum batch size, excessive repetition is written to `personalization_quality_flags` and can move affected rows from `ready` to `review`.

Review the manifest's `quality` object before uploading the ready-only CSV.
