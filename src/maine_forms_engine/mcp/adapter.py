"""The backend adapter a consuming repo supplies to the MCP scaffold.

The four forms repos expose conceptually parallel MCP tools but, before this
package, four dialects of them: court's ``find_forms(query) -> dict`` with
matter workflows vs tax/corp's ``find_forms(situation, top_k) -> list``;
``facts`` vs ``case``; ``out_dir`` vs ``out_path``; corp's separate
``plan_fill`` vs probate folding the plan into ``fill_form``; and mixed error
shapes (``{"error": ...}`` vs ``{"ok": False, "error": ...}`` vs raising).

The scaffold (``server.build_server``) standardizes the agent-facing surface:

    find_forms(query, top_k=5)
    get_form(form_id)
    plan_fill(form_id, case)
    fill_form(form_id, case, out_dir)

with ONE error shape — every tool returns a dict, failures are always
``{"ok": False, "error": "<message>", "error_type": "<ExceptionName>"}`` —
while everything domain-specific (routing, trust vocabulary, which fill path
runs, what diagnostics come back) lives in the backend object the repo passes
in. A backend implements this protocol:

- ``name``: the MCP server name (must stay unique per repo so all four servers
  can register simultaneously, e.g. ``"maine-court-forms"``).
- ``find_forms(query, top_k)``: return a list of candidate dicts, or a dict
  (court returns ``{"workflows": [...], "forms": [...]}``; a dict is merged
  into the result envelope, a list becomes ``results``).
- ``get_form(form_id)``: return the form's metadata dict. Raise
  :class:`UnknownFormError` (or ``KeyError``) for an unknown id.
- ``plan_fill(form_id, case)``: OPTIONAL — return a coverage-plan dict
  (corp's buckets, court's ``resolve_mapping`` coverage, ...). Backends that
  have no separate plan step simply don't define it; the scaffold answers
  with the standard error shape instead of crashing.
- ``fill_form(form_id, case, out_dir)``: write the filled PDF and return the
  repo's fill-result dict (must carry ``ok``; everything else — out_pdf/path,
  diagnostics, trust tier, caveats — passes through to the agent).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


class UnknownFormError(KeyError):
    """Raised by a backend when form_id is not in its catalog."""


@runtime_checkable
class FormsBackend(Protocol):
    """What the MCP scaffold needs from a consuming repo."""

    name: str

    def find_forms(self, query: str, top_k: int) -> list | dict:
        """Route a plain-language situation to candidate forms."""
        ...

    def get_form(self, form_id: str) -> dict:
        """Metadata an agent needs to fill one form (trust, facts, fields)."""
        ...

    def fill_form(self, form_id: str, case: dict, out_dir: str) -> dict:
        """Fill the form from a case/fact object; return the result dict."""
        ...

    # plan_fill(form_id, case) -> dict is OPTIONAL; see module docstring.
