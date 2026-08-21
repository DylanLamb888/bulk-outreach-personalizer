# Bulk Enrich Production Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the correctness bugs, offer-agnostic violations, and operator gaps found in the 2026-08-21 code review so the tool + skill are production-ready.

**Architecture:** No structural change. Ten independent tasks against the existing stdlib-only engine: five bug fixes, three operator features (evidence blocklist config, focus-gap manifest reporting, cache pruning), one cleanup pass, one docs/skill/version pass. TDD per task, one commit per task.

**Tech Stack:** Python ≥3.11 stdlib only. `unittest` (run: `env PYTHONPATH=src python3 -B -m unittest discover -s tests`).

**Spec:** None — the Findings section below is the spec. Full review context: conversation of 2026-08-21.

## Findings (spec)

1. Unknown HTTP charset raises uncaught `LookupError` → domain fails as "unexpected domain error" ([fetcher.py:210](../../../src/bulk_enrich/fetcher.py)).
2. Blank `company_name` renders an empty subject that passes every gate (template subject is `{{company_short_name}}`); no empty-subject gate exists.
3. `TitleHookTable.match` raises inside the row loop ([pipeline.py:1247](../../../src/bulk_enrich/pipeline.py)) — a hooks file without a catch-all kills the whole run after fetching.
4. A domain whose rows match two focus rules enters two balancing pools; last `assigned.update()` wins, `_build_copy` silently re-picks → skewed variant balance.
5. Batch QA demotes every domain sharing an over-cap opening/CTA/offer-line/buyer-phrase, not just the overflow — on homogeneous lists this flips most of the batch to review.
6. Engine fossils violate offer-agnostic rule: 3 CBP-site literals in `_BLOCKED_PHRASES` and a `sale of pianos → piano sales` rewrite ([extract.py:27-29,410](../../../src/bulk_enrich/extract.py)). No campaign-level evidence blocklist exists.
7. No operator view of *why* domains failed to match focus rules; iterating the focus CSV requires manually mining the audit CSV.
8. `var/cache/` grows forever (TTL gates reads only, never deletes).
9. User-agent strings say 0.1 / 0.8 while `__version__` is 0.9.0; manifest re-parses the input CSV a second time; stray `inspect-output.mjs` tracked at repo root.
10. Docs drift: review-output "with rendered copy" is wrong for `low_confidence_action: "blank"`; batch-relative determinism of variant balancing undocumented; robots.txt / DNS-rebinding stance undocumented.

## Global Constraints

- Stdlib only — `dependencies = []` stays empty.
- Python ≥3.11; suite must pass on 3.11 and 3.13 (CI matrix).
- Engine stays offer-agnostic: no niche/client literals in `src/`; market language lives in campaign JSON + focus CSV only.
- Fail-closed: uncertain rows go to `review`/`error`/blank, never `ready`.
- Deterministic: no randomness, no time-dependent copy decisions.
- No em dashes in any configured or rendered copy, including test fixtures.
- Full suite green after every task: `env PYTHONPATH=src python3 -B -m unittest discover -s tests`
- One commit per task, lowercase imperative message, ending `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Charset fallback in HTTP fetcher

**Files:**
- Modify: `src/bulk_enrich/fetcher.py` (~line 210, plus new function + `import codecs`)
- Test: `tests/test_fetcher.py`

**Interfaces:**
- Produces: `resolve_charset(value: str | None) -> str` (module-level, fetcher.py).

- [ ] **Step 1: Write the failing test** — add to `FetcherTests` in `tests/test_fetcher.py` (import `resolve_charset` from `bulk_enrich.fetcher`):

```python
def test_resolves_unknown_charset_to_utf8(self) -> None:
    self.assertEqual(resolve_charset("utf8mb4"), "utf-8")
    self.assertEqual(resolve_charset(None), "utf-8")
    self.assertEqual(resolve_charset(""), "utf-8")
    self.assertEqual(resolve_charset("ISO-8859-1"), "ISO-8859-1")
```

- [ ] **Step 2: Run to verify it fails** — `env PYTHONPATH=src python3 -B -m unittest tests.test_fetcher -v` → ImportError on `resolve_charset`.

- [ ] **Step 3: Implement** — in `fetcher.py`, add `import codecs` to imports and, above `class SafeRedirectHandler`:

```python
def resolve_charset(value: str | None) -> str:
    """Return a decodable charset label, falling back to UTF-8 for unknown ones."""
    if not value:
        return "utf-8"
    try:
        codecs.lookup(value)
    except LookupError:
        return "utf-8"
    return value
```

Replace line 210 `charset = response.headers.get_content_charset() or "utf-8"` with:

```python
charset = resolve_charset(response.headers.get_content_charset())
```

- [ ] **Step 4: Run full suite** → PASS.
- [ ] **Step 5: Commit** — `fix unknown charset handling in http fetcher`

---

### Task 2: Empty-subject gate and fail-closed blank company name

**Files:**
- Modify: `src/bulk_enrich/pipeline.py` — `_build_copy` (~line 411) and the `should_render` context block (~line 1248)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `_build_copy(campaign, context, domain, signal_type, source_evidence, focus_rule=...)` (existing signature unchanged).

- [ ] **Step 1: Write the failing tests** — in `PipelineTests`. `campaigns/campaign-template.json` has subject `{{company_short_name}}`, so it drives both cases:

```python
def _template_copy_context(self) -> dict[str, object]:
    return {
        "first_name": "Ana",
        "buyer_phrase": "owners planning an exit",
        "company_focus": "sell-side advisory",
        "company_short_name": "Northstar",
    }

def test_build_copy_rejects_empty_rendered_subject(self) -> None:
    campaign = load_campaign(ROOT / "campaigns" / "campaign-template.json")
    context = self._template_copy_context()
    context["company_short_name"] = ""
    with self.assertRaisesRegex(ValueError, "rendered subject is empty"):
        _build_copy(campaign, context, "northstar.example", "service", "")

def test_build_copy_reports_missing_company_short_name(self) -> None:
    campaign = load_campaign(ROOT / "campaigns" / "campaign-template.json")
    context = self._template_copy_context()
    del context["company_short_name"]
    with self.assertRaisesRegex(ValueError, "company_short_name"):
        _build_copy(campaign, context, "northstar.example", "service", "")
```

- [ ] **Step 2: Run to verify failure** — first test fails (empty subject currently passes: 0 words ≤ max, so `_build_copy` returns copy). Second may already pass via KeyError→failure listing; keep it as a regression pin.

- [ ] **Step 3: Implement** — in `_build_copy`, immediately after `subject, body = render_email(...)` (before the subject word-count check):

```python
        if not subject.strip():
            failures.append(f"{template.template_id}: rendered subject is empty")
            continue
```

In the row loop's `should_render` block, replace `"company_short_name": _short_company_name(company_name),` inside `context.update({...})` with nothing, and after the `context.update(...)` call add:

```python
            short_name = _short_company_name(company_name)
            if short_name:
                context["company_short_name"] = short_name
```

(Blank company name → key absent → any template referencing it fails with a clear missing-merge-field error → row becomes `personalization_status=error`, never `ready`.)

- [ ] **Step 4: Run full suite** → PASS (existing tests supply non-blank company names).
- [ ] **Step 5: Commit** — `fail closed on blank subjects and missing company names`

---

### Task 3: Contain title-hook failures to the row

**Files:**
- Modify: `src/bulk_enrich/pipeline.py` (~lines 1245–1248)
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test** — mirrors the run-level tests around `test_qualification_outputs_deduplicate_and_snapshot_the_run` (temp dir, small CSV, `FakeDomainEnricher`, scale-olympus example campaign). Key difference: a temp hooks CSV with **no catch-all** and a lead title that matches contact qualification but no hook rule:

```python
def test_title_hook_gap_fails_only_the_row(self) -> None:
    campaign = load_campaign(ROOT / "campaigns" / "examples" / "scale-olympus.json")
    focuses = CommercialFocusTable.load(ROOT / "campaigns" / "examples" / "scale-olympus-focus.csv")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        hooks_path = tmp_path / "hooks.csv"
        hooks_path.write_text(
            "priority,title_pattern,persona,hook\n"
            '10,chief financial officer,finance,"From finance, timing matters"\n',
            encoding="utf-8",
        )
        input_path = tmp_path / "leads.csv"
        with input_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["First Name", "Email", "Company Name", "Job Title", "Company Domain"])
            writer.writerow(["Ana", "ana@northstar.example", "Northstar Advisors", "Founder", "northstar.example"])
        manifest = run_enrichment(
            input_path=input_path,
            output_path=tmp_path / "out.csv",
            campaign=campaign,
            title_hooks=TitleHookTable.load(hooks_path),
            commercial_focuses=focuses,
            options=RunOptions(cache_dir=tmp_path / "cache"),
            domain_enricher=FakeDomainEnricher(),
        )
        with (tmp_path / "out.csv").open("r", encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row["personalization_status"], "error")
        self.assertIn("no fallback rule", row["personalization_error"])
        self.assertEqual(row["outreach_status"], "error")
        self.assertEqual(manifest["output"]["status_counts"].get("error"), 1)
```

("Founder" matches scale-olympus ready titles but not the CFO-only hooks file. Adjust column headers/assertions to the scale-olympus campaign's actual required columns if the existing run tests use different ones — copy their CSV shape.)

- [ ] **Step 2: Run to verify failure** — currently the run **raises** `ValueError: title-hook table has no fallback rule` instead of completing.

- [ ] **Step 3: Implement** — replace lines 1245–1248:

```python
        if should_render:
            title = str(context.get("job_title", ""))
            hook = title_hooks.match(title)
```

with:

```python
        if should_render:
            title = str(context.get("job_title", ""))
            try:
                hook = title_hooks.match(title)
            except ValueError as exc:
                errors.append(f"title hook: {exc}")
                values["personalization_status"] = "error"
                should_render = False
        if should_render:
```

(The remainder of the original block — `company_name = ...` through `values["personalization_status"] = ...` and the render-job append — stays under the second `if should_render:`; re-indent nothing else.)

- [ ] **Step 4: Run full suite** → PASS.
- [ ] **Step 5: Commit** — `contain title-hook gaps to the affected row`

---

### Task 4: One balancing pool per domain

**Files:**
- Modify: `src/bulk_enrich/pipeline.py` — `_balanced_ctas_for_rendered_domains` (~line 226) and `_balanced_offer_lines_for_rendered_domains` (~line 283)
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test:**

```python
def test_domain_with_two_focus_rules_is_assigned_once_from_first_rule_pool(self) -> None:
    variants = (
        CtaVariant(variant_id="x-only", text="Want the 3 X ideas?", focus_rules=("rule-x",)),
        CtaVariant(variant_id="y-only", text="Want the 3 Y ideas?", focus_rules=("rule-y",)),
        CtaVariant(variant_id="default", text="Want the outline?", focus_rules=("*",)),
    )
    assigned = _balanced_ctas_for_rendered_domains(
        variants,
        [("a.example", "rule-x"), ("a.example", "rule-y"), ("b.example", "rule-y")],
    )
    self.assertEqual(assigned["a.example"].variant_id, "x-only")
    self.assertEqual(assigned["b.example"].variant_id, "y-only")
```

- [ ] **Step 2: Run to verify failure** — today `a.example` lands in both pools and the rule-y assignment overwrites rule-x.

- [ ] **Step 3: Implement** — in both functions, replace `for domain, focus_rule in dict.fromkeys(domain_focus_rules):` with:

```python
    first_rule: dict[str, str] = {}
    for domain, focus_rule in domain_focus_rules:
        first_rule.setdefault(domain, focus_rule)
    for domain, focus_rule in first_rule.items():
```

(Keep everything inside the loop unchanged. `_build_copy`'s per-row applicability fallback already covers rows whose rule differs from the domain's first-seen rule.)

- [ ] **Step 4: Run full suite** → PASS.
- [ ] **Step 5: Commit** — `assign each domain to one variant balancing pool`

---

### Task 5: Batch QA demotes only the over-cap overflow

**Files:**
- Modify: `src/bulk_enrich/pipeline.py` — `_apply_batch_quality` (~lines 818–936), new helper above it
- Test: `tests/test_pipeline.py` (new test + adjust any existing test asserting all-affected flagging)

**Interfaces:**
- Produces: `_overflow_domains(kind: str, value: str, affected: set[str], allowed: int) -> tuple[str, ...]` (module-level).

**Semantics:** a share warning still records the full count/share; but only the domains *beyond* the cap (deterministically chosen by stable hash) get `personalization_quality_flags` and the ready→review demotion. `allowed = max(1, int(total * limit))`. Warning dicts gain `"flagged": <overflow count>`.

- [ ] **Step 1: Write the failing test** — drive `_apply_batch_quality` directly, as existing tests do:

```python
def test_batch_quality_demotes_only_the_overflow(self) -> None:
    campaign = load_campaign(ROOT / "campaigns" / "campaign-template.json")
    campaign.data["quality"]["min_rows"] = 10
    campaign.data["quality"]["max_opening_share"] = 0.45
    rows = []
    domains = []
    for index in range(10):
        pitch = (
            "Could a campaign start more conversations here?"
            if index < 6
            else f"Different opening number {index} for this domain?"
        )
        rows.append({
            "personalized_pitch": pitch,
            "personalization_status": "ready",
            "personalization_quality_flags": "",
        })
        domains.append(f"domain{index}.example")
    report = _apply_batch_quality(rows, domains, campaign)
    flagged = [row for row in rows if row["personalization_quality_flags"]]
    demoted = [row for row in rows if row["personalization_status"] == "review"]
    # 6/10 share the opening; cap 0.45 of 10 allows 4; only 2 overflow rows flagged.
    self.assertEqual(len(flagged), 2)
    self.assertEqual(len(demoted), 2)
    self.assertEqual(report["flagged_rows"], 2)
    opening_warnings = [w for w in report["warnings"] if w["type"] == "opening_share"]
    self.assertEqual(opening_warnings[0]["count"], 6)
    self.assertEqual(opening_warnings[0]["flagged"], 2)
```

(If the template campaign's other quality caps interfere, relax them in `campaign.data["quality"]` inside the test the same way. Re-run twice to confirm the same 2 domains are flagged — determinism.)

- [ ] **Step 2: Run to verify failure** — currently all 6 rows are flagged/demoted.

- [ ] **Step 3: Implement** — add helper above `_apply_batch_quality`:

```python
def _overflow_domains(
    kind: str, value: str, affected: set[str], allowed: int
) -> tuple[str, ...]:
    """Deterministically pick the domains beyond a share cap for review routing."""
    ordered = sorted(
        affected,
        key=lambda domain: hashlib.sha256(
            f"quality-overflow:{kind}:{value}:{domain}".encode("utf-8")
        ).hexdigest(),
    )
    return tuple(ordered[max(allowed, 1):])
```

Then, in each of the five gates (opening, exact pitch, buyer phrase, CTA, offer line), replace the `for domain in affected: flags_by_domain...` pattern with (opening shown; mirror for the others, using the gate's own kind string and, for exact pitch, `effective_limit` instead of the configured limit):

```python
        if share > float(quality["max_opening_share"]):
            allowed = int(total * float(quality["max_opening_share"]))
            overflow = _overflow_domains("opening", opening, affected, allowed)
            message = (
                f"opening '{opening}' appears on {share:.1%} of rendered domains; "
                "holding the overflow for review"
            )
            warnings.append(
                {
                    "type": "opening_share",
                    "opening": opening,
                    "count": len(affected),
                    "share": round(share, 4),
                    "flagged": len(overflow),
                }
            )
            for domain in overflow:
                flags_by_domain.setdefault(domain, []).append(message)
```

- [ ] **Step 4: Run full suite** — fix any existing repetition test that asserted the old all-affected behavior (update expected flagged counts to overflow-only; the warning `count` stays the full affected count).
- [ ] **Step 5: Commit** — `demote only over-cap overflow rows in batch quality gates`

---

### Task 6: Campaign-level evidence blocklist; remove engine fossils

**Files:**
- Modify: `src/bulk_enrich/config.py` (property + validation), `src/bulk_enrich/pipeline.py` (`_candidate_facts`), `src/bulk_enrich/extract.py` (delete fossils), `src/bulk_enrich/site.py` (`SIGNAL_ENGINE_VERSION`), `config/campaign.schema.json`, `campaigns/campaign-template.json`
- Test: `tests/test_config.py`, `tests/test_pipeline.py`, adjust `tests/test_extract.py:67-68,98-100`

**Interfaces:**
- Produces: `CampaignConfig.blocked_evidence_phrases -> tuple[str, ...]` (casefolded).
- `_candidate_facts(signal, row, campaign)` signature unchanged; now filters blocked evidence.

- [ ] **Step 1: Write the failing tests:**

In `tests/test_config.py` (mirror existing validation-test style — load template data, mutate, expect `CampaignConfigError`):

```python
def test_blocked_evidence_phrases_must_be_strings(self) -> None:
    data = self._valid_campaign_data()  # reuse the file's existing valid-data helper
    data["personalization"]["blocked_evidence_phrases"] = [1]
    with self.assertRaisesRegex(CampaignConfigError, "blocked_evidence_phrases"):
        validate_campaign_data(data)
```

In `tests/test_pipeline.py` (import `_candidate_facts`):

```python
def test_candidate_facts_drop_blocked_evidence(self) -> None:
    campaign = load_campaign(ROOT / "campaigns" / "campaign-template.json")
    campaign.data["personalization"]["blocked_evidence_phrases"] = [
        "works with the trade community"
    ]
    signal = SiteSignal(
        domain="example.com",
        observation="",
        evidence="",
        source_url="https://example.com/",
        confidence=0.9,
        status="ok",
        facts=(
            CompanyFact(
                signal_type="service",
                focus="the trade community programme",
                observation="your team works with the trade community",
                evidence="Through this program, CBP works with the trade community",
                source_url="https://example.com/",
                confidence=0.9,
            ),
            CompanyFact(
                signal_type="service",
                focus="customs brokerage for importers",
                observation="your team provides customs brokerage for importers",
                evidence="We provide customs brokerage for importers",
                source_url="https://example.com/about",
                confidence=0.85,
            ),
        ),
    )
    facts = _candidate_facts(signal, {}, campaign)
    self.assertEqual(len(facts), 1)
    self.assertIn("customs brokerage", facts[0].evidence)
```

- [ ] **Step 2: Run to verify failure** — config test fails (no validation), pipeline test fails (2 facts returned).

- [ ] **Step 3: Implement:**

`config.py` — property after `banned_phrases`:

```python
    @property
    def blocked_evidence_phrases(self) -> tuple[str, ...]:
        configured = self.data["personalization"].get("blocked_evidence_phrases", [])
        return tuple(str(item).casefold() for item in configured)
```

Validation, after the `banned_phrases` block in `validate_campaign_data`:

```python
    if "blocked_evidence_phrases" in personalization:
        _require_string_list(
            personalization,
            "blocked_evidence_phrases",
            allow_empty=True,
            label="personalization.blocked_evidence_phrases",
        )
```

`pipeline.py` — in `_candidate_facts`, after building `candidates` and before the dedupe loop:

```python
    blocked = campaign.blocked_evidence_phrases
    if blocked:
        candidates = [
            candidate
            for candidate in candidates
            if not any(
                phrase in candidate.evidence.casefold() for phrase in blocked
            )
        ]
```

`extract.py` — delete the three CBP lines from `_BLOCKED_PHRASES` (lines 27–29) and replace line 410's piano rewrite with `focus = lowered`.

`site.py` — `SIGNAL_ENGINE_VERSION = "9"` (extraction output changed; caches must invalidate).

`config/campaign.schema.json` — add under `personalization.properties` (not `required`):

```json
"blocked_evidence_phrases": {
  "type": "array",
  "items": {"type": "string", "minLength": 1},
  "uniqueItems": true
}
```

`campaigns/campaign-template.json` — add `"blocked_evidence_phrases": [],` after `"banned_phrases": [...]`.

- [ ] **Step 4: Run full suite** — update `tests/test_extract.py` expectations at lines 67-68 and 98-100 (the piano rewrite no longer applies: expected focus keeps `sale of pianos`; the `since 1920` case already fails on the metric gate and stays as-is if unaffected). Check `tests/test_focus.py:22-25` still passes (its rule pattern matches evidence directly, so it should).
- [ ] **Step 5: Commit** — `move evidence blocking to campaign config and drop engine fossils`

---

### Task 7: Focus-gap reporting in the manifest

**Files:**
- Modify: `src/bulk_enrich/pipeline.py` — in `run_enrichment`, before the `manifest` dict is built (~line 1391); add a `"focus_gaps"` manifest key
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `manifest["focus_gaps"] = {"domains": int, "samples": [{"domain","rule","tier","source_focus","evidence"}]}` — max 25 samples, one per unique domain.

- [ ] **Step 1: Write the failing test** — extend the existing `FailedDomainEnricher`-style run test, or add:

```python
def test_manifest_reports_focus_gaps_for_unmatched_domains(self) -> None:
    # Same harness as test_title_hook_gap_fails_only_the_row, but with
    # FailedDomainEnricher() and the repo's default config/title-hooks.csv.
    ...
    gaps = manifest["focus_gaps"]
    self.assertEqual(gaps["domains"], 1)
    self.assertEqual(gaps["samples"][0]["domain"], "northstar.example")
    self.assertEqual(gaps["samples"][0]["rule"], "no-company-evidence")
```

(Write the harness out in full by copying Task 3's test body; swap the enricher and hooks path, drop the row assertions.)

- [ ] **Step 2: Run to verify failure** — KeyError `focus_gaps`.

- [ ] **Step 3: Implement** — after `company_rule_counts` is computed (~line 1395):

```python
    focus_gap_samples: list[dict[str, str]] = []
    focus_gap_domains: set[str] = set()
    for row, domain in zip(output_rows, domains, strict=True):
        if not domain or domain in focus_gap_domains:
            continue
        rule = row.get("company_fit_rule", "")
        tier = row.get("company_fit_tier", "")
        if rule in {"no-company-evidence", "generic-compression"} or tier == "exclude":
            focus_gap_domains.add(domain)
            if len(focus_gap_samples) < 25:
                focus_gap_samples.append(
                    {
                        "domain": domain,
                        "rule": rule,
                        "tier": tier,
                        "source_focus": row.get("personalization_source_focus", ""),
                        "evidence": row.get("company_fit_evidence", "")[:240],
                    }
                )
```

and add to the manifest dict, after `"quality": quality_report,`:

```python
        "focus_gaps": {
            "domains": len(focus_gap_domains),
            "samples": focus_gap_samples,
        },
```

- [ ] **Step 4: Run full suite** → PASS.
- [ ] **Step 5: Commit** — `report focus-rule gaps in the run manifest`

---

### Task 8: Cache pruning

**Files:**
- Modify: `src/bulk_enrich/cache.py` (new `prune` method), `src/bulk_enrich/cli.py` (`--prune-cache` flag)
- Test: `tests/test_cache.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `JsonCache.prune(*, ttl_hours: float) -> int` (entries removed).

- [ ] **Step 1: Write the failing test** — in `tests/test_cache.py`:

```python
def test_prune_removes_only_expired_entries(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cache = JsonCache(tmp)
        with patch("bulk_enrich.cache.time.time", return_value=1_000.0):
            cache.put("http-success", "old", {"value": 1})
        cache.put("http-success", "fresh", {"value": 2})
        removed = cache.prune(ttl_hours=1.0)
        self.assertEqual(removed, 1)
        self.assertIsNone(cache.get("http-success", "old", ttl_hours=1e9))
        self.assertIsNotNone(cache.get("http-success", "fresh", ttl_hours=1.0))
```

(Add `from unittest.mock import patch` and `tempfile` imports if the file lacks them.)

- [ ] **Step 2: Run to verify failure** — AttributeError: no `prune`.

- [ ] **Step 3: Implement** — in `JsonCache`:

```python
    def prune(self, *, ttl_hours: float) -> int:
        """Delete cache entries stored before the TTL window; returns count removed."""
        cutoff = time.time() - ttl_hours * 3600
        removed = 0
        for path in self.root.glob("*/??/*.json.gz"):
            stored_at = 0.0
            try:
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    raw = json.load(handle).get("stored_at", 0.0)
                if isinstance(raw, (int, float)):
                    stored_at = float(raw)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                stored_at = 0.0
            if stored_at < cutoff:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
        return removed
```

In `cli.py`: add `from bulk_enrich.cache import JsonCache` and the flag:

```python
    parser.add_argument(
        "--prune-cache",
        action="store_true",
        help="Delete cache entries older than --cache-ttl-hours before running",
    )
```

In `main`, right after `args = parser.parse_args(argv)` succeeds and before the campaign load:

```python
    if args.prune_cache:
        removed = JsonCache(Path(args.cache_dir).expanduser().resolve()).prune(
            ttl_hours=args.cache_ttl_hours
        )
        print(f"pruned {removed} expired cache entries", file=sys.stderr)
```

- [ ] **Step 4: Run full suite**; add a `tests/test_cli.py` case asserting `--prune-cache --validate-only` exits 0 and prints the pruned line to stderr (mirror the file's existing CLI invocation style).
- [ ] **Step 5: Commit** — `add cache pruning via --prune-cache`

---

### Task 9: Cleanups — version strings, single CSV parse, stray file

**Files:**
- Modify: `src/bulk_enrich/fetcher.py`, `src/bulk_enrich/firecrawl.py`, `src/bulk_enrich/csv_io.py`, `src/bulk_enrich/pipeline.py`
- Delete: `inspect-output.mjs`
- Test: existing suites (behavior-neutral)

- [ ] **Step 1: User agents** — `fetcher.py`: add `from bulk_enrich import __version__` and:

```python
DEFAULT_USER_AGENT = (
    f"Mozilla/5.0 (compatible; BulkEnrich/{__version__}; "
    "deterministic public-site enrichment)"
)
```

`firecrawl.py`: same import; header becomes `"User-Agent": f"BulkEnrich/{__version__} Firecrawl fallback"`.

- [ ] **Step 2: Single CSV parse** — in `csv_io.py`, split `inspect_csv`:

```python
def summarize_csv(data: CSVData) -> CSVSummary:
    # move the current body of inspect_csv here, minus the load_csv call

def inspect_csv(path: str | Path) -> CSVSummary:
    return summarize_csv(load_csv(path))
```

In `pipeline.py`, import `summarize_csv` and change the manifest input block from `**inspect_csv(input_resolved).to_dict(),` to `**summarize_csv(data).to_dict(),`.

- [ ] **Step 3: Remove stray file** — `git rm inspect-output.mjs`.

- [ ] **Step 4: Run full suite** → PASS.
- [ ] **Step 5: Commit** — `interpolate tool version and reuse the parsed input csv`

---

### Task 10: Docs, skill, and release

**Files:**
- Modify: `README.md`, `docs/OUTPUTS.md`, `docs/IMPLEMENTATION_STATUS.md`, `skill/bulk-outreach-personalizer/SKILL.md`, `skill/bulk-outreach-personalizer/references/usage.md`, `src/bulk_enrich/__init__.py`, `pyproject.toml`

- [ ] **Step 1: README.md** — in the feature list and Quality-gate section: batch repetition now holds only the deterministic over-cap overflow for review; cache section gains one line for `--prune-cache`; correct the review-output sentence to "reviewable rows (rendered copy where personalisation succeeded; `low_confidence_action: \"blank\"` rows carry evidence only)".

- [ ] **Step 2: docs/OUTPUTS.md** — manifest bullet list: add `focus-rule gap summary with per-domain evidence samples`; note share warnings carry `count` (all affected) and `flagged` (overflow demoted).

- [ ] **Step 3: docs/IMPLEMENTATION_STATUS.md** — Core engine list: add `Campaign-level blocked evidence phrases`, `Overflow-only batch repetition routing`, `Focus-gap manifest reporting`, `Cache pruning`. Deliberate boundaries: add three lines — `robots.txt is not consulted; the engine fetches at most a few public pages per domain and the operator owns crawl-policy decisions`, `public-IP validation resolves DNS separately from the connection (a rebinding window is accepted for this outbound-only threat model)`, `balanced CTA/offer-line assignment is deterministic per identical batch; changing the batch's rendered-domain set re-balances assignments`.

- [ ] **Step 4: Skill files** — `SKILL.md` workflow: after step 5 insert `6. Review the manifest's focus_gaps samples; add or adjust focus rules for genuinely in-market unmatched domains, then re-run the test (cached pages make re-runs cheap).` and renumber. `references/usage.md`: document `personalization.blocked_evidence_phrases` (site-specific junk sentences that must never become evidence), the `focus_gaps` manifest object, and `--prune-cache`.

- [ ] **Step 5: Version bump** — `src/bulk_enrich/__init__.py` and `pyproject.toml` → `0.10.0`.

- [ ] **Step 6: Run full suite** → PASS. Also run a smoke: `python scripts/enrich.py --input tests/fixtures/leads.csv --output /tmp-scratch/out.csv --campaign campaigns/examples/scale-olympus.json --validate-only` (use the session scratchpad path) and confirm exit 0.

- [ ] **Step 7: Commit** — `release production hardening v0.10.0`

---

## Self-review notes

- Findings 1–10 → Tasks 1–10 one-to-one (finding 9 spans Task 9; finding 10 spans Task 10).
- Task 3/7 share a run-test harness; Task 7's step 1 instructs copying Task 3's body in full — executor must not leave the `...` in place.
- Types: `_overflow_domains` returns `tuple[str, ...]`; `prune` returns `int`; `resolve_charset` returns `str`; `summarize_csv` returns `CSVSummary`. All consumers shown use these.

## Unresolved questions (for Dylan)

1. **Batch QA semantics (Task 5):** OK to change from "demote all affected" to "demote overflow only"? Fewer manual reviews, same warning visibility. If you prefer current behavior, drop Task 5 and I'll only add the `flagged` count.
2. **robots.txt:** plan documents non-consultation as a deliberate boundary. Want an opt-in `--respect-robots` instead? (~1 extra task, stdlib `urllib.robotparser`, cached per domain.)
3. **Blocked-evidence default (Task 6):** template ships `[]`. Want a starter list of universal junk (e.g. "click here to", "your browser does not support") seeded in the template? Engine keeps its own generic list either way.
4. **Deferred refactors:** pipeline decomposition + typed `CampaignConfig` intentionally excluded (churn before your review). Schedule after production sign-off?
5. **Version:** bumping to 0.10.0 (behavior changes in QA routing + extraction). Prefer 1.0.0 once you finish production readiness instead?
