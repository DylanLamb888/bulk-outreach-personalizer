import contextlib
import csv
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bulk_enrich.cache import JsonCache
from bulk_enrich.cli import main
from bulk_enrich.config import CampaignConfigError, load_campaign
from bulk_enrich.focus import CommercialFocusTable
from bulk_enrich.hooks import TitleHookTable
from bulk_enrich.llm_focus import (
    BatchAnthropicTransport,
    ClaudeCodeTransport,
    CodexTransport,
    LlmFocusClassifier,
    LlmFocusDecision,
    LlmFocusError,
    LlmFocusItem,
    LlmFocusSettings,
    LlmRequest,
    PhraseLimits,
    SyncAnthropicTransport,
    TransportResult,
    build_request_params,
    build_system_prompt,
    build_transport,
    estimate_nominal_usd,
    evidence_quote_is_verbatim,
    model_allowed,
    provider_ready,
    validate_decision,
    validate_pitch,
)
from bulk_enrich.models import CompanyFact, SiteSignal
from bulk_enrich.pipeline import RunOptions, run_company_qualification, run_digests, run_enrichment


ROOT = Path(__file__).resolve().parents[1]
LIMITS = PhraseLimits(max_focus_words=7, max_buyer_phrase_words=8, banned_phrases=("saw that",))
SETTINGS = LlmFocusSettings(
    enabled=True,
    icp="B2B service companies that sell to other businesses",
    exclusions="law firms, private equity, associations",
    provider="api",
)
SITE_TEXT = (
    "[https://benskin.example/]\n"
    "Title: Home - Midwest Executive Search - Benskin Talent Partners\n"
    "Description: Benskin Talent Partners leads Midwest executive search in accounting, "
    "finance, HR, and supply chain with a relationship-first approach.\n"
    "Heading: Where Executive Search Meets True Alignment"
)
GOOD_RAW = {
    "fit_tier": "core",
    "signal_type": "service",
    "focus": "executive search",
    "buyer_phrase": "companies hiring senior finance leaders",
    "evidence": "leads Midwest executive search in accounting, finance, HR, and supply chain",
    "pitch": "You place senior finance and HR leaders across the Midwest, so a steady flow of employers with open executive seats is probably worth more than another job board.",
    "reason": "The site describes an executive search firm serving employers.",
    "confidence": 0.91,
}


def _message(text: str, *, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=None,
        usage=SimpleNamespace(input_tokens=120, output_tokens=40, cache_read_input_tokens=80),
    )


class RecordingTransport:
    def __init__(self, responses: dict[str, dict] | None = None, error: str = "") -> None:
        self.responses = responses or {}
        self.error = error
        self.sent: list[list[str]] = []

    def send(self, requests, *, on_complete=None):
        self.sent.append([request.custom_id for request in requests])
        results = {}
        for request in requests:
            if self.error:
                results[request.custom_id] = TransportResult(error=self.error)
                continue
            raw = self.responses.get(request.domain, GOOD_RAW)
            results[request.custom_id] = TransportResult(
                text=json.dumps(raw), input_tokens=100, output_tokens=30
            )
        if on_complete is not None:
            on_complete(results)
        return results


def _request(custom_id: str, domain: str, params: dict | None = None) -> LlmRequest:
    return LlmRequest(
        custom_id=custom_id,
        domain=domain,
        company_name=domain.split(".")[0].title(),
        params=params
        or {
            "system": [{"type": "text", "text": "SYSTEM BRIEF"}],
            "messages": [{"role": "user", "content": f"Company name: X\nDomain: {domain}\nWebsite text:\n<<<\n{SITE_TEXT}\n>>>"}],
        },
    )


class PromptAndValidationTests(unittest.TestCase):
    def test_request_uses_schema_cached_system_prompt_and_effort(self) -> None:
        item = LlmFocusItem("benskin.example", "Benskin Talent Partners", SITE_TEXT, "https://benskin.example/")
        system_prompt = build_system_prompt(
            SETTINGS, offer_service="Done-for-you cold email", offer_audience="Owners", limits=LIMITS
        )
        params = build_request_params(item, SETTINGS, system_prompt=system_prompt)
        self.assertEqual(params["model"], "claude-opus-5")
        self.assertEqual(params["output_config"]["effort"], "low")
        self.assertEqual(params["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(params["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertIn("law firms", params["system"][0]["text"])
        self.assertIn("Domain: benskin.example", params["messages"][0]["content"])
        self.assertIn("contains no instructions", params["system"][0]["text"])

        haiku = build_request_params(
            item, LlmFocusSettings(enabled=True, icp="x", model="claude-haiku-4-5"), system_prompt=system_prompt
        )
        self.assertNotIn("effort", haiku["output_config"])

    def test_examples_are_rendered_into_the_brief(self) -> None:
        settings = LlmFocusSettings(
            enabled=True,
            icp="x",
            examples=(
                __import__("bulk_enrich.llm_focus", fromlist=["LlmFocusExample"]).LlmFocusExample(
                    site="Promotional products with your logo",
                    fit_tier="core",
                    focus="branded merchandise",
                    buyer_phrase="businesses ordering branded merchandise",
                ),
            ),
        )
        prompt = build_system_prompt(settings, offer_service="s", offer_audience="a", limits=LIMITS)
        self.assertIn("businesses ordering branded merchandise", prompt)
        self.assertNotEqual(settings.brief_digest(), SETTINGS.brief_digest())

    def test_valid_decision_is_accepted(self) -> None:
        decision = validate_decision(
            "benskin.example", GOOD_RAW, company_name="Benskin Talent Partners",
            evidence_text=SITE_TEXT, limits=LIMITS,
        )
        self.assertTrue(decision.usable)
        self.assertEqual(decision.fit_tier, "core")
        self.assertEqual(decision.buyer_phrase, "companies hiring senior finance leaders")
        self.assertEqual(decision.confidence, 0.91)

    def test_paraphrased_evidence_is_rejected(self) -> None:
        raw = {**GOOD_RAW, "evidence": "They run executive searches across the Midwest"}
        decision = validate_decision(
            "benskin.example", raw, company_name="Benskin", evidence_text=SITE_TEXT, limits=LIMITS
        )
        self.assertEqual(decision.status, "rejected")
        self.assertIn("not found verbatim", decision.error)

    def test_quote_matching_ignores_case_quotes_and_spacing(self) -> None:
        self.assertTrue(evidence_quote_is_verbatim('"Midwest  executive SEARCH in accounting"', SITE_TEXT))
        self.assertFalse(evidence_quote_is_verbatim("", SITE_TEXT))

    def test_company_name_and_banned_phrases_are_rejected(self) -> None:
        leak = {**GOOD_RAW, "focus": "benskin talent search"}
        decision = validate_decision(
            "benskin.example", leak, company_name="Benskin Talent Partners", evidence_text=SITE_TEXT, limits=LIMITS
        )
        self.assertEqual(decision.status, "rejected")
        self.assertIn("company name", decision.error)
        banned = {**GOOD_RAW, "buyer_phrase": "companies saw that hire"}
        decision = validate_decision(
            "benskin.example", banned, company_name="Other", evidence_text=SITE_TEXT, limits=LIMITS
        )
        self.assertIn("banned phrase", decision.error)

    def test_exclusion_survives_unusable_phrases(self) -> None:
        raw = {
            **GOOD_RAW,
            "fit_tier": "exclude",
            "focus": "the best, leading law firm and more",
            "buyer_phrase": "",
        }
        decision = validate_decision(
            "firm.example", raw, company_name="Firm", evidence_text=SITE_TEXT, limits=LIMITS
        )
        self.assertTrue(decision.usable)
        self.assertEqual(decision.fit_tier, "exclude")
        self.assertEqual(decision.focus, "outside campaign target")

    def test_overlong_phrases_and_bad_tier_are_rejected(self) -> None:
        raw = {**GOOD_RAW, "fit_tier": "maybe", "buyer_phrase": "a b c d e f g h i j k"}
        decision = validate_decision(
            "x.example", raw, company_name="X", evidence_text=SITE_TEXT, limits=LIMITS
        )
        self.assertEqual(decision.status, "rejected")
        self.assertIn("fit_tier", decision.error)
        self.assertIn("exceeds 8 words", decision.error)


class ClassifierTests(unittest.TestCase):
    def _classifier(self, tmp: str, transport) -> LlmFocusClassifier:
        return LlmFocusClassifier(
            SETTINGS,
            cache=JsonCache(Path(tmp) / "cache"),
            transport=transport,
            offer_service="Done-for-you cold email",
            offer_audience="Owners",
            limits=LIMITS,
        )

    def test_decisions_are_cached_by_domain_and_brief(self) -> None:
        item = LlmFocusItem("benskin.example", "Benskin Talent Partners", SITE_TEXT, "https://benskin.example/")
        with tempfile.TemporaryDirectory() as tmp:
            transport = RecordingTransport()
            classifier = self._classifier(tmp, transport)
            first = classifier.classify([item])["benskin.example"]
            self.assertTrue(first.usable)
            self.assertFalse(first.from_cache)
            second = self._classifier(tmp, transport).classify([item])["benskin.example"]
            self.assertTrue(second.from_cache)
            self.assertEqual(len(transport.sent), 1)
            stats = classifier.stats()
            self.assertEqual(stats["sent"], 1)
            self.assertEqual(stats["input_tokens"], 100)

            changed = LlmFocusClassifier(
                LlmFocusSettings(enabled=True, icp="a different market"),
                cache=JsonCache(Path(tmp) / "cache"),
                transport=transport,
                offer_service="s",
                offer_audience="a",
                limits=LIMITS,
            )
            changed.classify([item])
            self.assertEqual(len(transport.sent), 2)

    def test_cache_does_not_reuse_decisions_after_request_or_validation_changes(self) -> None:
        item = LlmFocusItem(
            "benskin.example", "Benskin Talent Partners", SITE_TEXT,
            "https://benskin.example/", job_title="Founder",
        )
        old_pitch = "You recruit finance leaders, so employers with open roles could be a useful next conversation."
        new_pitch = "You connect finance leaders with employers planning their next hire."
        changes = {
            "offer": {"offer_service": "Recruitment software"},
            "audience": {"offer_audience": "Finance directors"},
            "approved claims": {"approved_claims": ("A free trial is available.",)},
            "forbidden claims": {"forbidden_claims": ("Do not promise a free trial.",)},
            "banned phrases": {"limits": replace(LIMITS, banned_phrases=("recruit",))},
            "pitch limit": {"limits": replace(LIMITS, max_pitch_words=12)},
            "source overlap limit": {"limits": replace(LIMITS, max_source_phrase_words=3)},
            "evidence truncation": {"settings": replace(SETTINGS, max_evidence_chars=200)},
            "company name": {"item": replace(item, company_name="Renamed Partners")},
            "contact title": {"item": replace(item, job_title="Finance Director")},
            "source URL": {"item": replace(item, source_url="https://benskin.example/services")},
            "redirect note": {"item": replace(item, note="Check whether this is the same company.")},
        }
        for label, overrides in changes.items():
            with self.subTest(change=label), tempfile.TemporaryDirectory() as tmp:
                transport = RecordingTransport({item.domain: {**GOOD_RAW, "pitch": old_pitch}})
                kwargs = {
                    "settings": SETTINGS,
                    "cache": JsonCache(Path(tmp) / "cache"),
                    "transport": transport,
                    "offer_service": "Done-for-you cold email",
                    "offer_audience": "Owners",
                    "limits": LIMITS,
                }
                first = LlmFocusClassifier(**kwargs).classify([item])[item.domain]
                self.assertEqual(first.pitch, old_pitch)
                transport.responses[item.domain] = {**GOOD_RAW, "pitch": new_pitch}
                unchanged = LlmFocusClassifier(**kwargs).classify([item])[item.domain]
                self.assertTrue(unchanged.from_cache)
                self.assertEqual(unchanged.pitch, old_pitch)
                self.assertEqual(len(transport.sent), 1)

                changed_item = overrides.get("item", item)
                kwargs.update({key: value for key, value in overrides.items() if key != "item"})
                updated = LlmFocusClassifier(**kwargs).classify([changed_item])[item.domain]
                self.assertFalse(updated.from_cache)
                self.assertEqual(updated.pitch, new_pitch)
                self.assertEqual(len(transport.sent), 2)
                repeated = LlmFocusClassifier(**kwargs).classify([changed_item])[item.domain]
                self.assertTrue(repeated.from_cache)
                self.assertEqual(repeated.pitch, new_pitch)
                self.assertEqual(len(transport.sent), 2)

    def test_transport_errors_are_reported_and_not_cached(self) -> None:
        item = LlmFocusItem("benskin.example", "Benskin", SITE_TEXT, "https://benskin.example/")
        with tempfile.TemporaryDirectory() as tmp:
            transport = RecordingTransport(error="APIConnectionError: boom")
            decision = self._classifier(tmp, transport).classify([item])["benskin.example"]
            self.assertEqual(decision.status, "error")
            self.assertIn("boom", decision.error)
            self._classifier(tmp, transport).classify([item])
            self.assertEqual(len(transport.sent), 2)

    def test_blank_evidence_is_never_sent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transport = RecordingTransport()
            decisions = self._classifier(tmp, transport).classify(
                [LlmFocusItem("empty.example", "Empty", "   ", "https://empty.example/")]
            )
            self.assertEqual(decisions, {})
            self.assertEqual(transport.sent, [])


class TransportTests(unittest.TestCase):
    def test_sync_transport_parses_messages_and_refusals(self) -> None:
        calls: list[dict] = []

        def create(**params):
            calls.append(params)
            if "refuse.example" in params["messages"][0]["content"]:
                return _message("", stop_reason="refusal")
            return _message(json.dumps(GOOD_RAW))

        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        transport = SyncAnthropicTransport(concurrency=2, client=client)
        results = transport.send(
            [
                _request("a", "ok.example", {"messages": [{"role": "user", "content": "Domain: ok.example"}]}),
                _request("b", "refuse.example", {"messages": [{"role": "user", "content": "Domain: refuse.example"}]}),
            ]
        )
        self.assertEqual(json.loads(results["a"].text)["focus"], "executive search")
        self.assertEqual(results["a"].cache_read_input_tokens, 80)
        self.assertIn("declined", results["b"].error)
        self.assertEqual(len(calls), 2)

    def test_batch_transport_submits_polls_and_collects(self) -> None:
        statuses = iter(["in_progress", "ended"])
        created: list[dict] = []

        def create(requests):
            created.append(requests)
            return SimpleNamespace(id="msgbatch_1")

        def retrieve(batch_id):
            return SimpleNamespace(
                processing_status=next(statuses),
                request_counts=SimpleNamespace(processing=1),
            )

        def results(batch_id):
            yield SimpleNamespace(
                custom_id="a",
                result=SimpleNamespace(type="succeeded", message=_message(json.dumps(GOOD_RAW))),
            )
            yield SimpleNamespace(
                custom_id="b",
                result=SimpleNamespace(type="errored", error=SimpleNamespace(type="invalid_request")),
            )

        client = SimpleNamespace(
            messages=SimpleNamespace(
                batches=SimpleNamespace(create=create, retrieve=retrieve, results=results)
            )
        )
        logs: list[str] = []
        transport = BatchAnthropicTransport(
            poll_seconds=1, client=client, log=logs.append, sleep=lambda seconds: None
        )
        output = transport.send([_request("a", "a.example", {"x": 1}), _request("b", "b.example", {"x": 2})])
        self.assertEqual(created[0][0], {"custom_id": "a", "params": {"x": 1}})
        self.assertEqual(json.loads(output["a"].text)["fit_tier"], "core")
        self.assertIn("invalid_request", output["b"].error)
        self.assertEqual(transport.batch_ids, ["msgbatch_1"])
        self.assertTrue(any("msgbatch_1" in line for line in logs))

    def test_batch_transport_reuses_an_existing_batch(self) -> None:
        def results(batch_id):
            yield SimpleNamespace(
                custom_id="a",
                result=SimpleNamespace(type="succeeded", message=_message(json.dumps(GOOD_RAW))),
            )

        client = SimpleNamespace(
            messages=SimpleNamespace(
                batches=SimpleNamespace(
                    create=lambda requests: self.fail("must not submit"),
                    retrieve=lambda batch_id: SimpleNamespace(processing_status="ended"),
                    results=results,
                )
            )
        )
        transport = BatchAnthropicTransport(
            client=client, existing_batch_ids=("msgbatch_old",), sleep=lambda seconds: None
        )
        output = transport.send([_request("a", "a.example", {"x": 1})])
        self.assertEqual(json.loads(output["a"].text)["focus"], "executive search")


def _claude_envelope(results: list[dict], *, is_error: bool = False, result: str = "") -> str:
    return json.dumps(
        {
            "type": "result",
            "is_error": is_error,
            "result": result or json.dumps({"results": results}),
            "structured_output": None if is_error else {"results": results},
            "usage": {
                "input_tokens": 2,
                "cache_creation_input_tokens": 3000,
                "cache_read_input_tokens": 0,
                "output_tokens": 300,
            },
        }
    )


class ClaudeCodeTransportTests(unittest.TestCase):
    def test_completed_chunks_survive_fatal_error_and_resume_without_rebilling(self) -> None:
        for concurrency in (1, 2):
            with self.subTest(concurrency=concurrency), tempfile.TemporaryDirectory() as tmp:
                cache = JsonCache(Path(tmp) / "cache")
                items = [
                    LlmFocusItem(f"site{i}.example", "Acme", SITE_TEXT, f"https://site{i}.example/")
                    for i in range(2)
                ]
                calls = []
                fail = True

                def run(argv, **kwargs):
                    domain = next(line[8:] for line in argv[-1].splitlines() if line.startswith("Domain: "))
                    calls.append(domain)
                    if domain == "site1.example" and fail:
                        if concurrency == 1:
                            self.assertIsNotNone(cache.get("llm-focus", classifier.cache_key(items[0]), ttl_hours=720))
                        raise FileNotFoundError("simulated fatal CLI failure")
                    envelope = json.loads(_claude_envelope([{"domain": domain, **GOOD_RAW}]))
                    envelope["total_cost_usd"] = 0.6
                    return SimpleNamespace(returncode=0, stdout=json.dumps(envelope), stderr="")

                def fresh():
                    return LlmFocusClassifier(
                        LlmFocusSettings(enabled=True, icp="x"), cache=cache,
                        transport=ClaudeCodeTransport(
                            model="claude-opus-5", binary="/fake/claude", run=run,
                            domains_per_call=1, concurrency=concurrency, max_nominal_usd=1.0,
                        ),
                        offer_service="s", offer_audience="a", limits=LIMITS,
                    )

                classifier = fresh()
                with self.assertRaises(LlmFocusError):
                    classifier.classify(items)
                self.assertAlmostEqual(classifier.stats()["nominal_cost_usd"], 0.6)
                self.assertIsNotNone(cache.get("llm-focus", classifier.cache_key(items[0]), ttl_hours=720))
                fail = False
                calls.clear()
                resumed = fresh()
                decisions = resumed.classify(items)
                self.assertEqual(calls, ["site1.example"])
                self.assertTrue(decisions["site0.example"].from_cache)
                self.assertFalse(decisions["site1.example"].from_cache)
                self.assertAlmostEqual(resumed.stats()["nominal_cost_usd"], 0.6)
                calls.clear()
                repeated = fresh()
                repeated.classify(items)
                self.assertEqual(calls, [])
                self.assertEqual(repeated.stats()["nominal_cost_usd"], 0)

    def test_parallel_checkpoints_keep_cumulative_budget_and_stop_next_wave(self) -> None:
        calls = []
        checkpoints = []

        def run(argv, **kwargs):
            domain = next(line[8:] for line in argv[-1].splitlines() if line.startswith("Domain: "))
            calls.append(domain)
            envelope = json.loads(_claude_envelope([{"domain": domain, **GOOD_RAW}]))
            envelope["total_cost_usd"] = 0.6
            return SimpleNamespace(returncode=0, stdout=json.dumps(envelope), stderr="")

        transport = ClaudeCodeTransport(
            model="claude-opus-5", binary="/fake/claude", run=run,
            domains_per_call=1, concurrency=2, max_nominal_usd=1.0,
        )
        results = transport.send(
            [_request(str(i), f"site{i}.example") for i in range(4)],
            on_complete=lambda chunk: checkpoints.extend(chunk),
        )
        self.assertEqual(len(calls), 2)
        self.assertCountEqual(checkpoints, ["0", "1"])
        self.assertAlmostEqual(transport.budget.spent_usd, 1.2)
        self.assertIn("budget", results["2"].error)

    def _fake_run(self, calls: list[list[str]], stdout_for):
        def run(argv, **kwargs):
            calls.append(argv)
            self.assertEqual(kwargs["stdin"], -3)  # subprocess.DEVNULL
            self.assertTrue(Path(kwargs["cwd"]).is_dir())
            self.assertEqual(sorted(Path(kwargs["cwd"]).iterdir()), [])
            return SimpleNamespace(returncode=0, stdout=stdout_for(argv), stderr="")
        return run

    def test_packs_domains_per_call_and_isolates_the_session(self) -> None:
        calls: list[list[str]] = []

        def stdout_for(argv):
            prompt = argv[-1]
            domains = [line.split("Domain: ", 1)[1] for line in prompt.splitlines() if line.startswith("Domain: ")]
            return _claude_envelope([{"domain": d, **GOOD_RAW} for d in domains])

        transport = ClaudeCodeTransport(
            model="claude-opus-5", effort="low", domains_per_call=2, concurrency=1,
            binary="/fake/claude", run=self._fake_run(calls, stdout_for),
        )
        requests = [_request(f"id{i}", f"site{i}.example") for i in range(3)]
        results = transport.send(requests)
        self.assertEqual(len(calls), 2)
        self.assertEqual(transport.calls, 2)
        argv = calls[0]
        self.assertEqual(argv[0], "/fake/claude")
        self.assertIn("-p", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual(argv[argv.index("--mcp-config") + 1], '{"mcpServers":{}}')
        self.assertIn("--no-session-persistence", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "claude-opus-5")
        self.assertEqual(argv[argv.index("--effort") + 1], "low")
        self.assertEqual(argv[argv.index("--system-prompt") + 1], "SYSTEM BRIEF")
        self.assertIn('"results"', argv[argv.index("--json-schema") + 1])
        self.assertIn("### Company 2", argv[-1])
        self.assertNotIn("### Company 3", argv[-1])
        for request in requests:
            self.assertEqual(json.loads(results[request.custom_id].text)["focus"], "executive search")
            self.assertNotIn("domain", json.loads(results[request.custom_id].text))
        self.assertEqual(results["id0"].input_tokens + results["id1"].input_tokens, 3002)

    def test_missing_domain_and_errors_are_reported(self) -> None:
        calls: list[list[str]] = []
        transport = ClaudeCodeTransport(
            model="claude-opus-5", binary="/fake/claude",
            run=self._fake_run(calls, lambda argv: _claude_envelope([{"domain": "site0.example", **GOOD_RAW}])),
        )
        results = transport.send([_request("id0", "site0.example"), _request("id1", "site1.example")])
        self.assertEqual(results["id0"].error, "")
        self.assertIn("did not include this domain", results["id1"].error)

        broken = ClaudeCodeTransport(
            model="claude-opus-5", binary="/fake/claude",
            run=lambda argv, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="boom"),
        )
        self.assertIn("without a JSON result", broken.send([_request("id0", "site0.example")])["id0"].error)

    def test_not_logged_in_stops_the_run(self) -> None:
        transport = ClaudeCodeTransport(
            model="claude-opus-5", binary="/fake/claude",
            run=lambda argv, **kwargs: SimpleNamespace(
                returncode=0, stdout=_claude_envelope([], is_error=True, result="Not logged in · Please run /login"), stderr=""
            ),
        )
        with self.assertRaisesRegex(LlmFocusError, "not logged in"):
            transport.send([_request("id0", "site0.example")])

    def test_haiku_omits_effort_and_missing_binary_is_clear(self) -> None:
        transport = ClaudeCodeTransport(model="claude-haiku-4-5", binary="/fake/claude", run=lambda argv, **kwargs: SimpleNamespace(returncode=0, stdout=_claude_envelope([]), stderr=""))
        transport.send([_request("id0", "site0.example")])

        def missing(argv, **kwargs):
            raise FileNotFoundError(argv[0])

        with self.assertRaisesRegex(LlmFocusError, "'claude' command"):
            ClaudeCodeTransport(model="claude-opus-5", binary="/nowhere/claude", run=missing).send([_request("id0", "site0.example")])


class CodexTransportTests(unittest.TestCase):
    def test_reads_last_message_file_and_strips_fences(self) -> None:
        calls: list[list[str]] = []

        def run(argv, **kwargs):
            calls.append(argv)
            output_path = Path(argv[argv.index("--output-last-message") + 1])
            output_path.write_text("```json\n" + json.dumps({"results": [{"domain": "site0.example", **GOOD_RAW}]}) + "\n```", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        transport = CodexTransport(model="gpt-5", binary="/fake/codex", run=run)
        results = transport.send([_request("id0", "site0.example")])
        argv = calls[0]
        self.assertEqual(argv[:2], ["/fake/codex", "exec"])
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertEqual(argv[argv.index("--model") + 1], "gpt-5")
        self.assertIn("SYSTEM BRIEF", argv[-1])
        self.assertIn('"results"', argv[-1])
        self.assertEqual(json.loads(results["id0"].text)["fit_tier"], "core")
        self.assertFalse(Path(argv[argv.index("--output-last-message") + 1]).exists())

    def test_login_failure_stops_the_run(self) -> None:
        transport = CodexTransport(
            model="gpt-5", binary="/fake/codex",
            run=lambda argv, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="Please run codex login first"),
        )
        with self.assertRaisesRegex(LlmFocusError, "codex login"):
            transport.send([_request("id0", "site0.example")])


class ProviderTests(unittest.TestCase):
    def test_build_transport_matches_provider(self) -> None:
        self.assertIsInstance(build_transport(LlmFocusSettings(enabled=True, icp="x", provider="claude-code")), ClaudeCodeTransport)
        self.assertIsInstance(build_transport(LlmFocusSettings(enabled=True, icp="x", provider="codex")), CodexTransport)
        self.assertIsInstance(build_transport(LlmFocusSettings(enabled=True, icp="x", provider="api")), SyncAnthropicTransport)
        self.assertIsInstance(build_transport(LlmFocusSettings(enabled=True, icp="x", provider="api"), mode="batch"), BatchAnthropicTransport)

    def test_provider_ready_checks_binaries_and_credentials(self) -> None:
        with patch("bulk_enrich.llm_focus.shutil.which", return_value=None):
            ready, blocker = provider_ready(LlmFocusSettings(enabled=True, icp="x", provider="claude-code"), {})
            self.assertFalse(ready)
            self.assertIn("claude", blocker)
        with patch("bulk_enrich.llm_focus.shutil.which", return_value="/usr/bin/claude"):
            self.assertTrue(provider_ready(LlmFocusSettings(enabled=True, icp="x", provider="claude-code"), {})[0])
        ready, blocker = provider_ready(LlmFocusSettings(enabled=True, icp="x", provider="api"), {})
        self.assertFalse(ready)
        self.assertRegex(blocker, "credentials|SDK")


class PitchAndGuardTests(unittest.TestCase):
    def test_good_pitch_survives_validation(self) -> None:
        decision = validate_decision(
            "benskin.example", GOOD_RAW, company_name="Benskin Talent Partners",
            evidence_text=SITE_TEXT, limits=LIMITS,
        )
        self.assertTrue(decision.pitch.startswith("You place"))
        self.assertEqual(decision.pitch_error, "")

    def test_bad_pitch_is_dropped_but_decision_kept(self) -> None:
        raw = {**GOOD_RAW, "pitch": "I noticed you do executive search in the Midwest and wondered about it."}
        decision = validate_decision("benskin.example", raw, company_name="Benskin", evidence_text=SITE_TEXT, limits=LIMITS)
        self.assertTrue(decision.usable)
        self.assertEqual(decision.pitch, "")
        self.assertIn("research announcement", decision.pitch_error)

    def test_pitch_gates(self) -> None:
        limits = LIMITS
        base = dict(company_name="Benskin Talent Partners", evidence_text=SITE_TEXT, limits=limits)
        self.assertIn("consecutive words", validate_pitch("You lead Midwest executive search in accounting, finance, HR, and supply chain for employers everywhere.", **base))
        self.assertIn("names the company", validate_pitch("Benskin Talent Partners places finance leaders and could use more employer conversations.", **base))
        self.assertIn("dash", validate_pitch("You place finance leaders \u2014 more employers with open seats would help.", **base))
        self.assertIn("unapproved figure", validate_pitch("You place finance leaders and we can bring 40 leads a month to your desk.", **base))
        self.assertIn("exceeds", validate_pitch("word " * 40, **base))
        self.assertIn("too short", validate_pitch("You place leaders.", **base))
        self.assertEqual(validate_pitch("You place senior finance leaders, so more employers with open executive seats is the conversation worth having.", **base), "")
        exclude = validate_decision("x", {**GOOD_RAW, "fit_tier": "exclude"}, **base)
        self.assertEqual(exclude.pitch, "")

    def test_premium_models_need_an_explicit_opt_in(self) -> None:
        self.assertIn("premium", model_allowed(LlmFocusSettings(enabled=True, icp="x", model="claude-fable-5-1")))
        self.assertIn("premium", model_allowed(LlmFocusSettings(enabled=True, icp="x", model="fable")))
        self.assertEqual(model_allowed(LlmFocusSettings(enabled=True, icp="x", model="claude-fable-5-1", allow_expensive_models=True)), "")
        self.assertEqual(model_allowed(LlmFocusSettings(enabled=True, icp="x", model="claude-opus-5")), "")
        with self.assertRaisesRegex(LlmFocusError, "premium"):
            build_transport(LlmFocusSettings(enabled=True, icp="x", model="claude-fable-5-1"))
        ready, blocker = provider_ready(LlmFocusSettings(enabled=True, icp="x", model="mythos"), {})
        self.assertFalse(ready)
        self.assertIn("premium", blocker)

    def test_estimate_scales_with_model_and_list_size(self) -> None:
        opus = estimate_nominal_usd("claude-opus-5", 1000)
        sonnet = estimate_nominal_usd("claude-sonnet-5", 1000)
        self.assertGreater(opus, sonnet)
        self.assertAlmostEqual(estimate_nominal_usd("claude-opus-5", 2000), opus * 2, places=1)
        self.assertEqual(estimate_nominal_usd("unknown-model", 100), 0.0)

    def test_budget_stops_new_calls_and_leaves_the_rest_for_next_run(self) -> None:
        calls: list[list[str]] = []

        def run(argv, **kwargs):
            calls.append(argv)
            prompt = argv[-1]
            domains = [line.split("Domain: ", 1)[1] for line in prompt.splitlines() if line.startswith("Domain: ")]
            envelope = json.loads(_claude_envelope([{"domain": d, **GOOD_RAW} for d in domains]))
            envelope["total_cost_usd"] = 0.6
            return SimpleNamespace(returncode=0, stdout=json.dumps(envelope), stderr="")

        transport = ClaudeCodeTransport(
            model="claude-opus-5", domains_per_call=1, concurrency=1, binary="/fake/claude",
            run=run, max_nominal_usd=1.0,
        )
        results = transport.send([_request(f"id{i}", f"site{i}.example") for i in range(4)])
        self.assertEqual(len(calls), 2)
        self.assertTrue(transport.budget.exhausted)
        self.assertEqual(results["id0"].error, "")
        self.assertAlmostEqual(results["id0"].nominal_cost_usd, 0.6)
        self.assertIn("budget", results["id3"].error)
        with tempfile.TemporaryDirectory() as tmp:
            fresh = ClaudeCodeTransport(
                model="claude-opus-5", domains_per_call=1, concurrency=1, binary="/fake/claude",
                run=run, max_nominal_usd=1.0,
            )
            classifier = LlmFocusClassifier(
                LlmFocusSettings(enabled=True, icp="x", provider="claude-code"),
                cache=JsonCache(Path(tmp) / "cache"), transport=fresh,
                offer_service="s", offer_audience="a", limits=LIMITS,
            )
            decisions = classifier.classify(
                [LlmFocusItem(f"site{i}.example", "X", SITE_TEXT, "https://x/") for i in range(4)]
            )
            self.assertEqual(decisions["site3.example"].status, "error")
            stats = classifier.stats()
            self.assertTrue(stats["budget_exhausted"])
            self.assertGreater(stats["nominal_cost_usd"], 0)


class DigestModeTests(unittest.TestCase):
    def test_digest_mode_writes_page_text_without_a_campaign(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "companies.csv").write_text("Company name,Website\nCore,core.example\nQuiet,quiet.example\n", encoding="utf-8")
            manifest = run_digests(
                input_path=tmp_path / "companies.csv",
                output_path=tmp_path / "digests.csv",
                options=RunOptions(cache_dir=tmp_path / "cache"),
                domain_enricher=ProfileEnricher(),
            )
            with (tmp_path / "digests.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(manifest["mode"], "digest_only")
            self.assertEqual(manifest["domains"]["with_page_text"], 2)
            self.assertIn("sell-side M&A advisory", rows[0]["company_page_digest"])
            self.assertEqual(rows[1]["company_enrichment_status"], "no_signal")
            self.assertIn("Commercial cleaning", rows[1]["company_page_digest"])

    def test_cli_digest_only_needs_no_campaign(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            stdout = io.StringIO()
            with patch("bulk_enrich.cli.run_digests", return_value={"mode": "digest_only"}) as fake, contextlib.redirect_stdout(stdout):
                code = main(["--input", str(ROOT / "tests" / "fixtures" / "leads.csv"), "--output", str(tmp_path / "d.csv"), "--digest-only", "--quiet"])
            self.assertEqual(code, 0)
            self.assertTrue(fake.called)
            self.assertEqual(json.loads(stdout.getvalue())["mode"], "digest_only")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(["--input", str(ROOT / "tests" / "fixtures" / "leads.csv"), "--output", str(tmp_path / "d.csv")])
            self.assertEqual(code, 2)
            self.assertIn("--campaign is required", stderr.getvalue())


class StubClassifier:
    def __init__(self, decisions: dict[str, LlmFocusDecision]) -> None:
        self.decisions = decisions
        self.items: list[LlmFocusItem] = []

    def classify(self, items):
        self.items.extend(items)
        return {item.domain: self.decisions[item.domain] for item in items if item.domain in self.decisions}

    def stats(self):
        return {"enabled": True, "model": "stub", "sent": len(self.items)}


class ProfileEnricher:
    facts = {
        "off.example": "piano restoration and repair",
        "core.example": "sell-side M&A advisory for private company owners",
        "secondary.example": "flexible capital solutions",
    }

    def enrich(self, domain: str) -> SiteSignal:
        if domain == "quiet.example":
            return SiteSignal(
                domain=domain,
                observation="",
                evidence="",
                source_url=f"https://{domain}/",
                confidence=0.0,
                status="no_signal",
                error="no usable company description found on fetched pages",
                page_digest="[https://quiet.example/]\nTitle: Quiet\nHeading: Commercial cleaning for offices and clinics",
            )
        focus = self.facts[domain]
        fact = CompanyFact("service", focus, focus, focus, f"https://{domain}/services", 0.90)
        return SiteSignal(
            domain=domain,
            observation=focus,
            evidence=focus,
            source_url=fact.source_url,
            confidence=0.90,
            status="ok",
            facts=(fact,),
            page_digest=f"[https://{domain}/]\nDescription: {focus}",
        )


def _write_llm_campaign(tmp_path: Path, provider: str = "api") -> Path:
    payload = json.loads((ROOT / "campaigns" / "examples" / "scale-olympus.json").read_text())
    payload["personalization"]["focus_rules_file"] = str(
        (ROOT / "campaigns" / "examples" / "scale-olympus-focus.csv").resolve()
    )
    payload["quality"]["max_body_words"] = 90
    payload["personalization"]["llm_focus"] = {
        "enabled": True,
        "icp": "B2B service companies that sell to other businesses",
        "exclusions": "piano restoration, law firms",
        "provider": provider,
        "model": "claude-opus-5",
        "effort": "low",
        "examples": [
            {
                "site": "Midwest executive search in accounting and finance",
                "fit_tier": "core",
                "focus": "executive search",
                "buyer_phrase": "companies hiring senior finance leaders",
            }
        ],
    }
    path = tmp_path / "llm-campaign.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class PipelineIntegrationTests(unittest.TestCase):
    def _decisions(self) -> dict[str, LlmFocusDecision]:
        return {
            "core.example": LlmFocusDecision(
                domain="core.example", status="ok", fit_tier="core", signal_type="service",
                focus="sell-side advisory", buyer_phrase="owners preparing a company sale",
                evidence="sell-side M&A advisory for private company owners",
                pitch="You advise founders through the sale of their business, so more owners at the start of that decision is probably the conversation worth having.",
                reason="The site describes sell-side M&A advisory.", confidence=0.93,
            ),
            "off.example": LlmFocusDecision(
                domain="off.example", status="ok", fit_tier="exclude", signal_type="service",
                focus="piano restoration", buyer_phrase="piano owners needing restoration",
                evidence="piano restoration and repair", reason="Consumer piano services.", confidence=0.95,
            ),
            "secondary.example": LlmFocusDecision(
                domain="secondary.example", status="rejected",
                error="evidence quote was not found verbatim in the website text",
            ),
            "quiet.example": LlmFocusDecision(
                domain="quiet.example", status="ok", fit_tier="core", signal_type="service",
                focus="commercial cleaning", buyer_phrase="offices needing regular cleaning",
                evidence="Commercial cleaning for offices and clinics",
                pitch_error="pitch opens with a research announcement",
                reason="The heading states commercial cleaning.", confidence=0.88,
            ),
        }

    def test_model_decisions_drive_company_fit_and_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "leads.csv"
            input_path.write_text(
                "Email,Email status,First name,Job title,Job seniority,Company name,Website\n"
                "amy@example.com,VERIFIED,Amy,Owner,Founder/Owner,Off Niche,off.example\n"
                "ben@example.com,VERIFIED,Ben,Founder,Founder/Owner,Core Adviser,core.example\n"
                "casey@example.com,VERIFIED,Casey,Founder,Founder/Owner,Secondary Capital,secondary.example\n"
                "dee@example.com,VERIFIED,Dee,Owner,Founder/Owner,Quiet Cleaning,quiet.example\n",
                encoding="utf-8",
            )
            campaign = load_campaign(_write_llm_campaign(tmp_path))
            self.assertIsNotNone(campaign.llm_focus)
            stub = StubClassifier(self._decisions())
            manifest = run_enrichment(
                input_path=input_path,
                output_path=tmp_path / "audit.csv",
                campaign=campaign,
                title_hooks=TitleHookTable.load(ROOT / "config" / "title-hooks.csv"),
                commercial_focuses=CommercialFocusTable.load(campaign.focus_rules_path),
                options=RunOptions(cache_dir=tmp_path / "cache"),
                domain_enricher=ProfileEnricher(),
                llm_classifier=stub,
            )
            with (tmp_path / "audit.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = {row["Website"]: row for row in csv.DictReader(handle)}

            self.assertEqual(sorted(item.domain for item in stub.items), ["core.example", "off.example", "quiet.example", "secondary.example"])
            core = rows["core.example"]
            self.assertEqual(core["outreach_status"], "ready")
            self.assertEqual(core["company_fit_rule"], "llm-focus")
            self.assertEqual(core["personalization_buyer_phrase"], "owners preparing a company sale")
            self.assertTrue(core["personalized_pitch"].startswith("You advise founders"))
            self.assertEqual(core["personalization_template"], "llm-pitch")
            self.assertEqual(core["personalization_angle"], "llm-pitch")
            self.assertIn("You advise founders", core["personalized_email"])
            self.assertTrue(core["personalized_subject"])
            self.assertIn("model: The site describes", core["company_fit_reason"])
            self.assertEqual(core["personalization_source"], "https://core.example/services")
            self.assertEqual(stub.items[[i.domain for i in stub.items].index("core.example")].job_title, "Founder")

            off = rows["off.example"]
            self.assertEqual(off["outreach_status"], "excluded")
            self.assertEqual(off["company_fit_rule"], "llm-focus")
            self.assertEqual(off["company_fit_tier"], "exclude")
            self.assertEqual(off["personalized_email"], "")

            secondary = rows["secondary.example"]
            self.assertNotEqual(secondary["company_fit_rule"], "llm-focus")
            self.assertIn("model decision rejected", secondary["company_fit_reason"])

            quiet = rows["quiet.example"]
            self.assertEqual(quiet["outreach_status"], "ready")
            self.assertEqual(quiet["company_fit_rule"], "llm-focus")
            self.assertNotEqual(quiet["personalization_template"], "llm-pitch")
            self.assertIn("offices needing regular cleaning", quiet["personalized_pitch"])
            self.assertIn("model pitch rejected", quiet["personalization_error"])

            self.assertTrue(manifest["llm_focus"]["enabled"])
            self.assertEqual(manifest["qualification"]["company_rule_counts"]["llm-focus"], 3)
            self.assertEqual(manifest["focus_gaps"]["excluded_samples"][0]["domain"], "off.example")

    def test_company_qualification_mode_uses_model_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "companies.csv"
            input_path.write_text(
                "Company name,Website\nOff Niche,off.example\nCore Adviser,core.example\n",
                encoding="utf-8",
            )
            campaign = load_campaign(_write_llm_campaign(tmp_path))
            manifest = run_company_qualification(
                input_path=input_path,
                output_path=tmp_path / "audit.csv",
                campaign=campaign,
                commercial_focuses=CommercialFocusTable.load(campaign.focus_rules_path),
                options=RunOptions(cache_dir=tmp_path / "cache"),
                domain_enricher=ProfileEnricher(),
                llm_classifier=StubClassifier(self._decisions()),
            )
            with (tmp_path / "audit.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["company_qualification_status"] for row in rows], ["not_fit", "fit"])
            self.assertEqual(rows[1]["company_fit_rule"], "llm-focus")
            self.assertTrue(manifest["llm_focus"]["enabled"])

    def test_campaign_without_llm_focus_never_builds_a_classifier(self) -> None:
        campaign = load_campaign(ROOT / "campaigns" / "examples" / "scale-olympus.json")
        self.assertIsNone(campaign.llm_focus)


class ConfigTests(unittest.TestCase):
    def _campaign_with(self, block: dict, body_words: int = 90) -> dict:
        payload = json.loads((ROOT / "campaigns" / "examples" / "scale-olympus.json").read_text())
        payload["quality"]["max_body_words"] = body_words
        payload["personalization"]["llm_focus"] = block
        return payload

    def _load(self, payload: dict):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "campaign.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return load_campaign(path)

    def test_enabled_block_requires_an_icp(self) -> None:
        with self.assertRaisesRegex(CampaignConfigError, "llm_focus.icp"):
            self._load(self._campaign_with({"enabled": True}))

    def test_unknown_keys_bad_effort_and_bad_examples_are_rejected(self) -> None:
        with self.assertRaisesRegex(CampaignConfigError, "unknown keys: api_key"):
            self._load(self._campaign_with({"enabled": False, "api_key": "sk-nope"}))
        with self.assertRaisesRegex(CampaignConfigError, "effort"):
            self._load(self._campaign_with({"enabled": True, "icp": "x", "effort": "turbo"}))
        with self.assertRaisesRegex(CampaignConfigError, "fit_tier"):
            self._load(
                self._campaign_with(
                    {
                        "enabled": True,
                        "icp": "x",
                        "examples": [{"site": "s", "fit_tier": "maybe", "focus": "f", "buyer_phrase": "b"}],
                    }
                )
            )

    def test_settings_are_parsed_with_defaults(self) -> None:
        config = self._load(self._campaign_with({"enabled": True, "icp": " B2B services "}))
        settings = config.llm_focus
        self.assertEqual(settings.icp, "B2B services")
        self.assertEqual(settings.model, "claude-opus-5")
        self.assertEqual(settings.effort, "low")
        self.assertEqual(settings.max_evidence_chars, 3000)
        self.assertEqual(settings.provider, "claude-code")
        self.assertEqual(settings.domains_per_call, 5)
        with self.assertRaisesRegex(CampaignConfigError, "provider"):
            self._load(self._campaign_with({"enabled": True, "icp": "x", "provider": "gemini"}))
        with self.assertRaisesRegex(CampaignConfigError, "domains_per_call"):
            self._load(self._campaign_with({"enabled": True, "icp": "x", "domains_per_call": 50}))
        self.assertTrue(settings.write_pitch)
        self.assertEqual(settings.max_pitch_words, 30)
        self.assertEqual(settings.max_nominal_usd, 20.0)
        self.assertFalse(settings.allow_expensive_models)
        disabled = self._load(self._campaign_with({"enabled": False}))
        self.assertIsNone(disabled.llm_focus)

    def test_body_limit_must_fit_a_model_pitch(self) -> None:
        with self.assertRaisesRegex(CampaignConfigError, "max_body_words.*at least"):
            self._load(self._campaign_with({"enabled": True, "icp": "x"}, body_words=55))
        self._load(self._campaign_with({"enabled": True, "icp": "x", "write_pitch": False}, body_words=55))
        with self.assertRaisesRegex(CampaignConfigError, "max_nominal_usd"):
            self._load(self._campaign_with({"enabled": True, "icp": "x", "max_nominal_usd": 0}))


class CliTests(unittest.TestCase):
    def test_validate_only_reports_llm_focus_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            campaign_path = _write_llm_campaign(tmp_path)
            stdout = io.StringIO()
            with patch.dict("os.environ", {}, clear=True), patch(
                "bulk_enrich.cli._load_local_env"
            ), contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "--input", str(ROOT / "tests" / "fixtures" / "leads.csv"),
                        "--output", str(tmp_path / "out.csv"),
                        "--campaign", str(campaign_path),
                        "--validate-only",
                    ]
                )
            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(payload["llm_focus"]["enabled"])
            self.assertEqual(payload["llm_focus"]["provider"], "api")
            self.assertEqual(payload["llm_focus"]["model"], "claude-opus-5")
            self.assertFalse(payload["llm_focus"]["provider_ready"])
            self.assertRegex(payload["llm_focus"]["provider_blocker"], "credentials|SDK")

    def test_run_refuses_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            campaign_path = _write_llm_campaign(tmp_path)
            stderr = io.StringIO()
            with patch.dict("os.environ", {}, clear=True), patch(
                "bulk_enrich.cli._load_local_env"
            ), contextlib.redirect_stderr(stderr):
                code = main(
                    [
                        "--input", str(ROOT / "tests" / "fixtures" / "leads.csv"),
                        "--output", str(tmp_path / "out.csv"),
                        "--campaign", str(campaign_path),
                        "--cache-dir", str(tmp_path / "cache"),
                        "--allow-test-campaign",
                    ]
                )
            self.assertEqual(code, 2)
            self.assertRegex(stderr.getvalue(), "cannot run: .*(credentials|SDK)")
            self.assertFalse((tmp_path / "out.csv").exists())


if __name__ == "__main__":
    unittest.main()
