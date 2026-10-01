"""Test the desktop knowledge composition root without model network I/O."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, NoReturn, cast

import pytest

import desktop_knowledge
from attachments import AttachmentService, JsonAttachmentStore
from config.settings import AppSettings
from documents import (
    DocumentChunkingPolicy,
    DocumentLoaderService,
    OllamaEmbeddingAdapter,
    OllamaEmbeddingConfig,
    SQLiteVectorStore,
    StructureAwareDocumentChunker,
)
from documents.text import TextDocumentLoader
from knowledge_lifecycle import KnowledgeExportService, KnowledgeLifecycleService
from project_sources import (
    ProjectSourceAnswerService,
    ProjectSourceOperationCoordinator,
)


_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _settings(base_dir: Path, *, host: str = "http://localhost:11434") -> AppSettings:
    """Return one minimal validated settings snapshot for local composition."""

    return AppSettings(
        base_dir=base_dir,
        model_name="fixture-grounded:1",
        log_level="INFO",
        debug=False,
        ollama_host=host,
    )


def _attachments(base_dir: Path) -> AttachmentService:
    """Create one isolated attachment authority for a runtime fixture."""

    return AttachmentService(
        JsonAttachmentStore(
            base_dir / "workspace" / "attachments",
            max_file_bytes=1024 * 1024,
        )
    )


def _unexpected_grounding_identity(_adapter: object) -> NoReturn:
    """Fail if runtime construction eagerly probes the grounded model."""

    raise AssertionError("Grounding identity must remain lazy.")


def test_factory_composes_shared_authority_and_persistence_without_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One factory call shares repositories, lease, store, and profile offline."""

    monkeypatch.setattr(
        desktop_knowledge.OllamaGroundedAnswerAdapter,
        "identity",
        property(_unexpected_grounding_identity),
    )
    runtime = desktop_knowledge.create_desktop_knowledge_runtime(
        _settings(tmp_path),
        _attachments(tmp_path),
    )
    assert isinstance(runtime.lifecycle, KnowledgeLifecycleService)
    assert isinstance(runtime.export, KnowledgeExportService)
    assert isinstance(runtime.answers, ProjectSourceAnswerService)
    assert isinstance(runtime.coordinator, ProjectSourceOperationCoordinator)
    assert isinstance(runtime.store, SQLiteVectorStore)
    assert _DIGEST_PATTERN.fullmatch(runtime.profile)
    assert runtime.lifecycle._mutation_lease is runtime.coordinator
    assert runtime.export._mutation_lease is runtime.coordinator
    assert runtime.answers._operation_lease is runtime.coordinator
    assert (
        runtime.lifecycle._project_repository
        is runtime.export._project_repository
        is runtime.answers._project_repository
    )
    assert (
        runtime.lifecycle._source_repository
        is runtime.answers._source_repository
    )
    assert (
        runtime.lifecycle._current_index_profile_fingerprint
        == runtime.answers._current_index_profile_fingerprint
        == runtime.profile
    )
    assert (
        runtime.lifecycle._supported_document_routes
        == runtime.answers._supported_document_routes
    )
    assert (
        cast(
            Any,
            runtime.answers._grounded_answer_service._retriever,
        )._store
        is runtime.store
    )
    runtime.close()


def test_factory_profile_is_path_independent_and_close_is_idempotent(
    tmp_path: Path,
) -> None:
    """Equivalent contracts share a profile and runtime ownership closes once."""

    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first = desktop_knowledge.create_desktop_knowledge_runtime(
        _settings(first_root),
        _attachments(first_root),
    )
    second = desktop_knowledge.create_desktop_knowledge_runtime(
        _settings(second_root),
        _attachments(second_root),
    )

    assert first.profile == second.profile
    assert not first.closed
    first.close()
    first.close()
    assert first.closed
    second.close()


def test_profile_binds_routes_and_chunking_contract(tmp_path: Path) -> None:
    """Route or chunk-boundary drift necessarily changes the profile digest."""

    attachments = _attachments(tmp_path)
    default_loader = DocumentLoaderService(attachments)
    text_only_loader = DocumentLoaderService(
        attachments,
        loaders=(TextDocumentLoader(),),
    )
    cleaner = desktop_knowledge.ConservativeDocumentCleaner()
    default_chunker = StructureAwareDocumentChunker()
    changed_chunker = StructureAwareDocumentChunker(
        policy=DocumentChunkingPolicy(max_chunk_code_points=1_000)
    )
    embedding = OllamaEmbeddingAdapter(OllamaEmbeddingConfig())

    baseline = desktop_knowledge._canonical_index_profile_fingerprint(
        default_loader,
        cleaner,
        default_chunker,
        embedding,
    )
    changed_routes = desktop_knowledge._canonical_index_profile_fingerprint(
        text_only_loader,
        cleaner,
        default_chunker,
        embedding,
    )
    changed_processing = (
        desktop_knowledge._canonical_index_profile_fingerprint(
            default_loader,
            cleaner,
            changed_chunker,
            embedding,
        )
    )

    assert baseline != changed_routes
    assert baseline != changed_processing


def test_factory_rejects_remote_ollama_before_creating_a_runtime(
    tmp_path: Path,
) -> None:
    """Both embedding and grounding remain restricted to loopback origins."""

    with pytest.raises(ValueError, match="loopback"):
        desktop_knowledge.create_desktop_knowledge_runtime(
            _settings(tmp_path, host="http://example.com:11434"),
            _attachments(tmp_path),
        )
