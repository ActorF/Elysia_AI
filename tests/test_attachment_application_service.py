"""Test the path-private attachment application service contract."""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from attachments import (
    FILE_METADATA_SCHEMA_VERSION,
    AttachmentRepository,
    AttachmentScope,
    AttachmentService,
    AttachmentValidationError,
    DerivedFileRelation,
    FileCatalogSnapshot,
    FileOwnership,
    OriginalFileMetadata,
)

_ORIGINAL_DIGEST = "c" * 64
_DERIVED_DIGEST = "d" * 64


def _file_record() -> OriginalFileMetadata:
    """Build one canonical file record returned by the fake repository."""

    return OriginalFileMetadata(
        schema_version=FILE_METADATA_SCHEMA_VERSION,
        file_id=f"file_{_ORIGINAL_DIGEST}",
        sha256=_ORIGINAL_DIGEST,
        file_name="notes.txt",
        media_type="text/plain",
        size_bytes=5,
        origin="local_import",
        imported_at=datetime(2026, 9, 23, tzinfo=timezone.utc),
    )


class _FakeRepository:
    """Record service delegation without implementing filesystem behavior."""

    def __init__(self) -> None:
        self.closed = False
        self.opened: tuple[AttachmentScope, str] | None = None
        self.single_delete_scope: AttachmentScope | None = None
        self.multi_delete_scopes: tuple[AttachmentScope, ...] = ()
        self.rollback_observed = False
        self.registered_relation: DerivedFileRelation | None = None
        self.project_source_removal: tuple[AttachmentScope, str, str] | None = None
        self.record = _file_record()
        self.ownership = FileOwnership(
            schema_version=FILE_METADATA_SCHEMA_VERSION,
            link_id="attachment_notes",
            file_id=self.record.file_id,
            file_name="notes.txt",
            media_type="text/plain",
            scope=AttachmentScope(kind="chat", id="chat_notes"),
            role="chat_attachment",
            imported_at=datetime(2026, 9, 23, tzinfo=timezone.utc),
        )
        self.relation = DerivedFileRelation(
            schema_version=FILE_METADATA_SCHEMA_VERSION,
            original_file_id=self.record.file_id,
            derived_file_id=f"file_{_DERIVED_DIGEST}",
            scope=self.ownership.scope,
            derivation_kind="normalized_text",
            producer_version="loader-1",
            created_at=datetime(2026, 9, 23, 0, 1, tzinfo=timezone.utc),
        )

    @property
    def max_file_bytes(self) -> int:
        """Return a deterministic byte limit for delegation checks."""

        return 1_024

    @property
    def max_file_count(self) -> int:
        """Return a deterministic count limit for delegation checks."""

        return 10

    def list_file_records(
        self,
        scope: AttachmentScope,
    ) -> tuple[OriginalFileMetadata, ...]:
        """Return the canonical record visible in the requested scope."""

        assert scope == self.ownership.scope
        return (self.record,)

    def list_file_ownerships(
        self,
        scope: AttachmentScope,
    ) -> tuple[FileOwnership, ...]:
        """Return the fake path-free ownership link."""

        assert scope == self.ownership.scope
        return (self.ownership,)

    def list_derived_relations(
        self,
        scope: AttachmentScope,
    ) -> tuple[DerivedFileRelation, ...]:
        """Return the fake relation for the requested owner scope."""

        assert scope == self.ownership.scope
        return (self.relation,)

    def register_derived_relation(
        self,
        scope: AttachmentScope,
        relation: DerivedFileRelation,
    ) -> None:
        """Record a derived relation only for its exact owner scope."""

        assert scope == relation.scope
        self.registered_relation = relation

    def remove_project_source(
        self,
        scope: AttachmentScope,
        link_id: str,
        expected_snapshot_fingerprint: str,
    ) -> FileCatalogSnapshot:
        """Record one guarded Project Source removal request."""

        self.project_source_removal = (
            scope,
            link_id,
            expected_snapshot_fingerprint,
        )
        return FileCatalogSnapshot(
            schema_version=1,
            scope=scope,
            originals=(),
            ownerships=(),
        )

    def open_verified_file(
        self,
        scope: AttachmentScope,
        file_id: str,
    ) -> AbstractContextManager[BinaryIO]:
        """Return verified in-memory bytes while recording opaque selectors."""

        self.opened = (scope, file_id)
        return nullcontext(cast(BinaryIO, BytesIO(b"notes")))

    def delete_scope_with(
        self,
        scope: AttachmentScope,
        delete_owner: Callable[[], None],
    ) -> None:
        """Simulate the repository's single-owner rollback boundary."""

        self.single_delete_scope = scope
        try:
            delete_owner()
        except Exception:
            self.rollback_observed = True
            raise

    def delete_scopes_with(
        self,
        scopes: Sequence[AttachmentScope],
        delete_owners: Callable[[], None],
    ) -> None:
        """Simulate one all-owner transaction for a Project cascade."""

        self.multi_delete_scopes = tuple(scopes)
        try:
            delete_owners()
        except Exception:
            self.rollback_observed = True
            raise

    def close(self) -> None:
        """Record release of repository resources."""

        self.closed = True


def _service(repository: _FakeRepository) -> AttachmentService:
    """Cast the focused fake to the complete structural repository contract."""

    return AttachmentService(cast(AttachmentRepository, repository))


def test_service_exposes_verified_records_without_paths() -> None:
    """Delegate opaque record, ownership, relation, and verified-byte reads."""

    repository = _FakeRepository()
    service = _service(repository)
    scope = repository.ownership.scope

    assert service.max_file_bytes == 1_024
    assert service.max_file_count == 10
    assert service.list_file_records(scope) == (repository.record,)
    assert service.list_file_ownerships(scope) == (repository.ownership,)
    assert service.list_derived_relations(scope) == (repository.relation,)
    service.register_derived_relation(scope, repository.relation)
    assert repository.registered_relation == repository.relation
    with service.open_verified_file(scope, repository.record.file_id) as stream:
        assert stream.read() == b"notes"
    assert repository.opened == (scope, repository.record.file_id)

    with pytest.raises(AttachmentValidationError, match="identifier"):
        service.open_verified_file(scope, r"C:\private\notes.txt")
    with pytest.raises(AttachmentValidationError, match="scope"):
        service.register_derived_relation(
            AttachmentScope(kind="chat", id="chat_other"),
            repository.relation,
        )


def test_service_rejects_scalar_sources_and_invalid_relations_at_boundary() -> None:
    """Normalize malformed inputs before an injected repository sees them."""

    repository = _FakeRepository()
    service = _service(repository)
    scope = repository.ownership.scope

    with pytest.raises(AttachmentValidationError, match="sequence"):
        service.stage_files(scope, cast(Sequence[Path], "notes.txt"))
    with pytest.raises(AttachmentValidationError, match="relationship"):
        service.register_derived_relation(
            scope,
            cast(DerivedFileRelation, object()),
        )


def test_service_delegates_guarded_project_source_removal() -> None:
    """Pass the exact scope, link, and catalog guard to the repository."""

    repository = _FakeRepository()
    service = _service(repository)
    scope = AttachmentScope(kind="project", id="project_notes")
    fingerprint = "e" * 64

    result = service.remove_project_source(
        scope,
        "attachment_notes",
        fingerprint,
    )

    assert result.scope == scope
    assert result.ownerships == ()
    assert repository.project_source_removal == (
        scope,
        "attachment_notes",
        fingerprint,
    )


def test_chat_owner_failure_is_left_inside_repository_rollback_boundary() -> None:
    """Propagate owner failure only after the repository observes rollback."""

    repository = _FakeRepository()
    service = _service(repository)

    def fail_owner_delete() -> None:
        """Represent a canonical Chat deletion that cannot commit."""

        raise RuntimeError("Chat stayed canonical")

    with pytest.raises(RuntimeError, match="stayed canonical"):
        service.delete_chat_owner("chat_notes", fail_owner_delete)

    assert repository.single_delete_scope == AttachmentScope(
        kind="chat",
        id="chat_notes",
    )
    assert repository.rollback_observed is True


def test_project_and_linked_chats_use_one_repository_transaction() -> None:
    """Keep Project Sources and linked Chat attachments in one delete rollback."""

    repository = _FakeRepository()
    service = _service(repository)
    owner_deleted = False

    def delete_owners() -> None:
        """Record the canonical aggregate deletion callback."""

        nonlocal owner_deleted
        owner_deleted = True

    service.delete_project_owner(
        "project_course",
        delete_owners,
        linked_chat_ids=("chat_one", "chat_two"),
    )

    assert owner_deleted is True
    assert repository.multi_delete_scopes == (
        AttachmentScope(kind="project", id="project_course"),
        AttachmentScope(kind="chat", id="chat_one"),
        AttachmentScope(kind="chat", id="chat_two"),
    )


def test_multi_owner_delete_rejects_duplicate_scopes_before_repository_work() -> None:
    """Reject ambiguous rollback sets before destructive repository work."""

    repository = _FakeRepository()
    service = _service(repository)
    scope = AttachmentScope(kind="chat", id="chat_duplicate")

    with pytest.raises(ValueError, match="unique"):
        service.delete_owners((scope, scope), lambda: None)

    assert repository.multi_delete_scopes == ()


def test_service_close_releases_the_repository() -> None:
    """Keep process-lock ownership behind the application service boundary."""

    repository = _FakeRepository()
    _service(repository).close()
    assert repository.closed is True
