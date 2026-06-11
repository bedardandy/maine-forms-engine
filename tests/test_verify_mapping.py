"""Mapping-staleness verifier (verify_mapping): blank identity + field
survival, report-only by default, --stamp only for forms that fully verify.

The fixtures mirror the donor's (transactional-tax-forms
tools/verify_mapping_fields.py) repo format exactly — forms/<ID>/<ID>.pdf +
schema.json + mapping.json, catalog/pdf_manifest.json {"forms": {...}} — so a
green run here is the donor-shim parity proof (the conftest tree was itself
ported from the tax offline suite)."""
import hashlib
import json

from conftest import synthetic_form

from maine_forms_engine import verify_mapping as vm


def _manifest(root):
    return json.loads((root / "catalog" / "pdf_manifest.json").read_text())


def _mapping_path(root, fid="TEST-1"):
    return root / "forms" / fid / "mapping.json"


# --- verify_form: the donor's failure ladder ---------------------------------

def test_verify_ok(form_tree):
    r = vm.verify_form("TEST-1", _manifest(form_tree),
                       form_tree / "forms")
    assert r["ok"], r
    assert r["mapped_fields"] == 4
    assert r["missing_in_schema"] == [] and r["missing_in_pdf"] == []
    assert r["manifest_sha256"] == \
        _manifest(form_tree)["forms"]["TEST-1"]["sha256"]


def test_donor_positional_signature(form_tree):
    """The donor calls verify_form(fid, manifest) with forms_root defaulted /
    positional — the tax shim must be able to forward unchanged."""
    r = vm.verify_form("TEST-1", _manifest(form_tree), form_tree / "forms")
    assert r["ok"]


def test_swapped_blank_refused(form_tree):
    """Field survival is only meaningful against the pinned revision; a
    swapped on-disk blank must refuse verification, not pass the field check
    against the wrong bytes."""
    pdf = form_tree / "forms" / "TEST-1" / "TEST-1.pdf"
    synthetic_form(pdf, captions=True)  # different bytes, same widgets
    r = vm.verify_form("TEST-1", _manifest(form_tree), form_tree / "forms")
    assert not r["ok"]
    assert "refusing to verify" in r["reason"]


def test_missing_blank(form_tree):
    (form_tree / "forms" / "TEST-1" / "TEST-1.pdf").unlink()
    r = vm.verify_form("TEST-1", _manifest(form_tree), form_tree / "forms")
    assert not r["ok"]
    assert "blank not fetched" in r["reason"]


def test_no_manifest_entry(form_tree):
    r = vm.verify_form("TEST-1", {"forms": {}}, form_tree / "forms")
    assert not r["ok"]
    assert "sha256 entry" in r["reason"]


def test_empty_map_is_a_recipe_pointer(form_tree):
    p = _mapping_path(form_tree)
    p.write_text(json.dumps({"form_id": "TEST-1", "status": "recipe",
                             "map": {}}))
    r = vm.verify_form("TEST-1", _manifest(form_tree), form_tree / "forms")
    assert not r["ok"]
    assert "recipe pointer" in r["reason"]
    assert r["status"] == "recipe"


def test_map_key_absent_from_schema(form_tree):
    p = _mapping_path(form_tree)
    mapping = json.loads(p.read_text())
    mapping["map"]["ghost_field"] = "facts.ghost"
    p.write_text(json.dumps(mapping))
    r = vm.verify_form("TEST-1", _manifest(form_tree), form_tree / "forms")
    assert not r["ok"]
    assert r["missing_in_schema"] == ["ghost_field"]
    assert r["missing_in_pdf"] == []


def test_widget_absent_from_pdf(form_tree):
    """The MRS-1041ME class: schema/mapping reference a widget name the
    (pinned) blank no longer carries."""
    sp = form_tree / "forms" / "TEST-1" / "schema.json"
    schema = json.loads(sp.read_text())
    for f in schema["fields"]:
        if f["field_id"] == "addr_field":
            f["label"] = "addr_field_renamed"
    sp.write_text(json.dumps(schema))
    r = vm.verify_form("TEST-1", _manifest(form_tree), form_tree / "forms")
    assert not r["ok"]
    assert r["missing_in_pdf"] == ["addr_field_renamed"]
    assert "re-map before stamping" in r["reason"]


def test_field_split_names_count_as_live(form_tree):
    """A field_splits.json rename is introduced on the fill-time working
    copy, so the renamed widget counts as present in the blank."""
    fdir = form_tree / "forms" / "TEST-1"
    schema = json.loads((fdir / "schema.json").read_text())
    for f in schema["fields"]:
        if f["field_id"] == "addr_field":
            f["label"] = "addr_field_split"
    (fdir / "schema.json").write_text(json.dumps(schema))
    (fdir / "field_splits.json").write_text(json.dumps({
        "splits": [{"name": "addr_field", "new_name": "addr_field_split"}]}))
    r = vm.verify_form("TEST-1", _manifest(form_tree), form_tree / "forms")
    assert r["ok"], r


# --- format hooks (non-donor dialects) ---------------------------------------

def test_direct_widget_names_hook(form_tree):
    """Corp dialect: map keys ARE widget names, values are spec dicts, and
    there is no schema field inventory to resolve through."""
    fdir = form_tree / "forms" / "TEST-1"
    (fdir / "schema.json").unlink()  # corp's schema.json is a case JSON Schema
    (fdir / "mapping.json").write_text(json.dumps({
        "form_id": "TEST-1",
        "map": {"name_field": {"key": "entity.name", "field_type": "text"},
                "consent_box": {"key": "filing.consents",
                                "field_type": "checkbox"}}}))
    r = vm.verify_form("TEST-1", _manifest(form_tree), form_tree / "forms",
                       resolve_widgets=vm.direct_widget_names)
    assert r["ok"], r
    assert r["mapped_fields"] == 2


def test_manifest_entry_and_blank_path_hooks(form_tree, tmp_path):
    """A consumer with a flat manifest and a filename-keyed blank location
    supplies hooks instead of converting up front."""
    flat = {"TEST-1": _manifest(form_tree)["forms"]["TEST-1"]}
    flat["TEST-1"]["filename"] = "blank.pdf"
    fdir = form_tree / "forms" / "TEST-1"
    (fdir / "TEST-1.pdf").rename(fdir / "blank.pdf")
    r = vm.verify_form(
        "TEST-1", flat, form_tree / "forms",
        manifest_entry=lambda man, fid: man.get(fid),
        blank_path=lambda fdir, fid, entry: fdir / entry["filename"])
    assert r["ok"], r


# --- stamp -------------------------------------------------------------------

def test_stamp_inserts_after_anchor_and_is_idempotent(form_tree):
    sha = _manifest(form_tree)["forms"]["TEST-1"]["sha256"]
    assert vm.stamp("TEST-1", sha, form_tree / "forms") is True
    mapping = json.loads(_mapping_path(form_tree).read_text())
    keys = list(mapping)
    assert mapping["built_against_sha256"] == sha
    # donor placement: right after "model" when present, else "status"
    assert keys.index("built_against_sha256") == keys.index("status") + 1
    assert vm.stamp("TEST-1", sha, form_tree / "forms") is False  # no rewrite


def test_stamp_appends_when_no_anchor(form_tree):
    """Corp-dialect mapping (no model/status): the stamp still lands instead
    of being silently dropped."""
    _mapping_path(form_tree).write_text(json.dumps(
        {"form_id": "TEST-1", "map": {"name_field": "entity.name"}}))
    assert vm.stamp("TEST-1", "f" * 64, form_tree / "forms") is True
    mapping = json.loads(_mapping_path(form_tree).read_text())
    assert mapping["built_against_sha256"] == "f" * 64
    assert list(mapping)[-1] == "built_against_sha256"


def test_stamp_preserves_indent_and_trailing_newline(form_tree):
    p = _mapping_path(form_tree)
    p.write_text(json.dumps(json.loads(p.read_text()), indent=4) + "\n")
    vm.stamp("TEST-1", "a" * 64, form_tree / "forms")
    raw = p.read_text()
    assert raw.startswith('{\n    "') and raw.endswith("\n")
    p.write_text(json.dumps(json.loads(p.read_text()), indent=2))  # no \n
    vm.stamp("TEST-1", "b" * 64, form_tree / "forms")
    raw = p.read_text()
    assert raw.startswith('{\n  "') and not raw.endswith("\n")


# --- CLI (donor-reproducible behavior) ----------------------------------------

def _cli(form_tree, *extra):
    return ["--forms-root", str(form_tree / "forms"),
            "--manifest", str(form_tree / "catalog" / "pdf_manifest.json"),
            *extra]


def test_cli_ok_report(form_tree, capsys):
    assert vm.main(_cli(form_tree)) == 0
    out = capsys.readouterr().out
    assert "OK " in out and "TEST-1 (4 mapped fields live)" in out
    # report-only: no stamp was written
    assert "built_against_sha256" not in _mapping_path(form_tree).read_text()


def test_cli_json_report(form_tree, capsys):
    assert vm.main(_cli(form_tree, "--json")) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["failed"] == []
    assert report["results"][0]["form_id"] == "TEST-1"
    assert report["results"][0]["ok"] is True


def test_cli_forms_filter(form_tree, capsys):
    assert vm.main(_cli(form_tree, "--forms", "TEST-1")) == 0
    assert "TEST-1" in capsys.readouterr().out


def test_cli_stamp_only_stamps_verified_forms(form_tree, capsys):
    """Two forms, one failing: exit non-zero, the good form is stamped, the
    failing form's mapping is byte-identical afterwards."""
    # second form whose blank drifted from its manifest pin
    fdir = form_tree / "forms" / "TEST-2"
    fdir.mkdir()
    synthetic_form(fdir / "TEST-2.pdf")
    (fdir / "schema.json").write_text(
        (form_tree / "forms" / "TEST-1" / "schema.json").read_text())
    bad_mapping = json.dumps({"form_id": "TEST-2", "status": "verified",
                              "map": {"name_field": "x.y"}})
    (fdir / "mapping.json").write_text(bad_mapping)
    man = _manifest(form_tree)
    man["forms"]["TEST-2"] = {"sha256": "0" * 64, "bytes": 1,
                              "url": "https://example.test/TEST-2"}
    (form_tree / "catalog" / "pdf_manifest.json").write_text(json.dumps(man))

    assert vm.main(_cli(form_tree, "--stamp")) == 1
    out = capsys.readouterr().out
    assert "FAIL TEST-2" in out and "[stamped]" in out
    assert "do NOT stamp them" in out
    stamped = json.loads(_mapping_path(form_tree).read_text())
    assert stamped["built_against_sha256"] == \
        man["forms"]["TEST-1"]["sha256"]
    assert (fdir / "mapping.json").read_text() == bad_mapping  # untouched


def test_cli_consumer_shim_defaults(form_tree, capsys):
    """A consumer shim pins its layout via default_forms_root /
    default_manifest and forwards argv untouched."""
    assert vm.main([], default_forms_root=form_tree / "forms",
                   default_manifest=form_tree / "catalog"
                   / "pdf_manifest.json") == 0
    assert "TEST-1 (4 mapped fields live)" in capsys.readouterr().out
