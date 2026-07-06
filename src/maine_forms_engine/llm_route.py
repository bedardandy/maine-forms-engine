"""llm_route — optional, stdlib-only, local-first model routing with auditable escalation.

RFC module (see ``docs/model-routing.md``). This is an **optional** add-on: nothing in
``maine_forms_engine.fill`` imports it, and it adds **zero** runtime dependencies. The
engine's fill path stays deterministic — no model is consulted at fill time. This module
is for the *callers* around the engine (the router, the drafting/verify tools) that today
each hand-roll their own OpenAI-compatible client with their own env convention.

Design goals
------------
* **One env convention** for the whole suite. Tiers are configured by a canonical trio::

      LLM_ROUTE_<TIER>_BASE_URL   e.g. LLM_ROUTE_LOCAL_BASE_URL=http://localhost:11434/v1
      LLM_ROUTE_<TIER>_MODEL      e.g. LLM_ROUTE_LOCAL_MODEL=gemma3:27b
      LLM_ROUTE_<TIER>_API_KEY    e.g. LLM_ROUTE_LOCAL_API_KEY=ollama   (optional)

  with a fallback to the ubiquitous ``OPENAI_BASE_URL`` / ``OPENAI_MODEL`` /
  ``OPENAI_API_KEY`` so a single-endpoint user needs no new env at all.

* **Local-first, escalate on evidence.** Cheap/local tiers answer structured extraction
  and classification; the router climbs one rung when a signal says the local rung is
  likely to be wrong (prior failures, high ambiguity, context overflow, vision need) or
  when a caller-supplied ``validate`` rejects the output.

* **Never silent.** Every :class:`Completion` carries which tier answered, how many
  attempts it took, whether it escalated, and a per-hop ``route_log``. An unconfigured
  tier is *skipped with a recorded reason*, not hidden. If **all** tiers are unconfigured
  the router raises :class:`NoTierConfigured` — the caller owns its deterministic
  fallback; this module never fakes success.

STDLIB ONLY: ``urllib``, ``json``, ``dataclasses``, ``enum``, ``os``. This mirrors the
sibling ``maine-forms-router`` client so the module can graduate to its own repo unchanged.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

__all__ = [
    "TaskClass",
    "Signals",
    "ModelTier",
    "Completion",
    "Router",
    "NoTierConfigured",
    "load_tier",
    "default_ladder",
]

# The canonical tier order, cheapest/most-local first. Extra tiers can be inserted
# between these (e.g. a mid-size regional model) by naming them in Router(ladder=[...]).
DEFAULT_TIER_ORDER = ("LOCAL", "FRONTIER")


class TaskClass(Enum):
    """What the model is being asked to do — drives the *starting* rung.

    EXTRACT / CLASSIFY are structured, low-liability, local-first tasks. DRAFT produces
    prose a human reviews. VERIFY is the high-liability adjudication path and starts at
    FRONTIER by default (mirrors the suite's Qwen-draft -> Opus-adjudicate pattern).
    """

    EXTRACT = "extract"
    CLASSIFY = "classify"
    DRAFT = "draft"
    VERIFY = "verify"


@dataclass
class Signals:
    """Per-call evidence the policy uses to decide the starting rung and escalations.

    All fields default to the "no escalation pressure" value so a caller can pass an
    empty ``Signals()`` and get plain local-first behavior.
    """

    input_chars: int = 0
    #: Rough token estimate. If not given, derived from input_chars (~4 chars/token),
    #: the same cheap heuristic the router uses for its long catalog prompt.
    approx_tokens: Optional[int] = None
    #: Output must satisfy a strict schema/enum (raises the bar for the local rung).
    schema_strict: bool = False
    #: How many times a prior attempt (this call or upstream) already failed.
    prior_failures: int = 0
    #: Lexical/consensus ambiguity in [0, 1]; > 0.7 escalates. Mirrors forms-router's
    #: lexical-margin and hallucheck's consensus-disagreement signals.
    ambiguity: float = 0.0
    #: The task needs image/vision input.
    needs_vision: bool = False

    def tokens(self) -> int:
        if self.approx_tokens is not None:
            return self.approx_tokens
        # ~4 chars/token is the standard rough English heuristic.
        return (self.input_chars + 3) // 4


@dataclass
class ModelTier:
    """One rung of the ladder. Configured from env by :func:`load_tier`.

    ``base_url``/``model`` empty => the tier is *unconfigured* and will be skipped
    (with a recorded reason) rather than called.
    """

    name: str
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    #: Advisory local context budget in tokens; a prompt above this escalates off this
    #: rung. Not a hard API limit — just the policy's "this rung will truncate" hint.
    max_context_hint: int = 8192
    #: Relative cost per call (USD-ish, advisory). Used only for the optional budget
    #: ceiling; local tiers are ~0.
    cost_hint: float = 0.0
    #: Whether this rung can accept vision input.
    supports_vision: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)


@dataclass
class Completion:
    """The result of :meth:`Router.complete`. Never silent about how it got here."""

    text: str
    tier_used: Optional[str]
    attempts: int
    escalated: bool
    #: One dict per hop: tier, configured, called, ok, reason, validated, cost.
    route_log: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "tier_used": self.tier_used,
            "attempts": self.attempts,
            "escalated": self.escalated,
            "route_log": self.route_log,
        }


class NoTierConfigured(RuntimeError):
    """Raised by :meth:`Router.complete` when no tier in the ladder is configured.

    The caller decides its deterministic fallback (e.g. forms-router drops to lexical).
    The router never fabricates a successful completion.
    """


# --------------------------------------------------------------- env config


def _env(tier: str, suffix: str) -> str:
    return os.environ.get(f"LLM_ROUTE_{tier.upper()}_{suffix}", "")


def load_tier(name: str, **overrides: Any) -> ModelTier:
    """Build a :class:`ModelTier` from the canonical env trio.

    ``LLM_ROUTE_<NAME>_BASE_URL`` / ``_MODEL`` / ``_API_KEY``. Falls back to the
    ubiquitous ``OPENAI_BASE_URL`` / ``OPENAI_MODEL`` / ``OPENAI_API_KEY`` so a
    single-endpoint deployment needs no tier-specific env. ``overrides`` (e.g.
    ``max_context_hint``, ``cost_hint``, ``supports_vision``) win over env.
    """

    base = _env(name, "BASE_URL") or os.environ.get("OPENAI_BASE_URL", "")
    model = _env(name, "MODEL") or os.environ.get("OPENAI_MODEL", "")
    key = _env(name, "API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    tier = ModelTier(
        name=name.upper(),
        base_url=base.rstrip("/"),
        model=model,
        api_key=key or "none",
    )
    for k, v in overrides.items():
        setattr(tier, k, v)
    return tier


def default_ladder(order: tuple[str, ...] = DEFAULT_TIER_ORDER,
                   **per_tier: dict) -> list[ModelTier]:
    """Build the standard ladder from env. ``per_tier`` maps tier name -> overrides,
    e.g. ``default_ladder(LOCAL={"max_context_hint": 32768, "supports_vision": True})``.
    """

    ladder = []
    for name in order:
        ladder.append(load_tier(name, **per_tier.get(name, {})))
    # A FRONTIER rung is assumed to be big-context + vision-capable unless told otherwise;
    # this only affects the *hint*-based escalation triggers, not any API behavior.
    for t in ladder:
        if t.name == "FRONTIER" and "FRONTIER" not in per_tier:
            t.max_context_hint = max(t.max_context_hint, 128_000)
            t.supports_vision = True
    return ladder


# --------------------------------------------------------------- transport


def _chat_completion(tier: ModelTier, messages: list[dict], *,
                    temperature: float = 0.0, max_tokens: int = 1024,
                    timeout: float = 60.0) -> str:
    """One OpenAI-compatible /chat/completions POST via urllib. Mirrors
    maine-forms-router._llm_call so the wire contract is identical across the suite."""

    body = json.dumps({
        "model": tier.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(
        f"{tier.base_url}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {tier.api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


# --------------------------------------------------------------- the router


@dataclass
class Router:
    """Local-first router over a tier ladder with auditable, budgeted escalation."""

    ladder: list[ModelTier] = field(default_factory=default_ladder)
    #: Cap on how many *additional* rungs above the start we will try.
    max_escalations: int = 2
    #: Optional cumulative cost ceiling (sum of cost_hint of called tiers). None = off.
    cost_ceiling: Optional[float] = None
    #: Injectable transport (for tests). Signature: (tier, messages) -> str.
    transport: Callable[..., str] = _chat_completion

    # --- policy ---------------------------------------------------------

    def _start_index(self, task: TaskClass, signals: Signals) -> int:
        """Which rung to *start* at, before escalation. VERIFY starts one rung up."""
        idx = 0
        if task == TaskClass.VERIFY:
            idx = min(1, len(self.ladder) - 1)  # frontier-by-default
        elif task == TaskClass.DRAFT:
            # Drafting is prose a human reviews; local is fine to start, but a strict
            # schema pushes it up a rung immediately.
            if signals.schema_strict:
                idx = min(idx + 1, len(self.ladder) - 1)
        return idx

    def _escalation_pressure(self, task: TaskClass, signals: Signals,
                            tier: ModelTier) -> list[str]:
        """Reasons (if any) the *current* rung is a poor fit -> escalate one rung.

        Returned as a list of human-readable reasons for the route_log; empty => stay.
        """
        reasons = []
        if signals.prior_failures > 0:
            reasons.append(f"prior_failures={signals.prior_failures}")
        if signals.ambiguity > 0.7:
            reasons.append(f"ambiguity={signals.ambiguity:.2f}>0.7")
        if signals.tokens() > tier.max_context_hint:
            reasons.append(
                f"approx_tokens={signals.tokens()}>context_hint={tier.max_context_hint}")
        if signals.needs_vision and not tier.supports_vision:
            reasons.append("needs_vision without local vision")
        return reasons

    def choose(self, task: TaskClass, signals: Optional[Signals] = None) -> ModelTier:
        """Pick the tier the policy would *start* at, after applying escalation pressure
        from the signals. Pure and side-effect-free; :meth:`complete` uses it as the
        entry rung then walks up on validation failure. Returns the chosen configured or
        unconfigured tier (caller can inspect ``.configured``)."""
        signals = signals or Signals()
        idx = self._start_index(task, signals)
        # Apply signal-driven pressure once from the start rung (bounded by ladder top).
        while idx < len(self.ladder) - 1 and self._escalation_pressure(
                task, signals, self.ladder[idx]):
            idx += 1
        return self.ladder[idx]

    # --- execution ------------------------------------------------------

    def complete(self, task: TaskClass, messages: list[dict],
                 signals: Optional[Signals] = None, *,
                 validate: Optional[Callable[[str], bool]] = None,
                 on_decision: Optional[Callable[[dict], None]] = None,
                 **transport_kw: Any) -> Completion:
        """Walk the ladder from the policy's entry rung, returning the first output that
        (optionally) passes ``validate``.

        Escalation happens when:
          * a rung is unconfigured (skipped, recorded), or
          * the transport errors, or
          * ``validate(text)`` returns falsy (generalizes the suite's retry-on-empty and
            enum-validation-reject patterns).

        ``on_decision`` receives each hop's dict as it happens — this is the seam for
        ``legal_logic_layer.Ledger.record(source="llm", ...)`` (no import here; keep
        zero-dep). Budget guard: at most ``max_escalations`` rungs above the start, and
        an optional cumulative ``cost_ceiling``.

        Raises :class:`NoTierConfigured` if no configured tier was reachable at all.
        """
        signals = signals or Signals()
        start = self._start_index(task, signals)
        route_log: list[dict] = []
        attempts = 0
        spent = 0.0
        escalations_used = 0
        any_configured_called = False

        idx = start
        # Fold the initial signal-driven pressure into the starting rung too, so choose()
        # and complete() agree on where we begin.
        while idx < len(self.ladder) - 1 and self._escalation_pressure(
                task, signals, self.ladder[idx]):
            idx += 1
        start = idx

        while idx < len(self.ladder):
            tier = self.ladder[idx]
            hop: dict = {
                "task": task.value,
                "tier": tier.name,
                "index": idx,
                "configured": tier.configured,
                "called": False,
                "ok": False,
                "validated": None,
                "reason": "",
                "cost": 0.0,
            }

            if not tier.configured:
                hop["reason"] = "tier unconfigured (no base_url/model in env)"
                route_log.append(hop)
                if on_decision:
                    on_decision(hop)
                idx += 1
                continue

            if self.cost_ceiling is not None and spent + tier.cost_hint > self.cost_ceiling:
                hop["reason"] = (f"cost ceiling {self.cost_ceiling} would be exceeded "
                                 f"(spent={spent}, tier={tier.cost_hint})")
                route_log.append(hop)
                if on_decision:
                    on_decision(hop)
                break

            attempts += 1
            any_configured_called = True
            hop["called"] = True
            try:
                text = self.transport(tier, messages, **transport_kw)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                    OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
                spent += tier.cost_hint
                hop["cost"] = tier.cost_hint
                hop["reason"] = f"transport error: {type(exc).__name__}: {exc}"
                route_log.append(hop)
                if on_decision:
                    on_decision(hop)
                idx, escalations_used = self._advance(idx, start, escalations_used,
                                                      route_log, on_decision)
                if idx is None:
                    break
                continue

            spent += tier.cost_hint
            hop["cost"] = tier.cost_hint
            ok = True
            if validate is not None:
                try:
                    ok = bool(validate(text))
                except Exception as exc:  # a broken validator must not crash routing
                    ok = False
                    hop["reason"] = f"validate() raised {type(exc).__name__}: {exc}"
                hop["validated"] = ok

            if ok:
                hop["ok"] = True
                if not hop["reason"]:
                    hop["reason"] = "accepted"
                route_log.append(hop)
                if on_decision:
                    on_decision(hop)
                return Completion(
                    text=text,
                    tier_used=tier.name,
                    attempts=attempts,
                    escalated=idx > start,
                    route_log=route_log,
                )

            if not hop["reason"]:
                hop["reason"] = "validate() rejected output"
            route_log.append(hop)
            if on_decision:
                on_decision(hop)
            idx, escalations_used = self._advance(idx, start, escalations_used,
                                                  route_log, on_decision)
            if idx is None:
                break

        if not any_configured_called:
            raise NoTierConfigured(
                "no configured tier in ladder "
                f"({[t.name for t in self.ladder]}); set LLM_ROUTE_<TIER>_BASE_URL "
                "and _MODEL (or OPENAI_BASE_URL/OPENAI_MODEL). route_log="
                f"{route_log}"
            )

        # Configured tiers were tried but none passed validation / budget ran out.
        # Return the last text-bearing failure honestly (empty text, escalated=True).
        return Completion(
            text="",
            tier_used=None,
            attempts=attempts,
            escalated=True,
            route_log=route_log,
        )

    def _advance(self, idx: int, start: int, escalations_used: int,
                 route_log: list[dict],
                 on_decision: Optional[Callable[[dict], None]]):
        """Move to the next rung if the escalation budget allows; else stop.

        Returns ``(new_idx_or_None, escalations_used)``. Records a budget-stop hop when
        the ladder cannot climb further.
        """
        if escalations_used >= self.max_escalations or idx + 1 >= len(self.ladder):
            stop = {
                "tier": None,
                "called": False,
                "ok": False,
                "reason": (f"escalation budget exhausted "
                           f"(used={escalations_used}, max={self.max_escalations}, "
                           f"at rung {idx} of {len(self.ladder) - 1})"),
            }
            route_log.append(stop)
            if on_decision:
                on_decision(stop)
            return None, escalations_used
        return idx + 1, escalations_used + 1
