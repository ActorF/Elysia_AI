"""Normalize user-facing Chinese while preserving literal technical text.

OpenCC is the canonical script converter. Chat prose additionally protects
Markdown code, link destinations, URLs, and unambiguous filesystem paths
because changing those literals could make an otherwise correct answer
unusable. A space-containing extensionless path must be quoted or formatted as
code because natural language offers no reliable endpoint for that span.
"""

from __future__ import annotations

import re
from threading import Lock
from typing import Final, Pattern

from opencc import OpenCC


_CONVERTER: Final = OpenCC("t2s")
_CONVERTER_LOCK: Final = Lock()

_PLACEHOLDER_OPEN: Final = "\ue000"
_PLACEHOLDER_CLOSE: Final = "\ue001"

_FENCED_CODE_PATTERN: Final = re.compile(
    r"(?ms)^[ \t]{0,3}(?P<fence>`{3,}|~{3,})[^\r\n]*\r?\n"
    r".*?^[ \t]{0,3}(?P=fence)[ \t]*(?=\r?$)"
)
_UNCLOSED_FENCED_CODE_PATTERN: Final = re.compile(
    r"(?ms)^[ \t]{0,3}(?:`{3,}|~{3,})[^\r\n]*(?:\r?\n.*)?\Z"
)
_INLINE_CODE_PATTERN: Final = re.compile(
    r"(?s)(?P<ticks>`+).*?(?P=ticks)"
)
_INDENTED_CODE_PATTERN: Final = re.compile(
    r"(?m)(?:^(?: {4}|\t)[^\r\n]*(?:\r?\n|$))+"
)
_MARKDOWN_DESTINATION_PATTERN: Final = re.compile(
    r"(?<=\]\()[^\r\n)]*(?=\))"
)
_URL_PATTERN: Final = re.compile(
    r"(?i)(?:https?://|mailto:)[^\s<>，。！？；、）】》」』]+"
)
_QUOTED_WINDOWS_PATH_PATTERN: Final = re.compile(
    r"(?P<quote>['\"])(?:[A-Za-z]:[\\/]|\\\\).*?(?P=quote)"
)
_QUOTED_POSIX_PATH_PATTERN: Final = re.compile(
    r"(?P<quote>['\"])/(?:[^\r\n]*?)(?P=quote)"
)
_WINDOWS_FILE_PATH_WITH_SPACES_PATTERN: Final = re.compile(
    r"(?<![\w])(?:"
    r"(?:[A-Za-z]:\\|\\\\)"
    r"(?:[^\\\r\n<>|?*，。！？；、）】》」』]+\\)+"
    r"[^\\\r\n<>|?*，。！？；、）】》」』]+?\.[A-Za-z0-9_-]{1,16}"
    r"|"
    r"[A-Za-z]:/"
    r"(?:[^/\r\n<>|?*，。！？；、）】》」』]+/)+"
    r"[^/\r\n<>|?*，。！？；、）】》」』]+?\.[A-Za-z0-9_-]{1,16}"
    r")"
    r"(?=$|[\s，。！？；、）】》」』])"
)
_WINDOWS_PATH_PATTERN: Final = re.compile(
    r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\)"
    r"[^\s<>|?*，。！？；、）】》」』]+"
)
_POSIX_PATH_PATTERN: Final = re.compile(
    r"(?<![\w:])/(?:[^/\s，。！？；、）】》」』]+/)*"
    r"[^/\s，。！？；、）】》」』]*"
)
_POSIX_FILE_PATH_WITH_SPACES_PATTERN: Final = re.compile(
    r"(?<![\w:])/(?:[^/\r\n，。！？；、）】》」』]+/)+"
    r"[^/\r\n，。！？；、）】》」』]+?\.[A-Za-z0-9_-]{1,16}"
    r"(?=$|[\s，。！？；、）】》」』])"
)

_STAGE_DIRECTION_PATTERN: Final = re.compile(
    r"(?m)^(?P<indent>[ \t]*)(?:"
    r"（(?P<fullwidth>[^）\r\n]{1,240})）|"
    r"\((?P<ascii>[^)\r\n]{1,240})\)|"
    r"【(?P<bracket>[^】\r\n]{1,240})】|"
    r"\*{1,2}(?P<asterisk>[^*\r\n]{1,240})\*{1,2}|"
    r"_{1,2}(?P<underscore>[^_\r\n]{1,240})_{1,2}"
    r")[ \t]*"
)
_CHINESE_STAGE_DIRECTION_PATTERN: Final = re.compile(
    r"^(?:(?:我|她|爱莉希雅)[，、 ]*)?"
    r"(?:轻轻地?|微微|悄悄地?|慢慢地?|温柔地?|开心地?|俏皮地?|"
    r"认真地?|小声地?|低声地?)?"
    r"(?:"
    r"笑(?:了?一声|起来|着)?|一笑|轻笑(?:一声)?|微笑|苦笑|"
    r"露出(?:一个)?(?:微笑|笑容)|抿嘴一笑|扬起嘴角|"
    r"叹(?:了?口气|气)?|"
    r"眨(?:了眨)?眼|歪(?:了歪)?头|点(?:了点)?头|摇(?:了摇)?头|挑眉|"
    r"垂眸|抬眸|侧(?:了侧)?头|看着(?:你|对方)?|"
    r"看向(?:你|对方|镜头)?|望向(?:你|对方)?|"
    r"注视(?:着)?(?:你|对方)?|凝视(?:着)?(?:你|对方)?|"
    r"闭上眼睛|睁大眼睛|抬起头|低下头|靠近|伸手|抬手|挥(?:了挥)?手|"
    r"抬手揉(?:了揉)?(?:你|对方)的?(?:头发|头)|"
    r"握住(?:你|对方)的?(?:手|手腕)?|抱住(?:你|对方)?|"
    r"拥抱(?:你|对方)?|拍了拍(?:你|对方)?|脸红|沉默|停顿|呼吸|"
    r"低声说|轻声说"
    r")"
    r"(?:[，、：:].*)?$"
)
_CHINESE_DESCRIPTIVE_STAGE_PATTERN: Final = re.compile(
    r"^(?:她|爱莉希雅)?[，、 ]*"
    r"(?:眼|眼神|目光|嘴角|脸上|神情|表情|耳朵|耳尖|尾巴)"
    r"(?:里|中|上)?(?:满是|带着|露出|显得|变得|泛起|泛着|闪过|"
    r"微红|扬起|弯起|垂下|"
    r"轻轻|微微|.*(?:开心|惊喜|温柔|认真|悲伤|害羞|担心|笑意)).*$"
)
_CHINESE_VOICE_STAGE_PATTERN: Final = re.compile(
    r"^(?:她|爱莉希雅)?[，、 ]*(?:声音|语气)(?:里|中)?"
    r"(?:(?:很|变得|显得|压得|放得)?(?:轻|低|温柔|认真|严肃|哽咽)"
    r"|(?:带着|透着).+)"
    r"(?:[，、：:].*)?$"
)
_ENGLISH_STAGE_DIRECTION_PATTERN: Final = re.compile(
    r"^(?:she\s+)?(?:softly\s+|gently\s+|quietly\s+|slowly\s+)?"
    r"(?:smil(?:e|es|ing)|laugh(?:s|ing)?|giggl(?:e|es|ing)|"
    r"sigh(?:s|ing)?|nod(?:s|ding)?|blush(?:es|ing)?|gaz(?:e|es|ing)|"
    r"whisper(?:s|ing)?|wink(?:s|ing)?|paus(?:e|es|ing))"
    r"(?:\s+(?:softly|gently|quietly|slowly))?(?:\s*[,.:;].*)?$",
    re.IGNORECASE,
)
# Full-width Chinese sentence marks and newlines are the only chunk-safe
# boundaries. ASCII punctuation may belong to an incomplete URL/query emitted
# by the next model chunk, so it remains buffered until a later safe boundary.
_STREAM_BOUNDARIES: Final = frozenset("。！？\n")
_ASCII_STREAM_BOUNDARIES: Final = frozenset(".!?;")
_OPENING_BRACKETS: Final = {
    "(": ")",
    "（": "）",
    "[": "]",
    "【": "】",
}


def simplify_chinese_text(text: str) -> str:
    """Convert complete text to Simplified Chinese with OpenCC ``t2s``.

    The converter is intentionally applied even when the caller believes the
    text is already simplified. Unicode has no reliable binary Traditional-
    Chinese flag, so unconditional conversion avoids a fallible pre-detection
    step. A lock avoids assuming that the native converter is thread-safe.

    Raises:
        TypeError: If ``text`` is not a string.
        RuntimeError: If OpenCC fails or returns a non-string value.
    """

    if not isinstance(text, str):
        raise TypeError("Chinese text must be a string.")
    try:
        with _CONVERTER_LOCK:
            converted = _CONVERTER.convert(text)
    except Exception as error:
        raise RuntimeError("Simplified-Chinese conversion failed.") from error
    if not isinstance(converted, str):
        raise RuntimeError("Simplified-Chinese conversion returned invalid text.")
    return converted


def _replace_with_placeholders(
    text: str,
    pattern: Pattern[str],
    protected: list[str],
) -> str:
    """Replace literal spans with collision-resistant private-use markers."""

    def replace(match: re.Match[str]) -> str:
        """Store one literal span and return its ordered marker."""

        index = len(protected)
        protected.append(match.group(0))
        return f"{_PLACEHOLDER_OPEN}{index}{_PLACEHOLDER_CLOSE}"

    return pattern.sub(replace, text)


def _protect_literal_text(text: str) -> tuple[str, tuple[str, ...]]:
    """Hide technical literals before natural-language normalization.

    Patterns run from the broadest Markdown structures to smaller path-like
    structures so a URL inside a code block cannot be captured twice. Private-
    use markers are rejected up front because accepting caller-provided markers
    would make restoration ambiguous.
    """

    if _PLACEHOLDER_OPEN in text or _PLACEHOLDER_CLOSE in text:
        raise ValueError("Assistant reply contains reserved placeholder text.")
    protected: list[str] = []
    masked = text
    for pattern in (
        _FENCED_CODE_PATTERN,
        _UNCLOSED_FENCED_CODE_PATTERN,
        _INDENTED_CODE_PATTERN,
        _INLINE_CODE_PATTERN,
        _MARKDOWN_DESTINATION_PATTERN,
        _URL_PATTERN,
        _QUOTED_WINDOWS_PATH_PATTERN,
        _QUOTED_POSIX_PATH_PATTERN,
        _WINDOWS_FILE_PATH_WITH_SPACES_PATTERN,
        _WINDOWS_PATH_PATTERN,
        _POSIX_FILE_PATH_WITH_SPACES_PATTERN,
        _POSIX_PATH_PATTERN,
    ):
        masked = _replace_with_placeholders(masked, pattern, protected)
    return masked, tuple(protected)


def _restore_literal_text(text: str, protected: tuple[str, ...]) -> str:
    """Restore every protected literal and reject missing or forged markers."""

    marker_pattern = re.compile(
        re.escape(_PLACEHOLDER_OPEN) + r"(\d+)" + re.escape(_PLACEHOLDER_CLOSE)
    )
    restored_indexes: list[int] = []

    def restore(match: re.Match[str]) -> str:
        """Resolve one ordered marker back to its original literal."""

        index = int(match.group(1))
        if index >= len(protected):
            raise RuntimeError("Assistant literal marker is invalid.")
        restored_indexes.append(index)
        return protected[index]

    restored = marker_pattern.sub(restore, text)
    if sorted(restored_indexes) != list(range(len(protected))):
        raise RuntimeError("Assistant literal markers were not preserved.")
    if _PLACEHOLDER_OPEN in restored or _PLACEHOLDER_CLOSE in restored:
        raise RuntimeError("Assistant literal marker restoration is incomplete.")
    return restored


def _looks_like_stage_direction(value: str) -> bool:
    """Identify explicit action grammar rather than labels or technical prose.

    Broad keyword matching used to delete headings such as ``声音设置`` and
    English terms such as ``smile detection``. Requiring a complete action or
    descriptive clause preserves those legitimate prefixes while still
    removing the common role-play cues this boundary is designed to reject.
    """

    normalized = value.strip()
    return any(
        pattern.fullmatch(normalized) is not None
        for pattern in (
            _CHINESE_STAGE_DIRECTION_PATTERN,
            _CHINESE_DESCRIPTIVE_STAGE_PATTERN,
            _CHINESE_VOICE_STAGE_PATTERN,
            _ENGLISH_STAGE_DIRECTION_PATTERN,
        )
    )


def _remove_stage_directions(
    text: str,
    *,
    starts_at_line_boundary: bool,
) -> str:
    """Remove paragraph-leading action cues while preserving normal asides.

    Only short, explicitly delimited prefixes containing narration markers are
    removed. This deliberately conservative rule avoids deleting mathematics,
    citations, ordinary parenthetical explanations, or requested prose that
    happens to describe an action in a normal sentence.
    """

    removed = False
    boundary_guard = "" if starts_at_line_boundary else "\u2060"
    guarded_text = boundary_guard + text

    def replace(match: re.Match[str]) -> str:
        """Drop a recognized cue and retain every non-narrative prefix."""

        nonlocal removed
        body = next(
            group
            for group in (
                match.group("fullwidth"),
                match.group("ascii"),
                match.group("bracket"),
                match.group("asterisk"),
                match.group("underscore"),
            )
            if group is not None
        )
        if not _looks_like_stage_direction(body):
            return match.group(0)
        removed = True
        return match.group("indent")

    cleaned = _STAGE_DIRECTION_PATTERN.sub(replace, guarded_text)
    if removed:
        # A cue on its own first line should not leave a blank line before the
        # spoken reply. Internal paragraph spacing remains untouched.
        cleaned = re.sub(r"\A(?:[ \t]*\r?\n)+", "", cleaned)
    if boundary_guard:
        if not cleaned.startswith(boundary_guard):
            raise RuntimeError("Assistant fragment boundary guard was lost.")
        cleaned = cleaned[len(boundary_guard):]
    return cleaned


def _normalize_assistant_fragment(
    text: str,
    *,
    preserve_stage_directions: bool,
    starts_at_line_boundary: bool,
) -> str:
    """Normalize one reassembled fragment with its original line position."""

    masked, protected = _protect_literal_text(text)
    simplified = simplify_chinese_text(masked)
    without_stage_directions = (
        simplified
        if preserve_stage_directions
        else _remove_stage_directions(
            simplified,
            starts_at_line_boundary=starts_at_line_boundary,
        )
    )
    return _restore_literal_text(without_stage_directions, protected)


def normalize_assistant_reply(
    text: str,
    *,
    preserve_stage_directions: bool = False,
) -> str:
    """Simplify assistant prose and normally remove role-play narration.

    Code, Markdown destinations, URLs, and unambiguous paths are restored byte
    for byte after prose conversion. This is a content-integrity exception:
    those literals may intentionally contain Traditional Chinese because
    changing a command or destination would be more harmful than changing its
    typography. Ordinary quoted prose is still simplified; callers must use a
    code span or block when byte-exact quotation is required.
    Callers may preserve action cues only when the user explicitly requested
    fiction, a script, role-play, or action analysis; ordinary conversation
    keeps the narration-free default.
    """

    if not isinstance(text, str):
        raise TypeError("Assistant reply must be a string.")
    return _normalize_assistant_fragment(
        text,
        preserve_stage_directions=preserve_stage_directions,
        starts_at_line_boundary=True,
    )


def _last_safe_stream_boundary(text: str) -> int:
    """Find the last sentence boundary outside code and bracketed regions.

    Traditional-to-Simplified conversion uses phrase context. Holding affected
    prose until a sentence boundary prevents token-sized model chunks from
    changing phrases independently. Fenced and inline code stay buffered until
    their delimiter closes, and punctuation inside parentheses is not treated
    as a boundary so a split stage direction can be filtered as one unit.
    """

    bracket_stack: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    inline_ticks = 0
    line_start = True
    indented_code_line = False
    last_boundary = 0
    index = 0
    while index < len(text):
        character = text[index]
        if character == "\n":
            if fence_character is None and inline_ticks == 0 and not bracket_stack:
                last_boundary = index + 1
            line_start = True
            indented_code_line = False
            index += 1
            continue

        if fence_character is not None:
            if line_start and character in " \t":
                index += 1
                continue
            if line_start and character == fence_character:
                run_end = index
                while run_end < len(text) and text[run_end] == character:
                    run_end += 1
                if run_end - index >= fence_length:
                    fence_character = None
                    fence_length = 0
                index = run_end
                line_start = False
                continue
            line_start = False
            index += 1
            continue

        if line_start and character == "\t":
            indented_code_line = True
            line_start = False
            index += 1
            continue
        if line_start and character == " ":
            run_end = index
            while run_end < len(text) and text[run_end] == " ":
                run_end += 1
            if run_end - index >= 4:
                indented_code_line = True
                line_start = False
            index = run_end
            continue
        if line_start and character in "`~":
            run_end = index
            while run_end < len(text) and text[run_end] == character:
                run_end += 1
            if run_end - index >= 3:
                fence_character = character
                fence_length = run_end - index
                index = run_end
                line_start = False
                continue

        line_start = False
        if character == "`":
            run_end = index
            while run_end < len(text) and text[run_end] == "`":
                run_end += 1
            run_length = run_end - index
            if inline_ticks == 0:
                inline_ticks = run_length
            elif run_length == inline_ticks:
                inline_ticks = 0
            index = run_end
            continue
        if inline_ticks:
            index += 1
            continue
        if indented_code_line:
            index += 1
            continue

        expected_closing = _OPENING_BRACKETS.get(character)
        if expected_closing is not None:
            bracket_stack.append(expected_closing)
        elif bracket_stack and character == bracket_stack[-1]:
            bracket_stack.pop()
        elif not bracket_stack and character in _STREAM_BOUNDARIES:
            last_boundary = index + 1
        elif (
            not bracket_stack
            and character in _ASCII_STREAM_BOUNDARIES
            and index + 1 < len(text)
            and text[index + 1].isspace()
        ):
            # Wait for one following character before accepting ASCII marks;
            # at a chunk tail they may still become part of a URL or token.
            last_boundary = index + 1
        index += 1
    return last_boundary


class AssistantReplyStreamNormalizer:
    """Normalize model chunks before any text reaches UI, storage, or speech.

    All arbitrary model chunks are reassembled through a safe sentence boundary
    before normalization. This prevents a split URL, path, code block, or stage
    cue from being interpreted differently from the same complete reply.
    ``finish`` must be called exactly once so an unterminated final sentence is
    normalized instead of being discarded.
    """

    def __init__(self, *, preserve_stage_directions: bool = False) -> None:
        """Create a normalizer for ordinary or explicitly creative output."""

        self._buffer = ""
        self._buffer_starts_at_line_boundary = True
        self._finished = False
        self._preserve_stage_directions = preserve_stage_directions

    def push(self, chunk: str) -> tuple[str, ...]:
        """Accept one canonical model chunk and return safe normalized chunks."""

        if self._finished:
            raise RuntimeError("Assistant reply normalizer is already finished.")
        if not isinstance(chunk, str):
            raise TypeError("Assistant reply chunk must be a string.")
        if not chunk:
            return ()

        self._buffer += chunk
        boundary = _last_safe_stream_boundary(self._buffer)
        if boundary == 0:
            return ()
        ready = self._buffer[:boundary]
        self._buffer = self._buffer[boundary:]
        ready_starts_at_line_boundary = self._buffer_starts_at_line_boundary
        self._buffer_starts_at_line_boundary = ready.endswith(("\n", "\r"))
        normalized = _normalize_assistant_fragment(
            ready,
            preserve_stage_directions=self._preserve_stage_directions,
            starts_at_line_boundary=ready_starts_at_line_boundary,
        )
        emitted: tuple[str, ...] = (normalized,) if normalized else ()
        return emitted

    def finish(self) -> tuple[str, ...]:
        """Normalize the final unterminated suffix and close this instance."""

        if self._finished:
            raise RuntimeError("Assistant reply normalizer is already finished.")
        self._finished = True
        if not self._buffer:
            return ()
        normalized = _normalize_assistant_fragment(
            self._buffer,
            preserve_stage_directions=self._preserve_stage_directions,
            starts_at_line_boundary=self._buffer_starts_at_line_boundary,
        )
        self._buffer = ""
        self._buffer_starts_at_line_boundary = True
        return (normalized,) if normalized else ()
