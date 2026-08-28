from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest

from chats import ChatNotFoundError, JsonChatRepository
from projects import (
    ChatProjectConflictError,
    JsonProjectRepository,
    Project,
    ProjectArchivedError,
    ProjectChatBusyError,
    ProjectChatService,
    ProjectDeletionPolicy,
    ProjectHasChatsError,
    ProjectId,
    ProjectNotFoundError,
    ProjectRelationshipError,
    ProjectStorageError,
    ProjectSettings,
    WorkspaceBinding,
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
        clock=lambda: BASE_TIME,
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


def test_project_lifecycle_operations_are_exposed_by_service(
    tmp_path: Path,
) -> None:
    projects, _chats, service = _repositories(tmp_path)

    created = service.create_project(
        name="Project UI",
        custom_instructions="Use the Project context.",
    )

    assert service.get_project(created.project_id) == created
    assert service.list_projects() == (created,)
    assert created.settings.custom_instructions == (
        "Use the Project context."
    )

    renamed = service.rename_project(created.project_id, "Renamed")
    assert renamed.name == "Renamed"

    archived = service.archive_project(created.project_id)
    assert archived.is_archived is True
    assert service.list_projects() == ()
    assert service.list_projects(include_archived=True) == (archived,)

    restored = service.restore_project(created.project_id)
    assert restored.is_archived is False
    assert service.list_projects() == (restored,)
    assert projects.get_project(created.project_id) == restored


def test_custom_instruction_update_preserves_default_model(
    tmp_path: Path,
) -> None:
    projects, _chats, service = _repositories(tmp_path)
    project = projects.create_project(
        name="Settings",
        settings=ProjectSettings(
            default_model_name="qwen3.5:9b",
            custom_instructions="Old instructions",
        ),
    )

    updated = service.update_custom_instructions(
        project.project_id,
        "New instructions",
    )

    assert updated.settings == ProjectSettings(
        default_model_name="qwen3.5:9b",
        custom_instructions="New instructions",
    )

    cleared = service.update_custom_instructions(
        project.project_id,
        None,
    )
    assert cleared.settings == ProjectSettings(
        default_model_name="qwen3.5:9b",
    )


def test_update_project_saves_name_and_instructions_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects, _chats, service = _repositories(tmp_path)
    project = projects.create_project(
        name="Before",
        settings=ProjectSettings(
            default_model_name="qwen3.5:9b",
            custom_instructions="Before instructions",
        ),
        workspace_binding=WorkspaceBinding(root_path=r"C:\Work\Atomic"),
    )
    save_calls = 0
    real_save_project = projects.save_project

    def record_save(updated_project: Project) -> None:
        nonlocal save_calls
        save_calls += 1
        real_save_project(updated_project)

    monkeypatch.setattr(projects, "save_project", record_save)
    monkeypatch.setattr(
        projects,
        "rename_project",
        lambda *_args, **_kwargs: pytest.fail("rename must not be called"),
    )
    monkeypatch.setattr(
        projects,
        "update_settings",
        lambda *_args, **_kwargs: pytest.fail(
            "update_settings must not be called"
        ),
    )

    updated = service.update_project(
        project.project_id,
        name="After",
        custom_instructions="After instructions",
    )

    assert save_calls == 1
    assert updated.name == "After"
    assert updated.settings == ProjectSettings(
        default_model_name="qwen3.5:9b",
        custom_instructions="After instructions",
    )
    assert updated.workspace_binding == project.workspace_binding
    assert updated.is_archived is project.is_archived
    assert projects.get_project(project.project_id) == updated


def test_invalid_atomic_project_update_does_not_partially_persist(
    tmp_path: Path,
) -> None:
    projects, _chats, service = _repositories(tmp_path)
    project = projects.create_project(
        name="Unchanged",
        settings=ProjectSettings(custom_instructions="Keep me"),
    )

    with pytest.raises(ValueError, match=r"name must be"):
        service.update_project(
            project.project_id,
            name="   ",
            custom_instructions="Must not persist",
        )

    assert projects.get_project(project.project_id) == project


def test_workspace_bind_replaces_and_unbinds_idempotently(
    tmp_path: Path,
) -> None:
    _projects, _chats, service = _repositories(tmp_path)
    first = service.create_project(name="First")
    second = service.create_project(name="Second")

    bound = service.bind_workspace(first.project_id, r"C:\Work\Elysia")
    assert bound.workspace_binding == WorkspaceBinding(
        root_path=r"C:\Work\Elysia"
    )

    replaced = service.bind_workspace(
        first.project_id,
        r"C:\Work\Other",
    )
    assert replaced.workspace_binding == WorkspaceBinding(
        root_path=r"C:\Work\Other"
    )

    same_path = service.bind_workspace(
        second.project_id,
        r"C:\Work\Other",
    )
    assert same_path.workspace_binding == replaced.workspace_binding

    unbound = service.unbind_workspace(first.project_id)
    assert unbound.workspace_binding is None
    assert service.unbind_workspace(first.project_id) == unbound

    assert service.get_project(second.project_id) == same_path


@pytest.mark.parametrize(
    "action",
    [
        "rename",
        "update",
        "instructions",
        "bind",
        "unbind",
        "detach",
        "move",
    ],
)
def test_archived_project_is_read_only_for_project_ui_mutations(
    tmp_path: Path,
    action: str,
) -> None:
    projects, chats, service = _repositories(tmp_path)
    project = projects.create_project(
        name="Archived",
        workspace_binding=WorkspaceBinding(root_path=r"C:\Work\Archived"),
    )
    chat = chats.create_chat(
        title="Linked",
        mode="chat",
        model_name="qwen3.5:9b",
        project_id=project.project_id,
    )
    projects.archive_project(project.project_id)

    with pytest.raises(ProjectArchivedError):
        if action == "rename":
            service.rename_project(project.project_id, "Changed")
        elif action == "update":
            service.update_project(
                project.project_id,
                name="Changed",
                custom_instructions="Changed",
            )
        elif action == "instructions":
            service.update_custom_instructions(
                project.project_id,
                "Changed",
            )
        elif action == "bind":
            service.bind_workspace(
                project.project_id,
                r"C:\Work\Changed",
            )
        elif action == "unbind":
            service.unbind_workspace(project.project_id)
        elif action == "move":
            service.move_chat(chat.chat_id, None)
        else:
            service.detach_chat(project.project_id, chat.chat_id)


def test_transfer_rejects_archived_source_project(
    tmp_path: Path,
) -> None:
    projects, chats, service = _repositories(tmp_path)
    source = projects.create_project(name="Archived source")
    destination = projects.create_project(name="Destination")
    chat = chats.create_chat(
        title="Stay put",
        mode="chat",
        model_name="qwen3.5:9b",
        project_id=source.project_id,
    )
    projects.archive_project(source.project_id)

    with pytest.raises(ProjectArchivedError):
        service.transfer_chat(chat.chat_id, destination.project_id)

    assert chats.get_chat(chat.chat_id).project_id == source.project_id


def test_archive_preserves_all_chat_relationships(
    tmp_path: Path,
) -> None:
    _projects, chats, service = _repositories(tmp_path)
    project = service.create_project(name="Keep relationships")
    visible = chats.create_chat(
        title="Visible",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    hidden = chats.create_chat(
        title="Archived",
        mode="work",
        model_name="qwen3.5:9b",
    )
    service.attach_chat(project.project_id, visible.chat_id)
    service.attach_chat(project.project_id, hidden.chat_id)
    chats.archive_chat(hidden.chat_id)

    service.archive_project(project.project_id)

    assert {
        metadata.chat_id
        for metadata in service.list_project_chats(
            project.project_id,
            include_archived=True,
        )
    } == {visible.chat_id, hidden.chat_id}
    assert chats.get_chat(visible.chat_id).project_id == project.project_id
    assert chats.get_chat(hidden.chat_id).project_id == project.project_id


def test_move_chat_selects_attach_transfer_detach_and_no_op(
    tmp_path: Path,
) -> None:
    _projects, chats, service = _repositories(tmp_path)
    first = service.create_project(name="First")
    second = service.create_project(name="Second")
    chat = chats.create_chat(
        title="Move through scopes",
        mode="chat",
        model_name="qwen3.5:9b",
    )

    attached = service.move_chat(chat.chat_id, first.project_id)
    assert attached.project_id == first.project_id

    transferred = service.move_chat(chat.chat_id, second.project_id)
    assert transferred.project_id == second.project_id

    assert service.move_chat(chat.chat_id, second.project_id) == transferred

    detached = service.move_chat(chat.chat_id, None)
    assert detached.project_id is None
    assert service.move_chat(chat.chat_id, None) == detached


def test_move_chat_rejects_archived_source_or_destination(
    tmp_path: Path,
) -> None:
    projects, chats, service = _repositories(tmp_path)
    source = service.create_project(name="Source")
    destination = service.create_project(name="Destination")
    chat = chats.create_chat(
        title="Cannot move",
        mode="chat",
        model_name="qwen3.5:9b",
        project_id=source.project_id,
    )
    projects.archive_project(destination.project_id)

    with pytest.raises(ProjectArchivedError):
        service.move_chat(chat.chat_id, destination.project_id)

    projects.archive_project(destination.project_id, archived=False)
    projects.archive_project(source.project_id)

    with pytest.raises(ProjectArchivedError):
        service.move_chat(chat.chat_id, None)

    assert chats.get_chat(chat.chat_id) == chat


@pytest.mark.parametrize(
    "action",
    [
        "attach",
        "detach",
        "transfer",
        "move",
        "rename",
        "update",
        "instructions",
        "bind",
        "unbind",
        "archive",
    ],
)
def test_busy_chat_rejects_project_and_relationship_mutations(
    tmp_path: Path,
    action: str,
) -> None:
    busy_chat_ids: set[object] = set()
    projects = JsonProjectRepository(tmp_path / "data" / "projects")
    chats = JsonChatRepository(tmp_path / "data" / "chats")
    service = ProjectChatService(
        projects,
        chats,
        is_chat_busy=lambda chat_id: chat_id in busy_chat_ids,
    )
    source = service.create_project(name="Source")
    destination = service.create_project(name="Destination")
    unassigned = chats.create_chat(
        title="Unassigned",
        mode="chat",
        model_name="qwen3.5:9b",
    )
    assigned = chats.create_chat(
        title="Assigned",
        mode="chat",
        model_name="qwen3.5:9b",
        project_id=source.project_id,
    )
    target_chat = unassigned if action == "attach" else assigned
    if action == "unbind":
        service.bind_workspace(source.project_id, r"C:\Work\Busy")
    busy_chat_ids.add(target_chat.chat_id)

    with pytest.raises(ProjectChatBusyError, match=r"busy"):
        if action == "attach":
            service.attach_chat(source.project_id, target_chat.chat_id)
        elif action == "detach":
            service.detach_chat(source.project_id, target_chat.chat_id)
        elif action == "transfer":
            service.transfer_chat(
                target_chat.chat_id,
                destination.project_id,
            )
        elif action == "move":
            service.move_chat(
                target_chat.chat_id,
                destination.project_id,
            )
        elif action == "rename":
            service.rename_project(source.project_id, "Changed")
        elif action == "update":
            service.update_project(
                source.project_id,
                name="Changed",
                custom_instructions="Changed",
            )
        elif action == "instructions":
            service.update_custom_instructions(
                source.project_id,
                "Changed",
            )
        elif action == "bind":
            service.bind_workspace(
                source.project_id,
                r"C:\Work\Changed",
            )
        elif action == "unbind":
            service.unbind_workspace(source.project_id)
        else:
            service.archive_project(source.project_id)

    assert chats.get_chat(target_chat.chat_id) == target_chat
    assert service.get_project(source.project_id).name == "Source"
    assert service.get_project(source.project_id).is_archived is False
    if action == "unbind":
        assert service.get_project(source.project_id).workspace_binding == (
            WorkspaceBinding(root_path=r"C:\Work\Busy")
        )
    elif action == "bind":
        assert service.get_project(source.project_id).workspace_binding is None
