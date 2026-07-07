#!/usr/bin/env python3
"""Fill a form directly from its ``mapping.json`` + a canonical fact object.

This is the **mapping.json-driven** fill path, and it is deliberately separate
from ``engine/fill.py``:

- ``engine/fill.py`` runs the generic ``map_form`` + form recipes from an
  engine-shape case. It never reads ``mapping.json``.
- this module resolves each canonical fact-key in a form's ``mapping.json``
  against a canonical fact object and writes the result to the mapped widget.

So this is what an external adapter (docassemble, LangChain, ...) conceptually
does, and it's how you *verify* a `mapping.json`: fill from it, then check the
output. Recipe-tier forms have a pointer-only ``mapping.json`` (empty ``map``)
and are skipped — use ``engine/fill.py`` for those.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import pathlib

from . import verify
from .form_filler import fill_form, match_radio_option
from .field_split import split_to_copy
from .text_fit import fit as _fit, widget_char_budget

# Packaged change (see CHANGES_FROM_DONOR.md): the donor resolved forms/ from
# its repo root via __file__; the package defaults to cwd-relative "forms" and
# consumers pass forms_root explicitly. The blank-revision manifest is looked
# up next to forms_root (<forms_root>/../catalog/pdf_manifest.json), matching
# the donor repo layout.
DEFAULT_FORMS_ROOT = pathlib.Path("forms")

# Default (court-donor) status handling is a blocklist: these two statuses are
# refused, anything else fills. A consumer can instead pass an allowlist via
# ``fillable_statuses`` (the transactional-tax-forms policy, e.g. refusing
# "remap-pending" after upstream drift) and override/extend the reasons via
# ``skip_reasons``.
DEFAULT_SKIP_REASONS = {
    "recipe": "mapping.json is a pointer; use engine.fill",
    "no-mappable-fields": ("form has no mappable fields (informational/"
                           "court-completed form); nothing to fill"),
}
_BLOCKED_STATUSES = frozenset(DEFAULT_SKIP_REASONS)

_NAME_KEY_SUFFIX = (".full_name", ".first_name", ".middle_name", ".last_name")


def _manifest_for(forms_root, manifest_path):
    """The pinned-revision manifest for this tree: explicit ``manifest_path``,
    else ``<forms_root>/../catalog/pdf_manifest.json`` (the shared repo
    layout), else ``None`` (verify falls back to its cwd-relative default)."""
    if manifest_path is not None:
        return verify.load_manifest(manifest_path)
    p = pathlib.Path(forms_root).resolve().parent / "catalog" / "pdf_manifest.json"
    return verify.load_manifest(p) if p.exists() else None


def _width_fit(fid_value: dict, fid_key: dict, rect_by_fid: dict) -> dict:
    """Shrink overflowing names/addresses to their widget's char budget.

    Mirrors the engine fill_one pass: names initial-collapse (never truncate a
    legal name), addresses postal-abbreviate. Generic/narrative values are left
    for form_filler's font auto-fit.
    """
    out = dict(fid_value)
    for fid, v in fid_value.items():
        rect = rect_by_fid.get(fid)
        if not rect or not isinstance(v, str):
            continue
        budget = widget_char_budget(rect)
        if len(v) <= budget:
            continue
        key = fid_key.get(fid, "")
        if key.endswith(_NAME_KEY_SUFFIX):
            out[fid] = _fit(v, budget, name=True)
        elif key.endswith(".address"):
            out[fid] = _fit(v, budget, address=True)
    return out


def _split_name(full: str, part: str) -> str | None:
    """Derive a name part from a full name ("Jane Q. Doe")."""
    toks = [t for t in str(full).split() if t]
    if not toks:
        return None
    if part == "first_name":
        return toks[0]
    if part == "last_name":
        return toks[-1] if len(toks) > 1 else None
    if part == "middle_name":
        return " ".join(toks[1:-1]) or None
    return None


def _resolve_key(key: str, facts: dict) -> str | None:
    """Resolve a canonical fact-key against a canonical fact object.

    ``today()`` is computed; dotted keys (``matter.docket_number``,
    ``parties.plaintiff.full_name``) walk the object. Returns a string value or
    None if the key is absent / non-scalar.
    """
    if key == "today()":
        return datetime.date.today().strftime("%m/%d/%Y")
    parts = key.split(".")
    parent: object = facts
    for p in parts[:-1]:
        if isinstance(parent, dict) and p in parent:
            parent = parent[p]
        else:
            return None
    last = parts[-1]
    if not isinstance(parent, dict):
        return None
    if last in parent:
        cur = parent[last]
    elif last in ("first_name", "middle_name", "last_name") and \
            isinstance(parent.get("full_name"), str):
        # Derive a name part from full_name when the contract's split-name
        # keys aren't supplied explicitly (forms with First/Middle/Last boxes).
        cur = _split_name(parent["full_name"], last)
    else:
        return None
    if isinstance(cur, (str, int, float)):
        s = str(cur)
        # Render ISO dates the way the forms expect (mm/dd/yyyy).
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            try:
                return datetime.date.fromisoformat(s[:10]).strftime("%m/%d/%Y")
            except ValueError:
                pass
        return s
    return None


def resolve_mapping(form_id: str, facts: dict,
                    forms_root: pathlib.Path = DEFAULT_FORMS_ROOT, *,
                    fillable_statuses: frozenset | set | None = None,
                    skip_reasons: dict | None = None,
                    require_built_against: bool = False,
                    manifest_path: pathlib.Path | None = None) -> dict:
    """Resolve a form's mapping.json against a canonical fact object.

    Pure (no PDF needed): returns coverage stats + the field_id->value map.

    Policy hooks (defaults = the court-donor behavior; the tax consumer
    configures all three — see CHANGES_FROM_DONOR.md):

    - ``fillable_statuses``: ``None`` (default) refuses only the blocklisted
      statuses ("recipe", "no-mappable-fields"); a set makes it an allowlist —
      any other status (e.g. "remap-pending", "unmapped") is refused with a
      machine-readable reason instead of silently writing a partial fill.
    - ``skip_reasons``: per-status overrides/additions to the refusal reasons.
    - ``require_built_against``: when True, a mapping that records the blank
      revision it was built against (``built_against_sha256``) is only
      fillable while the manifest still pins that same revision. On drift the
      status flag alone can lie (the tax repo's MRS-1041ME incident: status
      said fillable while 37 mapped widgets no longer existed); the hash
      comparison cannot. ``manifest_path`` overrides the manifest location
      (default: ``<forms_root>/../catalog/pdf_manifest.json``).
    """
    fdir = forms_root / form_id
    mapping = json.loads((fdir / "mapping.json").read_text())
    status = mapping.get("status")
    reasons = {**DEFAULT_SKIP_REASONS, **(skip_reasons or {})}
    refused = (status not in fillable_statuses
               if fillable_statuses is not None
               else status in _BLOCKED_STATUSES)
    if refused:
        reason = reasons.get(status) or (
            f"mapping status {status!r} is not fillable (fillable statuses: "
            f"{', '.join(sorted(fillable_statuses or ()))})")
        return {"form_id": form_id, "status": status, "skipped": True,
                "reason": reason}
    if require_built_against:
        built = (mapping.get("built_against_sha256") or "").lower()
        if built:
            manifest = _manifest_for(forms_root, manifest_path)
            entry = verify.manifest_entry(form_id, manifest) if manifest else None
            pinned = ((entry or {}).get("sha256") or "").lower()
            if pinned and built != pinned:
                return {
                    "form_id": form_id, "status": status, "skipped": True,
                    "reason": (f"mapping.json was built against blank revision "
                               f"{built[:12]}… but catalog/pdf_manifest.json now "
                               f"pins {pinned[:12]}… — the upstream blank "
                               "drifted; re-map before filling")}
    m = mapping.get("map") or {}
    fid_value, unresolved = {}, []
    for fid, key in m.items():
        v = _resolve_key(key, facts)
        if v is not None and v != "":
            fid_value[fid] = v
        else:
            unresolved.append((fid, key))
    # Computed fields (computations.json next to mapping.json; see
    # maine_forms_engine.computations): a target the case omits is computed
    # from the form's own printed arithmetic and filled; a supplied value is
    # written AS-IS — a contradiction only yields a COMPUTATION_MISMATCH
    # warning. No computations file = no keys, zero behavior change.
    from ..computations import evaluate as _eval_computations
    from ..computations import load_computations as _load_computations
    _computations = _load_computations(fdir)
    _comp_extra: dict = {}
    if _computations is not None:
        _comp = _eval_computations(_computations, facts)
        _by_key = {e["key"]: e for e in _comp["computed"]}
        _filled_keys, _still = set(), []
        for fid, key in unresolved:
            if key in _by_key:
                fid_value[fid] = _by_key[key]["value"]
                _filled_keys.add(key)
            else:
                _still.append((fid, key))
        unresolved = _still
        _comp_extra["computed_fields"] = [e for e in _comp["computed"]
                                          if e["key"] in _filled_keys]
        _comp_extra["computation_warnings"] = _comp["warnings"]
        if _comp["notes"]:
            _comp_extra["computation_notes"] = _comp["notes"]
    schema = json.loads((fdir / "schema.json").read_text())
    total_fields = len(schema.get("fields", []))
    out = {
        **_comp_extra,
        "form_id": form_id,
        "status": mapping.get("status"),
        "total_fields": total_fields,
        "mapped_keys": len(m),
        "resolved": len(fid_value),
        "unresolved": unresolved,
        "fid_value": fid_value,
        "_map": m,
        "_schema": schema,
    }
    # Manual-fill entries ("fill": "manual" — radio groups the engine never
    # writes). The canonical key is resolved against the case purely to
    # SUGGEST an option; nothing is ever written for these fields.
    manual = _manual_entries(mapping, facts)
    if manual:
        out["manual_fields"] = manual
    return out


def _manual_entries(mapping: dict, facts: dict) -> list[dict]:
    """Resolve a mapping's optional ``manual`` block into yellow-light
    entries: ``{field_id, kind, options, key, suggested, action[, note]}``.

    Dialect: ``mapping.json`` may carry, next to ``map``::

        "manual": {
          "<field_id>": {"fill": "manual", "kind": "radio_group",
                          "key": "facts.residency_status",
                          "options": ["Resident", "Nonresident"],
                          "note": "..."}
        }
    """
    entries = []
    for fid, spec in (mapping.get("manual") or {}).items():
        if not isinstance(spec, dict) or spec.get("fill") != "manual":
            continue
        options = list(spec.get("options") or [])
        key = spec.get("key")
        resolved = _resolve_key(key, facts) if key else None
        entry = {
            "field_id": fid,
            "kind": spec.get("kind") or "radio_group",
            "options": options,
            "key": key,
            "suggested": match_radio_option(resolved, options),
            "action": "manual selection required",
        }
        if spec.get("note"):
            entry["note"] = spec["note"]
        entries.append(entry)
    return entries


def fill_via_mapping(form_id: str, facts: dict, out_dir: pathlib.Path,
                     forms_root: pathlib.Path = DEFAULT_FORMS_ROOT, *,
                     fillable_statuses: frozenset | set | None = None,
                     skip_reasons: dict | None = None,
                     require_built_against: bool = False,
                     manifest_path: pathlib.Path | None = None,
                     blank_verify_env: tuple = ("MCF_VERIFY_BLANK",),
                     result_style: str = "court") -> dict:
    """Resolve mapping.json and write a filled PDF.

    Policy hooks: ``fillable_statuses`` / ``skip_reasons`` /
    ``require_built_against`` / ``manifest_path`` pass through to
    :func:`resolve_mapping`. ``blank_verify_env`` names the environment
    variable(s) consulted (first one set wins) for the fill-time blank guard
    mode — the tax consumer reads ``TTF_VERIFY_BLANK`` with
    ``MCF_VERIFY_BLANK`` as a fallback. ``result_style`` selects the result
    dialect the donors had diverged on:

    - ``"court"`` (default): coverage ratio, ``unresolved`` as
      ``[{"field_id", "key"}]``, ``fields_written`` = widgets *requested*,
      ``blank_verify`` mode/ok/detail dict, and the zero-resolved loud
      failure.
    - ``"tax"``: ``unresolved`` as ``[[field_id, key]]``, ``fields_written``
      = widgets *actually written* by the filler, plus ``missing_widgets`` /
      ``overflowed`` diagnostics and a ``blank_verified`` bool.
    """
    res = resolve_mapping(form_id, facts, forms_root,
                          fillable_statuses=fillable_statuses,
                          skip_reasons=skip_reasons,
                          require_built_against=require_built_against,
                          manifest_path=manifest_path)
    if res.get("skipped"):
        return {"form_id": form_id, "ok": False, "skipped": True,
                "status": res.get("status"), "error": res["reason"]}
    fdir = forms_root / form_id
    pdf = fdir / f"{form_id}.pdf"
    if not pdf.exists():
        return {"form_id": form_id, "ok": False,
                "error": f"blank PDF not found: {pdf} (run tools/fetch_pdfs.py)"}
    # Guard: the on-disk blank must be the revision this mapping was built
    # against (catalog/pdf_manifest.json). A mismatch warns by default; set
    # <first blank_verify_env var>=strict to refuse, =off to skip. The outcome
    # is captured into the result dict (callers rarely see Python warnings).
    blank_mode = next((os.environ[v] for v in blank_verify_env
                       if os.environ.get(v)), "warn")
    # Packaged change: resolve the manifest next to forms_root (the donor used
    # its repo-root manifest); falls back to verify's cwd-relative default.
    _manifest = _manifest_for(forms_root, manifest_path)
    import warnings as _warnings
    with _warnings.catch_warnings(record=True) as _blank_warns:
        _warnings.simplefilter("always")
        blank_ok = verify.guard_blank(form_id, forms_root, mode=blank_mode,
                                      manifest=_manifest)
    blank_detail = "; ".join(str(w.message) for w in _blank_warns) or None
    for w in _blank_warns:  # re-emit so warning-based callers still see it
        _warnings.warn_explicit(w.message, w.category, w.filename, w.lineno)
    # Width-fit overflowing values to their widget's char budget (mirrors the
    # engine's fill_one pass): names initial-collapse, addresses postal-
    # abbreviate, so a long real-world value shrinks instead of clipping.
    rect_by_fid = {f["field_id"]: f.get("rect") for f in res["_schema"]["fields"]}
    fitted = _width_fit(res["fid_value"], res["_map"], rect_by_fid)

    # field_id -> widget label(s) (a field_id may back multiple widgets).
    fid_to_widgets: dict[str, list[str]] = {}
    for f in res["_schema"]["fields"]:
        fid_to_widgets.setdefault(f["field_id"], []).append(f["label"])
    # Build the label-keyed fill dict. Because fill_form is keyed by widget
    # LABEL, two *different* field_ids that resolve to the SAME widget label
    # collide: the later write silently overwrites the earlier one and only
    # one value reaches the PDF. That is distinct from the shared-AcroForm-
    # field case (ONE field_id, many appearances) that field_split.py handles
    # by detaching an appearance; here it is genuinely conflicting values for
    # one box, so we can't split it — we surface it. We also make `resolved`
    # truthful: it must reflect widgets actually written, not the resolved
    # field_id count (which double-counts field_ids the collision dropped).
    field_data: dict[str, str] = {}
    # label -> the field_id that first claimed it (for conflict reporting).
    label_owner: dict[str, str] = {}
    label_conflicts: list[dict] = []
    # field_ids that resolved to a value but had ALL their widget labels
    # overwritten by a later, differing field_id (so nothing they resolved
    # actually reached the PDF).
    shadowed_fids: set[str] = set()
    for fid, v in fitted.items():
        for label in fid_to_widgets.get(fid, []):
            if label in field_data and field_data[label] != v:
                # Different field_id, different value, same widget label:
                # a silent overwrite. Record it. Last-write-wins is kept so
                # the PDF byte-output is unchanged from prior behavior — the
                # fix is to stop lying about it, not to reshuffle who wins.
                prev_fid = label_owner.get(label)
                label_conflicts.append({
                    "label": label,
                    "kept_field_id": fid,
                    "kept_value": v,
                    "dropped_field_id": prev_fid,
                    "dropped_value": field_data[label],
                })
                if prev_fid is not None:
                    shadowed_fids.add(prev_fid)
            field_data[label] = v
            label_owner[label] = fid
    # A field_id is only truly shadowed if NONE of its labels survived. Re-add
    # any that still own at least one label (e.g. a fid backing two widgets
    # where only one collided).
    surviving_fids = set(label_owner.values())
    shadowed_fids -= surviving_fids
    # Truthful resolution count: resolved field_ids minus those whose written
    # value was entirely dropped by a collision. With no collisions this is
    # unchanged (== res["resolved"]), so existing consumers see no drift.
    resolved_written = res["resolved"] - len(shadowed_fids)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Split any shared AcroForm fields (forms/<ID>/field_splits.json) on a
    # working copy first, so a value mapped to one appearance no longer fans
    # out onto the field's other, semantically different appearance — e.g.
    # OTH-029 `2_5` is a child-DOB table cell AND a "Mailing address" line.
    # The detached appearance is renamed + blanked, so the mapped value lands
    # only on its intended box. The repo blank is never modified.
    n_split = 0
    split_skipped = None
    try:
        split_src = out_dir / f"{form_id}.split.pdf"
        n_split = split_to_copy(pdf, split_src, form_id, forms_root)
        if n_split:
            pdf = split_src
    except Exception as e:  # noqa: BLE001 — never block a fill on the split step
        n_split = 0
        split_skipped = f"{type(e).__name__}: {e}"
        logging.getLogger(__name__).warning(
            "%s: shared-field split step skipped (%s) — mapped values may "
            "fan out to a shared field's other appearances; "
            "pip install pikepdf to enable the split guard", form_id,
            split_skipped)
    out_pdf = out_dir / f"{form_id}.filled.pdf"
    fill_res = fill_form(str(pdf), field_data, str(out_pdf),
                         form_id=form_id, addendum_policy="none",
                         return_report=True)
    if n_split:  # drop the split working copy; the deliverable is .filled.pdf
        try:
            (out_dir / f"{form_id}.split.pdf").unlink()
        except OSError:
            pass
    # Yellow light #1 — radio groups: entries declared "fill": "manual" in
    # mapping.json (suggestion resolved from the case) merged with any group
    # the engine's safety net skipped at write time. Never blocks; the PDF is
    # written either way, with the radio group left untouched.
    radio_entries = {e["field_id"]: dict(e)
                     for e in res.get("manual_fields") or []}
    label_to_fid: dict[str, str] = {}
    for f in res["_schema"]["fields"]:
        label_to_fid.setdefault(f["label"], f["field_id"])
    for e in fill_res.get("radio_groups_skipped") or []:
        fid = label_to_fid.get(e["field_id"], e["field_id"])
        if fid not in radio_entries:
            radio_entries[fid] = {**e, "field_id": fid}
    # Yellow light #2 — declarative paradox constraints (constraints.json
    # next to mapping.json; see maine_forms_engine.constraints). Warnings
    # only; no constraints file = no key, zero behavior change.
    from ..constraints import evaluate as _eval_constraints
    from ..constraints import load_constraints as _load_constraints
    _constraints = _load_constraints(fdir)
    _extra: dict = {}
    if radio_entries:
        _extra["radio_groups"] = list(radio_entries.values())
    if _constraints is not None:
        _extra["constraint_warnings"] = _eval_constraints(_constraints, facts)
    # Yellow light #3 — computed fields (computations.json; resolved by
    # resolve_mapping). Computed values were filled; supplied values were
    # written as-is, contradictions surface as COMPUTATION_MISMATCH.
    for _k in ("computed_fields", "computation_warnings",
               "computation_notes"):
        if _k in res:
            _extra[_k] = res[_k]
    if label_conflicts:
        # Additive diagnostic: two field_ids resolved to one widget label; the
        # loser's value never reached the PDF. Present on both result styles.
        _extra["label_conflicts"] = label_conflicts
    if result_style == "tax":
        return {
            **_extra,
            "form_id": form_id, "ok": True, "status": res["status"],
            "out_pdf": str(out_pdf),
            "mapped_keys": res["mapped_keys"], "resolved": resolved_written,
            # canonical keys that resolved to nothing in the case object
            "unresolved": [list(u) for u in res["unresolved"]],
            # widgets actually written, counted by the filler (not the request)
            "fields_written": fill_res["filled_count"],
            # mapped widget names absent from the PDF — a stale-mapping signal
            "missing_widgets": fill_res["missing_fields"],
            "overflowed": fill_res["overflowed"],
            "blank_verified": blank_ok,
            "fields_split": n_split,
        }
    out = {**_extra,
           "form_id": form_id, "ok": True, "out_pdf": str(out_pdf),
           "mapped_keys": res["mapped_keys"], "resolved": resolved_written,
           "coverage": (round(resolved_written / res["mapped_keys"], 3)
                        if res["mapped_keys"] else 0.0),
           "unresolved": [{"field_id": fid, "key": key}
                          for fid, key in res["unresolved"]],
           "fields_written": len(field_data), "fields_split": n_split,
           "blank_verify": {"mode": blank_mode, "ok": blank_ok,
                            "detail": blank_detail}}
    if split_skipped:
        out["split_step_skipped"] = split_skipped
    if resolved_written == 0 and res["mapped_keys"]:
        # A zero-resolved fill is a blank PDF — almost always a fact-object
        # shape problem (engine-shape case passed to the canonical-mapping
        # path). Surface it as a failure instead of a silent near-blank.
        out["ok"] = False
        out["error"] = (f"0 of {res['mapped_keys']} mapped keys resolved — "
                        "no fields were filled. Check that the fact object "
                        "is canonical-shape ({matter, parties, party, "
                        "facts}); see docs/integrations/README.md.")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--form", required=True)
    ap.add_argument("--case", type=pathlib.Path,
                    help="canonical fact object JSON "
                         "(default: the form's examples/sample_case.json)")
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path("/tmp/mapping_fill"))
    ap.add_argument("--forms-root", type=pathlib.Path,
                    default=DEFAULT_FORMS_ROOT,
                    help="per-form artifact tree (default: ./forms)")
    args = ap.parse_args()
    fdir = args.forms_root / args.form
    case_path = args.case or (fdir / "examples" / "sample_case.json")
    facts = json.loads(case_path.read_text())
    res = fill_via_mapping(args.form, facts, args.out,
                           forms_root=args.forms_root)
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
