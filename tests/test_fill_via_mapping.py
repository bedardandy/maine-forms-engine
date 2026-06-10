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


# --- tax-consumer policy configuration (see CHANGES_FROM_DONOR.md) ----------

TAX_FILLABLE = frozenset({"verified", "opus-adjudicated", "mapped",
                          "vision-mapped"})


def test_allowlist_status_gate_refuses_remap_pending(form_tree, tmp_path):
    mp = form_tree / "forms" / "TEST-1" / "mapping.json"
    m = json.loads(mp.read_text())
    m["status"] = "remap-pending"
    mp.write_text(json.dumps(m))
    res = fill_via_mapping("TEST-1", CASE, tmp_path / "out",
                           forms_root=form_tree / "forms",
                           fillable_statuses=TAX_FILLABLE,
                           skip_reasons={"remap-pending": "the upstream blank "
                                         "drifted; re-map before filling"},
                           result_style="tax")
    assert res["ok"] is False and res["skipped"] is True
    assert res["status"] == "remap-pending"
    assert "drifted" in res["error"]
    # without the allowlist (court default) the status is not blocked
    res2 = fill_via_mapping("TEST-1", CASE, tmp_path / "out2",
                            forms_root=form_tree / "forms")
    assert res2["ok"] is True


def test_built_against_sha_drifted_revision_is_refused(form_tree):
    """Ported from transactional-tax-forms tests/test_engine_offline.py
    (BuiltAgainstSha): a mapping pinned to a drifted blank revision is
    refused even though its status says fillable."""
    mp = form_tree / "forms" / "TEST-1" / "mapping.json"
    m = json.loads(mp.read_text())
    m["built_against_sha256"] = "0" * 64
    mp.write_text(json.dumps(m))
    res = resolve_mapping("TEST-1", CASE, forms_root=form_tree / "forms",
                          require_built_against=True)
    assert res.get("skipped")
    assert "drifted" in res["reason"]


def test_built_against_sha_matching_revision_resolves(form_tree):
    manifest = json.loads(
        (form_tree / "catalog" / "pdf_manifest.json").read_text())
    pinned = manifest["forms"]["TEST-1"]["sha256"]
    mp = form_tree / "forms" / "TEST-1" / "mapping.json"
    m = json.loads(mp.read_text())
    m["built_against_sha256"] = pinned
    mp.write_text(json.dumps(m))
    res = resolve_mapping("TEST-1", CASE, forms_root=form_tree / "forms",
                          require_built_against=True,
                          manifest_path=form_tree / "catalog" / "pdf_manifest.json")
    assert not res.get("skipped")
    assert res["resolved"] == 4
    # the gate is opt-in: court's default ignores built_against_sha256
    mp_drift = json.loads(mp.read_text())
    mp_drift["built_against_sha256"] = "f" * 64
    mp.write_text(json.dumps(mp_drift))
    res2 = resolve_mapping("TEST-1", CASE, forms_root=form_tree / "forms")
    assert not res2.get("skipped")


def test_tax_result_style_diagnostics(form_tree, tmp_path):
    res = fill_via_mapping("TEST-1", CASE, tmp_path / "out",
                           forms_root=form_tree / "forms",
                           fillable_statuses=TAX_FILLABLE,
                           require_built_against=True,
                           blank_verify_env=("TTF_VERIFY_BLANK",
                                             "MCF_VERIFY_BLANK"),
                           result_style="tax")
    assert res["ok"], res
    assert res["status"] == "verified"
    assert res["missing_widgets"] == []
    assert res["overflowed"] == []
    assert res["blank_verified"] is True
    # tax counts widgets actually written (filled_count), not requested:
    # 3 single widgets + 1 line of the 2-widget narrative group = 4 — unlike
    # the court style, whose fields_written of 5 counts the request (4 fields
    # + the __wrap_cache_ entry the filler adds for the group).
    assert res["fields_written"] == 4
    assert res["unresolved"] == []
