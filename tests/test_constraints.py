"""Checkbox-paradox constraints: declarative, warnings-only, optional."""
import json

from maine_forms_engine.constraints import (
    evaluate, evaluate_for_form, load_constraints)
from maine_forms_engine.fill.fill_via_mapping import fill_via_mapping

from conftest import CASE

CONS = {
    "form_id": "TEST-1",
    "mutually_exclusive": [
        {"keys": ["facts.resident", "facts.nonresident"],
         "note": "Resident / Nonresident — single-status pair"},
        ["facts.simple_trust", "facts.complex_trust", "facts.esbt"],
        {"keys": ["facts.weekly", "facts.monthly"], "inferred": True},
    ],
    "requires": {
        "facts.designee_pin": {"keys": ["facts.designee_name"],
                                "note": "a PIN belongs to a named designee"},
        "facts.bail_amount_set": ["facts.bail_amount"],
    },
}


def test_mutually_exclusive_fires_on_two_set_keys():
    w = evaluate(CONS, {"facts": {"resident": "x", "nonresident": "yes"}})
    assert w == [{"code": "MUTUALLY_EXCLUSIVE",
                  "keys": ["facts.resident", "facts.nonresident"],
                  "severity": "warning",
                  "note": "Resident / Nonresident — single-status pair"}]


def test_single_selection_is_clean():
    assert evaluate(CONS, {"facts": {"resident": "x"}}) == []
    # negative tokens are not selections (the engine leaves the box off)
    assert evaluate(CONS, {"facts": {"resident": "x",
                                     "nonresident": "no"}}) == []
    assert evaluate(CONS, {"facts": {"resident": True,
                                     "nonresident": False}}) == []


def test_plain_list_group_and_inferred_flag():
    w = evaluate(CONS, {"facts": {"simple_trust": "x", "esbt": "x",
                                  "weekly": "1", "monthly": "1"}})
    assert {tuple(x["keys"]) for x in w} == {
        ("facts.simple_trust", "facts.esbt"),
        ("facts.weekly", "facts.monthly")}
    inferred = next(x for x in w if "facts.weekly" in x["keys"])
    assert inferred["inferred"] is True
    assert all(x["severity"] == "warning" for x in w)


def test_requires():
    w = evaluate(CONS, {"facts": {"designee_pin": "12345"}})
    assert w == [{"code": "REQUIRES",
                  "keys": ["facts.designee_pin", "facts.designee_name"],
                  "severity": "warning",
                  "note": "a PIN belongs to a named designee"}]
    assert evaluate(CONS, {"facts": {"designee_pin": "12345",
                                     "designee_name": "Pat Q."}}) == []


def test_flat_resolved_values_dialect():
    # court recipe kv: flat {field_id: value}
    cons = {"mutually_exclusive": [["superior_court", "district_court"]]}
    assert evaluate(cons, {"superior_court": "X", "district_court": "X"}) \
        == [{"code": "MUTUALLY_EXCLUSIVE",
             "keys": ["superior_court", "district_court"],
             "severity": "warning"}]
    assert evaluate(cons, {"superior_court": "X"}) == []


def test_empty_and_missing_constraints_are_inert(tmp_path):
    assert evaluate(None, {"facts": {"resident": "x"}}) == []
    assert evaluate({}, {"facts": {"resident": "x"}}) == []
    assert load_constraints(tmp_path) is None
    assert evaluate_for_form("NOPE-1", {"facts": {}}, tmp_path) == []


# ----------------------------------------------------- fill-report wiring
def test_fill_report_carries_constraint_warnings(form_tree, tmp_path):
    fdir = form_tree / "forms" / "TEST-1"
    (fdir / "constraints.json").write_text(json.dumps({
        "form_id": "TEST-1",
        "mutually_exclusive": [
            {"keys": ["facts.consents_to_e_service", "facts.statement"],
             "note": "synthetic paradox for the test"}],
    }))
    res = fill_via_mapping("TEST-1", CASE, tmp_path / "out",
                           forms_root=form_tree / "forms")
    # warnings only: the fill itself is untouched
    assert res["ok"] is True
    assert res["resolved"] == 4
    assert res["constraint_warnings"] == [{
        "code": "MUTUALLY_EXCLUSIVE",
        "keys": ["facts.consents_to_e_service", "facts.statement"],
        "severity": "warning",
        "note": "synthetic paradox for the test"}]


def test_no_constraints_file_means_no_report_key(form_tree, tmp_path):
    res = fill_via_mapping("TEST-1", CASE, tmp_path / "out",
                           forms_root=form_tree / "forms")
    assert res["ok"] is True
    assert "constraint_warnings" not in res
    assert "radio_groups" not in res  # no radio groups on this form either
