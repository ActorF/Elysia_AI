"""Define the engine-independent contract for local speech transcription."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Literal, Protocol, TypeAlias

from .capture import VoiceCapture


TranscriptionLanguage: TypeAlias = Literal["auto", "zh", "en"]
TranscriptLanguage: TypeAlias = Literal["zh", "en"]
TRANSCRIPTION_MAX_TEXT_CODE_POINTS: Final = 4_096

_REQUEST_LANGUAGES = ("auto", "zh", "en")
_RESULT_LANGUAGES = ("zh", "en")
_ZERO_WIDTH_NO_BREAK_SPACE = "\ufeff"


class TranscriptionError(Exception):
    """Base class for stable failures exposed by a Transcriber."""


class TranscriptionValidationError(TranscriptionError):
    """Report an invalid transcription request or adapter result."""


class TranscriptionUnavailableError(TranscriptionError):
    """Report that the configured local transcription engine cannot run."""


class TranscriptionFailedError(TranscriptionError):
    """Report failure while an available engine processes valid audio."""


def _contains_spoken_text(value: str) -> bool:
    """Return whether text contains something that can become a Chat message."""

    # U+FEFF is not whitespace to str.isspace(), but the desktop protocol treats
    # a BOM-only message as blank so an adapter cannot recreate an empty Turn.
    return any(
        not (character.isspace() or character == _ZERO_WIDTH_NO_BREAK_SPACE)
        for character in value
    )


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    """Describe one validated PCM capture and its language hint.

    ``capture`` reuses the fixed 16 kHz mono ``s16le`` contract established by
    the capture domain. This keeps audio validation in one place and prevents a
    Transcriber implementation from accepting browser-specific audio objects.
    ``language`` is either automatic detection or an explicit supported language.
    """

    capture: VoiceCapture
    language: TranscriptionLanguage = "auto"

    def __post_init__(self) -> None:
        """Reject values that bypass the static request annotations."""

        if not isinstance(self.capture, VoiceCapture):
            raise TranscriptionValidationError(
                "Transcription capture must be a validated VoiceCapture."
            )
        if (
            not isinstance(self.language, str)
            or self.language not in _REQUEST_LANGUAGES
        ):
            raise TranscriptionValidationError(
                "Transcription language must be auto, zh, or en."
            )


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    """Hold one bounded final transcript and language-detection metadata.

    Text is preserved exactly for review and editing by a later caller. A result
    cannot use ``auto`` because language detection must be resolved before the
    engine-independent boundary returns. ``language_probability`` is a finite
    confidence value on the inclusive interval from zero to one.
    """

    text: str
    language: TranscriptLanguage
    language_probability: float

    def __post_init__(self) -> None:
        """Reject unsafe text, language, and confidence metadata."""

        if not isinstance(self.text, str):
            raise TranscriptionValidationError(
                "Transcription result text must be a string."
            )
        if len(self.text) > TRANSCRIPTION_MAX_TEXT_CODE_POINTS:
            raise TranscriptionValidationError(
                "Transcription result text exceeds the code-point limit."
            )
        if not _contains_spoken_text(self.text):
            raise TranscriptionValidationError(
                "Transcription result text must not be blank."
            )
        if (
            not isinstance(self.language, str)
            or self.language not in _RESULT_LANGUAGES
        ):
            raise TranscriptionValidationError(
                "Transcription result language must be zh or en."
            )
        if (
            not isinstance(self.language_probability, float)
            or not math.isfinite(self.language_probability)
            or not 0.0 <= self.language_probability <= 1.0
        ):
            raise TranscriptionValidationError(
                "Transcription language_probability must be a finite float "
                "between 0.0 and 1.0."
            )


class Transcriber(Protocol):
    """Convert validated local PCM into a final engine-independent transcript.

    Implementations may perform blocking model work. A later orchestration layer
    owns background execution, deadlines, cancellation, and progress so this
    boundary remains simple to fake in tests and contains no Faster-Whisper types.
    """

    def transcribe(
        self,
        request: TranscriptionRequest,
    ) -> TranscriptionResult:
        """Return one final result or raise a typed TranscriptionError."""
        ...
