"""Run the private SoulX-Singer Mandarin SVS pipeline inside WSL.

The worker deliberately exposes one argument only: a managed job directory.
That directory must live below the current Linux user's private Elysia job
root and use a UUIDv4 name.  Fixed filenames provide the separated target
vocal and synchronized lyrics; model, prompt, interpreter, and vendor-source
paths are never accepted from a caller.  This keeps Electron-selected paths,
commands, and model overrides outside the high-trust singing runtime.

Preprocessing and synthesis run in separate pinned Python environments.  Each
stage owns a new Linux session recorded below the UUID job.  Timeout and
cleanup paths terminate only that authenticated session, wait until every
member has disappeared, and only then allow private files to be removed.  Both
children receive an offline, proxy-free environment, and their output is
discarded so vendor diagnostics cannot leak private paths through the
structured progress channel.  The resulting vocal remains unmixed; the
existing reviewed FFmpeg layer is responsible for accompaniment and mastering.

The private WSL compute stages share a 90-minute deadline: preprocessing may
use at most 35 minutes and inference at most 55 minutes, with earlier runtime
verification reducing what remains.  This stays below Electron's two-hour
whole-cover deadline so native separation, mixing, verified cleanup, and
process-start overhead retain a separate safety margin.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import importlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import signal
import stat
import struct
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Iterator, Mapping, NoReturn, Sequence

if TYPE_CHECKING:
    from scripts.song_svs_quality import PromptRecord

_EVENT_PREFIX = "ELYSIA_SONG_SVS "
_EXPECTED_SOURCE_REVISION = "81aeb3ae772c70093c3de74dc23c92d983801ae4"
_JOB_TOKEN_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_MAX_VOCAL_BYTES = 256 * 1024 * 1024
_MAX_LYRICS_BYTES = 1024 * 1024
_MAX_METADATA_BYTES = 32 * 1024 * 1024
_MIN_DURATION_SECONDS = 1.0
_MAX_DURATION_SECONDS = 12 * 60.0
_TOTAL_RUNTIME_TIMEOUT_SECONDS = 90 * 60
_PREPROCESS_TIMEOUT_SECONDS = 35 * 60
_INFERENCE_TIMEOUT_SECONDS = 55 * 60
_STAGE_TERMINATE_GRACE_SECONDS = 5.0
_STAGE_KILL_GRACE_SECONDS = 5.0
_STAGE_POLL_SECONDS = 0.05
_STAGE_AUTHORIZATION_TIMEOUT_SECONDS = 5.0
_SIGTERM = int(getattr(signal, "SIGTERM", 15))
_SIGKILL = int(getattr(signal, "SIGKILL", 9))
_ALIGNMENT_EXIT_CODE = 21
_PREPROCESS_EXIT_CODE = 22
_INFERENCE_EXIT_CODE = 23
_OUTPUT_NAME = "generated_vocal.wav"
_WORK_DIRECTORY_NAME = ".svs-work"
_INTERNAL_JOB_TOKEN_ENV = "ELYSIA_SVS_INTERNAL_JOB_TOKEN"
_INTERNAL_STAGE_ID_ENV = "ELYSIA_SVS_INTERNAL_STAGE_ID"
_STAGE_LEASE_NAME = ".active-stage.json"
_STAGE_LEASE_TEMP_NAME = ".active-stage.json.tmp"
_STAGE_CONTROL_LOCK_NAME = ".stage-control.lock"
_CANCEL_REQUEST_NAME = ".cancel-requested"
_STAGE_LEASE_VERSION = 1
_MAX_STAGE_LEASE_BYTES = 2 * 1024
_MAX_PROC_ENVIRONMENT_BYTES = 256 * 1024
_STAGE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_STAGE_NAMES = frozenset({"preprocess", "inference"})
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PROMPT_BANK_SCHEMA_VERSION = 1
_PROMPT_BANK_ID = "elysia-bank-v1"
_PROMPT_BANK_PRIVACY = "private-local-only"
_PROMPT_BANK_ROOT = PurePosixPath("prompts/elysia-bank-v1")
_MAX_PROMPT_BANK_MANIFEST_BYTES = 1024 * 1024
_MAX_PROMPT_BANK_ENTRIES = 64
_MAX_PROMPT_BANK_PHONEMES = 4096
_MAX_PROMPT_BANK_PHONEME_CHARACTERS = 64
_PROMPT_BANK_ROLES = frozenset({"anchor", "emotion", "corpus"})
_PROMPT_BANK_STYLES = frozenset({"calm", "neutral", "expressive", "emphatic"})
_PROMPT_BANK_EMOTIONS = frozenset(
    {
        "neutral",
        "happy",
        "sad",
        "caring",
        "moved",
        "playful",
        "affectionate",
        "teasing",
        "serious",
        "surprised",
    }
)
_PROMPT_BANK_ENTRY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PROMPT_BANK_ASSET_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_PROMPT_BANK_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "bank_id",
        "privacy",
        "builder_sha256",
        "source_manifest_sha256",
        "entries",
    }
)
_PROMPT_BANK_ENTRY_FIELDS = frozenset(
    {
        "id",
        "role",
        "selection_rank",
        "style_proxy",
        "emotion",
        "audio",
        "metadata",
        "profile",
        "validation",
    }
)
_PROMPT_BANK_ASSET_FIELDS = frozenset({"path", "bytes", "sha256"})
_PROMPT_BANK_PROFILE_FIELDS = frozenset(
    {
        "duration_seconds",
        "note_median",
        "note_p10",
        "note_p90",
        "note_span",
        "syllables_per_second",
        "phonemes",
    }
)
_PROMPT_BANK_VALIDATION_FIELDS = frozenset(
    {
        "source_role",
        "transcript_sha256",
        "alignment_exact_match_ratio",
        "alignment_cost_ratio",
        "metadata_segments",
    }
)


@dataclass(frozen=True, slots=True)
class _AssetSpec:
    """Describe one immutable private runtime asset and its reviewed digest."""

    relative_path: str
    expected_bytes: int
    expected_sha256: str


@dataclass(frozen=True, slots=True)
class _PromptProfile:
    """Hold bounded acoustic and phoneme features for prompt selection."""

    duration_seconds: float
    note_median: float
    note_p10: float
    note_p90: float
    note_span: float
    syllables_per_second: float
    phonemes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PromptValidation:
    """Hold reviewed provenance and alignment evidence for one prompt."""

    source_role: str
    transcript_sha256: str
    alignment_exact_match_ratio: float
    alignment_cost_ratio: float
    metadata_segments: int


@dataclass(frozen=True, slots=True)
class _PromptBankEntry:
    """Describe one hash-pinned private prompt and its closed selection labels."""

    entry_id: str
    role: str
    selection_rank: int
    style_proxy: str
    emotion: str | None
    audio: _AssetSpec
    metadata: _AssetSpec
    profile: _PromptProfile
    validation: _PromptValidation


@dataclass(frozen=True, slots=True)
class _PromptBankManifest:
    """Represent one canonical private prompt-bank manifest."""

    schema_version: int
    bank_id: str
    privacy: str
    builder_sha256: str
    source_manifest_sha256: str
    entries: tuple[_PromptBankEntry, ...]


@dataclass(frozen=True, slots=True)
class _StageLease:
    """Identify one exact Linux stage session owned by a UUID-scoped job."""

    version: int
    job_token: str
    stage: str
    stage_id: str
    pid: int
    process_group_id: int
    session_id: int
    start_time_ticks: int


_PROMPT_BANK_MANIFEST_ASSET = _AssetSpec(
    "prompts/elysia-bank-v1/manifest.json",
    62_399,
    "8a5a4abd41054dce40e91996260600e3906df0f6d1e88a8aaf0e2b70efee825e",
)


_CORE_RUNTIME_ASSETS = (
    _AssetSpec(
        "python/cpython-3.10.22-linux-x86_64-gnu/bin/python3.10",
        18_848_968,
        "fc1cff69ed88fb77d262cd867a6032a7c270c20418e05b4728b9afaefc63a6a3",
    ),
    _AssetSpec(
        "prep-env/lib/python3.10/site-packages/imageio_ffmpeg/binaries/"
        "ffmpeg-linux-x86_64-v7.0.2",
        79_826_272,
        "e7e7fb30477f717e6f55f9180a70386c62677ef8a4d4d1a5d948f4098aa3eb99",
    ),
    _AssetSpec(
        "models/SoulX-Singer/model.pt",
        2_818_092_278,
        "447eaf41f91a6b6659d55e9ec3c9b809221724fb8592aebaec35a23751a5b500",
    ),
    _AssetSpec(
        "models/SoulX-Singer-Preprocess/rmvpe/rmvpe.pt",
        181_184_272,
        "6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193",
    ),
    _AssetSpec(
        "models/SoulX-Singer-Preprocess/rosvot/rmvpe/model.pt",
        368_492_925,
        "19dc1809cf4cdb0a18db93441816bc327e14e5644b72eeaae5220560c6736fe2",
    ),
    _AssetSpec(
        "models/SoulX-Singer-Preprocess/rosvot/rosvot/config.yaml",
        3_189,
        "6bccb2becf6c69dc7b60ecf4b4253854a70fda9b30d63838cd300b18da74abb4",
    ),
    _AssetSpec(
        "models/SoulX-Singer-Preprocess/rosvot/rosvot/model.pt",
        144_674_420,
        "7501fb5f913d971c2f51bcb3063b930027b03206581820a4d2bfdc394c9c3fcb",
    ),
    _AssetSpec(
        "models/SoulX-Singer-Preprocess/rosvot/rwbd/config.yaml",
        3_451,
        "3bb41f1d9eaa85aa1b3e5b6d94fff4ab4affb39719028ab69b4505974b9a1bc7",
    ),
    _AssetSpec(
        "models/SoulX-Singer-Preprocess/rosvot/rwbd/model.pt",
        119_897_457,
        "0bc2d42a6d4b7a05436deb937e2deda1c12de49e5687cfda0bdf6a430120dcd2",
    ),
    _AssetSpec(
        "models/SoulX-Singer-Preprocess/"
        "speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/"
        "am.mvn",
        11_203,
        "29b3c740a2c0cfc6b308126d31d7f265fa2be74f3bb095cd2f143ea970896ae5",
    ),
    _AssetSpec(
        "models/SoulX-Singer-Preprocess/"
        "speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/"
        "config.yaml",
        3_477,
        "6602efa95e4c7248e1d1030f7ff454a3c9af0f57c335ed87f35260cd7faec35d",
    ),
    _AssetSpec(
        "models/SoulX-Singer-Preprocess/"
        "speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/"
        "configuration.json",
        478,
        "1acac324430b5a4680ef5ee2947575443ab2039a92c8a0551665f6bc9a606b41",
    ),
    _AssetSpec(
        "models/SoulX-Singer-Preprocess/"
        "speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/"
        "model.pt",
        989_763_045,
        "3d491689244ec5dfbf9170ef3827c358aa10f1f20e42a7c59e15e688647946d1",
    ),
    _AssetSpec(
        "models/SoulX-Singer-Preprocess/"
        "speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/"
        "seg_dict",
        8_287_834,
        "59a2ef803a3f1648ad03a2e1480db1c1ee0c0d7dc4ef4dbd16cea33944329022",
    ),
    _AssetSpec(
        "models/SoulX-Singer-Preprocess/"
        "speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/"
        "tokens.json",
        93_676,
        "2b20c2b12572d682afff84ce1c8d560f67b8b32a4c1f21567411d141ed352127",
    ),
)

_LOCALIZATION_ASSETS = (
    _AssetSpec(
        "prep-env/lib/python3.10/site-packages/opencc-1.4.2.dist-info/METADATA",
        30_731,
        "69c61c4491bd1d472686c2487751657696f2779db93e8aa93cc26dc9cadf5b7f",
    ),
    _AssetSpec(
        "prep-env/lib/python3.10/site-packages/opencc/__init__.py",
        3_398,
        "02b08c0dc6994f9713055bb196254bde7e341fdba6f4ba2586990da18cd50645",
    ),
    _AssetSpec(
        "prep-env/lib/python3.10/site-packages/opencc/clib/"
        "opencc_clib.cpython-310-x86_64-linux-gnu.so",
        1_044_424,
        "77c34ffaf53bb339af565c21665f427d2e7ca324ef5f623bb24fac4e1bf48112",
    ),
    _AssetSpec(
        "prep-env/lib/python3.10/site-packages/opencc/clib/share/opencc/t2s.json",
        589,
        "96fe5cc374a80ccc49e3370006cce3aefe4af955868ae0b14fb3079ec695be4f",
    ),
    _AssetSpec(
        "prep-env/lib/python3.10/site-packages/opencc/clib/share/opencc/"
        "CJK_Compatibility_Ideographs.ocd2",
        13_843,
        "4b1faa6649012f524068ec18c0fb520ead343c11cbe0a8e4c8853ca61369d666",
    ),
    _AssetSpec(
        "prep-env/lib/python3.10/site-packages/opencc/clib/share/opencc/"
        "TSPhrases.ocd2",
        13_496,
        "2b722237cc00a4ac985790deb6a68c99f34de10e980a0029ac82c72b0f7ae22b",
    ),
    _AssetSpec(
        "prep-env/lib/python3.10/site-packages/opencc/clib/share/opencc/"
        "TSCharactersExt.ocd2",
        14_362,
        "e2b0286801dcd1d26119d19ba8b8a35cde021ec173468255d117c4c0951b2906",
    ),
    _AssetSpec(
        "prep-env/lib/python3.10/site-packages/opencc/clib/share/opencc/"
        "TSCharacters.ocd2",
        52_410,
        "7a994de7900707925fac9c68728a6bb9cf3059870426f661cf31b601efbf5f2f",
    ),
)

_RUNTIME_ASSETS = _CORE_RUNTIME_ASSETS + _LOCALIZATION_ASSETS


class _SvsRuntimeFailure(RuntimeError):
    """Carry one stable error code and a path-free user-facing explanation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> NoReturn:
    """Stop the worker with one bounded renderer-safe failure."""

    raise _SvsRuntimeFailure(code, message)


def _emit(
    stage: str,
    progress_percent: int,
    message: str,
    *,
    error_code: str | None = None,
) -> None:
    """Emit one closed, bounded JSON progress event for the native parent."""

    payload: dict[str, object] = {
        "message": message[:240],
        "progressPercent": max(0, min(100, int(progress_percent))),
        "stage": stage,
    }
    if error_code is not None:
        payload["errorCode"] = error_code[:64]
    print(
        f"{_EVENT_PREFIX}{json.dumps(payload, ensure_ascii=True, separators=(',', ':'))}",
        flush=True,
    )


def _runtime_root() -> Path:
    """Return the current Linux account's fixed private SoulX runtime root."""

    if os.name != "posix":
        _fail("unsupported_runtime", "SoulX singing requires the private WSL runtime.")
    try:
        pwd_module = importlib.import_module("pwd")
        home = Path(str(pwd_module.getpwuid(_current_uid()).pw_dir))
    except (ImportError, KeyError, OSError, TypeError, ValueError) as error:
        raise _SvsRuntimeFailure(
            "unsupported_runtime",
            "The private WSL account could not be resolved.",
        ) from error
    return home / ".local" / "share" / "elysia-ai" / "soulx"


def _current_uid() -> int:
    """Return the POSIX uid without exposing a Windows-only type-check error."""

    getter = getattr(os, "getuid", None)
    if not callable(getter):
        _fail("unsupported_runtime", "SoulX singing requires the private WSL runtime.")
    try:
        return int(getter())
    except (OSError, TypeError, ValueError) as error:
        raise _SvsRuntimeFailure(
            "unsupported_runtime",
            "The private WSL account could not be resolved.",
        ) from error


def _project_root() -> Path:
    """Return the repository root containing this reviewed worker module."""

    return Path(__file__).resolve().parents[1]


def _is_reparse(info: os.stat_result) -> bool:
    """Recognize Windows reparse points when tests exercise paths on Windows."""

    return bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _require_safe_path(
    path: Path,
    *,
    label: str,
    require_directory: bool,
    require_single_link: bool = False,
) -> Path:
    """Authenticate every existing component without following redirections."""

    candidate = Path(os.path.abspath(str(path)))
    try:
        current = Path(candidate.anchor)
        parts = candidate.parts[1:] if candidate.anchor else candidate.parts
        if not parts:
            raise OSError
        for index, part in enumerate(parts):
            current = current / part
            info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                raise OSError
            final = index == len(parts) - 1
            if not final:
                if not stat.S_ISDIR(info.st_mode):
                    raise OSError
                continue
            if require_directory:
                if not stat.S_ISDIR(info.st_mode):
                    raise OSError
            elif not stat.S_ISREG(info.st_mode):
                raise OSError
            if require_single_link and info.st_nlink != 1:
                raise OSError
    except (OSError, RuntimeError, ValueError) as error:
        raise _SvsRuntimeFailure(
            "unsafe_path",
            f"{label} is unavailable or unsafe.",
        ) from error
    return candidate


def _require_directory(path: Path, label: str) -> Path:
    """Require one real directory whose parent chain contains no links."""

    return _require_safe_path(path, label=label, require_directory=True)


def _require_file(
    path: Path,
    label: str,
    *,
    require_single_link: bool = False,
) -> Path:
    """Require one regular file without following a symlink or device node."""

    return _require_safe_path(
        path,
        label=label,
        require_directory=False,
        require_single_link=require_single_link,
    )


def _trusted_runtime_metadata_matches(
    info: os.stat_result,
    *,
    expected_uid: int,
) -> bool:
    """Return whether another local account cannot replace or edit an entry."""

    return bool(
        (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
        and info.st_uid in {0, expected_uid}
        and stat.S_IMODE(info.st_mode) & 0o022 == 0
    )


def _require_trusted_runtime_path(path: Path, *, label: str) -> Path:
    """Require root/current ownership and non-writable ancestry on POSIX.

    Hashing a runtime file is not sufficient if another local account can
    replace an ancestor or rewrite the file between verification and exec.
    System-owned ancestors are accepted, but every component must be a real
    directory or file and must deny group/other writes.
    """

    candidate = Path(os.path.abspath(str(path)))
    if os.name != "posix":
        return candidate
    try:
        expected_uid = _current_uid()
        current = Path(candidate.anchor)
        components = [current]
        for part in candidate.parts[1:]:
            current = current / part
            components.append(current)
        for component in components:
            info = os.lstat(component)
            if (
                stat.S_ISLNK(info.st_mode)
                or _is_reparse(info)
                or not _trusted_runtime_metadata_matches(
                    info,
                    expected_uid=expected_uid,
                )
            ):
                raise OSError
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise _SvsRuntimeFailure(
            "runtime_integrity_failed",
            f"{label} is not protected from other local accounts.",
        ) from error
    return candidate


def _verify_trusted_runtime_tree(runtime_root: Path) -> None:
    """Verify metadata trust for every executable or importable runtime entry.

    The UUID job tree is deliberately excluded because it contains mutable
    per-request inputs.  Static Python, venv, model, and vendor-source trees are
    walked without following links.  Internal links are accepted only when
    their resolved target remains under the trusted runtime and independently
    satisfies the same ancestry policy.  Installer ``.lock`` sentinels are
    inert data and are the only group-writable entries intentionally ignored.
    """

    trusted_root = _require_trusted_runtime_path(
        runtime_root,
        label="SoulX runtime",
    )
    if os.name != "posix":
        return
    roots = (
        trusted_root / "python",
        trusted_root / "prep-env",
        trusted_root / "infer-env",
        trusted_root / "models",
        trusted_root / "source",
    )
    ignored_locks = {root / ".lock" for root in roots}
    expected_uid = _current_uid()
    pending = [
        _require_trusted_runtime_path(root, label="SoulX runtime tree")
        for root in roots
    ]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            raise _SvsRuntimeFailure(
                "runtime_integrity_failed",
                "The private SoulX runtime tree could not be inspected.",
            ) from error
        for entry in entries:
            path = Path(entry.path)
            try:
                info = os.lstat(path)
                if path in ignored_locks:
                    if not stat.S_ISREG(info.st_mode):
                        raise OSError
                    continue
                if stat.S_ISLNK(info.st_mode):
                    target = path.resolve(strict=True)
                    target.relative_to(trusted_root)
                    _require_trusted_runtime_path(
                        target,
                        label="SoulX runtime link target",
                    )
                    continue
                if _is_reparse(info) or not _trusted_runtime_metadata_matches(
                    info,
                    expected_uid=expected_uid,
                ):
                    raise OSError
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                raise _SvsRuntimeFailure(
                    "runtime_integrity_failed",
                    "The private SoulX runtime tree is writable by another account.",
                ) from error


def _private_prompt_metadata_matches(
    info: os.stat_result,
    *,
    expected_uid: int,
    require_directory: bool,
) -> bool:
    """Return whether stat metadata satisfies the closed prompt privacy policy."""

    expected_mode = 0o700 if require_directory else 0o600
    expected_kind = (
        stat.S_ISDIR(info.st_mode)
        if require_directory
        else stat.S_ISREG(info.st_mode)
    )
    return bool(
        expected_kind
        and info.st_uid == expected_uid
        and stat.S_IMODE(info.st_mode) == expected_mode
    )


def _require_private_prompt_permissions(
    path: Path,
    *,
    label: str,
    require_directory: bool,
) -> Path:
    """Require owner-only POSIX permissions for one private prompt path.

    Windows unit tests still exercise the structural path contract, while the
    production WSL boundary additionally requires the current UID and exact
    ``0700`` directories or ``0600`` files.  Failing closed instead of changing
    permissions keeps an unexpected deployment visible to its owner.
    """

    if os.name != "posix":
        return path
    try:
        info = os.lstat(path)
        if not _private_prompt_metadata_matches(
            info,
            expected_uid=_current_uid(),
            require_directory=require_directory,
        ):
            raise OSError
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise _SvsRuntimeFailure(
            "unsafe_path",
            f"{label} does not have private owner-only permissions.",
        ) from error
    return path


def _sha256(path: Path) -> str:
    """Hash one local asset incrementally to avoid multi-gigabyte allocations."""

    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise _SvsRuntimeFailure(
            "runtime_unavailable",
            "A private SoulX runtime asset could not be verified.",
        ) from error
    return digest.hexdigest()


def _require_asset(
    runtime_root: Path,
    spec: _AssetSpec,
    *,
    require_single_link: bool = False,
    require_private_prompt_permissions: bool = False,
) -> Path:
    """Verify one fixed-size runtime asset against its reviewed SHA256."""

    candidate = _require_file(
        runtime_root / spec.relative_path,
        "SoulX runtime asset",
        require_single_link=require_single_link,
    )
    if require_private_prompt_permissions:
        _require_private_prompt_permissions(
            candidate,
            label="Private SoulX prompt asset",
            require_directory=False,
        )
    try:
        size_matches = candidate.stat().st_size == spec.expected_bytes
    except OSError as error:
        raise _SvsRuntimeFailure(
            "runtime_unavailable",
            "A private SoulX runtime asset could not be verified.",
        ) from error
    if not size_matches or _sha256(candidate) != spec.expected_sha256:
        _fail(
            "runtime_integrity_failed",
            "A private SoulX runtime asset does not match the reviewed version.",
        )
    return candidate


def _invalid_prompt_bank() -> NoReturn:
    """Reject an unreviewed prompt-bank shape without exposing private details."""

    _fail(
        "runtime_integrity_failed",
        "The private SoulX prompt bank manifest is invalid.",
    )


def _reject_duplicate_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """Build a JSON object while rejecting ambiguous duplicate field names."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> NoReturn:
    """Reject non-standard NaN and infinity tokens accepted by Python JSON."""

    raise ValueError(f"non-finite JSON constant: {value}")


def _require_manifest_object(
    value: object,
    expected_fields: frozenset[str],
) -> dict[str, object]:
    """Require one closed JSON object with exactly the reviewed fields."""

    if not isinstance(value, dict) or frozenset(value) != expected_fields:
        _invalid_prompt_bank()
    return value


def _require_manifest_string(value: object, allowed: frozenset[str]) -> str:
    """Require one string selected from a closed manifest vocabulary."""

    if not isinstance(value, str) or value not in allowed:
        _invalid_prompt_bank()
    return value


def _require_manifest_integer(value: object, minimum: int, maximum: int) -> int:
    """Require a bounded JSON integer while excluding booleans."""

    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        _invalid_prompt_bank()
    return value


def _require_manifest_number(
    value: object,
    minimum: float,
    maximum: float,
) -> float:
    """Require one finite bounded JSON number while excluding booleans."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _invalid_prompt_bank()
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        _invalid_prompt_bank()
    return result


def _parse_prompt_bank_asset(value: object, *, kind: str) -> _AssetSpec:
    """Parse one hash-pinned asset below the bank's fixed private directory."""

    item = _require_manifest_object(value, _PROMPT_BANK_ASSET_FIELDS)
    path_value = item["path"]
    expected_suffix = ".wav" if kind == "audio" else ".json"
    if not isinstance(path_value, str):
        _invalid_prompt_bank()
    relative = PurePosixPath(path_value)
    # Exactly two ASCII-safe components keep references inside the reviewed
    # audio/metadata directories and make Windows and WSL resolve them alike.
    if (
        path_value != relative.as_posix()
        or relative.is_absolute()
        or relative.parts != (kind, relative.name)
        or relative.suffix != expected_suffix
        or not _PROMPT_BANK_ASSET_NAME_PATTERN.fullmatch(relative.name)
    ):
        _invalid_prompt_bank()
    maximum_bytes = _MAX_VOCAL_BYTES if kind == "audio" else _MAX_METADATA_BYTES
    expected_bytes = _require_manifest_integer(item["bytes"], 1, maximum_bytes)
    expected_sha256 = item["sha256"]
    if (
        not isinstance(expected_sha256, str)
        or not _SHA256_PATTERN.fullmatch(expected_sha256)
    ):
        _invalid_prompt_bank()
    return _AssetSpec(
        str(_PROMPT_BANK_ROOT / relative),
        expected_bytes,
        expected_sha256,
    )


def _parse_prompt_profile(value: object) -> _PromptProfile:
    """Parse bounded prompt-selection measurements from one manifest entry."""

    item = _require_manifest_object(value, _PROMPT_BANK_PROFILE_FIELDS)
    duration_seconds = _require_manifest_number(item["duration_seconds"], 3.0, 12.0)
    note_median = _require_manifest_number(item["note_median"], 1.0, 127.0)
    note_p10 = _require_manifest_number(item["note_p10"], 1.0, 127.0)
    note_p90 = _require_manifest_number(item["note_p90"], 1.0, 127.0)
    if note_p10 > note_median or note_median > note_p90:
        _invalid_prompt_bank()
    raw_phonemes = item["phonemes"]
    if (
        not isinstance(raw_phonemes, list)
        or not 1 <= len(raw_phonemes) <= _MAX_PROMPT_BANK_PHONEMES
    ):
        _invalid_prompt_bank()
    phonemes: list[str] = []
    seen_phonemes: set[str] = set()
    for phoneme in raw_phonemes:
        if (
            not isinstance(phoneme, str)
            or not phoneme.strip()
            or phoneme != phoneme.strip()
            or len(phoneme) > _MAX_PROMPT_BANK_PHONEME_CHARACTERS
            or phoneme in seen_phonemes
        ):
            _invalid_prompt_bank()
        phonemes.append(phoneme)
        seen_phonemes.add(phoneme)
    return _PromptProfile(
        duration_seconds=duration_seconds,
        note_median=note_median,
        note_p10=note_p10,
        note_p90=note_p90,
        note_span=_require_manifest_number(item["note_span"], 0.0, 127.0),
        syllables_per_second=_require_manifest_number(
            item["syllables_per_second"],
            1.2,
            8.5,
        ),
        phonemes=tuple(phonemes),
    )


def _parse_prompt_validation(value: object) -> _PromptValidation:
    """Parse the fixed provenance and single-segment alignment evidence."""

    item = _require_manifest_object(value, _PROMPT_BANK_VALIDATION_FIELDS)
    transcript_sha256 = item["transcript_sha256"]
    if (
        not isinstance(transcript_sha256, str)
        or not _SHA256_PATTERN.fullmatch(transcript_sha256)
    ):
        _invalid_prompt_bank()
    return _PromptValidation(
        source_role=_require_manifest_string(item["source_role"], _PROMPT_BANK_ROLES),
        transcript_sha256=transcript_sha256,
        alignment_exact_match_ratio=_require_manifest_number(
            item["alignment_exact_match_ratio"],
            0.0,
            1.0,
        ),
        alignment_cost_ratio=_require_manifest_number(
            item["alignment_cost_ratio"],
            0.0,
            1.0,
        ),
        metadata_segments=_require_manifest_integer(item["metadata_segments"], 1, 1),
    )


def _parse_prompt_bank_entry(value: object) -> _PromptBankEntry:
    """Parse one exact prompt-bank entry and its closed selection labels."""

    item = _require_manifest_object(value, _PROMPT_BANK_ENTRY_FIELDS)
    entry_id = item["id"]
    if (
        not isinstance(entry_id, str)
        or not _PROMPT_BANK_ENTRY_ID_PATTERN.fullmatch(entry_id)
    ):
        _invalid_prompt_bank()
    emotion = item["emotion"]
    if emotion is not None and (
        not isinstance(emotion, str) or emotion not in _PROMPT_BANK_EMOTIONS
    ):
        _invalid_prompt_bank()
    role = _require_manifest_string(item["role"], _PROMPT_BANK_ROLES)
    validation = _parse_prompt_validation(item["validation"])
    if validation.source_role != role:
        _invalid_prompt_bank()
    return _PromptBankEntry(
        entry_id=entry_id,
        role=role,
        selection_rank=_require_manifest_integer(
            item["selection_rank"],
            0,
            _MAX_PROMPT_BANK_ENTRIES,
        ),
        style_proxy=_require_manifest_string(item["style_proxy"], _PROMPT_BANK_STYLES),
        emotion=emotion,
        audio=_parse_prompt_bank_asset(item["audio"], kind="audio"),
        metadata=_parse_prompt_bank_asset(item["metadata"], kind="metadata"),
        profile=_parse_prompt_profile(item["profile"]),
        validation=validation,
    )


def _parse_prompt_bank_manifest(raw: bytes) -> _PromptBankManifest:
    """Parse one canonical, duplicate-free private prompt-bank manifest."""

    if not raw or len(raw) > _MAX_PROMPT_BANK_MANIFEST_BYTES:
        _invalid_prompt_bank()
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_object,
            parse_constant=_reject_json_constant,
        )
        canonical = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (UnicodeError, ValueError, TypeError, OverflowError, RecursionError) as error:
        raise _SvsRuntimeFailure(
            "runtime_integrity_failed",
            "The private SoulX prompt bank manifest is invalid.",
        ) from error
    if raw != canonical:
        _invalid_prompt_bank()

    item = _require_manifest_object(document, _PROMPT_BANK_TOP_LEVEL_FIELDS)
    schema_version = _require_manifest_integer(
        item["schema_version"],
        _PROMPT_BANK_SCHEMA_VERSION,
        _PROMPT_BANK_SCHEMA_VERSION,
    )
    if item["bank_id"] != _PROMPT_BANK_ID or item["privacy"] != _PROMPT_BANK_PRIVACY:
        _invalid_prompt_bank()
    builder_sha256 = item["builder_sha256"]
    source_manifest_sha256 = item["source_manifest_sha256"]
    if (
        not isinstance(builder_sha256, str)
        or not _SHA256_PATTERN.fullmatch(builder_sha256)
        or not isinstance(source_manifest_sha256, str)
        or not _SHA256_PATTERN.fullmatch(source_manifest_sha256)
    ):
        _invalid_prompt_bank()
    raw_entries = item["entries"]
    if (
        not isinstance(raw_entries, list)
        or not 1 <= len(raw_entries) <= _MAX_PROMPT_BANK_ENTRIES
    ):
        _invalid_prompt_bank()
    entries = tuple(_parse_prompt_bank_entry(entry) for entry in raw_entries)
    entry_ids = [entry.entry_id for entry in entries]
    if len(entry_ids) != len(set(entry_ids)):
        _invalid_prompt_bank()
    return _PromptBankManifest(
        schema_version=schema_version,
        bank_id=_PROMPT_BANK_ID,
        privacy=_PROMPT_BANK_PRIVACY,
        builder_sha256=builder_sha256,
        source_manifest_sha256=source_manifest_sha256,
        entries=entries,
    )


def _verify_prompt_bank(runtime_root: Path) -> _PromptBankManifest:
    """Authenticate the canonical manifest and every private prompt it names."""

    for relative_path, label in (
        ("prompts", "Private SoulX prompt root"),
        ("prompts/elysia-bank-v1", "Private SoulX prompt bank"),
        ("prompts/elysia-bank-v1/audio", "Private SoulX prompt audio directory"),
        (
            "prompts/elysia-bank-v1/metadata",
            "Private SoulX prompt metadata directory",
        ),
    ):
        directory = _require_directory(runtime_root / relative_path, label)
        _require_private_prompt_permissions(
            directory,
            label=label,
            require_directory=True,
        )
    manifest_path = _require_asset(
        runtime_root,
        _PROMPT_BANK_MANIFEST_ASSET,
        require_single_link=True,
        require_private_prompt_permissions=True,
    )
    try:
        raw = manifest_path.read_bytes()
    except OSError as error:
        raise _SvsRuntimeFailure(
            "runtime_unavailable",
            "The private SoulX prompt bank manifest could not be read.",
        ) from error
    if (
        len(raw) != _PROMPT_BANK_MANIFEST_ASSET.expected_bytes
        or hashlib.sha256(raw).hexdigest()
        != _PROMPT_BANK_MANIFEST_ASSET.expected_sha256
    ):
        _fail(
            "runtime_integrity_failed",
            "The private SoulX prompt bank manifest does not match the reviewed version.",
        )
    manifest = _parse_prompt_bank_manifest(raw)
    for entry in manifest.entries:
        _require_asset(
            runtime_root,
            entry.audio,
            require_single_link=True,
            require_private_prompt_permissions=True,
        )
        _require_asset(
            runtime_root,
            entry.metadata,
            require_single_link=True,
            require_private_prompt_permissions=True,
        )
    return manifest


def _verify_runtime_assets(runtime_root: Path) -> _PromptBankManifest:
    """Authenticate every executable, model, tokenizer, and Elysia prompt used."""

    _require_directory(runtime_root, "SoulX runtime")
    _verify_trusted_runtime_tree(runtime_root)
    for spec in _RUNTIME_ASSETS:
        _require_asset(runtime_root, spec)
    prompt_bank = _verify_prompt_bank(runtime_root)

    expected_python = runtime_root / _CORE_RUNTIME_ASSETS[0].relative_path
    for relative_link in ("prep-env/bin/python", "infer-env/bin/python"):
        link = runtime_root / relative_link
        try:
            if not link.is_symlink() or link.resolve(strict=True) != expected_python:
                raise OSError
        except (OSError, RuntimeError) as error:
            raise _SvsRuntimeFailure(
                "runtime_integrity_failed",
                "A private SoulX interpreter link is invalid.",
            ) from error

    expected_ffmpeg = runtime_root / _CORE_RUNTIME_ASSETS[1].relative_path
    ffmpeg_link = runtime_root / "prep-env" / "bin" / "ffmpeg"
    try:
        if not ffmpeg_link.is_symlink() or ffmpeg_link.resolve(strict=True) != expected_ffmpeg:
            raise OSError
    except (OSError, RuntimeError) as error:
        raise _SvsRuntimeFailure(
            "runtime_integrity_failed",
            "The private audio decoder link is invalid.",
        ) from error
    return prompt_bank


def _verify_source_checkout(runtime_root: Path) -> Path:
    """Require the fixed SoulX revision and a clean local vendor checkout.

    The Git check is read-only and uses a fixed executable and arguments.  A
    clean tree matters because validating only ``HEAD`` would not detect a
    modified Python module loaded beneath that unchanged commit.
    """

    source_root = _require_directory(runtime_root / "source", "SoulX source")
    _require_trusted_runtime_path(source_root, label="SoulX source")
    head_path = _require_file(source_root / ".git" / "HEAD", "SoulX revision")
    try:
        head = head_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise _SvsRuntimeFailure(
            "runtime_integrity_failed",
            "The private SoulX revision could not be verified.",
        ) from error
    if head != _EXPECTED_SOURCE_REVISION:
        _fail(
            "runtime_integrity_failed",
            "The private SoulX source revision is not the reviewed version.",
        )
    try:
        completed = subprocess.run(
            [
                "/usr/bin/git",
                "-C",
                str(source_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise _SvsRuntimeFailure(
            "runtime_integrity_failed",
            "The private SoulX source could not be verified.",
        ) from error
    if completed.returncode != 0 or completed.stdout.strip():
        _fail(
            "runtime_integrity_failed",
            "The private SoulX source differs from the reviewed revision.",
        )

    model_links = {
        "SoulX-Singer": runtime_root / "models" / "SoulX-Singer",
        "SoulX-Singer-Preprocess": (
            runtime_root / "models" / "SoulX-Singer-Preprocess"
        ),
    }
    for name, expected_target in model_links.items():
        link = source_root / "pretrained_models" / name
        try:
            if not link.is_symlink() or link.resolve(strict=True) != expected_target:
                raise OSError
        except (OSError, RuntimeError) as error:
            raise _SvsRuntimeFailure(
                "runtime_integrity_failed",
                "A private SoulX model link is invalid.",
            ) from error
    return source_root


def _jobs_root(runtime_root: Path) -> Path:
    """Return the fixed parent for private UUID-scoped singing jobs."""

    return runtime_root / "jobs"


def _validate_job_root(job_root: Path, runtime_root: Path) -> tuple[Path, str]:
    """Require one direct UUIDv4 child of the managed private jobs directory."""

    managed_root = _require_directory(_jobs_root(runtime_root), "Singing job store")
    candidate = _require_directory(job_root, "Singing job")
    if candidate.parent != managed_root or _JOB_TOKEN_PATTERN.fullmatch(candidate.name) is None:
        _fail("invalid_job", "The singing job is not managed by Elysia.")
    if os.name == "posix":
        try:
            if candidate.stat().st_uid != _current_uid():
                raise OSError
        except OSError as error:
            raise _SvsRuntimeFailure(
                "invalid_job",
                "The singing job owner could not be verified.",
            ) from error
    return candidate, candidate.name


def _job_root_from_token(runtime_root: Path, token: str) -> Path:
    """Resolve an internal UUID token without accepting an arbitrary path."""

    if _JOB_TOKEN_PATTERN.fullmatch(token) is None:
        _fail("invalid_job", "The internal singing job token is invalid.")
    job_root, _token = _validate_job_root(_jobs_root(runtime_root) / token, runtime_root)
    return job_root


def _read_bounded_text(path: Path, *, label: str, maximum_bytes: int) -> str:
    """Decode one fixed lyrics file as strict bounded UTF-8 text."""

    candidate = _require_file(path, label, require_single_link=True)
    try:
        if candidate.stat().st_size > maximum_bytes:
            _fail("lyrics_too_large", f"{label} exceeds the supported size.")
        content = candidate.read_text(encoding="utf-8")
    except _SvsRuntimeFailure:
        raise
    except (OSError, UnicodeError) as error:
        raise _SvsRuntimeFailure(
            "invalid_lyrics",
            f"{label} is not valid UTF-8 text.",
        ) from error
    if "\x00" in content:
        _fail("invalid_lyrics", f"{label} contains unsupported control data.")
    return content


def _probe_wave(path: Path, *, label: str) -> float:
    """Read bounded RIFF/WAVE headers without invoking an external decoder.

    Already-separated vocals are required to be ordinary PCM or IEEE-float
    WAVE files.  Parsing chunks here keeps probing offline and accepts the
    float WAVE form commonly emitted by separation tools, which the standard
    library ``wave`` module does not consistently support.
    """

    candidate = _require_file(path, label, require_single_link=True)
    try:
        file_size = candidate.stat().st_size
        if not 44 <= file_size <= _MAX_VOCAL_BYTES:
            _fail("invalid_vocal", f"{label} has an unsupported size.")
        with candidate.open("rb") as source:
            header = source.read(12)
            if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
                _fail("invalid_vocal", f"{label} is not a supported WAVE file.")
            format_values: tuple[int, int, int, int, int, int] | None = None
            data_bytes: int | None = None
            cursor = 12
            while cursor + 8 <= file_size:
                source.seek(cursor)
                chunk_header = source.read(8)
                if len(chunk_header) != 8:
                    break
                chunk_id, chunk_size = struct.unpack("<4sI", chunk_header)
                data_start = cursor + 8
                data_end = data_start + chunk_size
                if data_end > file_size:
                    _fail("invalid_vocal", f"{label} contains a truncated WAVE chunk.")
                if chunk_id == b"fmt " and format_values is None:
                    source.seek(data_start)
                    raw_format = source.read(min(chunk_size, 40))
                    if len(raw_format) < 16:
                        _fail("invalid_vocal", f"{label} has an invalid WAVE format.")
                    format_values = struct.unpack("<HHIIHH", raw_format[:16])
                elif chunk_id == b"data" and data_bytes is None:
                    data_bytes = chunk_size
                cursor = data_end + (chunk_size & 1)
            if format_values is None or data_bytes is None:
                _fail("invalid_vocal", f"{label} is missing required WAVE chunks.")
    except _SvsRuntimeFailure:
        raise
    except (OSError, struct.error) as error:
        raise _SvsRuntimeFailure(
            "invalid_vocal",
            f"{label} could not be validated.",
        ) from error

    audio_format, channels, sample_rate, byte_rate, block_align, bits = format_values
    bytes_per_sample = bits // 8
    if (
        audio_format not in {1, 3}
        or channels not in {1, 2}
        or not 8_000 <= sample_rate <= 192_000
        or bits not in {16, 24, 32}
        or bytes_per_sample * channels != block_align
        or byte_rate != sample_rate * block_align
        or data_bytes <= 0
    ):
        _fail("invalid_vocal", f"{label} uses an unsupported WAVE format.")
    duration = data_bytes / float(byte_rate)
    if not _MIN_DURATION_SECONDS <= duration <= _MAX_DURATION_SECONDS:
        _fail("invalid_vocal", f"{label} has an unsupported duration.")
    return duration


def _iter_tree_bottom_up(root: Path) -> list[tuple[Path, bool]]:
    """Return a verified bottom-up work-tree listing without following links."""

    verified_root = _require_directory(root, "Singing work directory")
    entries: list[tuple[Path, bool]] = []
    pending = [verified_root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                children = list(iterator)
        except OSError as error:
            raise _SvsRuntimeFailure(
                "unsafe_path",
                "The singing work directory could not be inspected.",
            ) from error
        for child in children:
            try:
                # ``DirEntry.stat`` reports ``st_nlink == 0`` for ordinary
                # files on some Windows Python builds used by unit tests.
                # ``lstat`` preserves the no-follow boundary while returning
                # consistent link counts on both Windows and WSL.
                info = os.lstat(child.path)
            except OSError as error:
                raise _SvsRuntimeFailure(
                    "unsafe_path",
                    "The singing work directory could not be inspected.",
                ) from error
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                _fail("unsafe_path", "The singing work directory contains a link.")
            child_path = Path(child.path)
            if stat.S_ISDIR(info.st_mode):
                entries.append((child_path, True))
                pending.append(child_path)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                entries.append((child_path, False))
            else:
                _fail(
                    "unsafe_path",
                    "The singing work directory contains an unsupported file.",
                )
    return sorted(entries, key=lambda item: len(item[0].parts), reverse=True)


def _remove_work_tree(root: Path) -> None:
    """Delete only a previously authenticated private work directory."""

    if not root.exists():
        return
    for candidate, is_directory in _iter_tree_bottom_up(root):
        try:
            if is_directory:
                candidate.rmdir()
            else:
                candidate.unlink()
        except OSError as error:
            raise _SvsRuntimeFailure(
                "cleanup_failed",
                "The private singing workspace could not be cleared.",
            ) from error
    try:
        root.rmdir()
    except OSError as error:
        raise _SvsRuntimeFailure(
            "cleanup_failed",
            "The private singing workspace could not be cleared.",
        ) from error


def _prepare_work_tree(job_root: Path) -> Path:
    """Create a clean mode-0700 workspace below the authenticated job root."""

    work_root = job_root / _WORK_DIRECTORY_NAME
    if work_root.exists() or work_root.is_symlink():
        _remove_work_tree(work_root)
    try:
        work_root.mkdir(mode=0o700)
    except OSError as error:
        raise _SvsRuntimeFailure(
            "workspace_failed",
            "The private singing workspace could not be created.",
        ) from error
    return _require_directory(work_root, "Singing work directory")


def _clear_optional_output(path: Path) -> None:
    """Remove a prior fixed output only when it is a single-link regular file."""

    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as error:
        raise _SvsRuntimeFailure(
            "unsafe_path",
            "The prior generated vocal could not be inspected.",
        ) from error
    if (
        stat.S_ISLNK(info.st_mode)
        or _is_reparse(info)
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
    ):
        _fail("unsafe_path", "The prior generated vocal is unsafe.")
    try:
        path.unlink()
    except OSError as error:
        raise _SvsRuntimeFailure(
            "workspace_failed",
            "The prior generated vocal could not be replaced.",
        ) from error


def _read_proc_identity(pid: int) -> tuple[str, int, int, int] | None:
    """Return state, process group, session, and start ticks for one Linux PID.

    Linux wraps ``comm`` in parentheses and permits spaces or parentheses in
    that value, so fields are split only after the final closing parenthesis.
    The immutable kernel start tick prevents a recycled PID from inheriting a
    stale job lease.
    """

    try:
        raw = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
        closing = raw.rfind(")")
        if closing < 0 or len(raw) > 16 * 1024:
            raise ValueError
        fields = raw[closing + 2 :].split()
        if len(fields) < 20:
            raise ValueError
        return fields[0], int(fields[2]), int(fields[3]), int(fields[19])
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError) as error:
        raise _SvsRuntimeFailure(
            "stage_identity_failed",
            "The private singing process identity could not be verified.",
        ) from error


@contextmanager
def _stage_control_lock(job_root: Path) -> Iterator[None]:
    """Serialize stage registration against out-of-process cancellation.

    Cleanup writes its cancellation marker before taking this advisory lock.
    Therefore a stage already inside the launch critical section is recorded
    before cleanup inspects it, while every later launch observes cancellation
    before it can create a process.
    """

    lock_path = job_root / _STAGE_CONTROL_LOCK_NAME
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    fcntl_module = None
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError
        if os.name == "posix" and info.st_uid != _current_uid():
            raise OSError
        os.fchmod(descriptor, 0o600)
        fcntl_module = importlib.import_module("fcntl")
        fcntl_module.flock(descriptor, fcntl_module.LOCK_EX)
        yield
    except _SvsRuntimeFailure:
        raise
    except (ImportError, OSError, RuntimeError, TypeError) as error:
        raise _SvsRuntimeFailure(
            "stage_control_failed",
            "The private singing process control could not be secured.",
        ) from error
    finally:
        if descriptor >= 0:
            if fcntl_module is not None:
                try:
                    fcntl_module.flock(descriptor, fcntl_module.LOCK_UN)
                except (OSError, TypeError):
                    pass
            os.close(descriptor)


def _lease_payload(lease: _StageLease) -> dict[str, object]:
    """Return the closed JSON representation of one authenticated stage."""

    return {
        "jobToken": lease.job_token,
        "pgid": lease.process_group_id,
        "pid": lease.pid,
        "sessionId": lease.session_id,
        "stage": lease.stage,
        "stageId": lease.stage_id,
        "startTimeTicks": lease.start_time_ticks,
        "version": lease.version,
    }


def _write_stage_lease(job_root: Path, lease: _StageLease) -> None:
    """Atomically publish one mode-0600 stage lease below its private job."""

    destination = job_root / _STAGE_LEASE_NAME
    temporary = job_root / _STAGE_LEASE_TEMP_NAME
    if destination.exists() or destination.is_symlink():
        _fail("stage_control_failed", "A private singing stage is already active.")
    encoded = json.dumps(
        _lease_payload(lease),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    if len(encoded) > _MAX_STAGE_LEASE_BYTES:
        _fail("stage_control_failed", "The private singing stage identity is invalid.")
    descriptor = -1
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(temporary, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, destination)
    except (OSError, ValueError) as error:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise _SvsRuntimeFailure(
            "stage_control_failed",
            "The private singing stage identity could not be recorded.",
        ) from error


def _read_stage_lease(job_root: Path) -> _StageLease | None:
    """Load and authenticate the fixed lease without accepting caller paths."""

    path = job_root / _STAGE_LEASE_NAME
    try:
        os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise _SvsRuntimeFailure(
            "stage_control_failed",
            "The private singing stage identity could not be inspected.",
        ) from error
    candidate = _require_file(
        path,
        "Private singing stage identity",
        require_single_link=True,
    )
    try:
        if candidate.stat().st_size > _MAX_STAGE_LEASE_BYTES:
            raise ValueError
        payload = json.loads(candidate.read_text(encoding="ascii"))
        if not isinstance(payload, dict) or set(payload) != {
            "jobToken",
            "pgid",
            "pid",
            "sessionId",
            "stage",
            "stageId",
            "startTimeTicks",
            "version",
        }:
            raise ValueError
        lease = _StageLease(
            version=payload["version"],
            job_token=payload["jobToken"],
            stage=payload["stage"],
            stage_id=payload["stageId"],
            pid=payload["pid"],
            process_group_id=payload["pgid"],
            session_id=payload["sessionId"],
            start_time_ticks=payload["startTimeTicks"],
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise _SvsRuntimeFailure(
            "stage_control_failed",
            "The private singing stage identity is invalid.",
        ) from error
    if (
        type(lease.version) is not int
        or lease.version != _STAGE_LEASE_VERSION
        or not isinstance(lease.job_token, str)
        or lease.job_token != job_root.name
        or _JOB_TOKEN_PATTERN.fullmatch(lease.job_token) is None
        or not isinstance(lease.stage, str)
        or lease.stage not in _STAGE_NAMES
        or not isinstance(lease.stage_id, str)
        or _STAGE_ID_PATTERN.fullmatch(lease.stage_id) is None
        or type(lease.pid) is not int
        or type(lease.process_group_id) is not int
        or type(lease.session_id) is not int
        or type(lease.start_time_ticks) is not int
        or lease.pid <= 1
        or lease.process_group_id != lease.pid
        or lease.session_id != lease.pid
        or lease.start_time_ticks <= 0
    ):
        _fail("stage_control_failed", "The private singing stage identity is invalid.")
    return lease


def _clear_stage_lease(job_root: Path, stage_id: str) -> None:
    """Remove only the lease that belongs to the completing stage instance."""

    lease = _read_stage_lease(job_root)
    if lease is None:
        return
    if lease.stage_id != stage_id:
        _fail("stage_control_failed", "The private singing stage identity changed.")
    try:
        (job_root / _STAGE_LEASE_NAME).unlink()
    except FileNotFoundError:
        return
    except OSError as error:
        raise _SvsRuntimeFailure(
            "stage_control_failed",
            "The private singing stage identity could not be cleared.",
        ) from error


def _capture_stage_lease(
    process: subprocess.Popen[bytes],
    *,
    job_token: str,
    stage: str,
    stage_id: str,
) -> _StageLease:
    """Bind a just-launched session leader to its non-reusable kernel start tick."""

    identity = _read_proc_identity(process.pid)
    if identity is None:
        _fail("stage_unavailable", "The private singing stage stopped during startup.")
    state, process_group_id, session_id, start_time_ticks = identity
    if state == "Z" or process_group_id != process.pid or session_id != process.pid:
        _fail("stage_identity_failed", "The private singing stage was not isolated.")
    return _StageLease(
        version=_STAGE_LEASE_VERSION,
        job_token=job_token,
        stage=stage,
        stage_id=stage_id,
        pid=process.pid,
        process_group_id=process_group_id,
        session_id=session_id,
        start_time_ticks=start_time_ticks,
    )


def _authorize_stage_entry(job_root: Path, expected_stage: str) -> _StageLease:
    """Wait for and authenticate the parent-published one-time stage lease.

    The child can reach Python before the parent has captured its kernel start
    tick and atomically published the lease.  A short bounded wait closes that
    startup race.  Vendor imports begin only after the random capability,
    UUID, stage name, PID, session, process group, and non-reusable start tick
    all match this exact process.
    """

    job_token = os.environ.get(_INTERNAL_JOB_TOKEN_ENV, "")
    stage_id = os.environ.get(_INTERNAL_STAGE_ID_ENV, "")
    if (
        expected_stage not in _STAGE_NAMES
        or job_token != job_root.name
        or _JOB_TOKEN_PATTERN.fullmatch(job_token) is None
        or _STAGE_ID_PATTERN.fullmatch(stage_id) is None
    ):
        _fail("stage_control_failed", "The private singing stage is unauthorized.")
    deadline = time.monotonic() + _STAGE_AUTHORIZATION_TIMEOUT_SECONDS
    while True:
        lease = _read_stage_lease(job_root)
        if lease is not None:
            identity = _read_proc_identity(os.getpid())
            if identity is None:
                _fail(
                    "stage_control_failed",
                    "The private singing stage identity disappeared.",
                )
            state, process_group_id, session_id, start_time_ticks = identity
            if (
                state == "Z"
                or lease.job_token != job_token
                or lease.stage != expected_stage
                or lease.stage_id != stage_id
                or lease.pid != os.getpid()
                or lease.process_group_id != process_group_id
                or lease.session_id != session_id
                or lease.start_time_ticks != start_time_ticks
                or process_group_id != os.getpid()
                or session_id != os.getpid()
            ):
                _fail(
                    "stage_control_failed",
                    "The private singing stage lease does not match this process.",
                )
            return lease
        if time.monotonic() >= deadline:
            _fail(
                "stage_control_failed",
                "The private singing stage was not authorized in time.",
            )
        time.sleep(_STAGE_POLL_SECONDS)


def _has_inherited_stage_identity(pid: int, lease: _StageLease) -> bool:
    """Recognize a detached descendant by its inherited random stage identity.

    Session membership is the primary boundary.  The environment identity is
    an additional exact-job net for trusted vendor children that create a new
    session internally; it is random per launch and paired with the UUID, so
    no executable-name matching or global process killing is needed.
    """

    path = Path("/proc") / str(pid) / "environ"
    try:
        with path.open("rb") as source:
            payload = source.read(_MAX_PROC_ENVIRONMENT_BYTES + 1)
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return False
    except OSError:
        return False
    if len(payload) > _MAX_PROC_ENVIRONMENT_BYTES:
        return False
    values = set(payload.split(b"\0"))
    return (
        f"{_INTERNAL_JOB_TOKEN_ENV}={lease.job_token}".encode("ascii") in values
        and f"{_INTERNAL_STAGE_ID_ENV}={lease.stage_id}".encode("ascii") in values
    )


def _stage_session_members(lease: _StageLease) -> list[int]:
    """List live same-uid members of the exact leased Linux stage.

    Session identity covers normal descendants even when they create another
    process group.  The random inherited environment identity also catches a
    trusted library that detaches into another session.  Zombies are already
    incapable of accessing job files and are reaped by their parent or init.
    """

    proc_root = Path("/proc")
    members: list[int] = []
    try:
        entries = list(proc_root.iterdir())
    except OSError as error:
        raise _SvsRuntimeFailure(
            "stage_identity_failed",
            "The private singing process table could not be inspected.",
        ) from error
    current_uid = _current_uid()
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        pid = int(entry.name)
        identity = _read_proc_identity(pid)
        if identity is None:
            continue
        state, _process_group_id, session_id, start_time_ticks = identity
        try:
            if entry.stat().st_uid != current_uid:
                continue
        except FileNotFoundError:
            continue
        except OSError as error:
            raise _SvsRuntimeFailure(
                "stage_identity_failed",
                "The private singing process owner could not be verified.",
            ) from error
        if (
            session_id != lease.session_id
            and not _has_inherited_stage_identity(pid, lease)
        ):
            continue
        if pid == lease.pid and start_time_ticks != lease.start_time_ticks:
            _fail("stage_identity_failed", "The private singing process identity changed.")
        if state != "Z":
            members.append(pid)
    return sorted(set(members), reverse=True)


def _signal_stage_session(lease: _StageLease, signal_number: int) -> None:
    """Signal every live member of one authenticated session and no other PID."""

    for pid in _stage_session_members(lease):
        try:
            os.kill(pid, signal_number)
        except ProcessLookupError:
            continue
        except OSError as error:
            raise _SvsRuntimeFailure(
                "stage_termination_failed",
                "The private singing stage could not be terminated safely.",
            ) from error


def _wait_for_stage_exit(lease: _StageLease, timeout_seconds: float) -> bool:
    """Wait a bounded interval until an authenticated session has no live members."""

    deadline = time.monotonic() + timeout_seconds
    while True:
        if not _stage_session_members(lease):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_STAGE_POLL_SECONDS)


def _terminate_stage_session(lease: _StageLease) -> None:
    """Stop one exact stage session with bounded TERM then KILL escalation."""

    if not _stage_session_members(lease):
        return
    _signal_stage_session(lease, _SIGTERM)
    if _wait_for_stage_exit(lease, _STAGE_TERMINATE_GRACE_SECONDS):
        return
    _signal_stage_session(lease, _SIGKILL)
    if not _wait_for_stage_exit(lease, _STAGE_KILL_GRACE_SECONDS):
        _fail(
            "stage_termination_failed",
            "The private singing stage did not stop within its safe limit.",
        )


def _create_cancellation_marker(job_root: Path) -> None:
    """Atomically prevent any later stage launch for a cleanup-bound job."""

    path = job_root / _CANCEL_REQUEST_NAME
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except FileExistsError:
        _require_file(path, "Private singing cancellation", require_single_link=True)
    except OSError as error:
        raise _SvsRuntimeFailure(
            "stage_control_failed",
            "The private singing cancellation could not be recorded.",
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _raise_if_cancelled(job_root: Path) -> None:
    """Reject a stage launch after exact-job cleanup has begun."""

    path = job_root / _CANCEL_REQUEST_NAME
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as error:
        raise _SvsRuntimeFailure(
            "stage_control_failed",
            "The private singing cancellation could not be inspected.",
        ) from error
    _require_file(path, "Private singing cancellation", require_single_link=True)
    _fail("cancelled", "Singing synthesis was cancelled.")


def _terminate_job_processes_for_cleanup(job_root: Path) -> None:
    """Cancel and verify disappearance of one UUID job's leased Linux session.

    This is the sole entry point used by bridge cleanup before recursive file
    removal.  The UUID directory and its lease were authenticated earlier;
    cleanup never searches by executable name or affects sibling jobs.
    """

    runtime_root = _runtime_root()
    candidate, _token = _validate_job_root(job_root, runtime_root)
    _create_cancellation_marker(candidate)
    with _stage_control_lock(candidate):
        lease = _read_stage_lease(candidate)
        if lease is not None:
            _terminate_stage_session(lease)
            if _stage_session_members(lease):
                _fail(
                    "stage_termination_failed",
                    "The private singing stage is still running.",
                )
            _clear_stage_lease(candidate, lease.stage_id)


def _bounded_stage_timeout(deadline: float, stage_limit_seconds: int) -> int:
    """Return a stage cap reduced by the shared private-runtime deadline."""

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        _fail("stage_timeout", "The private singing job exceeded its safe time limit.")
    return min(stage_limit_seconds, max(1, math.ceil(remaining)))


def _offline_environment(
    runtime_root: Path,
    source_root: Path,
    *,
    job_token: str | None = None,
) -> dict[str, str]:
    """Build a minimal proxy-free environment for a fixed local child stage."""

    home = runtime_root.parents[3]
    environment = {
        "CUBLAS_WORKSPACE_CONFIG": ":4096:8",
        "CUDA_MODULE_LOADING": "LAZY",
        "CUDA_VISIBLE_DEVICES": "0",
        "HF_DATASETS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_OFFLINE": "1",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "NO_PROXY": "*",
        "OMP_NUM_THREADS": "4",
        "PATH": (
            f"{runtime_root / 'prep-env' / 'bin'}:"
            "/usr/local/bin:/usr/bin:/bin:/usr/lib/wsl/lib"
        ),
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": f"{_project_root()}:{source_root}",
        "TOKENIZERS_PARALLELISM": "false",
        "TORCH_FORCE_WEIGHTS_ONLY_LOAD": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    if job_token is not None:
        environment[_INTERNAL_JOB_TOKEN_ENV] = job_token
    return environment


def _run_quiet_process(
    command: Sequence[str],
    *,
    job_root: Path,
    job_token: str,
    stage: str,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: int,
) -> int:
    """Run one fixed command in a leased session with exact-job termination.

    ``start_new_session`` makes the direct child both session and process-group
    leader before exec.  A kernel start tick and random stage identifier are
    persisted before the launch lock is released, closing PID-reuse and
    cleanup/launch races.  Even a successful direct child is followed by a
    session sweep so background vendor descendants cannot outlive the stage.
    """

    if _JOB_TOKEN_PATTERN.fullmatch(job_token) is None or job_root.name != job_token:
        _fail("invalid_job", "The private singing job identity is invalid.")
    if stage not in _STAGE_NAMES:
        _fail("invalid_request", "The private singing stage is invalid.")
    if timeout_seconds <= 0:
        _fail("stage_timeout", "The private singing stage has no time remaining.")
    stage_id = secrets.token_hex(16)
    child_environment = dict(environment)
    child_environment[_INTERNAL_JOB_TOKEN_ENV] = job_token
    child_environment[_INTERNAL_STAGE_ID_ENV] = stage_id
    process: subprocess.Popen[bytes] | None = None
    lease: _StageLease | None = None
    timed_out: subprocess.TimeoutExpired | None = None
    try:
        with _stage_control_lock(job_root):
            _raise_if_cancelled(job_root)
            if _read_stage_lease(job_root) is not None:
                _fail("stage_control_failed", "A private singing stage is already active.")
            process = subprocess.Popen(
                list(command),
                cwd=str(cwd),
                env=child_environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                shell=False,
            )
            lease = _capture_stage_lease(
                process,
                job_token=job_token,
                stage=stage,
                stage_id=stage_id,
            )
            _write_stage_lease(job_root, lease)
        try:
            return_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as error:
            timed_out = error
            return_code = -1
    except OSError as error:
        raise _SvsRuntimeFailure(
            "stage_unavailable",
            "The private singing stage could not be started.",
        ) from error
    finally:
        if lease is not None:
            with _stage_control_lock(job_root):
                current = _read_stage_lease(job_root)
                if current is not None and current.stage_id != lease.stage_id:
                    _fail(
                        "stage_control_failed",
                        "The private singing stage identity changed.",
                    )
                _terminate_stage_session(lease)
                if current is not None:
                    _clear_stage_lease(job_root, lease.stage_id)
        elif process is not None and process.poll() is None:
            # A lease capture failure occurs before vendor code can be trusted
            # to remain childless.  The direct process handle is the only safe
            # identity available, so stop and reap it before surfacing failure.
            process.kill()
        if process is not None:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired as error:
                raise _SvsRuntimeFailure(
                    "stage_termination_failed",
                    "The private singing stage could not be reaped.",
                ) from error
    if timed_out is not None:
        raise _SvsRuntimeFailure(
            "stage_timeout",
            "The private singing stage exceeded its safe time limit.",
        ) from timed_out
    return return_code


def _load_metadata(path: Path) -> list[dict[str, object]]:
    """Load one bounded metadata list emitted by the fixed preprocessing stage."""

    candidate = _require_file(path, "SoulX target metadata", require_single_link=True)
    try:
        if candidate.stat().st_size > _MAX_METADATA_BYTES:
            _fail("invalid_metadata", "SoulX target metadata is too large.")
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except _SvsRuntimeFailure:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _SvsRuntimeFailure(
            "invalid_metadata",
            "SoulX target metadata could not be decoded.",
        ) from error
    if not isinstance(payload, list) or not payload or any(
        not isinstance(item, dict) for item in payload
    ):
        _fail("invalid_metadata", "SoulX target metadata has an invalid shape.")
    return payload


def _write_metadata(path: Path, metadata: Sequence[Mapping[str, object]]) -> None:
    """Atomically write bounded corrected metadata with mode-0600 semantics."""

    try:
        encoded = json.dumps(
            list(metadata),
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise _SvsRuntimeFailure(
            "invalid_metadata",
            "Corrected SoulX metadata could not be encoded.",
        ) from error
    if len(encoded) > _MAX_METADATA_BYTES:
        _fail("invalid_metadata", "Corrected SoulX metadata is too large.")
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("xb") as destination:
            os.chmod(temporary, 0o600)
            destination.write(encoded)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
    except FileExistsError as error:
        raise _SvsRuntimeFailure(
            "unsafe_path",
            "A corrected metadata temporary file already exists.",
        ) from error
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise _SvsRuntimeFailure(
            "workspace_failed",
            "Corrected SoulX metadata could not be saved.",
        ) from error


def _install_mandarin_hotword(pipeline: object, hotword: str) -> None:
    """Bias only the vendor Mandarin ASR call with validated lyric tokens.

    SoulX currently calls FunASR without a hotword and offers no pipeline
    parameter for one.  Replacing that single instance method avoids modifying
    the verified vendor checkout while preserving the official transcription
    and timestamp implementation.  The authoritative LRC still passes through
    strict post-alignment; hotwords improve recognition but never authorize a
    mismatched lyric.
    """

    try:
        lyric_transcriber = getattr(pipeline, "lyric_transcriber")
        zh_model = getattr(lyric_transcriber, "zh_model")
        funasr_model = getattr(zh_model, "model")
        original_generate = getattr(funasr_model, "generate")
    except (AttributeError, TypeError) as error:
        raise _SvsRuntimeFailure(
            "preprocess_failed",
            "The private Mandarin transcription stage is incompatible.",
        ) from error
    if not callable(original_generate):
        _fail(
            "preprocess_failed",
            "The private Mandarin transcription stage is incompatible.",
        )

    def _generate_with_hotword(*args: object, **kwargs: object) -> object:
        kwargs["hotword"] = hotword
        return original_generate(*args, **kwargs)

    try:
        setattr(funasr_model, "generate", _generate_with_hotword)
    except (AttributeError, TypeError) as error:
        raise _SvsRuntimeFailure(
            "preprocess_failed",
            "The private Mandarin transcription stage is incompatible.",
        ) from error


def _perform_preprocess_stage(job_root: Path, runtime_root: Path) -> None:
    """Transcribe the vocal and render fail-closed LRC-corrected metadata."""

    # Vendor imports remain inside the pinned prep subprocess; importing them
    # in the lightweight orchestrator would mix incompatible Torch runtimes.
    from preprocess.pipeline import PreprocessPipeline  # type: ignore[import-not-found]
    from preprocess.tools.g2p import g2p_transform  # type: ignore[import-not-found]
    from scripts.song_lyrics_alignment import (
        build_mandarin_hotword,
        plan_metadata_lyric_corrections,
        render_corrected_metadata,
    )

    work_root = _require_directory(
        job_root / _WORK_DIRECTORY_NAME,
        "Singing work directory",
    )
    target_vocal = _require_file(
        work_root / "target_vocal.wav",
        "Private target vocal",
        require_single_link=True,
    )
    duration_seconds = _probe_wave(target_vocal, label="Private target vocal")
    synchronized_lyrics = _read_bounded_text(
        job_root / "lyrics.lrc",
        label="Synchronized lyrics",
        maximum_bytes=_MAX_LYRICS_BYTES,
    )
    plain_path = job_root / "lyrics.txt"
    plain_lyrics = None
    if plain_path.exists() or plain_path.is_symlink():
        plain_lyrics = _read_bounded_text(
            plain_path,
            label="Plain lyrics",
            maximum_bytes=_MAX_LYRICS_BYTES,
        )
    duration_milliseconds = int(round(duration_seconds * 1000))
    hotword = build_mandarin_hotword(
        synchronized_lyrics,
        duration_milliseconds,
    )
    preprocess_root = work_root / "preprocess"
    try:
        preprocess_root.mkdir(mode=0o700)
    except OSError as error:
        raise _SvsRuntimeFailure(
            "workspace_failed",
            "The transcription workspace could not be created.",
        ) from error

    os.chdir(runtime_root / "source")
    pipeline = PreprocessPipeline(
        device="cuda:0",
        language="Mandarin",
        save_dir=str(preprocess_root),
        vocal_sep=False,
        max_merge_duration=60_000,
        midi_transcribe=True,
    )
    _install_mandarin_hotword(pipeline, hotword)
    pipeline.run(audio_path=str(target_vocal), language="Mandarin")

    metadata = _load_metadata(preprocess_root / "metadata.json")
    plan = plan_metadata_lyric_corrections(
        metadata,
        synchronized_lyrics,
        duration_milliseconds,
        plain_lyrics=plain_lyrics,
    )
    corrected = render_corrected_metadata(metadata, plan, g2p_transform)
    _write_metadata(work_root / "target.corrected.json", corrected)


def _preprocess_stage_entry() -> None:
    """Execute the private prep child and communicate only by exit category."""

    try:
        token = os.environ.get(_INTERNAL_JOB_TOKEN_ENV, "")
        runtime_root = _runtime_root()
        job_root = _job_root_from_token(runtime_root, token)
        _authorize_stage_entry(job_root, "preprocess")
        _verify_trusted_runtime_tree(runtime_root)
        # OpenCC is imported by the lyric alignment module.  Verify its Python,
        # native, configuration, and t2s dictionary files before that import so
        # a changed localization package cannot execute ahead of attestation.
        for spec in _LOCALIZATION_ASSETS:
            _require_asset(runtime_root, spec)
        _verify_source_checkout(runtime_root)
        _perform_preprocess_stage(job_root, runtime_root)
    except Exception as error:
        # Alignment gets a distinct exit code so the outer worker can explain
        # that authoritative lyrics were rejected without printing internals.
        try:
            alignment_module = importlib.import_module(
                "scripts.song_lyrics_alignment"
            )
            alignment_error_type = getattr(
                alignment_module,
                "LyricsAlignmentError",
                None,
            )
        except Exception:
            alignment_error_type = None
        if (
            isinstance(alignment_error_type, type)
            and issubclass(alignment_error_type, BaseException)
            and isinstance(error, alignment_error_type)
        ):
            raise SystemExit(_ALIGNMENT_EXIT_CODE) from None
        raise SystemExit(_PREPROCESS_EXIT_CODE) from None


def _invoke_preprocess(
    job_token: str,
    runtime_root: Path,
    source_root: Path,
    *,
    timeout_seconds: int,
) -> None:
    """Run the fixed transcription/alignment entry point in the prep venv."""

    command = [
        str(runtime_root / "prep-env" / "bin" / "python"),
        "-B",
        "-c",
        (
            "from scripts.song_svs_runtime import _preprocess_stage_entry as entry; "
            "entry()"
        ),
    ]
    return_code = _run_quiet_process(
        command,
        job_root=_jobs_root(runtime_root) / job_token,
        job_token=job_token,
        stage="preprocess",
        cwd=source_root,
        environment=_offline_environment(
            runtime_root,
            source_root,
            job_token=job_token,
        ),
        timeout_seconds=timeout_seconds,
    )
    if return_code == _ALIGNMENT_EXIT_CODE:
        _fail(
            "lyrics_alignment_failed",
            "The synchronized lyrics could not be aligned safely to this vocal.",
        )
    if return_code != 0:
        _fail(
            "preprocess_failed",
            "The private singing transcription stage did not complete.",
        )


def _prompt_records_from_manifest(
    runtime_root: Path,
    manifest: _PromptBankManifest,
) -> tuple[PromptRecord, ...]:
    """Convert authenticated manifest entries into selector-ready records.

    The manifest parser intentionally owns filesystem policy, while the
    quality module owns only acoustic selection.  Requiring each asset again
    here keeps the paths handed to the model tied to the already reviewed
    size, digest, and single-link contract at the last practical boundary.
    """

    from scripts.song_svs_quality import PromptAsset, PromptProfile, PromptRecord

    records: list[PromptRecord] = []
    for entry in manifest.entries:
        audio_path = _require_asset(
            runtime_root,
            entry.audio,
            require_single_link=True,
            require_private_prompt_permissions=True,
        )
        metadata_path = _require_asset(
            runtime_root,
            entry.metadata,
            require_single_link=True,
            require_private_prompt_permissions=True,
        )
        records.append(
            PromptRecord(
                prompt_id=entry.entry_id,
                role=entry.role,
                selection_rank=entry.selection_rank,
                style_proxy=entry.style_proxy,
                emotion=entry.emotion,
                audio=PromptAsset(
                    path=audio_path,
                    byte_count=entry.audio.expected_bytes,
                    sha256=entry.audio.expected_sha256,
                ),
                metadata=PromptAsset(
                    path=metadata_path,
                    byte_count=entry.metadata.expected_bytes,
                    sha256=entry.metadata.expected_sha256,
                ),
                profile=PromptProfile(
                    duration_seconds=entry.profile.duration_seconds,
                    note_median=entry.profile.note_median,
                    note_p10=entry.profile.note_p10,
                    note_p90=entry.profile.note_p90,
                    note_span=entry.profile.note_span,
                    syllables_per_second=entry.profile.syllables_per_second,
                    phonemes=frozenset(entry.profile.phonemes),
                ),
            )
        )
    return tuple(records)


def _perform_inference_stage(
    job_root: Path,
    runtime_root: Path,
    source_root: Path,
    prompt_bank: _PromptBankManifest,
) -> Path:
    """Run deterministic multi-prompt inference inside the pinned child.

    No renderer-selected model, prompt, configuration, or output path crosses
    this boundary.  The request is assembled exclusively from the fixed
    runtime, verified source checkout, authenticated prompt bank, and the
    corrected metadata produced earlier in the same private UUID job.
    """

    from scripts.song_svs_inference import SvsInferenceRequest, run_svs_inference

    work_root = _require_directory(
        job_root / _WORK_DIRECTORY_NAME,
        "Singing work directory",
    )
    target_vocal = _require_file(
        work_root / "target_vocal.wav",
        "Private target vocal",
        require_single_link=True,
    )
    target_metadata = _load_metadata(work_root / "target.corrected.json")
    output_root = _require_directory(
        work_root / "generated",
        "Synthesis workspace",
    )
    model_path = _require_file(
        runtime_root / "models" / "SoulX-Singer" / "model.pt",
        "SoulX model",
        require_single_link=True,
    )
    config_path = _require_file(
        source_root / "soulxsinger" / "config" / "soulxsinger.yaml",
        "SoulX configuration",
        require_single_link=True,
    )
    phoneset_path = _require_file(
        source_root / "soulxsinger" / "utils" / "phoneme" / "phone_set.json",
        "SoulX phoneset",
        require_single_link=True,
    )
    duration_seconds = _probe_wave(target_vocal, label="Private target vocal")
    total_samples = max(1, int(round(duration_seconds * 24_000)))
    expected_output = output_root / "generated.wav"
    if expected_output.exists() or expected_output.is_symlink():
        _fail("unsafe_path", "The synthesis output path is already occupied.")

    result = run_svs_inference(
        SvsInferenceRequest(
            model_path=model_path,
            config_path=config_path,
            phoneset_path=phoneset_path,
            output_directory=output_root,
            target_audio_sha256=_sha256(target_vocal),
            target_metadata=target_metadata,
            prompts=_prompt_records_from_manifest(runtime_root, prompt_bank),
            total_samples=total_samples,
        )
    )
    if result.output_path != expected_output:
        _fail("inference_failed", "The singing synthesizer returned an invalid output.")
    return _require_file(
        expected_output,
        "Generated SoulX vocal",
        require_single_link=True,
    )


def _inference_stage_entry() -> None:
    """Execute the private inference child and expose only one exit category."""

    try:
        token = os.environ.get(_INTERNAL_JOB_TOKEN_ENV, "")
        runtime_root = _runtime_root()
        job_root = _job_root_from_token(runtime_root, token)
        _authorize_stage_entry(job_root, "inference")
        prompt_bank = _verify_runtime_assets(runtime_root)
        source_root = _verify_source_checkout(runtime_root)
        _perform_inference_stage(
            job_root,
            runtime_root,
            source_root,
            prompt_bank,
        )
    except Exception:
        # The outer worker owns renderer-facing errors; private model, prompt,
        # CUDA, and path details must never cross this subprocess boundary.
        raise SystemExit(_INFERENCE_EXIT_CODE) from None


def _invoke_inference(
    job_root: Path,
    runtime_root: Path,
    source_root: Path,
    *,
    timeout_seconds: int,
) -> Path:
    """Run fixed Mandarin score-control inference in the isolated infer venv."""

    work_root = job_root / _WORK_DIRECTORY_NAME
    save_root = work_root / "generated"
    try:
        save_root.mkdir(mode=0o700)
    except OSError as error:
        raise _SvsRuntimeFailure(
            "workspace_failed",
            "The synthesis workspace could not be created.",
        ) from error
    command = [
        str(runtime_root / "infer-env" / "bin" / "python"),
        "-B",
        "-c",
        (
            "from scripts.song_svs_runtime import _inference_stage_entry as entry; "
            "entry()"
        ),
    ]
    return_code = _run_quiet_process(
        command,
        job_root=job_root,
        job_token=job_root.name,
        stage="inference",
        cwd=source_root,
        environment=_offline_environment(
            runtime_root,
            source_root,
            job_token=job_root.name,
        ),
        timeout_seconds=timeout_seconds,
    )
    if return_code != 0:
        _fail(
            "inference_failed",
            "The private Elysia singing synthesis stage did not complete.",
        )
    return _require_file(
        save_root / "generated.wav",
        "Generated SoulX vocal",
        require_single_link=True,
    )


def _publish_output(
    generated: Path,
    destination: Path,
    *,
    expected_duration_seconds: float,
) -> None:
    """Validate and atomically publish the fixed generated-vocal filename."""

    generated_duration = _probe_wave(generated, label="Generated SoulX vocal")
    if abs(generated_duration - expected_duration_seconds) > 1.0:
        _fail(
            "invalid_output",
            "The generated vocal duration does not match the target vocal.",
        )
    temporary = destination.with_suffix(".tmp.wav")
    _clear_optional_output(temporary)
    try:
        with generated.open("rb") as source, temporary.open("xb") as output:
            os.chmod(temporary, 0o600)
            shutil.copyfileobj(source, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise _SvsRuntimeFailure(
            "publish_failed",
            "The generated vocal could not be published.",
        ) from error


def _run_job(job_root_argument: str) -> None:
    """Validate one managed job and produce its fixed unmixed vocal output."""

    deadline = time.monotonic() + _TOTAL_RUNTIME_TIMEOUT_SECONDS
    runtime_root = _runtime_root()
    job_root, job_token = _validate_job_root(Path(job_root_argument), runtime_root)
    target_vocal = _require_file(
        job_root / "target_vocal.wav",
        "Separated target vocal",
        require_single_link=True,
    )
    synchronized_lyrics = _require_file(
        job_root / "lyrics.lrc",
        "Synchronized lyrics",
        require_single_link=True,
    )
    if synchronized_lyrics.stat().st_size > _MAX_LYRICS_BYTES:
        _fail("lyrics_too_large", "Synchronized lyrics exceed the supported size.")
    target_duration = _probe_wave(target_vocal, label="Separated target vocal")
    output_path = job_root / _OUTPUT_NAME
    _clear_optional_output(output_path)

    _emit("validating", 4, "Validating the private SoulX runtime.")
    _verify_runtime_assets(runtime_root)
    source_root = _verify_source_checkout(runtime_root)
    work_root = _prepare_work_tree(job_root)
    try:
        shutil.copyfile(target_vocal, work_root / "target_vocal.wav")
        os.chmod(work_root / "target_vocal.wav", 0o600)
        _emit("transcribing", 18, "Transcribing melody and Mandarin syllables.")
        _invoke_preprocess(
            job_token,
            runtime_root,
            source_root,
            timeout_seconds=_bounded_stage_timeout(
                deadline,
                _PREPROCESS_TIMEOUT_SECONDS,
            ),
        )
        _require_file(
            work_root / "target.corrected.json",
            "Corrected SoulX metadata",
            require_single_link=True,
        )
        _emit("aligning", 52, "Verified lyrics are aligned to detected notes.")
        _emit("synthesizing", 58, "Synthesizing the private Elysia vocal.")
        generated = _invoke_inference(
            job_root,
            runtime_root,
            source_root,
            timeout_seconds=_bounded_stage_timeout(
                deadline,
                _INFERENCE_TIMEOUT_SECONDS,
            ),
        )
        _publish_output(
            generated,
            output_path,
            expected_duration_seconds=target_duration,
        )
    finally:
        # Successful and failed jobs both discard prompt-derived intermediates;
        # the selected input and fixed final output are the only durable files.
        _remove_work_tree(work_root)
    _emit("ready", 100, "The Elysia vocal is ready for the reviewed mix stage.")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one managed offline SVS job and return a process-style status code.

    Exactly one positional job root is accepted.  All language, model, prompt,
    device, control-mode, and output choices are closed constants in this
    module so a renderer cannot turn the worker into a generic command runner.
    """

    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        _emit(
            "error",
            0,
            "Exactly one managed singing job is required.",
            error_code="invalid_request",
        )
        return 2
    try:
        _run_job(arguments[0])
    except _SvsRuntimeFailure as error:
        _emit("error", 0, str(error), error_code=error.code)
        return 1
    except KeyboardInterrupt:
        _emit("error", 0, "Singing synthesis was cancelled.", error_code="cancelled")
        return 130
    except Exception:
        # Vendor exceptions frequently include absolute paths.  The outermost
        # boundary intentionally collapses them to one stable safe message.
        _emit(
            "error",
            0,
            "The private singing runtime stopped unexpectedly.",
            error_code="runtime_failed",
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
