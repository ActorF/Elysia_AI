from pathlib import Path

import pytest

from core import Brain
from memory import Memory


def test_brain_cleans_model_name(
    tmp_path: Path,
) -> None:
    brain = Brain(
        "  test-model  ",
        Memory(tmp_path),
    )

    assert brain.model_name == "test-model"


def test_brain_rejects_empty_model_name(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError,
        match=r"Model name cannot be empty\.",
    ):
        Brain("   ", Memory(tmp_path))


def test_brain_remembers_and_recalls_messages(
    tmp_path: Path,
) -> None:
    brain = Brain(
        "test-model",
        Memory(tmp_path),
    )

    brain.remember_message("Ying", "Hello")
    brain.remember_message("Elysia", "Hi")

    messages = brain.recall_recent_messages(2)

    assert [
        (message["speaker"], message["message"])
        for message in messages
    ] == [
        ("Ying", "Hello"),
        ("Elysia", "Hi"),
    ]
