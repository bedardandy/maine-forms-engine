"""Standardized MCP scaffold: find_forms / get_form / plan_fill / fill_form
with one error shape; the consuming repo supplies a FormsBackend adapter."""

from .adapter import FormsBackend, UnknownFormError  # noqa: F401
from .server import build_server, main, make_tool_functions  # noqa: F401
