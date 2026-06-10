#!/usr/bin/env python3
"""Deterministic post-fill verification: did the values actually land?

Reopens a filled PDF, reads every widget value, and diffs them against the
intended ``field_id -> value`` dict. No model involved — this is the cheap,
machine-checkable half of verification (the vision audit judges *placement
against the printed label*; this checks *presence and value*).

Per-field statuses:

- ``placed``    — a widget for the field holds exactly the intended value
                  (checkboxes: intended affirmative and the widget is on).
- ``differs``   — a widget holds a non-empty value that is not the intended
                  one (commonly width-fit abbreviation or wrap; the actual
                  value is returned so the caller can judge).
- ``missing``   — the field's widget(s) exist but are empty.
- ``no-widget`` — no widget with that name exists in the filled PDF. This is
                  expected for multi-widget overflow groups: the filler
                  deletes those widgets and draws the wrapped text as page
                  content, which widget reads cannot see.

CLI:
    python3 -m engine.verify_fill --form AD-001 --pdf /tmp/out/AD-001.filled.pdf \
        --intended intended.json
"""
from __future__ import annotations

import argparse
import json
import pathlib

# Packaged change (see CHANGES_FROM_DONOR.md): repo-root anchor replaced by a
# --forms-root CLI argument (default: cwd-relative "forms").
DEFAULT_FORMS_ROOT = pathlib.Path("forms")

_AFFIRMATIVE = {"x", "✓", "yes", "y", "1", "true", "on",
                "checked", "[x]", "selected"}
_CHECKBOX_ON = {"yes", "on", "x", "1", "true", "checked"}


def _widget_values(pdf_path: pathlib.Path) -> dict[str, list[dict]]:
    """name -> [{value, page, type}] for every widget in the PDF."""
    import fitz
    out: dict[str, list[dict]] = {}
    doc = fitz.open(str(pdf_path))
    type_names = {
        fitz.PDF_WIDGET_TYPE_TEXT: "text",
        fitz.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
        fitz.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
    }
    for page_idx, page in enumerate(doc):
        for w in page.widgets() or []:
            out.setdefault(w.field_name, []).append({
                "value": (w.field_value or "").strip()
                          if isinstance(w.field_value, str)
                          else (w.field_value or ""),
                "page": page_idx,
                "type": type_names.get(w.field_type, "other"),
            })
    doc.close()
    return out


def _fid_labels(schema: dict | None) -> dict[str, list[str]]:
    """field_id -> widget label(s); identity when no schema is given."""
    if not schema:
        return {}
    fid_to_widgets: dict[str, list[str]] = {}
    for f in schema.get("fields", []):
        fid_to_widgets.setdefault(f["field_id"], []).append(f["label"])
    return fid_to_widgets


def verify_fill(pdf_path: str | pathlib.Path, intended: dict[str, str],
                schema: dict | None = None) -> dict:
    """Diff a filled PDF's widget values against the intended fid->value map.

    Args:
        pdf_path: the filled PDF.
        intended: field_id -> intended value (empty values are skipped).
        schema: the form's schema.json dict (maps field_id -> widget label).
            When omitted, intended keys are treated as widget names directly.

    Returns ``{"fields": {fid: {...}}, "summary": {...}, "ok": bool}`` —
    ``ok`` is True when nothing is missing (differs/no-widget are surfaced
    but don't fail the check on their own).
    """
    widgets = _widget_values(pathlib.Path(pdf_path))
    fid_to_widgets = _fid_labels(schema)
    fields: dict[str, dict] = {}
    counts = {"placed": 0, "differs": 0, "missing": 0, "no-widget": 0}

    for fid, value in intended.items():
        if value in (None, ""):
            continue
        labels = fid_to_widgets.get(fid, [fid])
        entries = [e for lb in labels for e in widgets.get(lb, [])]
        if not entries:
            status, actual, page = "no-widget", None, None
        else:
            sval = str(value).strip()
            placed = differs = None
            for e in entries:
                actual_v = str(e["value"]).strip()
                if e["type"] in ("checkbox", "radio"):
                    want_on = sval.lower() in _AFFIRMATIVE
                    is_on = actual_v.lower() in _CHECKBOX_ON
                    if want_on == is_on:
                        placed = e
                        break
                    if actual_v:
                        differs = e
                elif actual_v == sval:
                    placed = e
                    break
                elif actual_v:
                    differs = differs or e
            if placed is not None:
                status, actual, page = "placed", placed["value"], placed["page"]
            elif differs is not None:
                status, actual, page = "differs", differs["value"], differs["page"]
            else:
                status, actual, page = "missing", "", entries[0]["page"]
        counts[status] += 1
        fields[fid] = {"placed": status == "placed", "status": status,
                       "expected": value, "value": actual, "page": page}

    summary = {"intended": sum(counts.values()), **counts}
    return {"ok": counts["missing"] == 0, "fields": fields,
            "summary": summary}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--form", help="form id (loads <forms-root>/<ID>/schema.json)")
    ap.add_argument("--forms-root", type=pathlib.Path,
                    default=DEFAULT_FORMS_ROOT,
                    help="per-form artifact tree (default: ./forms)")
    ap.add_argument("--pdf", type=pathlib.Path, required=True,
                    help="the filled PDF to verify")
    ap.add_argument("--intended", type=pathlib.Path, required=True,
                    help="JSON file: {field_id: intended value, ...} "
                         "(e.g. the kv from <ID>.kv.json, or "
                         "resolve_mapping's fid_value)")
    args = ap.parse_args()
    intended = json.loads(args.intended.read_text())
    if isinstance(intended, dict) and "kv" in intended:
        intended = intended["kv"]
    schema = None
    if args.form:
        sp = args.forms_root / args.form / "schema.json"
        if sp.exists():
            schema = json.loads(sp.read_text())
    res = verify_fill(args.pdf, intended, schema)
    print(json.dumps(res, indent=2))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
