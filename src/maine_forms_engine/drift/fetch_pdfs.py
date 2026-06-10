#!/usr/bin/env python3
"""Fetch blank source PDFs from the official Maine Judicial Branch portal.

The library does not redistribute the blank PDFs (see LICENSE.md —
licensing). Instead each form folder ships its metadata, and this tool pulls
the official blank into ``forms/<FORM_ID>/<FORM_ID>.pdf`` on demand, verifying
each download against the size + SHA-256 recorded in ``catalog/pdf_manifest.json``.

    # fetch everything (skips files already present and valid)
    python3 tools/fetch_pdfs.py

    # fetch a subset, re-downloading even if present
    python3 tools/fetch_pdfs.py --forms AD-001,AD-022 --force

Each download is deterministic: the URL is the per-form entry in the manifest
(the portal's parametrized DownloadForm endpoint), and the bytes are checked
against the recorded SHA-256, so a fetched file is provably the same blank the
library was built against — without the repo ever shipping the PDF.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import ssl
import sys
import time
import urllib.error
import urllib.request

# Packaged change (see CHANGES_FROM_DONOR.md): repo-root anchors replaced by
# cwd-relative defaults + --forms-root; the User-Agent names the shared engine.
DEFAULT_MANIFEST = pathlib.Path("catalog") / "pdf_manifest.json"
DEFAULT_FORMS_ROOT = pathlib.Path("forms")
USER_AGENT = "maine-forms-engine/fetch_pdfs (public forms; verified against manifest)"


def _download(url: str, timeout: int, retries: int) -> bytes:
    ctx = ssl.create_default_context()
    last = None
    for attempt in range(max(1, retries)):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise last  # type: ignore[misc]


def _verify(data: bytes, entry: dict) -> str | None:
    """Return an error string if the bytes don't match the manifest, else None."""
    if data[:5] != b"%PDF-":
        return "not a PDF (bad magic bytes — likely an error/HTML page)"
    if "bytes" in entry and len(data) != entry["bytes"]:
        return f"size {len(data)} != expected {entry['bytes']}"
    if "sha256" in entry:
        got = hashlib.sha256(data).hexdigest()
        if got != entry["sha256"]:
            return f"sha256 mismatch (got {got[:12]}…)"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", type=pathlib.Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--forms-root", type=pathlib.Path,
                    default=DEFAULT_FORMS_ROOT,
                    help="per-form artifact tree (default: ./forms)")
    ap.add_argument("--forms", default="", help="comma list to limit; omit for all")
    ap.add_argument("--force", action="store_true",
                    help="re-download even if a valid file is already present")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--retries", type=int, default=3)
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text())
    forms = manifest["forms"]
    if args.forms:
        want = [f.strip() for f in args.forms.split(",") if f.strip()]
        missing = [f for f in want if f not in forms]
        if missing:
            print(f"unknown form ids (not in manifest): {', '.join(missing)}")
            return 2
        forms = {f: forms[f] for f in want}

    n_ok = n_skip = n_fetch = n_fail = 0
    failures: list[str] = []
    for fid in sorted(forms):
        entry = forms[fid]
        dest = args.forms_root / fid / f"{fid}.pdf"
        # Already present and valid → skip (unless --force).
        if dest.exists() and not args.force:
            err = _verify(dest.read_bytes(), entry)
            if err is None:
                print(f"  skip   {fid}: present and verified")
                n_skip += 1
                continue
            print(f"  stale  {fid}: on-disk file fails verify ({err}); re-fetching")

        try:
            data = _download(entry["url"], args.timeout, args.retries)
        except Exception as e:  # noqa: BLE001 — report and continue
            print(f"  FAIL   {fid}: download error: {e}")
            n_fail += 1
            failures.append(fid)
            continue

        err = _verify(data, entry)
        if err is not None:
            print(f"  FAIL   {fid}: {err}")
            n_fail += 1
            failures.append(fid)
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        print(f"  OK     {fid}: {len(data)} bytes, verified")
        n_fetch += 1
        n_ok += 1

    print(f"\nfetched {n_fetch}, skipped {n_skip} (already valid), "
          f"failed {n_fail} of {len(forms)}")
    if failures:
        print("failed forms:", ", ".join(failures))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
