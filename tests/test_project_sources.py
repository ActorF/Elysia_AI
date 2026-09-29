"""Verify Project Source catalogs, Chat-derived authorization, and sharing."""

from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing
from pathlib import Path
from threading import Barrier
from typing import Any, Iterator, cast

import pytest

from attachments import (
    AttachmentScope,
    AttachmentService,
    FileCatalogSnapshot,
    JsonAttachmentStore,
)
from chats import (
    AttachmentId,
    AttachmentMetadata,
    ChatId,
    ChatSession,
    ProjectId,
    create_chat_message,
    create_chat_session,
)
from documents import (
    DocumentSource,
    ExpectedDocumentGeneration,
    GROUNDED_ANSWER_PREFERENCES_SCHEMA_VERSION,
    GROUNDED_ANSWER_SCHEMA_VERSION,
    GroundedAnswerLimits,
    GroundedAnswerPreferences,
    GroundedAnswerResult,
    RetrievalLimits,
    RetrievalMetadataFilter,
    RetrievalPolicy,
)
from project_sources import (
    PROJECT_SOURCE_GENERATION_SCHEMA_VERSION,
    PROJECT_SOURCE_INSTRUCTIONS_SCHEMA_VERSION,
    PROJECT_SOURCE_SNAPSHOT_SCHEMA_VERSION,
    CatalogEntrySnapshot,
    JsonProjectSourceRepository,
    ProjectSourceAnswerService,
    ProjectSourceAuthorizationError,
    ProjectSourceConflictError,
    ProjectSourceGeneration,
    ProjectSourceInstructions,
    ProjectSourceSnapshot,
    ProjectSourceStaleError,
    ProjectSourceStorageError,
    select_indexable_file_catalog,
    snapshot_file_catalog,
    sources_from_catalog,
)
from projects import Project, ProjectSettings, create_project


def _digest(label: str) -> str:
    """Return one deterministic lowercase SHA-256 fixture digest."""

    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _save_catalog_in_process(
    storage_directory: str,
    snapshot: ProjectSourceSnapshot,
    barrier: Any,
    result_queue: Any,
) -> None:
    """Publish one snapshot from a spawned process and report a safe outcome."""

    try:
        barrier.wait(timeout=20)
        JsonProjectSourceRepository(Path(storage_directory)).save_snapshot(
            snapshot,
            expected_revision=0,
        )
        result_queue.put(("saved", snapshot.scope.id))
    except ProjectSourceConflictError:
        result_queue.put(("conflict", snapshot.scope.id))
    except Exception as error:  # pragma: no cover - diagnostic for child failure
        result_queue.put(("error", type(error).__name__))


class _ChatRepository:
    """Expose deterministic canonical Chat sessions for service tests."""

    def __init__(self, *sessions: ChatSession) -> None:
        """Index supplied sessions by their stable Chat IDs."""

        self.sessions = {session.chat_id: session for session in sessions}

    def get_chat(self, chat_id: ChatId) -> ChatSession:
        """Return the exact current Chat session."""

        return self.sessions[chat_id]


class _ProjectRepository:
    """Expose deterministic canonical Projects for service tests."""

    def __init__(self, *projects: Project) -> None:
        """Index supplied Projects by their stable IDs."""

        self.projects = {
            project.project_id: project for project in projects
        }

    def get_project(self, project_id: ProjectId) -> Project:
        """Return the exact current Project aggregate."""

        return self.projects[project_id]


class _Lease:
    """Model the authority lease required around every source answer."""

    def __init__(self) -> None:
        """Start with no held Chat authority."""

        self.depth = 0
        self.chat_ids: list[ChatId] = []

    @contextmanager
    def hold(self, chat_id: ChatId) -> Iterator[None]:
        """Keep a visible lease depth until the operation completes."""

        self.chat_ids.append(chat_id)
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1


class _Grounder:
    """Capture exact authorized calls without requiring a local model."""

    def __init__(self, lease: _Lease) -> None:
        """Retain the lease used to prove call ordering."""

        self.lease = lease
        self.calls: list[
            tuple[
                AttachmentScope,
                str,
                tuple[ExpectedDocumentGeneration, ...],
                GroundedAnswerPreferences | None,
            ]
        ] = []

    def answer(
        self,
        scope: AttachmentScope,
        query: str,
        expected_documents: tuple[ExpectedDocumentGeneration, ...],
        *,
        preferences: GroundedAnswerPreferences | None = None,
        metadata_filter: RetrievalMetadataFilter = RetrievalMetadataFilter(),
        retrieval_policy: RetrievalPolicy = RetrievalPolicy(),
        retrieval_limits: RetrievalLimits = RetrievalLimits(),
        answer_limits: GroundedAnswerLimits = GroundedAnswerLimits(),
    ) -> GroundedAnswerResult:
        """Return model-free insufficiency from the exact supplied scope."""

        del metadata_filter, retrieval_policy, retrieval_limits, answer_limits
        assert self.lease.depth == 1
        self.calls.append((scope, query, expected_documents, preferences))
        return GroundedAnswerResult(
            schema_version=GROUNDED_ANSWER_SCHEMA_VERSION,
            scope=scope,
            status="insufficient_evidence",
            generator_identity=None,
            context_passage_count=0,
            statements=(),
            citations=(),
        )


class _PreferredInstructions:
    """Return one structured source order plus bounded style guidance."""

    def __init__(self, preferred_index: int = 0) -> None:
        """Select which authorized source should be preferred."""

        self.preferred_index = preferred_index
        self.calls = 0

    def get_preferences(
        self,
        project: Project,
        scope: AttachmentScope,
        authorized_sources: tuple[DocumentSource, ...],
        instructions: ProjectSourceInstructions,
    ) -> GroundedAnswerPreferences:
        """Build a preference only from the supplied authorized sources."""

        del instructions
        self.calls += 1
        return GroundedAnswerPreferences(
            schema_version=GROUNDED_ANSWER_PREFERENCES_SCHEMA_VERSION,
            scope=scope,
            preferred_source_link_ids=(
                authorized_sources[self.preferred_index].link_id,
            ),
            answer_style="concise",
            style_guidance=project.settings.custom_instructions,
        )


class _ForeignInstructions:
    """Attempt to smuggle an unknown source through structured preferences."""

    def get_preferences(
        self,
        project: Project,
        scope: AttachmentScope,
        authorized_sources: tuple[DocumentSource, ...],
        instructions: ProjectSourceInstructions,
    ) -> GroundedAnswerPreferences:
        """Return one validly shaped but unauthorized preference."""

        del project, authorized_sources, instructions
        return GroundedAnswerPreferences(
            schema_version=GROUNDED_ANSWER_PREFERENCES_SCHEMA_VERSION,
            scope=scope,
            preferred_source_link_ids=("attachment_foreign",),
        )


class _ForeignCatalogRepository:
    """Return one catalog for a different Project regardless of request."""

    def __init__(self, snapshot: ProjectSourceSnapshot) -> None:
        """Retain the malicious cross-Project snapshot."""

        self.snapshot = snapshot

    def get_snapshot(self, scope: AttachmentScope) -> ProjectSourceSnapshot:
        """Ignore the requested scope to simulate a hostile adapter."""

        del scope
        return self.snapshot


def _owners(
    project: Project,
    *chat_titles: str,
) -> tuple[tuple[ChatSession, ...], AttachmentScope]:
    """Create active Chats linked to one Project and its exact scope."""

    chats = tuple(
        create_chat_session(
            title=title,
            mode="chat",
            model_name="fixture-model",
            project_id=project.project_id,
        )
        for title in chat_titles
    )
    return chats, AttachmentScope(
        kind="project",
        id=str(project.project_id),
    )


def _stage_sources(
    attachment_service: AttachmentService,
    scope: AttachmentScope,
    tmp_path: Path,
    *names: str,
) -> FileCatalogSnapshot:
    """Import deterministic text files directly as Project Sources."""

    paths: list[Path] = []
    for index, name in enumerate(names):
        path = tmp_path / name
        path.write_text(f"source {index}: {name}", encoding="utf-8")
        paths.append(path)
    attachment_service.stage_files(scope, tuple(paths))
    return attachment_service.snapshot_files(scope)


def _document_sources(
    snapshot: FileCatalogSnapshot,
) -> tuple[DocumentSource, ...]:
    """Project one attachment snapshot into exact document source values."""

    originals = {item.file_id: item for item in snapshot.originals}
    return tuple(
        DocumentSource(
            scope=snapshot.scope,
            link_id=ownership.link_id,
            file_id=ownership.file_id,
            file_name=ownership.file_name,
            media_type=ownership.media_type,
            size_bytes=originals[ownership.file_id].size_bytes,
        )
        for ownership in snapshot.ownerships
    )


def _snapshot(
    file_catalog: FileCatalogSnapshot,
    profile: str,
    *,
    revision: int = 1,
    preferred_index: int | None = None,
    answer_style: str = "default",
) -> ProjectSourceSnapshot:
    """Build one complete published generation catalog for current ownership."""

    sources = _document_sources(file_catalog)
    preferred = (
        ()
        if preferred_index is None
        else (sources[preferred_index].link_id,)
    )
    return ProjectSourceSnapshot(
        schema_version=PROJECT_SOURCE_SNAPSHOT_SCHEMA_VERSION,
        scope=file_catalog.scope,
        revision=revision,
        source_catalog_fingerprint=file_catalog.snapshot_fingerprint,
        sources=tuple(
            ProjectSourceGeneration(
                schema_version=PROJECT_SOURCE_GENERATION_SCHEMA_VERSION,
                generation=ExpectedDocumentGeneration(
                    source=source,
                    derivation_fingerprint=_digest(
                        f"derivation:{source.link_id}"
                    ),
                ),
                index_profile_fingerprint=profile,
                published_at=datetime.now(timezone.utc),
            )
            for source in sources
        ),
        instructions=ProjectSourceInstructions(
            schema_version=PROJECT_SOURCE_INSTRUCTIONS_SCHEMA_VERSION,
            preferred_source_link_ids=preferred,
            answer_style=cast(Any, answer_style),
        ),
    )


def _service(
    *,
    chats: tuple[ChatSession, ...],
    project: Project,
    attachments: AttachmentService,
    catalog: object,
    lease: _Lease,
    grounder: _Grounder,
    profile: str,
    instructions: object | None = None,
) -> ProjectSourceAnswerService:
    """Compose the tested service with deterministic in-memory boundaries."""

    return ProjectSourceAnswerService(
        cast(Any, _ChatRepository(*chats)),
        cast(Any, _ProjectRepository(project)),
        attachments,
        cast(Any, catalog),
        cast(Any, grounder),
        lease,
        current_index_profile_fingerprint=profile,
        instruction_provider=cast(Any, instructions),
    )


def test_shared_catalog_projection_filters_only_supported_document_routes(
    tmp_path: Path,
) -> None:
    """Give answering and lifecycle code one identical corpus projection."""

    project = create_project(name="Projection")
    _chats, scope = _owners(project, "Projection Chat")
    store = JsonAttachmentStore(tmp_path / "files", 1_000_000)
    attachments = AttachmentService(store)
    text = tmp_path / "notes.txt"
    image = tmp_path / "reference.png"
    text.write_text("trusted text", encoding="utf-8")
    image.write_bytes(b"image bytes")
    attachments.stage_files(scope, (text, image))

    detached = snapshot_file_catalog(attachments.snapshot_files(scope))
    selected = select_indexable_file_catalog(
        detached,
        frozenset({(".txt", "text/plain")}),
    )
    sources = sources_from_catalog(selected, scope)

    assert tuple(source.file_name for source in sources) == ("notes.txt",)
    assert selected.snapshot_fingerprint != detached.snapshot_fingerprint
    assert sources[0].scope == scope
    store.close()


def test_catalog_repository_persists_and_enforces_cas(
    tmp_path: Path,
) -> None:
    """Round-trip exact generations and reject stale writers and revokers."""

    attachment_store = JsonAttachmentStore(tmp_path / "files", 1_000_000)
    attachment_service = AttachmentService(attachment_store)
    project = create_project(name="Catalog")
    _chats, scope = _owners(project, "Catalog Chat")
    file_catalog = _stage_sources(
        attachment_service,
        scope,
        tmp_path,
        "catalog.txt",
    )
    snapshot = _snapshot(file_catalog, _digest("profile"))
    changed_instructions = replace(
        snapshot,
        instructions=ProjectSourceInstructions(
            schema_version=PROJECT_SOURCE_INSTRUCTIONS_SCHEMA_VERSION,
            answer_style="detailed",
        ),
        snapshot_fingerprint="",
    )
    assert changed_instructions.snapshot_fingerprint != (
        snapshot.snapshot_fingerprint
    )
    repository = JsonProjectSourceRepository(tmp_path / "catalog")

    assert repository.read_entry(scope) == CatalogEntrySnapshot(
        revision=0,
        snapshot=None,
    )
    assert repository.save_snapshot(snapshot, expected_revision=0) == snapshot
    assert repository.read_entry(scope) == CatalogEntrySnapshot(
        revision=1,
        snapshot=snapshot,
    )
    assert JsonProjectSourceRepository(
        tmp_path / "catalog"
    ).get_snapshot(scope) == snapshot
    with pytest.raises(ProjectSourceConflictError):
        repository.save_snapshot(snapshot, expected_revision=0)
    with pytest.raises(ProjectSourceConflictError):
        repository.delete_snapshot(scope, expected_revision=2)
    tombstone_revision = repository.delete_snapshot(
        scope,
        expected_revision=1,
    )
    assert tombstone_revision == 2
    assert repository.read_entry(scope) == CatalogEntrySnapshot(
        revision=2,
        snapshot=None,
    )
    assert repository.get_revision(scope) == 2
    assert repository.list_snapshots() == ()
    with pytest.raises(ProjectSourceConflictError):
        repository.save_snapshot(
            _snapshot(file_catalog, _digest("profile"), revision=2),
            expected_revision=1,
        )
    recreated = _snapshot(file_catalog, _digest("profile"), revision=3)
    assert repository.save_snapshot(
        recreated,
        expected_revision=2,
    ) == recreated
    assert repository.read_entry(scope) == CatalogEntrySnapshot(
        revision=3,
        snapshot=recreated,
    )
    attachment_store.close()


def test_catalog_cas_serializes_independent_repository_instances(
    tmp_path: Path,
) -> None:
    """Preserve concurrent updates from repository instances sharing one file."""

    first_project = create_project(name="Concurrent A")
    second_project = create_project(name="Concurrent B")
    _first_chats, first_scope = _owners(first_project, "A")
    _second_chats, second_scope = _owners(second_project, "B")
    first_catalog = FileCatalogSnapshot(1, first_scope, (), ())
    second_catalog = FileCatalogSnapshot(1, second_scope, (), ())
    first_snapshot = _snapshot(first_catalog, _digest("profile"))
    second_snapshot = _snapshot(second_catalog, _digest("profile"))
    storage = tmp_path / "catalog"
    barrier = Barrier(2)

    def _save(snapshot: ProjectSourceSnapshot) -> ProjectSourceSnapshot:
        """Start both CAS operations together through separate instances."""

        repository = JsonProjectSourceRepository(storage)
        barrier.wait()
        return repository.save_snapshot(snapshot, expected_revision=0)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(_save, (first_snapshot, second_snapshot))
        )

    assert set(results) == {first_snapshot, second_snapshot}
    assert JsonProjectSourceRepository(storage).list_snapshots() == tuple(
        sorted(
            (first_snapshot, second_snapshot),
            key=lambda snapshot: snapshot.scope.id,
        )
    )


def test_catalog_cas_serializes_independent_processes(tmp_path: Path) -> None:
    """Exercise the OS lock so separate writers cannot lose an update."""

    first_project = create_project(name="Process A")
    second_project = create_project(name="Process B")
    _first_chats, first_scope = _owners(first_project, "A")
    _second_chats, second_scope = _owners(second_project, "B")
    first_snapshot = _snapshot(
        FileCatalogSnapshot(1, first_scope, (), ()),
        _digest("profile"),
    )
    second_snapshot = _snapshot(
        FileCatalogSnapshot(1, second_scope, (), ()),
        _digest("profile"),
    )
    storage = tmp_path / "catalog"
    process_context = multiprocessing.get_context("spawn")
    barrier = process_context.Barrier(2)
    result_queue = process_context.Queue()
    processes = (
        process_context.Process(
            target=_save_catalog_in_process,
            args=(str(storage), first_snapshot, barrier, result_queue),
        ),
        process_context.Process(
            target=_save_catalog_in_process,
            args=(str(storage), second_snapshot, barrier, result_queue),
        ),
    )

    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=30)
        assert all(not process.is_alive() for process in processes)
        assert all(process.exitcode == 0 for process in processes)
        outcomes = {result_queue.get(timeout=10) for _ in processes}
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            process.close()
        result_queue.close()
        result_queue.join_thread()

    assert outcomes == {
        ("saved", first_scope.id),
        ("saved", second_scope.id),
    }
    assert JsonProjectSourceRepository(storage).list_snapshots() == tuple(
        sorted(
            (first_snapshot, second_snapshot),
            key=lambda snapshot: snapshot.scope.id,
        )
    )


def test_two_chats_share_only_their_canonical_project_generations(
    tmp_path: Path,
) -> None:
    """Resolve identical Project Scope and generations from two linked Chats."""

    project = create_project(
        name="Shared",
        settings=ProjectSettings(custom_instructions="Answer briefly."),
    )
    chats, scope = _owners(project, "First", "Second")
    attachment_store = JsonAttachmentStore(tmp_path / "files", 1_000_000)
    attachments = AttachmentService(attachment_store)
    file_catalog = _stage_sources(
        attachments,
        scope,
        tmp_path,
        "alpha.txt",
        "beta.txt",
    )
    profile = _digest("current-profile")
    catalog_repository = JsonProjectSourceRepository(tmp_path / "catalog")
    catalog_repository.save_snapshot(
        _snapshot(
            file_catalog,
            profile,
            preferred_index=1,
            answer_style="concise",
        ),
        expected_revision=0,
    )
    lease = _Lease()
    grounder = _Grounder(lease)
    service = _service(
        chats=chats,
        project=project,
        attachments=attachments,
        catalog=catalog_repository,
        lease=lease,
        grounder=grounder,
        profile=profile,
    )

    first = service.answer(chats[0].chat_id, "First question")
    second = service.answer(chats[1].chat_id, "Second question")

    assert first.project_id == second.project_id == project.project_id
    assert first.answer.scope == second.answer.scope == scope
    assert grounder.calls[0][2] == grounder.calls[1][2]
    assert grounder.calls[0][3] == grounder.calls[1][3]
    assert grounder.calls[0][3] is not None
    assert grounder.calls[0][3].preferred_source_link_ids == (
        grounder.calls[0][2][1].source.link_id,
    )
    assert grounder.calls[0][3].answer_style == "concise"
    assert grounder.calls[0][3].style_guidance == "Answer briefly."
    assert lease.depth == 0
    attachment_store.close()


def test_chat_attachment_never_joins_project_corpus_implicitly(
    tmp_path: Path,
) -> None:
    """Keep even an equal-content Chat file outside the Project allowlist."""

    project = create_project(name="Isolated")
    chats, project_scope = _owners(project, "Only Chat")
    chat_scope = AttachmentScope(kind="chat", id=str(chats[0].chat_id))
    attachment_store = JsonAttachmentStore(tmp_path / "files", 1_000_000)
    attachments = AttachmentService(attachment_store)
    source_path = tmp_path / "same.txt"
    source_path.write_text("same bytes", encoding="utf-8")
    attachments.stage_files(chat_scope, (source_path,))
    lease = _Lease()
    grounder = _Grounder(lease)
    service = _service(
        chats=chats,
        project=project,
        attachments=attachments,
        catalog=JsonProjectSourceRepository(tmp_path / "catalog"),
        lease=lease,
        grounder=grounder,
        profile=_digest("profile"),
    )

    result = service.answer(chats[0].chat_id, "Can Project see it?")

    assert result.answer.status == "insufficient_evidence"
    assert grounder.calls[0][0] == project_scope
    assert grounder.calls[0][2] == ()
    assert attachments.snapshot_files(chat_scope).ownerships
    assert attachments.snapshot_files(project_scope).ownerships == ()
    attachment_store.close()


def test_non_document_project_attachment_does_not_stale_text_corpus(
    tmp_path: Path,
) -> None:
    """Exclude image-only ownership from the trusted document-route corpus."""

    project = create_project(name="Images")
    chats, project_scope = _owners(project, "Image Chat")
    attachment_store = JsonAttachmentStore(tmp_path / "files", 1_000_000)
    attachments = AttachmentService(attachment_store)
    image = tmp_path / "reference.png"
    image.write_bytes(b"not parsed by attachment storage")
    attachments.stage_files(project_scope, (image,))
    lease = _Lease()
    grounder = _Grounder(lease)
    service = _service(
        chats=chats,
        project=project,
        attachments=attachments,
        catalog=JsonProjectSourceRepository(tmp_path / "catalog"),
        lease=lease,
        grounder=grounder,
        profile=_digest("profile"),
    )

    result = service.answer(chats[0].chat_id, "No indexable documents")

    assert result.answer.status == "insufficient_evidence"
    assert grounder.calls[0][0] == project_scope
    assert grounder.calls[0][2] == ()
    attachment_store.close()


def test_unknown_structured_preference_fails_before_grounding(
    tmp_path: Path,
) -> None:
    """Reject a preferred link that current Project ownership did not grant."""

    project = create_project(name="Preferences")
    chats, scope = _owners(project, "Chat")
    attachment_store = JsonAttachmentStore(tmp_path / "files", 1_000_000)
    attachments = AttachmentService(attachment_store)
    file_catalog = _stage_sources(
        attachments,
        scope,
        tmp_path,
        "authorized.txt",
    )
    profile = _digest("profile")
    catalog = JsonProjectSourceRepository(tmp_path / "catalog")
    catalog.save_snapshot(_snapshot(file_catalog, profile), expected_revision=0)
    lease = _Lease()
    grounder = _Grounder(lease)
    service = _service(
        chats=chats,
        project=project,
        attachments=attachments,
        catalog=catalog,
        lease=lease,
        grounder=grounder,
        profile=profile,
        instructions=_ForeignInstructions(),
    )

    with pytest.raises(ProjectSourceAuthorizationError):
        service.answer(chats[0].chat_id, "Attempt confused deputy")

    assert grounder.calls == []
    attachment_store.close()


def test_catalog_mismatch_or_old_profile_never_becomes_partial_evidence(
    tmp_path: Path,
) -> None:
    """Fail a stale corpus instead of silently omitting missing generations."""

    project = create_project(name="Stale")
    chats, scope = _owners(project, "Chat")
    attachment_store = JsonAttachmentStore(tmp_path / "files", 1_000_000)
    attachments = AttachmentService(attachment_store)
    first_catalog = _stage_sources(
        attachments,
        scope,
        tmp_path,
        "first.txt",
    )
    catalog = JsonProjectSourceRepository(tmp_path / "catalog")
    catalog.save_snapshot(
        _snapshot(first_catalog, _digest("old-profile")),
        expected_revision=0,
    )
    lease = _Lease()
    grounder = _Grounder(lease)
    service = _service(
        chats=chats,
        project=project,
        attachments=attachments,
        catalog=catalog,
        lease=lease,
        grounder=grounder,
        profile=_digest("new-profile"),
    )

    with pytest.raises(ProjectSourceStaleError, match="old index profile"):
        service.answer(chats[0].chat_id, "Do not use stale vectors")

    assert grounder.calls == []
    _stage_sources(attachments, scope, tmp_path, "second.txt")
    with pytest.raises(ProjectSourceStaleError, match="ownership"):
        service.answer(chats[0].chat_id, "Do not use a partial corpus")
    assert grounder.calls == []
    attachment_store.close()


def test_foreign_project_catalog_is_rejected_before_grounding(
    tmp_path: Path,
) -> None:
    """Refuse a catalog adapter that ignores the requested Project scope."""

    project_a = create_project(name="A")
    project_b = create_project(name="B")
    chats, scope_a = _owners(project_a, "A Chat")
    _other_chats, scope_b = _owners(project_b, "B Chat")
    attachment_store = JsonAttachmentStore(tmp_path / "files", 1_000_000)
    attachments = AttachmentService(attachment_store)
    catalog_a = _stage_sources(
        attachments,
        scope_a,
        tmp_path,
        "a.txt",
    )
    catalog_b = _stage_sources(
        attachments,
        scope_b,
        tmp_path,
        "b.txt",
    )
    profile = _digest("profile")
    lease = _Lease()
    grounder = _Grounder(lease)
    service = _service(
        chats=chats,
        project=project_a,
        attachments=attachments,
        catalog=_ForeignCatalogRepository(_snapshot(catalog_b, profile)),
        lease=lease,
        grounder=grounder,
        profile=profile,
    )

    with pytest.raises(ProjectSourceStaleError):
        service.answer(chats[0].chat_id, "Read B from A")

    assert catalog_a.scope == scope_a
    assert grounder.calls == []
    attachment_store.close()


@pytest.mark.parametrize("archived_owner", ("chat", "project"))
def test_archived_authority_cannot_read_project_sources(
    tmp_path: Path,
    archived_owner: str,
) -> None:
    """Reject archived Chats and Projects before ownership or grounding."""

    project = create_project(name="Archive")
    chats, _scope = _owners(project, "Chat")
    chat = replace(chats[0], is_archived=archived_owner == "chat")
    project = replace(project, is_archived=archived_owner == "project")
    attachment_store = JsonAttachmentStore(tmp_path / "files", 1_000_000)
    attachments = AttachmentService(attachment_store)
    lease = _Lease()
    grounder = _Grounder(lease)
    service = _service(
        chats=(chat,),
        project=project,
        attachments=attachments,
        catalog=JsonProjectSourceRepository(tmp_path / "catalog"),
        lease=lease,
        grounder=grounder,
        profile=_digest("profile"),
    )

    with pytest.raises(ProjectSourceAuthorizationError):
        service.answer(chat.chat_id, "Archived authority")

    assert grounder.calls == []
    attachment_store.close()


def test_orphan_committed_attachment_cannot_be_promoted(
    tmp_path: Path,
) -> None:
    """Require canonical Chat-message ownership beyond a manifest status bit."""

    project = create_project(name="Orphan")
    chats, project_scope = _owners(project, "Chat")
    chat_scope = AttachmentScope(kind="chat", id=str(chats[0].chat_id))
    attachment_store = JsonAttachmentStore(tmp_path / "files", 1_000_000)
    attachments = AttachmentService(attachment_store)
    path = tmp_path / "orphan.txt"
    path.write_text("not referenced by history", encoding="utf-8")
    state = attachments.stage_files(chat_scope, (path,))
    attachment_id = state.attachments[0].attachment_id
    attachments.claim_chat(chat_scope, (attachment_id,))
    attachments.mark_committed(chat_scope, (attachment_id,))
    lease = _Lease()
    service = _service(
        chats=chats,
        project=project,
        attachments=attachments,
        catalog=JsonProjectSourceRepository(tmp_path / "catalog"),
        lease=lease,
        grounder=_Grounder(lease),
        profile=_digest("profile"),
    )

    with pytest.raises(ProjectSourceAuthorizationError, match="history"):
        service.promote_chat_attachment(chats[0].chat_id, attachment_id)

    assert attachments.snapshot_files(project_scope).ownerships == ()
    attachment_store.close()


def test_explicit_promotion_copies_only_committed_chat_attachment(
    tmp_path: Path,
) -> None:
    """Preserve Chat ownership while creating one idempotent Project link."""

    project = create_project(name="Promotion")
    chats, project_scope = _owners(project, "Chat")
    chat_scope = AttachmentScope(kind="chat", id=str(chats[0].chat_id))
    attachment_store = JsonAttachmentStore(tmp_path / "files", 1_000_000)
    attachments = AttachmentService(attachment_store)
    path = tmp_path / "promote.txt"
    path.write_text("promote only after commit", encoding="utf-8")
    state = attachments.stage_files(chat_scope, (path,))
    item = state.attachments[0]
    attachment_id = item.attachment_id
    attachments.claim_chat(chat_scope, (attachment_id,))
    attachments.mark_committed(chat_scope, (attachment_id,))
    message = create_chat_message(
        role="user",
        content="Promote this committed file.",
        attachments=(
            AttachmentMetadata(
                attachment_id=cast(AttachmentId, attachment_id),
                file_name=item.file_name,
                media_type=item.media_type,
                size_bytes=item.size_bytes,
            ),
        ),
    )
    chat = replace(
        chats[0],
        messages=(message,),
        updated_at=message.created_at,
    )
    chats = (chat,)
    lease = _Lease()
    service = _service(
        chats=chats,
        project=project,
        attachments=attachments,
        catalog=JsonProjectSourceRepository(tmp_path / "catalog"),
        lease=lease,
        grounder=_Grounder(lease),
        profile=_digest("profile"),
    )

    promoted = service.promote_chat_attachment(
        chat.chat_id,
        attachment_id,
    )
    repeated = service.promote_chat_attachment(
        chat.chat_id,
        attachment_id,
    )

    assert promoted == repeated
    assert promoted.scope == project_scope
    assert promoted.role == "project_source"
    assert len(attachments.snapshot_files(chat_scope).ownerships) == 1
    assert attachments.snapshot_files(project_scope).ownerships == (promoted,)
    with pytest.raises(ProjectSourceStaleError, match="not fully indexed"):
        service.answer(chat.chat_id, "Index before use")
    attachment_store.close()


def test_promotion_rejects_same_bytes_owned_under_different_metadata(
    tmp_path: Path,
) -> None:
    """Do not report an indexable promotion using an existing image link."""

    project = create_project(name="Metadata collision")
    chats, project_scope = _owners(project, "Chat")
    chat_scope = AttachmentScope(kind="chat", id=str(chats[0].chat_id))
    attachment_store = JsonAttachmentStore(tmp_path / "files", 1_000_000)
    attachments = AttachmentService(attachment_store)
    image = tmp_path / "existing.png"
    text_file = tmp_path / "promoted.txt"
    image.write_bytes(b"same content")
    text_file.write_bytes(b"same content")
    attachments.stage_files(project_scope, (image,))
    state = attachments.stage_files(chat_scope, (text_file,))
    item = state.attachments[0]
    attachments.claim_chat(chat_scope, (item.attachment_id,))
    attachments.mark_committed(chat_scope, (item.attachment_id,))
    message = create_chat_message(
        role="user",
        content="Promote the text ownership.",
        attachments=(
            AttachmentMetadata(
                attachment_id=cast(AttachmentId, item.attachment_id),
                file_name=item.file_name,
                media_type=item.media_type,
                size_bytes=item.size_bytes,
            ),
        ),
    )
    chat = replace(
        chats[0],
        messages=(message,),
        updated_at=message.created_at,
    )
    lease = _Lease()
    service = _service(
        chats=(chat,),
        project=project,
        attachments=attachments,
        catalog=JsonProjectSourceRepository(tmp_path / "catalog"),
        lease=lease,
        grounder=_Grounder(lease),
        profile=_digest("profile"),
    )

    with pytest.raises(ProjectSourceConflictError, match="different metadata"):
        service.promote_chat_attachment(chat.chat_id, item.attachment_id)

    project_files = attachments.snapshot_files(project_scope)
    assert len(project_files.ownerships) == 1
    assert project_files.ownerships[0].media_type == "image/png"
    attachment_store.close()


def test_authority_change_during_grounding_discards_the_result(
    tmp_path: Path,
) -> None:
    """Reject publication when an injected lease fails to stabilize Project state."""

    project = create_project(name="Race")
    chats, scope = _owners(project, "Chat")
    attachment_store = JsonAttachmentStore(tmp_path / "files", 1_000_000)
    attachments = AttachmentService(attachment_store)
    file_catalog = _stage_sources(
        attachments,
        scope,
        tmp_path,
        "race.txt",
    )
    profile = _digest("profile")
    catalog = JsonProjectSourceRepository(tmp_path / "catalog")
    catalog.save_snapshot(_snapshot(file_catalog, profile), expected_revision=0)
    chat_repository = _ChatRepository(*chats)
    project_repository = _ProjectRepository(project)
    lease = _Lease()

    class _MutatingGrounder(_Grounder):
        """Archive the Project after producing an otherwise valid result."""

        def answer(self, *args: object, **kwargs: object) -> GroundedAnswerResult:
            """Mutate canonical authority before final revalidation."""

            result = super().answer(*args, **kwargs)  # type: ignore[arg-type]
            project_repository.projects[project.project_id] = replace(
                project,
                is_archived=True,
            )
            return result

    grounder = _MutatingGrounder(lease)
    service = ProjectSourceAnswerService(
        cast(Any, chat_repository),
        cast(Any, project_repository),
        attachments,
        catalog,
        cast(Any, grounder),
        lease,
        current_index_profile_fingerprint=profile,
    )

    with pytest.raises(ProjectSourceConflictError):
        service.answer(chats[0].chat_id, "Race authority")

    assert len(grounder.calls) == 1
    attachment_store.close()
