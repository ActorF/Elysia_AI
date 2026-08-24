import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import recovery.service as recovery_service
from chats import (
    ChatSession,
    ChatSummary,
    JsonChatRepository,
    LegacyConversationMigrator,
    ProjectId,
    create_chat_message,
)
from projects import (
    JsonProjectRepository,
    ProjectAlreadyExistsError,
    ProjectSettings,
)
from recovery import (
    DataPortabilityError,
    DataPortabilityService,
    ExportValidationError,
    ImportConflictError,
    ImportValidationError,
)

BASE_TIME = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def service_for(
    base_dir: Path,
    *,
    max_import_bytes: int = 16 * 1024 * 1024,
) -> tuple[
    DataPortabilityService,
    JsonChatRepository,
    JsonProjectRepository,
]:
    chats = JsonChatRepository(base_dir / "workspace" / "chats")
    projects = JsonProjectRepository(base_dir / "workspace" / "projects")
    service = DataPortabilityService(
        base_dir=base_dir,
        chat_repository=chats,
        project_repository=projects,
        max_import_bytes=max_import_bytes,
        clock=lambda: BASE_TIME,
    )
    return service, chats, projects


def populated_chat(
    chats: JsonChatRepository,
    *,
    title: str,
    project_id: ProjectId | None = None,
) -> ChatSession:
    chat = chats.create_chat(
        title=title,
        mode="chat",
        model_name="qwen3.5:9b",
        project_id=project_id,
    )
    first_time = max(chat.created_at, BASE_TIME) + timedelta(seconds=1)
    second_time = first_time + timedelta(seconds=1)
    user = create_chat_message(
        role="user",
        content=f"Question for {title}",
        created_at=first_time,
    )
    assistant = create_chat_message(
        role="assistant",
        content=f"Answer for {title}",
        created_at=second_time,
    )
    summary = ChatSummary(
        facts=(f"Fact from {title}",),
        decisions=(),
        action_items=(),
        unresolved_questions=(),
        source_message_ids=(user.message_id, assistant.message_id),
        updated_at=second_time,
    )
    complete = replace(
        chat,
        updated_at=second_time,
        messages=(user, assistant),
        summary=summary,
    )
    chats.save_chat(complete)
    return complete


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def seed_workspace_files(base_dir: Path) -> dict[str, object]:
    files: dict[str, object] = {
        "memory/profile.json": {
            "schema_version": 1,
            "user_name": "Ying",
            "assistant_name": "Elysia",
            "languages": ["Chinese", "English"],
            "project": "Elysia AI",
            "launch_count": 7,
        },
        "memory/long_term_memory.json": {
            "schema_version": 1,
            "memories": [],
        },
        "conversations/conversation.json": {
            "messages": [{
                "timestamp": "2026-08-01 12:00:00",
                "speaker": "User",
                "message": "Legacy message",
            }],
        },
        "conversations/conversation_summary.json": {
            "schema_version": 1,
            "summary": None,
        },
    }
    for relative_path, data in files.items():
        write_json(base_dir / "workspace" / relative_path, data)
    return files


def test_single_chat_export_import_restores_equivalent_session(
    tmp_path: Path,
) -> None:
    source_service, source_chats, _ = service_for(tmp_path / "source")
    original = populated_chat(source_chats, title="Standalone")
    export_file = tmp_path / "exports" / "chat.json"

    source_service.export_chat(original.chat_id, export_file)
    target_service, target_chats, _ = service_for(tmp_path / "target")
    result = target_service.import_bundle(export_file)

    assert result.bundle_type == "chat"
    assert result.chat_ids == (original.chat_id,)
    assert target_chats.get_chat(original.chat_id) == original


def test_project_export_import_restores_project_and_every_chat(
    tmp_path: Path,
) -> None:
    source_service, source_chats, source_projects = service_for(
        tmp_path / "source"
    )
    project = source_projects.create_project(
        name="Elysia",
        settings=ProjectSettings(custom_instructions="Stay local."),
    )
    first = populated_chat(
        source_chats,
        title="First",
        project_id=project.project_id,
    )
    second = populated_chat(
        source_chats,
        title="Second",
        project_id=project.project_id,
    )
    populated_chat(source_chats, title="Outside")
    export_file = tmp_path / "project.json"

    source_service.export_project(project.project_id, export_file)
    target_service, target_chats, target_projects = service_for(
        tmp_path / "target"
    )
    result = target_service.import_bundle(export_file)

    assert result.project_ids == (project.project_id,)
    assert set(result.chat_ids) == {first.chat_id, second.chat_id}
    assert target_projects.get_project(project.project_id) == project
    assert target_chats.get_chat(first.chat_id) == first
    assert target_chats.get_chat(second.chat_id) == second
    assert len(target_chats.list_chats()) == 2


def test_all_user_data_round_trip_restores_equivalent_state(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    source_service, source_chats, source_projects = service_for(source_dir)
    first_project = source_projects.create_project(name="First Project")
    second_project = source_projects.create_project(name="Second Project")
    first_chat = populated_chat(
        source_chats,
        title="First Chat",
        project_id=first_project.project_id,
    )
    second_chat = populated_chat(
        source_chats,
        title="Second Chat",
        project_id=second_project.project_id,
    )
    standalone = populated_chat(source_chats, title="Standalone")
    expected_files = seed_workspace_files(source_dir)
    export_file = tmp_path / "all-user-data.json"

    source_service.export_all_user_data(export_file)
    target_dir = tmp_path / "target"
    target_service, target_chats, target_projects = service_for(target_dir)
    result = target_service.import_bundle(export_file)

    assert result.bundle_type == "user_data"
    assert set(result.project_ids) == {
        first_project.project_id,
        second_project.project_id,
    }
    assert set(result.chat_ids) == {
        first_chat.chat_id,
        second_chat.chat_id,
        standalone.chat_id,
    }
    assert set(target_projects.list_projects()) == {
        first_project,
        second_project,
    }
    assert target_chats.get_chat(first_chat.chat_id) == first_chat
    assert target_chats.get_chat(second_chat.chat_id) == second_chat
    assert target_chats.get_chat(standalone.chat_id) == standalone
    for relative_path, expected in expected_files.items():
        restored = json.loads(
            (target_dir / "workspace" / relative_path).read_text(
                encoding="utf-8"
            )
        )
        assert restored == expected


def test_corrupt_bundle_is_quarantined_and_logged(tmp_path: Path) -> None:
    service, chats, projects = service_for(tmp_path)
    corrupt_file = tmp_path / "incoming" / "broken.json"
    corrupt_file.parent.mkdir()
    corrupt_file.write_text("{not-json", encoding="utf-8")

    with pytest.raises(
        ImportValidationError,
        match="not valid UTF-8 JSON",
    ):
        service.import_bundle(corrupt_file)

    quarantine_files = tuple(
        (tmp_path / "workspace" / "recovery" / "quarantine").iterdir()
    )
    assert len(quarantine_files) == 1
    assert quarantine_files[0].read_bytes() == corrupt_file.read_bytes()
    recovery_log = json.loads(
        (
            tmp_path
            / "workspace"
            / "recovery"
            / "recovery_log.json"
        ).read_text(encoding="utf-8")
    )
    assert recovery_log["schema_version"] == 1
    assert recovery_log["events"][0]["status"] == "rejected"
    assert projects.list_projects(include_archived=True) == ()
    assert chats.list_chats(include_archived=True) == ()


def test_checksum_mismatch_is_rejected_before_mutation(tmp_path: Path) -> None:
    source_service, source_chats, _ = service_for(tmp_path / "source")
    original = populated_chat(source_chats, title="Checksum")
    export_file = tmp_path / "chat.json"
    source_service.export_chat(original.chat_id, export_file)
    bundle = json.loads(export_file.read_text(encoding="utf-8"))
    bundle["payload"]["chat"]["title"] = "Tampered"
    write_json(export_file, bundle)
    target_service, target_chats, _ = service_for(tmp_path / "target")

    with pytest.raises(
        ImportValidationError,
        match="checksum does not match",
    ):
        target_service.import_bundle(export_file)

    assert target_chats.list_chats(include_archived=True) == ()


def test_unknown_chat_schema_field_is_rejected_and_quarantined(
    tmp_path: Path,
) -> None:
    source_service, source_chats, _ = service_for(tmp_path / "source")
    original = populated_chat(source_chats, title="Strict Schema")
    export_file = tmp_path / "chat.json"
    source_service.export_chat(original.chat_id, export_file)
    bundle = json.loads(export_file.read_text(encoding="utf-8"))
    bundle["payload"]["chat"]["unexpected"] = "field"
    bundle["payload_sha256"] = source_service._payload_digest(
        bundle["payload"]
    )
    write_json(export_file, bundle)
    target_dir = tmp_path / "target"
    target_service, target_chats, _ = service_for(target_dir)

    with pytest.raises(
        ImportValidationError,
        match="canonical schema form",
    ):
        target_service.import_bundle(export_file)

    assert target_chats.list_chats(include_archived=True) == ()
    assert len(tuple(
        (
            target_dir
            / "workspace"
            / "recovery"
            / "quarantine"
        ).iterdir()
    )) == 1


@pytest.mark.parametrize(
    "unsafe_path",
    ["../outside.json", r"memory/C:\outside.json"],
)
def test_unsafe_embedded_workspace_path_is_rejected(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    source_service, _, _ = service_for(tmp_path / "source")
    export_file = tmp_path / "all.json"
    source_service.export_all_user_data(export_file)
    bundle = json.loads(export_file.read_text(encoding="utf-8"))
    bundle["payload"]["workspace_files"] = {
        unsafe_path: {"do_not_write": True}
    }
    bundle["payload_sha256"] = source_service._payload_digest(
        bundle["payload"]
    )
    write_json(export_file, bundle)
    target_service, _, _ = service_for(tmp_path / "target")

    with pytest.raises(
        ImportValidationError,
        match="Unsafe workspace file path",
    ):
        target_service.import_bundle(export_file)

    assert not (tmp_path / "outside.json").exists()


def test_full_restore_rebases_legacy_backup_to_target_workspace(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    source_service, source_chats, _ = service_for(source_dir)
    write_json(
        source_dir
        / "workspace"
        / "conversations"
        / "conversation.json",
        {"messages": [{
            "timestamp": "2026-08-01 12:00:00",
            "speaker": "User",
            "message": "Legacy message",
        }]},
    )
    migration = LegacyConversationMigrator(
        base_dir=source_dir,
        chat_repository=source_chats,
        model_name="qwen3.5:9b",
        clock=lambda: BASE_TIME,
    ).migrate()
    assert migration.chat_id is not None
    source_state = json.loads(
        (
            source_dir
            / "workspace"
            / "migrations"
            / "legacy_conversation_v1.json"
        ).read_text(encoding="utf-8")
    )
    backup_name = Path(source_state["backup_path"]).name
    export_file = tmp_path / "all.json"
    source_service.export_all_user_data(export_file)
    target_dir = tmp_path / "target"
    target_service, _, _ = service_for(target_dir)

    target_service.import_bundle(export_file)

    restored_state = json.loads(
        (
            target_dir
            / "workspace"
            / "migrations"
            / "legacy_conversation_v1.json"
        ).read_text(encoding="utf-8")
    )
    assert restored_state["backup_path"] == str(
        target_dir
        / "workspace"
        / "migrations"
        / "backups"
        / backup_name
    )
    target_chats = JsonChatRepository(target_dir / "workspace" / "chats")
    repeated = LegacyConversationMigrator(
        base_dir=target_dir,
        chat_repository=target_chats,
        model_name="qwen3.5:9b",
        clock=lambda: BASE_TIME,
    ).migrate()
    assert repeated.status == "already_migrated"
    assert repeated.chat_id == migration.chat_id


def test_import_size_and_export_path_are_validated(tmp_path: Path) -> None:
    service, chats, _ = service_for(tmp_path, max_import_bytes=10)
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b"x" * 20 + b"}")
    with pytest.raises(ImportValidationError, match="size limit"):
        service.import_bundle(oversized)

    chat = populated_chat(chats, title="Path")
    with pytest.raises(ExportValidationError, match=r"\.json"):
        service.export_chat(chat.chat_id, tmp_path / "chat.txt")


def test_existing_ids_are_conflicts_and_are_not_overwritten(
    tmp_path: Path,
) -> None:
    service, chats, _ = service_for(tmp_path)
    original = populated_chat(chats, title="Existing")
    export_file = tmp_path / "chat.json"
    service.export_chat(original.chat_id, export_file)

    with pytest.raises(ImportConflictError, match="Chat IDs already exist"):
        service.import_bundle(export_file)

    assert chats.get_chat(original.chat_id) == original
    assert len(chats.list_chats()) == 1


def test_mid_import_failure_rolls_back_projects_chats_and_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "source"
    source_service, source_chats, source_projects = service_for(source_dir)
    project = source_projects.create_project(name="Rollback")
    populated_chat(
        source_chats,
        title="Rollback Chat",
        project_id=project.project_id,
    )
    seed_workspace_files(source_dir)
    export_file = tmp_path / "all.json"
    source_service.export_all_user_data(export_file)

    target_dir = tmp_path / "target"
    target_service, target_chats, target_projects = service_for(target_dir)
    old_profile = {
        "schema_version": 1,
        "user_name": "Before",
        "assistant_name": "Elysia",
        "languages": [],
        "project": "",
        "launch_count": 0,
    }
    profile_file = target_dir / "workspace" / "memory" / "profile.json"
    write_json(profile_file, old_profile)
    real_write = recovery_service.atomic_write_json
    write_count = 0

    def fail_second_workspace_write(
        path: Path,
        data: Mapping[str, object],
    ) -> None:
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            raise OSError("simulated restore failure")
        real_write(path, data)

    monkeypatch.setattr(
        recovery_service,
        "atomic_write_json",
        fail_second_workspace_write,
    )

    with pytest.raises(
        DataPortabilityError,
        match="rolled back",
    ):
        target_service.import_bundle(
            export_file,
            overwrite_user_files=True,
        )

    assert target_projects.list_projects(include_archived=True) == ()
    assert target_chats.list_chats(include_archived=True) == ()
    assert json.loads(profile_file.read_text(encoding="utf-8")) == old_profile


def test_restore_project_rejects_duplicate_stable_id(tmp_path: Path) -> None:
    _service, _chats, projects = service_for(tmp_path)
    project = projects.create_project(name="Stable")

    with pytest.raises(ProjectAlreadyExistsError):
        projects.restore_project(project)
