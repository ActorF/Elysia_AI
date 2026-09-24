"""Test path-free canonical file metadata and ownership invariants."""

from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone

import pytest

from attachments import (
    FILE_METADATA_SCHEMA_VERSION,
    MAX_JSON_SAFE_INTEGER,
    AttachmentScope,
    DerivedFileRelation,
    FileOrigin,
    FileOwnership,
    OriginalFileMetadata,
    file_id_from_sha256,
)

_FIRST_DIGEST = "a" * 64
_SECOND_DIGEST = "b" * 64


def _metadata() -> OriginalFileMetadata:
    """Build one valid canonical original for focused mutation tests."""

    return OriginalFileMetadata(
        schema_version=FILE_METADATA_SCHEMA_VERSION,
        file_id=f"file_{_FIRST_DIGEST}",
        sha256=_FIRST_DIGEST,
        file_name="course notes.md",
        media_type="text/markdown",
        size_bytes=42,
        origin="local_import",
        imported_at=datetime(2026, 9, 23, 12, 30, tzinfo=timezone.utc),
    )


def _ownership() -> FileOwnership:
    """Build one valid ownership link with import-specific display metadata."""

    return FileOwnership(
        schema_version=FILE_METADATA_SCHEMA_VERSION,
        link_id="attachment_lesson",
        file_id=f"file_{_FIRST_DIGEST}",
        file_name="lesson.md",
        media_type="text/markdown",
        scope=AttachmentScope(kind="chat", id="chat_lesson"),
        role="chat_attachment",
        imported_at=datetime(2026, 9, 23, 12, 31, tzinfo=timezone.utc),
    )


def test_file_domain_values_are_versioned_content_stable_and_path_free() -> None:
    """Keep paths out while preserving canonical IDs and ownership metadata."""

    metadata = _metadata()
    ownership = _ownership()
    relation = DerivedFileRelation(
        schema_version=FILE_METADATA_SCHEMA_VERSION,
        original_file_id=metadata.file_id,
        derived_file_id=f"file_{_SECOND_DIGEST}",
        scope=AttachmentScope(kind="chat", id="chat_lesson"),
        derivation_kind="normalized_text",
        producer_version="document-loader-1.0.0",
        created_at=datetime(2026, 9, 23, 12, 32, tzinfo=timezone.utc),
    )

    assert metadata.file_id == file_id_from_sha256(metadata.sha256)
    assert metadata.imported_at.utcoffset() == timedelta(0)
    assert ownership.scope.id == "chat_lesson"
    assert relation.original_file_id == metadata.file_id
    for value in (metadata, ownership, relation):
        assert all("path" not in field.name.casefold() for field in fields(value))


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"schema_version": 1.0},
        {"file_id": f"file_{_SECOND_DIGEST}"},
        {"file_id": f"file_{_FIRST_DIGEST.upper()}"},
        {"sha256": _FIRST_DIGEST.upper()},
        {"file_name": r"C:\private\notes.md"},
        {"media_type": "not a media type"},
        {"size_bytes": 0},
        {"size_bytes": True},
        {"size_bytes": MAX_JSON_SAFE_INTEGER + 1},
        {"origin": "download"},
        {"imported_at": datetime(2026, 9, 23, 12, 30)},
        {
            "imported_at": datetime(
                2026,
                9,
                23,
                12,
                30,
                tzinfo=timezone(timedelta(hours=8)),
            )
        },
    ],
)
def test_original_file_metadata_rejects_noncanonical_values(
    changes: dict[str, object],
) -> None:
    """Reject unstable IDs, unsafe metadata, and non-UTC import timestamps."""

    with pytest.raises(ValueError):
        replace(_metadata(), **changes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("origin"),
    ["local_import", "legacy_migration", "generated"],
)
def test_original_file_metadata_accepts_each_supported_origin(
    origin: FileOrigin,
) -> None:
    """Preserve the closed provenance vocabulary required by persistence."""

    assert replace(_metadata(), origin=origin).origin == origin


@pytest.mark.parametrize(
    ("scope", "role"),
    [
        (AttachmentScope(kind="chat", id="chat_one"), "project_source"),
        (AttachmentScope(kind="project", id="project_one"), "chat_attachment"),
    ],
)
def test_file_ownership_role_must_match_scope(
    scope: AttachmentScope,
    role: str,
) -> None:
    """Prevent a Chat attachment from masquerading as a Project Source."""

    with pytest.raises(ValueError, match="does not match"):
        FileOwnership(
            schema_version=FILE_METADATA_SCHEMA_VERSION,
            link_id="attachment_one",
            file_id=f"file_{_FIRST_DIGEST}",
            file_name="one.txt",
            media_type="text/plain",
            scope=scope,
            role=role,  # type: ignore[arg-type]
            imported_at=datetime(2026, 9, 23, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": 2},
        {"schema_version": 1.0},
        {"link_id": "attachment/escape"},
        {"file_id": f"file_{_FIRST_DIGEST.upper()}"},
        {"file_name": "../lesson.md"},
        {"media_type": "text"},
        {"imported_at": datetime(2026, 9, 23, 12, 31)},
    ],
)
def test_file_ownership_rejects_unsafe_import_specific_metadata(
    changes: dict[str, object],
) -> None:
    """Validate every owner-specific field independently of the original."""

    with pytest.raises(ValueError):
        replace(_ownership(), **changes)  # type: ignore[arg-type]


def test_derived_relation_rejects_self_reference_and_unstable_kind() -> None:
    """Keep derived ownership acyclic at its immediate relation boundary."""

    with pytest.raises(ValueError, match="differ"):
        DerivedFileRelation(
            schema_version=FILE_METADATA_SCHEMA_VERSION,
            original_file_id=f"file_{_FIRST_DIGEST}",
            derived_file_id=f"file_{_FIRST_DIGEST}",
            scope=AttachmentScope(kind="chat", id="chat_one"),
            derivation_kind="normalized_text",
            producer_version="loader-1",
            created_at=datetime(2026, 9, 23, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="lowercase identifier"):
        DerivedFileRelation(
            schema_version=FILE_METADATA_SCHEMA_VERSION,
            original_file_id=f"file_{_FIRST_DIGEST}",
            derived_file_id=f"file_{_SECOND_DIGEST}",
            scope=AttachmentScope(kind="chat", id="chat_one"),
            derivation_kind="Normalized Text",
            producer_version="loader-1",
            created_at=datetime(2026, 9, 23, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="schema version"):
        DerivedFileRelation(
            schema_version=1.0,  # type: ignore[arg-type]
            original_file_id=f"file_{_FIRST_DIGEST}",
            derived_file_id=f"file_{_SECOND_DIGEST}",
            scope=AttachmentScope(kind="chat", id="chat_one"),
            derivation_kind="normalized_text",
            producer_version="loader-1",
            created_at=datetime(2026, 9, 23, tzinfo=timezone.utc),
        )


def test_ownership_and_derived_values_require_stable_metadata() -> None:
    """Reject unsafe link metadata and unverifiable producer timestamps."""

    with pytest.raises(ValueError, match="attachment_id"):
        FileOwnership(
            schema_version=FILE_METADATA_SCHEMA_VERSION,
            link_id="../attachment_escape",
            file_id=f"file_{_FIRST_DIGEST}",
            file_name="one.txt",
            media_type="text/plain",
            scope=AttachmentScope(kind="chat", id="chat_one"),
            role="chat_attachment",
            imported_at=datetime(2026, 9, 23, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="producer_version"):
        DerivedFileRelation(
            schema_version=FILE_METADATA_SCHEMA_VERSION,
            original_file_id=f"file_{_FIRST_DIGEST}",
            derived_file_id=f"file_{_SECOND_DIGEST}",
            scope=AttachmentScope(kind="chat", id="chat_one"),
            derivation_kind="normalized_text",
            producer_version="",
            created_at=datetime(2026, 9, 23, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="created_at"):
        DerivedFileRelation(
            schema_version=FILE_METADATA_SCHEMA_VERSION,
            original_file_id=f"file_{_FIRST_DIGEST}",
            derived_file_id=f"file_{_SECOND_DIGEST}",
            scope=AttachmentScope(kind="chat", id="chat_one"),
            derivation_kind="normalized_text",
            producer_version="loader-1",
            created_at=datetime(2026, 9, 23),
        )
