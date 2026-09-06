# Implementation status

## Core engine complete

- Portable Claude Code and Codex Skill layout
- Safe idempotent installer linking one tracked Skill source into both products
- Python CLI with validation and production modes
- Domain/website-only company qualification mode with `fit`, `needs_review`, and `not_fit` outputs and no contact, email, sequencing, or copy processing
- Offer-agnostic campaign JSON validation
- Breaking schema-v3 qualification contract with explicit migration errors for v2
- Common CSV header mapping and row-order preservation
- Unique-domain planning and duplicate-domain reuse
- Parallel public website fetching
- Persistent compressed HTTP and extracted-signal caches
- HTTPS, redirect, timeout, retry, response-size, and content-type controls
- Private, loopback, link-local, and reserved network blocking
- Deterministic extraction of up to three structured company facts with source evidence
- Detail-page inspection even when homepage metadata scores highly, with body-evidence diversity so generic metadata cannot crowd out substantive first-party facts
- Campaign-scoped regex mapping from raw website signals to one commercial category and buyer phrase
- Core, secondary, and exclude fit tiers in each campaign focus table
- Campaign-specific title and seniority qualification with mandatory title matching
- Configurable provider email-status gates plus conservative syntax-only fallback
- Deterministic duplicate-email winner selection
- Deterministic company-contact ranking with one ready contact per company domain
- Safe secondary-fact selection when the highest-ranked website snippet is unusable
- Conservative fallback compression for complete unmapped categories
- Fail-closed rejection of slogans, testimonials, company-name leakage, sentence fragments, and incomplete clauses
- Editable regex-based job-title hooks
- Signal-routed subject/pitch pairs with stable within-angle selection
- Independently salted, deterministic offer-line and CTA variation with audit fields
- Campaign-scoped offer-line targeting by matched focus rule with a required fallback
- Campaign-scoped CTA targeting by matched focus rule with a required fallback
- Banned-phrase, forbidden-punctuation, subject-length, pitch-length, offer-line-length, focus-length, source-overlap, and full-email word gates
- Unique-domain batch QA for repeated openings and exact pitches
- Confidence scoring and fail-closed review/blank behavior
- Complete personalized subject, pitch, and email rendering
- Atomic CSV and checksummed manifest writing
- Audit, ready-only, and manual-review CSV outputs
- Later-wave review routing for additional eligible contacts at the same company
- Immutable campaign and focus-rule snapshots for every run
- Automated unit and integration tests
- Clean-room non-M&A integration coverage for client-specific offers and mappings
- Optional Firecrawl `/scrape` fallback for direct failures and weak page extraction
- Independent Firecrawl success/failure caching and manifest counters
- Ordered, campaign-configured input-CSV evidence fallback with explicit source and confidence auditing
- Conversational first-name cleanup and subject-safe company-name normalization
- Deterministically balanced offer-line and CTA assignment across rendered companies within each applicable focus-rule pool
- Evidence-only focus matching with most-specific-rule selection across candidate facts
- Fail-closed first-party precedence: CSV evidence is considered only when the website is unavailable
- Buyer-phrase, offer-line, and CTA concentration checks in batch QA
- Exact-pitch concentration checks with a template-capacity floor, preventing impossible thresholds from sending balanced copy to review
- Overflow-only batch repetition routing: rows within a share cap stay ready and only the deterministic over-cap overflow moves to review
- Campaign-level blocked evidence phrases filtered before candidate selection
- Focus-gap manifest reporting with separate unmatched and intentional-exclusion samples
- Cache pruning via `--prune-cache`
- Model quote length, configured evidence blocks, navigation/cookie filters; phrase presence alone is not a relevance guarantee
- Exact approved-sentence checks for model numeric/promissory copy, safe template fallback, and selective review for offer mechanics
- Bounded subscription-throttle retries for Claude Code and Codex (5, 15, 30 seconds), then fatal stop rather than regex fallback
- Incremental validated model checkpoints, successful-peer recovery on fatal CLI errors, and thread-safe cumulative wave budgets
- Model cache identity includes complete rendered system/company prompts and validation inputs; legacy partial-brief entries miss safely
- Per-row containment of title-hook gaps and fail-closed empty-subject and blank-company-name handling
- Opt-in broad-campaign title-persona fallback for unmatched, unavailable, or explicitly permitted company evidence
- Stable hashed company-name assignment for title fallback when a CSV has no usable website value
- Opt-in model classification once per unique domain (`personalization.llm_focus`) with JSON-schema output, verbatim-evidence verification, standard phrase gates, regex fallback, per-domain caching, and manifest token accounting
- Subscription providers: headless Claude Code (default, several domains per call, tools and MCP disabled) and Codex CLI, plus an optional Anthropic SDK provider with sync and Message Batches transports
- Redirected homepages retained as model evidence so rebrands can be classified instead of discarded
- Model-written personalised opening pitch per company, title-aware, gated by word limit, opener, company-name, figure, source-overlap, banned-phrase, and dash checks, with template fallback on rejection
- Premium-model opt-in guard, per-run nominal usage budget, usage estimate at validation, and nominal cost accounting in the manifest
- `--digest-only` mode and an interview-driven Skill workflow for conversational campaign setup
- Page digests (title, description, headings, paragraphs) stored with site signals for model classification

## Deliberate boundaries

- Public websites, approved company intelligence already supplied in the input CSV, and explicit title-persona fallback; no login, CAPTCHA bypass, LinkedIn scraping, or private data access
- No per-row LLM calls or agent-written personalization; the optional model step classifies each unique domain once and fills slots or writes a gated opening; offer variants remain campaign-approved
- No email discovery or verification waterfall; the engine only evaluates supplied statuses and syntax
- No automatic campaign sending or Smartlead mutation
- JavaScript-only websites may produce a weak or missing signal unless the optional Firecrawl fallback is configured
- Self-hosted Firecrawl deployment, proxy rotation, and service hardening remain operator responsibilities
- Client offer claims and copy still require human approval before production use
- Market-specific categories are configuration, not engine behaviour; each campaign declares its own adjacent focus-rule CSV
- CSV-only company evidence is review-only and requires two approved fields to agree on one rule
- robots.txt is not consulted; the engine fetches at most a few public pages per domain and the operator owns crawl-policy decisions
- Public-IP validation resolves DNS separately from the connection; the resulting rebinding window is an accepted risk for this outbound-only threat model
- Balanced CTA and offer-line assignment is deterministic per identical batch; changing the batch's rendered-domain set re-balances assignments across it

The engine can later accept optional API-backed fetch adapters without changing the campaign or CSV output contract.

Codex live status (2026-09-04): untested. The installed `/opt/homebrew/bin/codex` npm wrapper fails with `ENOENT` because its native executable is missing. Version, help, and login-status checks never reached the CLI; no five-domain test ran and no model usage occurred. Fake-runner tests cover transport behavior, not live authentication or model compatibility. Keep the default Claude Code provider for now.

The CLI renders and validates configured follow-up sequences from the confirmed
campaign and saved company information. Bulk writing guidance lives in the shared
skill; standalone cold-email-generator examples are optional. Platform scheduling
and uploads are not implemented by the engine.

## Complete sequence delivery

Implemented optional campaign-owned sequence rendering, inline greetings,
deterministic opt-out variants, exact editorial replacements, complete-sequence
validation and compact `--smartlead-output` bundles. `--render-only` replays new
audit sidecars offline and refuses changes to research/qualification inputs.
Legacy campaigns retain first-email behaviour. Automated QA, editorial review
and platform verification have separate statuses. No platform mutation occurs.

### Replacement skill acceptance (2026-09-05)

The shared Codex/Claude skill now covers briefing through complete sequence
review and delivery. New campaign scaffolds include overridable conversational
sequence defaults; no approved proof claims is valid. The standalone cold email
generator is unchanged. A fresh synthetic campaign uses only the standard CLI,
with fake site results and real model binaries/network prohibited. Live model
execution, Smartlead preview, upload, sending and unsubscribe handling are not
verified or configured by this acceptance check.

Acceptance: 180 unittest tests passed on Python 3.12.13 and Python 3.14.6;
`ruff check src tests --select F,E9` and skill validation passed. Both installed
links resolve to the repository-owned replacement. A five-company synthetic
campaign through the installed skill's resolved CLI path produced five complete
sequence rows, with no model calls or live website access. Real-list ready-row
impact was not measured; newly unsafe/incomplete sequences are held explicitly.


## One offer across the sequence

Keep one approved asset offer across the sequence and vary the reason to respond: new business, relevant buyers, then a brief offer to send it. Keep fees and quantities in the campaign brief; do not introduce guarantees, buying intent or additional deliverables. Review complete sentences, not just service labels. Store exact wording corrections in `sequence.editorial_replacements` and follow-ups in `sequence.followups`, with approved neutral alternatives for missing slots.

Set `sequence.ps_scope` to `first_only` for a P.S. in email one only. Omitting it preserves the legacy `all` behaviour. New campaign scaffolds use `first_only`. Follow-up signatures remain; length checks include any appended P.S. Use the standard `--render-only` command with a genuine audit sidecar for subsequent copy revisions. Historical CSVs without that sidecar must not be treated as requalified audits.

Validation for the one-offer sequence update: 183 unittest cases pass on both required Python invocations; Ruff F/E9 and skill validation pass. Existing shared installation links remain valid. Tests use no live model calls. The legacy 1,409-row campaign delivery was revised with the same sequence renderer using saved editorial context, preserving eligibility, first emails and subjects; 5,636 follow-ups rendered without P.S. No upload or sending was performed.

### Skill repair acceptance (2026-09-06)

The shared skill routes new campaigns, copy-only revisions and held-prospect
review separately. Current user/campaign choices govern generic writing rules;
standalone cold-email-generator is optional inspiration and remains unchanged.
New scaffolds use the approved priority/plans/company-relevance P.S. variants,
first-email-only placement and a "Who handles…" breakup. The generic ban on
"quick question" was removed so approved subject choices can pass validation.
Existing campaign settings and deliveries were not edited.

Both required unittest commands pass 184 cases (Python 3.12.13 and 3.14.6);
Ruff F/E9 passes. The fresh-campaign test covers the scaffold subject and all
exported follow-ups; a P.S.-only offline revision preserves other fields and
repeats byte-for-byte. A five-company synthetic run resolved from the installed
Codex skill produced five ready sequences and exercised all three P.S. variants.
Both Codex/Claude links resolve to the same source. Structural skill validation
and reference checks pass; these do not certify model routing or persuasive copy.

No live model, website or Smartlead verification ran. No real-list ready-row
reduction was measured or is expected from these defaults; qualification gates
are unchanged. Legacy uploads still need a genuine audit/sidecar for offline
replay; the repair documents recovery rather than fabricating that state.
