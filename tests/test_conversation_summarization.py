import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from core import Brain, ModelConversationSummarizer
from core.chat_model import ChatMessage
from memory import (
    ConversationMessage,
    ConversationSummary,
    ConversationSummarizer,
    ConversationSummaryContent,
    Memory,
)

class FakeSummaryChatModel:
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


class FakeConversationSummarizer:
    def __init__(
        self,
        contents: list[ConversationSummaryContent],
    ) -> None:
        self._contents = list(contents)
        self.calls: list[
            tuple[
                list[ConversationMessage],
                ConversationSummaryContent | None,
            ]
        ] = []

    def summarize(
        self,
        messages: list[ConversationMessage],
        previous_content: (
            ConversationSummaryContent | None
        ) = None,
    ) -> ConversationSummaryContent:
        self.calls.append(
            (
                list(messages),
                previous_content,
            )
        )

        if not self._contents:
            raise AssertionError(
                "Fake summarizer has no remaining content."
            )

        return self._contents.pop(0)


def _source_messages() -> list[ConversationMessage]:
    return [
        {
            "timestamp": "2026-08-12 12:00:00",
            "speaker": "user",
            "message": "I prefer Chinese replies.",
        },
        {
            "timestamp": "2026-08-12 12:00:05",
            "speaker": "assistant",
            "message": "I will reply in Chinese.",
        },
    ]


def _summary_content() -> ConversationSummaryContent:
    return {
        "facts": [
            "The user prefers Chinese replies.",
        ],
        "decisions": [
            "Use Chinese for future replies.",
        ],
        "action_items": [],
        "unresolved_questions": [],
    }


def _updated_summary_content(
    ) -> ConversationSummaryContent:
    return {
        "facts": [
            "The user prefers Chinese replies.",
            "The user studies computer science.",
        ],
        "decisions": [
            "Use Chinese for future replies.",
        ],
        "action_items": [
            "Continue Elysia AI development.",
        ],
        "unresolved_questions": [],
    }


def test_model_summarizer_builds_validated_content() -> None:
    chat_model = FakeSummaryChatModel(
        json.dumps(
            _summary_content(),
            ensure_ascii=False,
        )
    )
    summarizer: ConversationSummarizer = (
        ModelConversationSummarizer(chat_model)
    )

    content = summarizer.summarize(
        _source_messages()
    )

    assert content == _summary_content()

    model_messages = chat_model.received_messages

    assert model_messages is not None
    assert [
        message["role"]
        for message in model_messages
    ] == [
        "system",
        "user",
    ]
    assert "Do not turn guesses" in (
        model_messages[0]["content"]
    )

    request_prefix = (
        "CONVERSATION_SUMMARY_DATA:\n"
    )
    request_text = model_messages[1]["content"]

    assert request_text.startswith(request_prefix)

    request_data = json.loads(
        request_text[len(request_prefix):]
    )

    assert request_data == {
        "previous_summary_content": None,
        "new_messages": _source_messages(),
    }

    assert (
        "Phrase technical implementation as "
        "project or configuration facts"
        in model_messages[0]["content"]
    )
    assert (
        "Do not preserve invented assistant "
        "backstory"
        in model_messages[0]["content"]
    )


def test_model_summarizer_sends_previous_content() -> None:
    previous_content: ConversationSummaryContent = {
        "facts": [
            "The user studies computer science.",
        ],
        "decisions": [],
        "action_items": [],
        "unresolved_questions": [
            "Which language should replies use?",
        ],
    }
    updated_content = _summary_content()
    chat_model = FakeSummaryChatModel(
        json.dumps(updated_content)
    )
    summarizer = ModelConversationSummarizer(
        chat_model
    )

    content = summarizer.summarize(
        _source_messages(),
        previous_content,
    )

    assert content == updated_content

    model_messages = chat_model.received_messages

    assert model_messages is not None

    request_prefix = (
        "CONVERSATION_SUMMARY_DATA:\n"
    )
    request_text = model_messages[1]["content"]
    request_data = json.loads(
        request_text[len(request_prefix):]
    )

    assert (
        request_data["previous_summary_content"]
        == previous_content
    )


def test_model_summarizer_rejects_empty_messages() -> None:
    chat_model = FakeSummaryChatModel(
        json.dumps(_summary_content())
    )
    summarizer = ModelConversationSummarizer(
        chat_model
    )

    with pytest.raises(
        ValueError,
        match=re.escape(
            "Conversation messages cannot be empty."
        ),
    ):
        summarizer.summarize([])

    assert chat_model.received_messages is None


@pytest.mark.parametrize(
    (
        "reply",
        "expected_message",
    ),
    [
        (
            "   ",
            (
                "Conversation summarizer reply cannot "
                "be empty."
            ),
        ),
        (
            "not JSON",
            (
                "Conversation summarizer reply must "
                "be valid JSON."
            ),
        ),
    ],
)
def test_model_summarizer_rejects_invalid_json(
    reply: str,
    expected_message: str,
) -> None:
    summarizer = ModelConversationSummarizer(
        FakeSummaryChatModel(reply)
    )

    with pytest.raises(
        ValueError,
        match=re.escape(expected_message),
    ):
        summarizer.summarize(
            _source_messages()
        )


@pytest.mark.parametrize(
    "reply",
    [
        "[]",
        (
            "{"
            '"facts": [], '
            '"decisions": [], '
            '"action_items": [], '
            '"unresolved_questions": [], '
            '"extra": []'
            "}"
        ),
        (
            "{"
            '"facts": [1], '
            '"decisions": [], '
            '"action_items": [], '
            '"unresolved_questions": []'
            "}"
        ),
    ],
)
def test_model_summarizer_rejects_invalid_schema(
    reply: str,
) -> None:
    summarizer = ModelConversationSummarizer(
        FakeSummaryChatModel(reply)
    )

    with pytest.raises(ValueError):
        summarizer.summarize(
            _source_messages()
        )


def test_brain_creates_and_saves_initial_summary(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    memory.save_message(
        "Ying",
        "I prefer Chinese replies.",
    )
    memory.save_message(
        "Elysia",
        "I will reply in Chinese.",
    )

    summarizer = FakeConversationSummarizer(
        [_summary_content()]
    )
    brain = Brain(
        "fake-model",
        memory,
        conversation_summarizer=summarizer,
    )

    summary = brain.summarize_conversation()

    assert summary is not None

    stored_messages = memory.get_all_messages()

    assert summary["content"] == _summary_content()
    assert summary["source_message_count"] == 2
    assert (
        summary["source_start_timestamp"]
        == stored_messages[0]["timestamp"]
    )
    assert (
        summary["source_end_timestamp"]
        == stored_messages[-1]["timestamp"]
    )
    assert len(summary["updated_at"]) == 19
    assert summarizer.calls == [
        (
            stored_messages,
            None,
        )
    ]
    assert (
        memory.get_conversation_summary()["summary"]
        == summary
    )


def test_brain_updates_summary_from_only_new_messages(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    memory.save_message("Ying", "First question")
    memory.save_message("Elysia", "First answer")

    initial_content = _summary_content()
    updated_content = _updated_summary_content()
    summarizer = FakeConversationSummarizer(
        [
            initial_content,
            updated_content,
        ]
    )
    brain = Brain(
        "fake-model",
        memory,
        conversation_summarizer=summarizer,
    )

    first_summary = brain.summarize_conversation()

    assert first_summary is not None

    memory.save_message(
        "Ying",
        "I study computer science.",
    )
    memory.save_message(
        "Elysia",
        "I will remember that.",
    )

    all_messages = memory.get_all_messages()
    updated_summary = brain.summarize_conversation()

    assert updated_summary is not None
    assert updated_summary["content"] == updated_content
    assert updated_summary["source_message_count"] == 4
    assert (
        updated_summary["source_start_timestamp"]
        == all_messages[0]["timestamp"]
    )
    assert (
        updated_summary["source_end_timestamp"]
        == all_messages[-1]["timestamp"]
    )
    assert len(summarizer.calls) == 2
    assert summarizer.calls[1] == (
        all_messages[2:],
        initial_content,
    )


def test_brain_reuses_summary_when_no_new_messages(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    memory.save_message("Ying", "First question")
    memory.save_message("Elysia", "First answer")

    summarizer = FakeConversationSummarizer(
        [_summary_content()]
    )
    brain = Brain(
        "fake-model",
        memory,
        conversation_summarizer=summarizer,
    )

    first_summary = brain.summarize_conversation()

    assert first_summary is not None

    saved_file_content = (
        memory.conversation_summary_file.read_text(
            encoding="utf-8"
        )
    )

    second_summary = brain.summarize_conversation()

    assert second_summary == first_summary
    assert len(summarizer.calls) == 1
    assert (
        memory.conversation_summary_file.read_text(
            encoding="utf-8"
        )
        == saved_file_content
    )


def test_brain_rejects_summary_source_mismatch(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    memory.conversation_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    memory.conversation_file.write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "timestamp": (
                            "2026-08-12 12:00:00"
                        ),
                        "speaker": "Ying",
                        "message": "First",
                    },
                    {
                        "timestamp": (
                            "2026-08-12 12:00:05"
                        ),
                        "speaker": "Elysia",
                        "message": "Second",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    mismatched_summary: ConversationSummary = {
        "content": _summary_content(),
        "source_message_count": 2,
        "source_start_timestamp": (
            "2026-08-12 12:00:00"
        ),
        "source_end_timestamp": (
            "2026-08-12 12:00:06"
        ),
        "updated_at": "2026-08-12 12:01:00",
    }
    memory.save_conversation_summary(
        mismatched_summary
    )

    summarizer = FakeConversationSummarizer(
        [_updated_summary_content()]
    )
    brain = Brain(
        "fake-model",
        memory,
        conversation_summarizer=summarizer,
    )

    with pytest.raises(
        ValueError,
        match=re.escape(
            "Conversation summary does not match "
            "stored messages."
        ),
    ):
        brain.summarize_conversation()

    assert summarizer.calls == []


def test_brain_returns_none_for_empty_conversation(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    summarizer = FakeConversationSummarizer(
        [_summary_content()]
    )
    brain = Brain(
        "fake-model",
        memory,
        conversation_summarizer=summarizer,
    )

    assert brain.summarize_conversation() is None
    assert summarizer.calls == []
    assert not memory.conversation_summary_file.exists()


def test_brain_requires_connected_summarizer(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    memory.save_message("Ying", "Hello")
    brain = Brain("fake-model", memory)

    with pytest.raises(
        RuntimeError,
        match=re.escape(
            "Conversation summarizer is not connected."
        ),
    ):
        brain.summarize_conversation()


def test_brain_counts_only_unsummarized_messages(
    tmp_path: Path,
) -> None:
    memory = Memory(tmp_path)
    summarizer = FakeConversationSummarizer(
        [_summary_content()]
    )
    brain = Brain(
        "fake-model",
        memory,
        conversation_summarizer=summarizer,
    )

    assert (
        brain.get_unsummarized_message_count()
        == 0
    )

    memory.save_message(
        "Ying",
        "First question",
    )
    memory.save_message(
        "Elysia",
        "First answer",
    )

    assert (
        brain.get_unsummarized_message_count()
        == 2
    )

    summary = brain.summarize_conversation()

    assert summary is not None
    assert (
        brain.get_unsummarized_message_count()
        == 0
    )

    memory.save_message(
        "Ying",
        "Second question",
    )
    memory.save_message(
        "Elysia",
        "Second answer",
    )

    assert (
        brain.get_unsummarized_message_count()
        == 2
    )