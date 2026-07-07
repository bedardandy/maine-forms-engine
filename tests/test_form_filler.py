"""form_filler against a synthetic AcroForm (ported from
transactional-tax-forms tests/test_engine_offline.py and adapted to the donor
court return shape: ``fill_form`` returns the output path string — see
CHANGES_FROM_DONOR.md "donor divergence" notes)."""
import json
import unittest
from pathlib import Path

import fitz
import pytest

from maine_forms_engine.fill.form_filler import (
    _wrap_across_widgets, fill_form, fill_form_from_json, list_form_fields)


class WrapAcrossWidgets(unittest.TestCase):
    def test_value_wraps_in_capacity_order(self):
        lines, rem = _wrap_across_widgets("one two three four", [8, 20])
        self.assertEqual(lines, ["one two", "three four"])
        self.assertEqual(rem, "")

    def test_overflow_past_last_widget_is_returned(self):
        lines, rem = _wrap_across_widgets("aa bb cc dd", [5, 5])
        # whatever didn't fit must come back as the remainder, not vanish
        self.assertEqual((" ".join(lines) + " " + rem).split(),
                         "aa bb cc dd".split())

    def test_word_wider_than_widget_skips_ahead(self):
        lines, rem = _wrap_across_widgets("longword ok", [4, 10])
        self.assertEqual(lines[0], "")
        self.assertEqual(lines[1], "longword")
        self.assertEqual(rem, "ok")

    @pytest.mark.xfail(reason="audit 2026-07-06 follow-up: a wide word that "
                       "jumps ahead leaves the skipped widget blank; a later "
                       "narrow word could fill it, but packing it there would "
                       "reorder text out of top-to-bottom reading order "
                       "(see PR discussion). Deferred as a design decision.",
                       strict=True)
    def test_skipped_widget_backfilled_by_later_word(self):
        # 'bbbbbbbb' (8) skips widgets 0 and 1 (cap 5) to land in widget 2;
        # 'cc' (2) fits the skipped widget 1. The gap currently stays blank:
        # actual == (['aaaa', '', 'bbbbbbbb cc'], '').
        lines, rem = _wrap_across_widgets("aaaa bbbbbbbb cc", [5, 5, 20])
        self.assertNotEqual(lines[1], "")
        self.assertEqual(rem, "")


@pytest.fixture
def out(tmp_path):
    return tmp_path / "filled.pdf"


def _values(path):
    doc = fitz.open(str(path))
    values = {w.field_name: w.field_value for p in doc for w in p.widgets()}
    doc.close()
    return values


def test_fill_roundtrip(blank_pdf, out):
    data = {
        "name_field": "Example LLC",
        "addr_field": "123 Main St, Portland, ME 04101",
        "consent_box": "yes",
        "ghost_field": "value with no widget",
    }
    res = fill_form(blank_pdf, data, out)
    # donor (court) contract: returns the output path string
    assert res == str(out)
    values = _values(out)
    assert values["name_field"] == "Example LLC"
    assert values["addr_field"] == "123 Main St, Portland, ME 04101"
    assert values["consent_box"] == "Yes"


def test_checkbox_ignores_non_affirmative_value(blank_pdf, out):
    fill_form(blank_pdf, {"consent_box": "Jane Q. Doe"}, out)
    assert _values(out)["consent_box"] == "Off"


def test_multi_widget_group_wraps_as_overlay(blank_pdf, out):
    long_text = ("alpha beta gamma delta epsilon zeta eta theta " * 2).strip()
    fill_form(blank_pdf, {"narrative": long_text}, out)
    # group widgets are deleted and replaced with stamped text
    doc = fitz.open(str(out))
    names = {w.field_name for p in doc for w in p.widgets()}
    page_text = doc[0].get_text()
    doc.close()
    assert "narrative" not in names
    assert "alpha beta" in page_text


def test_addendum_auto_raises_without_renderer(blank_pdf, out):
    # The addendum renderer is deliberately NOT shipped (stays per-repo);
    # overflow under addendum_policy="auto" must fail loudly, donor-style.
    overflow = "word " * 400
    with pytest.raises(ValueError):
        fill_form(blank_pdf, {"narrative": overflow.strip()}, out,
                  addendum_policy="auto")


def test_fill_form_from_json(blank_pdf, tmp_path, out):
    case = tmp_path / "data.json"
    case.write_text(json.dumps({"name_field": "From JSON"}))
    res = fill_form_from_json(blank_pdf, case, out)
    assert res == str(out)
    assert _values(out)["name_field"] == "From JSON"


def test_list_form_fields(blank_pdf):
    fields = list_form_fields(blank_pdf)
    by_name = {f["field_name"]: f for f in fields}
    assert by_name["consent_box"]["field_type"] == "checkbox"
    assert by_name["name_field"]["field_type"] == "text"
    # the shared-name group contributes two widget entries
    assert sum(1 for f in fields if f["field_name"] == "narrative") == 2


def test_return_report_dict(blank_pdf, out):
    """The tax-consumer contract: return_report=True yields the result dict
    (ported from transactional-tax-forms tests/test_engine_offline.py)."""
    data = {
        "name_field": "Example LLC",
        "addr_field": "123 Main St, Portland, ME 04101",
        "consent_box": "yes",
        "ghost_field": "value with no widget",
    }
    res = fill_form(blank_pdf, data, out, return_report=True)
    assert res["missing_fields"] == ["ghost_field"]
    assert res["filled_count"] == 3
    assert res["output_path"] == str(out)


def test_supported_policies_gate_raises_up_front(blank_pdf, tmp_path):
    """The tax-consumer policy: only 'none' is supported — any other
    addendum policy is refused before the PDF is opened, even with no
    overflow at all."""
    with pytest.raises(ValueError):
        fill_form(blank_pdf, {}, tmp_path / "x.pdf",
                  addendum_policy="auto",
                  supported_policies=frozenset({"none"}))
    with pytest.raises(ValueError):
        # a tree-level override is gated too
        fill_form(blank_pdf, {}, tmp_path / "x.pdf",
                  tree={"addendum_policy": "court_form"},
                  supported_policies=frozenset({"none"}))


def test_fill_form_from_json_report(blank_pdf, tmp_path, out):
    case = tmp_path / "data.json"
    case.write_text(json.dumps({"name_field": "From JSON"}))
    res = fill_form_from_json(blank_pdf, case, out, return_report=True)
    assert res["filled_count"] == 1
