"""Coordinate recoverable Project Source indexing and secure cleanup.

The lifecycle is a durable saga rather than a fictitious transaction across
attachment JSON, SQLite vectors, and the Project Source catalog.  The catalog
is the sole authorization surface: new generations are published last, while
destructive operations revoke the complete Project catalog before deleting
anything.  Every unsafe interruption therefore fails closed and can be
replayed from the path-private operation journal.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Final, Literal, Protocol, cast

from attachments import (
    AttachmentError,
    AttachmentImportCancelledError,
    AttachmentNotFoundError,
    AttachmentScope,
    AttachmentService,
    FileCatalogSnapshot,
)
from chats import ProjectId
from documents import (
    DocumentError,
    DocumentLoaderService,
    DocumentRoute,
    DocumentSource,
    EmbeddedDocument,
    ExpectedDocumentGeneration,
)
from project_sources import (
    PROJECT_SOURCE_GENERATION_SCHEMA_VERSION,
    PROJECT_SOURCE_SNAPSHOT_SCHEMA_VERSION,
    CatalogEntrySnapshot,
    ProjectSourceConflictError,
    ProjectSourceError,
    ProjectSourceGeneration,
    ProjectSourceInstructions,
    ProjectSourceNotFoundError,
    ProjectSourceRepository,
    ProjectSourceSnapshot,
    select_indexable_file_catalog,
    snapshot_file_catalog,
    sources_from_catalog,
)
from projects import Project
from projects.exceptions import ProjectNotFoundError

from .domain import (
    KNOWLEDGE_OPERATION_SCHEMA_VERSION,
    MAX_KNOWLEDGE_OPERATION_ATTEMPTS,
    KnowledgeOperationId,
    KnowledgeOperationKind,
    KnowledgeOperationPhase,
    KnowledgeOperationSnapshot,
    generate_knowledge_operation_id,
    validate_knowledge_operation_id,
)
from .exceptions import (
    KnowledgeLifecycleConflictError,
    KnowledgeLifecycleError,
    KnowledgeLifecycleNotFoundError,
    KnowledgeLifecycleRecoveryError,
    KnowledgeLifecycleStorageError,
    KnowledgeLifecycleValidationError,
)
from .repository import KnowledgeOperationRepository


KnowledgeSourceState = Literal[
    "ready",
    "unindexed",
    "stale",
    "revoked",
    "processing",
]

_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_STATES: Final = frozenset({"succeeded", "cancelled", "failed"})


class _ProjectReader(Protocol):
    """Read the canonical Project authority for lifecycle commands."""

    def get_project(self, project_id: ProjectId) -> Project:
        """Return one canonical Project or raise a typed not-found error."""

        ...


class _DocumentIndexer(Protocol):
    """Prepare, commit, rebuild, and delete exact document generations."""

    def prepare_document(
        self,
        scope: AttachmentScope,
        link_id: str,
    ) -> EmbeddedDocument:
        """Process and embed one source without mutating vector storage."""

        ...

    def commit_document(
        self,
        scope: AttachmentScope,
        document: EmbeddedDocument,
    ) -> None:
        """Atomically replace one exact vector generation."""

        ...

    def rebuild_scope(
        self,
        scope: AttachmentScope,
        link_ids: tuple[str, ...],
    ) -> tuple[EmbeddedDocument, ...]:
        """Atomically replace every vector generation in one scope."""

        ...

    def delete_document(
        self,
        scope: AttachmentScope,
        link_id: str,
    ) -> bool:
        """Delete chunks, vectors, and their stored lineage metadata."""

        ...


class _MutationLease(Protocol):
    """Serialize source mutations with answers and relationship changes."""

    def hold_mutation(self) -> AbstractContextManager[None]:
        """Hold the shared conservative Project Source mutation lease."""

        ...


class KnowledgeArtifactCleanup(Protocol):
    """Delete optional preview and cache artifacts for one exact source.

    Chunk, vector, and lineage metadata live in the vector store and are
    deleted through ``_DocumentIndexer``.  Attachment derived relations are
    removed with their final original ownership.  This boundary is reserved
    for independently persisted previews or caches when such adapters exist.
    """

    def purge_document(
        self,
        scope: AttachmentScope,
        source: DocumentSource,
    ) -> None:
        """Idempotently delete preview/cache artifacts for one generation."""

        ...


class NoStoredKnowledgeArtifacts:
    """Declare that the composition has no preview or cache repository.

    Callers must pass this adapter explicitly.  That makes adding a future
    preview/cache store a conscious composition change instead of silently
    leaving newly introduced artifacts outside deletion propagation.
    """

    def purge_document(
        self,
        scope: AttachmentScope,
        source: DocumentSource,
    ) -> None:
        """Confirm that no separately persisted artifact requires cleanup."""

        del scope, source


@dataclass(frozen=True, slots=True)
class KnowledgeSourceView:
    """Describe one Project Source and its catalog authorization state."""

    source: DocumentSource
    state: KnowledgeSourceState
    derivation_fingerprint: str | None = None
    index_profile_fingerprint: str | None = None
    published_at: datetime | None = None
    operation_id: KnowledgeOperationId | None = None

    def __post_init__(self) -> None:
        """Require exact path-private metadata and a coherent state payload."""

        if type(self.source) is not DocumentSource:
            raise KnowledgeLifecycleValidationError(
                "Knowledge Source metadata is invalid."
            )
        if self.source.scope.kind != "project":
            raise KnowledgeLifecycleValidationError(
                "Knowledge Source must belong to a Project."
            )
        if self.state not in (
            "ready",
            "unindexed",
            "stale",
            "revoked",
            "processing",
        ):
            raise KnowledgeLifecycleValidationError(
                "Knowledge Source state is invalid."
            )
        for value, field_name in (
            (self.derivation_fingerprint, "derivation_fingerprint"),
            (self.index_profile_fingerprint, "index_profile_fingerprint"),
        ):
            if value is not None and (
                type(value) is not str
                or _DIGEST_PATTERN.fullmatch(value) is None
            ):
                raise KnowledgeLifecycleValidationError(
                    f"{field_name} is invalid."
                )
        if self.published_at is not None and (
            type(self.published_at) is not datetime
            or self.published_at.tzinfo is None
            or self.published_at.utcoffset() is None
        ):
            raise KnowledgeLifecycleValidationError(
                "published_at must be timezone-aware."
            )
        if self.operation_id is not None:
            validate_knowledge_operation_id(self.operation_id)
        generation_values = (
            self.derivation_fingerprint,
            self.index_profile_fingerprint,
            self.published_at,
        )
        if self.state == "ready" and (
            any(value is None for value in generation_values)
            or self.operation_id is not None
        ):
            raise KnowledgeLifecycleValidationError(
                "A ready Knowledge Source requires one complete generation."
            )
        if self.state in ("unindexed", "revoked") and any(
            value is not None for value in generation_values
        ):
            raise KnowledgeLifecycleValidationError(
                "An unauthorized Knowledge Source cannot claim a generation."
            )
        if self.state == "processing" and self.operation_id is None:
            raise KnowledgeLifecycleValidationError(
                "A processing Knowledge Source requires an operation ID."
            )


class KnowledgeLifecycleService:
    """Run recoverable Project Source add, replace, index, revoke, and delete.

    Only Project Sources are handled here.  Chat Attachments keep their Chat
    ownership and never enter this corpus unless the separate explicit
    promotion authority first creates a Project Source ownership link.
    """

    def __init__(
        self,
        project_repository: _ProjectReader,
        attachment_service: AttachmentService,
        document_indexer: _DocumentIndexer,
        source_repository: ProjectSourceRepository,
        operation_repository: KnowledgeOperationRepository,
        mutation_lease: _MutationLease,
        artifact_cleanup: KnowledgeArtifactCleanup,
        *,
        current_index_profile_fingerprint: str,
        supported_document_routes: frozenset[DocumentRoute] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Compose every durable store behind one shared mutation lease."""

        dependencies = (
            project_repository,
            attachment_service,
            document_indexer,
            source_repository,
            operation_repository,
            mutation_lease,
            artifact_cleanup,
        )
        if any(dependency is None for dependency in dependencies):
            raise TypeError("Knowledge lifecycle dependencies are required.")
        if (
            type(current_index_profile_fingerprint) is not str
            or _DIGEST_PATTERN.fullmatch(
                current_index_profile_fingerprint
            )
            is None
        ):
            raise KnowledgeLifecycleValidationError(
                "Current index profile fingerprint is invalid."
            )
        routes = (
            DocumentLoaderService.default_supported_routes()
            if supported_document_routes is None
            else supported_document_routes
        )
        if type(routes) is not frozenset or not routes:
            raise KnowledgeLifecycleValidationError(
                "Supported document routes are invalid."
            )
        canonical_routes: set[DocumentRoute] = set()
        for route in routes:
            if (
                type(route) is not tuple
                or len(route) != 2
                or type(route[0]) is not str
                or type(route[1]) is not str
                or not route[0].startswith(".")
                or route[0] != route[0].casefold()
                or not route[1]
            ):
                raise KnowledgeLifecycleValidationError(
                    "Supported document route is invalid."
                )
            canonical_routes.add((route[0], route[1]))
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable or None.")

        self._project_repository = project_repository
        self._attachment_service = attachment_service
        self._document_indexer = document_indexer
        self._source_repository = source_repository
        self._operation_repository = operation_repository
        self._mutation_lease = mutation_lease
        self._artifact_cleanup = artifact_cleanup
        self._current_index_profile_fingerprint = (
            current_index_profile_fingerprint
        )
        self._supported_document_routes = frozenset(canonical_routes)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def list_project_sources(
        self,
        project_id: ProjectId,
    ) -> tuple[KnowledgeSourceView, ...]:
        """Return every indexable ownership with its authorization health."""

        scope = self._active_project_scope(project_id)
        with self._mutation_lease.hold_mutation():
            files = self._indexable_catalog(scope)
            sources = sources_from_catalog(files, scope)
            entry = self._read_catalog_entry(scope)
            active = {
                link_id: operation
                for operation in self._list_recoverable(scope)
                for link_id in (
                    operation.staged_link_id,
                    operation.target_link_id,
                )
                if link_id is not None
            }
            generations = (
                {}
                if entry.snapshot is None
                else {
                    item.generation.source.link_id: item
                    for item in entry.snapshot.sources
                }
            )
            catalog_exact = (
                entry.snapshot is not None
                and entry.snapshot.source_catalog_fingerprint
                == files.snapshot_fingerprint
            )
            views: list[KnowledgeSourceView] = []
            for source in sources:
                operation = active.get(source.link_id)
                generation = generations.get(source.link_id)
                if operation is not None:
                    state: KnowledgeSourceState = "processing"
                elif entry.snapshot is None:
                    state = "revoked" if entry.revision > 0 else "unindexed"
                elif (
                    not catalog_exact
                    or generation is None
                    or generation.generation.source != source
                    or generation.index_profile_fingerprint
                    != self._current_index_profile_fingerprint
                ):
                    state = "stale"
                else:
                    state = "ready"
                views.append(
                    KnowledgeSourceView(
                        source=source,
                        state=state,
                        derivation_fingerprint=(
                            None
                            if generation is None
                            else generation.generation.derivation_fingerprint
                        ),
                        index_profile_fingerprint=(
                            None
                            if generation is None
                            else generation.index_profile_fingerprint
                        ),
                        published_at=(
                            None
                            if generation is None
                            else generation.published_at
                        ),
                        operation_id=(
                            None
                            if operation is None
                            else operation.operation_id
                        ),
                    )
                )
            return tuple(views)

    def get_operation(
        self,
        operation_id: KnowledgeOperationId,
    ) -> KnowledgeOperationSnapshot:
        """Return one durable lifecycle checkpoint by opaque operation ID."""

        return self._operation_repository.get_operation(operation_id)

    def list_operations(
        self,
        project_id: ProjectId | None = None,
    ) -> tuple[KnowledgeOperationSnapshot, ...]:
        """List bounded operation history globally or for one active Project."""

        scope = (
            None
            if project_id is None
            else self._active_project_scope(project_id)
        )
        operations = self._operation_repository.list_operations()
        if scope is None:
            return operations
        return tuple(
            operation
            for operation in operations
            if operation.scope == scope
        )

    def request_cancel(
        self,
        operation_id: KnowledgeOperationId,
    ) -> KnowledgeOperationSnapshot:
        """Durably request cooperative cancellation at the next safe boundary."""

        return self._operation_repository.request_cancel(operation_id)

    def add_project_source(
        self,
        project_id: ProjectId,
        source_path: Path,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> KnowledgeOperationSnapshot:
        """Import, index, then publish one new Project Source generation."""

        if not isinstance(source_path, Path) or not source_path.is_absolute():
            raise KnowledgeLifecycleValidationError(
                "Project Source input must be an absolute Path."
            )
        self._validate_cancel_callback(cancel_requested)
        scope = self._active_project_scope(project_id)
        with self._mutation_lease.hold_mutation():
            entry = self._read_catalog_entry(scope)
            self._require_not_explicitly_revoked(entry)
            before = self._indexable_catalog(scope)
            job = self._create_operation(
                scope,
                "add",
                entry,
                before,
            )
            staged_link: str | None = None
            vector_may_be_committed = False
            try:
                job = self._checkpoint(
                    job,
                    phase="importing",
                    progress_percent=10,
                )
                before_all = snapshot_file_catalog(
                    self._attachment_service.snapshot_files(scope)
                )
                self._attachment_service.stage_files(
                    scope,
                    (source_path,),
                    cancel_requested=lambda: self._cancel_now(
                        job.operation_id,
                        cancel_requested,
                    ),
                )
                after_all = snapshot_file_catalog(
                    self._attachment_service.snapshot_files(scope)
                )
                staged_link = self._single_new_link(before_all, after_all)
                after = select_indexable_file_catalog(
                    after_all,
                    self._supported_document_routes,
                )
                if staged_link not in {
                    item.link_id for item in after.ownerships
                }:
                    raise KnowledgeLifecycleValidationError(
                        "Selected file is not a supported document source."
                    )
                job = self._checkpoint(
                    job,
                    staged_link_id=staged_link,
                    progress_percent=20,
                )
                job = self._refresh_operation(job)
                if self._cancel_now(job.operation_id, cancel_requested):
                    return self._cancel_staged(job)

                job = self._checkpoint(
                    job,
                    phase="indexing",
                    progress_percent=35,
                )
                embedded = self._document_indexer.prepare_document(
                    scope,
                    staged_link,
                )
                job = self._refresh_operation(job)
                if self._cancel_now(job.operation_id, cancel_requested):
                    return self._cancel_staged(job)
                vector_may_be_committed = True
                self._document_indexer.commit_document(scope, embedded)
                generation = self._generation(embedded)
                job, snapshot = self._publish_complete_catalog(
                    job,
                    instructions=job.instructions,
                    overrides={staged_link: generation},
                    fallback_sources=(
                        () if entry.snapshot is None else entry.snapshot.sources
                    ),
                )
                return self._succeed(job, snapshot)
            except AttachmentImportCancelledError:
                return self._cancel_without_side_effect(job)
            except KnowledgeLifecycleError:
                rollback_completed = staged_link is None
                if staged_link is not None and not vector_may_be_committed:
                    rollback_completed = self._rollback_staged_quietly(
                        scope,
                        staged_link,
                    )
                self._fail_operation(
                    job,
                    recoverable=(
                        vector_may_be_committed or not rollback_completed
                    ),
                    error_code=(
                        "rollback_cleanup_failed"
                        if staged_link is not None
                        and not vector_may_be_committed
                        and not rollback_completed
                        else "add_failed"
                    ),
                )
                raise
            except Exception:
                rollback_completed = staged_link is None
                if staged_link is not None and not vector_may_be_committed:
                    rollback_completed = self._rollback_staged_quietly(
                        scope,
                        staged_link,
                    )
                self._fail_operation(
                    job,
                    recoverable=(
                        vector_may_be_committed or not rollback_completed
                    ),
                    error_code=(
                        "rollback_cleanup_failed"
                        if staged_link is not None
                        and not vector_may_be_committed
                        and not rollback_completed
                        else "add_failed"
                    ),
                )
                raise KnowledgeLifecycleStorageError(
                    "Project Source add failed safely."
                ) from None

    def replace_project_source(
        self,
        project_id: ProjectId,
        link_id: str,
        source_path: Path,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> KnowledgeOperationSnapshot:
        """Copy-on-write replace one source, then republish the full corpus."""

        if not isinstance(source_path, Path) or not source_path.is_absolute():
            raise KnowledgeLifecycleValidationError(
                "Project Source input must be an absolute Path."
            )
        self._validate_cancel_callback(cancel_requested)
        scope = self._active_project_scope(project_id)
        with self._mutation_lease.hold_mutation():
            entry = self._read_catalog_entry(scope)
            self._require_not_explicitly_revoked(entry)
            before = self._indexable_catalog(scope)
            self._require_source(before, scope, link_id)
            job = self._create_operation(
                scope,
                "replace",
                entry,
                before,
                target_link_id=link_id,
            )
            staged_link: str | None = None
            point_of_no_return = False
            try:
                job = self._checkpoint(
                    job,
                    phase="importing",
                    progress_percent=10,
                )
                before_all = snapshot_file_catalog(
                    self._attachment_service.snapshot_files(scope)
                )
                self._attachment_service.stage_files(
                    scope,
                    (source_path,),
                    cancel_requested=lambda: self._cancel_now(
                        job.operation_id,
                        cancel_requested,
                    ),
                )
                after_all = snapshot_file_catalog(
                    self._attachment_service.snapshot_files(scope)
                )
                staged_link = self._single_new_link(before_all, after_all)
                after = select_indexable_file_catalog(
                    after_all,
                    self._supported_document_routes,
                )
                if staged_link not in {
                    item.link_id for item in after.ownerships
                }:
                    raise KnowledgeLifecycleValidationError(
                        "Selected file is not a supported document source."
                    )
                job = self._checkpoint(
                    job,
                    staged_link_id=staged_link,
                    phase="indexing",
                    progress_percent=30,
                )
                embedded = self._document_indexer.prepare_document(
                    scope,
                    staged_link,
                )
                job = self._refresh_operation(job)
                if self._cancel_now(job.operation_id, cancel_requested):
                    return self._cancel_staged(job)
                self._document_indexer.commit_document(scope, embedded)
                generation = self._generation(embedded)
                # The staged vector is still unauthorized and can be removed
                # safely if cancellation won before the catalog tombstone.
                if self._cancel_now(job.operation_id, cancel_requested):
                    return self._cancel_staged(job)

                point_of_no_return = True
                instructions = self._replace_preference(
                    job.instructions,
                    link_id,
                    staged_link,
                )
                job = self._revoke_catalog(job, instructions=instructions)
                job = self._checkpoint_cleaning(job)
                self._cleanup_source(scope, link_id)
                job, snapshot = self._publish_complete_catalog(
                    job,
                    instructions=instructions,
                    overrides={staged_link: generation},
                    fallback_sources=(
                        () if entry.snapshot is None else entry.snapshot.sources
                    ),
                )
                return self._succeed(job, snapshot)
            except AttachmentImportCancelledError:
                return self._cancel_without_side_effect(job)
            except KnowledgeLifecycleError:
                rollback_completed = staged_link is None
                if staged_link is not None and not point_of_no_return:
                    rollback_completed = self._rollback_staged_quietly(
                        scope,
                        staged_link,
                    )
                self._fail_operation(
                    job,
                    recoverable=(point_of_no_return or not rollback_completed),
                    error_code=(
                        "rollback_cleanup_failed"
                        if staged_link is not None
                        and not point_of_no_return
                        and not rollback_completed
                        else "replace_failed"
                    ),
                )
                raise
            except Exception:
                rollback_completed = staged_link is None
                if staged_link is not None and not point_of_no_return:
                    rollback_completed = self._rollback_staged_quietly(
                        scope,
                        staged_link,
                    )
                self._fail_operation(
                    job,
                    recoverable=(point_of_no_return or not rollback_completed),
                    error_code=(
                        "rollback_cleanup_failed"
                        if staged_link is not None
                        and not point_of_no_return
                        and not rollback_completed
                        else "replace_failed"
                    ),
                )
                raise KnowledgeLifecycleStorageError(
                    "Project Source replacement failed safely."
                ) from None

    def reindex_project_source(
        self,
        project_id: ProjectId,
        link_id: str,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> KnowledgeOperationSnapshot:
        """Recompute one generation and publish it only after vector commit."""

        self._validate_cancel_callback(cancel_requested)
        scope = self._active_project_scope(project_id)
        with self._mutation_lease.hold_mutation():
            entry = self._read_catalog_entry(scope)
            self._require_not_explicitly_revoked(entry)
            files = self._indexable_catalog(scope)
            self._require_source(files, scope, link_id)
            job = self._create_operation(
                scope,
                "reindex",
                entry,
                files,
                target_link_id=link_id,
            )
            vector_may_be_committed = False
            try:
                if self._cancel_now(job.operation_id, cancel_requested):
                    return self._cancel_without_side_effect(job)
                job = self._checkpoint(
                    job,
                    phase="indexing",
                    progress_percent=30,
                )
                embedded = self._document_indexer.prepare_document(
                    scope,
                    link_id,
                )
                if self._cancel_now(job.operation_id, cancel_requested):
                    return self._cancel_without_side_effect(job)
                vector_may_be_committed = True
                self._document_indexer.commit_document(scope, embedded)
                job, snapshot = self._publish_complete_catalog(
                    job,
                    instructions=job.instructions,
                    overrides={link_id: self._generation(embedded)},
                    fallback_sources=(
                        () if entry.snapshot is None else entry.snapshot.sources
                    ),
                )
                return self._succeed(job, snapshot)
            except KnowledgeLifecycleError:
                self._fail_operation(
                    job,
                    recoverable=vector_may_be_committed,
                    error_code="reindex_failed",
                )
                raise
            except Exception:
                self._fail_operation(
                    job,
                    recoverable=vector_may_be_committed,
                    error_code="reindex_failed",
                )
                raise KnowledgeLifecycleStorageError(
                    "Project Source reindex failed safely."
                ) from None

    def rebuild_project_sources(
        self,
        project_id: ProjectId,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> KnowledgeOperationSnapshot:
        """Atomically rebuild vectors for the complete current Project corpus."""

        self._validate_cancel_callback(cancel_requested)
        scope = self._active_project_scope(project_id)
        with self._mutation_lease.hold_mutation():
            entry = self._read_catalog_entry(scope)
            files = self._indexable_catalog(scope)
            sources = sources_from_catalog(files, scope)
            job = self._create_operation(
                scope,
                "rebuild",
                entry,
                files,
            )
            vector_may_be_committed = False
            try:
                if self._cancel_now(job.operation_id, cancel_requested):
                    return self._cancel_without_side_effect(job)
                job = self._checkpoint(
                    job,
                    phase="indexing",
                    progress_percent=30,
                )
                vector_may_be_committed = True
                embedded = self._document_indexer.rebuild_scope(
                    scope,
                    tuple(source.link_id for source in sources),
                )
                overrides = {
                    document.source.link_id: self._generation(document)
                    for document in embedded
                }
                job, snapshot = self._publish_complete_catalog(
                    job,
                    instructions=job.instructions,
                    overrides=overrides,
                    fallback_sources=(),
                )
                return self._succeed(job, snapshot)
            except KnowledgeLifecycleError:
                self._fail_operation(
                    job,
                    recoverable=vector_may_be_committed,
                    error_code="rebuild_failed",
                )
                raise
            except Exception:
                self._fail_operation(
                    job,
                    recoverable=vector_may_be_committed,
                    error_code="rebuild_failed",
                )
                raise KnowledgeLifecycleStorageError(
                    "Project Source rebuild failed safely."
                ) from None

    def revoke_project_sources(
        self,
        project_id: ProjectId,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> KnowledgeOperationSnapshot:
        """Revoke the complete Project corpus while retaining owned bytes.

        The exact-corpus authorization model cannot disable one source while
        retaining its Project Source ownership.  This operation is therefore
        an explicit whole-Project emergency stop.  ``rebuild_project_sources``
        can later reauthorize every still-owned source.
        """

        self._validate_cancel_callback(cancel_requested)
        scope = self._active_project_scope(project_id)
        with self._mutation_lease.hold_mutation():
            entry = self._read_catalog_entry(scope)
            files = self._indexable_catalog(scope)
            job = self._create_operation(
                scope,
                "revoke",
                entry,
                files,
            )
            try:
                if self._cancel_now(job.operation_id, cancel_requested):
                    return self._cancel_without_side_effect(job)
                job = self._revoke_catalog(job)
                return self._succeed(job, None)
            except KnowledgeLifecycleError:
                self._fail_operation(
                    job,
                    recoverable=True,
                    error_code="revoke_failed",
                )
                raise
            except Exception:
                self._fail_operation(
                    job,
                    recoverable=True,
                    error_code="revoke_failed",
                )
                raise KnowledgeLifecycleStorageError(
                    "Project Source revocation failed safely."
                ) from None

    def delete_project_source(
        self,
        project_id: ProjectId,
        link_id: str,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> KnowledgeOperationSnapshot:
        """Revoke first, then propagate one source deletion to every store."""

        self._validate_cancel_callback(cancel_requested)
        scope = self._active_project_scope(project_id)
        with self._mutation_lease.hold_mutation():
            entry = self._read_catalog_entry(scope)
            files = self._indexable_catalog(scope)
            self._require_source(files, scope, link_id)
            job = self._create_operation(
                scope,
                "delete",
                entry,
                files,
                target_link_id=link_id,
            )
            point_of_no_return = False
            try:
                if self._cancel_now(job.operation_id, cancel_requested):
                    return self._cancel_without_side_effect(job)
                point_of_no_return = True
                job = self._revoke_catalog(job)
                job = self._checkpoint_cleaning(job)
                self._cleanup_source(scope, link_id)
                if job.catalog_snapshot_fingerprint is None:
                    return self._succeed(job, None)
                job, snapshot = self._publish_complete_catalog(
                    job,
                    instructions=job.instructions,
                    overrides={},
                    fallback_sources=(
                        () if entry.snapshot is None else entry.snapshot.sources
                    ),
                )
                return self._succeed(job, snapshot)
            except KnowledgeLifecycleError:
                self._fail_operation(
                    job,
                    recoverable=point_of_no_return,
                    error_code="delete_failed",
                )
                raise
            except Exception:
                self._fail_operation(
                    job,
                    recoverable=point_of_no_return,
                    error_code="delete_failed",
                )
                raise KnowledgeLifecycleStorageError(
                    "Project Source deletion failed safely."
                ) from None

    def recover_pending(
        self,
        project_id: ProjectId | None = None,
    ) -> tuple[KnowledgeOperationSnapshot, ...]:
        """Replay bounded nonterminal operations from canonical store state.

        Recovery trusts current ownership, vectors, and catalog state instead
        of assuming that the last journal write happened immediately before a
        crash.  Operations at or beyond the indexing phase roll forward; that
        conservative rule covers an ambiguous SQLite commit whose caller did
        not live long enough to checkpoint it.
        """

        scope = (
            None
            if project_id is None
            else self._active_project_scope(project_id)
        )
        recovered: list[KnowledgeOperationSnapshot] = []
        pending = self._operation_repository.list_recoverable()
        if scope is not None:
            pending = tuple(
                operation
                for operation in pending
                if operation.scope == scope
            )
        for raw in pending:
            with self._mutation_lease.hold_mutation():
                current = self._refresh_operation(raw)
                if current.state in _TERMINAL_STATES:
                    recovered.append(current)
                    continue
                if current.attempt >= MAX_KNOWLEDGE_OPERATION_ATTEMPTS:
                    recovered.append(
                        self._fail_operation(
                            current,
                            recoverable=False,
                            error_code="recovery_exhausted",
                        )
                    )
                    continue
                if current.error_code == "cancel_cleanup_failed":
                    current = self._checkpoint(
                        current,
                        state="cancel_requested",
                        attempt=current.attempt + 1,
                    )
                    recovered.append(self._cancel_staged(current))
                    continue
                if current.error_code == "rollback_cleanup_failed":
                    current = self._checkpoint(
                        current,
                        state="recovery_required",
                        attempt=current.attempt + 1,
                        error_code=None,
                    )
                    recovered.append(self._recover_failed_rollback(current))
                    continue
                recovery_state = (
                    "cancel_requested"
                    if current.state == "cancel_requested"
                    else "recovery_required"
                )
                current = self._checkpoint(
                    current,
                    state=recovery_state,
                    attempt=current.attempt + 1,
                    error_code=None,
                )
                try:
                    recovered.append(self._resume_operation(current))
                except Exception:
                    recovered.append(
                        self._fail_operation(
                            current,
                            recoverable=True,
                            error_code="recovery_failed",
                        )
                    )
        return tuple(recovered)

    def _resume_operation(
        self,
        job: KnowledgeOperationSnapshot,
    ) -> KnowledgeOperationSnapshot:
        """Idempotently roll one durable operation toward a safe terminal state."""

        scope = self._active_project_scope(ProjectId(job.scope.id))
        entry = self._read_catalog_entry(scope)
        files = self._indexable_catalog(scope)
        staged = job.staged_link_id
        profile_changed = (
            job.index_profile_fingerprint
            != self._current_index_profile_fingerprint
        )
        if job.state == "cancel_requested" and job.phase == "preparing":
            return self._cancel_without_side_effect(job)
        if job.kind in ("add", "replace") and staged is None:
            return self._fail_operation(
                job,
                recoverable=False,
                error_code="source_selection_lost",
            )
        # A persisted cancellation at a pre-publication boundary has priority
        # over profile migration: staged ownership must not survive merely
        # because the application upgraded before restart.
        if job.state == "cancel_requested" and job.phase == "importing":
            if staged is not None:
                return self._cancel_staged(job)
            return self._cancel_without_side_effect(job)
        if profile_changed and job.kind == "add" and staged is not None:
            if (
                entry.revision > job.catalog_revision
                and self._catalog_matches_profile(
                    entry,
                    files,
                    job.index_profile_fingerprint,
                    job.instructions,
                )
                and entry.snapshot is not None
                and self._operation_postcondition_satisfied(job, entry.snapshot)
            ):
                synchronized = self._checkpoint(
                    job,
                    catalog_revision=entry.revision,
                )
                return self._succeed(synchronized, entry.snapshot)
            if entry.snapshot is not None and any(
                item.generation.source.link_id == staged
                for item in entry.snapshot.sources
            ):
                return self._fail_operation(
                    job,
                    recoverable=False,
                    error_code="index_profile_changed",
                )
            return self._fail_after_staged_rollback(job)
        if profile_changed and job.kind in {"reindex", "rebuild"}:
            # Recovery must not silently execute immutable operation intent
            # with a different embedding/index contract. Any partially
            # committed old generation remains fail-closed; a new explicit
            # rebuild can migrate the current corpus.
            return self._fail_operation(
                job,
                recoverable=False,
                error_code="index_profile_changed",
            )
        if (
            job.kind in ("add", "replace", "reindex")
            and job.started_from_revoked_catalog
        ):
            return self._fail_operation(
                job,
                recoverable=False,
                error_code="catalog_revoked",
            )
        if self._catalog_is_current(entry, files):
            current_snapshot = cast(ProjectSourceSnapshot, entry.snapshot)
            if self._operation_postcondition_satisfied(job, current_snapshot):
                return self._succeed(job, current_snapshot)
            if (
                job.kind in ("reindex", "rebuild")
                and current_snapshot.source_catalog_fingerprint
                == job.source_catalog_fingerprint
                and current_snapshot.instructions == job.instructions
                and entry.revision > job.catalog_revision
            ):
                # Reindex/rebuild cannot prove that a changed catalog contains
                # the vector commit from this job: ownership and profile may
                # be unchanged while only Instructions advanced. When the
                # complete non-vector intent is identical, adopt the newer
                # revision and replay the idempotent indexing work instead of
                # claiming success from catalog shape alone.
                job = self._checkpoint(
                    job,
                    catalog_revision=entry.revision,
                )

        if job.kind == "revoke":
            return self._succeed(self._revoke_catalog(job), None)
        if job.kind == "delete":
            target_link_id = job.target_link_id
            if target_link_id is None:
                raise KnowledgeLifecycleRecoveryError(
                    "Delete recovery has no target source."
                )
            job = self._revoke_catalog(job)
            job = self._checkpoint_cleaning(job)
            self._cleanup_source(scope, target_link_id)
            if profile_changed or job.catalog_snapshot_fingerprint is None:
                return self._succeed(job, None)
            job, snapshot = self._publish_complete_catalog(
                job,
                instructions=job.instructions,
                overrides={},
                fallback_sources=(),
            )
            return self._succeed(job, snapshot)
        if job.kind == "replace":
            target_link_id = job.target_link_id
            if target_link_id is None or staged is None:
                raise KnowledgeLifecycleRecoveryError(
                    "Replacement recovery has incomplete source identity."
                )
            instructions = self._replace_preference(
                job.instructions,
                target_link_id,
                staged,
            )
            if profile_changed and entry.snapshot is not None:
                if (
                    entry.revision > job.catalog_revision
                    and self._catalog_matches_profile(
                        entry,
                        files,
                        job.index_profile_fingerprint,
                        instructions,
                    )
                    and self._operation_postcondition_satisfied(
                        job,
                        entry.snapshot,
                    )
                ):
                    synchronized = self._checkpoint(
                        job,
                        catalog_revision=entry.revision,
                    )
                    return self._succeed(synchronized, entry.snapshot)
                if any(
                    item.generation.source.link_id == staged
                    for item in entry.snapshot.sources
                ):
                    return self._fail_operation(
                        job,
                        recoverable=False,
                        error_code="index_profile_changed",
                    )
                return self._fail_after_staged_rollback(job)
            if profile_changed:
                # The old source may already be revoked. Complete its durable
                # destructive intent without creating vectors under a changed
                # profile, and leave the corpus tombstoned for explicit rebuild.
                job = self._revoke_catalog(job, instructions=instructions)
                job = self._checkpoint_cleaning(job)
                self._cleanup_source(scope, target_link_id)
                return self._succeed(job, None)
            embedded = self._document_indexer.prepare_document(scope, staged)
            self._document_indexer.commit_document(scope, embedded)
            job = self._revoke_catalog(job, instructions=instructions)
            job = self._checkpoint_cleaning(job)
            self._cleanup_source(scope, target_link_id)
            job, snapshot = self._publish_complete_catalog(
                job,
                instructions=instructions,
                overrides={staged: self._generation(embedded)},
                fallback_sources=(),
            )
            return self._succeed(job, snapshot)
        if job.kind == "add":
            if staged is None:
                raise KnowledgeLifecycleRecoveryError(
                    "Add recovery has no staged source."
                )
            embedded = self._document_indexer.prepare_document(scope, staged)
            self._document_indexer.commit_document(scope, embedded)
            job, snapshot = self._publish_complete_catalog(
                job,
                instructions=job.instructions,
                overrides={staged: self._generation(embedded)},
                fallback_sources=(),
            )
            return self._succeed(job, snapshot)
        if job.kind == "reindex":
            if job.target_link_id is None:
                raise KnowledgeLifecycleRecoveryError(
                    "Reindex recovery has no target source."
                )
            embedded = self._document_indexer.prepare_document(
                scope,
                job.target_link_id,
            )
            self._document_indexer.commit_document(scope, embedded)
            job, snapshot = self._publish_complete_catalog(
                job,
                instructions=job.instructions,
                overrides={
                    job.target_link_id: self._generation(embedded),
                },
                fallback_sources=(),
            )
            return self._succeed(job, snapshot)
        if job.kind == "rebuild":
            sources = sources_from_catalog(files, scope)
            rebuilt = self._document_indexer.rebuild_scope(
                scope,
                tuple(source.link_id for source in sources),
            )
            job, snapshot = self._publish_complete_catalog(
                job,
                instructions=job.instructions,
                overrides={
                    document.source.link_id: self._generation(document)
                    for document in rebuilt
                },
                fallback_sources=(),
            )
            return self._succeed(job, snapshot)
        raise KnowledgeLifecycleRecoveryError(
            "Knowledge operation kind cannot be recovered."
        )

    def _create_operation(
        self,
        scope: AttachmentScope,
        kind: KnowledgeOperationKind,
        entry: CatalogEntrySnapshot,
        files: FileCatalogSnapshot,
        *,
        target_link_id: str | None = None,
    ) -> KnowledgeOperationSnapshot:
        """Persist the journal before the first lifecycle side effect."""

        if self._list_recoverable(scope):
            raise KnowledgeLifecycleConflictError(
                "Project already has a recoverable knowledge operation."
            )
        now = self._utc_now()
        instructions = self._retain_owned_preferences(
            entry.instructions,
            files,
        )
        if kind == "delete" and target_link_id is not None:
            # A tombstoned delete may finish without republishing a live
            # catalog. Persist the post-delete policy in the saga from its
            # first checkpoint so recovery cannot retain a dangling preference.
            instructions = self._prune_preference(
                instructions,
                target_link_id,
            )
        operation = KnowledgeOperationSnapshot(
            schema_version=KNOWLEDGE_OPERATION_SCHEMA_VERSION,
            journal_revision=1,
            operation_id=generate_knowledge_operation_id(),
            scope=scope,
            kind=kind,
            state="running",
            phase="preparing",
            progress_percent=0,
            catalog_revision=entry.revision,
            index_profile_fingerprint=(
                self._current_index_profile_fingerprint
            ),
            instructions=instructions,
            attempt=1,
            created_at=now,
            updated_at=now,
            target_link_id=target_link_id,
            source_catalog_fingerprint=files.snapshot_fingerprint,
            catalog_snapshot_fingerprint=(
                None
                if entry.snapshot is None
                else entry.snapshot.snapshot_fingerprint
            ),
            started_from_revoked_catalog=(
                entry.revision > 0 and entry.snapshot is None
            ),
        )
        return self._operation_repository.create_operation(operation)

    def _publish_complete_catalog(
        self,
        job: KnowledgeOperationSnapshot,
        *,
        instructions: ProjectSourceInstructions,
        overrides: Mapping[str, ProjectSourceGeneration],
        fallback_sources: tuple[ProjectSourceGeneration, ...],
    ) -> tuple[KnowledgeOperationSnapshot, ProjectSourceSnapshot]:
        """Ensure every owned source has a vector, then CAS-publish last."""

        scope = job.scope
        if self._active_project_scope(ProjectId(scope.id)) != scope:
            raise KnowledgeLifecycleConflictError(
                "Project authority changed during knowledge publication."
            )
        job = self._checkpoint(
            job,
            phase="publishing",
            progress_percent=max(job.progress_percent, 85),
        )
        files = self._indexable_catalog(scope)
        sources = sources_from_catalog(files, scope)
        source_by_link = {source.link_id: source for source in sources}
        candidates: dict[str, ProjectSourceGeneration] = {}
        for item in fallback_sources:
            source = source_by_link.get(item.generation.source.link_id)
            if (
                source is not None
                and item.generation.source == source
                and item.index_profile_fingerprint
                == self._current_index_profile_fingerprint
            ):
                candidates[source.link_id] = item
        entry = self._read_catalog_entry(scope)
        if entry.revision != job.catalog_revision:
            raise KnowledgeLifecycleConflictError(
                "Project Source catalog changed before publication."
            )
        if entry.snapshot is not None:
            for item in entry.snapshot.sources:
                source = source_by_link.get(item.generation.source.link_id)
                if (
                    source is not None
                    and item.generation.source == source
                    and item.index_profile_fingerprint
                    == self._current_index_profile_fingerprint
                ):
                    candidates[source.link_id] = item
        for link_id, item in overrides.items():
            source = source_by_link.get(link_id)
            if (
                source is None
                or item.generation.source != source
                or item.index_profile_fingerprint
                != self._current_index_profile_fingerprint
            ):
                raise KnowledgeLifecycleConflictError(
                    "Prepared generation no longer matches Project ownership."
                )
            candidates[link_id] = item

        # Missing entries arise during recovery after a whole-catalog
        # tombstone. Recomputing them is slower than persisting vectors in the
        # journal, but keeps that journal bounded and free of private content.
        for source in sources:
            if source.link_id in candidates:
                continue
            embedded = self._document_indexer.prepare_document(
                scope,
                source.link_id,
            )
            self._document_indexer.commit_document(scope, embedded)
            candidates[source.link_id] = self._generation(embedded)

        ordered = tuple(candidates[source.link_id] for source in sources)
        current = self._read_catalog_entry(scope)
        if (
            current.snapshot is not None
            and current.snapshot.source_catalog_fingerprint
            == files.snapshot_fingerprint
            and current.snapshot.sources == ordered
            and current.snapshot.instructions == instructions
        ):
            return (
                self._checkpoint(
                    job,
                    catalog_revision=current.revision,
                    progress_percent=95,
                ),
                current.snapshot,
            )
        if current.revision != job.catalog_revision:
            raise KnowledgeLifecycleConflictError(
                "Project Source catalog changed during publication."
            )
        snapshot = ProjectSourceSnapshot(
            schema_version=PROJECT_SOURCE_SNAPSHOT_SCHEMA_VERSION,
            scope=scope,
            revision=current.revision + 1,
            source_catalog_fingerprint=files.snapshot_fingerprint,
            sources=ordered,
            instructions=instructions,
        )
        try:
            published = self._source_repository.save_snapshot(
                snapshot,
                expected_revision=current.revision,
            )
        except ProjectSourceConflictError:
            # A competing writer cannot be overwritten by a stale full-corpus
            # snapshot. Recovery will reload and either recognize its desired
            # state or rebuild against the new canonical ownership.
            raise KnowledgeLifecycleConflictError(
                "Project Source catalog changed during publication."
            ) from None
        except ProjectSourceError:
            raise KnowledgeLifecycleStorageError(
                "Project Source catalog could not be published."
            ) from None
        return (
            self._checkpoint(
                job,
                catalog_revision=published.revision,
                progress_percent=95,
            ),
            published,
        )

    def _revoke_catalog(
        self,
        job: KnowledgeOperationSnapshot,
        *,
        instructions: ProjectSourceInstructions | None = None,
    ) -> KnowledgeOperationSnapshot:
        """CAS-publish a policy-bearing tombstone before destructive work."""

        next_phase: KnowledgeOperationPhase = (
            "revoking"
            if job.phase in ("preparing", "importing", "indexing", "revoking")
            else job.phase
        )
        job = self._checkpoint(
            job,
            phase=next_phase,
            progress_percent=max(job.progress_percent, 55),
        )
        retained_instructions = (
            job.instructions if instructions is None else instructions
        )
        entry = self._read_catalog_entry(job.scope)
        if entry.snapshot is None:
            if entry.revision < job.catalog_revision:
                raise KnowledgeLifecycleConflictError(
                    "Project Source catalog revision moved backward."
                )
            if entry.revision > job.catalog_revision:
                if entry.instructions != retained_instructions:
                    raise KnowledgeLifecycleConflictError(
                        "Project Source tombstone policy changed during revocation."
                    )
                return self._checkpoint(
                    job,
                    catalog_revision=entry.revision,
                    progress_percent=max(job.progress_percent, 65),
                )
            if entry.revision > 0 and entry.instructions == retained_instructions:
                return self._checkpoint(
                    job,
                    catalog_revision=entry.revision,
                    progress_percent=max(job.progress_percent, 65),
                )
            try:
                revision = self._source_repository.delete_snapshot(
                    job.scope,
                    expected_revision=entry.revision,
                    instructions=retained_instructions,
                )
            except ProjectSourceConflictError:
                raise KnowledgeLifecycleConflictError(
                    "Project Source catalog changed during revocation."
                ) from None
            except ProjectSourceError:
                raise KnowledgeLifecycleStorageError(
                    "Project Source catalog could not be revoked."
                ) from None
            return self._checkpoint(
                job,
                catalog_revision=revision,
                progress_percent=max(job.progress_percent, 65),
            )
        if entry.revision != job.catalog_revision or (
            job.catalog_snapshot_fingerprint is None
            or entry.snapshot.snapshot_fingerprint
            != job.catalog_snapshot_fingerprint
        ):
            raise KnowledgeLifecycleConflictError(
                "Project Source catalog changed before revocation."
            )
        try:
            revision = self._source_repository.delete_snapshot(
                job.scope,
                expected_revision=entry.revision,
                instructions=retained_instructions,
            )
        except ProjectSourceConflictError:
            raise KnowledgeLifecycleConflictError(
                "Project Source catalog changed during revocation."
            ) from None
        except ProjectSourceError:
            raise KnowledgeLifecycleStorageError(
                "Project Source catalog could not be revoked."
            ) from None
        return self._checkpoint(
            job,
            catalog_revision=revision,
            progress_percent=max(job.progress_percent, 65),
        )

    def _cleanup_source(
        self,
        scope: AttachmentScope,
        link_id: str,
    ) -> None:
        """Idempotently delete vectors, optional artifacts, and ownership last."""

        entry = self._read_catalog_entry(scope)
        if entry.snapshot is not None:
            raise KnowledgeLifecycleConflictError(
                "Project Source cleanup requires a revoked catalog."
            )
        try:
            full = snapshot_file_catalog(
                self._attachment_service.snapshot_files(scope)
            )
            ownership = next(
                (
                    item
                    for item in full.ownerships
                    if item.link_id == link_id
                ),
                None,
            )
            if ownership is None:
                # Recovery may repeat cleanup after the manifest commit. Vector
                # and optional artifact deletion are required to be idempotent.
                self._document_indexer.delete_document(scope, link_id)
                return
            source = self._source_for_cleanup(full, scope, link_id)
            if source is None:
                raise KnowledgeLifecycleStorageError(
                    "Project Source cleanup identity disappeared."
                )
            self._document_indexer.delete_document(scope, link_id)
            self._artifact_cleanup.purge_document(scope, source)
            self._attachment_service.remove_project_source(
                scope,
                link_id,
                full.snapshot_fingerprint,
            )
        except KnowledgeLifecycleError:
            raise
        except AttachmentNotFoundError:
            self._document_indexer.delete_document(scope, link_id)
        except (AttachmentError, DocumentError):
            raise KnowledgeLifecycleStorageError(
                "Project Source cleanup did not complete."
            ) from None
        except Exception:
            raise KnowledgeLifecycleStorageError(
                "Project Source cleanup did not complete."
            ) from None

    def _checkpoint_cleaning(
        self,
        job: KnowledgeOperationSnapshot,
    ) -> KnowledgeOperationSnapshot:
        """Enter cleanup without regressing a replay already at publication."""

        phase: KnowledgeOperationPhase = (
            job.phase if job.phase == "publishing" else "cleaning"
        )
        return self._checkpoint(
            job,
            phase=phase,
            progress_percent=max(job.progress_percent, 70),
        )

    def _cancel_staged(
        self,
        job: KnowledgeOperationSnapshot,
    ) -> KnowledgeOperationSnapshot:
        """Remove an unauthorized staged source and complete cancellation."""

        if job.staged_link_id is not None:
            try:
                self._document_indexer.delete_document(
                    job.scope,
                    job.staged_link_id,
                )
                full = snapshot_file_catalog(
                    self._attachment_service.snapshot_files(job.scope)
                )
                source = self._source_for_cleanup(
                    full,
                    job.scope,
                    job.staged_link_id,
                )
                if source is not None:
                    self._artifact_cleanup.purge_document(job.scope, source)
                    self._attachment_service.remove_project_source(
                        job.scope,
                        job.staged_link_id,
                        full.snapshot_fingerprint,
                    )
            except Exception:
                return self._fail_operation(
                    job,
                    recoverable=True,
                    error_code="cancel_cleanup_failed",
                )
        return self._cancel_without_side_effect(job)

    def _cancel_without_side_effect(
        self,
        job: KnowledgeOperationSnapshot,
    ) -> KnowledgeOperationSnapshot:
        """Persist a clean terminal cancellation before the commit point."""

        return self._checkpoint(
            self._refresh_operation(job),
            state="cancelled",
            phase="completed",
            error_code=None,
        )

    def _rollback_staged_quietly(
        self,
        scope: AttachmentScope,
        link_id: str,
    ) -> bool:
        """Best-effort rollback and report whether no staged ownership remains."""

        try:
            self._document_indexer.delete_document(scope, link_id)
            full = snapshot_file_catalog(
                self._attachment_service.snapshot_files(scope)
            )
            source = self._source_for_cleanup(full, scope, link_id)
            if source is not None:
                self._artifact_cleanup.purge_document(scope, source)
                self._attachment_service.remove_project_source(
                    scope,
                    link_id,
                    full.snapshot_fingerprint,
                )
            remaining = snapshot_file_catalog(
                self._attachment_service.snapshot_files(scope)
            )
            return not any(
                item.link_id == link_id for item in remaining.ownerships
            )
        except Exception:
            # The durable job is moved to recovery-required by the caller when
            # a committed vector might exist. A best-effort rollback must not
            # replace the original sanitized operation failure.
            return False

    @staticmethod
    def _source_for_cleanup(
        files: FileCatalogSnapshot,
        scope: AttachmentScope,
        link_id: str,
    ) -> DocumentSource | None:
        """Rebuild exact cleanup identity without consulting current routes.

        Loader support may change across an application upgrade, but that must
        never strand an already-revoked ownership. Cleanup therefore uses the
        immutable manifest identity directly; supported routes remain an
        admission/indexing policy only.
        """

        if files.scope != scope or scope.kind != "project":
            raise KnowledgeLifecycleConflictError(
                "Project Source cleanup crossed its authorized Project."
            )
        ownership = next(
            (item for item in files.ownerships if item.link_id == link_id),
            None,
        )
        if ownership is None:
            return None
        if ownership.scope != scope or ownership.role != "project_source":
            raise KnowledgeLifecycleConflictError(
                "Project Source cleanup target is not authorized."
            )
        original = next(
            (
                item
                for item in files.originals
                if item.file_id == ownership.file_id
            ),
            None,
        )
        if original is None:
            raise KnowledgeLifecycleStorageError(
                "Project Source cleanup target has no immutable original."
            )
        try:
            return DocumentSource(
                scope=scope,
                link_id=ownership.link_id,
                file_id=ownership.file_id,
                file_name=ownership.file_name,
                media_type=ownership.media_type,
                size_bytes=original.size_bytes,
            )
        except (TypeError, ValueError):
            raise KnowledgeLifecycleStorageError(
                "Project Source cleanup identity is invalid."
            ) from None

    def _fail_after_staged_rollback(
        self,
        job: KnowledgeOperationSnapshot,
    ) -> KnowledgeOperationSnapshot:
        """Discard an unauthorized staged generation after profile drift."""

        staged = job.staged_link_id
        if staged is None:
            return self._fail_operation(
                job,
                recoverable=False,
                error_code="index_profile_changed",
            )
        rollback_completed = self._rollback_staged_quietly(job.scope, staged)
        return self._fail_operation(
            job,
            recoverable=not rollback_completed,
            error_code=(
                "index_profile_changed"
                if rollback_completed
                else "profile_cleanup_failed"
            ),
        )

    def _recover_failed_rollback(
        self,
        job: KnowledgeOperationSnapshot,
    ) -> KnowledgeOperationSnapshot:
        """Retry only pre-commit cleanup instead of replaying failed indexing."""

        staged = job.staged_link_id
        if staged is not None and self._rollback_staged_quietly(job.scope, staged):
            return self._fail_operation(
                job,
                recoverable=False,
                error_code=f"{job.kind}_failed",
            )
        return self._fail_operation(
            job,
            recoverable=True,
            error_code="rollback_cleanup_failed",
        )

    def _succeed(
        self,
        job: KnowledgeOperationSnapshot,
        snapshot: ProjectSourceSnapshot | None,
    ) -> KnowledgeOperationSnapshot:
        """Persist success after the durable authorization outcome is known."""

        return self._checkpoint(
            self._refresh_operation(job),
            state="succeeded",
            phase="completed",
            progress_percent=100,
            catalog_revision=(
                job.catalog_revision
                if snapshot is None
                else snapshot.revision
            ),
            error_code=None,
        )

    def _fail_operation(
        self,
        job: KnowledgeOperationSnapshot,
        *,
        recoverable: bool,
        error_code: str,
    ) -> KnowledgeOperationSnapshot:
        """Record only a closed error code, never adapter diagnostics."""

        try:
            current = self._refresh_operation(job)
            if current.state in _TERMINAL_STATES:
                return current
            return self._checkpoint(
                current,
                state=("recovery_required" if recoverable else "failed"),
                phase=(current.phase if recoverable else "completed"),
                error_code=error_code,
            )
        except Exception:
            # The original durable checkpoint remains recoverable after a
            # journal-write failure. Never mask the public sanitized error.
            return job

    def _checkpoint(
        self,
        job: KnowledgeOperationSnapshot,
        **changes: object,
    ) -> KnowledgeOperationSnapshot:
        """CAS one monotonic journal transition while preserving cancellation."""

        current = self._refresh_operation(job)
        requested_state = changes.get("state", current.state)
        if (
            current.state == "cancel_requested"
            and requested_state not in _TERMINAL_STATES
            and requested_state != "recovery_required"
        ):
            changes["state"] = "cancel_requested"
        progress = changes.get("progress_percent", current.progress_percent)
        if type(progress) is not int or progress < current.progress_percent:
            raise KnowledgeLifecycleConflictError(
                "Knowledge operation progress cannot move backward."
            )
        updated = replace(
            current,
            journal_revision=current.journal_revision + 1,
            updated_at=max(self._utc_now(), current.updated_at),
            **changes,  # type: ignore[arg-type]
        )
        return self._operation_repository.save_operation(
            updated,
            expected_revision=current.journal_revision,
        )

    def _refresh_operation(
        self,
        job: KnowledgeOperationSnapshot,
    ) -> KnowledgeOperationSnapshot:
        """Load the latest CAS revision for one exact operation identity."""

        try:
            current = self._operation_repository.get_operation(
                job.operation_id
            )
        except KnowledgeLifecycleError:
            raise
        except Exception:
            raise KnowledgeLifecycleStorageError(
                "Knowledge operation journal could not be read."
            ) from None
        if current.operation_id != job.operation_id or current.scope != job.scope:
            raise KnowledgeLifecycleConflictError(
                "Knowledge operation identity changed."
            )
        return current

    def _cancel_now(
        self,
        operation_id: KnowledgeOperationId,
        callback: Callable[[], bool] | None,
    ) -> bool:
        """Observe external and durable cancellation without leaking failures."""

        externally_requested = False
        if callback is not None:
            try:
                externally_requested = callback() is True
            except Exception:
                externally_requested = True
        if externally_requested:
            try:
                self._operation_repository.request_cancel(operation_id)
            except KnowledgeLifecycleConflictError:
                pass
        return (
            self._operation_repository.get_operation(operation_id).state
            == "cancel_requested"
        )

    def _indexable_catalog(
        self,
        scope: AttachmentScope,
    ) -> FileCatalogSnapshot:
        """Load one detached exact catalog and apply the trusted route set."""

        try:
            return select_indexable_file_catalog(
                snapshot_file_catalog(
                    self._attachment_service.snapshot_files(scope)
                ),
                self._supported_document_routes,
            )
        except (AttachmentError, ProjectSourceError):
            raise KnowledgeLifecycleStorageError(
                "Project Source ownership could not be loaded."
            ) from None
        except Exception:
            raise KnowledgeLifecycleStorageError(
                "Project Source ownership could not be loaded."
            ) from None

    def _read_catalog_entry(
        self,
        scope: AttachmentScope,
    ) -> CatalogEntrySnapshot:
        """Read one live/tombstone catalog observation under a single lock."""

        try:
            entry = self._source_repository.read_entry(scope)
        except ProjectSourceError:
            raise KnowledgeLifecycleStorageError(
                "Project Source catalog could not be loaded."
            ) from None
        except Exception:
            raise KnowledgeLifecycleStorageError(
                "Project Source catalog could not be loaded."
            ) from None
        if (
            type(entry) is not CatalogEntrySnapshot
            or (
                entry.snapshot is not None
                and entry.snapshot.scope != scope
            )
        ):
            raise KnowledgeLifecycleStorageError(
                "Project Source catalog returned invalid authority."
            )
        return entry

    def _active_project_scope(self, project_id: object) -> AttachmentScope:
        """Resolve an active canonical Project without trusting caller scope."""

        if type(project_id) is not str:
            raise KnowledgeLifecycleValidationError(
                "Project identifier is invalid."
            )
        try:
            project = self._project_repository.get_project(ProjectId(project_id))
        except ProjectNotFoundError:
            raise KnowledgeLifecycleNotFoundError(
                "Project is unavailable."
            ) from None
        except Exception:
            raise KnowledgeLifecycleStorageError(
                "Project authority could not be loaded."
            ) from None
        if (
            type(project) is not Project
            or str(project.project_id) != project_id
            or project.is_archived
        ):
            raise KnowledgeLifecycleConflictError(
                "Project is not active."
            )
        try:
            return AttachmentScope(kind="project", id=project_id)
        except ValueError:
            raise KnowledgeLifecycleValidationError(
                "Project identifier is invalid."
            ) from None

    def _generation(
        self,
        document: EmbeddedDocument,
    ) -> ProjectSourceGeneration:
        """Build one exact published-generation token from committed vectors."""

        if type(document) is not EmbeddedDocument:
            raise KnowledgeLifecycleValidationError(
                "Prepared document generation is invalid."
            )
        return ProjectSourceGeneration(
            schema_version=PROJECT_SOURCE_GENERATION_SCHEMA_VERSION,
            generation=ExpectedDocumentGeneration(
                source=document.source,
                derivation_fingerprint=document.derivation_fingerprint,
            ),
            index_profile_fingerprint=(
                self._current_index_profile_fingerprint
            ),
            published_at=self._utc_now(),
        )

    @staticmethod
    def _require_source(
        catalog: FileCatalogSnapshot,
        scope: AttachmentScope,
        link_id: str,
    ) -> DocumentSource:
        """Return one exact canonical source or a stable not-found failure."""

        try:
            sources = sources_from_catalog(catalog, scope)
        except ProjectSourceError:
            raise KnowledgeLifecycleStorageError(
                "Project Source ownership is invalid."
            ) from None
        source = next(
            (item for item in sources if item.link_id == link_id),
            None,
        )
        if source is None:
            raise KnowledgeLifecycleNotFoundError(
                "Project Source is unavailable."
            )
        return source

    @staticmethod
    def _single_new_link(
        before: FileCatalogSnapshot,
        after: FileCatalogSnapshot,
    ) -> str:
        """Identify exactly one new immutable ownership after atomic staging."""

        previous = {item.link_id for item in before.ownerships}
        added = tuple(
            item.link_id
            for item in after.ownerships
            if item.link_id not in previous
        )
        if len(added) != 1:
            raise KnowledgeLifecycleConflictError(
                "Selected source did not create one new Project ownership."
            )
        return added[0]

    def _catalog_is_current(
        self,
        entry: CatalogEntrySnapshot,
        files: FileCatalogSnapshot,
    ) -> bool:
        """Return whether a live snapshot authorizes the current-profile corpus."""

        if entry.snapshot is None:
            return False
        expected = sources_from_catalog(files, files.scope)
        return (
            entry.snapshot.source_catalog_fingerprint
            == files.snapshot_fingerprint
            and tuple(
                item.generation.source for item in entry.snapshot.sources
            )
            == expected
            and all(
                item.index_profile_fingerprint
                == self._current_index_profile_fingerprint
                for item in entry.snapshot.sources
            )
        )

    @staticmethod
    def _catalog_matches_profile(
        entry: CatalogEntrySnapshot,
        files: FileCatalogSnapshot,
        profile_fingerprint: str,
        instructions: ProjectSourceInstructions,
    ) -> bool:
        """Recognize a complete publication made under a prior profile."""

        if entry.snapshot is None:
            return False
        expected = sources_from_catalog(files, files.scope)
        return (
            entry.snapshot.source_catalog_fingerprint
            == files.snapshot_fingerprint
            and tuple(
                item.generation.source for item in entry.snapshot.sources
            )
            == expected
            and entry.snapshot.instructions == instructions
            and all(
                item.index_profile_fingerprint == profile_fingerprint
                for item in entry.snapshot.sources
            )
        )

    @staticmethod
    def _operation_postcondition_satisfied(
        job: KnowledgeOperationSnapshot,
        snapshot: ProjectSourceSnapshot,
    ) -> bool:
        """Recognize only the exact catalog outcome required by one operation."""

        link_ids = {
            item.generation.source.link_id for item in snapshot.sources
        }
        if job.kind == "add":
            return job.staged_link_id in link_ids
        if job.kind == "replace":
            return (
                job.staged_link_id in link_ids
                and job.target_link_id not in link_ids
            )
        if job.kind == "delete":
            return job.target_link_id not in link_ids
        return False

    @staticmethod
    def _retain_owned_preferences(
        instructions: ProjectSourceInstructions,
        files: FileCatalogSnapshot,
    ) -> ProjectSourceInstructions:
        """Drop preferences whose owned sources were deleted while revoked."""

        owned_link_ids = {item.link_id for item in files.ownerships}
        return ProjectSourceInstructions(
            schema_version=instructions.schema_version,
            preferred_source_link_ids=tuple(
                link_id
                for link_id in instructions.preferred_source_link_ids
                if link_id in owned_link_ids
            ),
            answer_style=instructions.answer_style,
        )

    @staticmethod
    def _require_not_explicitly_revoked(
        entry: CatalogEntrySnapshot,
    ) -> None:
        """Reserve reauthorization after a tombstone for explicit rebuild."""

        if entry.revision > 0 and entry.snapshot is None:
            raise KnowledgeLifecycleConflictError(
                "Project Sources are revoked; rebuild is required."
            )

    def _list_recoverable(
        self,
        scope: AttachmentScope,
    ) -> tuple[KnowledgeOperationSnapshot, ...]:
        """Return bounded nonterminal operations for one Project."""

        return tuple(
            operation
            for operation in self._operation_repository.list_recoverable()
            if operation.scope == scope
        )

    @staticmethod
    def _prune_preference(
        instructions: ProjectSourceInstructions,
        removed_link_id: str,
    ) -> ProjectSourceInstructions:
        """Remove a deleted source from the non-authorizing preference order."""

        return ProjectSourceInstructions(
            schema_version=instructions.schema_version,
            preferred_source_link_ids=tuple(
                link_id
                for link_id in instructions.preferred_source_link_ids
                if link_id != removed_link_id
            ),
            answer_style=instructions.answer_style,
        )

    @staticmethod
    def _replace_preference(
        instructions: ProjectSourceInstructions,
        old_link_id: str,
        new_link_id: str,
    ) -> ProjectSourceInstructions:
        """Preserve preference position when copy-on-write changes a link ID."""

        return ProjectSourceInstructions(
            schema_version=instructions.schema_version,
            preferred_source_link_ids=tuple(
                new_link_id if link_id == old_link_id else link_id
                for link_id in instructions.preferred_source_link_ids
            ),
            answer_style=instructions.answer_style,
        )

    @staticmethod
    def _validate_cancel_callback(
        callback: Callable[[], bool] | None,
    ) -> None:
        """Require an optional cooperative cancellation predicate."""

        if callback is not None and not callable(callback):
            raise KnowledgeLifecycleValidationError(
                "cancel_requested must be callable or None."
            )

    def _utc_now(self) -> datetime:
        """Return one strict UTC clock value for durable ordering."""

        value = self._clock()
        if (
            type(value) is not datetime
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise KnowledgeLifecycleStorageError(
                "Knowledge lifecycle clock returned an invalid timestamp."
            )
        return value.astimezone(timezone.utc)
