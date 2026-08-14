"""Tests for the continuous console chat session."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from core import Brain
from core.chat_model import ChatMessage
from memory import (
    ConversationMessage,
    ConversationSummaryContent,
    Memory,
)
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
    memory = Memory(tmp_path)
    chat_model = FakeConsoleChatModel()
    brain = Brain(
        "fake-model",
        memory,
        chat_model,
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

    messages = memory.get_all_messages()

    assert [
        (
            message["speaker"],
            message["message"],
        )
        for message in messages
    ] == [
        (
            "Ying",
            "First question",
        ),
        (
            "Elysia",
            "Reply to First question",
        ),
        (
            "Ying",
            "Second question",
        ),
        (
            "Elysia",
            "Reply to Second question",
        ),
    ]
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
    brain = Brain(
        "fake-model",
        Memory(tmp_path),
    )

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
    memory = Memory(tmp_path)
    memory.save_message(
        "Ying",
        "First question",
    )
    memory.save_message(
        "Elysia",
        "First answer",
    )

    summarizer = FakeConsoleSummarizer()
    brain = Brain(
        "fake-model",
        memory,
        conversation_summarizer=summarizer,
    )

    _set_console_answers(
        monkeypatch,
        [
            "/summarize",
            "/quit",
        ],
    )

    run_console_session(brain)

    summary = (
        memory
        .get_conversation_summary()["summary"]
    )

    assert summary is not None
    assert (
        summary["source_message_count"]
        == 2
    )
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
    memory = Memory(tmp_path)
    chat_model = FakeConsoleChatModel()
    summarizer = FakeConsoleSummarizer()
    brain = Brain(
        "fake-model",
        memory,
        chat_model,
        conversation_summarizer=summarizer,
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

    assert len(
        memory.get_all_messages()
    ) == 10
    assert len(summarizer.calls) == 1
    assert len(
        summarizer.calls[0][0]
    ) == 10
    assert summarizer.calls[0][1] is None

    summary = (
        memory
        .get_conversation_summary()["summary"]
    )

    assert summary is not None
    assert (
        summary["source_message_count"]
        == 10
    )

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
    memory = Memory(tmp_path)
    memory.save_message(
        "Ying",
        "First question",
    )
    memory.save_message(
        "Elysia",
        "First answer",
    )

    summarizer = FakeConsoleSummarizer(
        error=ValueError(
            "Summary generation failed."
        )
    )
    brain = Brain(
        "fake-model",
        memory,
        conversation_summarizer=summarizer,
    )

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