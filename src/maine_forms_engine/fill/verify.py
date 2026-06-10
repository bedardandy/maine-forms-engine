"""Pin each blank PDF to the revision the mappings were built against.

``catalog/pdf_manifest.json`` records a SHA-256 (plus byte size) for every blank
Maine Judicial Branch form — the revision the mapping, schema, and field
inventory were enriched against. If the courts re-upload a revised form at the
*same* DownloadForm URL, its bytes change and the widget layout may shift; a fill
built on the old mapping then lands values in the wrong place. These checks fail
loudly on a mismatch — the signal to re-map before trusting a fill.

The manifest here is keyed by form id under ``"forms"`` (the shape
``tools/fetch_pdfs.py`` reads), e.g. ``{"forms": {"AD-001": {"sha256": …}}}``.

- :func:`verify_blank` — does the blank *on disk* still match the manifest? Used
  as a fill-time guard so a swapped-out local file cannot be filled silently.
- the manifest helpers are reused by ``tools/check_upstream.py``, which
  re-downloads from the official URL to detect upstream drift.
"""
import hashlib
import json
from pathlib import Path

# Packaged change (see CHANGES_FROM_DONOR.md): the donor anchored these to its
# repo root via __file__. The package defaults to the same layout relative to
# the current working directory; consumers pass manifest_path / forms_root.
_MANIFEST = Path("catalog") / "pdf_manifest.json"
_DEFAULT_FORMS_ROOT = Path("forms")


class BlankRevisionWarning(UserWarning):
    """The on-disk blank does not match the manifest hash (non-fatal)."""


class BlankRevisionError(RuntimeError):
    """The on-disk blank does not match the manifest hash (strict mode)."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_manifest(manifest_path=None) -> dict:
    path = Path(manifest_path) if manifest_path else _MANIFEST
    return json.loads(path.read_text(encoding="utf-8"))


def manifest_entry(form_id: str, manifest=None):
    """Return the manifest record for ``form_id`` (or ``None``)."""
    man = manifest if manifest is not None else load_manifest()
    return man.get("forms", {}).get(form_id)


def verify_blank(form_id, forms_root=None, manifest=None):
    """Check the on-disk blank for ``form_id`` against the manifest.

    Returns ``(ok, detail)``. ``ok`` is ``True`` only when a manifest entry with
    a SHA-256 exists and the on-disk PDF matches it byte-for-byte. A missing
    file, a missing manifest hash, or a size/hash mismatch returns ``False`` with
    a human-readable reason.
    """
    entry = manifest_entry(form_id, manifest)
    if entry is None:
        return False, f"{form_id}: not in manifest"
    expected = entry.get("sha256")
    if not expected:
        return False, f"{form_id}: manifest has no sha256 to verify against"
    root = Path(forms_root) if forms_root else _DEFAULT_FORMS_ROOT
    pdf = root / form_id / f"{form_id}.pdf"
    if not pdf.exists():
        return False, f"{form_id}: blank not present ({pdf}); run tools/fetch_pdfs.py"
    data = pdf.read_bytes()
    if entry.get("bytes") is not None and len(data) != entry["bytes"]:
        return False, (f"{form_id}: size {len(data)} != manifest {entry['bytes']} — "
                       "on-disk blank is not the revision the mapping was built against")
    got = sha256_bytes(data)
    if got != expected:
        return False, (f"{form_id}: SHA-256 mismatch — on-disk blank is not the "
                       f"revision the mapping was built against "
                       f"(got {got[:12]}…, manifest {expected[:12]}…); re-map this form")
    return True, f"{form_id}: verified against manifest"


def guard_blank(form_id, forms_root=None, mode="warn", manifest=None) -> bool:
    """Fill-time guard. ``mode`` is ``"warn"`` (default), ``"strict"``, or ``"off"``.

    ``warn``  — emit :class:`BlankRevisionWarning` on mismatch, return ``False``.
    ``strict`` — raise :class:`BlankRevisionError` on mismatch.
    ``off``   — skip the check, return ``True``.

    A clean verify always returns ``True``.
    """
    if mode == "off":
        return True
    ok, detail = verify_blank(form_id, forms_root, manifest)
    if ok:
        return True
    if mode == "strict":
        raise BlankRevisionError(detail)
    import warnings
    warnings.warn(detail, BlankRevisionWarning, stacklevel=3)
    return False
