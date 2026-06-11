#!/usr/bin/env python3
"""Re-verify a mapping.json against the pinned blank, then stamp it.

``built_against_sha256`` is the staleness gate the engine enforces at fill
time (the MRS-1041ME incident class: the status flag said fillable while 37
mapped widgets no longer existed in a re-issued blank). A mapping must only
carry the stamp after an honest re-verification against the very revision the
manifest pins — never as a blind back-fill. Per form this tool checks:

1. **blank identity** — the on-disk ``forms/<ID>/<ID>.pdf`` matches the
   ``catalog/pdf_manifest.json`` SHA-256 byte-for-byte (so the field check
   below is made against the pinned revision, not a swapped file);
2. **field survival** — every ``mapping.map`` field_id resolves through
   ``schema.json`` to a widget name present in the blank's AcroForm tree.
   Names introduced by ``field_splits.json`` count as present (the fill path
   splits a working copy before writing). ``"manual"`` radio entries and
   documented ``dropped_keys`` never enter ``map`` and are exempt by
   construction.

Read-only by default. ``--stamp`` writes ``built_against_sha256`` (the
manifest hash) into ``mapping.json`` — only for forms that fully verify; a
form that fails stays unstamped and is reported (re-map it, the MRS-1041ME
treatment, before it may fill). Exit code is non-zero when any checked form
fails, so it gates a pipeline.

Usage:
    python3 -m maine_forms_engine.verify_mapping                 # verify all
    python3 -m maine_forms_engine.verify_mapping --forms IRS-706
    python3 -m maine_forms_engine.verify_mapping --json          # machine report
    python3 -m maine_forms_engine.verify_mapping --stamp         # verify + stamp

Packaged change (see CHANGES_FROM_DONOR.md): the donor
(transactional-tax-forms ``tools/verify_mapping_fields.py``) anchored
``forms/`` and the manifest to its repo root via ``__file__``; the package
defaults to the same layout relative to the current working directory and
takes ``--forms-root`` / ``--manifest`` (and ``forms_root=`` / ``manifest=``
parameters) explicitly. The consumer-repo format differences are constructor
hooks (``manifest_entry`` / ``blank_path`` / ``resolve_widgets`` /
``split_names``), defaulting to the donor (tax/court) dialect.
"""
import argparse
import hashlib
import json
import pathlib
import sys

DEFAULT_FORMS_ROOT = pathlib.Path("forms")
DEFAULT_MANIFEST = pathlib.Path("catalog") / "pdf_manifest.json"


def _acroform_names(pdf_path: pathlib.Path) -> set:
    import fitz
    doc = fitz.open(str(pdf_path))
    try:
        return {w.field_name for page in doc for w in page.widgets() or []}
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Format hooks. Defaults are the donor (transactional-tax-forms) dialect,
# which the court repo shares; a consumer with a divergent layout passes its
# own callable instead of the package growing repo-name conditionals.

def manifest_entry(manifest: dict, fid: str) -> dict:
    """Default ``manifest_entry`` hook: the ``{"forms": {<id>: {...}}}``
    dialect every consumer manifest uses (``specs/pdf_manifest.schema.json``)."""
    return (manifest.get("forms") or {}).get(fid) or {}


def blank_path(fdir: pathlib.Path, fid: str, entry: dict) -> pathlib.Path:
    """Default ``blank_path`` hook: ``forms/<ID>/<ID>.pdf``."""
    return fdir / f"{fid}.pdf"


def schema_widget_names(fdir: pathlib.Path, fmap: dict) -> tuple:
    """Default ``resolve_widgets`` hook (the tax/court dialect): ``map`` keys
    are ``schema.json`` field_ids; each resolves to its ``label``, the live
    AcroForm widget name. Returns ``({map_key: widget_name},
    [keys absent from the schema])``."""
    labels = {f["field_id"]: f["label"]
              for f in json.loads((fdir / "schema.json").read_text())
              .get("fields", [])}
    return ({f: labels[f] for f in fmap if f in labels},
            sorted(f for f in fmap if f not in labels))


def direct_widget_names(fdir: pathlib.Path, fmap: dict) -> tuple:
    """``resolve_widgets`` hook for the corp dialect: ``map`` keys ARE the
    AcroForm widget names (no schema field inventory; the values are
    ``{key, field_type, ...}`` dicts the check never reads)."""
    return {k: k for k in fmap}, []


def split_names(fdir: pathlib.Path) -> set:
    """Default ``split_names`` hook: widget names a fill-time field split
    introduces (``field_splits.json``)."""
    p = fdir / "field_splits.json"
    if not p.exists():
        return set()
    spec = json.loads(p.read_text())
    return {s["new_name"] for s in spec.get("splits", []) if s.get("new_name")}


# kept under the donor's private name so a consumer shim can re-export it
_split_names = split_names


def verify_form(fid: str, manifest: dict,
                forms_root: pathlib.Path = DEFAULT_FORMS_ROOT, *,
                manifest_entry=manifest_entry, blank_path=blank_path,
                resolve_widgets=schema_widget_names,
                split_names=split_names) -> dict:
    """Verify one form's mapping against the pinned blank.

    Returns ``{form_id, ok, ...}``; ``ok`` is True only when the blank
    matches the manifest hash AND every mapped field resolves to a live
    widget. Failure modes carry a ``reason`` plus the offending lists.

    The keyword hooks default to the donor (tax/court) dialect; see the
    module docstring.
    """
    fdir = forms_root / fid
    out = {"form_id": fid, "ok": False}
    mapping = json.loads((fdir / "mapping.json").read_text())
    out["status"] = mapping.get("status")
    fmap = mapping.get("map") or {}
    if not fmap:
        out["reason"] = "empty map (recipe pointer) — nothing to verify"
        return out
    entry = manifest_entry(manifest, fid) or {}
    pinned = (entry.get("sha256") or "").lower()
    if not pinned:
        out["reason"] = "no catalog/pdf_manifest.json sha256 entry"
        return out
    out["manifest_sha256"] = pinned
    pdf = blank_path(fdir, fid, entry)
    if not pdf.exists():
        out["reason"] = f"blank not fetched: {pdf.name} (tools/fetch_pdfs.py)"
        return out
    on_disk = hashlib.sha256(pdf.read_bytes()).hexdigest()
    if on_disk != pinned:
        out["reason"] = (f"on-disk blank is {on_disk[:12]}… but the manifest "
                         f"pins {pinned[:12]}… — refusing to verify a mapping "
                         "against the wrong revision")
        return out
    widget_by_key, missing_in_schema = resolve_widgets(fdir, fmap)
    names = _acroform_names(pdf) | split_names(fdir)
    missing_in_pdf = sorted({w for w in widget_by_key.values()
                             if w not in names})
    out["mapped_fields"] = len(fmap)
    out["missing_in_schema"] = missing_in_schema
    out["missing_in_pdf"] = missing_in_pdf
    if missing_in_schema or missing_in_pdf:
        out["reason"] = (f"{len(missing_in_schema)} field_id(s) absent from "
                         f"schema.json, {len(missing_in_pdf)} widget name(s) "
                         "absent from the blank — re-map before stamping")
        return out
    out["ok"] = True
    return out


def stamp(fid: str, sha: str,
          forms_root: pathlib.Path = DEFAULT_FORMS_ROOT) -> bool:
    """Write ``built_against_sha256`` into mapping.json (after ``model`` /
    ``status``, the shape the tax mappings already carry; appended when the
    mapping has neither anchor — the corp dialect). Returns True when the
    file changed."""
    p = forms_root / fid / "mapping.json"
    raw = p.read_text()
    mapping = json.loads(raw)
    if mapping.get("built_against_sha256") == sha:
        return False
    anchor = ("model" if "model" in mapping
              else "status" if "status" in mapping else None)
    rebuilt = {}
    for k, v in mapping.items():
        if k == "built_against_sha256":
            continue
        rebuilt[k] = v
        if k == anchor:
            rebuilt["built_against_sha256"] = sha
    if anchor is None:
        rebuilt["built_against_sha256"] = sha
    indent = 4 if raw.startswith('{\n    "') else 2
    p.write_text(json.dumps(rebuilt, indent=indent)
                 + ("\n" if raw.endswith("\n") else ""))
    return True


def main(argv=None, *, default_forms_root: pathlib.Path | None = None,
         default_manifest: pathlib.Path | None = None,
         manifest_entry=manifest_entry, blank_path=blank_path,
         resolve_widgets=schema_widget_names, split_names=split_names) -> int:
    """CLI entry point.

    The keyword hooks exist for consumer-repo shims: ``default_forms_root`` /
    ``default_manifest`` pin the repo's layout (so its tool keeps running
    from anywhere), and the four format hooks pass through to
    :func:`verify_form`.
    """
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--forms", help="comma list of form ids (default: all)")
    ap.add_argument("--stamp", action="store_true",
                    help="write built_against_sha256 for forms that verify")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="machine-readable report on stdout")
    ap.add_argument("--forms-root", type=pathlib.Path,
                    default=default_forms_root or DEFAULT_FORMS_ROOT,
                    help="per-form artifact tree (default: ./forms)")
    ap.add_argument("--manifest", type=pathlib.Path,
                    default=default_manifest or DEFAULT_MANIFEST,
                    help="pdf_manifest.json path "
                         "(default: ./catalog/pdf_manifest.json)")
    args = ap.parse_args(argv)
    manifest = json.loads(args.manifest.read_text())
    forms_root = args.forms_root
    fids = ([f.strip() for f in args.forms.split(",") if f.strip()]
            if args.forms else
            sorted(d.name for d in forms_root.iterdir()
                   if (d / "mapping.json").exists()))
    results, failed = [], []
    for fid in fids:
        r = verify_form(fid, manifest, forms_root,
                        manifest_entry=manifest_entry, blank_path=blank_path,
                        resolve_widgets=resolve_widgets,
                        split_names=split_names)
        if r["ok"] and args.stamp:
            r["stamped"] = stamp(fid, r["manifest_sha256"], forms_root)
        results.append(r)
        if not r["ok"]:
            failed.append(fid)
        if not args.as_json:
            mark = "OK " if r["ok"] else "FAIL"
            extra = (f" ({r['mapped_fields']} mapped fields live)"
                     if r["ok"] else f" — {r['reason']}")
            if r.get("stamped"):
                extra += " [stamped]"
            print(f"{mark} {fid}{extra}")
    if args.as_json:
        print(json.dumps({"results": results, "failed": failed}, indent=2))
    elif failed:
        print(f"\n{len(failed)} form(s) failed verification: "
              f"{', '.join(failed)} — fix or mark remap-pending; "
              "do NOT stamp them.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
