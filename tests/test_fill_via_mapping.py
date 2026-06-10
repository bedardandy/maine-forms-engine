"""End-to-end mapping fill over a synthetic consumer-repo tree (modeled on
maine-court-forms tests/test_fill_smoke.py, with the unshipped official
blanks replaced by an in-test fixture form)."""
import json
import warnings

import pytest

from maine_forms_engine.fill import verify
from maine_forms_engine.fill.fill_via_mapping import (
    fill_via_mapping, resolve_mapping)
from maine_forms_engine.fill.verify_fill import verify_fill

from conftest import CASE


def test_resolve_mapping_coverage(form_tree):
    res = resolve_mapping("TEST-1", CASE, forms_root=form_tree / "forms")
    assert res["mapped_keys"] == 4
    assert res["resolved"] == 4
    assert res["fid_value"]["name_field"] == "Jane Q. Doe"
    assert res["unresolved"] == []


def test_fill_via_mapping_roundtrip_and_post_fill_verify(form_tree, tmp_path):
    out = tmp_path / "out"
    res = fill_via_mapping("TEST-1", CASE, out, forms_root=form_tree / "forms")
    assert res["ok"], res
    assert res["resolved"] == 4
    # donor quirk preserved: a multi-widget group adds a __wrap_cache_ entry
    # to field_data, which fields_written counts (4 fields + 1 cache key)
    assert res["fields_written"] == 5
    # the manifest pins the fixture blank, so the fill-time guard verifies
    assert res["blank_verify"]["ok"] is True
    # deterministic post-fill verify: every intended value landed
    schema = json.loads((form_tree / "forms" / "TEST-1" / "schema.json").read_text())
    intended = resolve_mapping("TEST-1", CASE,
                               forms_root=form_tree / "forms")["fid_value"]
    v = verify_fill(res["out_pdf"], intended, schema)
    assert v["ok"], v
    assert v["summary"]["missing"] == 0


def test_blank_revision_guard_warns_on_swapped_blank(form_tree, tmp_path):
    blank = form_tree / "forms" / "TEST-1" / "TEST-1.pdf"
    blank.write_bytes(blank.read_bytes() + b"\n% tampered")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", verify.BlankRevisionWarning)
        res = fill_via_mapping("TEST-1", CASE, tmp_path / "out",
                               forms_root=form_tree / "forms")
    assert res["ok"]  # warn mode: fill proceeds...
    assert res["blank_verify"]["ok"] is False  # ...but the mismatch is surfaced
    assert "mapping was built against" in (res["blank_verify"]["detail"] or "")


def test_recipe_pointer_mapping_is_skipped(form_tree, tmp_path):
    mp = form_tree / "forms" / "TEST-1" / "mapping.json"
    mp.write_text(json.dumps({"form_id": "TEST-1", "status": "recipe", "map": {}}))
    res = fill_via_mapping("TEST-1", CASE, tmp_path / "out",
                           forms_root=form_tree / "forms")
    assert res["ok"] is False
    assert "pointer" in res["error"]


def test_zero_resolved_fill_fails_loudly(form_tree, tmp_path):
    engine_shape = {"court": {"name": "District"}, "docket_no": "X"}
    res = fill_via_mapping("TEST-1", engine_shape, tmp_path / "out",
                           forms_root=form_tree / "forms")
    assert res["ok"] is False
    assert "0 of" in res["error"]


def test_missing_blank_is_an_error(form_tree, tmp_path):
    (form_tree / "forms" / "TEST-1" / "TEST-1.pdf").unlink()
    res = fill_via_mapping("TEST-1", CASE, tmp_path / "out",
                           forms_root=form_tree / "forms")
    assert res["ok"] is False
    assert "blank PDF not found" in res["error"]
