"""Accessibility remediation core + the pluggable /TU naming strategy
(caption = court donor behavior, schema-label = probate behavior, callable)."""
import json

import pikepdf
import pytest

from maine_forms_engine.accessibility.remediate_form import (
    remediate, schema_label_names)

from conftest import synthetic_form

SCHEMA = {
    "form_id": "TEST-A",
    "_skill_metadata_override": {"form_title": "Accessibility Test Form"},
    "fields": [
        {"field_id": "name_field", "label": "Full legal name"},
        {"field_id": "addr_field", "label": "Mailing address"},
        {"field_id": "consent_box", "label": "Consent to e-service"},
        {"field_id": "narrative", "label": "Statement"},
    ],
}


@pytest.fixture
def filled(tmp_path):
    pdf = tmp_path / "filled.pdf"
    synthetic_form(pdf, captions=True)
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(SCHEMA))
    return pdf, schema


def _tus(path):
    out = {}
    with pikepdf.open(str(path)) as p:
        for pg in p.pages:
            for a in pg.get("/Annots", []):
                if a.get("/Subtype") == pikepdf.Name("/Widget") and "/T" in a:
                    out.setdefault(str(a["/T"]), str(a["/TU"]) if "/TU" in a else None)
    return out


def test_schema_label_strategy(filled, tmp_path):
    pdf, schema = filled
    out = tmp_path / "out.pdf"
    done, title = remediate(str(pdf), str(out), str(schema), "en-US", None,
                            naming="schema-label")
    assert done["tu_set"] == done["tu_total"] > 0
    assert title == "Accessibility Test Form"
    tus = _tus(out)
    assert tus["name_field"] == "Full legal name"
    assert tus["consent_box"] == "Consent to e-service"
    with pikepdf.open(str(out)) as p:
        assert str(p.Root["/Lang"]) == "en-US"
        assert p.Root["/ViewerPreferences"]["/DisplayDocTitle"]
        assert all(str(pg["/Tabs"]) == "/S" for pg in p.pages)


def test_caption_strategy_reads_printed_labels(filled, tmp_path):
    pdf, schema = filled
    out = tmp_path / "out.pdf"
    done, _ = remediate(str(pdf), str(out), str(schema), "en-US", None,
                        naming="caption")
    assert done["tu_set"] > 0
    tus = _tus(out)
    # text field: caption printed left of the box
    assert tus["name_field"] == "Name"
    # checkbox: option text printed right of the box
    assert "consent" in tus["consent_box"].lower()


def test_callable_strategy(filled, tmp_path):
    pdf, schema = filled
    out = tmp_path / "out.pdf"
    remediate(str(pdf), str(out), str(schema), "en-US", "Custom Title",
              naming=lambda p, s, m: {"name_field": "Injected name"})
    tus = _tus(out)
    assert tus["name_field"] == "Injected name"
    assert tus["addr_field"] is None  # strategy named only one field


def test_unknown_strategy_rejected(filled, tmp_path):
    pdf, schema = filled
    with pytest.raises(ValueError):
        remediate(str(pdf), str(tmp_path / "o.pdf"), str(schema), "en-US",
                  None, naming="nope")


def test_schema_label_names_helper():
    names = schema_label_names(SCHEMA)
    assert names["narrative"] == "Statement"
