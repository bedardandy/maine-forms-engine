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


def check_one(fid: str, entry: dict, timeout: int, retries: int,
              downloader=None, on_download_error=None) -> dict:
    """Probe one form's official URL and classify it against the manifest.

    ``downloader`` (a ``(url, timeout, retries) -> bytes`` callable) lets a
    consumer shim route through its own download helper — e.g. so its tests
    can keep stubbing ``tools.check_upstream._download``.

    ``on_download_error`` (an ``(exception) -> status_str`` callable) lets a
    consumer refine how a failed download classifies. The default is the
    donor behavior: every failure is ``"GONE"``. The corp repo returns
    ``"ERROR"`` for transient failures (timeout, DNS, HTTP 5xx) and reserves
    ``"GONE"`` for a definitive 404/410, so a scheduled run does not
    false-alarm on network flake — ``"ERROR"`` results never gate
    ``main()``'s exit code."""
    url = entry.get("url")
    if not url:
        return {"form_id": fid, "status": "NO_URL"}
    try:
        data = (downloader or _download)(url, timeout, retries)
    except Exception as e:  # noqa: BLE001
        status = on_download_error(e) if on_download_error else "GONE"
        return {"form_id": fid, "status": status, "detail": str(e)[:160]}
    if data[:5] != b"%PDF-":
        return {"form_id": fid, "status": "GONE",
                "detail": "response is not a PDF (error/HTML page)"}
    got = verify.sha256_bytes(data)
    if got == entry.get("sha256"):
        return {"form_id": fid, "status": "ok", "sha256": got, "bytes": len(data)}
    res = {
        "form_id": fid,
        "status": "CHANGED",
        "expected_sha256": entry.get("sha256"),
        "got_sha256": got,
        "expected_bytes": entry.get("bytes"),
        "got_bytes": len(data),
        "url": url,
    }
    # Inspect the revised blank so --update-manifest can adopt every manifest
    # field (num_pages / has_acroform), not just the hash. (Adopted from the
    # transactional-tax-forms fork; see CHANGES_FROM_DONOR.md.)
    try:
        import fitz  # noqa: PLC0415 — optional; the drift report stands without it
        doc = fitz.open(stream=data, filetype="pdf")
        res["got_num_pages"] = doc.page_count
        res["got_has_acroform"] = any((p.widgets() or []) for p in doc)
        doc.close()
    except Exception as e:  # noqa: BLE001 — drift report still stands
        res["inspect_error"] = repr(e)[:120]
    return res


DEFAULT_UPDATE_HINT = "Re-run mapping + audit for each before publishing."


def main(argv=None, *, default_manifest: pathlib.Path | None = None,
         update_hint: str | None = None, downloader=None,
         on_download_error=None, entry_filter=None,
         default_retries: int = 3, default_timeout: int = 30) -> int:
    """CLI entry point.

    The keyword hooks exist for consumer-repo shims:

    - ``default_manifest`` pins the repo's manifest path; ``update_hint``
      supplies the post-``--update-manifest`` guidance (e.g. the tax repo
      points at its ``tools/build_manifest.py`` re-inventory step).
    - ``on_download_error`` refines failed-download classification (see
      :func:`check_one`). Results classified ``"ERROR"`` (transient) are
      reported — an ``errors`` list in the JSON report, an ``errors=`` count
      in the summary — but never gate the exit code.
    - ``entry_filter`` (``(form_id, entry) -> bool``) restricts the default
      (no ``--forms``) probe set — e.g. the corp repo probes only manifest
      entries flagged ``"fetch": true``.
    - ``default_retries`` / ``default_timeout`` override the donor's
      ``--retries`` / ``--timeout`` defaults.
    """
    ap = argparse.ArgumentParser(description="Detect upstream revisions of the blank PDFs")
    ap.add_argument("--forms", help="comma-separated form ids (default: all)")
    ap.add_argument("--timeout", type=int, default=default_timeout)
    ap.add_argument("--retries", type=int, default=default_retries)
    ap.add_argument("--json", action="store_true", help="emit a JSON report")
    ap.add_argument("--update-manifest", action="store_true",
                    help="adopt the new sha256/bytes (and num_pages/"
                         "has_acroform when known) for CHANGED forms "
                         "(only after the mapping has been re-verified)")
    ap.add_argument("--manifest", type=pathlib.Path,
                    default=default_manifest or DEFAULT_MANIFEST,
                    help="pdf_manifest.json path (default: ./catalog/pdf_manifest.json)")
    args = ap.parse_args(argv)

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
        if entry_filter is not None:
            ids = [f for f in ids if entry_filter(f, forms[f])]

    results = [check_one(f, forms[f], args.timeout, args.retries,
                         downloader=downloader,
                         on_download_error=on_download_error) for f in ids]

    changed = [r for r in results if r["status"] == "CHANGED"]
    gone = [r for r in results if r["status"] == "GONE"]
    errors = [r for r in results if r["status"] == "ERROR"]
    ok = [r for r in results if r["status"] == "ok"]

    if args.json:
        report = {"ok": len(ok), "changed": changed, "gone": gone}
        if on_download_error is not None:
            report["errors"] = errors
        print(json.dumps(report, indent=2))
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
            elif r["status"] == "ERROR":
                print(f"  ERROR    {r['form_id']}: {r.get('detail', 'download failed')} "
                      f"(transient — does not gate; re-run)")
            else:
                print(f"  {r['status']:<8} {r['form_id']}")
        tail = (f" errors={len(errors)}" if on_download_error is not None else "")
        print(f"\nok={len(ok)} changed={len(changed)} gone={len(gone)}"
              f"{tail} checked={len(results)}")

    if args.update_manifest and changed:
        for r in changed:
            e = forms[r["form_id"]]
            e["sha256"] = r["got_sha256"]
            e["bytes"] = r["got_bytes"]
            if "got_num_pages" in r:
                e["num_pages"] = r["got_num_pages"]
            if "got_has_acroform" in r:
                e["has_acroform"] = r["got_has_acroform"]
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        hint = update_hint or DEFAULT_UPDATE_HINT
        if callable(hint):
            hint = hint(changed)
        print(f"\nmanifest updated for {len(changed)} form(s). {hint}")

    return 1 if (changed or gone) else 0


if __name__ == "__main__":
    sys.exit(main())
