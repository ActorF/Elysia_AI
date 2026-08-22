from typing import cast

import pytest

from chats import ChatId, ProjectId
from memory import (
    MemoryScope,
    MemoryScopeContext,
    MemoryScopeRef,
    validate_memory_scope,
)


def test_context_orders_readable_scopes_by_specificity() -> None:
    context = MemoryScopeContext(
        chat_id=ChatId("chat_active"),
        project_id=ProjectId("project_active"),
    )

    assert context.readable_scopes() == (
        MemoryScopeRef("chat", "chat_active"),
        MemoryScopeRef("project", "project_active"),
        MemoryScopeRef("global", None),
    )


def test_unassigned_chat_reads_chat_and_global_only() -> None:
    context = MemoryScopeContext(
        chat_id=ChatId("chat_unassigned"),
        project_id=None,
    )

    assert context.readable_scopes() == (
        MemoryScopeRef("chat", "chat_unassigned"),
        MemoryScopeRef("global", None),
    )


@pytest.mark.parametrize(
    ("scope", "scope_id"),
    [
        ("global", "project_wrong"),
        ("project", None),
        ("project", "chat_wrong"),
        ("chat", None),
        ("chat", "project_wrong"),
    ],
)
def test_scope_ref_rejects_invalid_scope_id_pairs(
    scope: MemoryScope,
    scope_id: str | None,
) -> None:
    with pytest.raises(ValueError):
        MemoryScopeRef(scope, scope_id)


def test_validate_memory_scope_rejects_unknown_external_value() -> None:
    with pytest.raises(
        ValueError,
        match=r"scope must be global, project, or chat",
    ):
        validate_memory_scope("workspace", None)


def test_context_rejects_invalid_identifiers() -> None:
    with pytest.raises(ValueError):
        MemoryScopeContext(
            chat_id=cast(ChatId, "project_wrong"),
            project_id=None,
        )
