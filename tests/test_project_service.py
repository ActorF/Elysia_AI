from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest

from chats import ChatNotFoundError, JsonChatRepository
from projects import (
    ChatProjectConflictError,
    JsonProjectRepository,
    ProjectArchivedError,
    ProjectChatService,
    ProjectDeletionPolicy,
    ProjectHasChatsError,
    ProjectId,
    ProjectNotFoundError,
    ProjectRelationshipError,
    ProjectStorageError,
    validate_deletion_policy,
)

BASE_TIME = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def _repositories(
    tmp_path: Path,
) -> tuple[
    JsonProjectRepository,
    JsonChatRepository,
    ProjectChatService,
]:
    project_repository = JsonProjectRepository(
        tmp_path / "data" / "projects",
        clock=lambda: BASE_TIME,
    )
    chat_repository = JsonChatRepository(
        tmp_path / "data" / "chats",
        clock=lambda: BASE_TIME,
    )
    service = ProjectChatService(
        project_repository,
        chat_repository,
    )
    return project_repository, chat_repository, service


def test_add_and_remove_chat_updates_single_relationship_source(
    tmp_path: Path,
) -> None:
    projects, chats, service = _repositories(tmp_path)
    project = projects.create_project(name="Elysia")
    chat = chats.create_chat(
        title="Architecture",
        mode="work",
        model_name="qwen3.5:9b",
    )

    assigned = service.add_chat(project.project_id, chat.chat_id)

    assert assigned.project_id == project.project_id
    assert chats.get_chat(chat.chat_id).project_id == project.project_id
    assert service.list_project_chats(project.project_id)[0].chat_id == (
        chat.chat_id
    )

    removed = service.remove_chat(project.project_id, chat.chat_id)

    assert removed.project_id is None
    assert chats.get_chat(chat.chat_id).project_id is None
    assert service.list_project_chats(project.project_id) == ()


def test_add_rejects_chat_owned_by_another_project(
    tmp_path: Path,
) -> None:
    projects, chats, service = _repositories(tmp_path)
    first = projects.create_project(name="First")
    second = projects.create_project(name="Second")
    chat = chats.create_chat(
        title="Owned",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    service.add_chat(first.project_id, chat.chat_id)

    with pytest.raises(
        ChatProjectConflictError,
        match=r"use transfer_chat",
    ):
        service.add_chat(second.project_id, chat.chat_id)


def test_transfer_moves_chat_between_project_scopes(
    tmp_path: Path,
) -> None:
    projects, chats, service = _repositories(tmp_path)
    first = projects.create_project(name="First")
    second = projects.create_project(name="Second")
    chat = chats.create_chat(
        title="Move me",
        mode="work",
        model_name="qwen3.5:9b",
    )
    service.add_chat(first.project_id, chat.chat_id)

    transferred = service.transfer_chat(
        chat.chat_id,
        second.project_id,
    )

    assert transferred.project_id == second.project_id
    assert service.list_project_chats(first.project_id) == ()
    assert service.list_project_chats(second.project_id)[0].chat_id == (
        chat.chat_id
    )


def test_transfer_rejects_unassigned_chat(tmp_path: Path) -> None:
    projects, chats, service = _repositories(tmp_path)
    project = projects.create_project(name="Target")
    chat = chats.create_chat(
        title="Unassigned",
        mode="chat",
        model_name="qwen3.5:9b",
    )

    with pytest.raises(ChatProjectConflictError, match=r"use add_chat"):
        service.transfer_chat(chat.chat_id, project.project_id)


def test_remove_rejects_wrong_project_scope(tmp_path: Path) -> None:
    projects, chats, service = _repositories(tmp_path)
    first = projects.create_project(name="First")
    second = projects.create_project(name="Second")
    chat = chats.create_chat(
        title="Scoped",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    service.add_chat(first.project_id, chat.chat_id)

    with pytest.raises(ChatProjectConflictError, match=r"does not belong"):
        service.remove_chat(second.project_id, chat.chat_id)


def test_project_chat_lists_do_not_leak_other_scopes(
    tmp_path: Path,
) -> None:
    projects, chats, service = _repositories(tmp_path)
    first = projects.create_project(name="First")
    second = projects.create_project(name="Second")
    first_chat = chats.create_chat(
        title="First chat",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    second_chat = chats.create_chat(
        title="Second chat",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    service.add_chat(first.project_id, first_chat.chat_id)
    service.add_chat(second.project_id, second_chat.chat_id)
    chats.archive_chat(first_chat.chat_id)

    assert service.list_project_chats(first.project_id) == ()
    assert [
        metadata.chat_id
        for metadata in service.list_project_chats(
            first.project_id,
            include_archived=True,
        )
    ] == [first_chat.chat_id]
    assert [
        metadata.chat_id
        for metadata in service.list_project_chats(second.project_id)
    ] == [second_chat.chat_id]


def test_archived_project_cannot_accept_or_receive_chat(
    tmp_path: Path,
) -> None:
    projects, chats, service = _repositories(tmp_path)
    source = projects.create_project(name="Source")
    archived = projects.create_project(name="Archived")
    projects.archive_project(archived.project_id)
    unassigned = chats.create_chat(
        title="Unassigned",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    assigned = chats.create_chat(
        title="Assigned",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    service.add_chat(source.project_id, assigned.chat_id)

    with pytest.raises(ProjectArchivedError):
        service.add_chat(archived.project_id, unassigned.chat_id)

    with pytest.raises(ProjectArchivedError):
        service.transfer_chat(assigned.chat_id, archived.project_id)


def test_restrict_deletion_preserves_project_and_linked_chats(
    tmp_path: Path,
) -> None:
    projects, chats, service = _repositories(tmp_path)
    project = projects.create_project(name="Keep")
    chat = chats.create_chat(
        title="Keep me",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    service.add_chat(project.project_id, chat.chat_id)

    with pytest.raises(ProjectHasChatsError, match=r"1 Chat"):
        service.delete_project(project.project_id, policy="restrict")

    assert projects.get_project(project.project_id) == project
    assert chats.get_chat(chat.chat_id).project_id == project.project_id


def test_detach_deletion_keeps_chats_without_project(
    tmp_path: Path,
) -> None:
    projects, chats, service = _repositories(tmp_path)
    project = projects.create_project(name="Detach")
    first = chats.create_chat(
        title="First",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    second = chats.create_chat(
        title="Second",
        mode="work",
        model_name="qwen3.5:9b",
    )
    service.add_chat(project.project_id, first.chat_id)
    service.add_chat(project.project_id, second.chat_id)
    chats.archive_chat(second.chat_id)

    service.delete_project(project.project_id, policy="detach")

    with pytest.raises(ProjectNotFoundError):
        projects.get_project(project.project_id)
    assert chats.get_chat(first.chat_id).project_id is None
    assert chats.get_chat(second.chat_id).project_id is None


def test_cascade_deletion_removes_project_and_all_linked_chats(
    tmp_path: Path,
) -> None:
    projects, chats, service = _repositories(tmp_path)
    project = projects.create_project(name="Cascade")
    first = chats.create_chat(
        title="First",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    second = chats.create_chat(
        title="Second",
        mode="work",
        model_name="qwen3.5:9b",
    )
    service.add_chat(project.project_id, first.chat_id)
    service.add_chat(project.project_id, second.chat_id)

    service.delete_project(project.project_id, policy="cascade")

    with pytest.raises(ProjectNotFoundError):
        projects.get_project(project.project_id)
    with pytest.raises(ChatNotFoundError):
        chats.get_chat(first.chat_id)
    with pytest.raises(ChatNotFoundError):
        chats.get_chat(second.chat_id)


def test_empty_project_can_be_deleted_with_restrict(
    tmp_path: Path,
) -> None:
    projects, _, service = _repositories(tmp_path)
    project = projects.create_project(name="Empty")

    service.delete_project(project.project_id, policy="restrict")

    with pytest.raises(ProjectNotFoundError):
        projects.get_project(project.project_id)


def test_detach_rolls_back_when_project_deletion_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects, chats, service = _repositories(tmp_path)
    project = projects.create_project(name="Rollback detach")
    chat = chats.create_chat(
        title="Restore relationship",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    service.add_chat(project.project_id, chat.chat_id)

    def fail_delete(project_id: ProjectId) -> None:
        raise ProjectStorageError(f"Cannot delete {project_id}")

    monkeypatch.setattr(projects, "delete_project", fail_delete)

    with pytest.raises(ProjectRelationshipError):
        service.delete_project(project.project_id, policy="detach")

    assert chats.get_chat(chat.chat_id).project_id == project.project_id
    assert projects.get_project(project.project_id).project_id == (
        project.project_id
    )


def test_cascade_rolls_back_deleted_chats_when_project_delete_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects, chats, service = _repositories(tmp_path)
    project = projects.create_project(name="Rollback cascade")
    chat = chats.create_chat(
        title="Restore content",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    service.add_chat(project.project_id, chat.chat_id)
    assigned_chat = chats.get_chat(chat.chat_id)

    def fail_delete(project_id: ProjectId) -> None:
        raise ProjectStorageError(f"Cannot delete {project_id}")

    monkeypatch.setattr(projects, "delete_project", fail_delete)

    with pytest.raises(ProjectRelationshipError):
        service.delete_project(project.project_id, policy="cascade")

    assert chats.get_chat(chat.chat_id) == assigned_chat
    assert projects.get_project(project.project_id).project_id == (
        project.project_id
    )


def test_invalid_deletion_policy_is_rejected_before_changes(
    tmp_path: Path,
) -> None:
    projects, _, service = _repositories(tmp_path)
    project = projects.create_project(name="Safe")

    with pytest.raises(ValueError, match=r"policy must be"):
        service.delete_project(
            project.project_id,
            policy=cast(ProjectDeletionPolicy, "keep"),
        )

    assert projects.get_project(project.project_id) == project


def test_validate_deletion_policy_narrows_external_strings() -> None:
    assert validate_deletion_policy("detach") == "detach"

    with pytest.raises(ValueError, match=r"Unknown"):
        validate_deletion_policy("keep")
