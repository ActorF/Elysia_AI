"""Provide bounded advisory and capability-checked nodes for the fixed Work graph.

Memory, RAG, and model output are advisory data only. No provider can replace a
tool call, select graph edges, or grant permission. Each owner gets its own node
instance; context is cached once per query without holding locks during native
work. Cancellation or stale authority clears that cache before publication.
"""

from __future__ import annotations

from collections.abc import Callable
import json
from threading import RLock
from typing import Any, Literal, TypedDict, cast

from work_tools.contracts import ToolCall, ToolProtocolError, ToolResult, validate_tool_call, validate_tool_result


MAX_WORKFLOW_QUERY_BYTES = 4096
MAX_WORKFLOW_CONTEXT_BYTES = 24 * 1024
MAX_WORKFLOW_ADVICE_BYTES = 8192
_READ_TOOLS = frozenset({"list_directory", "read_text", "search_files", "metadata"})
_UNAVAILABLE = "Local analysis is unavailable; the explicit tool action has not been analysed."
_BLANK = "\u0009\u000a\u000b\u000c\u000d\u0020\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"


class ContextFact(TypedDict):
    """Carry bounded nonauthorizing text and its logical memory or citation reference."""

    source: Literal["memory", "rag"]
    reference: str
    text: str


class ContextResult(TypedDict):
    """Distinguish a valid empty retrieval from an unavailable local provider."""

    status: Literal["ready", "unavailable"]
    items: list[ContextFact]


class AdviceResult(TypedDict):
    """Describe local advisory availability without creating an executable decision."""

    status: Literal["ready", "unavailable"]
    text: str


ContextProvider = Callable[[str, Callable[[], bool]], list[ContextFact]]
AdviceProvider = Callable[[str, list[ContextFact], list[ContextFact], Callable[[], bool]], str | None]
ReadProvider = Callable[[ToolCall, Callable[[], bool]], ToolResult]


def validate_workflow_query(value: object) -> str:
    """Require bounded plain UTF-8 query text before invoking any local context provider."""

    if type(value) is not str or not value.strip(_BLANK) or "\x00" in value or len(value) > 1024:
        raise ToolProtocolError()
    try:
        if len(value.encode("utf-8")) > MAX_WORKFLOW_QUERY_BYTES:
            raise ToolProtocolError()
    except UnicodeError:
        raise ToolProtocolError() from None
    return value


def validate_context_facts(value: object, source: Literal["memory", "rag"]) -> list[ContextFact]:
    """Accept at most eight exact provenance records within one bounded advisory frame."""

    if type(value) is not list or len(value) > 8:
        raise ToolProtocolError()
    for fact in value:
        if type(fact) is not dict or set(fact) != {"source", "reference", "text"} or fact["source"] != source:
            raise ToolProtocolError()
        for key, limit in (("reference", 256), ("text", 4096)):
            text = fact[key]
            if type(text) is not str or len(text) > limit or "\x00" in text:
                raise ToolProtocolError()
            try:
                if len(text.encode("utf-8")) > limit:
                    raise ToolProtocolError()
            except UnicodeError:
                raise ToolProtocolError() from None
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_WORKFLOW_CONTEXT_BYTES:
        raise ToolProtocolError()
    facts: list[ContextFact] = json.loads(encoded)
    return facts


def _context_result(value: object, source: Literal["memory", "rag"]) -> ContextResult:
    if type(value) is not dict or set(value) != {"status", "items"} or type(value["status"]) is not str or value["status"] not in {"ready", "unavailable"}:
        raise ToolProtocolError()
    facts = validate_context_facts(value["items"], source)
    if value["status"] == "unavailable" and facts:
        raise ToolProtocolError()
    return {"status": cast(Literal["ready", "unavailable"], value["status"]), "items": facts}


class WorkflowNodes:
    """Own one plan's isolated context cache and explicit advisory/tool node contracts."""

    def __init__(
        self, memory_call: ContextProvider | None = None,
        rag_call: ContextProvider | None = None, brain_call: AdviceProvider | None = None,
        *, authority_guard: Callable[[], None] | None = None,
    ) -> None:
        """Bind lazy canonical-owner callbacks; construction never loads a model or corpus."""

        if any(callback is not None and not callable(callback) for callback in (memory_call, rag_call, brain_call, authority_guard)):
            raise TypeError("Workflow node providers must be callable.")
        self._memory_call = memory_call
        self._rag_call = rag_call
        self._brain_call = brain_call
        self._authority_guard = authority_guard
        self._lock = RLock()
        self._query: str | None = None
        self._epoch = 0
        self._cache: dict[str, Any] = {}
        self._observation: dict[str, Any] = {
            "node": "idle", "memoryCount": 0, "ragCount": 0,
            "memoryUnavailable": True, "ragUnavailable": True, "advice": None,
        }

    def _guard(self, cancel: Callable[[], bool]) -> None:
        if not callable(cancel):
            raise ToolProtocolError()
        try:
            if cancel():
                raise ToolProtocolError("cancelled")
            if self._authority_guard is not None:
                self._authority_guard()
            if cancel():
                raise ToolProtocolError("cancelled")
        except Exception:
            with self._lock:
                self._cache.clear()
                self._epoch += 1
                self._observation.update(memoryCount=0, ragCount=0, advice=None,
                                         memoryUnavailable=True, ragUnavailable=True)
            raise

    def _cached(self, name: str, query: str, cancel: Callable[[], bool], build: Callable[[], Any]) -> Any:
        """Publish cached data only after the same owner/query epoch still wins."""

        checked = validate_workflow_query(query)
        self._guard(cancel)
        with self._lock:
            if self._query != checked:
                self._query = checked
                self._cache.clear()
                self._epoch += 1
                self._observation.update(memoryCount=0, ragCount=0, advice=None,
                                         memoryUnavailable=True, ragUnavailable=True)
            epoch = self._epoch
            cached = self._cache.get(name)
        if cached is not None:
            self._guard(cancel)
            return json.loads(json.dumps(cached))
        value = build()
        self._guard(cancel)
        with self._lock:
            if self._epoch != epoch:
                raise ToolProtocolError("conflict")
            self._cache[name] = value
        return json.loads(json.dumps(value))

    def _context(self, source: Literal["memory", "rag"], query: str, cancel: Callable[[], bool]) -> ContextResult:
        provider = self._memory_call if source == "memory" else self._rag_call

        def _build() -> ContextResult:
            if provider is None:
                return {"status": "unavailable", "items": []}
            try:
                return {"status": "ready", "items": validate_context_facts(provider(query, cancel), source)}
            except ToolProtocolError as error:
                if error.code in {"cancelled", "conflict", "work_mode_required"}:
                    raise
                return {"status": "unavailable", "items": []}
            except Exception:
                self._guard(cancel)
                return {"status": "unavailable", "items": []}

        result: ContextResult = self._cached(source, query, cancel, _build)
        return result

    def memory(self, query: str, cancel: Callable[[], bool]) -> ContextResult:
        """Retrieve one bounded canonical-owner memory view or explicit unavailable facts."""

        self.observe_node("memory")
        result = self._context("memory", query, cancel)
        with self._lock:
            self._observation.update(memoryCount=len(result["items"]), memoryUnavailable=result["status"] == "unavailable")
        return result

    def rag(self, query: str, cancel: Callable[[], bool]) -> ContextResult:
        """Retrieve explicitly enabled Project evidence once, never another Chat's corpus."""

        self.observe_node("rag")
        result = self._context("rag", query, cancel)
        with self._lock:
            self._observation.update(ragCount=len(result["items"]), ragUnavailable=result["status"] == "unavailable")
        return result

    def brain(self, query: str, memory: ContextResult, rag: ContextResult, cancel: Callable[[], bool]) -> AdviceResult:
        """Produce bounded local advice; model text cannot change calls, edges, or permissions."""

        self.observe_node("brain")
        memories = _context_result(memory, "memory")["items"]
        evidence = _context_result(rag, "rag")["items"]

        def _build() -> AdviceResult:
            if self._brain_call is None:
                return {"status": "unavailable", "text": _UNAVAILABLE}
            try:
                advice = self._brain_call(query, memories, evidence, cancel)
                if type(advice) is not str or len(advice) > MAX_WORKFLOW_ADVICE_BYTES or not advice.strip(_BLANK) or "\x00" in advice or len(advice.encode("utf-8")) > MAX_WORKFLOW_ADVICE_BYTES:
                    return {"status": "unavailable", "text": _UNAVAILABLE}
                return {"status": "ready", "text": advice}
            except ToolProtocolError as error:
                if error.code in {"cancelled", "conflict", "work_mode_required"}:
                    raise
                return {"status": "unavailable", "text": _UNAVAILABLE}
            except Exception:
                self._guard(cancel)
                return {"status": "unavailable", "text": _UNAVAILABLE}

        result: AdviceResult = self._cached("brain", query, cancel, _build)
        with self._lock:
            self._observation["advice"] = json.loads(json.dumps(result))
        return result

    def approval(self, call: ToolCall) -> bool:
        """Derive the write-approval route solely from the validated closed tool identifier."""

        if type(call) is not ToolCall:
            raise ToolProtocolError()
        self.observe_node("approval")
        return validate_tool_call(call.to_data()).tool_id not in _READ_TOOLS

    def tool(self, call: ToolCall, cancel: Callable[[], bool], read_call: ReadProvider) -> ToolResult:
        """Execute exactly one supplied read; every write returns an approval-required fact."""

        self._guard(cancel)
        if self.approval(call):
            return ToolResult("denied", error_code="permission_required")
        self.observe_node("tool")
        result = read_call(validate_tool_call(call.to_data()), cancel)
        self._guard(cancel)
        self.observe_node("verification")
        validate_tool_result(call.tool_id, result)
        return result

    def verification(self, call: ToolCall, result: ToolResult, cancel: Callable[[], bool]) -> ToolResult:
        """Check typed outcome scope against the original call before publishing any read body."""

        self._guard(cancel)
        checked = validate_tool_call(call.to_data())
        data = validate_tool_result(checked.tool_id, result)["data"]
        if data is not None:
            if checked.tool_id in {"list_directory", "search_files"}:
                entries = data["entries"] if checked.tool_id == "list_directory" else data["matches"]
                prefix = checked.path + "/" if checked.path else ""
                if any(not entry["path"].startswith(prefix) or entry["path"] == checked.path for entry in entries):
                    raise ToolProtocolError("verification_failed")
            elif data["path"] != checked.path:
                raise ToolProtocolError("verification_failed")
        self._guard(cancel)
        return result

    def observe_node(self, node: str) -> None:
        """Record only a fixed graph-node fact; model output cannot name the current node."""

        if type(node) is not str or node not in {"idle", "admission", "memory", "rag", "brain", "approval", "tool", "verification", "end"}:
            raise ToolProtocolError()
        with self._lock:
            self._observation["node"] = node

    def observation(self) -> dict[str, Any]:
        """Return bounded progress/advice metadata after checking the captured owner is still current."""

        self._guard(lambda: False)
        with self._lock:
            snapshot: dict[str, Any] = json.loads(json.dumps(self._observation))
            return snapshot
