"""Computed fields: declarative printed arithmetic, warnings-only, optional."""
import hashlib
import json
from decimal import Decimal

import fitz
import pytest

from maine_forms_engine.computations import (
    compute_facts, evaluate, evaluate_for_form, load_computations,
    parse_amount)
from maine_forms_engine.fill.fill_via_mapping import fill_via_mapping

from conftest import CASE, synthetic_form


# ------------------------------------------------------------- parsing
def test_parse_amount():
    assert parse_amount("1300") == 1300
    assert parse_amount("1,300.00") == 1300
    assert parse_amount("$1,234.56") == Decimal("1234.56")
    assert parse_amount("(1,300.00)") == -1300
    assert parse_amount("-5") == -5
    assert parse_amount(12) == 12
    assert parse_amount(12.5) == Decimal("12.5")
    assert parse_amount("  $ 99 ") == 99
    for bad in ("", "abc", "12 Main St", None, True, ["1"], "1-2", "$"):
        assert parse_amount(bad) is None, bad


# ------------------------------------------------------------- ops
def _one(op, inputs, values, **spec):
    comp = {"computed": {"facts.target": {
        "op": op, "inputs": inputs, "formula_text": "Printed text.", **spec}}}
    return evaluate(comp, values)


def test_sum_difference_product():
    r = _one("sum", ["facts.a", "facts.b"], {"facts": {"a": "1000", "b": "2000"}})
    assert r["computed"] == [{"key": "facts.target", "kind": "computed",
                              "value": "3000", "formula_text": "Printed text."}]
    r = _one("difference", ["facts.a", "facts.b", "facts.c"],
             {"facts": {"a": "10", "b": "3", "c": "2"}})
    assert r["computed"][0]["value"] == "5"
    r = _one("product", ["facts.a", "facts.b"], {"facts": {"a": "6", "b": "7"}})
    assert r["computed"][0]["value"] == "42"


def test_min_and_literal_constant_inputs():
    # "Enter the least of the amounts of lines (4), (6) or (7):"
    r = _one("min", ["facts.a", "facts.b", "facts.c"],
             {"facts": {"a": "120", "b": "75.50", "c": "300"}})
    assert r["computed"][0]["value"] == "75.50"
    # "Multiply amount on line (3) by .25 and enter answer here:"
    r = _one("product", ["facts.disposable", 0.25],
             {"facts": {"disposable": "1000"}})
    assert r["computed"][0]["value"] == "250"


def test_signed_sum_inputs():
    # "line 1 minus line 2 plus line 3"
    r = _one("sum", ["facts.l1", "-facts.l2", "facts.l3"],
             {"facts": {"l1": "100", "l2": "30", "l3": "5"}})
    assert r["computed"][0]["value"] == "75"


def test_floor_and_round():
    r = _one("difference", ["facts.a", "facts.b"],
             {"facts": {"a": "5", "b": "9"}}, floor=0)
    assert r["computed"][0]["value"] == "0"  # "If zero or less, enter -0-"
    r = _one("product", ["facts.a", "facts.b"],
             {"facts": {"a": "10.00", "b": "0.333"}}, **{"round": 2})
    assert r["computed"][0]["value"] == "3.33"


def test_output_format_mimics_inputs():
    # bare integers stay bare; commas/cents/$ only when the inputs carry them
    r = _one("sum", ["facts.a", "facts.b"],
             {"facts": {"a": "1,200.00", "b": "100"}})
    assert r["computed"][0]["value"] == "1,300.00"
    r = _one("sum", ["facts.a", "facts.b"],
             {"facts": {"a": "$10", "b": "$2.50"}})
    assert r["computed"][0]["value"] == "$12.50"
    # negatives render with a leading minus, never invented parentheses
    r = _one("difference", ["facts.a", "facts.b"],
             {"facts": {"a": "100", "b": "350"}})
    assert r["computed"][0]["value"] == "-250"


# ------------------------------------------- supplied wins + mismatch warning
def test_supplied_value_wins_with_mismatch_warning():
    comp = {"computed": {"facts.total": {
        "op": "sum", "inputs": ["facts.a", "facts.b"],
        "formula_text": "Add lines 1 and 2."}}}
    r = evaluate(comp, {"facts": {"a": "100", "b": "200", "total": "999"}})
    assert r["computed"] == []  # never overrides
    assert r["warnings"] == [{"code": "COMPUTATION_MISMATCH",
                              "key": "facts.total", "supplied": "999",
                              "computed": "300",
                              "formula_text": "Add lines 1 and 2.",
                              "severity": "warning"}]


def test_formatting_tolerance_is_not_a_mismatch():
    comp = {"computed": {"facts.total": {
        "op": "sum", "inputs": ["facts.a", "facts.b"],
        "formula_text": "Add lines 1 and 2."}}}
    for ok in ("1300", "1,300.00", "$1,300", 1300, 1300.0):
        r = evaluate(comp, {"facts": {"a": "1,000", "b": "300", "total": ok}})
        assert r["warnings"] == [], ok


# ------------------------------------------------------ skip semantics
def test_missing_input_skips_silently():
    r = _one("sum", ["facts.a", "facts.b"], {"facts": {"a": "100"}})
    assert r == {"computed": [], "warnings": [], "notes": []}


def test_unparseable_input_skips_with_note():
    r = _one("sum", ["facts.a", "facts.b"],
             {"facts": {"a": "100", "b": "see attached"}})
    assert r["computed"] == [] and r["warnings"] == []
    assert r["notes"] == [{"key": "facts.target",
                           "note": "input facts.b = 'see attached' is not a "
                                   "number; computation skipped"}]


def test_unparseable_supplied_target_notes_not_checked():
    comp = {"computed": {"facts.total": {
        "op": "sum", "inputs": ["facts.a", "facts.b"],
        "formula_text": "Add lines 1 and 2."}}}
    r = evaluate(comp, {"facts": {"a": "1", "b": "2", "total": "n/a"}})
    assert r["warnings"] == []
    assert "not checked" in r["notes"][0]["note"]


# -------------------------------------------------- topological chaining
CHAIN = {"computed": {
    "facts.subtotal": {"op": "sum", "inputs": ["facts.a", "facts.b"],
                       "formula_text": "Add lines 1 and 2."},
    "facts.grand_total": {"op": "sum",
                          "inputs": ["facts.subtotal", "facts.c"],
                          "formula_text": "Add lines 3 and 4."},
}}


def test_computed_values_feed_later_computations():
    r = evaluate(CHAIN, {"facts": {"a": "1", "b": "2", "c": "10"}})
    assert {e["key"]: e["value"] for e in r["computed"]} == {
        "facts.subtotal": "3", "facts.grand_total": "13"}


def test_supplied_intermediate_feeds_downstream_as_is():
    # the engine never overrides: the (wrong) supplied subtotal is what
    # flows into the grand total, plus a mismatch warning on the subtotal
    r = evaluate(CHAIN, {"facts": {"a": "1", "b": "2", "c": "10",
                                   "subtotal": "5"}})
    assert [w["key"] for w in r["warnings"]] == ["facts.subtotal"]
    assert r["computed"] == [{"key": "facts.grand_total", "kind": "computed",
                              "value": "15",
                              "formula_text": "Add lines 3 and 4."}]


def test_cycle_is_a_load_error(tmp_path):
    (tmp_path / "computations.json").write_text(json.dumps({"computed": {
        "facts.a": {"op": "sum", "inputs": ["facts.b"], "formula_text": "x"},
        "facts.b": {"op": "sum", "inputs": ["facts.a"], "formula_text": "x"},
    }}))
    with pytest.raises(ValueError, match="cycle"):
        load_computations(tmp_path)


def test_unknown_op_and_malformed_specs_are_load_errors(tmp_path):
    for spec in ({"op": "quotient", "inputs": ["facts.a"], "formula_text": "x"},
                 {"op": "sum", "inputs": [], "formula_text": "x"},
                 {"op": "sum", "inputs": ["facts.a"]},
                 {"op": "difference", "inputs": ["facts.a", "-facts.b"],
                  "formula_text": "x"},
                 {"op": "product", "inputs": [0.25, 2],
                  "formula_text": "x"},
                 {"op": "sum", "inputs": ["facts.a", True],
                  "formula_text": "x"}):
        (tmp_path / "computations.json").write_text(
            json.dumps({"computed": {"facts.t": spec}}))
        with pytest.raises(ValueError):
            load_computations(tmp_path)


def test_empty_and_missing_computations_are_inert(tmp_path):
    empty = {"computed": [], "warnings": [], "notes": []}
    assert evaluate(None, {"facts": {"a": "1"}}) == empty
    assert evaluate({}, {"facts": {"a": "1"}}) == empty
    assert load_computations(tmp_path) is None
    assert evaluate_for_form("NOPE-1", {"facts": {}}, tmp_path) == empty


# ------------------------------- compute_facts (recipe-tier, facts-only)
# The mapping-independent public entry point: facts dict + spec in,
# (computed_values_to_add, warnings) out — no mapping.json, no form dir,
# no widgets anywhere in the call. Targets are canonical fact keys a
# recipe reads off the case (e.g. court MJ-009/MJ-015 "for a total of $").
TOTAL = {"computed": {"facts.judgment_total": {
    "op": "sum",
    "inputs": ["facts.judgment_amount", "facts.costs", "facts.interest"],
    "formula_text": "for a total of $"}}}


def test_compute_facts_facts_only_omitted_target_is_computed():
    facts = {"facts": {"judgment_amount": "1,200.00", "costs": "85.00",
                       "interest": "15.00"}}
    values, warnings = compute_facts(TOTAL, facts)
    assert values == {"facts.judgment_total": "1,300.00"}
    assert warnings == []
    # the case is never mutated — the caller decides where to merge
    assert "judgment_total" not in facts["facts"]
    assert "facts.judgment_total" not in facts


def test_compute_facts_supplied_wins_with_mismatch_warning():
    values, warnings = compute_facts(
        TOTAL, {"facts": {"judgment_amount": "1,200.00", "costs": "85.00",
                          "interest": "15.00", "judgment_total": "999"}})
    assert values == {}  # supplied value never returned, never overridden
    assert warnings == [{"code": "COMPUTATION_MISMATCH",
                         "key": "facts.judgment_total", "supplied": "999",
                         "computed": "1,300.00",
                         "formula_text": "for a total of $",
                         "severity": "warning"}]
    # a consistent supplied value (any formatting) is clean
    for ok in ("1300", "1,300.00", "$1,300", 1300):
        values, warnings = compute_facts(
            TOTAL, {"facts": {"judgment_amount": "1,200.00",
                              "costs": "85.00", "interest": "15.00",
                              "judgment_total": ok}})
        assert (values, warnings) == ({}, []), ok


def test_compute_facts_topological_chain_and_flat_merge():
    values, warnings = compute_facts(
        CHAIN, {"facts": {"a": "1", "b": "2", "c": "10"}})
    assert values == {"facts.subtotal": "3", "facts.grand_total": "13"}
    assert warnings == []
    # supplied intermediate feeds downstream AS-IS + warns, exactly like
    # the mapped path
    values, warnings = compute_facts(
        CHAIN, {"facts": {"a": "1", "b": "2", "c": "10", "subtotal": "5"}})
    assert values == {"facts.grand_total": "15"}
    assert [w["code"] for w in warnings] == ["COMPUTATION_MISMATCH"]
    assert warnings[0]["key"] == "facts.subtotal"
    # the documented merge: a flat case.update(values) round-trips —
    # flat dotted keys resolve before nested paths on the next call
    case = {"facts": {"a": "1", "b": "2", "c": "10"}}
    case.update(compute_facts(CHAIN, case)[0])
    assert compute_facts(CHAIN, case) == ({}, [])


def test_compute_facts_money_formats():
    # $-and-comma inputs keep their style; parentheses-negatives parse
    values, _ = compute_facts(
        {"computed": {"facts.t": {
            "op": "sum", "inputs": ["facts.a", "facts.b"],
            "formula_text": "Total"}}},
        {"facts": {"a": "$1,234.56", "b": "($234.56)"}})
    assert values == {"facts.t": "$1,000.00"}


def test_compute_facts_skips_and_inert_spec():
    # missing input -> target skipped silently; unparseable -> never guessed
    assert compute_facts(TOTAL, {"facts": {"judgment_amount": "1200"}}) \
        == ({}, [])
    assert compute_facts(
        TOTAL, {"facts": {"judgment_amount": "1200", "costs": "see attached",
                          "interest": "15"}}) == ({}, [])
    assert compute_facts(None, {"facts": {"a": "1"}}) == ({}, [])
    assert compute_facts({}, {"facts": {"a": "1"}}) == ({}, [])


# ----------------------------------------------------- fill-path wiring
def money_form(path):
    """Three single text widgets: line_1, line_2, total_box."""
    doc = fitz.open()
    page = doc.new_page()
    for i, name in enumerate(("line_1", "line_2", "total_box")):
        w = fitz.Widget()
        w.field_name = name
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.rect = fitz.Rect(72, 100 + 40 * i, 400, 120 + 40 * i)
        page.add_widget(w)
    doc.save(str(path))
    doc.close()


MONEY_SCHEMA = {
    "form_id": "MONEY-1",
    "fields": [
        {"field_id": "line_1", "label": "line_1",
         "rect": [72, 100, 400, 120]},
        {"field_id": "line_2", "label": "line_2",
         "rect": [72, 140, 400, 160]},
        {"field_id": "total_box", "label": "total_box",
         "rect": [72, 180, 400, 200]},
    ],
}

MONEY_MAPPING = {
    "form_id": "MONEY-1",
    "status": "verified",
    "map": {
        "line_1": "facts.line_1",
        "line_2": "facts.line_2",
        "total_box": "facts.total_due",
    },
}

MONEY_COMPUTATIONS = {
    "form_id": "MONEY-1",
    "computed": {
        "facts.total_due": {
            "op": "sum",
            "inputs": ["facts.line_1", "facts.line_2"],
            "formula_text": "3. Total due. (Add lines 1 and 2.)",
        },
    },
}


@pytest.fixture
def money_tree(tmp_path):
    """A consumer-shaped tree whose 'narrative' field is a computed total."""
    root = tmp_path / "money_repo"
    fdir = root / "forms" / "MONEY-1"
    fdir.mkdir(parents=True)
    blank = fdir / "MONEY-1.pdf"
    money_form(blank)
    (fdir / "schema.json").write_text(json.dumps(MONEY_SCHEMA))
    (fdir / "mapping.json").write_text(json.dumps(MONEY_MAPPING))
    (fdir / "computations.json").write_text(json.dumps(MONEY_COMPUTATIONS))
    data = blank.read_bytes()
    (root / "catalog").mkdir()
    (root / "catalog" / "pdf_manifest.json").write_text(json.dumps({
        "count": 1,
        "forms": {"MONEY-1": {"url": "https://example.test/MONEY-1",
                              "sha256": hashlib.sha256(data).hexdigest(),
                              "bytes": len(data)}},
    }))
    return root


def _widget_values(pdf_path):
    doc = fitz.open(str(pdf_path))
    vals = {}
    for page in doc:
        for w in page.widgets() or []:
            vals[w.field_name] = w.field_value
    doc.close()
    return vals


def test_omitted_total_is_computed_and_filled(money_tree, tmp_path):
    case = {"facts": {"line_1": "1000", "line_2": "300"}}
    res = fill_via_mapping("MONEY-1", case, tmp_path / "out",
                           forms_root=money_tree / "forms")
    assert res["ok"] is True
    assert res["computed_fields"] == [{
        "key": "facts.total_due", "kind": "computed", "value": "1300",
        "formula_text": "3. Total due. (Add lines 1 and 2.)"}]
    assert res["computation_warnings"] == []
    # the computed value actually landed in the PDF
    assert _widget_values(res["out_pdf"])["total_box"] == "1300"
    # and the computed key no longer counts as unresolved
    assert res["unresolved"] == []


def test_supplied_contradiction_is_written_as_is_with_warning(money_tree,
                                                              tmp_path):
    case = {"facts": {"line_1": "1000", "line_2": "300", "total_due": "9999"}}
    res = fill_via_mapping("MONEY-1", case, tmp_path / "out",
                           forms_root=money_tree / "forms")
    assert res["ok"] is True
    assert res["computed_fields"] == []
    assert res["computation_warnings"] == [{
        "code": "COMPUTATION_MISMATCH", "key": "facts.total_due",
        "supplied": "9999", "computed": "1300",
        "formula_text": "3. Total due. (Add lines 1 and 2.)",
        "severity": "warning"}]
    # the supplied value is what's in the PDF — never enforced/overridden
    assert _widget_values(res["out_pdf"])["total_box"] == "9999"


def test_supplied_consistent_total_is_clean(money_tree, tmp_path):
    case = {"facts": {"line_1": "1000", "line_2": "300",
                      "total_due": "1,300.00"}}  # formatting-only difference
    res = fill_via_mapping("MONEY-1", case, tmp_path / "out",
                           forms_root=money_tree / "forms")
    assert res["computation_warnings"] == []
    assert _widget_values(res["out_pdf"])["total_box"] == "1,300.00"


def test_no_computations_file_means_no_report_keys(form_tree, tmp_path):
    res = fill_via_mapping("TEST-1", CASE, tmp_path / "out",
                           forms_root=form_tree / "forms")
    assert res["ok"] is True
    for k in ("computed_fields", "computation_warnings",
              "computation_notes"):
        assert k not in res
