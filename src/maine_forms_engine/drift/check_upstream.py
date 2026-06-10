#!/usr/bin/env python3
"""Detect when the Judicial Branch has revised a blank form out from under us.

The mappings, schema, and field inventory for each form were enriched against
one specific revision of the official PDF, pinned by SHA-256 in
``catalog/pdf_manifest.json``. The portal's DownloadForm endpoint serves the
*current* revision at a stable URL — so when a form is updated, the URL is
unchanged but the bytes (and often the widget layout) are not, and a fill built
on the old mapping can land values in the wrong place.

This tool re-downloads each blank from its official URL, hashes it, and compares
to the manifest. It is read-only by default: nothing is written to ``forms/``,
so it is safe to run on a schedule (CI/cron) as an early-warning that a form
needs re-mapping.

Exit code is non-zero if any form is CHANGED or GONE, so it gates a pipeline.

Usage:
    python3 tools/check_upstream.py                      # check every form
    python3 tools/check_upstream.py --forms AD-001,CV-FM-288
    python3 tools/check_upstream.py --json               # machine-readable report
    python3 tools/check_upstream.py --update-manifest     # after re-mapping: adopt new hashes
"""
import argparse
import json
import pathlib
import sys

# Packaged change (see CHANGES_FROM_DONOR.md): donor's sys.path hack + repo
# imports become package-relative; the manifest path is a --manifest argument.
from ..fill import verify
from .fetch_pdfs import _download  # reuse the verified-download helper

DEFAULT_MANIFEST = pathlib.Path("catalog") / "pdf_manifest.json"


def check_one(fid: str, entry: dict, timeout: int, retries: int) -> dict:
    """Probe one form's official URL and classify it against the manifest."""
    url = entry.get("url")
    if not url:
        return {"form_id": fid, "status": "NO_URL"}
    try:
        data = _download(url, timeout, retries)
    except Exception as e:  # noqa: BLE001
        return {"form_id": fid, "status": "GONE", "detail": str(e)[:160]}
    if data[:5] != b"%PDF-":
        return {"form_id": fid, "status": "GONE",
                "detail": "response is not a PDF (error/HTML page)"}
    got = verify.sha256_bytes(data)
    if got == entry.get("sha256"):
        return {"form_id": fid, "status": "ok", "sha256": got, "bytes": len(data)}
    return {
        "form_id": fid,
        "status": "CHANGED",
        "expected_sha256": entry.get("sha256"),
        "got_sha256": got,
        "expected_bytes": entry.get("bytes"),
        "got_bytes": len(data),
        "url": url,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Detect upstream revisions of the blank PDFs")
    ap.add_argument("--forms", help="comma-separated form ids (default: all)")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--json", action="store_true", help="emit a JSON report")
    ap.add_argument("--update-manifest", action="store_true",
                    help="adopt the new sha256/bytes for CHANGED forms "
                         "(only after the mapping has been re-verified)")
    ap.add_argument("--manifest", type=pathlib.Path, default=DEFAULT_MANIFEST,
                    help="pdf_manifest.json path (default: ./catalog/pdf_manifest.json)")
    args = ap.parse_args()

    MANIFEST = args.manifest
    manifest = verify.load_manifest(MANIFEST)
    forms = manifest["forms"]

    if args.forms:
        want = [f.strip() for f in args.forms.split(",") if f.strip()]
        unknown = [f for f in want if f not in forms]
        if unknown:
            print(f"unknown form ids (not in manifest): {', '.join(unknown)}")
            return 2
        ids = want
    else:
        ids = sorted(forms)

    results = [check_one(f, forms[f], args.timeout, args.retries) for f in ids]

    changed = [r for r in results if r["status"] == "CHANGED"]
    gone = [r for r in results if r["status"] == "GONE"]
    ok = [r for r in results if r["status"] == "ok"]

    if args.json:
        print(json.dumps({"ok": len(ok), "changed": changed, "gone": gone}, indent=2))
    else:
        for r in results:
            if r["status"] == "ok":
                continue
            if r["status"] == "CHANGED":
                print(f"  CHANGED  {r['form_id']}: upstream PDF revised — "
                      f"{r['got_sha256'][:12]}… (was {(r['expected_sha256'] or '')[:12]}…), "
                      f"{r['got_bytes']} bytes (was {r['expected_bytes']}). "
                      f"Re-map before trusting fills.")
            elif r["status"] == "GONE":
                print(f"  GONE     {r['form_id']}: {r.get('detail', 'download failed')}")
            else:
                print(f"  {r['status']:<8} {r['form_id']}")
        print(f"\nok={len(ok)} changed={len(changed)} gone={len(gone)} "
              f"checked={len(results)}")

    if args.update_manifest and changed:
        for r in changed:
            forms[r["form_id"]]["sha256"] = r["got_sha256"]
            forms[r["form_id"]]["bytes"] = r["got_bytes"]
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"\nmanifest updated for {len(changed)} form(s). "
              "Re-run mapping + audit for each before publishing.")

    return 1 if (changed or gone) else 0


if __name__ == "__main__":
    sys.exit(main())
