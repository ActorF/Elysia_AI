"""Authorize Chat-derived Project corpora and compose grounded answers."""

from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePath
import re
from threading import RLock
from typing import Iterator, Protocol, cast

from attachments import (
    AttachmentError,
    AttachmentConflictError,
    AttachmentNotFoundError,
    AttachmentScope,
    AttachmentService,
    AttachmentStorageError,
    AttachmentValidationError,
    FileCatalogSnapshot,
    FileOwnership,
)
from attachments.domain import validate_attachment_id
from chats import (
    AttachmentMetadata,
    ChatId,
    ChatNotFoundError,
    ChatRepository,
    ChatRepositoryError,
    ChatSession,
    ProjectId,
)
from documents import (
    MAX_RETRIEVAL_DOCUMENTS,
    DocumentSource,
    DocumentError,
    DocumentLoaderService,
    DocumentRoute,
    ExpectedDocumentGeneration,
    GroundedAnswerError,
    GroundedAnswerLimits,
    GroundedAnswerPreferences,
    GroundedAnswerResult,
    GroundedAnswerService,
    RetrievalLimits,
    RetrievalMetadataFilter,
    RetrievalPolicy,
)
from projects import (
    Project,
    ProjectNotFoundError,
    ProjectRepository,
    ProjectRepositoryError,
)

from .domain import (
    PROJECT_SOURCE_ANSWER_SCHEMA_VERSION,
    PROJECT_SOURCE_GENERATION_SCHEMA_VERSION,
    PROJECT_SOURCE_INSTRUCTIONS_SCHEMA_VERSION,
    PROJECT_SOURCE_SNAPSHOT_SCHEMA_VERSION,
    ProjectSourceAnswer,
    ProjectSourceGeneration,
    ProjectSourceInstructions,
    ProjectSourceSnapshot,
)
from .catalog import (
    select_indexable_file_catalog as _select_indexable_file_catalog,
    snapshot_file_catalog as _snapshot_file_catalog,
    sources_from_catalog as _sources_from_catalog,
)
from .exceptions import (
    ProjectSourceAuthorizationError,
    ProjectSourceConflictError,
    ProjectSourceError,
    ProjectSourceNotFoundError,
    ProjectSourceStaleError,
    ProjectSourceStorageError,
    ProjectSourceValidationError,
)
from .repository import ProjectSourceRepository


_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ProjectSourceOperationLease(Protocol):
    """Keep Chat relationship and Project authority stable for an operation.

    Production code must share this lease with Chat moves, Project archive and
    instruction edits, Project Source mutation, and catalog publication.  A
    boolean busy check cannot close the check-then-use race.
    """

    def hold(self, chat_id: ChatId) -> AbstractContextManager[None]:
        """Hold exclusive authority for one Chat until the context exits."""

        ...


class ProjectSourceOperationCoordinator:
    """Provide one conservative in-process lease for source operations.

    The first production integration can safely share this single coordinator
    across answers and every authority mutation.  A global reentrant lock is
    intentionally coarser than per-Project locking: it also closes races while
    a Chat moves between Projects, without acquiring keys from a relationship
    that is itself changing.  A later keyed coordinator may replace it only
    with equivalent move semantics and tests.
    """

    def __init__(self) -> None:
        """Create an initially unheld reentrant authority lease."""

        self._lock = RLock()

    @contextmanager
    def hold(self, chat_id: ChatId) -> Iterator[None]:
        """Hold answer or promotion authority for one validated Chat ID."""

        if type(chat_id) is not str:
            raise ProjectSourceValidationError(
                "Project Source Chat ID is invalid."
            )
        try:
            AttachmentScope(kind="chat", id=chat_id)
        except ValueError as error:
            raise ProjectSourceValidationError(
                "Project Source Chat ID is invalid."
            ) from error
        with self._lock:
            yield

    @contextmanager
    def hold_mutation(self) -> Iterator[None]:
        """Hold the same lease around any relationship or corpus mutation."""

        with self._lock:
            yield


class ProjectSourceInstructionProvider(Protocol):
    """Resolve structured, non-authorizing preferences for one Project."""

    def get_preferences(
        self,
        project: Project,
        scope: AttachmentScope,
        authorized_sources: tuple[DocumentSource, ...],
        instructions: ProjectSourceInstructions,
    ) -> GroundedAnswerPreferences:
        """Return bounded preferences whose link IDs are already authorized."""

        ...


class ProjectSettingsInstructionProvider:
    """Combine persisted source preferences with free-form style guidance.

    Free-form text is never parsed as a link ID, scope, permission, or citation
    directive.  Ordered link IDs and the closed answer style come only from the
    exact persisted Project Source catalog, whose fingerprint binds them to its
    authorized generations.
    """

    def get_preferences(
        self,
        project: Project,
        scope: AttachmentScope,
        authorized_sources: tuple[DocumentSource, ...],
        instructions: ProjectSourceInstructions,
    ) -> GroundedAnswerPreferences:
        """Return catalog preferences plus canonical Project style guidance."""

        del authorized_sources
        return GroundedAnswerPreferences(
            schema_version=1,
            scope=scope,
            preferred_source_link_ids=(
                instructions.preferred_source_link_ids
            ),
            answer_style=instructions.answer_style,
            style_guidance=project.settings.custom_instructions,
        )


@dataclass(frozen=True, slots=True)
class _AuthoritySnapshot:
    """Retain only scalar authority needed for post-answer revalidation."""

    chat_id: ChatId
    project_id: ProjectId
    chat_updated_at: datetime
    project_updated_at: datetime
    default_model_name: str | None
    custom_instructions: str | None


def _snapshot_grounded_result(value: object) -> GroundedAnswerResult:
    """Detach the complete grounded result returned by an injected service."""

    if type(value) is not GroundedAnswerResult:
        raise ProjectSourceValidationError(
            "Grounded answer result is invalid."
        )
    try:
        return GroundedAnswerResult(
            schema_version=value.schema_version,
            scope=value.scope,
            status=value.status,
            generator_identity=value.generator_identity,
            context_passage_count=value.context_passage_count,
            statements=value.statements,
            citations=value.citations,
        )
    except (AttributeError, DocumentError, TypeError, ValueError) as error:
        raise ProjectSourceValidationError(
            "Grounded answer result is invalid."
        ) from error


def _canonical_chat_attachment(
    chat: ChatSession,
    attachment_id: str,
) -> AttachmentMetadata:
    """Resolve one attachment that actually belongs to canonical Chat history."""

    matches = tuple(
        attachment
        for message in chat.messages
        for attachment in message.attachments
        if attachment.attachment_id == attachment_id
    )
    if len(matches) != 1:
        raise ProjectSourceAuthorizationError(
            "Chat attachment is not owned by canonical Chat history."
        )
    attachment = matches[0]
    if type(attachment) is not AttachmentMetadata:
        raise ProjectSourceValidationError(
            "Canonical Chat attachment metadata is invalid."
        )
    return AttachmentMetadata(
        attachment_id=attachment.attachment_id,
        file_name=attachment.file_name,
        media_type=attachment.media_type,
        size_bytes=attachment.size_bytes,
    )


def _snapshot_file_ownership(value: object) -> FileOwnership:
    """Detach one promoted ownership returned by an injected repository."""

    if type(value) is not FileOwnership:
        raise ProjectSourceValidationError(
            "Attachment promotion returned invalid ownership."
        )
    try:
        return FileOwnership(
            schema_version=value.schema_version,
            link_id=value.link_id,
            file_id=value.file_id,
            file_name=value.file_name,
            media_type=value.media_type,
            scope=AttachmentScope(kind=value.scope.kind, id=value.scope.id),
            role=value.role,
            imported_at=value.imported_at,
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise ProjectSourceValidationError(
            "Attachment promotion returned invalid ownership."
        ) from error


def _snapshot_project_source_catalog(value: object) -> ProjectSourceSnapshot:
    """Detach one catalog snapshot and every nested generation value."""

    if type(value) is not ProjectSourceSnapshot:
        raise ProjectSourceValidationError(
            "Project Source catalog snapshot is invalid."
        )
    if type(value.sources) is not tuple or len(value.sources) > (
        MAX_RETRIEVAL_DOCUMENTS
    ):
        raise ProjectSourceValidationError(
            "Project Source catalog snapshot exceeds its safe limit."
        )
    try:
        scope = AttachmentScope(kind=value.scope.kind, id=value.scope.id)
        sources = tuple(
            ProjectSourceGeneration(
                schema_version=item.schema_version,
                generation=ExpectedDocumentGeneration(
                    source=DocumentSource(
                        scope=AttachmentScope(
                            kind=item.generation.source.scope.kind,
                            id=item.generation.source.scope.id,
                        ),
                        link_id=item.generation.source.link_id,
                        file_id=item.generation.source.file_id,
                        file_name=item.generation.source.file_name,
                        media_type=item.generation.source.media_type,
                        size_bytes=item.generation.source.size_bytes,
                    ),
                    derivation_fingerprint=(
                        item.generation.derivation_fingerprint
                    ),
                ),
                index_profile_fingerprint=item.index_profile_fingerprint,
                published_at=item.published_at,
            )
            for item in value.sources
        )
        return ProjectSourceSnapshot(
            schema_version=value.schema_version,
            scope=scope,
            revision=value.revision,
            source_catalog_fingerprint=value.source_catalog_fingerprint,
            sources=sources,
            instructions=ProjectSourceInstructions(
                schema_version=value.instructions.schema_version,
                preferred_source_link_ids=tuple(
                    value.instructions.preferred_source_link_ids
                ),
                answer_style=value.instructions.answer_style,
            ),
            snapshot_fingerprint=value.snapshot_fingerprint,
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise ProjectSourceValidationError(
            "Project Source catalog snapshot is invalid."
        ) from error


class ProjectSourceAnswerService:
    """Resolve one Chat's exact Project corpus and publish a grounded answer.

    Callers submit only ``chat_id`` and the question; Project ID, attachment
    scope, and generation allowlist are derived from canonical repositories.
    The required operation lease keeps that authority valid through retrieval,
    generation, and final revalidation.
    """

    def __init__(
        self,
        chat_repository: ChatRepository,
        project_repository: ProjectRepository,
        attachment_service: AttachmentService,
        source_repository: ProjectSourceRepository,
        grounded_answer_service: GroundedAnswerService,
        operation_lease: ProjectSourceOperationLease,
        *,
        current_index_profile_fingerprint: str,
        instruction_provider: ProjectSourceInstructionProvider | None = None,
        supported_document_routes: frozenset[DocumentRoute] | None = None,
    ) -> None:
        """Compose canonical authority, catalog, grounding, and lease boundaries."""

        dependencies = (
            chat_repository,
            project_repository,
            attachment_service,
            source_repository,
            grounded_answer_service,
            operation_lease,
        )
        if any(dependency is None for dependency in dependencies):
            raise TypeError("Project Source service dependencies are required.")
        if (
            type(current_index_profile_fingerprint) is not str
            or _DIGEST_PATTERN.fullmatch(
                current_index_profile_fingerprint
            )
            is None
        ):
            raise ProjectSourceValidationError(
                "Current index profile fingerprint is invalid."
            )
        self._chat_repository = chat_repository
        self._project_repository = project_repository
        self._attachment_service = attachment_service
        self._source_repository = source_repository
        self._grounded_answer_service = grounded_answer_service
        self._operation_lease = operation_lease
        self._current_index_profile_fingerprint = (
            current_index_profile_fingerprint
        )
        routes = (
            DocumentLoaderService.default_supported_routes()
            if supported_document_routes is None
            else supported_document_routes
        )
        if type(routes) is not frozenset or not routes:
            raise ProjectSourceValidationError(
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
                raise ProjectSourceValidationError(
                    "Supported document route is invalid."
                )
            canonical_routes.add((route[0], route[1]))
        self._supported_document_routes = frozenset(canonical_routes)
        self._instruction_provider = (
            ProjectSettingsInstructionProvider()
            if instruction_provider is None
            else instruction_provider
        )

    def answer(
        self,
        chat_id: ChatId,
        query: str,
        *,
        metadata_filter: RetrievalMetadataFilter = RetrievalMetadataFilter(),
        retrieval_policy: RetrievalPolicy = RetrievalPolicy(),
        retrieval_limits: RetrievalLimits = RetrievalLimits(),
        answer_limits: GroundedAnswerLimits = GroundedAnswerLimits(),
    ) -> ProjectSourceAnswer:
        """Answer from the complete current Project corpus or fail closed."""

        canonical_chat_id = self._validate_chat_id(chat_id)
        try:
            lease = self._operation_lease.hold(canonical_chat_id)
        except Exception:
            raise ProjectSourceConflictError(
                "Project Source authority lease is unavailable."
            ) from None
        try:
            with lease:
                chat, project, authority, scope = self._load_authority(
                    canonical_chat_id
                )
                file_catalog = _select_indexable_file_catalog(
                    self._load_file_catalog(scope),
                    self._supported_document_routes,
                )
                sources = _sources_from_catalog(file_catalog, scope)
                catalog = self._load_generation_catalog(
                    scope,
                    file_catalog,
                    sources,
                )
                preferences = self._load_preferences(
                    project,
                    scope,
                    sources,
                    catalog.instructions,
                )
                expected_documents = tuple(
                    item.generation for item in catalog.sources
                )
                answer = _snapshot_grounded_result(
                    self._grounded_answer_service.answer(
                        scope,
                        query,
                        expected_documents,
                        preferences=preferences,
                        metadata_filter=metadata_filter,
                        retrieval_policy=retrieval_policy,
                        retrieval_limits=retrieval_limits,
                        answer_limits=answer_limits,
                    )
                )
                if answer.scope != scope:
                    raise ProjectSourceValidationError(
                        "Grounded answer changed the authorized Project scope."
                    )
                self._revalidate_authority(
                    authority,
                    scope,
                    file_catalog,
                    catalog,
                    preferences,
                )
                return ProjectSourceAnswer(
                    schema_version=PROJECT_SOURCE_ANSWER_SCHEMA_VERSION,
                    chat_id=chat.chat_id,
                    project_id=cast(ProjectId, scope.id),
                    answer=answer,
                )
        except (ProjectSourceError, DocumentError):
            raise
        except (MemoryError, RecursionError):
            raise ProjectSourceValidationError(
                "Project Source authorization exceeded a safe resource limit."
            ) from None
        except Exception:
            raise ProjectSourceStorageError(
                "Project Source answer failed without a safe typed result."
            ) from None

    def promote_chat_attachment(
        self,
        chat_id: ChatId,
        attachment_id: str,
    ) -> FileOwnership:
        """Explicitly create a Project ownership link for a committed file.

        This operation does not publish an indexed generation.  Until the
        lifecycle service indexes the new ownership and updates the catalog,
        answer authorization detects the ownership/catalog mismatch and fails
        closed instead of reading a stale partial corpus.
        """

        canonical_chat_id = self._validate_chat_id(chat_id)
        try:
            canonical_attachment_id = validate_attachment_id(attachment_id)
        except (TypeError, ValueError) as error:
            raise ProjectSourceValidationError(
                "Chat attachment identifier is invalid."
            ) from error
        try:
            lease = self._operation_lease.hold(canonical_chat_id)
            with lease:
                chat, _project, authority, project_scope = (
                    self._load_authority(canonical_chat_id)
                )
                canonical_attachment = _canonical_chat_attachment(
                    chat,
                    canonical_attachment_id,
                )
                route = (
                    PurePath(canonical_attachment.file_name).suffix.casefold(),
                    canonical_attachment.media_type,
                )
                if route not in self._supported_document_routes:
                    raise ProjectSourceValidationError(
                        "Chat attachment format is not indexable as a Project Source."
                    )
                chat_scope = AttachmentScope(
                    kind="chat",
                    id=str(chat.chat_id),
                )
                chat_catalog = self._load_file_snapshot(
                    chat_scope,
                    canonical_attachment_id,
                )
                if (
                    len(chat_catalog.ownerships) != 1
                    or chat_catalog.ownerships[0].role != "chat_attachment"
                ):
                    raise ProjectSourceAuthorizationError(
                        "Chat attachment ownership is not canonical."
                    )
                chat_ownership = chat_catalog.ownerships[0]
                originals = {
                    original.file_id: original
                    for original in chat_catalog.originals
                }
                original = originals.get(chat_ownership.file_id)
                if (
                    original is None
                    or chat_ownership.file_name
                    != canonical_attachment.file_name
                    or chat_ownership.media_type
                    != canonical_attachment.media_type
                    or original.size_bytes != canonical_attachment.size_bytes
                ):
                    raise ProjectSourceAuthorizationError(
                        "Chat attachment metadata is not canonical."
                    )
                ownership = _snapshot_file_ownership(
                    self._attachment_service._promote_chat_attachment(
                        chat_scope,
                        project_scope,
                        canonical_attachment_id,
                    )
                )
                if (
                    ownership.scope != project_scope
                    or ownership.role != "project_source"
                    or ownership.file_id != chat_ownership.file_id
                ):
                    raise ProjectSourceValidationError(
                        "Attachment promotion returned invalid ownership."
                    )
                if (
                    ownership.file_name != canonical_attachment.file_name
                    or ownership.media_type != canonical_attachment.media_type
                    or (
                        PurePath(ownership.file_name).suffix.casefold(),
                        ownership.media_type,
                    )
                    not in self._supported_document_routes
                ):
                    # Content-addressed storage may already contain identical
                    # bytes under another ownership label. Treat that as a
                    # conflict instead of reporting a successful promotion of
                    # a different or non-indexable Project Source.
                    raise ProjectSourceConflictError(
                        "Project already owns these bytes under different metadata."
                    )
                current_project_files = self._load_file_catalog(project_scope)
                if ownership not in current_project_files.ownerships:
                    raise ProjectSourceStorageError(
                        "Promoted Project Source ownership was not committed."
                    )
                _current_chat, _current_project, current, current_scope = (
                    self._load_authority(canonical_chat_id)
                )
                if current != authority or current_scope != project_scope:
                    raise ProjectSourceConflictError(
                        "Project Source authority changed during promotion."
                    )
                return ownership
        except ProjectSourceError:
            raise
        except AttachmentValidationError:
            raise ProjectSourceValidationError(
                "Chat attachment promotion request is invalid."
            ) from None
        except AttachmentNotFoundError:
            raise ProjectSourceNotFoundError(
                "Chat attachment is unavailable for promotion."
            ) from None
        except AttachmentConflictError:
            raise ProjectSourceConflictError(
                "Chat attachment is not ready for promotion."
            ) from None
        except AttachmentStorageError:
            raise ProjectSourceStorageError(
                "Chat attachment promotion failed safely."
            ) from None
        except AttachmentError:
            raise ProjectSourceStorageError(
                "Chat attachment promotion failed safely."
            ) from None
        except Exception:
            raise ProjectSourceStorageError(
                "Chat attachment promotion failed safely."
            ) from None

    @staticmethod
    def _validate_chat_id(chat_id: object) -> ChatId:
        """Return one stable Chat ID without accepting a Project or path."""

        if type(chat_id) is not str:
            raise ProjectSourceValidationError(
                "Project Source Chat ID is invalid."
            )
        try:
            scope = AttachmentScope(kind="chat", id=chat_id)
        except (TypeError, ValueError) as error:
            raise ProjectSourceValidationError(
                "Project Source Chat ID is invalid."
            ) from error
        return cast(ChatId, scope.id)

    def _load_authority(
        self,
        chat_id: ChatId,
    ) -> tuple[ChatSession, Project, _AuthoritySnapshot, AttachmentScope]:
        """Load one active canonical Chat→Project relationship under lease."""

        try:
            chat = self._chat_repository.get_chat(chat_id)
        except (ChatNotFoundError, ChatRepositoryError):
            raise ProjectSourceAuthorizationError(
                "Project Source Chat is unavailable."
            ) from None
        except Exception:
            raise ProjectSourceStorageError(
                "Project Source Chat authority could not be loaded."
            ) from None
        if (
            type(chat) is not ChatSession
            or chat.chat_id != chat_id
            or chat.is_archived
            or chat.project_id is None
        ):
            raise ProjectSourceAuthorizationError(
                "Chat is not authorized for Project Sources."
            )
        try:
            project = self._project_repository.get_project(chat.project_id)
        except (ProjectNotFoundError, ProjectRepositoryError):
            raise ProjectSourceAuthorizationError(
                "Project Source owner is unavailable."
            ) from None
        except Exception:
            raise ProjectSourceStorageError(
                "Project Source owner could not be loaded."
            ) from None
        if (
            type(project) is not Project
            or project.project_id != chat.project_id
            or project.is_archived
        ):
            raise ProjectSourceAuthorizationError(
                "Chat is not authorized for an active Project corpus."
            )
        try:
            scope = AttachmentScope(
                kind="project",
                id=str(project.project_id),
            )
        except ValueError as error:
            raise ProjectSourceValidationError(
                "Canonical Project identity is invalid."
            ) from error
        authority = _AuthoritySnapshot(
            chat_id=chat.chat_id,
            project_id=project.project_id,
            chat_updated_at=chat.updated_at,
            project_updated_at=project.updated_at,
            default_model_name=project.settings.default_model_name,
            custom_instructions=project.settings.custom_instructions,
        )
        return chat, project, authority, scope

    def _load_file_catalog(
        self,
        scope: AttachmentScope,
    ) -> FileCatalogSnapshot:
        """Load one atomic ownership/original snapshot with sanitized errors."""

        try:
            return _snapshot_file_catalog(
                self._attachment_service.snapshot_files(scope)
            )
        except ProjectSourceError:
            raise
        except AttachmentError:
            raise ProjectSourceStorageError(
                "Project Source ownership could not be loaded."
            ) from None
        except Exception:
            raise ProjectSourceStorageError(
                "Project Source ownership could not be loaded."
            ) from None

    def _load_file_snapshot(
        self,
        scope: AttachmentScope,
        attachment_id: str,
    ) -> FileCatalogSnapshot:
        """Load one atomic ownership/original pair with sanitized errors."""

        try:
            return _snapshot_file_catalog(
                self._attachment_service.snapshot_file(scope, attachment_id)
            )
        except ProjectSourceError:
            raise
        except AttachmentNotFoundError:
            raise ProjectSourceNotFoundError(
                "Chat attachment is unavailable for promotion."
            ) from None
        except AttachmentValidationError:
            raise ProjectSourceValidationError(
                "Chat attachment promotion request is invalid."
            ) from None
        except AttachmentError:
            raise ProjectSourceStorageError(
                "Chat attachment ownership could not be loaded."
            ) from None
        except Exception:
            raise ProjectSourceStorageError(
                "Chat attachment ownership could not be loaded."
            ) from None

    def _load_generation_catalog(
        self,
        scope: AttachmentScope,
        file_catalog: FileCatalogSnapshot,
        sources: tuple[DocumentSource, ...],
    ) -> ProjectSourceSnapshot:
        """Authorize an exact complete generation set for current ownership."""

        try:
            raw = self._source_repository.get_snapshot(scope)
        except ProjectSourceNotFoundError:
            if sources:
                raise ProjectSourceStaleError(
                    "Project Sources are not fully indexed."
                ) from None
            # A Project with no Sources needs no persisted catalog to prove an
            # empty corpus; the synthetic revision never reaches persistence.
            return ProjectSourceSnapshot(
                schema_version=PROJECT_SOURCE_SNAPSHOT_SCHEMA_VERSION,
                scope=scope,
                revision=1,
                source_catalog_fingerprint=(
                    file_catalog.snapshot_fingerprint
                ),
                sources=(),
                instructions=ProjectSourceInstructions(
                    schema_version=(
                        PROJECT_SOURCE_INSTRUCTIONS_SCHEMA_VERSION
                    )
                ),
            )
        except ProjectSourceError:
            raise
        except Exception:
            raise ProjectSourceStorageError(
                "Project Source catalog could not be loaded."
            ) from None
        catalog = _snapshot_project_source_catalog(raw)
        if (
            catalog.scope != scope
            or catalog.source_catalog_fingerprint
            != file_catalog.snapshot_fingerprint
        ):
            raise ProjectSourceStaleError(
                "Project Source catalog does not match current ownership."
            )
        generation_sources = tuple(
            item.generation.source for item in catalog.sources
        )
        if generation_sources != sources:
            raise ProjectSourceStaleError(
                "Project Source catalog is incomplete or stale."
            )
        if any(
            item.index_profile_fingerprint
            != self._current_index_profile_fingerprint
            for item in catalog.sources
        ):
            raise ProjectSourceStaleError(
                "Project Source catalog uses an old index profile."
            )
        return catalog

    def _load_preferences(
        self,
        project: Project,
        scope: AttachmentScope,
        sources: tuple[DocumentSource, ...],
        instructions: ProjectSourceInstructions,
    ) -> GroundedAnswerPreferences:
        """Load bounded preferences and prove they grant no new source."""

        try:
            raw = self._instruction_provider.get_preferences(
                project,
                scope,
                sources,
                instructions,
            )
            if type(raw) is not GroundedAnswerPreferences:
                raise ProjectSourceValidationError(
                    "Project Source instructions are invalid."
                )
            preferences = GroundedAnswerPreferences(
                schema_version=raw.schema_version,
                scope=AttachmentScope(
                    kind=raw.scope.kind,
                    id=raw.scope.id,
                ),
                preferred_source_link_ids=tuple(
                    raw.preferred_source_link_ids
                ),
                answer_style=raw.answer_style,
                style_guidance=raw.style_guidance,
                preferences_fingerprint=raw.preferences_fingerprint,
            )
        except ProjectSourceError:
            raise
        except Exception as error:
            # Grounding validation and limit failures are safe typed errors and
            # must retain their category for user guidance.
            if isinstance(error, GroundedAnswerError):
                raise
            raise ProjectSourceValidationError(
                "Project Source instructions are invalid."
            ) from None
        allowed = {source.link_id for source in sources}
        if preferences.scope != scope or any(
            link_id not in allowed
            for link_id in preferences.preferred_source_link_ids
        ):
            raise ProjectSourceAuthorizationError(
                "Project Source instructions reference an unauthorized source."
            )
        return preferences

    def _revalidate_authority(
        self,
        authority: _AuthoritySnapshot,
        scope: AttachmentScope,
        file_catalog: FileCatalogSnapshot,
        catalog: ProjectSourceSnapshot,
        preferences: GroundedAnswerPreferences,
    ) -> None:
        """Discard an answer if any authority snapshot changed during work."""

        try:
            _chat, project, current, current_scope = self._load_authority(
                authority.chat_id
            )
        except ProjectSourceAuthorizationError:
            raise ProjectSourceConflictError(
                "Project Source authority changed during the answer."
            ) from None
        if current != authority or current_scope != scope:
            raise ProjectSourceConflictError(
                "Project Source authority changed during the answer."
            )
        current_files = _select_indexable_file_catalog(
            self._load_file_catalog(scope),
            self._supported_document_routes,
        )
        current_sources = _sources_from_catalog(current_files, scope)
        current_catalog = self._load_generation_catalog(
            scope,
            current_files,
            current_sources,
        )
        current_preferences = self._load_preferences(
            project,
            scope,
            current_sources,
            current_catalog.instructions,
        )
        if (
            current_files.snapshot_fingerprint
            != file_catalog.snapshot_fingerprint
            or current_catalog.snapshot_fingerprint
            != catalog.snapshot_fingerprint
            or current_preferences != preferences
        ):
            raise ProjectSourceConflictError(
                "Project Source authority changed during the answer."
            )
