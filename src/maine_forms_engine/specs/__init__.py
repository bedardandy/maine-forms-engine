"""Shared artifact specs shipped with the package.

- ``pdf_manifest.schema.json`` — JSON Schema for the ``{"forms": {...}}``
  ``pdf_manifest.json`` dialect every consumer repo pins its blanks with
  (read by :mod:`maine_forms_engine.drift` and
  :mod:`maine_forms_engine.fill.verify`). Consumers validate their manifest
  against it in CI via :func:`pdf_manifest_schema`.

The prose spec for the canonical fact object stays in the repo's ``specs/``
directory (documentation, not runtime data).
"""
import importlib.resources
import json


def pdf_manifest_schema() -> dict:
    """Return the parsed pdf_manifest.json JSON Schema."""
    ref = importlib.resources.files(__package__) / "pdf_manifest.schema.json"
    return json.loads(ref.read_text(encoding="utf-8"))
