"""Define versioned Project Source generations, snapshots, and answers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import json
import re
from typing import Final, Literal

from attachments import AttachmentScope
from attachments.domain import validate_attachment_id
from chats import ChatId, ProjectId
from documents import (
    MAX_RETRIEVAL_DOCUMENTS,
    ExpectedDocumentGeneration,
    GroundedAnswerStyle,
    GroundedAnswerResult,
)

from .exceptions import ProjectSourceValidationError


PROJECT_SOURCE_GENERATION_SCHEMA_VERSION: Final[Literal[1]] = 1
PROJECT_SOURCE_SNAPSHOT_SCHEMA_VERSION: Final[Literal[1]] = 1
PROJECT_SOURCE_ANSWER_SCHEMA_VERSION: Final[Literal[1]] = 1
PROJECT_SOURCE_INSTRUCTIONS_SCHEMA_VERSION: Final[Literal[1]] = 1

_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_SNAPSHOT_FINGERPRINT_DOMAIN: Final = "elysia.project-source-snapshot.v1"
_ANSWER_STYLES: Final = ("default", "concise", "balanced", "detailed")


def _canonical_digest(value: object) -> str:
    """Hash one explicitly shaped value as canonical UTF-8 JSON."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_digest(value: object, field_name: str) -> str:
    """Return one exact lowercase SHA-256 digest."""

    if type(value) is not str or _DIGEST_PATTERN.fullmatch(value) is None:
        raise ProjectSourceValidationError(
            f"{field_name} must be a lowercase SHA-256 digest."
        )
    return value


def _validate_utc_timestamp(value: object, field_name: str) -> datetime:
    """Return one timezone-aware zero-offset timestamp."""

    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ProjectSourceValidationError(
            f"{field_name} must be a UTC-aware datetime."
        )
    return value


def _generation_payload(source: "ProjectSourceGeneration") -> dict[str, object]:
    """Project one generation into its fingerprint-bearing public values."""

    document = source.generation
    metadata = document.source
    return {
        "schema_version": source.schema_version,
        "source": {
            "scope": {
                "kind": metadata.scope.kind,
                "id": metadata.scope.id,
            },
            "link_id": metadata.link_id,
            "file_id": metadata.file_id,
            "file_name": metadata.file_name,
            "media_type": metadata.media_type,
            "size_bytes": metadata.size_bytes,
        },
        "derivation_fingerprint": document.derivation_fingerprint,
        "index_profile_fingerprint": source.index_profile_fingerprint,
        "published_at": source.published_at.isoformat(),
    }


@dataclass(frozen=True, slots=True)
class ProjectSourceInstructions:
    """Persist bounded, non-authorizing Project answer preferences.

    Preferred link IDs are meaningful only inside the exact catalog snapshot
    that contains this value.  They can change presentation order but cannot
    create ownership, generations, evidence, or citations.
    """

    schema_version: Literal[1]
    preferred_source_link_ids: tuple[str, ...] = ()
    answer_style: GroundedAnswerStyle = "default"

    def __post_init__(self) -> None:
        """Validate a unique bounded source order and closed answer style."""

        if (
            type(self.schema_version) is not int
            or self.schema_version != PROJECT_SOURCE_INSTRUCTIONS_SCHEMA_VERSION
        ):
            raise ProjectSourceValidationError(
                "Unsupported Project Source instructions schema version."
            )
        if type(self.preferred_source_link_ids) is not tuple:
            raise ProjectSourceValidationError(
                "Preferred Project Source link IDs must be an exact tuple."
            )
        if len(self.preferred_source_link_ids) > MAX_RETRIEVAL_DOCUMENTS:
            raise ProjectSourceValidationError(
                "Preferred Project Source link IDs exceed the safe limit."
            )
        try:
            preferred = tuple(
                validate_attachment_id(value)
                for value in self.preferred_source_link_ids
            )
        except (TypeError, ValueError) as error:
            raise ProjectSourceValidationError(
                "Preferred Project Source link ID is invalid."
            ) from error
        if len(set(preferred)) != len(preferred):
            raise ProjectSourceValidationError(
                "Preferred Project Source link IDs must be unique."
            )
        if type(self.answer_style) is not str or self.answer_style not in (
            _ANSWER_STYLES
        ):
            raise ProjectSourceValidationError(
                "Project Source answer style is invalid."
            )
        object.__setattr__(self, "preferred_source_link_ids", preferred)


@dataclass(frozen=True, slots=True)
class ProjectSourceGeneration:
    """Publish one exact indexed generation under an audited index profile."""

    schema_version: Literal[1]
    generation: ExpectedDocumentGeneration
    index_profile_fingerprint: str
    published_at: datetime

    def __post_init__(self) -> None:
        """Reject non-Project, malformed, stale-profile, or naive values."""

        if (
            type(self.schema_version) is not int
            or self.schema_version != PROJECT_SOURCE_GENERATION_SCHEMA_VERSION
        ):
            raise ProjectSourceValidationError(
                "Unsupported Project Source generation schema version."
            )
        if type(self.generation) is not ExpectedDocumentGeneration:
            raise ProjectSourceValidationError(
                "Project Source generation is invalid."
            )
        if (
            type(self.generation.source.scope) is not AttachmentScope
            or self.generation.source.scope.kind != "project"
        ):
            raise ProjectSourceValidationError(
                "Project Source generation must use a Project scope."
            )
        _validate_digest(
            self.index_profile_fingerprint,
            "index_profile_fingerprint",
        )
        _validate_utc_timestamp(self.published_at, "published_at")


def _snapshot_fingerprint(
    scope: AttachmentScope,
    revision: int,
    source_catalog_fingerprint: str,
    sources: tuple[ProjectSourceGeneration, ...],
    instructions: ProjectSourceInstructions,
) -> str:
    """Bind one catalog revision to exact ownership and generations."""

    return _canonical_digest(
        {
            "fingerprint_domain": _SNAPSHOT_FINGERPRINT_DOMAIN,
            "schema_version": PROJECT_SOURCE_SNAPSHOT_SCHEMA_VERSION,
            "scope": {"kind": scope.kind, "id": scope.id},
            "revision": revision,
            "source_catalog_fingerprint": source_catalog_fingerprint,
            "sources": [_generation_payload(source) for source in sources],
            "instructions": {
                "schema_version": instructions.schema_version,
                "preferred_source_link_ids": list(
                    instructions.preferred_source_link_ids
                ),
                "answer_style": instructions.answer_style,
            },
        }
    )


@dataclass(frozen=True, slots=True)
class ProjectSourceSnapshot:
    """Bind a CAS revision to the complete current Project corpus.

    The attachment catalog fingerprint proves which ownership view indexing
    consumed.  The index-profile fingerprint on each generation prevents an
    old vector row from declaring itself current after processing policy or
    embedding identity changes.
    """

    schema_version: Literal[1]
    scope: AttachmentScope
    revision: int
    source_catalog_fingerprint: str
    sources: tuple[ProjectSourceGeneration, ...]
    instructions: ProjectSourceInstructions = field(
        default_factory=lambda: ProjectSourceInstructions(
            schema_version=PROJECT_SOURCE_INSTRUCTIONS_SCHEMA_VERSION
        )
    )
    snapshot_fingerprint: str = ""

    def __post_init__(self) -> None:
        """Canonicalize source order and validate a complete Project snapshot."""

        if (
            type(self.schema_version) is not int
            or self.schema_version != PROJECT_SOURCE_SNAPSHOT_SCHEMA_VERSION
        ):
            raise ProjectSourceValidationError(
                "Unsupported Project Source snapshot schema version."
            )
        if type(self.scope) is not AttachmentScope or self.scope.kind != "project":
            raise ProjectSourceValidationError(
                "Project Source snapshot must use a Project scope."
            )
        if type(self.revision) is not int or self.revision <= 0:
            raise ProjectSourceValidationError(
                "Project Source snapshot revision must be positive."
            )
        _validate_digest(
            self.source_catalog_fingerprint,
            "source_catalog_fingerprint",
        )
        if type(self.sources) is not tuple:
            raise ProjectSourceValidationError(
                "Project Source snapshot sources are invalid."
            )
        if len(self.sources) > MAX_RETRIEVAL_DOCUMENTS:
            raise ProjectSourceValidationError(
                "Project Source snapshot exceeds the retrieval document limit."
            )
        if not all(
            type(source) is ProjectSourceGeneration for source in self.sources
        ):
            raise ProjectSourceValidationError(
                "Project Source snapshot sources are invalid."
            )
        if type(self.instructions) is not ProjectSourceInstructions:
            raise ProjectSourceValidationError(
                "Project Source snapshot instructions are invalid."
            )
        sources = tuple(
            sorted(
                self.sources,
                key=lambda source: source.generation.source.link_id,
            )
        )
        link_ids = tuple(source.generation.source.link_id for source in sources)
        if len(set(link_ids)) != len(link_ids):
            raise ProjectSourceValidationError(
                "Project Source snapshot contains duplicate links."
            )
        if any(
            source.generation.source.scope != self.scope for source in sources
        ):
            raise ProjectSourceValidationError(
                "Project Source snapshot crosses its Project scope."
            )
        authorized_link_ids = set(link_ids)
        if any(
            link_id not in authorized_link_ids
            for link_id in self.instructions.preferred_source_link_ids
        ):
            raise ProjectSourceValidationError(
                "Project Source instructions reference an unknown source."
            )
        expected = _snapshot_fingerprint(
            self.scope,
            self.revision,
            self.source_catalog_fingerprint,
            sources,
            self.instructions,
        )
        if self.snapshot_fingerprint and self.snapshot_fingerprint != expected:
            raise ProjectSourceValidationError(
                "Project Source snapshot fingerprint does not match."
            )
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "snapshot_fingerprint", expected)


@dataclass(frozen=True, slots=True)
class ProjectSourceAnswer:
    """Bind one grounded result to its authorized Chat and Project."""

    schema_version: Literal[1]
    chat_id: ChatId
    project_id: ProjectId
    answer: GroundedAnswerResult

    def __post_init__(self) -> None:
        """Require stable owner IDs and an answer from the exact Project scope."""

        if (
            type(self.schema_version) is not int
            or self.schema_version != PROJECT_SOURCE_ANSWER_SCHEMA_VERSION
        ):
            raise ProjectSourceValidationError(
                "Unsupported Project Source answer schema version."
            )
        if type(self.chat_id) is not str or type(self.project_id) is not str:
            raise ProjectSourceValidationError(
                "Project Source answer owner identity is invalid."
            )
        try:
            AttachmentScope(kind="chat", id=self.chat_id)
            project_scope = AttachmentScope(
                kind="project",
                id=self.project_id,
            )
        except (TypeError, ValueError) as error:
            raise ProjectSourceValidationError(
                "Project Source answer owner identity is invalid."
            ) from error
        if type(self.answer) is not GroundedAnswerResult:
            raise ProjectSourceValidationError(
                "Project Source grounded answer is invalid."
            )
        if self.answer.scope != project_scope:
            raise ProjectSourceValidationError(
                "Project Source answer crosses its authorized Project."
            )
