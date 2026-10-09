"""Test deterministic Simplified-Chinese and reply-style normalization."""

from types import SimpleNamespace

import pytest

import localization.chinese as chinese_module
from localization import (
    AssistantReplyStreamNormalizer,
    normalize_assistant_reply,
    simplify_chinese_text,
)


def test_complete_text_uses_contextual_traditional_to_simplified_rules() -> None:
    """Convert phrases as a whole instead of mapping isolated characters."""

    assert simplify_chinese_text("乾坤與乾燥，繁體中文。") == (
        "乾坤与干燥，繁体中文。"
    )


def test_assistant_reply_removes_stage_cue_and_simplifies_spoken_prose() -> None:
    """Keep only Elysia's spoken answer from a role-play styled model reply."""

    reply = (
        "（輕笑一聲，眼神裡滿是驚喜與玩味）"
        "哎呀，你總是這麼會開玩笑。"
    )

    assert normalize_assistant_reply(reply) == (
        "哎呀，你总是这么会开玩笑。"
    )


def test_assistant_reply_preserves_ordinary_parenthetical_explanation() -> None:
    """Avoid treating normal explanatory parentheses as stage directions."""

    assert normalize_assistant_reply(
        "普通說明（括號中的數值是估算）。"
    ) == "普通说明（括号中的数值是估算）。"


@pytest.mark.parametrize(
    ("reply", "expected"),
    (
        ("**聲音設定**\n請打開 Settings。", "**声音设定**\n请打开 Settings。"),
        ("*smile detection* is a CV task.", "*smile detection* is a CV task."),
        ("（聲音設定）請調整音量。", "（声音设定）请调整音量。"),
    ),
)
def test_assistant_reply_does_not_delete_labels_or_technical_terms(
    reply: str,
    expected: str,
) -> None:
    """Keep emphasized labels that merely contain former cue keywords."""

    assert normalize_assistant_reply(reply) == expected


def test_assistant_reply_can_preserve_explicitly_requested_stage_content() -> None:
    """Retain creative action text when the caller proves user authorization."""

    reply = "（輕笑一聲）她把故事的最後一頁翻開。"

    assert normalize_assistant_reply(
        reply,
        preserve_stage_directions=True,
    ) == "（轻笑一声）她把故事的最后一页翻开。"


@pytest.mark.parametrize(
    "reply",
    (
        "（轻轻一笑）你好呀。",
        "（开心地笑着）你好。",
        "（露出微笑）你好。",
        "（温柔地看向你）你好。",
        "*giggles softly* Hello.",
        "(smiling) Hello.",
        "（轻轻握住你的手）别担心，我在。",
        "（眼里闪过一丝担忧）你还好吗？",
        "（语气带着关心）慢慢来。",
        "（微微侧头）怎么了？",
        "（抬手揉了揉你的头发）辛苦了。",
        "（耳尖微红）才没有呢。",
    ),
)
def test_assistant_reply_removes_common_bounded_stage_cues(reply: str) -> None:
    """Remove common action inflections without broad keyword deletion."""

    assert normalize_assistant_reply(reply) in {
        "你好呀。",
        "你好。",
        "Hello.",
        "别担心，我在。",
        "你还好吗？",
        "慢慢来。",
        "怎么了？",
        "辛苦了。",
        "才没有呢。",
    }


def test_assistant_reply_preserves_code_links_urls_and_paths() -> None:
    """Do not silently rewrite executable or navigational literal text."""

    reply = (
        "請看 `繁體變數`、[文件](D:/資料/說明.md) 與 "
        "https://example.test/繁體，然後打開 D:\\資料\\檔案.txt。\n\n"
        "```python\n繁體變數 = '資料'\n```"
    )

    assert normalize_assistant_reply(reply) == (
        "请看 `繁體變數`、[文件](D:/資料/說明.md) 与 "
        "https://example.test/繁體，然后打开 D:\\資料\\檔案.txt。\n\n"
        "```python\n繁體變數 = '資料'\n```"
    )


def test_assistant_reply_preserves_indented_code_and_paths_with_spaces() -> None:
    """Keep Markdown code and unquoted file paths byte-exact with spaces."""

    reply = (
        "請保留：\n    繁體變數 = 1\n"
        "再看 D:\\繁體 資料\\檔案.txt 與 "
        "/home/繁體 資料/檔案.txt。"
    )

    assert normalize_assistant_reply(reply) == (
        "请保留：\n    繁體變數 = 1\n"
        "再看 D:\\繁體 資料\\檔案.txt 与 "
        "/home/繁體 資料/檔案.txt。"
    )

def test_stream_normalizer_holds_phrase_and_split_stage_direction() -> None:
    """Retain context across arbitrary token boundaries before exposing text."""

    normalizer = AssistantReplyStreamNormalizer()
    emitted: list[str] = []
    for chunk in (
        "（輕笑一",
        "聲，眼神裡很開心）",
        "乾",
        "坤與乾",
        "燥。",
    ):
        emitted.extend(normalizer.push(chunk))
    emitted.extend(normalizer.finish())

    assert "".join(emitted) == "乾坤与干燥。"


def test_stream_normalizer_buffers_unterminated_ascii_sentence() -> None:
    """Hold ASCII until finish because a later chunk may complete a URL."""

    normalizer = AssistantReplyStreamNormalizer()

    assert normalizer.push("Hello") == ()
    assert normalizer.push(" world.") == ()
    assert normalizer.finish() == ("Hello world.",)


def test_stream_normalizer_emits_completed_english_sentence() -> None:
    """Stream English once following whitespace proves the sentence boundary."""

    normalizer = AssistantReplyStreamNormalizer()

    assert normalizer.push("Hello.") == ()
    assert normalizer.push(" Next sentence") == ("Hello.",)
    assert normalizer.finish() == (" Next sentence",)


@pytest.mark.parametrize(
    "chunks",
    (
        ("D:\\", "繁體\\檔案.txt。"),
        ("https://example.test/", "繁體。"),
        ("    ", "繁體變數 = 1\n"),
        ("    繁體變數 = 1;", " 下一項\n"),
        ("Hello ", "(smiling)", " is a phrase."),
        ("你好。", "（微笑）这只是术语。"),
        ("繁體。", " (smiling) is a term."),
    ),
)
def test_stream_normalizer_matches_complete_reply_across_literal_splits(
    chunks: tuple[str, ...],
) -> None:
    """Make literal preservation independent of arbitrary model chunking."""

    complete = "".join(chunks)
    normalizer = AssistantReplyStreamNormalizer()
    emitted: list[str] = []
    for chunk in chunks:
        emitted.extend(normalizer.push(chunk))
    emitted.extend(normalizer.finish())

    assert "".join(emitted) == normalize_assistant_reply(complete)


def test_stream_normalizer_cannot_be_used_after_finish() -> None:
    """Reject lifecycle reuse that could join two unrelated model replies."""

    normalizer = AssistantReplyStreamNormalizer()
    normalizer.finish()

    with pytest.raises(RuntimeError, match="already finished"):
        normalizer.push("another reply")


def test_conversion_failure_never_falls_back_to_traditional_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed when the required canonical converter is unavailable."""

    def _fail_conversion(_text: str) -> str:
        """Simulate an unexpected converter failure without leaking input."""

        raise OSError("private native diagnostic")

    monkeypatch.setattr(
        chinese_module,
        "_CONVERTER",
        SimpleNamespace(convert=_fail_conversion),
    )

    with pytest.raises(
        RuntimeError,
        match=r"^Simplified-Chinese conversion failed\.$",
    ) as captured:
        simplify_chinese_text("不應回退的繁體內容")

    assert "private native diagnostic" not in str(captured.value)
