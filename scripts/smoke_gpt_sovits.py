"""Run a privacy-preserving smoke test against configured local synthesis."""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
import wave
from collections.abc import Sequence
from typing import Final

# Direct execution normally exposes only ``scripts`` on sys.path. Anchoring the
# repository root keeps both documented CMD forms equivalent without depending
# on the caller's working directory.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import SETTINGS
from voice import (
    LocalSpeechSynthesisService,
    SynthesisFailedError,
    SynthesisRequest,
    SynthesisResult,
    SynthesisUnavailableError,
    SynthesisValidationError,
    create_local_speech_synthesis_service,
)


_SMOKE_TEXT: Final = "这是本地语音合成连通性测试。"
_REPETITIONS: Final = 2
_MAX_EMOTIONS: Final = 8


class _InvalidSmokeAudioError(Exception):
    """Mark an audio result that cannot satisfy the smoke-test contract."""


def _build_parser() -> argparse.ArgumentParser:
    """Create the bounded command-line parser without accepting arbitrary text."""

    parser = argparse.ArgumentParser(
        description=(
            "Synthesize the same private test sentence twice per local voice "
            "selection and emit only non-sensitive audio metadata."
        )
    )
    parser.add_argument(
        "--profile",
        default="default",
        help="Logical voice profile identifier (default: catalog default).",
    )
    parser.add_argument(
        "--emotion",
        action="append",
        dest="emotions",
        help="Logical emotion identifier; repeat to test more than one.",
    )
    return parser


def _create_service() -> LocalSpeechSynthesisService:
    """Compose the production service from the immutable application settings."""

    return create_local_speech_synthesis_service(
        SETTINGS.base_dir,
        allow_local_evaluation=(
            SETTINGS.gpt_sovits_allow_local_evaluation
        ),
        request_timeout_seconds=(
            SETTINGS.gpt_sovits_request_timeout_seconds
        ),
        probe_timeout_seconds=(
            SETTINGS.gpt_sovits_probe_timeout_seconds
        ),
        deterministic_seed=SETTINGS.gpt_sovits_deterministic_seed,
    )


def _build_requests(
    profile_id: str,
    emotions: Sequence[str] | None,
) -> tuple[SynthesisRequest, ...]:
    """Validate bounded logical selections before any filesystem or network work."""

    selected_emotions = tuple(emotions) if emotions else ("neutral",)
    if (
        len(selected_emotions) > _MAX_EMOTIONS
        or len(set(selected_emotions)) != len(selected_emotions)
    ):
        raise SynthesisValidationError(
            "Smoke-test voice selections are invalid."
        )
    return tuple(
        SynthesisRequest(
            text=_SMOKE_TEXT,
            language="zh",
            profile_id=profile_id,
            emotion=emotion,
        )
        for emotion in selected_emotions
    )


def _summarize_audio(result: SynthesisResult) -> dict[str, object]:
    """Return only stable, non-sensitive metadata for validated encoded audio."""

    duration_seconds: float | None = None
    if result.audio_format == "wav":
        try:
            with wave.open(BytesIO(result.audio), "rb") as wav_file:
                frame_count = wav_file.getnframes()
                frame_rate = wav_file.getframerate()
        except (EOFError, wave.Error) as error:
            raise _InvalidSmokeAudioError from error
        if frame_count <= 0 or frame_rate <= 0:
            raise _InvalidSmokeAudioError
        duration_seconds = round(frame_count / frame_rate, 6)
    # SynthesisResult has already validated an Ogg page or complete AAC frame.
    # Their duration remains null because stdlib has no trustworthy decoder.
    return {
        "format": result.audio_format,
        "bytes": len(result.audio),
        "duration_seconds": duration_seconds,
        "sha256": hashlib.sha256(result.audio).hexdigest(),
    }


def _write_safe_error(code: str) -> None:
    """Write one closed error code without serializing exception details."""

    print(
        json.dumps({"error": code}, separators=(",", ":")),
        file=sys.stderr,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run repeated local synthesis and return a shell-friendly status code."""

    arguments = _build_parser().parse_args(argv)
    try:
        requests = _build_requests(arguments.profile, arguments.emotions)
    except (TypeError, SynthesisValidationError):
        _write_safe_error("invalid_selection")
        return 2

    try:
        service = _create_service()
        readiness_codes: list[str] = []
        for request in requests:
            status = service.get_status(
                profile_id=request.profile_id,
                emotion=request.emotion,
            )
            if status.adapter.state == "unavailable":
                _write_safe_error(
                    status.adapter.reason or "synthesis_unavailable"
                )
                return 3
            readiness_codes.append(
                status.adapter.reason or status.adapter.state
            )

        # Buffer every summary until all calls pass. A later failure therefore
        # cannot leave misleading partial success on stdout.
        summaries = [
            _summarize_audio(service.synthesize(request))
            for request in requests
            for _ in range(_REPETITIONS)
        ]
    except SynthesisUnavailableError:
        _write_safe_error("synthesis_unavailable")
        return 3
    except SynthesisFailedError:
        _write_safe_error("synthesis_failed")
        return 4
    except SynthesisValidationError:
        _write_safe_error("invalid_audio")
        return 5
    except _InvalidSmokeAudioError:
        _write_safe_error("invalid_audio")
        return 5
    except Exception:
        # A smoke command may run beside private model paths and prompts. Never
        # expose an unexpected dependency or OS exception through its CLI.
        _write_safe_error("internal_error")
        return 1

    print(
        json.dumps(
            {
                "readiness": (
                    readiness_codes[0]
                    if len(set(readiness_codes)) == 1
                    else "mixed"
                ),
                "results": summaries,
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
