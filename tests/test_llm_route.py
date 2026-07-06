"""Tests for the optional llm_route module. No network: the transport is either
injected as a stub or urllib is monkeypatched. Covers the choose() policy matrix,
escalation on validate-fail, budget stop, the all-unconfigured raise, and route_log
completeness."""
import json
import urllib.error

import pytest

from maine_forms_engine.llm_route import (
    Completion,
    ModelTier,
    NoTierConfigured,
    Router,
    Signals,
    TaskClass,
    default_ladder,
    load_tier,
)


# --------------------------------------------------------------- fixtures


def _tier(name, *, configured=True, **kw):
    if configured:
        kw.setdefault("base_url", f"http://{name.lower()}.invalid/v1")
        kw.setdefault("model", f"{name.lower()}-model")
        kw.setdefault("api_key", "none")
    return ModelTier(name=name, **kw)


def _echo_transport(reply):
    """Return a transport that always returns ``reply`` and records calls."""
    calls = []

    def transport(tier, messages, **kw):
        calls.append(tier.name)
        return reply

    transport.calls = calls
    return transport


def _local_frontier(**router_kw):
    ladder = [
        _tier("LOCAL", max_context_hint=8192, cost_hint=0.0, supports_vision=False),
        _tier("FRONTIER", max_context_hint=128000, cost_hint=0.05, supports_vision=True),
    ]
    return ladder, Router(ladder=ladder, **router_kw)


MSGS = [{"role": "user", "content": "hi"}]


# --------------------------------------------------------------- env / config


def test_load_tier_canonical_env(monkeypatch):
    monkeypatch.setenv("LLM_ROUTE_LOCAL_BASE_URL", "http://localhost:11434/v1/")
    monkeypatch.setenv("LLM_ROUTE_LOCAL_MODEL", "gemma3:27b")
    monkeypatch.setenv("LLM_ROUTE_LOCAL_API_KEY", "ollama")
    t = load_tier("local")
    assert t.name == "LOCAL"
    assert t.base_url == "http://localhost:11434/v1"  # trailing slash stripped
    assert t.model == "gemma3:27b"
    assert t.api_key == "ollama"
    assert t.configured


def test_load_tier_openai_fallback(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("LLM_ROUTE_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("OPENAI_MODEL", "qwen2.5")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    t = load_tier("LOCAL")
    assert t.base_url == "http://localhost:8000/v1"
    assert t.model == "qwen2.5"
    assert t.api_key == "none"  # empty -> "none" sentinel like the sibling router
    assert t.configured


def test_load_tier_unconfigured(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith(("LLM_ROUTE_", "OPENAI_")):
            monkeypatch.delenv(k, raising=False)
    t = load_tier("FRONTIER")
    assert not t.configured


def test_default_ladder_frontier_defaults(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith(("LLM_ROUTE_", "OPENAI_")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_ROUTE_FRONTIER_BASE_URL", "http://f.invalid/v1")
    monkeypatch.setenv("LLM_ROUTE_FRONTIER_MODEL", "big")
    ladder = default_ladder()
    frontier = [t for t in ladder if t.name == "FRONTIER"][0]
    assert frontier.max_context_hint >= 128000
    assert frontier.supports_vision is True


# --------------------------------------------------------------- choose() matrix


def test_choose_extract_classify_start_local():
    _, r = _local_frontier()
    assert r.choose(TaskClass.EXTRACT, Signals()).name == "LOCAL"
    assert r.choose(TaskClass.CLASSIFY, Signals()).name == "LOCAL"


def test_choose_verify_starts_frontier():
    _, r = _local_frontier()
    assert r.choose(TaskClass.VERIFY, Signals()).name == "FRONTIER"


def test_choose_draft_schema_strict_escalates():
    _, r = _local_frontier()
    assert r.choose(TaskClass.DRAFT, Signals()).name == "LOCAL"
    assert r.choose(TaskClass.DRAFT, Signals(schema_strict=True)).name == "FRONTIER"


def test_choose_prior_failures_escalates():
    _, r = _local_frontier()
    assert r.choose(TaskClass.EXTRACT, Signals(prior_failures=1)).name == "FRONTIER"


def test_choose_high_ambiguity_escalates():
    _, r = _local_frontier()
    assert r.choose(TaskClass.CLASSIFY, Signals(ambiguity=0.9)).name == "FRONTIER"
    assert r.choose(TaskClass.CLASSIFY, Signals(ambiguity=0.5)).name == "LOCAL"


def test_choose_context_overflow_escalates():
    _, r = _local_frontier()
    big = Signals(approx_tokens=20000)  # over LOCAL's 8192 hint
    assert r.choose(TaskClass.EXTRACT, big).name == "FRONTIER"


def test_choose_vision_without_local_vision_escalates():
    _, r = _local_frontier()
    assert r.choose(TaskClass.EXTRACT, Signals(needs_vision=True)).name == "FRONTIER"


def test_signals_token_estimate_from_chars():
    assert Signals(input_chars=400).tokens() == 100  # ~4 chars/token
    assert Signals(approx_tokens=7).tokens() == 7  # explicit wins


# --------------------------------------------------------------- complete()


def test_complete_local_first_success():
    ladder, r = _local_frontier()
    r.transport = _echo_transport("ok")
    c = r.complete(TaskClass.EXTRACT, MSGS, Signals())
    assert isinstance(c, Completion)
    assert c.tier_used == "LOCAL"
    assert c.attempts == 1
    assert c.escalated is False
    assert r.transport.calls == ["LOCAL"]
    assert c.route_log[-1]["ok"] is True


def test_complete_escalates_on_validate_fail():
    ladder, r = _local_frontier()
    # LOCAL returns "bad", FRONTIER returns "good"; validate accepts only "good".
    def transport(tier, messages, **kw):
        return "good" if tier.name == "FRONTIER" else "bad"
    r.transport = transport
    c = r.complete(TaskClass.EXTRACT, MSGS, Signals(),
                   validate=lambda t: t == "good")
    assert c.tier_used == "FRONTIER"
    assert c.attempts == 2
    assert c.escalated is True
    # route_log: LOCAL rejected, FRONTIER accepted
    assert c.route_log[0]["tier"] == "LOCAL"
    assert c.route_log[0]["validated"] is False
    assert c.route_log[-1]["tier"] == "FRONTIER"
    assert c.route_log[-1]["ok"] is True


def test_complete_skips_unconfigured_tier():
    ladder = [
        _tier("LOCAL", configured=False),
        _tier("FRONTIER"),
    ]
    r = Router(ladder=ladder)
    r.transport = _echo_transport("answer")
    c = r.complete(TaskClass.EXTRACT, MSGS, Signals())
    assert c.tier_used == "FRONTIER"
    # LOCAL recorded as skipped with a reason, not silently dropped
    local_hop = c.route_log[0]
    assert local_hop["tier"] == "LOCAL"
    assert local_hop["configured"] is False
    assert local_hop["called"] is False
    assert "unconfigured" in local_hop["reason"]


def test_complete_all_unconfigured_raises():
    ladder = [_tier("LOCAL", configured=False), _tier("FRONTIER", configured=False)]
    r = Router(ladder=ladder)
    with pytest.raises(NoTierConfigured):
        r.complete(TaskClass.EXTRACT, MSGS, Signals())


def test_complete_budget_stops_escalation():
    ladder = [_tier("LOCAL"), _tier("MID"), _tier("FRONTIER")]
    r = Router(ladder=ladder, max_escalations=1)
    r.transport = _echo_transport("nope")  # always fails validate
    c = r.complete(TaskClass.EXTRACT, MSGS, Signals(),
                   validate=lambda t: False)
    # start=LOCAL, one escalation allowed -> tries LOCAL, MID, then budget stop
    assert c.tier_used is None
    assert c.attempts == 2
    assert c.escalated is True
    assert any("budget exhausted" in h["reason"] for h in c.route_log)


def test_complete_cost_ceiling_blocks_expensive_tier():
    ladder = [
        _tier("LOCAL", cost_hint=0.0),
        _tier("FRONTIER", cost_hint=1.0),
    ]
    r = Router(ladder=ladder, cost_ceiling=0.5)
    r.transport = _echo_transport("x")
    c = r.complete(TaskClass.EXTRACT, MSGS, Signals(),
                   validate=lambda t: False)  # force escalation attempt
    assert c.tier_used is None
    # FRONTIER blocked by cost ceiling, recorded
    assert any("cost ceiling" in h["reason"] for h in c.route_log)


def test_complete_transport_error_escalates():
    ladder, r = _local_frontier()

    def transport(tier, messages, **kw):
        if tier.name == "LOCAL":
            raise urllib.error.URLError("connection refused")
        return "recovered"
    r.transport = transport
    c = r.complete(TaskClass.EXTRACT, MSGS, Signals())
    assert c.tier_used == "FRONTIER"
    assert "transport error" in c.route_log[0]["reason"]
    assert c.route_log[0]["called"] is True


def test_on_decision_callback_receives_every_hop():
    ladder, r = _local_frontier()

    def transport(tier, messages, **kw):
        return "good" if tier.name == "FRONTIER" else "bad"
    r.transport = transport
    seen = []
    r.complete(TaskClass.EXTRACT, MSGS, Signals(),
               validate=lambda t: t == "good",
               on_decision=seen.append)
    # LOCAL reject + FRONTIER accept => 2 decisions, mirroring route_log
    assert [h["tier"] for h in seen] == ["LOCAL", "FRONTIER"]
    assert seen[-1]["ok"] is True


def test_ledger_shaped_callback_wiring():
    """The on_decision dict carries exactly what a ledger llm_inference backing needs:
    tier + escalation + reason. This test documents the seam (no legal_logic_layer import)."""
    ladder, r = _local_frontier()
    r.transport = _echo_transport("ok")
    records = []

    def fake_ledger_record(hop):
        # what legal_logic_layer.Ledger.record(source="llm", backing=[{type:llm_inference}])
        # would consume:
        records.append({
            "source": "llm",
            "backing_type": "llm_inference",
            "applies_kind": "route",
            "tier": hop["tier"],
            "reason": hop["reason"],
        })
    r.complete(TaskClass.CLASSIFY, MSGS, Signals(), on_decision=fake_ledger_record)
    assert records[0]["tier"] == "LOCAL"
    assert records[0]["applies_kind"] == "route"
    assert records[0]["backing_type"] == "llm_inference"


def test_route_log_completeness_fields():
    ladder, r = _local_frontier()
    r.transport = _echo_transport("ok")
    c = r.complete(TaskClass.EXTRACT, MSGS, Signals())
    hop = c.route_log[-1]
    for key in ("task", "tier", "index", "configured", "called", "ok",
                "validated", "reason", "cost"):
        assert key in hop


def test_completion_to_dict_roundtrips():
    ladder, r = _local_frontier()
    r.transport = _echo_transport("ok")
    c = r.complete(TaskClass.EXTRACT, MSGS, Signals())
    d = c.to_dict()
    assert set(d) == {"text", "tier_used", "attempts", "escalated", "route_log"}
    # JSON-serializable end to end (matters for ledger emit)
    assert json.loads(json.dumps(d))["tier_used"] == "LOCAL"


# --------------------------------------------------------------- transport wire shape


def test_real_transport_builds_openai_request(monkeypatch):
    """Verify _chat_completion posts an OpenAI-compatible body via urllib (mocked)."""
    from maine_forms_engine import llm_route

    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "hello"}}]}).encode()

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data)
        captured["auth"] = req.headers.get("Authorization")
        return FakeResp()

    monkeypatch.setattr(llm_route.urllib.request, "urlopen", fake_urlopen)
    tier = _tier("LOCAL")
    out = llm_route._chat_completion(tier, MSGS)
    assert out == "hello"
    assert captured["url"].endswith("/chat/completions")
    assert captured["body"]["model"] == tier.model
    assert captured["body"]["messages"] == MSGS
    assert captured["auth"] == "Bearer none"
