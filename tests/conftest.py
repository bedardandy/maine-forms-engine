"""Shared fixtures: tiny synthetic AcroForm PDFs built in-test with PyMuPDF.

No official/copyrighted blanks are committed or fetched — every PDF a test
touches is synthesized here, mirroring the donor repos' offline test strategy
(transactional-tax-forms tests/test_engine_offline.py)."""
import hashlib
import json
import pathlib

import fitz
import pytest


def synthetic_form(path: pathlib.Path, *, captions: bool = False) -> None:
    """Two text fields, a checkbox, and a 2-widget shared-name group.

    Ported from the tax repo's offline suite; ``captions=True`` additionally
    prints a label left of each text box (for the caption-/TU strategy).
    """
    doc = fitz.open()
    page = doc.new_page()
    for i, name in enumerate(("name_field", "addr_field")):
        if captions:
            label = "Name:" if name == "name_field" else "Mailing address:"
            page.insert_text((20, 114 + 40 * i), label, fontsize=10)
        w = fitz.Widget()
        w.field_name = name
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.rect = fitz.Rect(72, 100 + 40 * i, 400, 120 + 40 * i)
        page.add_widget(w)
    cb = fitz.Widget()
    cb.field_name = "consent_box"
    cb.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
    cb.rect = fitz.Rect(72, 200, 86, 214)
    page.add_widget(cb)
    if captions:
        page.insert_text((92, 211), "I consent to electronic service", fontsize=10)
    # multi-widget group: same field name on two stacked lines
    for i in range(2):
        w = fitz.Widget()
        w.field_name = "narrative"
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.rect = fitz.Rect(72, 250 + 22 * i, 300, 266 + 22 * i)
        page.add_widget(w)
    doc.save(str(path))
    doc.close()


def add_radio_group(path: pathlib.Path, name: str, states: list[str],
                    y: float = 300.0) -> None:
    """Append a radio group to a saved PDF: one same-named button widget per
    on-state. PyMuPDF can't author radios directly, so checkboxes are added
    and rewritten via pikepdf (/AP /N re-keyed to the on-state + the
    AcroForm radio flag set) — mirroring how official blanks encode them."""
    import pikepdf

    doc = fitz.open(str(path))
    page = doc[0]
    rects = []
    for i in range(len(states)):
        w = fitz.Widget()
        w.field_name = name
        w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
        w.rect = fitz.Rect(72 + 40 * i, y, 86 + 40 * i, y + 14)
        page.add_widget(w)
        rects.append(tuple(w.rect))
    doc.save(str(path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()

    pdf = pikepdf.open(str(path), allow_overwriting_input=True)
    i = 0
    for pg in pdf.pages:
        for annot in pg.get("/Annots", []):
            if str(annot.get("/T", "")) != name:
                continue
            ap = annot.get("/AP")
            if ap is not None and "/N" in ap:
                n = ap["/N"]
                newn = pikepdf.Dictionary()
                for k in [str(k) for k in n.keys()]:
                    tgt = "/" + states[i] if k == "/Yes" else k
                    newn[pikepdf.Name(tgt)] = n[pikepdf.Name(k)]
                ap["/N"] = newn
            annot["/Ff"] = 32768  # radio-button field flag
            annot["/V"] = pikepdf.Name("/Off")
            annot["/AS"] = pikepdf.Name("/Off")
            i += 1
    pdf.save(str(path))
    pdf.close()


def add_checkbox_fanout(path: pathlib.Path, name: str, n: int = 2,
                        y: float = 340.0) -> None:
    """Append the court repos' legitimate fan-out class: same-named
    checkboxes that all share the default /Yes on-state."""
    doc = fitz.open(str(path))
    page = doc[0]
    for i in range(n):
        w = fitz.Widget()
        w.field_name = name
        w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
        w.rect = fitz.Rect(72 + 40 * i, y, 86 + 40 * i, y + 14)
        page.add_widget(w)
    doc.save(str(path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()


@pytest.fixture
def blank_pdf(tmp_path):
    p = tmp_path / "blank.pdf"
    synthetic_form(p)
    return p


SCHEMA = {
    "form_id": "TEST-1",
    "_skill_metadata_override": {"form_title": "Test Form One"},
    "fields": [
        {"field_id": "name_field", "label": "name_field",
         "rect": [72, 100, 400, 120]},
        {"field_id": "addr_field", "label": "addr_field",
         "rect": [72, 140, 400, 160]},
        {"field_id": "consent_box", "label": "consent_box",
         "rect": [72, 200, 86, 214]},
        {"field_id": "narrative", "label": "narrative",
         "rect": [72, 250, 300, 266]},
    ],
}

MAPPING = {
    "form_id": "TEST-1",
    "status": "verified",
    "map": {
        "name_field": "parties.plaintiff.full_name",
        "addr_field": "parties.plaintiff.address",
        "consent_box": "facts.consents_to_e_service",
        "narrative": "facts.statement",
    },
}

CASE = {
    "matter": {"court_county": "Cumberland", "docket_number": "FM-2025-001"},
    "parties": {"plaintiff": {"full_name": "Jane Q. Doe",
                              "address": "1 Main St, Portland, ME 04101"}},
    "facts": {"consents_to_e_service": "yes",
              "statement": "Short statement."},
}


@pytest.fixture
def form_tree(tmp_path):
    """A consumer-repo-shaped tree: forms/TEST-1/* + catalog/pdf_manifest.json
    whose sha256 pins the synthesized blank."""
    root = tmp_path / "repo"
    fdir = root / "forms" / "TEST-1"
    (fdir / "examples").mkdir(parents=True)
    blank = fdir / "TEST-1.pdf"
    synthetic_form(blank)
    (fdir / "schema.json").write_text(json.dumps(SCHEMA))
    (fdir / "mapping.json").write_text(json.dumps(MAPPING))
    (fdir / "examples" / "sample_case.json").write_text(json.dumps(CASE))
    data = blank.read_bytes()
    (root / "catalog").mkdir()
    (root / "catalog" / "pdf_manifest.json").write_text(json.dumps({
        "count": 1,
        "forms": {"TEST-1": {
            "url": "https://example.test/TEST-1",
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }},
    }))
    return root


RADIO_SCHEMA = {
    "form_id": "TEST-1",
    "fields": SCHEMA["fields"] + [
        {"field_id": "residency_status", "label": "residency_status",
         "type": "radio", "rect": [72, 300, 86, 314]},
        {"field_id": "residency_status_2", "label": "residency_status",
         "type": "radio", "rect": [112, 300, 126, 314]},
    ],
}

RADIO_MAPPING = {
    **MAPPING,
    "manual": {
        "residency_status": {
            "fill": "manual", "kind": "radio_group",
            "key": "facts.residency_status",
            "options": ["Resident", "Nonresident"],
            "note": "single-status radio group; never written by the engine",
        },
    },
}

RADIO_CASE = {**CASE,
              "facts": {**CASE["facts"], "residency_status": "nonresident"}}


@pytest.fixture
def radio_form_tree(tmp_path):
    """form_tree plus a 2-option radio group ('Resident'/'Nonresident')
    declared "fill": "manual" in mapping.json."""
    root = tmp_path / "radio_repo"
    fdir = root / "forms" / "TEST-1"
    (fdir / "examples").mkdir(parents=True)
    blank = fdir / "TEST-1.pdf"
    synthetic_form(blank)
    add_radio_group(blank, "residency_status", ["Resident", "Nonresident"])
    (fdir / "schema.json").write_text(json.dumps(RADIO_SCHEMA))
    (fdir / "mapping.json").write_text(json.dumps(RADIO_MAPPING))
    (fdir / "examples" / "sample_case.json").write_text(json.dumps(RADIO_CASE))
    data = blank.read_bytes()
    (root / "catalog").mkdir()
    (root / "catalog" / "pdf_manifest.json").write_text(json.dumps({
        "count": 1,
        "forms": {"TEST-1": {
            "url": "https://example.test/TEST-1",
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }},
    }))
    return root
