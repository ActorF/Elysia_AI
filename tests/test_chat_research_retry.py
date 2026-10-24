"""Verify regenerated Chat answers refresh clock/research evidence without replacing history on failure."""

import json
from pathlib import Path
from threading import Event

import pytest

from core import GenerationCancelledError
from tests.test_brain import _active_brain
from tests.test_internet_assistant import _Model, _evidence


def _tail(brain, chat):
    records = brain.get_chat(chat.chat_id).messages
    return records[-2].message_id, records[-1].message_id


def test_clock_retry_samples_the_local_clock_without_using_the_model(tmp_path: Path) -> None:
    """Regenerating a factual local-time answer must not fall back to model guesses."""
    model = _Model("THIS MUST NOT BE USED")
    brain, _, chat = _active_brain(tmp_path, model)
    first = "".join(brain.stream_chat(chat.chat_id, "现在几点？"))
    answer = "".join(brain.stream_retry(chat.chat_id, *_tail(brain, chat)))
    assert not model.prompts
    assert "THIS MUST NOT" not in answer
    assert answer and first
    assert len(brain.get_chat(chat.chat_id).messages) == 2


def test_offline_weather_retry_keeps_the_unavailable_notice(tmp_path: Path) -> None:
    """Retry and edit-and-retry honor Internet Off rather than inventing live weather."""
    model = _Model("Unverified sunshine")
    brain, _, chat = _active_brain(tmp_path, model)
    first = "".join(brain.stream_chat(chat.chat_id, "上海今天天气如何？"))
    answer = "".join(brain.stream_retry(chat.chat_id, *_tail(brain, chat)))
    assert answer == first
    edited = "".join(brain.stream_retry(chat.chat_id, *_tail(brain, chat), "北京今天天气如何？"))
    assert edited and "Unverified" not in edited
    assert not model.prompts


def test_research_retry_refreshes_sources_and_excludes_the_replaced_pair(tmp_path: Path) -> None:
    """A retry performs a new read while its old question/answer cannot bias planning."""
    model = _Model("Old response")
    brain, _, chat = _active_brain(tmp_path, model)
    list(brain.stream_chat(chat.chat_id, "Old question"))
    model.responses = ['{"action":"search","query":"new fact","language":"en"}', "New answer"]
    calls = []
    phases = []
    answer = "".join(brain.stream_retry(chat.chat_id, *_tail(brain, chat), "Please verify the new fact",
        internet_research=lambda *args: calls.append(args) or _evidence(), research_progress=phases.append))
    assert calls == [("new fact", "en")]
    planner = json.loads(model.prompts[-2][-1]["content"])
    assert "Old question" not in json.dumps(planner)
    assert "Old response" not in json.dumps(model.prompts[-1])
    assert phases == ["planning", "searching", "answering"]
    assert "Retrieved sources" in answer
    stored = brain.get_chat(chat.chat_id).messages
    assert [item.content for item in stored] == ["Please verify the new fact", answer]


def test_cancelled_retry_research_keeps_the_original_answer(tmp_path: Path) -> None:
    """Fresh evidence arriving after Stop cannot replace the previously committed pair."""
    model = _Model("Original answer")
    brain, _, chat = _active_brain(tmp_path, model)
    list(brain.stream_chat(chat.chat_id, "Question"))
    before = brain.get_chat(chat.chat_id).messages
    model.responses = ['{"action":"search","query":"new fact","language":"en"}']
    cancelled = Event()

    def _research(*args):
        cancelled.set()
        return _evidence()

    with pytest.raises(GenerationCancelledError):
        list(brain.stream_retry(chat.chat_id, *_tail(brain, chat), internet_research=_research,
                               should_cancel=cancelled.is_set))
    assert brain.get_chat(chat.chat_id).messages == before
    assert not brain.is_chat_busy(chat.chat_id)
