"""Radio-group soft lock: detection, never-write skip, and the yellow-light
report entry (engine safety net + mapping "fill": "manual" suggestions)."""
import json

import fitz

from maine_forms_engine.fill.form_filler import (
    detect_radio_groups, fill_form, match_radio_option)
from maine_forms_engine.fill.fill_via_mapping import (
    fill_via_mapping, resolve_mapping)

from conftest import (
    RADIO_CASE, add_checkbox_fanout, add_radio_group, synthetic_form)


def _radio_values(pdf_path, name):
    doc = fitz.open(str(pdf_path))
    vals = [(w.field_value, w.field_type) for page in doc
            for w in page.widgets() or [] if w.field_name == name]
    doc.close()
    return vals


# --------------------------------------------------------------- detection
def test_detection_distinguishes_lookalike_classes(tmp_path):
    p = tmp_path / "mixed.pdf"
    synthetic_form(p)  # has 2-widget text continuation + single checkbox
    add_radio_group(p, "residency", ["Resident", "Nonresident"], y=300)
    add_checkbox_fanout(p, "fanout_box", n=2, y=340)
    doc = fitz.open(str(p))
    groups = detect_radio_groups(doc)
    doc.close()
    # the radio group is found with its options ...
    assert groups == {"residency": ["Nonresident", "Resident"]}
    # ... while the same-named text continuation ("narrative"), the single
    # checkbox, and the same-on-state checkbox fan-out are NOT radio groups
    assert "narrative" not in groups
    assert "consent_box" not in groups
    assert "fanout_box" not in groups


def test_match_radio_option():
    opts = ["Resident", "Nonresident"]
    assert match_radio_option("nonresident", opts) == "Nonresident"
    assert match_radio_option("Non-Resident", opts) == "Nonresident"
    assert match_radio_option("Augusta", opts) is None
    assert match_radio_option(None, opts) is None
    assert match_radio_option("yes", ["Yes", "No"]) == "Yes"
    assert match_radio_option(True, ["Yes", "No"]) == "Yes"
    assert match_radio_option("false", ["Yes", "No"]) == "No"


# ------------------------------------------------------- engine safety net
def test_fill_form_never_writes_radio_groups(tmp_path):
    p = tmp_path / "radio.pdf"
    synthetic_form(p)
    add_radio_group(p, "residency", ["Resident", "Nonresident"], y=300)
    out = tmp_path / "filled.pdf"
    # a mapping bug routes a value at the radio group: the engine must skip
    # it (NOT wrap-as-text / NOT delete the widgets) and report it
    rep = fill_form(p, {"name_field": "Jane Q. Doe",
                        "residency": "Nonresident"}, out, return_report=True)
    assert rep["radio_groups_skipped"] == [{
        "field_id": "residency", "kind": "radio_group",
        "options": ["Nonresident", "Resident"],
        "suggested": "Nonresident",
        "action": "manual selection required"}]
    # not a stale-mapping signal — the group exists, it's just manual
    assert "residency" not in rep["missing_fields"]
    # both radio widgets survive, still off, still radio-typed
    vals = _radio_values(out, "residency")
    assert len(vals) == 2
    assert all(t == fitz.PDF_WIDGET_TYPE_RADIOBUTTON for _, t in vals)
    assert all(v in (None, "", "Off") for v, _ in vals)


def test_untargeted_radio_group_changes_nothing(tmp_path):
    p = tmp_path / "radio.pdf"
    synthetic_form(p)
    add_radio_group(p, "residency", ["Resident", "Nonresident"], y=300)
    rep = fill_form(p, {"name_field": "Jane Q. Doe"}, tmp_path / "f.pdf",
                    return_report=True)
    assert rep["radio_groups_skipped"] == []
    assert rep["filled_count"] == 1


# ------------------------------------------- mapping-driven manual entries
def test_manual_mapping_entry_suggests_without_writing(radio_form_tree,
                                                       tmp_path):
    res = resolve_mapping("TEST-1", RADIO_CASE,
                          forms_root=radio_form_tree / "forms")
    assert res["manual_fields"] == [{
        "field_id": "residency_status", "kind": "radio_group",
        "options": ["Resident", "Nonresident"],
        "key": "facts.residency_status",
        "suggested": "Nonresident",
        "action": "manual selection required",
        "note": "single-status radio group; never written by the engine"}]
    # manual entries never enter the writable fid->value map
    assert "residency_status" not in res["fid_value"]

    fill = fill_via_mapping("TEST-1", RADIO_CASE, tmp_path / "out",
                            forms_root=radio_form_tree / "forms")
    assert fill["ok"]
    assert [e["field_id"] for e in fill["radio_groups"]] == ["residency_status"]
    assert fill["radio_groups"][0]["suggested"] == "Nonresident"
    vals = _radio_values(fill["out_pdf"], "residency_status")
    assert all(v in (None, "", "Off") for v, _ in vals)


def test_manual_suggestion_none_when_fact_absent(radio_form_tree, tmp_path):
    case = json.loads(json.dumps(RADIO_CASE))
    del case["facts"]["residency_status"]
    fill = fill_via_mapping("TEST-1", case, tmp_path / "out",
                            forms_root=radio_form_tree / "forms")
    assert fill["ok"]
    assert fill["radio_groups"][0]["suggested"] is None


def test_map_routed_radio_is_skipped_and_reported(radio_form_tree, tmp_path):
    # even when a mapping (wrongly) routes the radio through "map", the
    # engine-layer safety net skips it and the fill result reports it
    mp = radio_form_tree / "forms" / "TEST-1" / "mapping.json"
    m = json.loads(mp.read_text())
    del m["manual"]
    m["map"]["residency_status"] = "facts.residency_status"
    mp.write_text(json.dumps(m))
    fill = fill_via_mapping("TEST-1", RADIO_CASE, tmp_path / "out",
                            forms_root=radio_form_tree / "forms",
                            result_style="tax")
    assert fill["ok"]
    entries = fill["radio_groups"]
    assert [e["field_id"] for e in entries] == ["residency_status"]
    assert entries[0]["suggested"] == "Nonresident"
    assert entries[0]["action"] == "manual selection required"
    assert fill["missing_widgets"] == []
    vals = _radio_values(fill["out_pdf"], "residency_status")
    assert all(v in (None, "", "Off") for v, _ in vals)
