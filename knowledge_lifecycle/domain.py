"""Define the versioned, path-private knowledge-operation journal domain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Final, Literal, NewType, cast
from uuid import uuid4

from attachments import AttachmentScope
from attachments.domain import MAX_JSON_SAFE_INTEGER, validate_attachment_id
from project_sources.domain import (
    PROJECT_SOURCE_INSTRUCTIONS_SCHEMA_VERSION,
    ProjectSourceInstructions,
)
from project_sources.exceptions import ProjectSourceValidationError

from .exceptions import KnowledgeLifecycleValidationError


KNOWLEDGE_OPERATION_SCHEMA_VERSION: Final[Literal[1]] = 1
MAX_KNOWLEDGE_OPERATION_ATTEMPTS: Final = 8
MAX_KNOWLEDGE_ERROR_CODE_LENGTH: Final = 64

KnowledgeOperationId = NewType("KnowledgeOperationId", str)
KnowledgeOperationKind = Literal[
    "add",
    "replace",
    "reindex",
    "rebuild",
    "revoke",
    "delete",
]
KnowledgeOperationState = Literal[
    "running",
    "cancel_requested",
    "recovery_required",
    "succeeded",
    "cancelled",
    "failed",
]
KnowledgeOperationPhase = Literal[
    "preparing",
    "importing",
    "indexing",
    "revoking",
    "cleaning",
    "publishing",
    "completed",
]

_OPERATION_ID_PATTERN: Final = re.compile(r"^knowledge_[0-9a-f]{32}$")
_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_OPERATION_KINDS: Final = frozenset(
    {"add", "replace", "reindex", "rebuild", "revoke", "delete"}
)
_OPERATION_STATES: Final = frozenset(
    {
        "running",
        "cancel_requested",
        "recovery_required",
        "succeeded",
        "cancelled",
        "failed",
    }
)
_OPERATION_PHASES: Final = frozenset(
    {
        "preparing",
        "importing",
        "indexing",
        "revoking",
        "cleaning",
        "publishing",
        "completed",
    }
)


def generate_knowledge_operation_id() -> KnowledgeOperationId:
    """Return an opaque operation ID independent of files and display text."""

    return KnowledgeOperationId(f"knowledge_{uuid4().hex}")


def validate_knowledge_operation_id(value: object) -> KnowledgeOperationId:
    """Return one exact lowercase opaque knowledge-operation identifier."""

    if type(value) is not str or _OPERATION_ID_PATTERN.fullmatch(value) is None:
        raise KnowledgeLifecycleValidationError(
            "Knowledge operation identifier is invalid."
        )
    return KnowledgeOperationId(value)


def _validate_digest(
    value: object,
    field_name: str,
    *,
    optional: bool = False,
) -> str | None:
    """Validate a required or optional canonical SHA-256 fingerprint."""

    if optional and value is None:
        return None
    if type(value) is not str or _DIGEST_PATTERN.fullmatch(value) is None:
        raise KnowledgeLifecycleValidationError(
            f"{field_name} must be a lowercase SHA-256 fingerprint."
        )
    return value


def _validate_optional_link_id(value: object, field_name: str) -> str | None:
    """Validate one optional opaque ownership link without resolving bytes."""

    if value is None:
        return None
    try:
        return validate_attachment_id(value)
    except (TypeError, ValueError) as error:
        raise KnowledgeLifecycleValidationError(
            f"{field_name} is invalid."
        ) from error


def _validate_utc_timestamp(value: object, field_name: str) -> datetime:
    """Require one exact timezone-aware UTC timestamp."""

    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise KnowledgeLifecycleValidationError(
            f"{field_name} must be a UTC-aware datetime."
        )
    return value


def _snapshot_project_scope(value: object) -> AttachmentScope:
    """Rebuild an exact Project scope so subclasses cannot alter authority."""

    if type(value) is not AttachmentScope:
        raise KnowledgeLifecycleValidationError(
            "Knowledge operation scope is invalid."
        )
    try:
        scope = AttachmentScope(kind=value.kind, id=value.id)
    except (TypeError, ValueError) as error:
        raise KnowledgeLifecycleValidationError(
            "Knowledge operation scope is invalid."
        ) from error
    if scope.kind != "project":
        raise KnowledgeLifecycleValidationError(
            "Knowledge lifecycle operations require a Project scope."
        )
    return scope


def _snapshot_instructions(value: object) -> ProjectSourceInstructions:
    """Detach the bounded non-authorizing instruction policy."""

    if type(value) is not ProjectSourceInstructions:
        raise KnowledgeLifecycleValidationError(
            "Knowledge operation instructions are invalid."
        )
    try:
        return ProjectSourceInstructions(
            schema_version=cast(
                Literal[1],
                value.schema_version,
            ),
            preferred_source_link_ids=tuple(value.preferred_source_link_ids),
            answer_style=value.answer_style,
        )
    except (ProjectSourceValidationError, TypeError, ValueError) as error:
        raise KnowledgeLifecycleValidationError(
            "Knowledge operation instructions are invalid."
        ) from error


@dataclass(frozen=True, slots=True)
class KnowledgeOperationSnapshot:
    """Persist one bounded Project-only saga checkpoint.

    The value deliberately contains only opaque identifiers, policy
    fingerprints, closed status tokens, and bounded Project Source
    preferences. It cannot carry a local path, source content, vector,
    content digest, or traceback across the lifecycle journal boundary.
    """

    schema_version: Literal[1]
    journal_revision: int
    operation_id: KnowledgeOperationId
    scope: AttachmentScope
    kind: KnowledgeOperationKind
    state: KnowledgeOperationState
    phase: KnowledgeOperationPhase
    progress_percent: int
    catalog_revision: int
    index_profile_fingerprint: str
    instructions: ProjectSourceInstructions
    attempt: int
    created_at: datetime
    updated_at: datetime
    target_link_id: str | None = None
    staged_link_id: str | None = None
    source_catalog_fingerprint: str | None = None
    catalog_snapshot_fingerprint: str | None = None
    started_from_revoked_catalog: bool = False
    error_code: str | None = None

    def __post_init__(self) -> None:
        """Validate and detach one exact journal checkpoint."""

        if (
            type(self.schema_version) is not int
            or self.schema_version != KNOWLEDGE_OPERATION_SCHEMA_VERSION
        ):
            raise KnowledgeLifecycleValidationError(
                "Unsupported knowledge operation schema version."
            )
        if (
            type(self.journal_revision) is not int
            or not 1 <= self.journal_revision <= MAX_JSON_SAFE_INTEGER
        ):
            raise KnowledgeLifecycleValidationError(
                "journal_revision must be a positive JSON-safe integer."
            )
        operation_id = validate_knowledge_operation_id(self.operation_id)
        scope = _snapshot_project_scope(self.scope)
        if type(self.kind) is not str or self.kind not in _OPERATION_KINDS:
            raise KnowledgeLifecycleValidationError(
                "Knowledge operation kind is invalid."
            )
        if type(self.state) is not str or self.state not in _OPERATION_STATES:
            raise KnowledgeLifecycleValidationError(
                "Knowledge operation state is invalid."
            )
        if type(self.phase) is not str or self.phase not in _OPERATION_PHASES:
            raise KnowledgeLifecycleValidationError(
                "Knowledge operation phase is invalid."
            )
        if (
            type(self.progress_percent) is not int
            or not 0 <= self.progress_percent <= 100
        ):
            raise KnowledgeLifecycleValidationError(
                "progress_percent must be an integer from 0 through 100."
            )
        if (
            type(self.catalog_revision) is not int
            or not 0 <= self.catalog_revision <= MAX_JSON_SAFE_INTEGER
        ):
            raise KnowledgeLifecycleValidationError(
                "catalog_revision must be a non-negative JSON-safe integer."
            )
        index_profile = _validate_digest(
            self.index_profile_fingerprint,
            "index_profile_fingerprint",
        )
        instructions = _snapshot_instructions(self.instructions)
        if (
            type(self.attempt) is not int
            or not 1 <= self.attempt <= MAX_KNOWLEDGE_OPERATION_ATTEMPTS
        ):
            raise KnowledgeLifecycleValidationError(
                "attempt must be an integer from 1 through 8."
            )
        created_at = _validate_utc_timestamp(self.created_at, "created_at")
        updated_at = _validate_utc_timestamp(self.updated_at, "updated_at")
        if updated_at < created_at:
            raise KnowledgeLifecycleValidationError(
                "updated_at cannot precede created_at."
            )
        target_link_id = _validate_optional_link_id(
            self.target_link_id,
            "target_link_id",
        )
        staged_link_id = _validate_optional_link_id(
            self.staged_link_id,
            "staged_link_id",
        )
        source_fingerprint = _validate_digest(
            self.source_catalog_fingerprint,
            "source_catalog_fingerprint",
            optional=True,
        )
        catalog_fingerprint = _validate_digest(
            self.catalog_snapshot_fingerprint,
            "catalog_snapshot_fingerprint",
            optional=True,
        )
        if type(self.started_from_revoked_catalog) is not bool:
            raise KnowledgeLifecycleValidationError(
                "started_from_revoked_catalog must be a boolean."
            )
        if self.error_code is not None and (
            type(self.error_code) is not str
            or len(self.error_code) > MAX_KNOWLEDGE_ERROR_CODE_LENGTH
            or _ERROR_CODE_PATTERN.fullmatch(self.error_code) is None
        ):
            raise KnowledgeLifecycleValidationError(
                "error_code must be a bounded lowercase token."
            )

        object.__setattr__(self, "operation_id", operation_id)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "index_profile_fingerprint", index_profile)
        object.__setattr__(self, "instructions", instructions)
        object.__setattr__(self, "target_link_id", target_link_id)
        object.__setattr__(self, "staged_link_id", staged_link_id)
        object.__setattr__(
            self,
            "source_catalog_fingerprint",
            source_fingerprint,
        )
        object.__setattr__(
            self,
            "catalog_snapshot_fingerprint",
            catalog_fingerprint,
        )
