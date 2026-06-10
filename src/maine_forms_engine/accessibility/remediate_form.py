#!/usr/bin/env python3
"""Schema/caption-driven accessibility remediation for a FILLED court form.

Ported from the maine-probate-forms-oss accessibility builder, with one critical
adaptation. In probate, schema.json's `label` is human-readable ("County Probate
Court"), so `/TU` (the accessible field name a screen reader announces) can be
the label directly. In maine-court-forms the `label` is the raw AcroForm field
NAME — often non-descriptive ("1", "2", "1_2", "2_5", "mmddyyyy_2") and, as the
sentinel/lint work found, frequently MISALIGNED with the box's real meaning. So
announcing the schema label would read "two underscore five" to a blind user.

Instead we derive each widget's accessible name from the box's **printed
caption** (the same nearest-caption geometry the key-family lint uses), falling
back to a humanized form of its mapped canonical key, then the schema label:

    /TU  =  printed caption           (e.g. "Telephone number")
        |   humanized mapping key      ("parties.defendant.phone" -> "Defendant phone")
        |   schema label               (last resort)

Everything else matches probate: /Info+XMP title + DisplayDocTitle (2.4.2),
/Root /Lang (3.1.1), /Tabs=/S logical tab order (2.4.3). Field names are WCAG
1.3.1 / 4.1.2. This does NOT build the content tag tree (7.1/7.2) — that is
OpenDataLoader's job in accessibility_pipeline.py.

    python3 remediate_form.py <filled.pdf> <out.pdf> --schema forms/<ID>/schema.json
                                                      [--title "..."] [--lang en-US]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import fitz  # PyMuPDF — for caption geometry off the filled page
import pikepdf

_ROLE_WORD = {"plaintiff": "Plaintiff", "defendant": "Defendant",
              "attorney": "Attorney", "other_party": "Other party",
              "party": "Your", "child_1": "Child 1", "child_2": "Child 2",
              "child_3": "Child 3"}
_FIELD_WORD = {"full_name": "name", "first_name": "first name",
               "middle_name": "middle name", "last_name": "last name",
               "address": "address", "city": "city", "state": "state",
               "zip": "ZIP", "phone": "telephone", "email": "email",
               "date_of_birth": "date of birth", "signature": "signature"}
_MATTER_WORD = {"court_location": "Court location", "docket_number": "Docket number",
                "court_county": "County", "court_type": "Court type"}


def _humanize_key(key: str | None) -> str | None:
    """A readable accessible name from a canonical fact-key, or None."""
    if not isinstance(key, str) or not key:
        return None
    if key == "today()":
        return "Date"
    parts = key.split(".")
    if parts[0] == "matter":
        return _MATTER_WORD.get(parts[-1])
    if parts[0] == "party":
        f = _FIELD_WORD.get(parts[-1])
        return f"Your {f}" if f else None
    if parts[0] == "parties" and len(parts) >= 3:
        role = _ROLE_WORD.get(parts[1], parts[1].replace("_", " ").title())
        f = _FIELD_WORD.get(parts[-1], parts[-1].replace("_", " "))
        return f"{role} {f}"
    return None


def _clean_caption(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = text.rstrip(":").strip()
    # drop a leading enumerator ("1.", "C.", "(a)") that isn't a name
    text = re.sub(r"^(\(?[A-Za-z0-9]{1,3}[\.\)])\s+", "", text)
    return text


def _is_descriptive(label: str) -> bool:
    """A schema label worth announcing (not a bare slug like '2_5'/'1')."""
    return bool(label) and not re.fullmatch(r"[\d_]+|mmddyyyy(_\d+)?", label)


def _row_words(page, rect, side: str) -> str:
    """Same-row printed text on one side of a widget, via WORD tokens (not raw
    spans). Court source PDFs often omit inter-word spaces in the content stream,
    so span-join yields 'thereisno disputeregarding'; word-mode keeps the tokens
    separate, so joining with spaces restores 'there is no dispute regarding'.
    side='left' for text fields (label precedes the box); side='right' for
    checkboxes/radios (the option text follows the box)."""
    cy = (rect.y0 + rect.y1) / 2
    tol = max(4.0, 0.6 * (rect.y1 - rect.y0))
    picks = []
    for x0, y0, x1, y1, word, *_ in page.get_text("words"):
        if abs((y0 + y1) / 2 - cy) > tol:
            continue
        if side == "left" and x1 <= rect.x0 + 6 and (rect.x0 - x1) < 300:
            picks.append((x0, word))
        elif side == "right" and x0 >= rect.x1 - 6 and (x0 - rect.x1) < 440:
            picks.append((x0, word))
    picks.sort()
    return " ".join(w for _, w in picks)


def _accessible_names(pdf_path, schema, mp) -> dict:
    """field_name -> accessible name, derived per widget from its caption."""
    id_by_label = {f.get("label"): f.get("field_id") for f in schema["fields"]}
    label_by_label = {f.get("label"): f.get("label") for f in schema["fields"]}
    checkish = {"CheckBox", "RadioButton"}
    out: dict[str, str] = {}
    doc = fitz.open(pdf_path)
    for pno in range(doc.page_count):
        for w in (doc[pno].widgets() or []):
            name = w.field_name
            if name in out:
                continue
            r = fitz.Rect(w.rect)
            # checkboxes/radios label-on-right; text fields label-on-left.
            side = "right" if w.field_type_string in checkish else "left"
            cap = _clean_caption(_row_words(doc[pno], r, side))
            if not (cap and len(cap) >= 2):  # fall back to the other side
                cap = _clean_caption(_row_words(
                    doc[pno], r, "left" if side == "right" else "right"))
            if cap and len(cap) >= 2:
                out[name] = cap[:120]
                continue
            key = mp.get(id_by_label.get(name))
            hk = _humanize_key(key)
            if hk:
                out[name] = hk
                continue
            lbl = label_by_label.get(name)
            if _is_descriptive(lbl):
                out[name] = lbl
    doc.close()
    return out


def schema_label_names(schema) -> dict:
    """field_id -> accessible name, straight from the schema label.

    The probate-forms naming strategy: there schema.json's `label` is already
    human-readable ("County Probate Court"), so /TU can be the label directly.
    """
    return {f["field_id"]: (f.get("label") or f["field_id"])
            for f in schema["fields"]}


def caption_names(pdf_path, schema, mp) -> dict:
    """field_name -> accessible name from the printed caption (court strategy)."""
    return _accessible_names(pdf_path, schema, mp)


def remediate(inp, outp, schema_path, lang, title, *, naming="caption"):
    """Remediate a filled form. ``naming`` picks the /TU naming strategy:

    - ``"caption"`` (default, donor behavior): derive each widget's accessible
      name from its printed caption, falling back to the humanized mapping key,
      then the schema label. For repos whose schema labels are raw widget slugs.
    - ``"schema-label"``: announce the schema label directly (probate strategy,
      for repos whose schema labels are human-readable).
    - a callable ``f(pdf_path, schema, mapping_map) -> {field_name: name}`` for
      anything else.
    """
    schema_path = pathlib.Path(schema_path)
    schema = json.loads(schema_path.read_text())
    mapping_path = schema_path.parent / "mapping.json"
    mp = {}
    if mapping_path.exists():
        mp = json.loads(mapping_path.read_text()).get("map") or {}
    form_title = (schema.get("_skill_metadata_override", {}).get("form_title")
                  or schema.get("form_id", ""))
    if callable(naming):
        names = naming(inp, schema, mp)
    elif naming == "schema-label":
        names = schema_label_names(schema)
    elif naming == "caption":
        names = _accessible_names(inp, schema, mp)
    else:
        raise ValueError(f"unknown naming strategy {naming!r} "
                         "(expected 'caption', 'schema-label', or a callable)")

    done = {"tu_set": 0, "tu_total": 0}
    with pikepdf.open(inp) as p:
        for pg in p.pages:
            for a in pg.get("/Annots", []):
                if a.get("/Subtype") == pikepdf.Name("/Widget") and "/T" in a:
                    done["tu_total"] += 1
                    base = str(a["/T"]).split("__")[0]
                    nm = names.get(base)
                    if nm:
                        a["/TU"] = pikepdf.String(nm)
                        done["tu_set"] += 1
            pg["/Tabs"] = pikepdf.Name("/S")
        final_title = title or form_title or pathlib.Path(inp).stem
        with p.open_metadata(set_pikepdf_as_editor=False) as xmp:
            xmp["dc:title"] = final_title
        p.docinfo["/Title"] = final_title
        vp = p.Root.get("/ViewerPreferences", pikepdf.Dictionary())
        vp["/DisplayDocTitle"] = True
        p.Root["/ViewerPreferences"] = vp
        p.Root["/Lang"] = pikepdf.String(lang)
        p.save(outp)
    return done, final_title


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("out")
    ap.add_argument("--schema", required=True)
    ap.add_argument("--lang", default="en-US")
    ap.add_argument("--title", default=None)
    ap.add_argument("--naming", default="caption",
                    choices=["caption", "schema-label"],
                    help="/TU naming strategy (caption = court donor behavior; "
                         "schema-label = probate behavior)")
    a = ap.parse_args()
    if pathlib.Path(a.out).resolve() == pathlib.Path(a.pdf).resolve():
        print("refusing to overwrite the original", file=sys.stderr)
        return 2
    done, title = remediate(a.pdf, a.out, a.schema, a.lang, a.title,
                            naming=a.naming)
    print(f"# Form remediation: {a.out}  (NEW file)")
    print(f"  - accessible field names (/TU from caption->key->label): "
          f"{done['tu_set']}/{done['tu_total']}")
    print(f"  - document title set ('{title}') + DisplayDocTitle on")
    print(f"  - /Lang = {a.lang}; tab order = /S (logical) on every page")
    print(f"## still needs OpenDataLoader (content tag tree 7.1/7.2) + "
          f"embed_widget_font (fonts) — see accessibility_pipeline.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
