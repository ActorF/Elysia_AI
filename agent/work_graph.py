"""Coordinate bounded advisory, approval routing, and read tools in a local LangGraph.

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

from agent.workflow_nodes import AdviceResult, ContextResult, WorkflowNodes, validate_workflow_query
from work_tools.contracts import ToolCall, ToolProtocolError, ToolResult, validate_tool_call

_READ_TOOLS = frozenset({"list_directory", "read_text", "search_files", "metadata"})


class _ReadState(TypedDict):
    """Keep only bounded request and outcome JSON inside graph channels."""

    call: dict[str, Any]
    result: dict[str, Any] | None
    query: str
    memory: ContextResult
    rag: ContextResult
    advice: AdviceResult | None
    needs_approval: bool


@dataclass(frozen=True)
class _ReadContext:
    """Carry a live cancellation signal without making it checkpoint data."""

    should_cancel: Callable[[], bool]
    prepare_write: bool = False


class WorkReadGraph:
    """Run a fixed Memory/RAG/Brain/Approval/Tool/Verification pipeline without write authority."""

    def __init__(self, read_call: Callable[[ToolCall, Callable[[], bool]], ToolResult], *, nodes: WorkflowNodes | None = None) -> None:
        """Bind a canonical owner's lazy advisory providers and compile a fixed acyclic graph."""

        if not callable(read_call):
            raise TypeError("Work read callback must be callable.")
        self._read_call = read_call
        self._nodes = WorkflowNodes() if nodes is None else nodes
        builder = StateGraph(_ReadState, context_schema=_ReadContext)
        builder.add_node("admission", self._admission)
        builder.add_node("memory", self._memory)
        builder.add_node("rag", self._rag)
        builder.add_node("brain", self._brain)
        builder.add_node("approval", self._approval)
        builder.add_node("tool", self._tool)
        builder.add_node("verification", self._verification)
        builder.add_edge(START, "admission")
        builder.add_conditional_edges("admission", self._admission_route, {"memory": "memory", "end": END})
        builder.add_edge("memory", "rag")
        builder.add_edge("rag", "brain")
        builder.add_edge("brain", "approval")
        builder.add_conditional_edges("approval", self._route, {"tool": "tool", "end": END})
        builder.add_edge("tool", "verification")
        builder.add_edge("verification", END)
        self._graph = builder.compile()

    def run(self, call: ToolCall, should_cancel: Callable[[], bool], *, query: str = "Workspace task") -> ToolResult:
        """Return one verified read outcome; reject writes before calling any adapter.

        External tracing environment variables must not export local file data.
        Cancellation is checked before admission and after a possibly slow read;
        exceptions are converted to fixed diagnostics without private paths.
        """

        return self._invoke(call, should_cancel, query=query, prepare_write=False)

    def prepare(self, call: ToolCall, should_cancel: Callable[[], bool], *, query: str = "Workspace task") -> ToolResult:
        """Run advice and the approval router for a write without previewing or executing it.

        A permission_required outcome is a route fact only. The runner must
        separately obtain its existing owner-bound preview and human decision.
        """

        if type(call) is not ToolCall or call.tool_id in _READ_TOOLS:
            raise ValueError("Work approval routing requires an explicit write.")
        return self._invoke(call, should_cancel, query=query, prepare_write=True)

    def observation(self) -> dict[str, Any]:
        """Return bounded owner-checked current-node/context/advice metadata for the UI."""

        return self._nodes.observation()

    def _invoke(self, call: ToolCall, should_cancel: Callable[[], bool], *, query: str, prepare_write: bool) -> ToolResult:
        if type(call) is not ToolCall or not callable(should_cancel):
            raise ValueError("Work graph input is invalid.")
        request = validate_tool_call(call.to_data())
        checked_query = validate_workflow_query(query)
        try:
            with tracing_context(enabled=False, parent=False):
                state = self._graph.invoke(
                    {"call": request.to_data(), "result": None, "query": checked_query,
                     "memory": {"status": "unavailable", "items": []},
                     "rag": {"status": "unavailable", "items": []},
                     "advice": None, "needs_approval": False},
                    config={"recursion_limit": 12, "callbacks": []},
                    context=_ReadContext(should_cancel, prepare_write),
                )
            result = state["result"]
            if result is None:
                raise ValueError("Work graph did not settle.")
            if should_cancel():
                return ToolResult("cancelled", error_code="cancelled")
            self._nodes.observe_node("end")
            error = result["error"]
            return ToolResult(result["status"], result["data"], None if error is None else error["code"])
        except ToolProtocolError as error:
            status: Literal["cancelled", "denied", "failed"] = "cancelled" if error.code == "cancelled" else "denied" if error.code in {"conflict", "work_mode_required"} else "failed"
            return ToolResult(status, error_code=error.code)
        except Exception:
            if should_cancel():
                return ToolResult("cancelled", error_code="cancelled")
            return ToolResult("failed", error_code="io_error")

    def _admission(self, state: _ReadState, runtime: Runtime[_ReadContext]) -> dict[str, Any]:
        call = validate_tool_call(state["call"])
        self._nodes.observe_node("admission")
        if call.tool_id not in _READ_TOOLS and not runtime.context.prepare_write:
            return {"result": ToolResult("denied", error_code="permission_denied").to_data()}
        if runtime.context.should_cancel():
            return {"result": ToolResult("cancelled", error_code="cancelled").to_data()}
        return {}

    def _admission_route(self, state: _ReadState) -> Literal["memory", "end"]:
        return "memory" if state["result"] is None else "end"

    def _memory(self, state: _ReadState, runtime: Runtime[_ReadContext]) -> dict[str, Any]:
        return {"memory": self._nodes.memory(state["query"], runtime.context.should_cancel)}

    def _rag(self, state: _ReadState, runtime: Runtime[_ReadContext]) -> dict[str, Any]:
        return {"rag": self._nodes.rag(state["query"], runtime.context.should_cancel)}

    def _brain(self, state: _ReadState, runtime: Runtime[_ReadContext]) -> dict[str, Any]:
        return {"advice": self._nodes.brain(state["query"], state["memory"], state["rag"], runtime.context.should_cancel)}

    def _approval(self, state: _ReadState, runtime: Runtime[_ReadContext]) -> dict[str, Any]:
        if runtime.context.should_cancel():
            return {"result": ToolResult("cancelled", error_code="cancelled").to_data()}
        required = self._nodes.approval(validate_tool_call(state["call"]))
        return {"needs_approval": required, "result": ToolResult("denied", error_code="permission_required").to_data() if required else None}

    def _route(self, state: _ReadState) -> Literal["tool", "end"]:
        return "tool" if state["result"] is None else "end"

    def _tool(self, state: _ReadState, runtime: Runtime[_ReadContext]) -> dict[str, Any]:
        call = validate_tool_call(state["call"])
        result = self._nodes.tool(call, runtime.context.should_cancel, self._read_call)
        return {"result": result.to_data()}

    def _verification(self, state: _ReadState, runtime: Runtime[_ReadContext]) -> dict[str, Any]:
        if runtime.context.should_cancel():
            return {"result": ToolResult("cancelled", error_code="cancelled").to_data()}
        call = validate_tool_call(state["call"])
        result = state["result"]
        if result is None:
            raise ValueError("Work graph has no result to verify.")
        error = result["error"]
        outcome = ToolResult(result["status"], result["data"], None if error is None else error["code"])
        try:
            self._nodes.verification(call, outcome, runtime.context.should_cancel)
        except ToolProtocolError as failure:
            if failure.code == "verification_failed":
                raise ValueError("Work graph result changed its target.") from None
            raise
        return {}
