"""Verify path-private document-loader orchestration and repository integration."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from attachments import (
    FILE_METADATA_SCHEMA_VERSION,
    AttachmentScope,
    AttachmentService,
    AttachmentStorageError,
    FileOwnership,
    JsonAttachmentStore,
    OriginalFileMetadata,
)
from documents import (
    DOCUMENT_SCHEMA_VERSION,
    DocumentBlock,
    DocumentContentLimitError,
    DocumentLoadFailedError,
    DocumentLoadLimits,
    DocumentLoaderService,
    DocumentNotFoundError,
    DocumentReadError,
    DocumentSource,
    DocumentTooLargeError,
    DocumentUnsupportedFeatureError,
    DocumentUnsupportedFormatError,
    DocumentValidationError,
    LoadedDocument,
)
from documents.docx import DocxDocumentLoader
from documents.pdf import PdfDocumentLoader
from documents.text import TextDocumentLoader


_NOW = datetime(2026, 9, 23, 18, 0, tzinfo=timezone.utc)
_DIGEST = "a" * 64


class _RecordingMarkdownLoader:
    """Return one deterministic block while recording the selected source."""

    def __init__(self) -> None:
        """Create an unused-source marker for delegation assertions."""

        self.seen_source: DocumentSource | None = None

    @property
    def loader_id(self) -> str:
        """Return the stable fake adapter identity."""

        return "recording-markdown"

    @property
    def loader_version(self) -> str:
        """Return the fake output contract version."""

        return "1.0.0"

    @property
    def routes(self) -> frozenset[tuple[str, str]]:
        """Accept exactly a Markdown ownership link."""

        return frozenset({(".md", "text/markdown")})

    def load(
        self,
        source: DocumentSource,
        data: bytes,
        limits: DocumentLoadLimits,
    ) -> LoadedDocument:
        """Decode the known test bytes into one raw paragraph."""

        self.seen_source = source
        return LoadedDocument(
            schema_version=DOCUMENT_SCHEMA_VERSION,
            source=source,
            document_format="markdown",
            loader_id=self.loader_id,
            loader_version=self.loader_version,
            blocks=(
                DocumentBlock(
                    ordinal=0,
                    kind="paragraph",
                    text=data.decode("utf-8"),
                ),
            ),
            limits=limits,
        )


class _WrongSourceLoader(_RecordingMarkdownLoader):
    """Simulate a defective adapter that changes authorization metadata."""

    def load(
        self,
        source: DocumentSource,
        data: bytes,
        limits: DocumentLoadLimits,
    ) -> LoadedDocument:
        """Return structurally valid output for the wrong ownership link."""

        wrong_source = DocumentSource(
            scope=source.scope,
            link_id="attachment_wrong",
            file_id=source.file_id,
            file_name=source.file_name,
            media_type=source.media_type,
            size_bytes=source.size_bytes,
        )
        return LoadedDocument(
            schema_version=DOCUMENT_SCHEMA_VERSION,
            source=wrong_source,
            document_format="markdown",
            loader_id=self.loader_id,
            loader_version=self.loader_version,
            blocks=(
                DocumentBlock(
                    ordinal=0,
                    kind="paragraph",
                    text=data.decode("utf-8"),
                ),
            ),
            limits=limits,
        )


class _BoundaryRepository:
    """Return configurable repository values for trust-boundary regressions."""

    def __init__(
        self,
        *,
        ownerships: object,
        records: object,
        stream: object = b"data",
        ownership_error: BaseException | None = None,
        record_error: BaseException | None = None,
        open_error: BaseException | None = None,
    ) -> None:
        """Store intentionally weakly typed values for runtime validation."""

        self.ownerships = ownerships
        self.records = records
        self.stream = stream
        self.ownership_error = ownership_error
        self.record_error = record_error
        self.open_error = open_error

    def list_file_ownerships(
        self,
        scope: AttachmentScope,
    ) -> tuple[FileOwnership, ...]:
        """Return or fail with the configured ownership response."""

        del scope
        if self.ownership_error is not None:
            raise self.ownership_error
        return cast(tuple[FileOwnership, ...], self.ownerships)

    def list_file_records(
        self,
        scope: AttachmentScope,
    ) -> tuple[OriginalFileMetadata, ...]:
        """Return or fail with the configured canonical-record response."""

        del scope
        if self.record_error is not None:
            raise self.record_error
        return cast(tuple[OriginalFileMetadata, ...], self.records)

    def open_verified_file(
        self,
        scope: AttachmentScope,
        file_id: str,
    ) -> AbstractContextManager[BinaryIO]:
        """Return one configured stream without exposing any local path."""

        del scope, file_id
        if self.open_error is not None:
            raise self.open_error
        stream = (
            BytesIO(self.stream)
            if isinstance(self.stream, bytes)
            else self.stream
        )
        return nullcontext(cast(BinaryIO, stream))


class _NonBytesStream:
    """Violate BinaryIO by returning decoded text from read()."""

    def read(self, size: int = -1) -> str:
        """Return a non-byte value regardless of the requested bound."""

        del size
        return r"C:\private\document.md"


class _RaisingStream:
    """Raise one native failure only after the verified stream is entered."""

    def __init__(self, error: BaseException) -> None:
        """Store the exact read failure for translation assertions."""

        self.error = error

    def read(self, size: int = -1) -> bytes:
        """Fail during bounded reading instead of repository open."""

        del size
        raise self.error


class _InvalidResultLoader(_RecordingMarkdownLoader):
    """Return a non-domain object across the adapter boundary."""

    def load(
        self,
        source: DocumentSource,
        data: bytes,
        limits: DocumentLoadLimits,
    ) -> LoadedDocument:
        """Return an object that only a runtime check can reject."""

        del source, data, limits
        return cast(LoadedDocument, object())


class _ChangedResultLoader(_RecordingMarkdownLoader):
    """Return valid-shaped output with altered policy or version metadata."""

    def __init__(self, changed_field: str) -> None:
        """Choose which service-owned output field the fake will change."""

        super().__init__()
        self.changed_field = changed_field

    def load(
        self,
        source: DocumentSource,
        data: bytes,
        limits: DocumentLoadLimits,
    ) -> LoadedDocument:
        """Build a document that disagrees with its registered adapter."""

        result_limits = (
            replace(limits, max_blocks=limits.max_blocks + 1)
            if self.changed_field == "limits"
            else limits
        )
        result_version = (
            "2.0.0"
            if self.changed_field == "version"
            else self.loader_version
        )
        return LoadedDocument(
            schema_version=DOCUMENT_SCHEMA_VERSION,
            source=source,
            document_format="markdown",
            loader_id=self.loader_id,
            loader_version=result_version,
            blocks=(
                DocumentBlock(
                    ordinal=0,
                    kind="paragraph",
                    text=data.decode("utf-8"),
                ),
            ),
            limits=result_limits,
        )


class _RaisingLoader(_RecordingMarkdownLoader):
    """Raise one configured adapter failure for translation tests."""

    def __init__(self, error: BaseException) -> None:
        """Retain the exact failure raised when loading starts."""

        super().__init__()
        self.error = error

    def load(
        self,
        source: DocumentSource,
        data: bytes,
        limits: DocumentLoadLimits,
    ) -> LoadedDocument:
        """Raise the configured failure without returning partial output."""

        del source, data, limits
        raise self.error


def _ownership(
    scope: AttachmentScope,
    *,
    link_id: str = "attachment_document",
) -> FileOwnership:
    """Build one valid ownership record for a Markdown source."""

    return FileOwnership(
        schema_version=FILE_METADATA_SCHEMA_VERSION,
        link_id=link_id,
        file_id=f"file_{_DIGEST}",
        file_name="document.md",
        media_type="text/markdown",
        scope=scope,
        role="chat_attachment" if scope.kind == "chat" else "project_source",
        imported_at=_NOW,
    )


def _record(*, size_bytes: int = 4) -> OriginalFileMetadata:
    """Build one canonical record matching the boundary ownership helper."""

    return OriginalFileMetadata(
        schema_version=FILE_METADATA_SCHEMA_VERSION,
        file_id=f"file_{_DIGEST}",
        sha256=_DIGEST,
        file_name="document.md",
        media_type="text/markdown",
        size_bytes=size_bytes,
        origin="local_import",
        imported_at=_NOW,
    )


def _boundary_service(
    repository: _BoundaryRepository,
    *,
    loader: _RecordingMarkdownLoader | None = None,
    max_source_bytes: int = 100,
) -> DocumentLoaderService:
    """Construct one service around deliberately configurable boundaries."""

    return DocumentLoaderService(
        repository,
        limits=DocumentLoadLimits(max_source_bytes=max_source_bytes),
        loaders=(
            _RecordingMarkdownLoader() if loader is None else loader,
        ),
    )


def _store(tmp_path: Path) -> JsonAttachmentStore:
    """Create an isolated repository with deterministic import timestamps."""

    return JsonAttachmentStore(
        tmp_path / "workspace" / "attachments",
        max_file_bytes=1024 * 1024,
        clock=lambda: _NOW,
    )


def _source(tmp_path: Path, name: str, data: bytes) -> Path:
    """Create one trusted native-selection fixture outside attachment storage."""

    path = tmp_path / "incoming" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path.resolve()


def test_service_uses_link_specific_metadata_and_verified_original(
    tmp_path: Path,
) -> None:
    """Preserve a reimported link name while opening its shared File ID."""

    store = _store(tmp_path)
    repository = AttachmentService(store)
    scope = AttachmentScope(kind="chat", id="chat_documents")
    first = store.stage_files(
        scope,
        [_source(tmp_path, "canonical.txt", b"same content")],
    ).attachments[0]
    store.claim_chat(scope, (first.attachment_id,))
    store.mark_committed(scope, (first.attachment_id,))
    second = store.stage_files(
        scope,
        [_source(tmp_path, "current.md", b"same content")],
        (first.attachment_id,),
    ).attachments[0]
    loader = _RecordingMarkdownLoader()
    service = DocumentLoaderService(repository, loaders=(loader,))

    loaded = service.load(scope, second.attachment_id)

    assert loaded.source.file_name == "current.md"
    assert loaded.source.media_type == "text/markdown"
    assert loaded.source.file_id == store.list_file_records(scope)[0].file_id
    assert loaded.blocks[0].text == "same content"
    assert loader.seen_source == loaded.source
    assert not any("path" in name for name in loaded.source.__dataclass_fields__)
    store.close()


def test_default_service_loads_real_markdown_from_attachment_storage(
    tmp_path: Path,
) -> None:
    """Exercise the built-in registry through the real verified-file boundary."""

    store = _store(tmp_path)
    scope = AttachmentScope(kind="project", id="project_markdown")
    item = store.stage_files(
        scope,
        [_source(tmp_path, "guide.md", b"# Elysia\n\nWelcome home.\n")],
    ).attachments[0]
    service = DocumentLoaderService(AttachmentService(store))

    loaded = service.load(scope, item.attachment_id)

    assert loaded.document_format == "markdown"
    assert loaded.loader_id == "text-structure"
    assert loaded.title is not None
    assert loaded.title.text == "Elysia"
    assert tuple(block.kind for block in loaded.blocks) == (
        "heading",
        "paragraph",
    )
    assert tuple(block.text for block in loaded.blocks) == (
        "Elysia",
        "Welcome home.\n",
    )
    store.close()


def test_default_service_registers_every_builtin_loader_route() -> None:
    """The default registry contains exactly the text and binary adapters."""

    repository = _BoundaryRepository(ownerships=(), records=())
    service = DocumentLoaderService(repository)
    expected_routes = frozenset(
        {
            *TextDocumentLoader().routes,
            *PdfDocumentLoader().routes,
            *DocxDocumentLoader().routes,
        }
    )

    assert service.supported_routes == expected_routes


def test_service_rejects_cross_scope_or_missing_ownership(
    tmp_path: Path,
) -> None:
    """Keep an attachment link from authorizing another owner namespace."""

    store = _store(tmp_path)
    first_scope = AttachmentScope(kind="project", id="project_first")
    other_scope = AttachmentScope(kind="project", id="project_other")
    item = store.stage_files(
        first_scope,
        [_source(tmp_path, "notes.md", b"notes")],
    ).attachments[0]
    service = DocumentLoaderService(
        AttachmentService(store),
        loaders=(_RecordingMarkdownLoader(),),
    )

    with pytest.raises(DocumentNotFoundError, match="ownership"):
        service.load(other_scope, item.attachment_id)

    store.close()


def test_service_rejects_unsupported_suffix_and_media_pair(
    tmp_path: Path,
) -> None:
    """Never fall back to text parsing for an unregistered binary route."""

    store = _store(tmp_path)
    scope = AttachmentScope(kind="project", id="project_image")
    item = store.stage_files(
        scope,
        [_source(tmp_path, "image.png", b"not really an image")],
    ).attachments[0]
    service = DocumentLoaderService(
        AttachmentService(store),
        loaders=(_RecordingMarkdownLoader(),),
    )

    with pytest.raises(DocumentUnsupportedFormatError, match="not supported"):
        service.load(scope, item.attachment_id)

    store.close()


def test_service_checks_metadata_size_before_opening_verified_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject an oversized original before the repository builds a snapshot."""

    store = _store(tmp_path)
    scope = AttachmentScope(kind="project", id="project_large")
    item = store.stage_files(
        scope,
        [_source(tmp_path, "large.md", b"too large")],
    ).attachments[0]

    def fail_if_opened(*_args: object, **_kwargs: object) -> object:
        """Prove that the verified-read path is not entered."""

        raise AssertionError("open_verified_file must not be called")

    monkeypatch.setattr(store, "open_verified_file", fail_if_opened)
    service = DocumentLoaderService(
        store,
        limits=DocumentLoadLimits(max_source_bytes=1),
        loaders=(_RecordingMarkdownLoader(),),
    )

    with pytest.raises(DocumentTooLargeError, match="byte limit"):
        service.load(scope, item.attachment_id)

    store.close()


def test_service_rejects_duplicate_routes_and_changed_source(
    tmp_path: Path,
) -> None:
    """Fail closed on ambiguous adapters and identity-changing results."""

    store = _store(tmp_path)
    with pytest.raises(ValueError, match="unique"):
        DocumentLoaderService(
            store,
            loaders=(
                _RecordingMarkdownLoader(),
                _RecordingMarkdownLoader(),
            ),
        )

    scope = AttachmentScope(kind="chat", id="chat_wrong_source")
    item = store.stage_files(
        scope,
        [_source(tmp_path, "notes.md", b"notes")],
    ).attachments[0]
    service = DocumentLoaderService(
        store,
        loaders=(_WrongSourceLoader(),),
    )
    with pytest.raises(DocumentValidationError, match="source identity"):
        service.load(scope, item.attachment_id)

    store.close()


@pytest.mark.parametrize(
    ("ownerships", "records"),
    [
        ([], (_record(),)),
        ((object(),), (_record(),)),
        ((_ownership(AttachmentScope(kind="chat", id="chat_shape")),), []),
        (
            (_ownership(AttachmentScope(kind="chat", id="chat_shape")),),
            (object(),),
        ),
    ],
)
def test_service_rejects_invalid_repository_collection_shapes(
    ownerships: object,
    records: object,
) -> None:
    """Require exact tuples containing only the promised domain records."""

    scope = AttachmentScope(kind="chat", id="chat_shape")
    repository = _BoundaryRepository(
        ownerships=ownerships,
        records=records,
    )

    with pytest.raises(DocumentReadError, match="repository shape"):
        _boundary_service(repository).load(scope, "attachment_document")


def test_service_requires_one_matching_ownership_in_the_caller_scope() -> None:
    """Reject duplicate links and records returned for a different owner."""

    scope = AttachmentScope(kind="chat", id="chat_unique")
    ownership = _ownership(scope)
    duplicate_repository = _BoundaryRepository(
        ownerships=(ownership, ownership),
        records=(_record(),),
    )
    with pytest.raises(DocumentReadError, match="ambiguous"):
        _boundary_service(duplicate_repository).load(
            scope,
            ownership.link_id,
        )

    wrong_scope = AttachmentScope(kind="chat", id="chat_wrong_scope")
    mismatched_repository = _BoundaryRepository(
        ownerships=(_ownership(wrong_scope),),
        records=(_record(),),
    )
    with pytest.raises(DocumentReadError, match="does not match its scope"):
        _boundary_service(mismatched_repository).load(
            scope,
            ownership.link_id,
        )


def test_service_requires_one_canonical_record_for_the_owned_file() -> None:
    """Treat duplicate canonical metadata as corruption, not as first-wins."""

    scope = AttachmentScope(kind="chat", id="chat_record_unique")
    record = _record()
    repository = _BoundaryRepository(
        ownerships=(_ownership(scope),),
        records=(record, record),
    )

    with pytest.raises(DocumentReadError, match="ambiguous"):
        _boundary_service(repository).load(scope, "attachment_document")


@pytest.mark.parametrize(
    ("data", "metadata_size", "max_source_bytes", "error_type", "message"),
    [
        (b"abc", 4, 10, DocumentReadError, "does not match"),
        (b"abcde", 4, 10, DocumentReadError, "exceeds its metadata"),
        (b"abcde", 4, 4, DocumentTooLargeError, "byte limit"),
    ],
)
def test_service_fails_closed_on_short_or_oversized_verified_streams(
    data: bytes,
    metadata_size: int,
    max_source_bytes: int,
    error_type: type[Exception],
    message: str,
) -> None:
    """Compare the actual byte stream against both metadata and policy."""

    scope = AttachmentScope(kind="chat", id="chat_stream_size")
    repository = _BoundaryRepository(
        ownerships=(_ownership(scope),),
        records=(_record(size_bytes=metadata_size),),
        stream=data,
    )

    with pytest.raises(error_type, match=message):
        _boundary_service(
            repository,
            max_source_bytes=max_source_bytes,
        ).load(scope, "attachment_document")


def test_service_rejects_a_non_byte_verified_stream() -> None:
    """Prevent a defective repository from injecting decoded or path text."""

    scope = AttachmentScope(kind="chat", id="chat_non_bytes")
    repository = _BoundaryRepository(
        ownerships=(_ownership(scope),),
        records=(_record(),),
        stream=_NonBytesStream(),
    )

    with pytest.raises(DocumentReadError, match="invalid byte stream") as caught:
        _boundary_service(repository).load(scope, "attachment_document")
    assert "private" not in str(caught.value).casefold()


@pytest.mark.parametrize(
    "error",
    [
        AttachmentStorageError(r"C:\private\attachment.blob"),
        OSError(r"C:\private\attachment.blob"),
    ],
)
def test_service_sanitizes_verified_open_failures(error: BaseException) -> None:
    """Map storage and operating-system details to one path-free read error."""

    scope = AttachmentScope(kind="chat", id="chat_open_error")
    repository = _BoundaryRepository(
        ownerships=(_ownership(scope),),
        records=(_record(),),
        open_error=error,
    )

    with pytest.raises(DocumentReadError, match="could not be") as caught:
        _boundary_service(repository).load(scope, "attachment_document")
    assert "private" not in str(caught.value).casefold()


@pytest.mark.parametrize(
    "error",
    [
        AttachmentStorageError(r"C:\private\attachment.blob"),
        OSError(r"C:\private\attachment.blob"),
    ],
)
def test_service_sanitizes_verified_stream_read_failures(
    error: BaseException,
) -> None:
    """Translate failures raised after entering the verified byte stream."""

    scope = AttachmentScope(kind="chat", id="chat_stream_error")
    repository = _BoundaryRepository(
        ownerships=(_ownership(scope),),
        records=(_record(),),
        stream=_RaisingStream(error),
    )

    with pytest.raises(DocumentReadError, match="could not be") as caught:
        _boundary_service(repository).load(scope, "attachment_document")
    assert "private" not in str(caught.value).casefold()


@pytest.mark.parametrize("metadata_kind", ["ownership", "record"])
@pytest.mark.parametrize(
    "error",
    [
        AttachmentStorageError(r"C:\private\manifest.json"),
        OSError(r"C:\private\manifest.json"),
    ],
)
def test_service_sanitizes_repository_metadata_failures(
    metadata_kind: str,
    error: BaseException,
) -> None:
    """Translate list failures without including a repository-native path."""

    scope = AttachmentScope(kind="chat", id="chat_metadata_error")
    repository = _BoundaryRepository(
        ownerships=(_ownership(scope),),
        records=(_record(),),
        ownership_error=error if metadata_kind == "ownership" else None,
        record_error=error if metadata_kind == "record" else None,
    )

    with pytest.raises(DocumentReadError, match="could not be read") as caught:
        _boundary_service(repository).load(scope, "attachment_document")
    assert "private" not in str(caught.value).casefold()


def test_service_rejects_non_document_loader_output() -> None:
    """Validate the runtime adapter result instead of trusting annotations."""

    scope = AttachmentScope(kind="chat", id="chat_invalid_result")
    repository = _BoundaryRepository(
        ownerships=(_ownership(scope),),
        records=(_record(),),
    )

    with pytest.raises(DocumentValidationError, match="invalid result"):
        _boundary_service(
            repository,
            loader=_InvalidResultLoader(),
        ).load(scope, "attachment_document")


@pytest.mark.parametrize(
    ("changed_field", "message"),
    [
        ("limits", "resource policy"),
        ("version", "version metadata"),
    ],
)
def test_service_rejects_loader_changes_to_policy_or_version(
    changed_field: str,
    message: str,
) -> None:
    """Pin output to the registered adapter identity and active limits."""

    scope = AttachmentScope(kind="chat", id="chat_changed_result")
    repository = _BoundaryRepository(
        ownerships=(_ownership(scope),),
        records=(_record(),),
    )

    with pytest.raises(DocumentValidationError, match=message):
        _boundary_service(
            repository,
            loader=_ChangedResultLoader(changed_field),
        ).load(scope, "attachment_document")


def test_service_preserves_typed_unsupported_feature_failures() -> None:
    """A loader capability mismatch crosses orchestration without wrapping."""

    scope = AttachmentScope(kind="chat", id="chat_unsupported_feature")
    repository = _BoundaryRepository(
        ownerships=(_ownership(scope),),
        records=(_record(),),
    )
    error = DocumentUnsupportedFeatureError("unsupported capability")

    with pytest.raises(DocumentUnsupportedFeatureError) as caught:
        _boundary_service(
            repository,
            loader=_RaisingLoader(error),
        ).load(scope, "attachment_document")

    assert caught.value is error


@pytest.mark.parametrize(
    ("adapter_error", "public_error", "message"),
    [
        (
            MemoryError(r"C:\private\parser-buffer"),
            DocumentContentLimitError,
            "resource limit",
        ),
        (
            RuntimeError(r"C:\private\parser-state"),
            DocumentLoadFailedError,
            "safe typed result",
        ),
    ],
)
def test_service_sanitizes_untyped_adapter_failures(
    adapter_error: BaseException,
    public_error: type[Exception],
    message: str,
) -> None:
    """Translate parser failures without leaking adapter-native details."""

    scope = AttachmentScope(kind="chat", id="chat_loader_error")
    repository = _BoundaryRepository(
        ownerships=(_ownership(scope),),
        records=(_record(),),
    )

    with pytest.raises(public_error, match=message) as caught:
        _boundary_service(
            repository,
            loader=_RaisingLoader(adapter_error),
        ).load(scope, "attachment_document")
    assert "private" not in str(caught.value).casefold()
