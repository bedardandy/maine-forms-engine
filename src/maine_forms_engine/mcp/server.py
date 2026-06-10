"""Standardized MCP server scaffold over a repo-supplied FormsBackend.

Usage in a consuming repo (its ``tools/agent_server.py`` shrinks to):

    from maine_forms_engine.mcp.server import build_server, main
    from my_repo_backend import Backend          # implements FormsBackend

    if __name__ == "__main__":
        raise SystemExit(main(Backend()))

Tools exposed (stdio FastMCP), one error shape everywhere
(``{"ok": False, "error": str, "error_type": str}``):

    find_forms(query, top_k=5) -> dict   {"ok": True, "results": [...]} or
                                         {"ok": True, **backend_dict}
    get_form(form_id)          -> dict   {"ok": True, **metadata}
    plan_fill(form_id, case)   -> dict   {"ok": True, **plan} (if supported)
    fill_form(form_id, case, out_dir) -> dict  backend result (carries "ok")

``mcp`` (pip install maine-forms-engine[mcp]) is imported lazily so the
module documents itself — and the tool functions stay unit-testable — without
the dependency installed.
"""
from __future__ import annotations

import sys

from .adapter import FormsBackend, UnknownFormError


def _error(e: Exception) -> dict:
    return {"ok": False, "error": str(e) or repr(e),
            "error_type": type(e).__name__}


def make_tool_functions(backend: FormsBackend) -> dict:
    """The four standardized tool callables, keyed by tool name.

    Separated from FastMCP registration so consumers (and tests) can call the
    tools directly without an MCP transport.
    """

    def find_forms(query: str, top_k: int = 5) -> dict:
        """Route a plain-language situation to candidate forms."""
        try:
            res = backend.find_forms(query, top_k)
        except Exception as e:  # noqa: BLE001 — one error shape, never raise
            return _error(e)
        if isinstance(res, dict):
            return {"ok": True, **res}
        return {"ok": True, "results": list(res)}

    def get_form(form_id: str) -> dict:
        """What an agent needs to fill one form (metadata, trust, fields)."""
        try:
            return {"ok": True, **backend.get_form(form_id)}
        except (UnknownFormError, KeyError):
            return {"ok": False, "error": f"unknown form {form_id!r}",
                    "error_type": "UnknownFormError"}
        except Exception as e:  # noqa: BLE001
            return _error(e)

    def plan_fill(form_id: str, case: dict) -> dict:
        """Resolve a case object into a coverage plan (backends that support it)."""
        fn = getattr(backend, "plan_fill", None)
        if fn is None:
            return {"ok": False,
                    "error": f"backend {backend.name!r} has no plan step; "
                             "call fill_form (its result carries the fill "
                             "diagnostics)",
                    "error_type": "NotSupported"}
        try:
            return {"ok": True, **fn(form_id, case)}
        except (UnknownFormError, KeyError):
            return {"ok": False, "error": f"unknown form {form_id!r}",
                    "error_type": "UnknownFormError"}
        except Exception as e:  # noqa: BLE001
            return _error(e)

    def fill_form(form_id: str, case: dict, out_dir: str = "/tmp/forms_fill") -> dict:
        """Fill the form from a case/fact object; returns the backend's result
        dict (always carries ``ok``; on failure the standard error shape)."""
        try:
            res = backend.fill_form(form_id, case, out_dir)
        except (UnknownFormError, KeyError):
            return {"ok": False, "error": f"unknown form {form_id!r}",
                    "error_type": "UnknownFormError"}
        except Exception as e:  # noqa: BLE001
            return _error(e)
        if "ok" not in res:
            res = {"ok": True, **res}
        return res

    return {"find_forms": find_forms, "get_form": get_form,
            "plan_fill": plan_fill, "fill_form": fill_form}


def build_server(backend: FormsBackend):
    """Build a FastMCP stdio server exposing the standardized tools."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(backend.name)
    for fn in make_tool_functions(backend).values():
        mcp.tool()(fn)
    return mcp


def main(backend: FormsBackend) -> int:
    try:
        server = build_server(backend)
    except ImportError:
        print("mcp not installed: pip install 'maine-forms-engine[mcp]'",
              file=sys.stderr)
        return 1
    server.run()  # stdio transport
    return 0
