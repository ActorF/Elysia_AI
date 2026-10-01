"""Compose the desktop's durable local Project Source knowledge runtime.

This module is the single composition boundary for document loading,
deterministic processing, pinned local embeddings, vector persistence,
retrieval, grounded generation, recoverable lifecycle operations, and original
file export.  It intentionally creates one shared repository instance for
Chats, Projects, and Project Source catalogs and one shared operation
coordinator so authorization cannot change midway through an answer or corpus
mutation.
"""

from __future__ import annotations

from _thread import LockType
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import re
from threading import Lock
from typing import Final

from attachments import AttachmentService
from chats import JsonChatRepository
from config.settings import AppSettings
from documents import (
    CHUNKED_DOCUMENT_SCHEMA_VERSION,
    CLEANED_DOCUMENT_SCHEMA_VERSION,
    DOCUMENT_SCHEMA_VERSION,
    EMBEDDED_DOCUMENT_SCHEMA_VERSION,
    ConservativeDocumentCleaner,
    DocumentEmbeddingService,
    DocumentIndexingService,
    DocumentLoaderService,
    DocumentProcessingService,
    DocumentRetriever,
    GroundedAnswerService,
    OllamaEmbeddingAdapter,
    OllamaEmbeddingConfig,
    OllamaGroundedAnswerAdapter,
    OllamaGroundedAnswerConfig,
    SQLiteVectorStore,
    StructureAwareDocumentChunker,
)
from knowledge_lifecycle import (
    JsonKnowledgeOperationRepository,
    KnowledgeExportService,
    KnowledgeLifecycleService,
    NoStoredKnowledgeArtifacts,
)
from project_sources import (
    JsonProjectSourceRepository,
    ProjectSourceAnswerService,
    ProjectSourceOperationCoordinator,
)
from projects import JsonProjectRepository


__all__ = [
    "DesktopKnowledgeRuntime",
    "create_desktop_knowledge_runtime",
]

_PROFILE_FINGERPRINT_DOMAIN: Final = (
    "elysia.desktop-knowledge-index-profile.v1"
)
_PROFILE_SCHEMA_VERSION: Final = 1
_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


def _canonical_index_profile_fingerprint(
    loader: DocumentLoaderService,
    cleaner: ConservativeDocumentCleaner,
    chunker: StructureAwareDocumentChunker,
    embedding_adapter: OllamaEmbeddingAdapter,
) -> str:
    """Hash every output-affecting route, processor, and vector-space rule."""

    # A lifecycle generation is reusable only when its complete deterministic
    # derivation contract still matches.  Canonical JSON makes that decision
    # independent of Python mapping order and prevents an accidental repr or
    # platform change from invalidating otherwise identical indexes.
    # ``DocumentLoaderService`` snapshots validated loader IDs and versions in
    # its closed registry but deliberately publishes only route pairs.  This
    # composition root reads that already-frozen internal metadata so a parser
    # version bump invalidates generations; it never retains the loader objects
    # or exposes the registry beyond this digest calculation.
    payload = {
        "fingerprint_domain": _PROFILE_FINGERPRINT_DOMAIN,
        "schema_version": _PROFILE_SCHEMA_VERSION,
        "document_routes": [
            {
                "loader_id": loader._routes[(suffix, media_type)][1],
                "loader_version": loader._routes[(suffix, media_type)][2],
                "media_type": media_type,
                "suffix": suffix,
            }
            for suffix, media_type in sorted(loader.supported_routes)
        ],
        "processing_contract": {
            "loaded_schema_version": DOCUMENT_SCHEMA_VERSION,
            "cleaned_schema_version": CLEANED_DOCUMENT_SCHEMA_VERSION,
            "chunked_schema_version": CHUNKED_DOCUMENT_SCHEMA_VERSION,
            "embedded_document_schema_version": (
                EMBEDDED_DOCUMENT_SCHEMA_VERSION
            ),
            "load_limits": asdict(loader.limits),
            "cleaner": {
                "id": cleaner.cleaner_id,
                "version": cleaner.cleaner_version,
                "policy": asdict(cleaner.policy),
                "limits": asdict(cleaner.limits),
            },
            "chunker": {
                "id": chunker.chunker_id,
                "version": chunker.chunker_version,
                "policy": asdict(chunker.policy),
            },
        },
        "embedding_contract": {
            "embedding_space_id": (
                embedding_adapter.identity.embedding_space_id
            ),
            "batch_policy": asdict(embedding_adapter.policy),
        },
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class DesktopKnowledgeRuntime:
    """Own the production desktop knowledge services and local vector store.

    The three public services deliberately share ``coordinator``.  ``profile``
    is the exact derivation fingerprint persisted into Project Source
    generations, while ``store`` is exposed for bounded lifecycle ownership
    and diagnostics rather than direct UI queries.
    """

    lifecycle: KnowledgeLifecycleService
    export: KnowledgeExportService
    answers: ProjectSourceAnswerService
    coordinator: ProjectSourceOperationCoordinator
    profile: str
    store: SQLiteVectorStore
    _close_lock: LockType = field(
        default_factory=Lock,
        init=False,
        repr=False,
    )
    _closed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        """Reject partially composed or ambiguously identified runtimes."""

        if not isinstance(self.lifecycle, KnowledgeLifecycleService):
            raise TypeError("lifecycle must be KnowledgeLifecycleService.")
        if not isinstance(self.export, KnowledgeExportService):
            raise TypeError("export must be KnowledgeExportService.")
        if not isinstance(self.answers, ProjectSourceAnswerService):
            raise TypeError("answers must be ProjectSourceAnswerService.")
        if not isinstance(
            self.coordinator,
            ProjectSourceOperationCoordinator,
        ):
            raise TypeError(
                "coordinator must be ProjectSourceOperationCoordinator."
            )
        if not isinstance(self.store, SQLiteVectorStore):
            raise TypeError("store must be SQLiteVectorStore.")
        if (
            type(self.profile) is not str
            or _DIGEST_PATTERN.fullmatch(self.profile) is None
        ):
            raise ValueError("profile must be a lowercase SHA-256 digest.")

    @property
    def closed(self) -> bool:
        """Return whether this owner has completed its idempotent close step."""

        with self._close_lock:
            return self._closed

    def close(self) -> None:
        """Idempotently release store resources owned by this composition.

        ``SQLiteVectorStore`` currently opens only transaction-scoped
        connections and therefore has no persistent handle to close.  The
        conditional call preserves that invariant while making this ownership
        boundary safe if a later store implementation adds an explicit close.
        """

        with self._close_lock:
            if self._closed:
                return
            close_store = getattr(self.store, "close", None)
            if callable(close_store):
                close_store()
            object.__setattr__(self, "_closed", True)


def create_desktop_knowledge_runtime(
    settings: AppSettings,
    attachment_service: AttachmentService,
) -> DesktopKnowledgeRuntime:
    """Compose one offline-first knowledge runtime from validated app services.

    Construction creates local repositories and the SQLite schema but performs
    no Ollama request, model pull, document indexing, recovery, or mutation.
    The desktop backend can therefore establish all protocol handlers before
    choosing when to recover durable jobs or invoke model work.
    """

    if not isinstance(settings, AppSettings):
        raise TypeError("settings must be AppSettings.")
    if not isinstance(attachment_service, AttachmentService):
        raise TypeError("attachment_service must be AttachmentService.")
    if not isinstance(settings.base_dir, Path):
        raise TypeError("settings.base_dir must be Path.")

    base_dir = settings.base_dir.absolute()
    workspace = base_dir / "workspace"
    knowledge_directory = workspace / "knowledge"

    # These exact instances are shared across services; independently created
    # repositories can point at the same files, but sharing makes authorization
    # and test inspection explicit and avoids accidental path drift.
    chat_repository = JsonChatRepository(workspace / "chats")
    project_repository = JsonProjectRepository(workspace / "projects")
    source_repository = JsonProjectSourceRepository(
        knowledge_directory / "project-sources"
    )
    operation_repository = JsonKnowledgeOperationRepository(
        knowledge_directory / "operations"
    )
    coordinator = ProjectSourceOperationCoordinator()

    loader = DocumentLoaderService(attachment_service)
    cleaner = ConservativeDocumentCleaner()
    chunker = StructureAwareDocumentChunker()
    processor = DocumentProcessingService(
        loader,
        cleaner=cleaner,
        chunker=chunker,
    )
    embedding_adapter = OllamaEmbeddingAdapter(
        OllamaEmbeddingConfig(base_url=settings.ollama_host)
    )
    embedding_service = DocumentEmbeddingService(embedding_adapter)
    store = SQLiteVectorStore(
        knowledge_directory / "vectors.sqlite3",
        embedding_adapter.identity,
    )
    indexer = DocumentIndexingService(
        processor,
        embedding_service,
        store,
    )
    retriever = DocumentRetriever(embedding_service, store)
    grounding_adapter = OllamaGroundedAnswerAdapter(
        OllamaGroundedAnswerConfig(
            model_tag=settings.model_name,
            base_url=settings.ollama_host,
        )
    )
    grounded_answers = GroundedAnswerService(retriever, grounding_adapter)
    profile = _canonical_index_profile_fingerprint(
        loader,
        cleaner,
        chunker,
        embedding_adapter,
    )
    routes = loader.supported_routes

    lifecycle = KnowledgeLifecycleService(
        project_repository,
        attachment_service,
        indexer,
        source_repository,
        operation_repository,
        coordinator,
        NoStoredKnowledgeArtifacts(),
        current_index_profile_fingerprint=profile,
        supported_document_routes=routes,
    )
    export_service = KnowledgeExportService(
        project_repository,
        attachment_service,
        coordinator,
        intent_directory=knowledge_directory / "export-intents",
    )
    answers = ProjectSourceAnswerService(
        chat_repository,
        project_repository,
        attachment_service,
        source_repository,
        grounded_answers,
        coordinator,
        current_index_profile_fingerprint=profile,
        supported_document_routes=routes,
    )
    return DesktopKnowledgeRuntime(
        lifecycle=lifecycle,
        export=export_service,
        answers=answers,
        coordinator=coordinator,
        profile=profile,
        store=store,
    )
