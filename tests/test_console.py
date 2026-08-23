"""Tests for the continuous console chat session."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from chats import JsonChatRepository
from core import ActiveConversationService, Brain
from core.chat_model import ChatMessage
from memory import (
    ConversationMessage,
    ConversationSummaryContent,
    Memory,
)
from projects import JsonProjectRepository
from ui.console import run_console_session


class FakeConsoleChatModel:
    def __init__(self) -> None:
        self.received_messages: list[
            list[ChatMessage]
        ] = []

    def _record_and_build_reply(
        self,
        messages: list[ChatMessage],
    ) -> str:
        self.received_messages.append(
            list(messages)
        )

        return (
            "Reply to "
            f"{messages[-1]['content']}"
        )

    def generate_reply(
        self,
        messages: list[ChatMessage],
    ) -> str:
        return self._record_and_build_reply(
            messages
        )

    def stream_reply(
        self,
        messages: list[ChatMessage],
    ) -> Iterator[str]:
        yield self._record_and_build_reply(
            messages
        )


class FakeConsoleSummarizer:
    def __init__(
        self,
        error: Exception | None = None,
    ) -> None:
        self._error = error
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

        if self._error is not None:
            raise self._error

        return {
            "facts": [
                (
                    "Summarized "
                    f"{len(messages)} messages."
                ),
            ],
            "decisions": [],
            "action_items": [],
            "unresolved_questions": [],
        }


def _brain(
    tmp_path: Path,
    *,
    chat_model: FakeConsoleChatModel | None = None,
    summarizer: FakeConsoleSummarizer | None = None,
) -> tuple[Brain, Memory]:
    memory = Memory(tmp_path)
    active = ActiveConversationService(
        JsonChatRepository(tmp_path / "data" / "chats"),
        JsonProjectRepository(tmp_path / "data" / "projects"),
    )
    return (
        Brain(
            "fake-model",
            memory,
            chat_model,
            conversation_summarizer=summarizer,
            active_conversation_service=active,
        ),
        memory,
    )


def _set_console_answers(
    monkeypatch: pytest.MonkeyPatch,
    answers: list[str],
) -> None:
    answer_iterator = iter(answers)

    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: next(answer_iterator),
    )


def test_console_supports_multiple_turns_and_quit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    chat_model = FakeConsoleChatModel()
    brain, memory = _brain(
        tmp_path,
        chat_model=chat_model,
    )

    _set_console_answers(
        monkeypatch,
        [
            "First question",
            "Second question",
            "/quit",
        ],
    )

    run_console_session(brain)

    chat_id = brain.list_chats()[0].chat_id
    messages = brain.get_chat(chat_id).messages

    assert [
        (
            message.role,
            message.content,
        )
        for message in messages
    ] == [
        (
            "user",
            "First question",
        ),
        (
            "assistant",
            "Reply to First question",
        ),
        (
            "user",
            "Second question",
        ),
        (
            "assistant",
            "Reply to Second question",
        ),
    ]
    assert memory.get_all_messages() == []
    assert (
        len(chat_model.received_messages)
        == 2
    )

    output = capsys.readouterr().out

    assert "Reply to First question" in output
    assert "Reply to Second question" in output
    assert "Chat session ended." in output


def test_console_continues_after_empty_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    brain, _memory = _brain(tmp_path)

    _set_console_answers(
        monkeypatch,
        [
            "   ",
            "/quit",
        ],
    )

    run_console_session(brain)

    output = capsys.readouterr().out

    assert "No message was entered." in output
    assert "Chat session ended." in output


def test_console_can_manually_update_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    chat_model = FakeConsoleChatModel()
    summarizer = FakeConsoleSummarizer()
    brain, _memory = _brain(
        tmp_path,
        chat_model=chat_model,
        summarizer=summarizer,
    )
    chat = brain.create_chat(title="Existing Chat")
    brain.chat(chat.chat_id, "First question")

    _set_console_answers(
        monkeypatch,
        [
            "/summarize",
            "/quit",
        ],
    )

    run_console_session(brain)

    summary = brain.get_chat(chat.chat_id).summary

    assert summary is not None
    assert len(summary.source_message_ids) == 2
    assert len(summarizer.calls) == 1

    output = capsys.readouterr().out

    assert (
        "Conversation summary updated."
        in output
    )


def test_console_automatically_summarizes_ten_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    chat_model = FakeConsoleChatModel()
    summarizer = FakeConsoleSummarizer()
    brain, memory = _brain(
        tmp_path,
        chat_model=chat_model,
        summarizer=summarizer,
    )

    _set_console_answers(
        monkeypatch,
        [
            "Question 1",
            "Question 2",
            "Question 3",
            "Question 4",
            "Question 5",
            "/quit",
        ],
    )

    run_console_session(brain)

    chat_id = brain.list_chats()[0].chat_id
    persisted_chat = brain.get_chat(chat_id)
    assert len(persisted_chat.messages) == 10
    assert memory.get_all_messages() == []
    assert len(summarizer.calls) == 1
    assert len(
        summarizer.calls[0][0]
    ) == 10
    assert summarizer.calls[0][1] is None

    summary = persisted_chat.summary

    assert summary is not None
    assert len(summary.source_message_ids) == 10

    output = capsys.readouterr().out

    assert (
        "Conversation summary updated "
        "automatically."
        in output
    )


def test_console_summary_failure_does_not_end_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    chat_model = FakeConsoleChatModel()
    summarizer = FakeConsoleSummarizer(
        error=ValueError(
            "Summary generation failed."
        )
    )
    brain, _memory = _brain(
        tmp_path,
        chat_model=chat_model,
        summarizer=summarizer,
    )
    chat = brain.create_chat(title="Existing Chat")
    brain.chat(chat.chat_id, "First question")

    _set_console_answers(
        monkeypatch,
        [
            "/summarize",
            "/quit",
        ],
    )

    run_console_session(brain)

    output = capsys.readouterr().out

    assert (
        "Conversation summary skipped: "
        "Summary generation failed."
        in output
    )
    assert "Chat session ended." in output
    assert len(summarizer.calls) == 1
