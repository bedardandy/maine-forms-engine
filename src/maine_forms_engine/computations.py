"""Declarative computed fields — a form's printed arithmetic as data.

Forms print arithmetic instructions ("Add lines 7a, 7b and 7c.", "Subtract
line 2i from line 1j"). A form folder may carry an optional
``computations.json`` next to its ``mapping.json`` declaring those printed
formulas over canonical fact keys:

    {
      "form_id": "MRS-1041ME",
      "computed": {
        "facts.total_payments": {
          "op": "sum",
          "inputs": ["facts.maine_income_tax_withheld",
                      "facts.estimated_tax_payments",
                      "facts.refundable_tax_credits"],
          "formula_text": "d. Total payments. (Add lines 7a, 7b and 7c.)"
        },
        "facts.net_fiduciary_adjustment": {
          "op": "difference",
          "inputs": ["facts.total_additions", "facts.total_subtractions"],
          "formula_text": "3 Net Fiduciary Adjustment. (Subtract line 2i "
                          "from line 1j — see instructions [may be a "
                          "negative amount].)"
        }
      }
    }

Per-target spec fields:

- ``op``: ``"sum"`` | ``"difference"`` | ``"product"`` | ``"min"``.
  ``difference`` is the first input minus all the rest; ``sum`` inputs may
  carry a ``"-"`` prefix ("line 1 minus line 2 plus line 3" →
  ``["facts.l1", "-facts.l2", "facts.l3"]``); ``min`` is the least input
  ("Enter the least of the amounts of lines (4), (6) or (7)"). An input may
  also be a JSON number — a literal constant, used ONLY for a multiplier the
  form itself prints ("Multiply amount on line (3) by .25" →
  ``["facts.line_3", 0.25]``). The vocabulary is deliberately tiny — it
  covers what the surveyed forms literally print, nothing speculative.
- ``formula_text``: the **verbatim printed instruction** (anti-fabrication:
  every formula must trace to text printed on the form; quote it). Anything
  not verbatim must carry ``"inferred": true``.
- ``round``: optional decimal places for the computed value.
- ``floor``: optional clamp, only when the form prints one ("If zero or
  less, enter -0-" → ``"floor": 0``).
- ``note`` / ``inferred``: travel into the report entries.

Behavior (mirrors the constraints layer's warnings-only contract):

- target key **omitted** by the case + all inputs present → the value is
  computed and filled; the fill report marks it ``{"key", "kind":
  "computed", "value", "formula_text"}``.
- target key **supplied** but contradicting the computation → the supplied
  value is written **as-is** (the engine never enforces or overrides) and
  the report carries ``{"code": "COMPUTATION_MISMATCH", "key", "supplied",
  "computed", "formula_text", "severity": "warning"}``.
- any input missing → that computation is skipped silently; an input present
  but unparseable as a number → skipped with a report note, never guessed.
- evaluation is topological, so a computed value (or a supplied one) can
  feed later computations; a dependency cycle is a load error.
- no ``computations.json`` → zero behavior change. Nothing is ever embedded
  in the PDF itself (no AcroForm calculation JavaScript, no field locking).

Number handling is deterministic: ``"$1,234.56"``, thousands commas, and
parentheses-negatives parse; comparison uses a small tolerance so "1300" vs
"1,300.00" is not a mismatch; computed output mimics the formatting style of
the input values (bare integers stay bare integers).
"""
from __future__ import annotations

import json
import pathlib
import re
from decimal import Decimal

COMPUTATIONS_FILENAME = "computations.json"

_OPS = ("sum", "difference", "product", "min")

# |supplied - computed| <= tolerance is "equal" — formatting noise, not a
# contradiction (half a cent absorbs decimal-rendering differences).
TOLERANCE = Decimal("0.005")

_NUMBER = re.compile(r"-?\d+(\.\d+)?")


def parse_amount(value) -> Decimal | None:
    """Deterministic money/number parsing; ``None`` when unparseable.

    Accepts int/float/numeric strings with optional ``$``, thousands commas,
    and parentheses-negatives ("(1,300.00)" → -1300). Never guesses.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if not isinstance(value, str):
        return None
    s = value.strip()
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1].strip()
    if s.startswith("$"):
        s = s[1:].strip()
    if s.startswith("-"):
        neg, s = not neg if neg else True, s[1:].strip()
    s = s.replace(",", "")
    if not s or not _NUMBER.fullmatch(s):
        return None
    d = Decimal(s)
    return -d if neg else d


def _resolve(key: str, values: dict) -> object:
    """Look ``key`` up flat first, then as a dotted path (same dialect as
    ``maine_forms_engine.constraints``)."""
    if not isinstance(values, dict):
        return None
    if key in values:
        return values[key]
    cur: object = values
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _input_key(raw) -> tuple[str | None, int]:
    """Split an input entry into (key, sign): ``"-facts.x"`` → ("facts.x", -1).
    A JSON-number input is a printed literal constant: (None, +1)."""
    if isinstance(raw, (int, float)):
        return None, 1
    if raw.startswith("-"):
        return raw[1:], -1
    return raw, 1


def _validate(computed: dict) -> None:
    for key, spec in computed.items():
        if not isinstance(spec, dict):
            raise ValueError(f"computations: {key}: spec must be an object")
        op = spec.get("op")
        if op not in _OPS:
            raise ValueError(f"computations: {key}: unknown op {op!r} "
                             f"(supported: {', '.join(_OPS)})")
        inputs = spec.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            raise ValueError(f"computations: {key}: inputs must be a "
                             "non-empty list of keys")
        for i in inputs:
            if isinstance(i, bool) or not isinstance(i, (str, int, float)):
                raise ValueError(f"computations: {key}: input {i!r} must be "
                                 "a key string or a printed literal number")
        if op != "sum" and any(isinstance(i, str) and i.startswith("-")
                               for i in inputs):
            raise ValueError(f"computations: {key}: signed '-' inputs are "
                             "only meaningful for op 'sum'")
        if all(not isinstance(i, str) for i in inputs):
            raise ValueError(f"computations: {key}: at least one input must "
                             "be a fact key")
        if not (spec.get("formula_text") or "").strip():
            raise ValueError(f"computations: {key}: formula_text (the "
                             "verbatim printed instruction) is required")


def _toposort(computed: dict) -> list[str]:
    """Targets in dependency order; raises ValueError on a cycle."""
    deps = {key: {k for k, _ in (_input_key(i) for i in spec["inputs"])
                  if k in computed and k != key}
            for key, spec in computed.items()}
    order: list[str] = []
    ready = sorted(k for k, d in deps.items() if not d)
    while ready:
        k = ready.pop(0)
        order.append(k)
        for other in sorted(deps):
            if k in deps[other]:
                deps[other].discard(k)
                if not deps[other] and other not in order \
                        and other not in ready:
                    ready.append(other)
    if len(order) != len(deps):
        cyc = sorted(set(deps) - set(order))
        raise ValueError(f"computations: dependency cycle among {cyc}")
    return order


def load_computations(form_dir: str | pathlib.Path) -> dict | None:
    """Read ``<form_dir>/computations.json``; ``None`` when absent (the
    common case — absence means zero behavior change). Unknown ops, malformed
    specs, and dependency cycles are load errors (ValueError)."""
    p = pathlib.Path(form_dir) / COMPUTATIONS_FILENAME
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    computed = data.get("computed") or {}
    _validate(computed)
    _toposort(computed)  # cycle = load error
    return data


def _decimal_places(sample: str) -> int:
    m = re.search(r"\.(\d+)\b", sample)
    return len(m.group(1)) if m else 0


def _format_like(result: Decimal, samples: list[str],
                 places: int | None) -> str:
    """Format a computed value the way this form's existing values look:
    bare integers stay bare ("3000"), cents/commas/$ appear only when the
    inputs carry them (or ``round`` forces decimal places)."""
    if places is None:
        places = max((_decimal_places(s) for s in samples), default=0)
        frac = -result.normalize().as_tuple().exponent
        places = max(places, max(frac, 0))
    q = result.quantize(Decimal(1).scaleb(-places)) if places >= 0 else result
    commas = any("," in s for s in samples)
    body = f"{q:,.{places}f}" if commas else f"{q:.{places}f}"
    if samples and all(s.lstrip("(").strip().startswith("$")
                       for s in samples):
        body = ("-$" + body[1:]) if body.startswith("-") else ("$" + body)
    return body


def evaluate(computations: dict | None, values: dict) -> dict:
    """Evaluate a computations dict against a case / resolved-values dict.

    Returns ``{"computed": [entries], "warnings": [warnings], "notes":
    [notes]}`` — all empty when ``computations`` is None/empty. Supplied
    values are never altered; supplied-or-computed values feed later
    computations topologically.
    """
    out = {"computed": [], "warnings": [], "notes": []}
    if not computations:
        return out
    computed_spec = computations.get("computed") or {}
    if not computed_spec:
        return out
    _validate(computed_spec)
    overlay: dict = {}  # computed values, flat-keyed; consulted before values

    def lookup(key: str):
        if key in overlay:
            return overlay[key]
        return _resolve(key, values)

    for key in _toposort(computed_spec):
        spec = computed_spec[key]
        terms, samples, skip = [], [], False
        for raw in spec["inputs"]:
            ik, sign = _input_key(raw)
            if ik is None:  # printed literal constant (e.g. a multiplier)
                terms.append((1, Decimal(str(raw))))
                continue
            v = lookup(ik)
            if v is None or v == "":
                skip = True  # input missing → skip silently
                break
            amount = parse_amount(v)
            if amount is None:
                out["notes"].append(
                    {"key": key,
                     "note": f"input {ik} = {v!r} is not a number; "
                             "computation skipped"})
                skip = True
                break
            terms.append((sign, amount))
            samples.append(str(v))
        if skip:
            continue
        op = spec["op"]
        if op == "min":
            result = min(a for _, a in terms)
        elif op == "product":
            result = Decimal(1)
            for _, a in terms:
                result *= a
        elif op == "difference":
            result = terms[0][1] - sum(a for _, a in terms[1:])
        else:  # sum (with optional per-input signs)
            result = sum(s * a for s, a in terms)
        result = Decimal(result)
        places = spec.get("round")
        if places is not None:
            result = result.quantize(Decimal(1).scaleb(-int(places)))
        if spec.get("floor") is not None and \
                result < Decimal(str(spec["floor"])):
            result = Decimal(str(spec["floor"]))
        formatted = _format_like(result, samples, places)
        supplied = lookup(key)
        if supplied is None or supplied == "":
            entry = {"key": key, "kind": "computed", "value": formatted,
                     "formula_text": spec["formula_text"]}
            for extra in ("note", "inferred"):
                if extra in spec:
                    entry[extra] = spec[extra]
            out["computed"].append(entry)
            overlay[key] = formatted
        else:
            sup = parse_amount(supplied)
            if sup is None:
                out["notes"].append(
                    {"key": key,
                     "note": f"supplied value {supplied!r} is not a number; "
                             "not checked against the printed formula"})
            elif abs(sup - result) > TOLERANCE:
                w = {"code": "COMPUTATION_MISMATCH", "key": key,
                     "supplied": supplied, "computed": formatted,
                     "formula_text": spec["formula_text"],
                     "severity": "warning"}
                if spec.get("inferred"):
                    w["inferred"] = spec["inferred"]
                out["warnings"].append(w)
            # supplied (even contradicting) always wins and feeds downstream
    return out


def evaluate_for_form(form_id: str, values: dict,
                      forms_root: str | pathlib.Path = "forms") -> dict:
    """Convenience: load + evaluate for ``<forms_root>/<form_id>/``."""
    return evaluate(load_computations(pathlib.Path(forms_root) / form_id),
                    values)
