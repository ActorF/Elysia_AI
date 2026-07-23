from pathlib import Path

import pytest

import start
from config.settings import AppSettings, parse_bool
from core import ConfigurationError


def make_settings(
    tmp_path: Path,
    *,
    model_name: str = "test-model",
    ollama_host: str = "http://localhost:11434",
) -> AppSettings:
    return AppSettings(
        base_dir=tmp_path,
        model_name=model_name,
        log_level="INFO",
        debug=False,
        ollama_host=ollama_host,
    )


def test_parse_bool_accepts_true_values() -> None:
    true_values = [
        "1",
        "true",
        "TRUE",
        " yes ",
        "on",
    ]

    for value in true_values:
        assert parse_bool(value) is True


def test_parse_bool_returns_false_for_other_values() -> None:
    false_values = [
        "0",
        "false",
        "FALSE",
        " no ",
        "off",
        "",
    ]

    for value in false_values:
        assert parse_bool(value) is False


def test_validate_settings_accepts_valid_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        start,
        "SETTINGS",
        make_settings(tmp_path),
    )

    start.validate_settings()


def test_validate_settings_rejects_empty_model_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        start,
        "SETTINGS",
        make_settings(
            tmp_path,
            model_name="   ",
        ),
    )

    with pytest.raises(
        ConfigurationError,
        match=r"MODEL_NAME cannot be empty\.",
    ):
        start.validate_settings()


@pytest.mark.parametrize(
    "ollama_host",
    [
        "",
        "localhost:11434",
        "ftp://localhost:11434",
    ],
)
def test_validate_settings_rejects_invalid_ollama_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ollama_host: str,
) -> None:
    monkeypatch.setattr(
        start,
        "SETTINGS",
        make_settings(
            tmp_path,
            ollama_host=ollama_host,
        ),
    )

    with pytest.raises(
        ConfigurationError,
        match=r"OLLAMA_HOST must start with http:// or https://\.",
    ):
        start.validate_settings()
