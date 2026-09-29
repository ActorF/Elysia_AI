"""Verify recoverable Project Source lifecycle ordering and safe export."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from threading import Barrier

import pytest

from attachments import (
    AttachmentScope,
    AttachmentService,
    FileCatalogSnapshot,
    JsonAttachmentStore,
)
from documents import (
    DOCUMENT_EMBEDDING_TEMPLATE_VERSION,
    DOCUMENT_SCHEMA_VERSION,
    EMBEDDING_SCHEMA_VERSION,
    QUERY_EMBEDDING_TEMPLATE_VERSION,
    ConservativeDocumentCleaner,
    DocumentBlock,
    DocumentEmbeddingService,
    DocumentLoadLimits,
    DocumentSource,
    EmbeddedDocument,
    EmbeddingBatch,
    EmbeddingBatchPolicy,
    EmbeddingModelIdentity,
    EmbeddingRequest,
    EmbeddingVector,
    LoadedDocument,
    StructureAwareDocumentChunker,
)
from knowledge_lifecycle import (
    KNOWLEDGE_OPERATION_SCHEMA_VERSION,
    JsonKnowledgeOperationRepository,
    KnowledgeExportService,
    KnowledgeLifecycleConflictError,
    KnowledgeLifecycleDataCorruptionError,
    KnowledgeLifecycleNotFoundError,
    KnowledgeLifecycleService,
    KnowledgeLifecycleStorageError,
    KnowledgeOperationId,
    KnowledgeOperationSnapshot,
    NoStoredKnowledgeArtifacts,
)
from project_sources import (
    PROJECT_SOURCE_INSTRUCTIONS_SCHEMA_VERSION,
    JsonProjectSourceRepository,
    ProjectSourceInstructions,
    ProjectSourceOperationCoordinator,
    ProjectSourceSnapshot,
)
from projects import (
    Project,
    ProjectId,
    ProjectNotFoundError,
    create_project,
)


_BASE_TIME = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
_PROFILE = hashlib.sha256(b"knowledge-lifecycle-profile").hexdigest()


def _digest(label: str) -> str:
    """Return one deterministic lowercase SHA-256 test fingerprint."""

    return hashlib.sha256(label.encode("utf-8")).hexdigest()


class _ProjectRepository:
    """Expose active canonical Projects without filesystem test noise."""

    def __init__(self, *projects: Project) -> None:
        """Index the supplied Projects by their exact opaque IDs."""

        self.projects = {project.project_id: project for project in projects}

    def add(self, project: Project) -> None:
        """Register another canonical Project for a scope-isolation test."""

        self.projects[project.project_id] = project

    def get_project(self, project_id: ProjectId) -> Project:
        """Return one Project or the same typed absence as production."""

        try:
            return self.projects[project_id]
        except KeyError:
            raise ProjectNotFoundError("Project does not exist.") from None


class _UnitTextEmbedder:
    """Return deterministic unit vectors for complete document fixtures."""

    def __init__(self) -> None:
        """Create one stable two-dimensional embedding space."""

        self._identity = EmbeddingModelIdentity(
            provider="ollama",
            adapter_id="lifecycle-test",
            adapter_version="1.0.0",
            model_tag="fixture:1",
            model_digest=_digest("embedding-model"),
            dimension=2,
            normalization="l2",
            document_template_version=DOCUMENT_EMBEDDING_TEMPLATE_VERSION,
            query_template_version=QUERY_EMBEDDING_TEMPLATE_VERSION,
        )
        self._policy = EmbeddingBatchPolicy()

    @property
    def identity(self) -> EmbeddingModelIdentity:
        """Return the immutable fixture embedding identity."""

        return self._identity

    @property
    def policy(self) -> EmbeddingBatchPolicy:
        """Return the bounded fixture batch policy."""

        return self._policy

    def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        """Return one ordered unit vector for each admitted request item."""

        return EmbeddingBatch(
            schema_version=EMBEDDING_SCHEMA_VERSION,
            identity=self._identity,
            policy=self._policy,
            purpose=request.purpose,
            vectors=tuple(
                EmbeddingVector(item.item_id, (1.0, 0.0))
                for item in request.items
            ),
        )


class _RecordingAttachmentService(AttachmentService):
    """Use the real attachment service while recording ownership deletion."""

    def __init__(
        self,
        repository: JsonAttachmentStore,
        events: list[tuple[str, str]],
    ) -> None:
        """Compose the real store and shared lifecycle event trace."""

        super().__init__(repository)
        self._events = events
        self.fail_remove_once: Exception | None = None

    def remove_project_source(
        self,
        scope: AttachmentScope,
        link_id: str,
        expected_snapshot_fingerprint: str,
    ) -> FileCatalogSnapshot:
        """Record the ownership commit while preserving real CAS behavior."""

        self._events.append(("ownership_remove", link_id))
        failure = self.fail_remove_once
        self.fail_remove_once = None
        if failure is not None:
            raise failure
        return super().remove_project_source(
            scope,
            link_id,
            expected_snapshot_fingerprint,
        )


class _RecordingProjectSourceRepository(JsonProjectSourceRepository):
    """Retain the real strict catalog while exposing publication ordering."""

    def __init__(
        self,
        storage_directory: Path,
        events: list[tuple[str, str]],
    ) -> None:
        """Configure real JSON persistence and a deterministic event trace."""

        super().__init__(storage_directory)
        self._events = events
        self.fail_save_once: Exception | None = None

    def save_snapshot(
        self,
        snapshot: ProjectSourceSnapshot,
        *,
        expected_revision: int,
    ) -> ProjectSourceSnapshot:
        """Record and optionally fault one real catalog publication."""

        self._events.append(("catalog_publish", snapshot.scope.id))
        failure = self.fail_save_once
        self.fail_save_once = None
        if failure is not None:
            raise failure
        return super().save_snapshot(
            snapshot,
            expected_revision=expected_revision,
        )

    def delete_snapshot(
        self,
        scope: AttachmentScope,
        *,
        expected_revision: int,
        instructions: ProjectSourceInstructions | None = None,
    ) -> int:
        """Record and perform one real catalog tombstone CAS."""

        self._events.append(("catalog_revoke", scope.id))
        return super().delete_snapshot(
            scope,
            expected_revision=expected_revision,
            instructions=instructions,
        )


class _RecordingArtifactCleanup(NoStoredKnowledgeArtifacts):
    """Record the explicit no-extra-artifacts cleanup boundary."""

    def __init__(self, events: list[tuple[str, str]]) -> None:
        """Retain the lifecycle event trace."""

        self._events = events

    def purge_document(
        self,
        scope: AttachmentScope,
        source: DocumentSource,
    ) -> None:
        """Record the call before confirming no separate artifacts exist."""

        self._events.append(("artifact_cleanup", source.link_id))
        super().purge_document(scope, source)


class _DeterministicIndexer:
    """Model exact vectors while delegating lineage to real domain services."""

    def __init__(
        self,
        attachments: AttachmentService,
        events: list[tuple[str, str]],
    ) -> None:
        """Create an empty scope-keyed vector map and fault controls."""

        self._attachments = attachments
        self._events = events
        self._embedder = DocumentEmbeddingService(_UnitTextEmbedder())
        self.vectors: dict[tuple[AttachmentScope, str], EmbeddedDocument] = {}
        self.fail_prepare_once: Exception | None = None
        self.fail_delete_once: Exception | None = None
        self.after_commit_once: Callable[[], None] | None = None

    def prepare_document(
        self,
        scope: AttachmentScope,
        link_id: str,
    ) -> EmbeddedDocument:
        """Prepare real immutable lineage without mutating the vector map."""

        self._events.append(("prepare", link_id))
        failure = self.fail_prepare_once
        self.fail_prepare_once = None
        if failure is not None:
            raise failure
        snapshot = self._attachments.snapshot_file(scope, link_id)
        ownership = snapshot.ownerships[0]
        original = snapshot.originals[0]
        source = DocumentSource(
            scope=scope,
            link_id=ownership.link_id,
            file_id=ownership.file_id,
            file_name=ownership.file_name,
            media_type=ownership.media_type,
            size_bytes=original.size_bytes,
        )
        loaded = LoadedDocument(
            schema_version=DOCUMENT_SCHEMA_VERSION,
            source=source,
            document_format="text",
            loader_id="knowledge-lifecycle-test",
            loader_version="1.0.0",
            blocks=(
                DocumentBlock(
                    0,
                    "paragraph",
                    f"fixture content for {ownership.file_name}",
                ),
            ),
            limits=DocumentLoadLimits(),
        )
        chunked = StructureAwareDocumentChunker().chunk(
            ConservativeDocumentCleaner().clean(loaded)
        )
        return self._embedder.embed_document(chunked)

    def commit_document(
        self,
        scope: AttachmentScope,
        document: EmbeddedDocument,
    ) -> None:
        """Atomically replace one exact in-memory generation."""

        assert document.source.scope == scope
        self._events.append(("vector_commit", document.source.link_id))
        self.vectors[(scope, document.source.link_id)] = document
        callback = self.after_commit_once
        self.after_commit_once = None
        if callback is not None:
            callback()

    def rebuild_scope(
        self,
        scope: AttachmentScope,
        link_ids: tuple[str, ...],
    ) -> tuple[EmbeddedDocument, ...]:
        """Prepare all sources before atomically replacing one scope map."""

        prepared = tuple(
            self.prepare_document(scope, link_id) for link_id in link_ids
        )
        self._events.append(("vector_rebuild", scope.id))
        retained = {
            key: value
            for key, value in self.vectors.items()
            if key[0] != scope
        }
        retained.update(
            {
                (scope, document.source.link_id): document
                for document in prepared
            }
        )
        self.vectors = retained
        return prepared

    def delete_document(
        self,
        scope: AttachmentScope,
        link_id: str,
    ) -> bool:
        """Delete one exact generation or raise one configured transient fault."""

        self._events.append(("vector_delete", link_id))
        failure = self.fail_delete_once
        self.fail_delete_once = None
        if failure is not None:
            raise failure
        return self.vectors.pop((scope, link_id), None) is not None


@dataclass(slots=True)
class _Harness:
    """Group real repositories and deterministic adapters for one test."""

    project: Project
    projects: _ProjectRepository
    scope: AttachmentScope
    store: JsonAttachmentStore
    attachments: _RecordingAttachmentService
    source_repository: _RecordingProjectSourceRepository
    operations: JsonKnowledgeOperationRepository
    indexer: _DeterministicIndexer
    lease: ProjectSourceOperationCoordinator
    service: KnowledgeLifecycleService
    events: list[tuple[str, str]]


def _build_harness(tmp_path: Path) -> _Harness:
    """Compose real JSON authorities around a deterministic vector adapter."""

    project = create_project(name="Knowledge Lifecycle")
    projects = _ProjectRepository(project)
    scope = AttachmentScope(kind="project", id=str(project.project_id))
    events: list[tuple[str, str]] = []
    store = JsonAttachmentStore(tmp_path / "attachments", 1_000_000)
    attachments = _RecordingAttachmentService(store, events)
    source_repository = _RecordingProjectSourceRepository(
        tmp_path / "project-sources",
        events,
    )
    operations = JsonKnowledgeOperationRepository(tmp_path / "operations")
    indexer = _DeterministicIndexer(attachments, events)
    lease = ProjectSourceOperationCoordinator()
    service = KnowledgeLifecycleService(
        projects,
        attachments,
        indexer,
        source_repository,
        operations,
        lease,
        _RecordingArtifactCleanup(events),
        current_index_profile_fingerprint=_PROFILE,
        supported_document_routes=frozenset({(".txt", "text/plain")}),
        clock=lambda: _BASE_TIME,
    )
    return _Harness(
        project=project,
        projects=projects,
        scope=scope,
        store=store,
        attachments=attachments,
        source_repository=source_repository,
        operations=operations,
        indexer=indexer,
        lease=lease,
        service=service,
        events=events,
    )


@pytest.fixture
def lifecycle_environment(tmp_path: Path) -> Iterator[_Harness]:
    """Yield one isolated lifecycle composition and release its process lock."""

    harness = _build_harness(tmp_path)
    try:
        yield harness
    finally:
        harness.store.close()


def _source_path(tmp_path: Path, name: str, text: str) -> Path:
    """Write one trusted native selection outside attachment storage."""

    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _operation(
    scope: AttachmentScope,
    *,
    token: str = "1",
    target_link_id: str | None = "attachment_journal_target",
    created_offset_seconds: int = 0,
) -> KnowledgeOperationSnapshot:
    """Build one valid revision-one operation for journal/view tests."""

    created_at = _BASE_TIME + timedelta(seconds=created_offset_seconds)
    return KnowledgeOperationSnapshot(
        schema_version=KNOWLEDGE_OPERATION_SCHEMA_VERSION,
        journal_revision=1,
        operation_id=KnowledgeOperationId(f"knowledge_{token * 32}"),
        scope=scope,
        kind="reindex",
        state="running",
        phase="preparing",
        progress_percent=0,
        catalog_revision=0,
        index_profile_fingerprint=_PROFILE,
        instructions=ProjectSourceInstructions(
            schema_version=PROJECT_SOURCE_INSTRUCTIONS_SCHEMA_VERSION
        ),
        attempt=1,
        created_at=created_at,
        updated_at=created_at,
        target_link_id=target_link_id,
        source_catalog_fingerprint=_digest("source-catalog"),
    )


def test_json_operation_journal_is_strict_cas_and_cancel_safe(
    tmp_path: Path,
) -> None:
    """Reject stale writers and malformed JSON while cancellation stays idempotent."""

    scope = AttachmentScope("project", "project_journal")
    storage = tmp_path / "journal"
    repository = JsonKnowledgeOperationRepository(storage)
    created = repository.create_operation(_operation(scope))
    advanced = replace(
        created,
        journal_revision=2,
        phase="indexing",
        progress_percent=30,
        updated_at=_BASE_TIME + timedelta(seconds=1),
    )

    assert repository.save_operation(advanced, expected_revision=1) == advanced
    with pytest.raises(KnowledgeLifecycleConflictError):
        repository.save_operation(advanced, expected_revision=1)

    cancelled = repository.request_cancel(created.operation_id)
    assert cancelled.state == "cancel_requested"
    assert cancelled.journal_revision == 3
    assert repository.request_cancel(created.operation_id) == cancelled

    journal = storage / "knowledge_operations.json"
    journal.write_text(
        '{"operations":[],"schema_version":1,"unexpected":true}\n',
        encoding="utf-8",
    )
    with pytest.raises(KnowledgeLifecycleDataCorruptionError):
        repository.list_operations()

    journal.write_text(
        '{"schema_version":1,"schema_version":1,"operations":[]}\n',
        encoding="utf-8",
    )
    with pytest.raises(KnowledgeLifecycleDataCorruptionError):
        repository.list_operations()


def test_two_repository_instances_admit_only_one_active_job_per_scope(
    tmp_path: Path,
) -> None:
    """Make same-Project active-operation admission atomic across instances."""

    storage = tmp_path / "shared-journal"
    repositories = (
        JsonKnowledgeOperationRepository(storage),
        JsonKnowledgeOperationRepository(storage),
    )
    scope = AttachmentScope("project", "project_atomic_active")
    operations = (
        _operation(scope, token="3"),
        _operation(scope, token="4"),
    )
    barrier = Barrier(2)

    def _create(index: int) -> str:
        barrier.wait(timeout=10)
        try:
            repositories[index].create_operation(operations[index])
            return "created"
        except KnowledgeLifecycleConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(_create, (0, 1)))

    assert sorted(outcomes) == ["conflict", "created"]
    assert len(repositories[0].list_recoverable()) == 1


def test_journal_rejects_terminal_incoherence_and_phase_regression(
    tmp_path: Path,
) -> None:
    """Enforce closed terminal semantics and monotonic durable phases."""

    repository = JsonKnowledgeOperationRepository(tmp_path / "state-journal")
    created = repository.create_operation(
        _operation(AttachmentScope("project", "project_state"), token="5")
    )
    with pytest.raises(KnowledgeLifecycleConflictError):
        repository.save_operation(
            replace(
                created,
                journal_revision=2,
                state="succeeded",
                phase="indexing",
                progress_percent=100,
            ),
            expected_revision=1,
        )

    indexing = repository.save_operation(
        replace(
            created,
            journal_revision=2,
            phase="indexing",
            progress_percent=30,
        ),
        expected_revision=1,
    )
    with pytest.raises(KnowledgeLifecycleConflictError):
        repository.save_operation(
            replace(
                indexing,
                journal_revision=3,
                phase="importing",
            ),
            expected_revision=2,
        )

    succeeded = repository.save_operation(
        replace(
            indexing,
            journal_revision=3,
            state="succeeded",
            phase="completed",
            progress_percent=100,
        ),
        expected_revision=2,
    )
    with pytest.raises(KnowledgeLifecycleConflictError):
        repository.save_operation(
            replace(succeeded, journal_revision=4),
            expected_revision=3,
        )


def test_journal_rejects_an_incoherent_initial_checkpoint(
    tmp_path: Path,
) -> None:
    """Prevent terminal or resumed-looking jobs from entering through create."""

    repository = JsonKnowledgeOperationRepository(tmp_path / "initial-state")
    malformed = replace(
        _operation(AttachmentScope("project", "project_initial"), token="6"),
        state="succeeded",
        phase="completed",
        progress_percent=100,
    )

    with pytest.raises(KnowledgeLifecycleConflictError):
        repository.create_operation(malformed)


def test_journal_load_rejects_two_recoverable_jobs_for_one_scope(
    tmp_path: Path,
) -> None:
    """Treat a disk journal with conflicting same-Project work as corrupt."""

    scope = AttachmentScope("project", "project_poisoned_active")
    first_directory = tmp_path / "first-active"
    second_directory = tmp_path / "second-active"
    JsonKnowledgeOperationRepository(first_directory).create_operation(
        _operation(scope, token="7")
    )
    JsonKnowledgeOperationRepository(second_directory).create_operation(
        _operation(scope, token="8")
    )
    first_document = json.loads(
        (first_directory / "knowledge_operations.json").read_text(
            encoding="utf-8"
        )
    )
    second_document = json.loads(
        (second_directory / "knowledge_operations.json").read_text(
            encoding="utf-8"
        )
    )
    first_document["operations"].extend(second_document["operations"])
    poisoned_directory = tmp_path / "poisoned-active"
    poisoned_directory.mkdir()
    (poisoned_directory / "knowledge_operations.json").write_text(
        json.dumps(first_document, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(KnowledgeLifecycleDataCorruptionError):
        JsonKnowledgeOperationRepository(poisoned_directory).list_operations()


def test_full_journal_evicts_oldest_terminal_but_keeps_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound history by evicting terminal work without losing active recovery."""

    monkeypatch.setattr("knowledge_lifecycle.repository.MAX_KNOWLEDGE_OPERATIONS", 3)
    repository = JsonKnowledgeOperationRepository(tmp_path / "bounded-journal")
    oldest = repository.create_operation(
        _operation(
            AttachmentScope("project", "project_oldest_terminal"),
            token="a",
            created_offset_seconds=0,
        )
    )
    oldest = repository.save_operation(
        replace(
            oldest,
            journal_revision=2,
            state="succeeded",
            phase="completed",
            progress_percent=100,
        ),
        expected_revision=1,
    )
    recoverable = repository.create_operation(
        _operation(
            AttachmentScope("project", "project_recoverable"),
            token="b",
            created_offset_seconds=1,
        )
    )
    newer_terminal = repository.create_operation(
        _operation(
            AttachmentScope("project", "project_newer_terminal"),
            token="c",
            created_offset_seconds=2,
        )
    )
    newer_terminal = repository.save_operation(
        replace(
            newer_terminal,
            journal_revision=2,
            state="succeeded",
            phase="completed",
            progress_percent=100,
        ),
        expected_revision=1,
    )
    newcomer = repository.create_operation(
        _operation(
            AttachmentScope("project", "project_newcomer"),
            token="d",
            created_offset_seconds=3,
        )
    )

    retained = repository.list_operations()
    retained_ids = {operation.operation_id for operation in retained}
    assert len(retained) == 3
    assert oldest.operation_id not in retained_ids
    assert recoverable.operation_id in retained_ids
    assert newer_terminal.operation_id in retained_ids
    assert newcomer.operation_id in retained_ids
    assert repository.list_recoverable() == (recoverable, newcomer)


def test_journal_creation_order_does_not_depend_on_equal_time_uuid_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Evict the first append when clocks tie instead of sorting opaque IDs."""

    monkeypatch.setattr("knowledge_lifecycle.repository.MAX_KNOWLEDGE_OPERATIONS", 2)
    repository = JsonKnowledgeOperationRepository(tmp_path / "ordered-journal")
    first = repository.create_operation(
        _operation(AttachmentScope("project", "project_first"), token="f")
    )
    first = repository.save_operation(
        replace(
            first,
            journal_revision=2,
            state="succeeded",
            phase="completed",
            progress_percent=100,
        ),
        expected_revision=1,
    )
    second = repository.create_operation(
        _operation(AttachmentScope("project", "project_second"), token="a")
    )
    second = repository.save_operation(
        replace(
            second,
            journal_revision=2,
            state="succeeded",
            phase="completed",
            progress_percent=100,
        ),
        expected_revision=1,
    )
    newcomer = repository.create_operation(
        _operation(AttachmentScope("project", "project_third"), token="b")
    )

    retained_ids = {
        operation.operation_id for operation in repository.list_operations()
    }
    assert first.operation_id not in retained_ids
    assert second.operation_id in retained_ids
    assert newcomer.operation_id in retained_ids


def test_add_commits_vector_before_catalog_publication(
    lifecycle_environment: _Harness,
    tmp_path: Path,
) -> None:
    """Keep a newly indexed generation unauthorized until vector commit succeeds."""

    harness = lifecycle_environment
    result = harness.service.add_project_source(
        harness.project.project_id,
        _source_path(tmp_path, "add.txt", "trusted add source"),
    )

    names = [event[0] for event in harness.events]
    assert names.index("vector_commit") < names.index("catalog_publish")
    assert result.state == "succeeded"
    assert result.staged_link_id is not None
    assert (
        harness.scope,
        result.staged_link_id,
    ) in harness.indexer.vectors
    entry = harness.source_repository.read_entry(harness.scope)
    assert entry.snapshot is not None
    assert tuple(
        item.generation.source.link_id for item in entry.snapshot.sources
    ) == (result.staged_link_id,)


def test_publish_does_not_absorb_an_unexpected_catalog_revision(
    lifecycle_environment: _Harness,
    tmp_path: Path,
) -> None:
    """Keep a concurrent catalog edit from being overwritten after commit."""

    harness = lifecycle_environment
    added = harness.service.add_project_source(
        harness.project.project_id,
        _source_path(tmp_path, "concurrent.txt", "concurrent source"),
    )
    assert added.staged_link_id is not None
    baseline = harness.source_repository.read_entry(harness.scope)
    assert baseline.snapshot is not None
    concurrent = ProjectSourceSnapshot(
        schema_version=baseline.snapshot.schema_version,
        scope=harness.scope,
        revision=baseline.revision + 1,
        source_catalog_fingerprint=(
            baseline.snapshot.source_catalog_fingerprint
        ),
        sources=baseline.snapshot.sources,
        instructions=ProjectSourceInstructions(
            schema_version=PROJECT_SOURCE_INSTRUCTIONS_SCHEMA_VERSION,
            answer_style="concise",
        ),
    )

    def _publish_concurrent_edit() -> None:
        """Advance the catalog after vector commit but before lifecycle publish."""

        harness.source_repository.save_snapshot(
            concurrent,
            expected_revision=baseline.revision,
        )

    harness.indexer.after_commit_once = _publish_concurrent_edit
    with pytest.raises(KnowledgeLifecycleConflictError):
        harness.service.reindex_project_source(
            harness.project.project_id,
            added.staged_link_id,
        )

    retained = harness.source_repository.read_entry(harness.scope)
    assert retained.snapshot is not None
    assert retained.revision == concurrent.revision
    assert retained.snapshot.instructions.answer_style == "concise"
    pending = harness.operations.list_recoverable()
    assert len(pending) == 1
    assert pending[0].state == "recovery_required"
    recovered = harness.service.recover_pending(harness.project.project_id)
    assert len(recovered) == 1
    assert recovered[0].state == "recovery_required"
    retained_after_recovery = harness.source_repository.read_entry(
        harness.scope
    )
    assert retained_after_recovery.snapshot is not None
    assert (
        retained_after_recovery.snapshot.instructions.answer_style
        == "concise"
    )


def test_reindex_recovery_replays_after_publish_checkpoint_loss(
    lifecycle_environment: _Harness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recover a catalog commit whose following journal checkpoint was lost."""

    harness = lifecycle_environment
    added = harness.service.add_project_source(
        harness.project.project_id,
        _source_path(tmp_path, "checkpoint.txt", "checkpoint source"),
    )
    assert added.staged_link_id is not None
    baseline = harness.source_repository.read_entry(harness.scope)
    assert baseline.snapshot is not None
    monkeypatch.setattr(
        harness.service,
        "_clock",
        lambda: _BASE_TIME + timedelta(seconds=1),
    )
    original_save = harness.operations.save_operation
    failed = False

    def _fail_post_publish_checkpoint(
        snapshot: KnowledgeOperationSnapshot,
        *,
        expected_revision: int,
    ) -> KnowledgeOperationSnapshot:
        """Lose exactly the checkpoint acknowledging the committed catalog."""

        nonlocal failed
        if (
            not failed
            and snapshot.phase == "publishing"
            and snapshot.catalog_revision > baseline.revision
        ):
            failed = True
            raise KnowledgeLifecycleStorageError(
                "simulated post-publication checkpoint loss"
            )
        return original_save(snapshot, expected_revision=expected_revision)

    monkeypatch.setattr(
        harness.operations,
        "save_operation",
        _fail_post_publish_checkpoint,
    )
    with pytest.raises(KnowledgeLifecycleStorageError):
        harness.service.reindex_project_source(
            harness.project.project_id,
            added.staged_link_id,
        )
    committed = harness.source_repository.read_entry(harness.scope)
    assert committed.snapshot is not None
    assert committed.revision == baseline.revision + 1

    recovered = harness.service.recover_pending(harness.project.project_id)

    assert len(recovered) == 1
    assert recovered[0].state == "succeeded"
    assert harness.source_repository.read_entry(
        harness.scope
    ).snapshot is not None


@pytest.mark.parametrize("kind", ("delete", "revoke"))
def test_recovery_requires_the_operation_specific_catalog_postcondition(
    lifecycle_environment: _Harness,
    tmp_path: Path,
    kind: str,
) -> None:
    """Do not call changed-but-live catalogs successful delete or revoke work."""

    harness = lifecycle_environment
    added = harness.service.add_project_source(
        harness.project.project_id,
        _source_path(tmp_path, f"{kind}.txt", f"{kind} source"),
    )
    assert added.staged_link_id is not None
    baseline = harness.source_repository.read_entry(harness.scope)
    assert baseline.snapshot is not None
    pending = replace(
        _operation(
            harness.scope,
            token=("a" if kind == "delete" else "b"),
            target_link_id=(
                added.staged_link_id if kind == "delete" else None
            ),
        ),
        kind=kind,
        catalog_revision=baseline.revision,
        instructions=baseline.snapshot.instructions,
        source_catalog_fingerprint=(
            baseline.snapshot.source_catalog_fingerprint
        ),
        catalog_snapshot_fingerprint=baseline.snapshot.snapshot_fingerprint,
    )
    harness.operations.create_operation(pending)
    changed = ProjectSourceSnapshot(
        schema_version=baseline.snapshot.schema_version,
        scope=harness.scope,
        revision=baseline.revision + 1,
        source_catalog_fingerprint=(
            baseline.snapshot.source_catalog_fingerprint
        ),
        sources=baseline.snapshot.sources,
        instructions=ProjectSourceInstructions(
            schema_version=PROJECT_SOURCE_INSTRUCTIONS_SCHEMA_VERSION,
            answer_style="detailed",
        ),
    )
    harness.source_repository.save_snapshot(
        changed,
        expected_revision=baseline.revision,
    )

    recovered = harness.service.recover_pending(harness.project.project_id)

    assert len(recovered) == 1
    assert recovered[0].state == "recovery_required"
    assert recovered[0].error_code == "recovery_failed"
    retained = harness.source_repository.read_entry(harness.scope)
    assert retained.snapshot is not None
    assert retained.snapshot.snapshot_fingerprint == changed.snapshot_fingerprint
    assert any(
        item.link_id == added.staged_link_id
        for item in harness.attachments.snapshot_files(
            harness.scope
        ).ownerships
    )


def test_recovery_rejects_an_index_profile_change(
    lifecycle_environment: _Harness,
    tmp_path: Path,
) -> None:
    """Require an explicit new rebuild instead of changing durable job intent."""

    harness = lifecycle_environment
    added = harness.service.add_project_source(
        harness.project.project_id,
        _source_path(tmp_path, "profile.txt", "profile source"),
    )
    assert added.staged_link_id is not None
    baseline = harness.source_repository.read_entry(harness.scope)
    assert baseline.snapshot is not None
    pending = replace(
        _operation(
            harness.scope,
            token="c",
            target_link_id=added.staged_link_id,
        ),
        catalog_revision=baseline.revision,
        instructions=baseline.snapshot.instructions,
        source_catalog_fingerprint=(
            baseline.snapshot.source_catalog_fingerprint
        ),
        catalog_snapshot_fingerprint=baseline.snapshot.snapshot_fingerprint,
    )
    harness.operations.create_operation(pending)
    changed_service = KnowledgeLifecycleService(
        harness.projects,
        harness.attachments,
        harness.indexer,
        harness.source_repository,
        harness.operations,
        harness.lease,
        _RecordingArtifactCleanup(harness.events),
        current_index_profile_fingerprint=_digest("changed-index-profile"),
        supported_document_routes=frozenset({(".txt", "text/plain")}),
        clock=lambda: _BASE_TIME,
    )
    harness.events.clear()

    recovered = changed_service.recover_pending(harness.project.project_id)

    assert len(recovered) == 1
    assert recovered[0].state == "failed"
    assert recovered[0].error_code == "index_profile_changed"
    assert not any(
        event[0] in {"prepare", "vector_commit", "catalog_publish"}
        for event in harness.events
    )


def test_reindex_cancellation_after_prepare_never_commits(
    lifecycle_environment: _Harness,
    tmp_path: Path,
) -> None:
    """Honor cancellation at the prepare/commit boundary without catalog drift."""

    harness = lifecycle_environment
    added = harness.service.add_project_source(
        harness.project.project_id,
        _source_path(tmp_path, "reindex.txt", "reindex source"),
    )
    assert added.staged_link_id is not None
    before = harness.source_repository.read_entry(harness.scope)
    old_vector = harness.indexer.vectors[(harness.scope, added.staged_link_id)]
    harness.events.clear()
    decisions = iter((False, True))

    def _cancel_after_prepare() -> bool:
        return next(decisions, True)

    result = harness.service.reindex_project_source(
        harness.project.project_id,
        added.staged_link_id,
        cancel_requested=_cancel_after_prepare,
    )

    assert result.state == "cancelled"
    assert [event[0] for event in harness.events] == ["prepare"]
    assert harness.source_repository.read_entry(harness.scope) == before
    assert (
        harness.indexer.vectors[(harness.scope, added.staged_link_id)]
        == old_vector
    )


def test_restart_honors_preparing_cancellation_before_source_selection(
    lifecycle_environment: _Harness,
) -> None:
    """Finish a durable early cancellation without inventing a staged source."""

    harness = lifecycle_environment
    files = harness.attachments.snapshot_files(harness.scope)
    pending = replace(
        _operation(harness.scope, token="e", target_link_id=None),
        kind="add",
        source_catalog_fingerprint=files.snapshot_fingerprint,
    )
    created = harness.operations.create_operation(pending)
    harness.operations.request_cancel(created.operation_id)
    harness.events.clear()

    recovered = harness.service.recover_pending(harness.project.project_id)

    assert len(recovered) == 1
    assert recovered[0].state == "cancelled"
    assert recovered[0].phase == "completed"
    assert recovered[0].error_code is None
    assert harness.events == []
    assert harness.attachments.snapshot_files(harness.scope).ownerships == ()
    assert harness.source_repository.read_entry(harness.scope).revision == 0


def test_recovery_retries_failed_precommit_rollback_without_indexing(
    lifecycle_environment: _Harness,
    tmp_path: Path,
) -> None:
    """Remove an orphaned staged source instead of replaying a failed prepare."""

    harness = lifecycle_environment
    harness.indexer.fail_prepare_once = RuntimeError("deterministic parse failure")
    harness.attachments.fail_remove_once = RuntimeError("transient manifest failure")

    with pytest.raises(KnowledgeLifecycleStorageError):
        harness.service.add_project_source(
            harness.project.project_id,
            _source_path(tmp_path, "rollback.txt", "cannot be prepared"),
        )

    pending = harness.operations.list_recoverable()
    assert len(pending) == 1
    assert pending[0].error_code == "rollback_cleanup_failed"
    assert pending[0].staged_link_id is not None
    harness.events.clear()

    recovered = harness.service.recover_pending(harness.project.project_id)

    assert recovered[-1].state == "failed"
    assert recovered[-1].error_code == "add_failed"
    assert "prepare" not in [event[0] for event in harness.events]
    assert harness.attachments.snapshot_files(harness.scope).ownerships == ()
    assert harness.indexer.vectors == {}
    assert harness.source_repository.read_entry(harness.scope).revision == 0


def test_failed_staged_cancel_cleanup_recovers_only_to_cancelled(
    lifecycle_environment: _Harness,
    tmp_path: Path,
) -> None:
    """Retry staged cleanup without ever committing vectors or authorization."""

    harness = lifecycle_environment
    harness.indexer.fail_delete_once = RuntimeError(
        r"C:\private\cancel-cleanup.sqlite3"
    )

    def _cancel_after_manifest_commit() -> bool:
        return bool(
            harness.attachments.snapshot_files(harness.scope).ownerships
        )

    interrupted = harness.service.add_project_source(
        harness.project.project_id,
        _source_path(tmp_path, "cancel-cleanup.txt", "cancel me"),
        cancel_requested=_cancel_after_manifest_commit,
    )

    assert interrupted.state == "recovery_required"
    assert interrupted.error_code == "cancel_cleanup_failed"
    assert interrupted.staged_link_id is not None
    assert "vector_commit" not in [event[0] for event in harness.events]
    assert "catalog_publish" not in [event[0] for event in harness.events]
    assert any(
        item.link_id == interrupted.staged_link_id
        for item in harness.attachments.snapshot_files(
            harness.scope
        ).ownerships
    )

    harness.events.clear()
    recovered = harness.service.recover_pending(harness.project.project_id)

    assert len(recovered) == 1
    assert recovered[0].state == "cancelled"
    assert recovered[0].phase == "completed"
    assert recovered[0].error_code is None
    names = [event[0] for event in harness.events]
    assert names == [
        "vector_delete",
        "artifact_cleanup",
        "ownership_remove",
    ]
    assert harness.attachments.snapshot_files(harness.scope).ownerships == ()
    assert harness.source_repository.read_entry(harness.scope).snapshot is None
    assert harness.operations.list_recoverable() == ()


def test_replace_tombstones_then_cleans_then_republishes(
    lifecycle_environment: _Harness,
    tmp_path: Path,
) -> None:
    """Make copy-on-write replacement unavailable during destructive cleanup."""

    harness = lifecycle_environment
    added = harness.service.add_project_source(
        harness.project.project_id,
        _source_path(tmp_path, "old.txt", "old source"),
    )
    assert added.staged_link_id is not None
    old_link = added.staged_link_id
    harness.events.clear()

    result = harness.service.replace_project_source(
        harness.project.project_id,
        old_link,
        _source_path(tmp_path, "new.txt", "new source"),
    )

    assert result.state == "succeeded"
    assert result.staged_link_id is not None
    new_link = result.staged_link_id
    names = [event[0] for event in harness.events]
    assert names.index("vector_commit") < names.index("catalog_revoke")
    assert names.index("catalog_revoke") < names.index("vector_delete")
    assert names.index("vector_delete") < names.index("artifact_cleanup")
    assert names.index("artifact_cleanup") < names.index("ownership_remove")
    assert names.index("ownership_remove") < names.index("catalog_publish")
    assert (harness.scope, old_link) not in harness.indexer.vectors
    assert (harness.scope, new_link) in harness.indexer.vectors
    ownership_links = {
        item.link_id
        for item in harness.attachments.snapshot_files(
            harness.scope
        ).ownerships
    }
    assert ownership_links == {new_link}
    entry = harness.source_repository.read_entry(harness.scope)
    assert entry.snapshot is not None
    assert tuple(
        item.generation.source.link_id for item in entry.snapshot.sources
    ) == (new_link,)


@pytest.mark.parametrize("kind", ("replace", "delete"))
def test_destructive_recovery_does_not_regress_from_publishing_to_cleaning(
    lifecycle_environment: _Harness,
    tmp_path: Path,
    kind: str,
) -> None:
    """Replay cleanup after a final catalog save fails at publishing phase."""

    harness = lifecycle_environment
    added = harness.service.add_project_source(
        harness.project.project_id,
        _source_path(tmp_path, f"old-{kind}.txt", "old source"),
    )
    assert added.staged_link_id is not None
    old_link = added.staged_link_id
    harness.source_repository.fail_save_once = RuntimeError(
        "simulated final catalog failure"
    )

    with pytest.raises(KnowledgeLifecycleStorageError):
        if kind == "replace":
            harness.service.replace_project_source(
                harness.project.project_id,
                old_link,
                _source_path(tmp_path, "replacement.txt", "replacement"),
            )
        else:
            harness.service.delete_project_source(
                harness.project.project_id,
                old_link,
            )

    pending = harness.operations.list_recoverable()
    assert len(pending) == 1
    assert pending[0].phase == "publishing"
    assert harness.source_repository.read_entry(harness.scope).snapshot is None

    recovered = harness.service.recover_pending(harness.project.project_id)

    assert len(recovered) == 1
    assert recovered[0].state == "succeeded"
    live = harness.source_repository.read_entry(harness.scope)
    assert live.snapshot is not None
    links = {
        item.generation.source.link_id for item in live.snapshot.sources
    }
    assert old_link not in links
    assert len(links) == (1 if kind == "replace" else 0)


def test_replace_from_unpublished_catalog_recovers_its_own_tombstone(
    lifecycle_environment: _Harness,
    tmp_path: Path,
) -> None:
    """Distinguish a saga-created first tombstone from an earlier revoke."""

    harness = lifecycle_environment
    harness.attachments.stage_files(
        harness.scope,
        (_source_path(tmp_path, "unpublished-old.txt", "old"),),
    )
    old_link = harness.attachments.snapshot_files(
        harness.scope
    ).ownerships[0].link_id
    harness.source_repository.fail_save_once = RuntimeError(
        "simulated final catalog failure"
    )

    with pytest.raises(KnowledgeLifecycleStorageError):
        harness.service.replace_project_source(
            harness.project.project_id,
            old_link,
            _source_path(tmp_path, "unpublished-new.txt", "new"),
        )

    pending = harness.operations.list_recoverable()
    assert len(pending) == 1
    assert pending[0].kind == "replace"
    assert pending[0].started_from_revoked_catalog is False
    assert pending[0].phase == "publishing"
    assert harness.source_repository.read_entry(harness.scope).revision == 1

    recovered = harness.service.recover_pending(harness.project.project_id)

    assert recovered[-1].state == "succeeded"
    live = harness.source_repository.read_entry(harness.scope)
    assert live.snapshot is not None
    links = {
        item.generation.source.link_id for item in live.snapshot.sources
    }
    assert old_link not in links
    assert links == {pending[0].staged_link_id}


def test_delete_cleanup_failure_leaves_tombstone_and_recovers(
    lifecycle_environment: _Harness,
    tmp_path: Path,
) -> None:
    """Keep deletion revoked and recoverable when post-revoke cleanup fails."""

    harness = lifecycle_environment
    added = harness.service.add_project_source(
        harness.project.project_id,
        _source_path(tmp_path, "delete.txt", "delete source"),
    )
    assert added.staged_link_id is not None
    link_id = added.staged_link_id
    secret = r"C:\private\vectors.sqlite3"
    harness.indexer.fail_delete_once = RuntimeError(secret)
    harness.events.clear()

    with pytest.raises(KnowledgeLifecycleStorageError) as caught:
        harness.service.delete_project_source(
            harness.project.project_id,
            link_id,
        )

    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None
    names = [event[0] for event in harness.events]
    assert names.index("catalog_revoke") < names.index("vector_delete")
    tombstone = harness.source_repository.read_entry(harness.scope)
    assert tombstone.snapshot is None
    failed = next(
        operation
        for operation in harness.operations.list_operations()
        if operation.kind == "delete"
    )
    assert failed.state == "recovery_required"
    assert failed.error_code == "delete_failed"
    journal_text = (
        tmp_path / "operations" / "knowledge_operations.json"
    ).read_text(encoding="utf-8")
    assert secret not in journal_text
    assert any(
        item.link_id == link_id
        for item in harness.attachments.snapshot_files(
            harness.scope
        ).ownerships
    )

    recovered = harness.service.recover_pending(harness.project.project_id)

    assert recovered[-1].state == "succeeded"
    live = harness.source_repository.read_entry(harness.scope)
    assert live.snapshot is not None
    assert live.snapshot.sources == ()
    assert harness.attachments.snapshot_files(harness.scope).ownerships == ()
    assert (harness.scope, link_id) not in harness.indexer.vectors


def test_whole_project_revoke_retains_bytes_and_rebuild_reauthorizes(
    lifecycle_environment: _Harness,
    tmp_path: Path,
) -> None:
    """Treat whole-corpus revoke as reversible without deleting ownership."""

    harness = lifecycle_environment
    first = harness.service.add_project_source(
        harness.project.project_id,
        _source_path(tmp_path, "first.txt", "first"),
    )
    second = harness.service.add_project_source(
        harness.project.project_id,
        _source_path(tmp_path, "second.txt", "second"),
    )
    links = {first.staged_link_id, second.staged_link_id}

    revoked = harness.service.revoke_project_sources(
        harness.project.project_id
    )

    assert revoked.state == "succeeded"
    assert harness.source_repository.read_entry(harness.scope).snapshot is None
    assert {
        item.link_id
        for item in harness.attachments.snapshot_files(
            harness.scope
        ).ownerships
    } == links
    assert {key[1] for key in harness.indexer.vectors if key[0] == harness.scope} == links
    assert {
        view.state
        for view in harness.service.list_project_sources(
            harness.project.project_id
        )
    } == {"revoked"}

    rebuilt = harness.service.rebuild_project_sources(
        harness.project.project_id
    )

    assert rebuilt.state == "succeeded"
    entry = harness.source_repository.read_entry(harness.scope)
    assert entry.snapshot is not None
    assert {
        item.generation.source.link_id for item in entry.snapshot.sources
    } == links
    assert {
        view.state
        for view in harness.service.list_project_sources(
            harness.project.project_id
        )
    } == {"ready"}


def test_revoke_before_first_publication_creates_a_durable_tombstone(
    lifecycle_environment: _Harness,
    tmp_path: Path,
) -> None:
    """Make an emergency revoke sticky even for an unindexed owned source."""

    harness = lifecycle_environment
    source = _source_path(tmp_path, "never-published.txt", "owned but unindexed")
    harness.attachments.stage_files(harness.scope, (source,))
    link_id = harness.attachments.snapshot_files(harness.scope).ownerships[0].link_id

    revoked = harness.service.revoke_project_sources(harness.project.project_id)

    tombstone = harness.source_repository.read_entry(harness.scope)
    assert revoked.state == "succeeded"
    assert tombstone.revision == 1
    assert tombstone.snapshot is None
    assert {
        view.state
        for view in harness.service.list_project_sources(
            harness.project.project_id
        )
    } == {"revoked"}
    with pytest.raises(KnowledgeLifecycleConflictError):
        harness.service.add_project_source(
            harness.project.project_id,
            _source_path(tmp_path, "blocked-after-revoke.txt", "blocked"),
        )

    rebuilt = harness.service.rebuild_project_sources(harness.project.project_id)

    assert rebuilt.state == "succeeded"
    live = harness.source_repository.read_entry(harness.scope)
    assert live.snapshot is not None
    assert {
        item.generation.source.link_id for item in live.snapshot.sources
    } == {link_id}


@pytest.mark.parametrize("kind", ("add", "replace", "reindex"))
def test_revoked_corpus_requires_explicit_rebuild_before_reauthorization(
    lifecycle_environment: _Harness,
    tmp_path: Path,
    kind: str,
) -> None:
    """Prevent ordinary mutations from silently undoing a Project revoke."""

    harness = lifecycle_environment
    added = harness.service.add_project_source(
        harness.project.project_id,
        _source_path(tmp_path, "revoked.txt", "revoked source"),
    )
    assert added.staged_link_id is not None
    harness.service.revoke_project_sources(harness.project.project_id)
    before = harness.attachments.snapshot_files(harness.scope)
    harness.events.clear()

    with pytest.raises(KnowledgeLifecycleConflictError):
        if kind == "add":
            harness.service.add_project_source(
                harness.project.project_id,
                _source_path(tmp_path, "blocked.txt", "blocked source"),
            )
        elif kind == "replace":
            harness.service.replace_project_source(
                harness.project.project_id,
                added.staged_link_id,
                _source_path(tmp_path, "blocked.txt", "blocked source"),
            )
        else:
            harness.service.reindex_project_source(
                harness.project.project_id,
                added.staged_link_id,
            )

    assert harness.source_repository.read_entry(harness.scope).snapshot is None
    assert harness.attachments.snapshot_files(harness.scope) == before
    assert harness.operations.list_recoverable() == ()
    assert harness.events == []


def test_delete_from_revoked_corpus_preserves_the_tombstone(
    lifecycle_environment: _Harness,
    tmp_path: Path,
) -> None:
    """Allow secure byte cleanup without reauthorizing retained sources."""

    harness = lifecycle_environment
    first = harness.service.add_project_source(
        harness.project.project_id,
        _source_path(tmp_path, "revoked-first.txt", "first"),
    )
    second = harness.service.add_project_source(
        harness.project.project_id,
        _source_path(tmp_path, "revoked-second.txt", "second"),
    )
    assert first.staged_link_id is not None
    assert second.staged_link_id is not None
    harness.service.revoke_project_sources(harness.project.project_id)

    deleted = harness.service.delete_project_source(
        harness.project.project_id,
        first.staged_link_id,
    )

    assert deleted.state == "succeeded"
    tombstone = harness.source_repository.read_entry(harness.scope)
    assert tombstone.snapshot is None
    retained_links = {
        item.link_id
        for item in harness.attachments.snapshot_files(
            harness.scope
        ).ownerships
    }
    assert retained_links == {second.staged_link_id}
    assert {
        view.state
        for view in harness.service.list_project_sources(
            harness.project.project_id
        )
    } == {"revoked"}


def test_revoked_delete_retains_pruned_policy_for_later_rebuild(
    lifecycle_environment: _Harness,
    tmp_path: Path,
) -> None:
    """Persist policy in the tombstone so deleted preferences cannot return."""

    harness = lifecycle_environment
    first = harness.service.add_project_source(
        harness.project.project_id,
        _source_path(tmp_path, "preferred-first.txt", "first"),
    )
    second = harness.service.add_project_source(
        harness.project.project_id,
        _source_path(tmp_path, "preferred-second.txt", "second"),
    )
    assert first.staged_link_id is not None
    assert second.staged_link_id is not None
    baseline = harness.source_repository.read_entry(harness.scope)
    assert baseline.snapshot is not None
    preferred = replace(
        baseline.snapshot,
        revision=baseline.revision + 1,
        instructions=ProjectSourceInstructions(
            schema_version=PROJECT_SOURCE_INSTRUCTIONS_SCHEMA_VERSION,
            preferred_source_link_ids=(first.staged_link_id,),
            answer_style="detailed",
        ),
        snapshot_fingerprint="",
    )
    harness.source_repository.save_snapshot(
        preferred,
        expected_revision=baseline.revision,
    )
    harness.service.revoke_project_sources(harness.project.project_id)

    harness.service.delete_project_source(
        harness.project.project_id,
        first.staged_link_id,
    )

    tombstone = harness.source_repository.read_entry(harness.scope)
    assert tombstone.snapshot is None
    assert tombstone.instructions.preferred_source_link_ids == ()
    assert tombstone.instructions.answer_style == "detailed"

    rebuilt = harness.service.rebuild_project_sources(harness.project.project_id)

    assert rebuilt.state == "succeeded"
    live = harness.source_repository.read_entry(harness.scope)
    assert live.snapshot is not None
    assert live.snapshot.instructions.preferred_source_link_ids == ()
    assert live.snapshot.instructions.answer_style == "detailed"
    assert {
        item.generation.source.link_id for item in live.snapshot.sources
    } == {second.staged_link_id}


def test_delete_recovery_ignores_profile_and_route_drift_without_restoring_target(
    lifecycle_environment: _Harness,
    tmp_path: Path,
) -> None:
    """Finish policy-independent cleanup and keep authorization revoked."""

    harness = lifecycle_environment
    added = harness.service.add_project_source(
        harness.project.project_id,
        _source_path(tmp_path, "delete-before-upgrade.txt", "delete me"),
    )
    assert added.staged_link_id is not None
    harness.indexer.fail_delete_once = RuntimeError("transient cleanup failure")
    with pytest.raises(KnowledgeLifecycleStorageError):
        harness.service.delete_project_source(
            harness.project.project_id,
            added.staged_link_id,
        )
    changed_service = KnowledgeLifecycleService(
        harness.projects,
        harness.attachments,
        harness.indexer,
        harness.source_repository,
        harness.operations,
        harness.lease,
        _RecordingArtifactCleanup(harness.events),
        current_index_profile_fingerprint=_digest("upgraded-profile"),
        supported_document_routes=frozenset({(".md", "text/markdown")}),
        clock=lambda: _BASE_TIME,
    )

    recovered = changed_service.recover_pending(harness.project.project_id)

    assert recovered[-1].state == "succeeded"
    tombstone = harness.source_repository.read_entry(harness.scope)
    assert tombstone.snapshot is None
    assert harness.attachments.snapshot_files(harness.scope).ownerships == ()

    rebuilt = changed_service.rebuild_project_sources(
        harness.project.project_id
    )

    assert rebuilt.state == "succeeded"
    live = harness.source_repository.read_entry(harness.scope)
    assert live.snapshot is not None
    assert live.snapshot.sources == ()


def test_recovery_rolls_forward_after_ambiguous_catalog_failure(
    lifecycle_environment: _Harness,
    tmp_path: Path,
) -> None:
    """Republish a committed staged vector without leaking adapter diagnostics."""

    harness = lifecycle_environment
    secret = r"C:\private\project-source-catalog.json"
    harness.source_repository.fail_save_once = RuntimeError(secret)

    with pytest.raises(KnowledgeLifecycleStorageError) as caught:
        harness.service.add_project_source(
            harness.project.project_id,
            _source_path(tmp_path, "recover.txt", "recover source"),
        )

    assert secret not in str(caught.value)
    pending = harness.operations.list_recoverable()
    assert len(pending) == 1
    assert pending[0].state == "recovery_required"
    assert pending[0].error_code == "add_failed"
    assert pending[0].staged_link_id is not None
    assert (harness.scope, pending[0].staged_link_id) in harness.indexer.vectors
    assert harness.source_repository.read_entry(harness.scope).snapshot is None

    recovered = harness.service.recover_pending(harness.project.project_id)

    assert recovered == (harness.operations.get_operation(pending[0].operation_id),)
    assert recovered[0].state == "succeeded"
    entry = harness.source_repository.read_entry(harness.scope)
    assert entry.snapshot is not None
    assert tuple(
        item.generation.source.link_id for item in entry.snapshot.sources
    ) == (pending[0].staged_link_id,)


def test_source_views_cover_processing_ready_stale_and_revoked(
    lifecycle_environment: _Harness,
    tmp_path: Path,
) -> None:
    """Project every authorization-health state from canonical store data."""

    harness = lifecycle_environment
    first_path = _source_path(tmp_path, "view-first.txt", "first view")
    harness.attachments.stage_files(harness.scope, (first_path,))
    first_link = harness.attachments.snapshot_files(
        harness.scope
    ).ownerships[0].link_id

    assert harness.service.list_project_sources(
        harness.project.project_id
    )[0].state == "unindexed"

    running = harness.operations.create_operation(
        _operation(harness.scope, token="2", target_link_id=first_link)
    )
    processing = harness.service.list_project_sources(
        harness.project.project_id
    )[0]
    assert processing.state == "processing"
    assert processing.operation_id == running.operation_id
    harness.operations.save_operation(
        replace(
            running,
            journal_revision=2,
            state="succeeded",
            phase="completed",
            progress_percent=100,
            updated_at=_BASE_TIME + timedelta(seconds=1),
        ),
        expected_revision=1,
    )

    harness.service.rebuild_project_sources(harness.project.project_id)
    assert harness.service.list_project_sources(
        harness.project.project_id
    )[0].state == "ready"

    harness.attachments.stage_files(
        harness.scope,
        (_source_path(tmp_path, "view-second.txt", "second view"),),
    )
    assert {
        view.state
        for view in harness.service.list_project_sources(
            harness.project.project_id
        )
    } == {"stale"}

    harness.service.revoke_project_sources(harness.project.project_id)
    assert {
        view.state
        for view in harness.service.list_project_sources(
            harness.project.project_id
        )
    } == {"revoked"}


def test_project_scopes_never_share_vectors_or_catalog_authority(
    lifecycle_environment: _Harness,
    tmp_path: Path,
) -> None:
    """Reject a foreign Project link while preserving its independent corpus."""

    harness = lifecycle_environment
    other = create_project(name="Other Knowledge")
    harness.projects.add(other)
    other_scope = AttachmentScope("project", str(other.project_id))
    first = harness.service.add_project_source(
        harness.project.project_id,
        _source_path(tmp_path, "scope-a.txt", "scope a"),
    )
    second = harness.service.add_project_source(
        other.project_id,
        _source_path(tmp_path, "scope-b.txt", "scope b"),
    )
    assert first.staged_link_id is not None
    assert second.staged_link_id is not None
    other_before = harness.source_repository.read_entry(other_scope)

    with pytest.raises(KnowledgeLifecycleNotFoundError):
        harness.service.delete_project_source(
            harness.project.project_id,
            second.staged_link_id,
        )

    assert harness.source_repository.read_entry(other_scope) == other_before
    assert (harness.scope, first.staged_link_id) in harness.indexer.vectors
    assert (other_scope, second.staged_link_id) in harness.indexer.vectors
    assert (harness.scope, second.staged_link_id) not in harness.indexer.vectors


def test_export_reads_verified_original_and_never_overwrites_by_default(
    lifecycle_environment: _Harness,
    tmp_path: Path,
) -> None:
    """Export stored verified bytes atomically and preserve an existing target."""

    harness = lifecycle_environment
    source = _source_path(tmp_path, "export-source.txt", "verified original")
    harness.attachments.stage_files(harness.scope, (source,))
    link_id = harness.attachments.snapshot_files(
        harness.scope
    ).ownerships[0].link_id
    source.write_text("changed after import", encoding="utf-8")
    exporter = KnowledgeExportService(
        harness.projects,
        harness.attachments,
        harness.lease,
    )
    destination = tmp_path / "exported.txt"

    result = exporter.export_original(
        harness.project.project_id,
        link_id,
        destination,
    )

    assert result.file_name == "export-source.txt"
    assert result.bytes_written == len(b"verified original")
    assert destination.read_text(encoding="utf-8") == "verified original"

    blocked = tmp_path / "blocked.txt"
    blocked.write_text("keep me", encoding="utf-8")
    with pytest.raises(KnowledgeLifecycleConflictError):
        exporter.export_original(
            harness.project.project_id,
            link_id,
            blocked,
        )
    assert blocked.read_text(encoding="utf-8") == "keep me"
    assert tuple(tmp_path.glob(".blocked.txt.*.export.tmp")) == ()
