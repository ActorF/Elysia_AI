"""Profile schema for Elysia's persistent user data."""

from typing import Final, Literal, TypedDict, cast

PROFILE_SCHEMA_VERSION: Final = 1


class Profile(TypedDict):
    """Describe durable user, assistant, language, and project preferences."""

    schema_version: Literal[1]
    user_name: str
    assistant_name: str
    languages: list[str]
    project: str
    launch_count: int


# Exact-field validation catches stale or misspelled persisted profile data.
_PROFILE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "user_name",
        "assistant_name",
        "languages",
        "project",
        "launch_count",
    }
)


def validate_profile(data: object) -> Profile:
    """Validate untrusted decoded JSON against the current profile schema.

    Returns:
        The same mapping narrowed to ``Profile`` after every field is checked.

    Raises:
        ValueError: If the object has missing, unknown, or invalid fields.
    """

    if not isinstance(data, dict):
        raise ValueError(
            "Profile must be a JSON object."
        )

    if not all(
        isinstance(field_name, str)
        for field_name in data
    ):
        raise ValueError(
            "Profile field names must be strings."
        )

    profile_data = cast(dict[str, object], data)
    actual_fields = set(profile_data)

    missing_fields = (
        _PROFILE_FIELDS - actual_fields
    )
    unknown_fields = (
        actual_fields - _PROFILE_FIELDS
    )

    if missing_fields:
        raise ValueError(
            "Profile is missing required fields: "
            f"{', '.join(sorted(missing_fields))}."
        )

    if unknown_fields:
        raise ValueError(
            "Profile contains unknown fields: "
            f"{', '.join(sorted(unknown_fields))}."
        )

    schema_version = profile_data[
        "schema_version"
    ]

    # ``bool`` is an ``int`` subclass, so reject it explicitly for versions.
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
    ):
        raise ValueError(
            "schema_version must be an integer."
        )

    if schema_version != PROFILE_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported profile schema version: "
            f"{schema_version}."
        )

    for field_name in (
        "user_name",
        "assistant_name",
        "project",
    ):
        if not isinstance(
            profile_data[field_name],
            str,
        ):
            raise ValueError(
                f"{field_name} must be a string."
            )

    languages = profile_data["languages"]

    if (
        not isinstance(languages, list)
        or not all(
            isinstance(language, str)
            for language in languages
        )
    ):
        raise ValueError(
            "languages must be a list of strings."
        )

    launch_count = profile_data["launch_count"]

    # Apply the same bool guard to counters persisted as JSON integers.
    if (
        not isinstance(launch_count, int)
        or isinstance(launch_count, bool)
        or launch_count < 0
    ):
        raise ValueError(
            "launch_count must be a "
            "non-negative integer."
        )

    return cast(Profile, profile_data)


def migrate_profile(data: object) -> Profile:
    """Upgrade a legacy versionless profile, then run full validation.

    Versioned input is never guessed or rewritten; it must already satisfy the
    declared schema. Only the known pre-version format receives defaults.
    """

    if not isinstance(data, dict):
        return validate_profile(data)

    if "schema_version" in data:
        return validate_profile(data)

    # Work on a copy so migration never mutates the decoded caller-owned data.
    migrated_data = dict(data)
    migrated_data["schema_version"] = (
        PROFILE_SCHEMA_VERSION
    )
    migrated_data.setdefault("launch_count", 0)

    return validate_profile(migrated_data)
