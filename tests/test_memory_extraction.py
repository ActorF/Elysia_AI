import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from core import Brain, ModelMemoryExtractor
from core.chat_model import ChatMessage
from memory import (
    Memory,
    MemoryCandidate,
)
from ui.console import review_memory_candidates


class FakeExtractionChatModel:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.received_messages: (
            list[ChatMessage] | None
        ) = None

    def generate_reply(
        self,
        messages: list[ChatMessage],
    ) -> str:
        self.received_messages = messages
        return self._reply

    def stream_reply(
        self,
        messages: list[ChatMessage],
    ) -> Iterator[str]:
        self.received_messages = messages
        yield self._reply


class FakeMemoryExtractor:
    def __init__(
        self,
        candidates: list[MemoryCandidate],
    ) -> None:
        self._candidates = candidates
        self.received_user_message: str | None = None

    def extract_candidates(
        self,
        user_message: str,
    ) -> list[MemoryCandidate]:
        self.received_user_message = user_message
        return list(self._candidates)


def _candidate() -> MemoryCandidate:
    return {
        "key": "preferred_language",
        "value": "Chinese",
        "source_type": "model_inferred",
        "source_text": (
            "I usually prefer Chinese replies."
        ),
        "requires_confirmation": True,
    }


def test_model_extractor_builds_confirmable_candidate() -> None:
    chat_model = FakeExtractionChatModel(
        """
        [
          {
            "key": "Preferred Language",
            "value": " Chinese ",
            "source_type": "model_inferred"
          }
        ]
        """
    )
    extractor = ModelMemoryExtractor(chat_model)

    candidates = extractor.extract_candidates(
        "  I usually prefer Chinese replies.  "
    )

    assert candidates == [_candidate()]

    messages = chat_model.received_messages

    assert messages is not None
    assert messages[0]["role"] == "system"
    assert "Return only a JSON array" in (
        messages[0]["content"]
    )
    assert messages[1] == {
        "role": "user",
        "content": (
            "USER_MESSAGE_DATA:\n"
            "I usually prefer Chinese replies."
        ),
    }


def test_model_extractor_returns_no_small_talk_memory() -> None:
    extractor = ModelMemoryExtractor(
        FakeExtractionChatModel("[]")
    )

    assert extractor.extract_candidates("Hello!") == []


def test_model_extractor_accepts_json_code_fence() -> None:
    extractor = ModelMemoryExtractor(
        FakeExtractionChatModel(
            """```json
[
  {
    "key": "preferred_language",
    "value": "Chinese",
    "source_type": "model_inferred"
  }
]
```"""
        )
    )

    assert extractor.extract_candidates(
        "I usually prefer Chinese replies."
    ) == [_candidate()]


def test_model_extractor_removes_duplicate_candidates() -> None:
    extractor = ModelMemoryExtractor(
        FakeExtractionChatModel(
            """
            [
              {
                "key": "preferred_language",
                "value": "Chinese",
                "source_type": "model_inferred"
              },
              {
                "key": "preferred_language",
                "value": "Chinese",
                "source_type": "model_inferred"
              }
            ]
            """
        )
    )

    assert extractor.extract_candidates(
        "I usually prefer Chinese replies."
    ) == [_candidate()]


@pytest.mark.parametrize(
    (
        "reply",
        "expected_message",
    ),
    [
        (
            "   ",
            "Memory extractor reply cannot be empty.",
        ),
        (
            "not JSON",
            (
                "Memory extractor reply must be valid "
                "JSON."
            ),
        ),
        (
            "{}",
            (
                "Memory extractor reply must be a JSON "
                "array."
            ),
        ),
        (
            "[1]",
            (
                "Each memory candidate must be a JSON "
                "object."
            ),
        ),
        (
            (
                '[{"key": "language", "value": '
                '"Chinese", "source_type": '
                '"unknown"}]'
            ),
            (
                "Memory candidate source_type must be "
                "user_explicit or model_inferred."
            ),
        ),
    ],
)
def test_model_extractor_rejects_invalid_reply(
    reply: str,
    expected_message: str,
) -> None:
    extractor = ModelMemoryExtractor(
        FakeExtractionChatModel(reply)
    )

    with pytest.raises(
        ValueError,
        match=re.escape(expected_message),
    ):
        extractor.extract_candidates("Remember this.")


def test_model_extractor_rejects_empty_user_message() -> None:
    chat_model = FakeExtractionChatModel("[]")
    extractor = ModelMemoryExtractor(chat_model)

    with pytest.raises(
        ValueError,
        match=r"User message cannot be empty\.",
    ):
        extractor.extract_candidates("   ")

    assert chat_model.received_messages is None


def test_brain_extracts_candidate_without_saving(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    extractor = FakeMemoryExtractor([_candidate()])
    brain = Brain(
        "fake-model",
        memory,
        memory_extractor=extractor,
    )

    candidates = brain.extract_memory_candidates(
        "  I usually prefer Chinese replies.  "
    )

    assert candidates == [_candidate()]
    assert (
        extractor.received_user_message
        == "I usually prefer Chinese replies."
    )
    assert memory.get_long_term_memories() == []


def test_brain_without_extractor_returns_no_candidates(
    tmp_path: Path,
) -> None:
    brain = Brain("fake-model", Memory(tmp_path))

    assert brain.extract_memory_candidates("Hello") == []


def test_brain_saves_confirmed_candidate(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    brain = Brain("fake-model", memory)

    saved_record = brain.confirm_memory_candidate(
        _candidate()
    )

    assert saved_record["key"] == "preferred_language"
    assert saved_record["value"] == "Chinese"
    assert saved_record["source_type"] == "model_inferred"
    assert memory.get_long_term_memories() == [
        saved_record,
    ]


def test_console_rejects_candidate_without_saving(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    memory = Memory(tmp_path)
    brain = Brain("fake-model", memory)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: "n",
    )

    review_memory_candidates(brain, [_candidate()])

    assert memory.get_long_term_memories() == []
    output = capsys.readouterr().out
    assert "Possible long-term memories:" in output
    assert "Memory not saved." in output


def test_console_saves_candidate_after_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    memory = Memory(tmp_path)
    brain = Brain("fake-model", memory)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: "yes",
    )

    review_memory_candidates(brain, [_candidate()])

    memories = memory.get_long_term_memories()

    assert len(memories) == 1
    assert memories[0]["key"] == "preferred_language"
    assert "Memory saved." in capsys.readouterr().out