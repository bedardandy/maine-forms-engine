# RFC: `llm_route` — suite-wide local-first model routing with auditable escalation

Status: **RFC / reference implementation.** Ships as an **optional** module
`maine_forms_engine.llm_route` (stdlib-only, zero new dependencies). Nothing in
`maine_forms_engine.fill` imports it — the engine's deterministic, no-model-at-fill-time
contract is untouched. This document is the design rationale; the code is
[`src/maine_forms_engine/llm_route.py`](../src/maine_forms_engine/llm_route.py) with
tests in [`tests/test_llm_route.py`](../tests/test_llm_route.py).

> **May graduate to its own repo.** The module has no dependency on the engine and could
> live as a tiny standalone `llm-route` package (git-tag-pinned like the engine), imported
> by every repo in the suite. It is proposed here first because the engine is the hub the
> form repos already depend on, which makes it the lowest-friction place to trial the API.
> Maintainers should say which they prefer.

---

## 1. Problem

The 16-repo suite has grown **nine different base-URL env conventions** and **20+
hardcoded model strings**, and every LLM-touching tool hand-rolls its own OpenAI-compatible
client (≥6 copies). There is **no shared abstraction for choosing a model per task or for
escalating when a cheap/local model is likely wrong** — each repo reinvents retry-on-empty,
enum-reject, and consensus logic in isolation. A model choice is never recorded as an
auditable decision.

### Current-state inventory: base-URL env conventions

| # | Convention | Where it lives today | Notes |
|---|------------|----------------------|-------|
| 1 | `MCF_LLM_ENDPOINTS` | maine-corporation-forms | list form |
| 2 | `AUDIT_QWEN_BASE_URL` | corp audit tools | model-named env |
| 3 | `ROUTER_BASE_URL` / `ROUTER_MODEL` / `ROUTER_API_KEY` | maine-forms-router | closest to canonical; already has `OPENAI_*` fallback |
| 4 | `HALLUCHECK_BASE_URL` | LLM_Hallucination_Checker | |
| 5 | `INSPECTOR_BASE_URL` | hallucheck inspector | second env in the same repo |
| 6 | `VLM_API_BASE` | (a **hardcoded constant**, not env) | must be un-hardcoded |
| 7 | `LOCAL_VL_ENDPOINTS` | vision tooling | list form |
| 8 | `OPENAI_BASE_URL` / `OPENAI_MODEL` / `OPENAI_API_KEY` | several | de-facto lowest common denominator |
| 9 | `ANTHROPIC_BASE_URL` | hallucheck anthropic branch | provider-specific |

Plus probate `config.py` hardcodes a local fleet layout as Python constants in a public
repo, and 20+ model-name string literals are scattered across drafting/verify call sites.

### The canonical trio (this RFC)

One convention, provider-agnostic, self-hostable, **no hardcoded hosts**:

```
LLM_ROUTE_<TIER>_BASE_URL     # OpenAI-compatible /v1 base, e.g. http://localhost:11434/v1
LLM_ROUTE_<TIER>_MODEL        # model name as the endpoint expects it
LLM_ROUTE_<TIER>_API_KEY      # optional; "none" if the endpoint ignores it
```

Tiers default to `LOCAL` and `FRONTIER` and are **extensible** (insert e.g. a `MID` rung by
passing `Router(ladder=[...])`). If no tier-specific env is set, each tier falls back to the
ubiquitous `OPENAI_BASE_URL` / `OPENAI_MODEL` / `OPENAI_API_KEY`, so a single-endpoint user
needs **no new env at all**. There are no hardcoded hosts, no provider SDKs, no private
infrastructure references anywhere in the module.

---

## 2. Design

Four small pieces (see the module docstring for the full contract):

* **`TaskClass`** — `EXTRACT`, `CLASSIFY`, `DRAFT`, `VERIFY`. Sets the *starting* rung.
  EXTRACT/CLASSIFY are local-first; VERIFY starts at `FRONTIER` (high-liability path).
* **`Signals`** — per-call evidence: `input_chars` / `approx_tokens`, `schema_strict`,
  `prior_failures`, `ambiguity` (0–1), `needs_vision`. All default to "no pressure", so
  `Signals()` gives plain local-first behavior.
* **`ModelTier`** — one rung: `name`, `base_url` (from env), `model`, `api_key`,
  `max_context_hint`, `cost_hint`, `supports_vision`. Empty base/model ⇒ *unconfigured*.
* **`Router`** — `choose(task, signals) -> tier` (pure policy) and
  `complete(task, messages, signals, validate=, on_decision=) -> Completion` (walks the
  ladder). The returned `Completion` carries `tier_used`, `attempts`, `escalated`, and a
  per-hop `route_log`.

### Policy (`choose`)

* **LOCAL-first** for EXTRACT / CLASSIFY / structured work.
* **Escalate one rung** per active signal: `prior_failures > 0`, `ambiguity > 0.7`,
  `approx_tokens > tier.max_context_hint`, or `needs_vision` without local vision.
* **DRAFT** starts LOCAL but jumps a rung when `schema_strict`.
* **VERIFY** starts at `FRONTIER` by default — this mirrors the suite's existing
  Qwen-draft → Opus-adjudicate split, where the high-liability judgment is not trusted to
  the cheap rung.

### Execution (`complete`)

Walks from the policy's entry rung upward. Escalation is triggered by:

1. an **unconfigured** rung (skipped, with a recorded reason — never silently dropped),
2. a **transport error**, or
3. a caller-supplied **`validate(text) -> bool`** returning falsy.

`validate` generalizes the suite's existing retry-on-empty and enum-validation-reject
patterns (see forms-router `_llm` — empty JSON array retries, invalid form ids are
filtered). A **budget guard** caps escalations (`max_escalations`) and optionally total
cost (`cost_ceiling`).

**Never silent.** Every hop appends a dict to `route_log` (and fires `on_decision`).
If **all** tiers are unconfigured, `complete` raises `NoTierConfigured` — the caller owns
its deterministic fallback (forms-router drops to lexical; hallucheck stays fail-closed).
The router never fabricates success.

---

## 3. Escalation-signal catalog

Each existing repo already computes one of these signals ad hoc; the router makes them a
uniform escalation vocabulary.

| Signal | `Signals` field | Existing analog in the suite |
|--------|-----------------|------------------------------|
| Schema / enum validation failure | `validate` callback | forms-router `_llm` enum-filter + retry-on-empty; corp/probate `route_form.py` |
| Consensus disagreement | `prior_failures` / re-call | hallucheck `inspect_consensus(..., samples=3)` fail-biased vote |
| Lexical-margin ambiguity | `ambiguity` (0–1) | forms-router lexical scoring (tie-break / low-margin) |
| Context overflow | `approx_tokens` vs `max_context_hint` | forms-router's 591-form catalog prompt is already long-context |
| Vision requirement | `needs_vision` + `supports_vision` | the VLM / `LOCAL_VL_ENDPOINTS` tooling |
| Prior failure (any) | `prior_failures` | generic retry loops across corp/court tools |

A caller wires consensus disagreement in by counting minority votes and passing them as
`prior_failures` (or by returning falsy from `validate` when agreement is below threshold),
so hallucheck's sampling behavior becomes an escalation trigger without special-casing.

---

## 4. Ledger auditability wiring

`complete(..., on_decision=cb)` calls `cb(hop_dict)` for **every** routing decision. The
hop dict carries `tier`, `configured`, `called`, `ok`, `validated`, `reason`, and `cost` —
exactly what an audit backing needs. It is designed to plug straight into
`legal_logic_layer.Ledger.record(source="llm", ...)`:

* `schema.py` already lists **`llm_inference`** in `BACKING_TYPES`, and
* `APPLIES_KINDS` already contains an **unused `"route"` kind**.

So a model choice becomes a first-class, reviewable ledger decision:

```python
# In a consumer that also uses legal-logic-layer (llm_route itself imports neither):
def to_ledger(hop, ledger):
    ledger.record(
        f"routed to {hop['tier']}: {hop['reason']}",
        source="llm",                       # forces needs_review=True in the ledger
        backing=[{"type": "llm_inference"}],
        # applies_to kind "route" — already a valid APPLIES_KIND, currently unused
    )

router.complete(task, messages, signals, on_decision=lambda h: to_ledger(h, ledger))
```

`llm_route` **does not import** `legal_logic_layer` — the callback is the only seam,
keeping the module zero-dependency and the wiring the consumer's choice.

---

## 5. Migration plan

Order and effort (S/M/L) follow the ecosystem audit's ranking. Each step swaps a repo's
hand-rolled client + bespoke env for `Router`, with no behavior change beyond gaining the
canonical env trio and the escalation ladder.

| Order | Repo | Effort | What changes |
|-------|------|--------|--------------|
| 1 | maine-forms-router | **S** | Replace `_llm_config` / `_llm_call` with `Router.complete`; catalog prompt = long-context escalation; retry-on-empty + enum-reject become `validate`. One call site. |
| 2 | maine-government-feeds | **S** | Also moves off raw `httpx` onto the stdlib client; unifies `classify_items.py` env. |
| 3 | LLM_Hallucination_Checker | **M** | Reference **VERIFY** route; keep fail-closed; `inspect_consensus` disagreement → escalation signal. |
| 4 | corp audit tools | **M** | Un-name `AUDIT_QWEN_BASE_URL` / `VLM_API_BASE`; consolidate `route_form.py`. |
| 5 | maine-probate-forms | **L** | Un-hardcode `config.py` fleet constants; many call sites. |
| 6 | maine-court-forms | **L** | Most call sites in the suite; migrate last once the API is proven. |

Each migration keeps the repo's existing deterministic fallback (lexical routing,
fail-closed verify) — `NoTierConfigured` hands control back rather than guessing.

---

## 6. Configuration example

Point tiers at any OpenAI-compatible endpoint. A common self-hosted setup: a
Gemma-3-27B-class local model via Ollama as `LOCAL`, and any frontier model as `FRONTIER`.

```bash
# LOCAL tier: a Gemma-3-27B-class model served by Ollama's OpenAI-compatible API
export LLM_ROUTE_LOCAL_BASE_URL="http://localhost:11434/v1"
export LLM_ROUTE_LOCAL_MODEL="gemma3:27b"
export LLM_ROUTE_LOCAL_API_KEY="ollama"          # Ollama ignores it; any string works

# FRONTIER tier: any OpenAI-compatible frontier endpoint (cloud or self-hosted gateway)
export LLM_ROUTE_FRONTIER_BASE_URL="https://your-openai-compatible-endpoint/v1"
export LLM_ROUTE_FRONTIER_MODEL="your-frontier-model"
export LLM_ROUTE_FRONTIER_API_KEY="sk-..."
```

The same works with vLLM, llama.cpp's server, LiteLLM, or any cloud that speaks
OpenAI chat-completions. Set only `OPENAI_BASE_URL` / `OPENAI_MODEL` and every tier falls
back to it (single-endpoint mode). Examples are `localhost` / placeholder only — no host
is hardcoded in the module.

```python
from maine_forms_engine.llm_route import Router, TaskClass, Signals, default_ladder

router = Router(ladder=default_ladder())        # LOCAL then FRONTIER, from env
completion = router.complete(
    TaskClass.CLASSIFY,
    [{"role": "user", "content": "Classify this matter: ..."}],
    Signals(input_chars=2400, schema_strict=True),
    validate=lambda text: text.strip() in {"probate", "family", "corporate"},
)
print(completion.tier_used, completion.escalated)
for hop in completion.route_log:
    print(hop["tier"], hop["reason"])
```

---

## 7. What this deliberately does NOT do

* **No provider SDKs.** stdlib `urllib` only; OpenAI-compatible chat-completions only.
* **No change to deterministic fill.** Nothing in `maine_forms_engine.fill` imports this;
  the engine still consults **no model at fill time**.
* **No hardcoded hosts or private-infrastructure references.** All endpoints come from env.
* **No hidden fallback.** All tiers unconfigured ⇒ raise; the caller owns its deterministic
  fallback. The router never fakes a completion.
* **No streaming, no function-calling, no token accounting** in the reference cut — those
  are additive and out of scope for the RFC.

*Part of the 2026-07-06 suite-wide audit.*
