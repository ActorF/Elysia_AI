import re

import pytest

from memory.profile import (
    migrate_profile,
    validate_profile,
)


def _valid_profile_data() -> dict[str, object]:
    return {
        "schema_version": 1,
        "user_name": "Ying",
        "assistant_name": "Elysia",
        "languages": ["Chinese", "English"],
        "project": "Elysia AI",
        "launch_count": 0,
    }


def _legacy_profile_data() -> dict[str, object]:
    return {
        "user_name": "Ying",
        "assistant_name": "Elysia",
        "languages": ["Chinese", "English"],
        "project": "Elysia AI",
    }


def test_validate_profile_accepts_valid_profile() -> None:
    profile_data = _valid_profile_data()

    profile = validate_profile(profile_data)

    assert profile == profile_data


def test_validate_profile_rejects_non_object() -> None:
    with pytest.raises(
        ValueError,
        match=r"Profile must be a JSON object\.",
    ):
        validate_profile([])


def test_validate_profile_rejects_missing_field() -> None:
    profile_data = _valid_profile_data()
    del profile_data["project"]

    with pytest.raises(
        ValueError,
        match=r"Profile is missing required fields: project\.",
    ):
        validate_profile(profile_data)


def test_validate_profile_rejects_unknown_field() -> None:
    profile_data = _valid_profile_data()
    profile_data["unexpected"] = True

    with pytest.raises(
        ValueError,
        match=r"Profile contains unknown fields: unexpected\.",
    ):
        validate_profile(profile_data)


def test_validate_profile_rejects_unsupported_version() -> None:
    profile_data = _valid_profile_data()
    profile_data["schema_version"] = 2

    with pytest.raises(
        ValueError,
        match=r"Unsupported profile schema version: 2\.",
    ):
        validate_profile(profile_data)


@pytest.mark.parametrize(
    (
        "field_name",
        "invalid_value",
        "expected_message",
    ),
    [
        (
            "schema_version",
            True,
            "schema_version must be an integer.",
        ),
        (
            "user_name",
            123,
            "user_name must be a string.",
        ),
        (
            "assistant_name",
            None,
            "assistant_name must be a string.",
        ),
        (
            "languages",
            ["Chinese", 123],
            "languages must be a list of strings.",
        ),
        (
            "project",
            [],
            "project must be a string.",
        ),
        (
            "launch_count",
            -1,
            (
                "launch_count must be a "
                "non-negative integer."
            ),
        ),
        (
            "launch_count",
            True,
            (
                "launch_count must be a "
                "non-negative integer."
            ),
        ),
    ],
)
def test_validate_profile_rejects_invalid_field_values(
    field_name: str,
    invalid_value: object,
    expected_message: str,
) -> None:
    profile_data = _valid_profile_data()
    profile_data[field_name] = invalid_value

    with pytest.raises(
        ValueError,
        match=re.escape(expected_message),
    ):
        validate_profile(profile_data)


def test_migrate_profile_adds_version_and_launch_count() -> None:
    legacy_profile = _legacy_profile_data()

    migrated_profile = migrate_profile(legacy_profile)

    assert migrated_profile == {
        **legacy_profile,
        "schema_version": 1,
        "launch_count": 0,
    }
    assert legacy_profile == _legacy_profile_data()


def test_migrate_profile_preserves_existing_launch_count() -> None:
    legacy_profile = _legacy_profile_data()
    legacy_profile["launch_count"] = 7

    migrated_profile = migrate_profile(legacy_profile)

    assert migrated_profile["schema_version"] == 1
    assert migrated_profile["launch_count"] == 7


def test_migrate_profile_does_not_hide_unknown_fields() -> None:
    legacy_profile = _legacy_profile_data()
    legacy_profile["unexpected"] = True

    with pytest.raises(
        ValueError,
        match=r"Profile contains unknown fields: unexpected\.",
    ):
        migrate_profile(legacy_profile)