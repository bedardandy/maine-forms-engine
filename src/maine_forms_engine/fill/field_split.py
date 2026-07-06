#!/usr/bin/env python3
"""Runtime split of shared AcroForm fields, driven by ``field_splits.json``.

Some court source PDFs reuse ONE AcroForm field across semantically different
boxes — e.g. OTH-029 field ``2_5`` is a child-DOB table cell on pg4 AND the
confidential "Mailing address" line on pg12. Because both are kid widgets of a
single field they always hold the same value, so a value mapped to the table
cell *fans out* onto the unrelated box (and a screen reader can name the field
only once). This module detaches a named appearance into its OWN terminal
field so it stops inheriting the other box's value.

Driven by a per-form spec at ``forms/<ID>/field_splits.json``:

    {"splits": [
      {"field": "2_5", "page": 12, "new_name": "confidential_mailing_address",
       "clear": true}
    ]}

``clear`` (default true) blanks the detached widget — showing a blank is
correct when no distinct fact feeds that box (better blank than the other
appearance's wrong value).

This is the shared core used by BOTH the fill path (``fill_via_mapping``, so a
plain fill no longer fans) and the accessibility path
(``tools/accessibility/split_shared_fields.py``, for ``/TU`` per appearance).
Always operates on a working copy via pikepdf; the repo blank is never touched.
"""
from __future__ import annotations

import json
import pathlib

# Packaged change (see CHANGES_FROM_DONOR.md): the donor resolved forms/
# relative to its repo root via __file__; a pip-installed package has no repo
# root, so the default is cwd-relative "forms" and callers pass forms_root.
DEFAULT_FORMS_ROOT = pathlib.Path("forms")


def _page_index(pdf, widget) -> int | None:
    p = widget.get("/P")
    if p is None:
        return None
    for i, pg in enumerate(pdf.pages):
        if pg.objgen == p.objgen:
            return i
    return None


def _find_field(fields, name):
    for f in fields:
        if "/T" in f and str(f["/T"]) == name:
            return f
        if "/Kids" in f and "/FT" not in f:
            r = _find_field(f["/Kids"], name)
            if r is not None:
                return r
    return None


def split(pdf, specs: list[dict]) -> int:
    """Apply field splits to an open pikepdf. Returns the number performed."""
    import pikepdf

    acro = pdf.Root.AcroForm.Fields
    done = 0
    for spec in specs:
        fld = _find_field(acro, spec["field"])
        if fld is None or "/Kids" not in fld:
            continue
        kids = fld["/Kids"]
        target = None
        for k in kids:
            if _page_index(pdf, k) == spec["page"] - 1:
                target = k
                break
        if target is None:
            continue
        # detach the target widget from the shared field -> its own terminal field
        remaining = [k for k in kids if k.objgen != target.objgen]
        fld["/Kids"] = pikepdf.Array(remaining)
        target["/T"] = pikepdf.String(spec["new_name"])
        if "/FT" not in target and "/FT" in fld:
            target["/FT"] = fld["/FT"]
        if "/DA" not in target and "/DA" in fld:
            target["/DA"] = fld["/DA"]
        if "/Parent" in target:
            del target["/Parent"]
        if spec.get("clear", True):
            for k in ("/V", "/AP", "/AS"):
                if k in target:
                    del target[k]
        acro.append(target)
        done += 1
    if done:
        # values changed -> let viewers rebuild appearances
        try:
            pdf.Root.AcroForm["/NeedAppearances"] = True
        except Exception:  # noqa: BLE001
            pass
    return done


def specs_for(form_id: str, forms_root: pathlib.Path | None = None) -> list[dict]:
    root = forms_root or DEFAULT_FORMS_ROOT
    p = root / form_id / "field_splits.json"
    if not p.exists():
        return []
    return json.loads(p.read_text()).get("splits", [])


def split_to_copy(src_pdf: pathlib.Path, dst_pdf: pathlib.Path,
                  form_id: str, forms_root: pathlib.Path | None = None) -> int:
    """If the form has split specs, apply them to a working copy at dst_pdf.

    Returns the number of splits performed (0 if no specs / nothing to do).
    The source PDF is never modified.
    """
    specs = specs_for(form_id, forms_root)
    if not specs:
        return 0
    import pikepdf

    # Context manager so the file handle is released even if split()/save()
    # raises — this runs per fill inside the long-lived MCP server every
    # sibling repo hosts, where leaked handles accumulate.
    with pikepdf.open(str(src_pdf)) as pdf:
        n = split(pdf, specs)
        pdf.save(str(dst_pdf))
    return n
