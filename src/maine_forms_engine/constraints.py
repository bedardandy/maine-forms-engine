"""Declarative checkbox-paradox constraints — warnings only, never blocking.

A form folder may carry an optional ``constraints.json`` next to its
``mapping.json`` declaring selections that cannot logically coexist on the
printed form (a "yellow light" layer):

    {
      "form_id": "MRS-1041ME",
      "mutually_exclusive": [
        {"keys": ["facts.resident_estate_or_trust",
                   "facts.nonresident_estate_or_trust"],
         "note": "Resident / Nonresident — single-status pair"},
        ["facts.entity_type_simple_trust", "facts.entity_type_complex_trust"]
      ],
      "requires": {
        "facts.designee_pin": {"keys": ["facts.designee_name"],
                                "note": "a PIN belongs to a named designee"}
      }
    }

Shapes:

- ``mutually_exclusive``: list of groups. Each group is either a plain list
  of keys or ``{"keys": [...], "note": str, "inferred": bool}``. A warning
  fires when two or more keys in a group are affirmatively set.
- ``requires``: ``{key: [key, ...]}`` or ``{key: {"keys": [...], "note",
  "inferred"}}``. A warning fires when ``key`` is set but a required key
  is not.
- ``"inferred": true`` marks a group that was harvested/inferred rather than
  literally printed on the form ("check one box"); it travels into the
  warning so callers can weight it.

``evaluate`` accepts either a canonical fact object (nested ``{matter,
parties, party, facts}`` — keys are dotted paths) or an already-resolved
flat ``{key: value}`` dict (e.g. a court recipe ``kv`` keyed by field_id).
It returns a list of warning dicts:

    {"code": "MUTUALLY_EXCLUSIVE" | "REQUIRES", "keys": [...],
     "severity": "warning", "note": ..., "inferred": ...}

Severity is always ``"warning"``: this layer NEVER blocks or alters a fill —
no constraints file means zero behavior change.
"""
from __future__ import annotations

import json
import pathlib

CONSTRAINTS_FILENAME = "constraints.json"

# A value counts as "not selected" when it is empty or an explicit negative
# token (mirrors the fill engine's checkbox affirmative-token gate: a
# checkbox driven by "no"/"false"/"off" is left unchecked, so it cannot
# participate in a paradox).
_NEGATIVE = {"no", "n", "false", "off", "0", "unchecked", "none"}


def _resolve(key: str, values: dict) -> object:
    """Look ``key`` up flat first, then as a dotted path into a nested case."""
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


def _is_set(value: object) -> bool:
    """Is this value an affirmative selection (checkbox on / enum chosen)?"""
    if value is None or value is False:
        return False
    if isinstance(value, str):
        v = value.strip().lower()
        return bool(v) and v not in _NEGATIVE
    if isinstance(value, (int, float)):
        return bool(value)
    return bool(value)


def _norm_group(group) -> tuple[list[str], dict]:
    """Normalize a list-or-dict group to (keys, extras)."""
    if isinstance(group, dict):
        keys = list(group.get("keys") or [])
        extras = {k: group[k] for k in ("note", "inferred") if k in group}
        return keys, extras
    return list(group or []), {}


def load_constraints(form_dir: str | pathlib.Path) -> dict | None:
    """Read ``<form_dir>/constraints.json``; None when absent (the common
    case — absence means zero behavior change)."""
    p = pathlib.Path(form_dir) / CONSTRAINTS_FILENAME
    if not p.exists():
        return None
    return json.loads(p.read_text())


def evaluate(constraints: dict | None, values: dict) -> list[dict]:
    """Evaluate a constraints dict against a case / resolved-values dict.

    Returns ``[]`` when nothing fires (or ``constraints`` is None/empty).
    Warnings only — callers surface them; nothing here raises or blocks.
    """
    if not constraints:
        return []
    warnings: list[dict] = []
    for group in constraints.get("mutually_exclusive") or []:
        keys, extras = _norm_group(group)
        hot = [k for k in keys if _is_set(_resolve(k, values))]
        if len(hot) >= 2:
            warnings.append({"code": "MUTUALLY_EXCLUSIVE", "keys": hot,
                             "severity": "warning", **extras})
    for key, group in (constraints.get("requires") or {}).items():
        needed, extras = _norm_group(group)
        if not _is_set(_resolve(key, values)):
            continue
        missing = [k for k in needed if not _is_set(_resolve(k, values))]
        if missing:
            warnings.append({"code": "REQUIRES",
                             "keys": [key, *missing],
                             "severity": "warning", **extras})
    return warnings


def evaluate_for_form(form_id: str, values: dict,
                      forms_root: str | pathlib.Path = "forms") -> list[dict]:
    """Convenience: load + evaluate for ``<forms_root>/<form_id>/``."""
    return evaluate(load_constraints(pathlib.Path(forms_root) / form_id),
                    values)
