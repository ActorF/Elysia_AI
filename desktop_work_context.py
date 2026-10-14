"""Bind Work graph advice to canonical Chat, local Memory, and opted-in Project evidence.

Factories are lazy: creating a plan does not initialize a model or knowledge
runtime. The Backend supplies a leased answer callback only when Project
knowledge was explicitly selected for this plan. Advice never includes the
selected Workspace's tool results and cannot authorize additional operations.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.vision_context import validate_vision_context
from agent.workflow_nodes import ContextFact, WorkflowNodes, validate_context_facts
from chats import ChatSession
from core.brain import Brain, ProjectSourceAnswerer
from project_sources import PROJECT_SOURCE_ANSWER_SCHEMA_VERSION, ProjectSourceAnswer
from work_tools.contracts import ToolProtocolError


def make_workflow_nodes(
    chat_provider: Callable[[], ChatSession], brain: Brain,
    rag_answerer: ProjectSourceAnswerer | None = None, *,
    use_project_knowledge: bool = False, authority_guard: Callable[[], None] | None = None,
    vision_context: dict[str, Any] | None = None,
) -> WorkflowNodes:
    """Capture one Work owner and return lazy providers that revalidate before/after use.

    Project membership is not an opt-in. ``use_project_knowledge`` must be an
    explicit Boolean for this plan; false performs no corpus lookup even if a
    previous Chat turn used Project evidence. The optional guard additionally
    binds the selected Workspace and manager lifecycle to this exact owner.
    """

    if not callable(chat_provider) or type(use_project_knowledge) is not bool or (rag_answerer is not None and not callable(rag_answerer)):
        raise TypeError("Workflow context providers are invalid.")
    selected = chat_provider()
    chat_id, project_id = selected.chat_id, selected.project_id
    # Image evidence is an explicitly reviewed, text-only snapshot. Keeping it
    # in this provider closure excludes pixels and OCR text from checkpoints.
    reviewed_vision = None if vision_context is None else validate_vision_context(vision_context)

    def _guard() -> None:
        if authority_guard is not None:
            authority_guard()
        current = chat_provider()
        if current.chat_id != chat_id or current.project_id != project_id or current.is_archived:
            raise ToolProtocolError("conflict")
        if current.mode != "work":
            raise ToolProtocolError("work_mode_required")
        canonical = brain.get_chat(chat_id)
        if canonical != current:
            raise ToolProtocolError("conflict")
        if project_id is not None and brain.get_project(project_id).is_archived:
            raise ToolProtocolError("conflict")

    def _memory(query: str, cancel: Callable[[], bool]) -> list[ContextFact]:
        _guard()
        facts = brain.workflow_memory_context(chat_id, project_id, query, cancel)
        _guard()
        return facts

    def _rag(query: str, cancel: Callable[[], bool]) -> list[ContextFact]:
        _guard()
        if not use_project_knowledge or project_id is None:
            return []
        if rag_answerer is None:
            raise RuntimeError("Local Project evidence is unavailable.")
        answer = rag_answerer(chat_id, query, should_cancel=cancel)
        _guard()
        if (type(answer) is not ProjectSourceAnswer or answer.schema_version != PROJECT_SOURCE_ANSWER_SCHEMA_VERSION
            or answer.chat_id != chat_id or answer.project_id != project_id):
            raise ToolProtocolError("conflict")
        facts: list[ContextFact] = []
        for statement in answer.answer.statements[:8]:
            text = statement.text[:2048].encode("utf-8")[:2048].decode("utf-8", errors="ignore")
            reference = statement.citation_ids[0] if statement.citation_ids else statement.statement_id
            facts.append({"source": "rag", "reference": reference, "text": text})
        return validate_context_facts(facts, "rag")

    def _brain(query: str, memories: list[ContextFact], evidence: list[ContextFact], cancel: Callable[[], bool]) -> str | None:
        _guard()
        advice = brain.workflow_advice(chat_id, project_id, query, memories, evidence, cancel,
                                      vision_context=reviewed_vision)
        _guard()
        return advice

    return WorkflowNodes(_memory, _rag, _brain, authority_guard=_guard)
