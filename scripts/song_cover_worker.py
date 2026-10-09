"""Create one local high-quality Elysia singing cover from private audio.

The worker is intentionally a short-lived process.  Electron Main owns the
native file selection and launches this script with paths that never cross into
the sandboxed renderer.  Song mode separates one complete mix with Demucs;
stems mode accepts an already aligned vocal and accompaniment pair.  Both modes
can apply one bounded F0-domain vocal shift inside So-VITS-SVC and a matched
duration-preserving accompaniment shift, preserve only high-confidence
unvoiced consonant energy, and use a vocal-aware clarity mix.

Neither the selected song nor the private voice model is copied into Git or an
installer.  Progress is emitted as prefixed JSON lines so unrelated upstream
diagnostics cannot be mistaken for trusted lifecycle messages.
"""

from __future__ import annotations

import argparse
from array import array
import gc
import hashlib
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import runpy
import shutil
import stat
import subprocess
import sys
from typing import Iterator, NoReturn, Optional, Sequence
import wave


_EVENT_PREFIX = "ELYSIA_SONG_COVER "
_SUPPORTED_EXTENSIONS = frozenset(
    {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}
)
_MAX_SOURCE_BYTES = 1024 * 1024 * 1024
_MAX_DURATION_SECONDS = 12 * 60
_MIN_DURATION_SECONDS = 1.0
_NATIVE_COMMAND_TIMEOUT_SECONDS = 30 * 60
_PROBE_TIMEOUT_SECONDS = 30
_MAX_DECODED_BYTES = 140 * 1024 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_SAMPLE_RATE = 44_100
_ALLOWED_KEY_SHIFTS = frozenset({-2, -1, 0, 1, 2})
_SVC_SLICE_DB = -48
_SVC_NOISE_SCALE = 0.16
_SVC_LOUDNESS_ENVELOPE_BLEND = 0.50
_CONVERTED_VOCAL_GAIN = 1.161449  # +1.3 dB
_CONSONANT_LAYER_GAIN = 0.125893  # -18 dB
_EXPECTED_DEMUCS_VERSION = "4.0.1"
_EXPECTED_PYTHON_BYTES = 103_416
_EXPECTED_PYTHON_SHA256 = (
    "07c96337729e3c4c986e8f5660a97cb475c4c04842606be2654b37892af57de9"
)
_EXPECTED_FFMPEG_BYTES = 52_925_440
_EXPECTED_FFMPEG_SHA256 = (
    "b6a4d917a444790f4c06ada640c1c0c95aecde2f8953ed8d0dfb19352500bfcd"
)
_EXPECTED_FFPROBE_BYTES = 122_135_040
_EXPECTED_FFPROBE_SHA256 = (
    "2da5b980a9a14a808f423d181c4ed51c2b8af11b1366699f3f7eab0609926f8f"
)
_EXPECTED_SINGING_RUNTIME_FILES = 119
_EXPECTED_SINGING_RUNTIME_BYTES = 1_107_739
_EXPECTED_SINGING_RUNTIME_SHA256 = (
    "3146d43372b416a46c17c2d06227f98b92c1e26c62eea208e6277d1428b30712"
)
_EXPECTED_DEMUCS_MODEL_BYTES = 84_141_911
_EXPECTED_DEMUCS_MODEL_SHA256 = (
    "8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4"
)
_EXPECTED_CONTENTVEC_BYTES = 189_507_909
_EXPECTED_CONTENTVEC_SHA256 = (
    "f54b40fd2802423a5643779c4861af1e9ee9c1564dc9d32f54f20b5ffba7db96"
)
_EXPECTED_FCPE_BYTES = 69_005_189
_EXPECTED_FCPE_SHA256 = (
    "c3a8dd2dbd51baf19ed295006f2ac25dba6dd60adc7ec578ae5fbd94970951da"
)
_EXPECTED_SVC_PYTHON_FILES = 126
_EXPECTED_SVC_PYTHON_BYTES = 887_322
_EXPECTED_SVC_PYTHON_SHA256 = (
    "d5647b6aa4bc07ed9afffe19701948e18deaa2cb2ab1fe0adb8e5eb9b96a8f6f"
)
_EXPECTED_SVC_MODEL_BYTES = 160_938_745
_EXPECTED_SVC_MODEL_SHA256 = (
    "49ad5e97a709f5cccab7d9329cb3012f07a657c73574eb6013e257640cb16873"
)
_EXPECTED_SVC_CONFIG_BYTES = 2_522
_EXPECTED_SVC_CONFIG_SHA256 = (
    "2a2675b683d94c0c969f03f9aeef55fc416b4053b5e572a97c6f8a9a462a4dc5"
)
_SINGING_RUNTIME_PACKAGES = (
    "cloudpickle",
    "demucs",
    "dora",
    "julius",
    "openunmix",
    "submitit",
    "treetable",
)
_SINGING_RUNTIME_IMPORTS = (*_SINGING_RUNTIME_PACKAGES, "retrying", "lameenc")
_JOB_TOKEN_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_CONSONANT_FRAME_SAMPLES = 1_024
_CONSONANT_MIN_RMS = 64.0
_CONSONANT_MIN_HIGH_FREQUENCY_FRACTION = 0.30
_CONSONANT_MIN_ZERO_CROSSING_RATE = 0.08
_CONSONANT_MIN_INTERVAL_VARIATION = 0.35
_CONSONANT_MIN_SELECTED_FRAMES = 3
_CONSONANT_MAX_SELECTED_FRACTION = 0.35


class _SongCoverFailure(RuntimeError):
    """Represent one renderer-safe terminal failure from the local pipeline."""


def _emit(stage: str, progress_percent: int, message: str) -> None:
    """Publish one bounded progress event for the Electron parent process."""

    payload = {
        "message": message,
        "progressPercent": progress_percent,
        "stage": stage,
    }
    print(
        f"{_EVENT_PREFIX}{json.dumps(payload, ensure_ascii=True, separators=(',', ':'))}",
        flush=True,
    )


def _fail(message: str) -> NoReturn:
    """Raise one stable failure without leaking a selected local path."""

    raise _SongCoverFailure(message)


def _is_reparse(info: os.stat_result) -> bool:
    """Recognize Windows junctions and other reparse-backed path redirects."""

    return bool(
        getattr(info, "st_file_attributes", 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _require_safe_path(
    path: Path,
    *,
    label: str,
    leaf_is_file: bool,
) -> Path:
    """Reject links/reparse points in every component of one required path."""

    candidate = Path(os.path.abspath(str(path)))
    try:
        current = Path(candidate.anchor)
        parts = candidate.parts[1:] if candidate.anchor else candidate.parts
        if not parts:
            raise OSError
        for index, part in enumerate(parts):
            current = current / part
            info = os.lstat(current)
            final = index == len(parts) - 1
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                raise OSError
            if final and leaf_is_file:
                if not stat.S_ISREG(info.st_mode):
                    raise OSError
            elif not stat.S_ISDIR(info.st_mode):
                raise OSError
    except (OSError, RuntimeError, ValueError) as error:
        raise _SongCoverFailure(f"{label} is unavailable or unsafe.") from error
    return candidate


def _require_file(path: Path, label: str) -> Path:
    """Resolve one required regular file while rejecting links and devices."""

    return _require_safe_path(path, label=label, leaf_is_file=True)


def _require_directory(path: Path, label: str) -> Path:
    """Resolve one required directory while rejecting a linked root."""

    return _require_safe_path(path, label=label, leaf_is_file=False)


def _iter_safe_tree(
    root: Path,
    *,
    label: str,
    excluded_directory_names: frozenset[str] = frozenset(),
) -> Iterator[tuple[Path, bool]]:
    """Walk a tree without following a symlink, junction, or special node."""

    safe_root = _require_directory(root, label)
    pending = [safe_root]
    while pending:
        directory = pending.pop()
        for candidate, is_directory in _iter_safe_directory(
            directory,
            label=label,
        ):
            if is_directory:
                yield candidate, True
                if candidate.name not in excluded_directory_names:
                    pending.append(candidate)
            else:
                yield candidate, False


def _iter_safe_directory(
    root: Path,
    *,
    label: str,
) -> Iterator[tuple[Path, bool]]:
    """Enumerate one directory without following redirected child entries."""

    safe_root = _require_directory(root, label)
    try:
        with os.scandir(safe_root) as entries:
            current_entries = list(entries)
    except OSError as error:
        raise _SongCoverFailure(f"{label} could not be verified.") from error
    for entry in current_entries:
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError as error:
            raise _SongCoverFailure(f"{label} could not be verified.") from error
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            _fail(f"{label} contains an unsupported link or junction.")
        candidate = Path(entry.path)
        if stat.S_ISDIR(info.st_mode):
            yield candidate, True
        elif stat.S_ISREG(info.st_mode):
            yield candidate, False
        else:
            _fail(f"{label} contains an unsupported special file.")


def _require_or_create_directory(path: Path, *, label: str) -> Path:
    """Create one direct child only after authenticating its existing parent."""

    candidate = Path(os.path.abspath(str(path)))
    _require_directory(candidate.parent, label)
    try:
        os.mkdir(candidate)
    except FileExistsError:
        pass
    except OSError as error:
        raise _SongCoverFailure(f"{label} could not be created.") from error
    return _require_directory(candidate, label)


def _unlink_safe_file(path: Path, *, label: str) -> None:
    """Remove one optional regular file without traversing a redirected parent."""

    candidate = Path(os.path.abspath(str(path)))
    _require_directory(candidate.parent, label)
    try:
        info = os.lstat(candidate)
    except FileNotFoundError:
        return
    except OSError as error:
        raise _SongCoverFailure(f"{label} could not be verified.") from error
    if (
        stat.S_ISLNK(info.st_mode)
        or _is_reparse(info)
        or not stat.S_ISREG(info.st_mode)
    ):
        _fail(f"{label} is unavailable or unsafe.")
    try:
        candidate.unlink()
    except OSError as error:
        raise _SongCoverFailure(f"{label} could not be cleared.") from error


def _remove_safe_tree(root: Path, *, label: str) -> None:
    """Delete one verified private tree without following redirected children."""

    entries = list(_iter_safe_tree(root, label=label))
    files = [path for path, is_directory in entries if not is_directory]
    directories = [path for path, is_directory in entries if is_directory]
    try:
        for candidate in files:
            _require_file(candidate, label).unlink()
        for candidate in sorted(
            directories,
            key=lambda value: len(value.parts),
            reverse=True,
        ):
            _require_directory(candidate, label).rmdir()
        _require_directory(root, label).rmdir()
    except OSError as error:
        raise _SongCoverFailure(f"{label} could not be cleared.") from error


def _sha256(path: Path) -> str:
    """Hash one bounded runtime asset without loading it into process memory."""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _require_asset(
    path: Path,
    *,
    label: str,
    expected_bytes: int,
    expected_sha256: str,
) -> Path:
    """Authenticate one exact local runtime or private-model asset."""

    candidate = _require_file(path, label)
    if candidate.stat().st_size != expected_bytes:
        _fail(f"{label} does not match the reviewed local asset.")
    if _sha256(candidate) != expected_sha256:
        _fail(f"{label} does not match the reviewed local asset.")
    return candidate


def _remove_bytecode_caches(root: Path, *, label: str) -> None:
    """Remove generated bytecode so imports cannot bypass reviewed source bytes."""

    try:
        caches = [
            candidate
            for candidate, is_directory in _iter_safe_tree(
                root,
                label=label,
                excluded_directory_names=frozenset({"__pycache__"}),
            )
            if is_directory and candidate.name == "__pycache__"
        ]
        for cache_directory in caches:
            _remove_safe_tree(cache_directory, label=f"{label} bytecode cache")
    except (OSError, _SongCoverFailure) as error:
        raise _SongCoverFailure(f"{label} bytecode cache could not be cleared.") from error


def _require_python_tree(
    root: Path,
    *,
    label: str,
    expected_files: int,
    expected_bytes: int,
    expected_sha256: str,
) -> None:
    """Authenticate imported local Python code using a deterministic tree hash.

    Writable inference scratch directories and bytecode caches are excluded;
    every source path, length, and byte is included.  Compiled extensions and
    links are rejected because either could bypass the reviewed ``.py`` set.
    """

    root = _require_directory(root, label)
    excluded_parts = frozenset({".git", "raw", "results"})
    forbidden_suffixes = frozenset({".dll", ".pyd", ".pyc", ".so"})
    python_files: list[tuple[str, Path]] = []
    try:
        for candidate, is_directory in _iter_safe_tree(
            root,
            label=label,
            excluded_directory_names=excluded_parts,
        ):
            relative = candidate.relative_to(root)
            if any(part in excluded_parts for part in relative.parts):
                continue
            if is_directory:
                continue
            if candidate.suffix.lower() in forbidden_suffixes:
                _fail(f"{label} contains unreviewed executable code.")
            if candidate.suffix.lower() == ".py":
                python_files.append((relative.as_posix(), candidate))
    except OSError as error:
        raise _SongCoverFailure(f"{label} could not be verified.") from error

    digest = hashlib.sha256()
    total_bytes = 0
    for relative_name, candidate in sorted(python_files):
        content = candidate.read_bytes()
        total_bytes += len(content)
        digest.update(relative_name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
    if (
        len(python_files) != expected_files
        or total_bytes != expected_bytes
        or digest.hexdigest() != expected_sha256
    ):
        _fail(f"{label} does not match the reviewed local runtime.")


def _require_singing_runtime(root: Path) -> None:
    """Authenticate every Demucs-only dependency absent from the base runtime."""

    root = _require_directory(root, "Song-separation package")
    reviewed_files: list[tuple[str, Path]] = []
    forbidden_suffixes = frozenset({".dll", ".pyc", ".so"})
    try:
        for package_name in _SINGING_RUNTIME_PACKAGES:
            package_root = _require_directory(
                root / package_name,
                "Song-separation package",
            )
            for candidate, is_directory in _iter_safe_tree(
                package_root,
                label="Song-separation package",
            ):
                if is_directory:
                    continue
                suffix = candidate.suffix.lower()
                if suffix in forbidden_suffixes or suffix == ".pyd":
                    _fail("Song-separation package contains unreviewed executable code.")
                if suffix == ".py":
                    reviewed_files.append(
                        (candidate.relative_to(root).as_posix(), candidate)
                    )
        for file_name in (
            "retrying.py",
            "lameenc.cp39-win_amd64.pyd",
            "demucs/remote/files.txt",
            "demucs/remote/htdemucs.yaml",
        ):
            candidate = _require_file(root / file_name, "Song-separation dependency")
            reviewed_files.append((candidate.relative_to(root).as_posix(), candidate))
        allowed_providers = {
            *(name.casefold() for name in _SINGING_RUNTIME_PACKAGES),
            "retrying.py",
            "lameenc.cp39-win_amd64.pyd",
        }
        protected_imports = {
            *(name.casefold() for name in _SINGING_RUNTIME_PACKAGES),
            "retrying",
            "lameenc",
        }
        # Python may prefer an extension module or a sibling module over the
        # reviewed package directory. Reject every alternate provider for the
        # exact imports used by Demucs instead of merely hashing the provider
        # we expect to win.
        for candidate, _is_directory in _iter_safe_directory(
            root,
            label="Song-separation package",
        ):
            provider_name = candidate.name.casefold()
            protects_import = any(
                provider_name == import_name
                or provider_name.startswith(f"{import_name}.")
                for import_name in protected_imports
            )
            if protects_import and provider_name not in allowed_providers:
                _fail("Song-separation package contains a conflicting module.")
    except OSError as error:
        raise _SongCoverFailure(
            "Song-separation package could not be verified."
        ) from error

    digest = hashlib.sha256()
    total_bytes = 0
    for relative_name, candidate in sorted(reviewed_files):
        content = candidate.read_bytes()
        total_bytes += len(content)
        digest.update(relative_name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
    if (
        len(reviewed_files) != _EXPECTED_SINGING_RUNTIME_FILES
        or total_bytes != _EXPECTED_SINGING_RUNTIME_BYTES
        or digest.hexdigest() != _EXPECTED_SINGING_RUNTIME_SHA256
    ):
        _fail("Song-separation package does not match the reviewed local runtime.")


def _run_command(arguments: Sequence[str], failure_message: str) -> None:
    """Run one exact native audio command and collapse its private diagnostics."""

    try:
        completed = subprocess.run(
            list(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            # Native decoders can produce unbounded per-packet diagnostics for
            # corrupt input.  The renderer receives only fixed errors, so
            # discarding that stream prevents a 30-minute command from growing
            # the worker heap without changing the public failure contract.
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=_NATIVE_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise _SongCoverFailure(failure_message) from error
    if completed.returncode != 0:
        _fail(failure_message)


def _probe_duration(ffprobe: Path, source: Path) -> float:
    """Return the single selected track duration after bounded native probing."""

    try:
        completed = subprocess.run(
            [
                str(ffprobe),
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_type,duration",
                "-of",
                "json",
                str(source),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            # ffprobe diagnostics are not part of the closed worker protocol;
            # discard them so malformed media cannot consume unbounded memory.
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise _SongCoverFailure(
            "The selected song could not be read as audio."
        ) from error
    if completed.returncode != 0 or len(completed.stdout) > 64 * 1024:
        _fail("The selected song could not be read as audio.")
    try:
        payload = json.loads(completed.stdout)
        streams = payload["streams"]
        if (
            not isinstance(streams, list)
            or len(streams) != 1
            or not isinstance(streams[0], dict)
            or streams[0].get("codec_type") != "audio"
        ):
            _fail("The selected song does not contain a readable audio track.")
        duration = float(streams[0]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise _SongCoverFailure(
            "The selected song does not contain a readable audio track."
        ) from error
    if not _MIN_DURATION_SECONDS <= duration <= _MAX_DURATION_SECONDS:
        _fail("Choose a song between 1 second and 12 minutes long.")
    return duration


def _validate_source(path: Path, ffprobe: Path) -> tuple[Path, float]:
    """Validate the user-selected song before any model or GPU work begins."""

    source = _require_file(path, "Selected song")
    if source.suffix.lower() not in _SUPPORTED_EXTENSIONS:
        _fail("Choose a supported local audio file.")
    if source.stat().st_size < 1 or source.stat().st_size > _MAX_SOURCE_BYTES:
        _fail("The selected song is empty or exceeds the 1 GiB limit.")
    return source, _probe_duration(ffprobe, source)


def _validate_source_arguments(arguments: argparse.Namespace) -> None:
    """Require exactly one reviewed native-path contract for the source mode."""

    song_contract = (
        arguments.input is not None
        and arguments.vocal_input is None
        and arguments.accompaniment_input is None
    )
    stems_contract = (
        arguments.input is None
        and arguments.vocal_input is not None
        and arguments.accompaniment_input is not None
    )
    if (
        arguments.source_mode == "song"
        and song_contract
    ) or (
        arguments.source_mode == "stems"
        and stems_contract
    ):
        return
    _fail("Song-cover source inputs are invalid.")


def _target_samples(duration: float) -> int:
    """Convert a validated duration to one exact 44.1 kHz output length."""

    samples = round(duration * _SAMPLE_RATE)
    maximum = round(_MAX_DURATION_SECONDS * _SAMPLE_RATE)
    if samples < 1 or samples > maximum:
        _fail("The selected audio duration is invalid.")
    return samples


def _decode_song(
    ffmpeg: Path,
    source: Path,
    duration: float,
    output: Path,
) -> None:
    """Decode exactly the first validated audio stream to canonical PCM."""

    target_samples = _target_samples(duration)
    audio_filter = (
        f"apad=whole_len={target_samples},"
        f"atrim=end_sample={target_samples},asetpts=N/SR/TB"
    )

    _run_command(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            # ffprobe validates a:0. Explicit mapping prevents FFmpeg's stream
            # heuristic from silently selecting a later multichannel track.
            "-map",
            "0:a:0",
            "-t",
            f"{duration:.6f}",
            "-vn",
            "-af",
            audio_filter,
            "-ac",
            "2",
            "-ar",
            str(_SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            "-fs",
            str(_MAX_DECODED_BYTES),
            "-y",
            str(output),
        ],
        "The selected song could not be decoded.",
    )


def _transpose_audio(
    ffmpeg: Path,
    source: Path,
    output: Path,
    *,
    key_shift_semitones: int,
    target_samples: int,
) -> None:
    """Apply one bounded whole-track key shift without changing sample count.

    The bundled FFmpeg has no Rubber Band filter.  A small reviewed shift uses
    sample-rate transposition followed by pitch-preserving tempo correction;
    restricting the contract to two semitones in either direction bounds
    artifacts. Only the accompaniment uses this waveform transform; SVC moves
    vocal F0 directly so FCPE and ContentVec still receive the clean source.
    """

    if key_shift_semitones not in _ALLOWED_KEY_SHIFTS:
        _fail("The requested song-cover key shift is not supported.")
    maximum = round(_MAX_DURATION_SECONDS * _SAMPLE_RATE)
    if target_samples < 1 or target_samples > maximum:
        _fail("The requested song-cover output length is invalid.")
    rate = round(_SAMPLE_RATE * (2 ** (key_shift_semitones / 12)))
    tempo = _SAMPLE_RATE / rate
    audio_filter = (
        f"asetrate={rate},aresample={_SAMPLE_RATE},atempo={tempo:.9f},"
        f"apad=whole_len={target_samples},"
        f"atrim=end_sample={target_samples},asetpts=N/SR/TB"
    )
    _run_command(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vn",
            "-af",
            audio_filter,
            "-ar",
            str(_SAMPLE_RATE),
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(output),
        ],
        "The selected audio could not be shifted to the protected key.",
    )


def _prepare_mono_vocals(
    ffmpeg: Path,
    vocals: Path,
    output: Path,
    *,
    target_samples: int,
) -> None:
    """Create one exact-length mono stem shared by conversion and DSP analysis."""

    audio_filter = (
        f"apad=whole_len={target_samples},"
        f"atrim=end_sample={target_samples},asetpts=N/SR/TB"
    )
    _run_command(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(vocals),
            "-vn",
            "-af",
            audio_filter,
            "-ac",
            "1",
            "-ar",
            str(_SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            "-y",
            str(output),
        ],
        "The vocal track could not be prepared for conversion.",
    )


def _load_singing_runtime_modules(demucs_site: Path) -> tuple[object, object]:
    """Load every pinned separation provider from its authenticated origin.

    Appending the reviewed directory is insufficient because Python searches
    earlier entries first.  A sibling ``dora.py`` or native ``lameenc`` in the
    base runtime could otherwise execute while all aggregate hashes still
    match.  The two-phase check rejects every earlier provider, authenticates
    each winning spec, and then verifies the imported module origin again.
    """

    demucs_site = _require_directory(demucs_site, "Song-separation package")
    protected = tuple(name.casefold() for name in _SINGING_RUNTIME_IMPORTS)
    if any(
        module_name.casefold() == import_name
        or module_name.casefold().startswith(f"{import_name}.")
        for module_name in sys.modules
        for import_name in protected
    ):
        _fail("An unexpected song-separation module is already active.")
    try:
        for module_name in _SINGING_RUNTIME_IMPORTS:
            if importlib.util.find_spec(module_name) is not None:
                _fail("An unexpected song-separation module is already available.")
    except (ImportError, AttributeError, ValueError) as error:
        raise _SongCoverFailure(
            "The song-separation import boundary could not be verified."
        ) from error

    expected_origins = {
        **{
            package_name: demucs_site / package_name / "__init__.py"
            for package_name in _SINGING_RUNTIME_PACKAGES
        },
        "retrying": demucs_site / "retrying.py",
        "lameenc": demucs_site / "lameenc.cp39-win_amd64.pyd",
    }
    sys.path.append(str(demucs_site))
    imported: dict[str, object] = {}
    try:
        for module_name, expected_origin in expected_origins.items():
            spec = importlib.util.find_spec(module_name)
            if spec is None or spec.origin is None:
                _fail("The local song-separation runtime origin is invalid.")
            actual_origin = _require_file(
                Path(spec.origin),
                "Song-separation module",
            )
            if actual_origin != _require_file(
                expected_origin,
                "Song-separation module",
            ):
                _fail("The local song-separation runtime origin is invalid.")
        for module_name, expected_origin in expected_origins.items():
            module = importlib.import_module(module_name)
            module_path = _require_file(
                Path(getattr(module, "__file__", "")),
                "Song-separation module",
            )
            if module_path != _require_file(
                expected_origin,
                "Song-separation module",
            ):
                _fail("The local song-separation runtime origin is invalid.")
            imported[module_name] = module
        separate = importlib.import_module("demucs.separate")
    except _SongCoverFailure:
        raise
    except Exception as error:
        raise _SongCoverFailure(
            "The local song-separation runtime is unavailable."
        ) from error
    return imported["demucs"], separate


def _separate_vocals(
    decoded_song: Path,
    separation_root: Path,
    demucs_site: Path,
    torch_home: Path,
) -> tuple[Path, Path]:
    """Split vocals from accompaniment with the pinned local Demucs model."""

    # The loader appends rather than prepends so the reviewed CUDA Torch already
    # supplied by the isolated GPT-SoVITS runtime remains authoritative.
    os.environ["TORCH_HOME"] = str(torch_home)
    demucs, demucs_separate = _load_singing_runtime_modules(demucs_site)
    if getattr(demucs, "__version__", None) != _EXPECTED_DEMUCS_VERSION:
        _fail("The local song-separation runtime version is not supported.")
    try:
        getattr(demucs_separate, "main")(
            [
                "--two-stems",
                "vocals",
                "-n",
                "htdemucs",
                "--device",
                "cuda",
                "--segment",
                "7",
                "--overlap",
                "0.5",
                "--shifts",
                "1",
                "-j",
                "1",
                "-o",
                str(separation_root),
                str(decoded_song),
            ]
        )
    except SystemExit as error:
        if error.code not in (None, 0):
            raise _SongCoverFailure("Song vocal separation failed.") from error
    except Exception as error:
        raise _SongCoverFailure("Song vocal separation failed.") from error
    stem_root = separation_root / "htdemucs" / decoded_song.stem
    vocals = _require_file(stem_root / "vocals.wav", "Separated vocals")
    accompaniment = _require_file(
        stem_root / "no_vocals.wav", "Separated accompaniment"
    )
    return vocals, accompaniment


def _release_gpu_cache() -> None:
    """Release stage-local allocations before loading the singing model."""

    gc.collect()
    try:
        import torch  # type: ignore[import-not-found]

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        # Cleanup is advisory; the following model load remains authoritative.
        pass


def _convert_vocals(
    *,
    ffmpeg: Path,
    vocals: Path,
    output: Path,
    svc_root: Path,
    svc_model: Path,
    svc_config: Path,
    job_token: str,
    key_shift_semitones: int,
) -> None:
    """Replace the singing timbre and apply one bounded F0-domain key shift.

    Moving the detected F0 inside So-VITS-SVC avoids resampling the source
    vocal before ContentVec and FCPE inspect it.  That distinction matters at
    the low end of the checkpoint, where another waveform transformation can
    turn a marginal but usable note into a rough or poorly articulated one.
    """

    if key_shift_semitones not in _ALLOWED_KEY_SHIFTS:
        _fail("The requested song-cover key shift is not supported.")

    raw_name = f"elysia_cover_{job_token}.wav"
    raw_path = svc_root / "raw" / raw_name
    result_path = (
        svc_root
        / "results"
        / f"{raw_name}_{key_shift_semitones}key_Elysia_sovits_fcpe.wav"
    )
    # The upstream CLI requires fixed scratch directories inside its runtime.
    # Create only direct children of the authenticated runtime, then revalidate
    # every path component so a junction cannot redirect private vocals.
    _require_or_create_directory(raw_path.parent, label="So-VITS-SVC raw scratch")
    _require_or_create_directory(
        result_path.parent,
        label="So-VITS-SVC result scratch",
    )
    _unlink_safe_file(raw_path, label="So-VITS-SVC raw scratch")
    _unlink_safe_file(result_path, label="So-VITS-SVC result scratch")
    _run_command(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(vocals),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(raw_path),
        ],
        "The separated vocal track could not be prepared.",
    )
    previous_argv = sys.argv[:]
    previous_cwd = Path.cwd()
    sys.path.insert(0, str(svc_root))
    try:
        os.chdir(svc_root)
        sys.argv = [
            str(svc_root / "inference_main.py"),
            "-m",
            str(svc_model),
            "-c",
            str(svc_config),
            "-n",
            raw_name,
            "-t",
            str(key_shift_semitones),
            "-s",
            "Elysia",
            "-sd",
            str(_SVC_SLICE_DB),
            "-f0p",
            "fcpe",
            "-ns",
            str(_SVC_NOISE_SCALE),
            "-lea",
            str(_SVC_LOUDNESS_ENVELOPE_BLEND),
            "-wf",
            "wav",
        ]
        runpy.run_path(str(svc_root / "inference_main.py"), run_name="__main__")
        converted = _require_file(result_path, "Converted singing voice")
        shutil.copyfile(converted, output)
    except SystemExit as error:
        if error.code not in (None, 0):
            raise _SongCoverFailure("Elysia singing-voice conversion failed.") from error
    except _SongCoverFailure:
        raise
    except Exception as error:
        raise _SongCoverFailure("Elysia singing-voice conversion failed.") from error
    finally:
        sys.argv = previous_argv
        os.chdir(previous_cwd)
        if sys.path and sys.path[0] == str(svc_root):
            sys.path.pop(0)
        _unlink_safe_file(raw_path, label="So-VITS-SVC raw scratch")
        _unlink_safe_file(result_path, label="So-VITS-SVC result scratch")


def _frame_has_clear_consonant(
    vocal_samples: Sequence[int],
    highpass_samples: Sequence[int],
) -> bool:
    """Recognize one conservative aperiodic, high-frequency vocal frame.

    A high-frequency energy ratio alone would retain pitched harmonics and
    cymbal leakage.  The zero-crossing intervals must also be irregular, which
    admits fricative-like noise while rejecting stable high notes.  Ambiguous
    frames deliberately return false because this layer supplements rather
    than replaces the converted vocal.
    """

    if len(vocal_samples) != len(highpass_samples) or len(vocal_samples) < 64:
        return False
    full_energy = sum(float(sample) * sample for sample in vocal_samples)
    if full_energy <= 0:
        return False
    rms = math.sqrt(full_energy / len(vocal_samples))
    if rms < _CONSONANT_MIN_RMS:
        return False
    high_energy = sum(float(sample) * sample for sample in highpass_samples)
    high_fraction = min(1.0, high_energy / full_energy)
    if high_fraction < _CONSONANT_MIN_HIGH_FREQUENCY_FRACTION:
        return False

    crossings: list[int] = []
    previous_sign = 0
    for index, sample in enumerate(vocal_samples):
        sign = 1 if sample > 0 else -1 if sample < 0 else previous_sign
        if previous_sign and sign and sign != previous_sign:
            crossings.append(index)
        if sign:
            previous_sign = sign
    crossing_rate = len(crossings) / max(1, len(vocal_samples) - 1)
    if crossing_rate < _CONSONANT_MIN_ZERO_CROSSING_RATE or len(crossings) < 4:
        return False
    intervals = [
        current - previous
        for previous, current in zip(crossings, crossings[1:])
    ]
    if not intervals:
        return False
    mean_interval = sum(intervals) / len(intervals)
    if mean_interval <= 0:
        return False
    variance = sum(
        (interval - mean_interval) ** 2 for interval in intervals
    ) / len(intervals)
    interval_variation = math.sqrt(variance) / mean_interval
    return interval_variation >= _CONSONANT_MIN_INTERVAL_VARIATION


def _stabilize_consonant_masks(raw_masks: Sequence[bool]) -> list[float]:
    """Gate low-confidence masks and smooth accepted consonant transitions."""

    if not raw_masks:
        return []
    selected = sum(raw_masks)
    selected_fraction = selected / len(raw_masks)
    if (
        selected < _CONSONANT_MIN_SELECTED_FRAMES
        or selected_fraction > _CONSONANT_MAX_SELECTED_FRACTION
    ):
        return [0.0] * len(raw_masks)

    frame_seconds = _CONSONANT_FRAME_SAMPLES / _SAMPLE_RATE
    attack_coefficient = math.exp(-frame_seconds / 0.005)
    release_coefficient = math.exp(-frame_seconds / 0.040)
    envelope = 0.0
    smoothed: list[float] = []
    for selected_frame in raw_masks:
        target = 1.0 if selected_frame else 0.0
        coefficient = (
            attack_coefficient if target > envelope else release_coefficient
        )
        envelope = target + ((envelope - target) * coefficient)
        if not selected_frame and envelope < 0.0001:
            envelope = 0.0
        smoothed.append(min(1.0, max(0.0, envelope)))
    return smoothed


def _read_pcm16_frame(source: wave.Wave_read, frame_samples: int) -> array[int]:
    """Read one mono PCM16 frame without assuming host byte order."""

    content = source.readframes(frame_samples)
    samples = array("h")
    samples.frombytes(content)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _interpolate_consonant_gain(
    previous_gain: float,
    current_gain: float,
    sample_index: int,
    sample_count: int,
) -> float:
    """Interpolate one frame's envelope without a sample-boundary step.

    ``_stabilize_consonant_masks`` computes exponential envelope endpoints at
    a coarse 1,024-sample cadence.  Applying each endpoint as a constant gain
    would create a discontinuity at every accepted consonant boundary, which
    can be heard as a short electrical click even when the layer is quiet.
    Reconstructing the exponential path between endpoints preserves the chosen
    attack/release timing while keeping adjacent output samples continuous.
    """

    if sample_count <= 0:
        return current_gain
    progress = min(1.0, max(0.0, (sample_index + 1) / sample_count))
    if current_gain > previous_gain:
        start_distance = 1.0 - previous_gain
        end_distance = 1.0 - current_gain
        if start_distance <= 0.0:
            return current_gain
        if end_distance <= 0.0:
            return previous_gain + ((current_gain - previous_gain) * progress)
        return 1.0 - (
            start_distance * ((end_distance / start_distance) ** progress)
        )
    if current_gain < previous_gain:
        if previous_gain <= 0.0:
            return current_gain
        if current_gain <= 0.0:
            return previous_gain * (1.0 - progress)
        return previous_gain * ((current_gain / previous_gain) ** progress)
    return current_gain


def _require_consonant_wave(source: wave.Wave_read) -> None:
    """Require the canonical mono PCM shape used by consonant analysis."""

    if (
        source.getnchannels() != 1
        or source.getsampwidth() != 2
        or source.getframerate() != _SAMPLE_RATE
        or source.getcomptype() != "NONE"
    ):
        _fail("The consonant-protection vocal track is invalid.")


def _analyze_consonant_frames(vocals: Path, highpass: Path) -> list[float]:
    """Return a smoothed mask only for confidently unvoiced high-frequency frames."""

    vocals = _require_file(vocals, "Prepared vocal track")
    highpass = _require_file(highpass, "High-frequency vocal scratch")
    raw_masks: list[bool] = []
    try:
        with wave.open(str(vocals), "rb") as vocal_stream, wave.open(
            str(highpass), "rb"
        ) as high_stream:
            _require_consonant_wave(vocal_stream)
            _require_consonant_wave(high_stream)
            if vocal_stream.getnframes() != high_stream.getnframes():
                _fail("The consonant-protection vocal tracks are misaligned.")
            while vocal_stream.tell() < vocal_stream.getnframes():
                vocal_frame = _read_pcm16_frame(
                    vocal_stream,
                    _CONSONANT_FRAME_SAMPLES,
                )
                high_frame = _read_pcm16_frame(
                    high_stream,
                    _CONSONANT_FRAME_SAMPLES,
                )
                if len(vocal_frame) != len(high_frame):
                    _fail("The consonant-protection vocal tracks are misaligned.")
                raw_masks.append(
                    _frame_has_clear_consonant(vocal_frame, high_frame)
                )
    except _SongCoverFailure:
        raise
    except (EOFError, OSError, wave.Error) as error:
        raise _SongCoverFailure(
            "The consonant-protection vocal track could not be analyzed."
        ) from error
    return _stabilize_consonant_masks(raw_masks)


def _write_consonant_layer(
    highpass: Path,
    output: Path,
    masks: Sequence[float],
) -> None:
    """Write one exact-length masked high-frequency layer as mono PCM16."""

    highpass = _require_file(highpass, "High-frequency vocal scratch")
    _require_directory(output.parent, "Song-cover job directory")
    _unlink_safe_file(output, label="Consonant-protection vocal layer")
    try:
        with wave.open(str(highpass), "rb") as source:
            _require_consonant_wave(source)
            expected_frames = math.ceil(
                source.getnframes() / _CONSONANT_FRAME_SAMPLES
            )
            if len(masks) != expected_frames:
                _fail("The consonant-protection mask is misaligned.")
            with wave.open(str(output), "wb") as destination:
                destination.setnchannels(1)
                destination.setsampwidth(2)
                destination.setframerate(_SAMPLE_RATE)
                previous_gain = 0.0
                for gain in masks:
                    frame = _read_pcm16_frame(
                        source,
                        _CONSONANT_FRAME_SAMPLES,
                    )
                    if (
                        gain <= (1 / 32_768)
                        and previous_gain <= (1 / 32_768)
                    ):
                        destination.writeframesraw(b"\0" * (len(frame) * 2))
                        previous_gain = 0.0
                        continue
                    for index, sample in enumerate(frame):
                        interpolated_gain = _interpolate_consonant_gain(
                            previous_gain,
                            gain,
                            index,
                            len(frame),
                        )
                        scaled = round(sample * interpolated_gain)
                        frame[index] = min(32_767, max(-32_768, scaled))
                    if sys.byteorder != "little":
                        frame.byteswap()
                    destination.writeframesraw(frame.tobytes())
                    previous_gain = gain
                destination.writeframes(b"")
    except _SongCoverFailure:
        raise
    except (EOFError, OSError, OverflowError, wave.Error) as error:
        raise _SongCoverFailure(
            "The consonant-protection vocal layer could not be created."
        ) from error
    _require_file(output, "Consonant-protection vocal layer")


def _create_consonant_layer(
    ffmpeg: Path,
    vocals: Path,
    output: Path,
    *,
    target_samples: int,
) -> None:
    """Extract a bounded consonant layer, emitting silence when confidence is low."""

    highpass = output.with_name(f".{output.stem}-highpass.wav")
    audio_filter = (
        "highpass=f=3200,"
        f"apad=whole_len={target_samples},"
        f"atrim=end_sample={target_samples},asetpts=N/SR/TB"
    )
    try:
        _unlink_safe_file(highpass, label="High-frequency vocal scratch")
        _run_command(
            [
                str(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(vocals),
                "-vn",
                "-af",
                audio_filter,
                "-ac",
                "1",
                "-ar",
                str(_SAMPLE_RATE),
                "-c:a",
                "pcm_s16le",
                "-y",
                str(highpass),
            ],
            "The vocal consonants could not be isolated.",
        )
        masks = _analyze_consonant_frames(vocals, highpass)
        _write_consonant_layer(highpass, output, masks)
    finally:
        _unlink_safe_file(highpass, label="High-frequency vocal scratch")


def _mix_cover(
    ffmpeg: Path,
    converted_vocals: Path,
    consonant_layer: Path,
    accompaniment: Path,
    wav_output: Path,
    playback_output: Path,
    *,
    target_samples: int,
) -> None:
    """Create a clear vocal-aware mix with exact duration and safe headroom."""

    filter_graph = (
        "[0:a]aformat=sample_fmts=fltp:sample_rates=44100:"
        "channel_layouts=mono,pan=stereo|c0=c0|c1=c0[vc];"
        "[2:a]aformat=sample_fmts=fltp:sample_rates=44100:"
        "channel_layouts=mono,pan=stereo|c0=c0|c1=c0,"
        f"volume={_CONSONANT_LAYER_GAIN:.6f}[c];"
        # The old bundled amix divides by its input count. Restore the sum so
        # the consonant layer is exactly -18 dB relative to its extracted stem.
        # Private-sample A/B found this was the first conservative gain that
        # improved recognition while the strict unvoiced mask still prevented
        # measurable high-frequency noise from spreading into inactive frames.
        "[vc][c]amix=inputs=2:duration=longest:dropout_transition=0,"
        "volume=2,highpass=f=60,equalizer=f=3000:t=q:w=0.8:g=0.5,"
        # A measured +1.3 dB linear recovery matched the source vocal more
        # closely than the former compressor/makeup chain.  Avoiding another
        # compressor also preserves phrase-level dynamics; the final limiter
        # remains the safety boundary for the completed mix.
        f"volume={_CONVERTED_VOCAL_GAIN:.6f},asplit=2[v][key0];"
        # This explicit format is required by the pinned legacy FFmpeg. Without
        # it sidechaincompress cannot negotiate the crossover/key sample types.
        "[key0]aformat=sample_fmts=dbl:sample_rates=44100:"
        "channel_layouts=stereo[key];"
        "[1:a]aformat=sample_fmts=fltp:sample_rates=44100:"
        "channel_layouts=stereo,acrossover=split='2200 5200':order=4th"
        "[lo][mid0][hi];"
        "[mid0]aformat=sample_fmts=dbl:sample_rates=44100:"
        "channel_layouts=stereo[mid];"
        "[mid][key]sidechaincompress=threshold=0.05:ratio=2.5:"
        "attack=5:release=120:makeup=1[midduck];"
        # Each amix gain compensates only the legacy input-count division. The
        # final limiter leaves one decibel of headroom for MP3 intersample peaks.
        "[lo][midduck][hi]amix=inputs=3:duration=longest:"
        "dropout_transition=0,volume=3[inst];"
        "[inst][v]amix=inputs=2:duration=longest:dropout_transition=0,"
        "volume=2,alimiter=limit=0.891251:level=false,"
        f"apad=whole_len={target_samples},atrim=end_sample={target_samples},"
        "asetpts=N/SR/TB[out]"
    )
    _run_command(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(converted_vocals),
            "-i",
            str(accompaniment),
            "-i",
            str(consonant_layer),
            "-filter_complex",
            filter_graph,
            "-map",
            "[out]",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(wav_output),
        ],
        "The Elysia vocal and accompaniment could not be mixed.",
    )
    _run_command(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(wav_output),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "320k",
            "-y",
            str(playback_output),
        ],
        "The Elysia cover preview could not be encoded.",
    )


def _parser() -> argparse.ArgumentParser:
    """Build the closed command-line contract owned by Electron Main."""

    parser = argparse.ArgumentParser(description="Create one local Elysia song cover.")
    parser.add_argument(
        "--source-mode",
        choices=("song", "stems"),
        default="song",
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument("--vocal-input", type=Path)
    parser.add_argument("--accompaniment-input", type=Path)
    parser.add_argument(
        "--key-shift-semitones",
        choices=tuple(sorted(_ALLOWED_KEY_SHIFTS)),
        default=0,
        type=int,
    )
    parser.add_argument("--job-dir", required=True, type=Path)
    parser.add_argument("--ffmpeg", required=True, type=Path)
    parser.add_argument("--ffprobe", required=True, type=Path)
    parser.add_argument("--demucs-site", required=True, type=Path)
    parser.add_argument("--torch-home", required=True, type=Path)
    parser.add_argument("--svc-root", required=True, type=Path)
    parser.add_argument("--svc-model-root", required=True, type=Path)
    parser.add_argument("--job-token", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run one complete, bounded local cover job and return a process status."""

    arguments = _parser().parse_args(argv)
    try:
        if _JOB_TOKEN_PATTERN.fullmatch(arguments.job_token) is None:
            _fail("Song-cover job identity is invalid.")
        _validate_source_arguments(arguments)
        _require_asset(
            Path(sys.executable),
            label="Python runtime",
            expected_bytes=_EXPECTED_PYTHON_BYTES,
            expected_sha256=_EXPECTED_PYTHON_SHA256,
        )
        ffmpeg = _require_asset(
            arguments.ffmpeg,
            label="FFmpeg runtime",
            expected_bytes=_EXPECTED_FFMPEG_BYTES,
            expected_sha256=_EXPECTED_FFMPEG_SHA256,
        )
        ffprobe = _require_asset(
            arguments.ffprobe,
            label="FFprobe runtime",
            expected_bytes=_EXPECTED_FFPROBE_BYTES,
            expected_sha256=_EXPECTED_FFPROBE_SHA256,
        )
        demucs_site: Optional[Path] = None
        torch_home: Optional[Path] = None
        if arguments.source_mode == "song":
            demucs_site = _require_directory(
                arguments.demucs_site, "Song-separation package"
            )
            _remove_bytecode_caches(
                demucs_site,
                label="Song-separation package",
            )
            _require_singing_runtime(demucs_site)
            torch_home = _require_directory(
                arguments.torch_home,
                "Demucs model cache",
            )
            _require_asset(
                torch_home / "hub" / "checkpoints" / "955717e8-8726e21a.th",
                label="Demucs separation model",
                expected_bytes=_EXPECTED_DEMUCS_MODEL_BYTES,
                expected_sha256=_EXPECTED_DEMUCS_MODEL_SHA256,
            )
        svc_root = _require_directory(arguments.svc_root, "So-VITS-SVC runtime")
        _remove_bytecode_caches(svc_root, label="So-VITS-SVC runtime")
        _require_python_tree(
            svc_root,
            label="So-VITS-SVC Python runtime",
            expected_files=_EXPECTED_SVC_PYTHON_FILES,
            expected_bytes=_EXPECTED_SVC_PYTHON_BYTES,
            expected_sha256=_EXPECTED_SVC_PYTHON_SHA256,
        )
        _require_asset(
            svc_root / "pretrain" / "checkpoint_best_legacy_500.pt",
            label="ContentVec model",
            expected_bytes=_EXPECTED_CONTENTVEC_BYTES,
            expected_sha256=_EXPECTED_CONTENTVEC_SHA256,
        )
        _require_asset(
            svc_root / "pretrain" / "fcpe.pt",
            label="FCPE pitch model",
            expected_bytes=_EXPECTED_FCPE_BYTES,
            expected_sha256=_EXPECTED_FCPE_SHA256,
        )
        svc_model_root = _require_directory(
            arguments.svc_model_root, "Elysia singing model"
        )
        svc_model = _require_asset(
            svc_model_root / "G_166400.pth",
            label="Elysia singing checkpoint",
            expected_bytes=_EXPECTED_SVC_MODEL_BYTES,
            expected_sha256=_EXPECTED_SVC_MODEL_SHA256,
        )
        svc_config = _require_asset(
            svc_model_root / "G_166400.json",
            label="Elysia singing configuration",
            expected_bytes=_EXPECTED_SVC_CONFIG_BYTES,
            expected_sha256=_EXPECTED_SVC_CONFIG_SHA256,
        )
        job_dir = Path(os.path.abspath(str(arguments.job_dir)))
        if job_dir.name != arguments.job_token:
            _fail("Song-cover job directory is invalid.")
        _require_directory(job_dir.parent, "Song-cover job parent")
        try:
            os.mkdir(job_dir)
        except OSError as error:
            raise _SongCoverFailure(
                "Song-cover job directory could not be created."
            ) from error
        job_dir = _require_directory(job_dir, "Song-cover job directory")
        _emit("validating", 5, "Reading the selected audio")
        scratch_trees: list[tuple[Path, str]] = []
        scratch_files: list[tuple[Path, str]] = []
        if arguments.source_mode == "song":
            source, duration = _validate_source(arguments.input, ffprobe)
            target_samples = _target_samples(duration)
            decoded_song = job_dir / "source.wav"
            _decode_song(ffmpeg, source, duration, decoded_song)
            scratch_files.append((decoded_song, "Decoded-song scratch"))
            _emit("separating", 15, "Preparing vocals and accompaniment")
            if demucs_site is None or torch_home is None:
                _fail("The song-separation runtime is unavailable.")
            separation_root = job_dir / "separated"
            vocals, accompaniment = _separate_vocals(
                decoded_song,
                separation_root,
                demucs_site,
                torch_home,
            )
            scratch_trees.append((separation_root, "Separated-audio scratch"))
        else:
            vocal_source, vocal_duration = _validate_source(
                arguments.vocal_input,
                ffprobe,
            )
            accompaniment_source, accompaniment_duration = _validate_source(
                arguments.accompaniment_input,
                ffprobe,
            )
            # A shorter stem is padded at the tail. This preserves the shared
            # zero timestamp without guessing at user-owned leading alignment.
            duration = max(vocal_duration, accompaniment_duration)
            target_samples = _target_samples(duration)
            _emit("separating", 15, "Preparing vocals and accompaniment")
            provided_root = _require_or_create_directory(
                job_dir / "provided-stems",
                label="Provided-stem scratch",
            )
            scratch_trees.append((provided_root, "Provided-stem scratch"))
            decoded_vocals = provided_root / "vocals.wav"
            decoded_accompaniment = provided_root / "accompaniment.wav"
            _decode_song(ffmpeg, vocal_source, duration, decoded_vocals)
            _decode_song(
                ffmpeg,
                accompaniment_source,
                duration,
                decoded_accompaniment,
            )
            vocals = decoded_vocals
            accompaniment = decoded_accompaniment
        if arguments.key_shift_semitones != 0:
            shifted_accompaniment = job_dir / "accompaniment-key-shifted.wav"
            # Keep the original vocal untouched for FCPE and ContentVec. SVC
            # shifts its detected F0 below, while this matched, duration-safe
            # transform keeps the instrumental in the same musical key.
            _transpose_audio(
                ffmpeg,
                accompaniment,
                shifted_accompaniment,
                key_shift_semitones=arguments.key_shift_semitones,
                target_samples=target_samples,
            )
            scratch_files.append(
                (shifted_accompaniment, "Key-shifted accompaniment scratch")
            )
            accompaniment = shifted_accompaniment
        _release_gpu_cache()
        _emit("converting", 55, "Converting the vocal melody to Elysia")
        mono_vocals = job_dir / "source-vocals-mono.wav"
        _prepare_mono_vocals(
            ffmpeg,
            vocals,
            mono_vocals,
            target_samples=target_samples,
        )
        scratch_files.append((mono_vocals, "Prepared-vocal scratch"))
        consonant_layer = job_dir / "consonant-layer.wav"
        _create_consonant_layer(
            ffmpeg,
            mono_vocals,
            consonant_layer,
            target_samples=target_samples,
        )
        scratch_files.append((consonant_layer, "Consonant-layer scratch"))
        converted_vocals = job_dir / "elysia-vocals.wav"
        _convert_vocals(
            ffmpeg=ffmpeg,
            vocals=mono_vocals,
            output=converted_vocals,
            svc_root=svc_root,
            svc_model=svc_model,
            svc_config=svc_config,
            job_token=arguments.job_token.replace("-", ""),
            key_shift_semitones=arguments.key_shift_semitones,
        )
        scratch_files.append((converted_vocals, "Converted-vocal scratch"))
        _release_gpu_cache()
        _emit("mixing", 90, "Mixing Elysia with the accompaniment")
        wav_output = job_dir / "elysia-cover.wav"
        playback_output = job_dir / "elysia-cover.mp3"
        _mix_cover(
            ffmpeg,
            converted_vocals,
            consonant_layer,
            accompaniment,
            wav_output,
            playback_output,
            target_samples=target_samples,
        )
        # Intermediate PCM and separated stems are temporary implementation
        # details.  Keeping only the final lossless export and compact preview
        # bounds disk use without deleting the user's selected source song.
        for scratch_root, label in scratch_trees:
            try:
                os.lstat(scratch_root)
            except FileNotFoundError:
                continue
            _remove_safe_tree(scratch_root, label=label)
        for scratch_file, label in scratch_files:
            _unlink_safe_file(scratch_file, label=label)
        _emit("complete", 100, f"{duration:.3f}")
        return 0
    except _SongCoverFailure as error:
        print(str(error), file=sys.stderr, flush=True)
        return 2
    except Exception:
        # Unexpected tracebacks stay in Main diagnostics.  The renderer receives
        # only a fixed failure supplied by the owning job manager.
        import traceback

        traceback.print_exc(file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
