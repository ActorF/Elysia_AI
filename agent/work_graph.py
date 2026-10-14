"""Run bounded read-only Work steps through an explicit local LangGraph.

The graph routes only closed, validated tool calls. Request data is JSON;
runtime callbacks and cancellation live in a separate nonpersistent context.
It has no checkpointer, retry policy, remote tracing, or authority to execute a
write. Application-owned workflow checkpoints will control explicit recovery,
so graph replay cannot implicitly repeat a filesystem mutation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context

from work_tools.contracts import ToolCall, ToolResult, validate_tool_call, validate_tool_result

_READ_TOOLS = frozenset({"list_directory", "read_text", "search_files", "metadata"})


class _ReadState(TypedDict):
    """Keep only bounded request and outcome JSON inside graph channels."""

    call: dict[str, Any]
    result: dict[str, Any] | None


@dataclass(frozen=True)
class _ReadContext:
    """Carry a live cancellation signal without making it checkpoint data."""

    should_cancel: Callable[[], bool]


class WorkReadGraph:
    """Execute a single read through admission, routing, tool, and verification nodes."""

    def __init__(self, read_call: Callable[[ToolCall, Callable[[], bool]], ToolResult]) -> None:
        """Bind a canonical owner's read callback and compile a fixed acyclic graph."""

        if not callable(read_call):
            raise TypeError("Work read callback must be callable.")
        self._read_call = read_call
        builder = StateGraph(_ReadState, context_schema=_ReadContext)
        builder.add_node("admission", self._admission)
        builder.add_node("tool", self._tool)
        builder.add_node("verification", self._verification)
        builder.add_edge(START, "admission")
        builder.add_conditional_edges("admission", self._route, {"tool": "tool", "end": END})
        builder.add_edge("tool", "verification")
        builder.add_edge("verification", END)
        self._graph = builder.compile()

    def run(self, call: ToolCall, should_cancel: Callable[[], bool]) -> ToolResult:
        """Return one verified read outcome; reject writes before calling any adapter.

        External tracing environment variables must not export local file data.
        Cancellation is checked before admission and after a possibly slow read;
        exceptions are converted to fixed diagnostics without private paths.
        """

        if type(call) is not ToolCall or not callable(should_cancel):
            raise ValueError("Work graph input is invalid.")
        request = validate_tool_call(call.to_data())
        try:
            with tracing_context(enabled=False, parent=False):
                state = self._graph.invoke(
                    {"call": request.to_data(), "result": None},
                    config={"recursion_limit": 8, "callbacks": []},
                    context=_ReadContext(should_cancel),
                )
            result = state["result"]
            if result is None:
                raise ValueError("Work graph did not settle.")
            error = result["error"]
            return ToolResult(result["status"], result["data"], None if error is None else error["code"])
        except Exception:
            return ToolResult("failed", error_code="io_error")

    def _admission(self, state: _ReadState, runtime: Runtime[_ReadContext]) -> dict[str, Any]:
        call = validate_tool_call(state["call"])
        if call.tool_id not in _READ_TOOLS:
            return {"result": ToolResult("denied", error_code="permission_denied").to_data()}
        if runtime.context.should_cancel():
            return {"result": ToolResult("cancelled", error_code="cancelled").to_data()}
        return {}

    def _route(self, state: _ReadState) -> Literal["tool", "end"]:
        return "tool" if state["result"] is None else "end"

    def _tool(self, state: _ReadState, runtime: Runtime[_ReadContext]) -> dict[str, Any]:
        call = validate_tool_call(state["call"])
        result = self._read_call(call, runtime.context.should_cancel)
        return {"result": validate_tool_result(call.tool_id, result)}

    def _verification(self, state: _ReadState, runtime: Runtime[_ReadContext]) -> dict[str, Any]:
        if runtime.context.should_cancel():
            return {"result": ToolResult("cancelled", error_code="cancelled").to_data()}
        call = validate_tool_call(state["call"])
        result = state["result"]
        if result is None:
            raise ValueError("Work graph has no result to verify.")
        data = result["data"]
        if data is not None:
            # A valid output schema alone cannot prove that the adapter read
            # this requested target instead of a different Workspace file.
            if call.tool_id in {"list_directory", "search_files"}:
                entries = data["entries"] if call.tool_id == "list_directory" else data["matches"]
                prefix = call.path + "/" if call.path else ""
                if any(not item["path"].startswith(prefix) or item["path"] == call.path for item in entries):
                    raise ValueError("Work graph result changed directory scope.")
            elif data["path"] != call.path:
                raise ValueError("Work graph result changed its target.")
        return {}
