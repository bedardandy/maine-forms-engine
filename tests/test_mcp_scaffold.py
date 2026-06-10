"""The standardized MCP scaffold: one tool surface, one error shape, backend
adapter contract (find_forms list-vs-dict, optional plan_fill, unknown forms)."""
import pytest

from maine_forms_engine.mcp import (
    UnknownFormError, build_server, make_tool_functions)


class ListBackend:
    """tax/corp-style backend: find_forms returns a ranked list."""
    name = "test-forms"

    def find_forms(self, query, top_k):
        return [{"form_id": "T-1", "score": 2}][:top_k]

    def get_form(self, form_id):
        if form_id != "T-1":
            raise UnknownFormError(form_id)
        return {"form_id": "T-1", "title": "Test", "n_fields": 4}

    def fill_form(self, form_id, case, out_dir):
        if form_id != "T-1":
            raise UnknownFormError(form_id)
        if not case:
            raise ValueError("empty case")
        return {"ok": True, "out_pdf": f"{out_dir}/T-1.filled.pdf",
                "fields_written": 3}


class DictBackend(ListBackend):
    """court-style backend: find_forms returns workflows + forms, and a plan."""
    name = "test-forms-dict"

    def find_forms(self, query, top_k):
        return {"workflows": [{"name": "wf"}], "forms": [{"form_id": "T-1"}]}

    def plan_fill(self, form_id, case):
        return {"resolved": 3, "unresolved": []}


def test_find_forms_list_becomes_results():
    t = make_tool_functions(ListBackend())
    res = t["find_forms"]("test situation")
    assert res == {"ok": True, "results": [{"form_id": "T-1", "score": 2}]}


def test_find_forms_dict_is_merged():
    t = make_tool_functions(DictBackend())
    res = t["find_forms"]("test situation", top_k=3)
    assert res["ok"] and res["workflows"] and res["forms"]


def test_get_form_ok_and_unknown():
    t = make_tool_functions(ListBackend())
    assert t["get_form"]("T-1")["title"] == "Test"
    res = t["get_form"]("NOPE")
    assert res == {"ok": False, "error": "unknown form 'NOPE'",
                   "error_type": "UnknownFormError"}


def test_plan_fill_optional():
    res = make_tool_functions(ListBackend())["plan_fill"]("T-1", {})
    assert res["ok"] is False and res["error_type"] == "NotSupported"
    res = make_tool_functions(DictBackend())["plan_fill"]("T-1", {})
    assert res == {"ok": True, "resolved": 3, "unresolved": []}


def test_fill_form_passthrough_and_error_shape():
    t = make_tool_functions(ListBackend())
    ok = t["fill_form"]("T-1", {"matter": {}}, "/tmp/x")
    assert ok["ok"] and ok["fields_written"] == 3
    # backend exception -> the ONE error shape, never a raised exception
    err = t["fill_form"]("T-1", {}, "/tmp/x")
    assert err == {"ok": False, "error": "empty case",
                   "error_type": "ValueError"}
    unk = t["fill_form"]("NOPE", {"a": 1}, "/tmp/x")
    assert unk["error_type"] == "UnknownFormError"


def test_backend_exceptions_never_escape_find_forms():
    class Boom(ListBackend):
        def find_forms(self, query, top_k):
            raise RuntimeError("router down")
    res = make_tool_functions(Boom())["find_forms"]("x")
    assert res == {"ok": False, "error": "router down",
                   "error_type": "RuntimeError"}


def test_build_server_registers_all_tools():
    pytest.importorskip("mcp")
    server = build_server(ListBackend())
    assert server.name == "test-forms"
    import anyio
    tools = anyio.run(server.list_tools)
    assert {t.name for t in tools} == {"find_forms", "get_form",
                                       "plan_fill", "fill_form"}
