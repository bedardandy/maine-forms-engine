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
from .form_filler import fill_form
from .field_split import split_to_copy
from .text_fit import fit as _fit, widget_char_budget

# Packaged change (see CHANGES_FROM_DONOR.md): the donor resolved forms/ from
# its repo root via __file__; the package defaults to cwd-relative "forms" and
# consumers pass forms_root explicitly. The blank-revision manifest is looked
# up next to forms_root (<forms_root>/../catalog/pdf_manifest.json), matching
# the donor repo layout.
DEFAULT_FORMS_ROOT = pathlib.Path("forms")

_NAME_KEY_SUFFIX = (".full_name", ".first_name", ".middle_name", ".last_name")


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
                    forms_root: pathlib.Path = DEFAULT_FORMS_ROOT) -> dict:
    """Resolve a form's mapping.json against a canonical fact object.

    Pure (no PDF needed): returns coverage stats + the field_id->value map.
    """
    fdir = forms_root / form_id
    mapping = json.loads((fdir / "mapping.json").read_text())
    if mapping.get("status") == "recipe":
        return {"form_id": form_id, "status": "recipe", "skipped": True,
                "reason": "mapping.json is a pointer; use engine.fill"}
    if mapping.get("status") == "no-mappable-fields":
        return {"form_id": form_id, "status": "no-mappable-fields",
                "skipped": True,
                "reason": "form has no mappable fields (informational/"
                          "court-completed form); nothing to fill"}
    m = mapping.get("map") or {}
    fid_value, unresolved = {}, []
    for fid, key in m.items():
        v = _resolve_key(key, facts)
        if v is not None and v != "":
            fid_value[fid] = v
        else:
            unresolved.append((fid, key))
    schema = json.loads((fdir / "schema.json").read_text())
    total_fields = len(schema.get("fields", []))
    return {
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


def fill_via_mapping(form_id: str, facts: dict, out_dir: pathlib.Path,
                     forms_root: pathlib.Path = DEFAULT_FORMS_ROOT) -> dict:
    """Resolve mapping.json and write a filled PDF."""
    res = resolve_mapping(form_id, facts, forms_root)
    if res.get("skipped"):
        return {"form_id": form_id, "ok": False, "error": res["reason"]}
    fdir = forms_root / form_id
    pdf = fdir / f"{form_id}.pdf"
    if not pdf.exists():
        return {"form_id": form_id, "ok": False,
                "error": f"blank PDF not found: {pdf} (run tools/fetch_pdfs.py)"}
    # Guard: the on-disk blank must be the revision this mapping was built
    # against (catalog/pdf_manifest.json). A mismatch warns by default; set
    # MCF_VERIFY_BLANK=strict to refuse, =off to skip. The outcome is also
    # captured into the result dict (callers rarely see Python warnings).
    blank_mode = os.environ.get("MCF_VERIFY_BLANK", "warn")
    # Packaged change: resolve the manifest next to forms_root (the donor used
    # its repo-root manifest); falls back to verify's cwd-relative default.
    _man_path = pathlib.Path(forms_root).resolve().parent / "catalog" / "pdf_manifest.json"
    _manifest = verify.load_manifest(_man_path) if _man_path.exists() else None
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
    field_data: dict[str, str] = {}
    for fid, v in fitted.items():
        for label in fid_to_widgets.get(fid, []):
            field_data[label] = v
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
    fill_form(str(pdf), field_data, str(out_pdf),
              form_id=form_id, addendum_policy="none")
    if n_split:  # drop the split working copy; the deliverable is .filled.pdf
        try:
            (out_dir / f"{form_id}.split.pdf").unlink()
        except OSError:
            pass
    out = {"form_id": form_id, "ok": True, "out_pdf": str(out_pdf),
           "mapped_keys": res["mapped_keys"], "resolved": res["resolved"],
           "coverage": (round(res["resolved"] / res["mapped_keys"], 3)
                        if res["mapped_keys"] else 0.0),
           "unresolved": [{"field_id": fid, "key": key}
                          for fid, key in res["unresolved"]],
           "fields_written": len(field_data), "fields_split": n_split,
           "blank_verify": {"mode": blank_mode, "ok": blank_ok,
                            "detail": blank_detail}}
    if split_skipped:
        out["split_step_skipped"] = split_skipped
    if res["resolved"] == 0 and res["mapped_keys"]:
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
