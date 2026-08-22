import json
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from chats import (
    CHAT_SESSION_SCHEMA_VERSION,
    ChatId,
    ChatMessage,
    ChatMessageId,
    ChatModelSettings,
    ChatSession,
    ChatSummary,
    ProjectId,
)
from core import Brain
from memory import (
    LongTermMemoryRecord,
    Memory,
    MemoryRetriever,
    MemoryScope,
    Profile,
    RetrievedMemory,
)

BASE_TIME = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _profile() -> Profile:
    return {
        "schema_version": 1,
        "user_name": "Ying",
        "assistant_name": "Elysia",
        "languages": ["Chinese", "English"],
        "project": "Elysia AI",
        "launch_count": 1,
    }


def _record(
    key: str,
    value: str,
    *,
    scope: str,
    scope_id: str | None,
) -> LongTermMemoryRecord:
    return {
        "key": key,
        "value": value,
        "source_type": "user_explicit",
        "source_text": f"Remember {value}.",
        "created_at": "2026-08-22 12:00:00",
        "scope": cast(MemoryScope, scope),
        "scope_id": scope_id,
    }


def _chat_session(
    *,
    chat_id: str = "chat_active",
    project_id: str | None = "project_alpha",
    summary_fact: str = "Active Chat uses stable message IDs.",
) -> ChatSession:
    message = ChatMessage(
        message_id=ChatMessageId("message_active"),
        role="user",
        content="Previous active Chat question",
        created_at=BASE_TIME,
    )
    summary = ChatSummary(
        facts=(summary_fact,),
        decisions=("Use repository pattern in this Chat.",),
        action_items=(),
        unresolved_questions=(),
        source_message_ids=(message.message_id,),
        updated_at=BASE_TIME,
    )
    return ChatSession(
        schema_version=CHAT_SESSION_SCHEMA_VERSION,
        chat_id=ChatId(chat_id),
        title="Scoped Chat",
        mode="chat",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
        messages=(message,),
        summary=summary,
        project_id=(
            ProjectId(project_id)
            if project_id is not None
            else None
        ),
        model_settings=ChatModelSettings(
            model_name="qwen3.5:9b"
        ),
    )


def _retrieved_json(system_prompt: str) -> list[RetrievedMemory]:
    memory_json = system_prompt.split(
        "RETRIEVED_MEMORY_JSON:\n",
        1,
    )[1].split(
        "\nUSER_PROFILE_JSON:\n",
        1,
    )[0]
    decoded: object = json.loads(memory_json)
    assert isinstance(decoded, list)
    return cast(list[RetrievedMemory], decoded)


def test_scoped_retrieval_excludes_other_project_and_chat() -> None:
    records = [
        _record(
            "preferred_language",
            "Chinese",
            scope="global",
            scope_id=None,
        ),
        _record(
            "project_database",
            "Alpha SQLite",
            scope="project",
            scope_id="project_alpha",
        ),
        _record(
            "project_database",
            "Beta PostgreSQL",
            scope="project",
            scope_id="project_beta",
        ),
        _record(
            "chat_pattern",
            "Active repository pattern",
            scope="chat",
            scope_id="chat_active",
        ),
        _record(
            "chat_pattern",
            "Other singleton pattern",
            scope="chat",
            scope_id="chat_other",
        ),
    ]

    results = MemoryRetriever(10).retrieve_for_chat(
        "Chinese database repository pattern",
        _profile(),
        _chat_session(),
        records,
    )

    values = {result["value"] for result in results}
    assert "Chinese" in values
    assert "Alpha SQLite" in values
    assert "Active repository pattern" in values
    assert "Beta PostgreSQL" not in values
    assert "Other singleton pattern" not in values


def test_more_specific_scope_wins_same_key_conflict() -> None:
    records = [
        _record(
            "storage_backend",
            "Global storage backend",
            scope="global",
            scope_id=None,
        ),
        _record(
            "storage_backend",
            "Project storage backend",
            scope="project",
            scope_id="project_alpha",
        ),
        _record(
            "storage_backend",
            "Chat storage backend",
            scope="chat",
            scope_id="chat_active",
        ),
    ]

    results = MemoryRetriever(10).retrieve_for_chat(
        "storage backend",
        _profile(),
        _chat_session(),
        records,
    )
    long_term_results = [
        result
        for result in results
        if result["source"] == "long_term_memory"
    ]

    assert [
        result["value"] for result in long_term_results
    ] == ["Chat storage backend"]
    assert long_term_results[0]["scope"] == "chat"
    assert long_term_results[0]["scope_id"] == "chat_active"


def test_chat_summary_is_labeled_with_owning_chat_scope() -> None:
    chat = _chat_session()

    results = MemoryRetriever(10).retrieve_for_chat(
        "stable message IDs",
        _profile(),
        chat,
        [],
    )
    summary_results = [
        result
        for result in results
        if result["source"] == "chat_summary"
    ]

    assert summary_results
    assert all(
        result["scope"] == "chat"
        and result["scope_id"] == chat.chat_id
        for result in summary_results
    )


def test_chat_summary_does_not_cross_chat_boundary() -> None:
    chat_a = _chat_session(
        chat_id="chat_a",
        summary_fact="Alpha private summary fact.",
    )
    chat_b = _chat_session(
        chat_id="chat_b",
        summary_fact="Beta active summary fact.",
    )
    retriever = MemoryRetriever(10)

    results_a = retriever.retrieve_for_chat(
        "Alpha Beta summary fact",
        _profile(),
        chat_a,
        [],
    )
    results_b = retriever.retrieve_for_chat(
        "Alpha Beta summary fact",
        _profile(),
        chat_b,
        [],
    )

    assert "Alpha private summary fact." in {
        result["value"] for result in results_a
    }
    assert "Alpha private summary fact." not in {
        result["value"] for result in results_b
    }
    assert "Beta active summary fact." in {
        result["value"] for result in results_b
    }


def test_legacy_retrieval_never_reads_project_or_chat_records() -> None:
    results = MemoryRetriever(10).retrieve(
        "storage backend",
        _profile(),
        None,
        [
            _record(
                "storage_backend",
                "Global storage backend",
                scope="global",
                scope_id=None,
            ),
            _record(
                "storage_backend",
                "Private project backend",
                scope="project",
                scope_id="project_alpha",
            ),
        ],
    )

    assert all(
        result["scope"] == "global"
        for result in results
    )
    assert all(
        result["value"] != "Private project backend"
        for result in results
    )


def test_brain_builds_prompt_from_active_scopes_only(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    memory.save_message("Ying", "Legacy conversation must not leak")
    memory.save_long_term_memory(
        "preferred_language",
        "Chinese",
        "user_explicit",
        "Remember Chinese.",
    )
    memory.save_long_term_memory(
        "project_database",
        "Alpha SQLite",
        "user_explicit",
        "Remember Alpha database.",
        scope="project",
        scope_id="project_alpha",
    )
    memory.save_long_term_memory(
        "project_database",
        "Beta PostgreSQL",
        "user_explicit",
        "Remember Beta database.",
        scope="project",
        scope_id="project_beta",
    )
    chat = _chat_session()
    brain = Brain(
        "fake-model",
        memory,
        memory_retriever=MemoryRetriever(10),
    )

    messages = brain._build_chat_messages(
        memory.load_profile(),
        "Chinese database repository decision",
        chat_session=chat,
    )
    retrieved = _retrieved_json(messages[0]["content"])

    assert all(
        result["scope_id"] != "project_beta"
        for result in retrieved
    )
    assert any(
        result["value"] == "Alpha SQLite"
        for result in retrieved
    )
    assert any(
        result["source"] == "chat_summary"
        for result in retrieved
    )
    assert messages[1:] == [
        {
            "role": "user",
            "content": "Previous active Chat question",
        },
        {
            "role": "user",
            "content": "Chinese database repository decision",
        },
    ]
