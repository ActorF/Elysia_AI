"""Test the closed offline SoulX SVS runtime without loading private models."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace
from typing import Iterator
import wave

import pytest

from scripts import song_svs_runtime


def _canonical_json(value: object) -> bytes:
    """Encode one fixture with the production manifest's canonical JSON form."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _prompt_bank_document() -> dict[str, object]:
    """Return the smallest valid closed prompt-bank manifest fixture."""

    return {
        "schema_version": 1,
        "bank_id": "elysia-bank-v1",
        "privacy": "private-local-only",
        "builder_sha256": "1" * 64,
        "source_manifest_sha256": "2" * 64,
        "entries": [
            {
                "id": "synthetic-fixture-a",
                "role": "anchor",
                "selection_rank": 0,
                "style_proxy": "neutral",
                "emotion": None,
                "audio": {
                    "path": "audio/synthetic-fixture-a.wav",
                    "bytes": 8,
                    "sha256": hashlib.sha256(b"audio-v1").hexdigest(),
                },
                "metadata": {
                    "path": "metadata/synthetic-fixture-a.json",
                    "bytes": 13,
                    "sha256": hashlib.sha256(b'{"version":1}').hexdigest(),
                },
                "profile": {
                    "duration_seconds": 6.25,
                    "note_median": 62.0,
                    "note_p10": 55.0,
                    "note_p90": 70.0,
                    "note_span": 15.0,
                    "syllables_per_second": 3.2,
                    "phonemes": ["zh_ai", "zh_li", "<AP>"],
                },
                "validation": {
                    "source_role": "anchor",
                    "transcript_sha256": "3" * 64,
                    "alignment_exact_match_ratio": 1.0,
                    "alignment_cost_ratio": 0.0,
                    "metadata_segments": 1,
                },
            }
        ],
    }


def _write_prompt_bank(
    runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, object], Path]:
    """Write and pin one minimal private prompt bank for runtime tests."""

    document = _prompt_bank_document()
    bank_root = runtime_root / "prompts" / "elysia-bank-v1"
    audio_path = bank_root / "audio" / "synthetic-fixture-a.wav"
    metadata_path = bank_root / "metadata" / "synthetic-fixture-a.json"
    audio_path.parent.mkdir(parents=True)
    metadata_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"audio-v1")
    metadata_path.write_bytes(b'{"version":1}')
    manifest_path = bank_root / "manifest.json"
    encoded = _canonical_json(document)
    manifest_path.write_bytes(encoded)
    if os.name == "posix":
        # The production privacy contract is exact rather than umask-relative.
        # Explicit fixture modes keep Linux CI representative and reproducible.
        for directory in (
            runtime_root / "prompts",
            bank_root,
            audio_path.parent,
            metadata_path.parent,
        ):
            directory.chmod(0o700)
        for private_file in (audio_path, metadata_path, manifest_path):
            private_file.chmod(0o600)
    monkeypatch.setattr(
        song_svs_runtime,
        "_PROMPT_BANK_MANIFEST_ASSET",
        song_svs_runtime._AssetSpec(
            "prompts/elysia-bank-v1/manifest.json",
            len(encoded),
            hashlib.sha256(encoded).hexdigest(),
        ),
    )
    return document, manifest_path


def _set_nested_value(
    document: dict[str, object],
    path: tuple[str | int, ...],
    value: object,
) -> None:
    """Replace one nested fixture field without weakening production parsing."""

    target: object = document
    for part in path[:-1]:
        if isinstance(part, int):
            assert isinstance(target, list)
            target = target[part]
        else:
            assert isinstance(target, dict)
            target = target[part]
    final = path[-1]
    if isinstance(final, int):
        assert isinstance(target, list)
        target[final] = value
    else:
        assert isinstance(target, dict)
        target[final] = value


def _write_pcm_wave(path: Path, *, duration_seconds: float = 1.25) -> None:
    """Write one deterministic mono PCM fixture accepted by the worker."""

    sample_rate = 16_000
    frame_count = int(round(sample_rate * duration_seconds))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x00\x00" * frame_count)


def _assert_failure_code(
    expected_code: str,
    callback: Callable[[], object],
) -> None:
    """Assert that one fail-closed boundary exposes only its stable code."""

    with pytest.raises(song_svs_runtime._SvsRuntimeFailure) as captured:
        callback()
    assert captured.value.code == expected_code


def _create_managed_job(runtime_root: Path, token: str) -> Path:
    """Create one UUID-scoped managed job directory for an orchestration test."""

    jobs_root = runtime_root / "jobs"
    jobs_root.mkdir(parents=True)
    job_root = jobs_root / token
    job_root.mkdir()
    _write_pcm_wave(job_root / "target_vocal.wav")
    (job_root / "lyrics.lrc").write_text(
        "[00:00.00]无瑕的歌谣\n",
        encoding="utf-8",
    )
    return job_root


def test_probe_wave_accepts_bounded_pcm_and_reports_duration(tmp_path: Path) -> None:
    """Read duration from a supported fixed-size PCM fixture without FFmpeg."""

    vocal = tmp_path / "vocal.wav"
    _write_pcm_wave(vocal, duration_seconds=1.5)

    assert song_svs_runtime._probe_wave(vocal, label="Test vocal") == pytest.approx(
        1.5
    )


def test_probe_wave_rejects_non_wave_and_hardlinked_input(tmp_path: Path) -> None:
    """Reject malformed or multiply linked input before a decoder sees it."""

    malformed = tmp_path / "malformed.wav"
    malformed.write_bytes(b"not a wave file")
    _assert_failure_code(
        "invalid_vocal",
        lambda: song_svs_runtime._probe_wave(malformed, label="Test vocal"),
    )

    vocal = tmp_path / "linked.wav"
    _write_pcm_wave(vocal)
    os.link(vocal, tmp_path / "second-name.wav")
    _assert_failure_code(
        "unsafe_path",
        lambda: song_svs_runtime._probe_wave(vocal, label="Test vocal"),
    )


def test_validate_job_root_accepts_only_direct_uuid_v4_children(tmp_path: Path) -> None:
    """Keep renderer paths within one direct private UUIDv4 job directory."""

    runtime_root = tmp_path / "runtime"
    jobs_root = runtime_root / "jobs"
    jobs_root.mkdir(parents=True)
    valid = jobs_root / "123e4567-e89b-42d3-a456-426614174000"
    valid.mkdir()

    job_root, token = song_svs_runtime._validate_job_root(valid, runtime_root)
    assert job_root == valid
    assert token == valid.name

    nested = valid / "123e4567-e89b-42d3-a456-426614174001"
    nested.mkdir()
    _assert_failure_code(
        "invalid_job",
        lambda: song_svs_runtime._validate_job_root(nested, runtime_root),
    )

    invalid = jobs_root / "not-a-managed-job"
    invalid.mkdir()
    _assert_failure_code(
        "invalid_job",
        lambda: song_svs_runtime._validate_job_root(invalid, runtime_root),
    )


def test_asset_verification_rejects_digest_or_size_changes(tmp_path: Path) -> None:
    """Pin every private asset by both reviewed length and SHA256 digest."""

    asset = tmp_path / "model.pt"
    asset.write_bytes(b"reviewed-model")
    spec = song_svs_runtime._AssetSpec(
        "model.pt",
        len(b"reviewed-model"),
        "80837ffc9506e991f5ca5ae6ba7a57947fb5606128d6a2574c13b9b010db1e23",
    )

    assert song_svs_runtime._require_asset(tmp_path, spec) == asset
    asset.write_bytes(b"modified-model")
    _assert_failure_code(
        "runtime_integrity_failed",
        lambda: song_svs_runtime._require_asset(tmp_path, spec),
    )


def test_core_runtime_assets_do_not_require_retired_single_prompt() -> None:
    """Let a clean bank-only runtime omit the superseded one-prompt layout."""

    required_paths = {
        asset.relative_path for asset in song_svs_runtime._CORE_RUNTIME_ASSETS
    }

    assert not any(path.startswith("prompts/elysia-v1/") for path in required_paths)


def test_prompt_bank_accepts_one_canonical_hash_pinned_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accept the fixed identity and verify every referenced local asset."""

    runtime_root = tmp_path / "runtime"
    _write_prompt_bank(runtime_root, monkeypatch)

    manifest = song_svs_runtime._verify_prompt_bank(runtime_root)

    assert manifest.schema_version == 1
    assert manifest.bank_id == "elysia-bank-v1"
    assert manifest.privacy == "private-local-only"
    assert [entry.entry_id for entry in manifest.entries] == ["synthetic-fixture-a"]
    assert manifest.entries[0].selection_rank == 0
    assert manifest.entries[0].profile.phonemes == ("zh_ai", "zh_li", "<AP>")
    assert manifest.entries[0].audio.relative_path == (
        "prompts/elysia-bank-v1/audio/synthetic-fixture-a.wav"
    )


@pytest.mark.parametrize(
    ("require_directory", "mode", "uid", "expected"),
    [
        (True, stat.S_IFDIR | 0o700, 42, True),
        (False, stat.S_IFREG | 0o600, 42, True),
        (True, stat.S_IFDIR | 0o750, 42, False),
        (False, stat.S_IFREG | 0o640, 42, False),
        (True, stat.S_IFDIR | 0o700, 7, False),
        (False, stat.S_IFDIR | 0o600, 42, False),
    ],
    ids=[
        "private-directory",
        "private-file",
        "group-visible-directory",
        "group-readable-file",
        "foreign-owner",
        "wrong-kind",
    ],
)
def test_private_prompt_metadata_policy_is_exact(
    require_directory: bool,
    mode: int,
    uid: int,
    expected: bool,
) -> None:
    """Keep owner, kind, and mode checks testable on every host platform."""

    info = SimpleNamespace(st_mode=mode, st_uid=uid)

    assert song_svs_runtime._private_prompt_metadata_matches(
        info,  # type: ignore[arg-type]
        expected_uid=42,
        require_directory=require_directory,
    ) is expected


@pytest.mark.parametrize(
    ("mode", "uid", "expected"),
    [
        (stat.S_IFDIR | 0o755, 0, True),
        (stat.S_IFREG | 0o755, 42, True),
        (stat.S_IFREG | 0o644, 42, True),
        (stat.S_IFDIR | 0o775, 42, False),
        (stat.S_IFREG | 0o646, 42, False),
        (stat.S_IFREG | 0o644, 7, False),
        (stat.S_IFLNK | 0o777, 42, False),
    ],
    ids=[
        "root-owned-directory",
        "owner-executable",
        "owner-file",
        "group-writable-directory",
        "other-writable-file",
        "foreign-owner",
        "link",
    ],
)
def test_trusted_runtime_metadata_excludes_other_account_writes(
    mode: int,
    uid: int,
    expected: bool,
) -> None:
    """Keep runtime ownership and write-permission policy platform-neutral."""

    info = SimpleNamespace(st_mode=mode, st_uid=uid)

    assert song_svs_runtime._trusted_runtime_metadata_matches(
        info,  # type: ignore[arg-type]
        expected_uid=42,
    ) is expected


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes are enforced in WSL.")
@pytest.mark.parametrize(
    ("relative_path", "mode"),
    [
        ("prompts", 0o750),
        ("prompts/elysia-bank-v1", 0o750),
        ("prompts/elysia-bank-v1/audio", 0o750),
        ("prompts/elysia-bank-v1/metadata", 0o750),
        ("prompts/elysia-bank-v1/manifest.json", 0o640),
        ("prompts/elysia-bank-v1/audio/synthetic-fixture-a.wav", 0o640),
        ("prompts/elysia-bank-v1/metadata/synthetic-fixture-a.json", 0o640),
    ],
)
def test_prompt_bank_rejects_group_visible_private_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_path: str,
    mode: int,
) -> None:
    """Reject every bank directory or file that another local account can read."""

    runtime_root = tmp_path / "runtime"
    _write_prompt_bank(runtime_root, monkeypatch)
    (runtime_root / relative_path).chmod(mode)

    _assert_failure_code(
        "unsafe_path",
        lambda: song_svs_runtime._verify_prompt_bank(runtime_root),
    )


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("entries", 0),
        ("entries", 0, "audio"),
        ("entries", 0, "metadata"),
        ("entries", 0, "profile"),
        ("entries", 0, "validation"),
    ],
    ids=["top", "entry", "audio", "metadata", "profile", "validation"],
)
def test_prompt_bank_rejects_unknown_fields_at_every_object_boundary(
    path: tuple[str | int, ...],
) -> None:
    """Fail closed when any manifest object carries an unreviewed field."""

    document = _prompt_bank_document()
    target: object = document
    for part in path:
        if isinstance(part, int):
            assert isinstance(target, list)
            target = target[part]
        else:
            assert isinstance(target, dict)
            target = target[part]
    assert isinstance(target, dict)
    target["unexpected"] = True

    _assert_failure_code(
        "runtime_integrity_failed",
        lambda: song_svs_runtime._parse_prompt_bank_manifest(
            _canonical_json(document)
        ),
    )


def test_prompt_bank_rejects_noncanonical_or_duplicate_json_fields() -> None:
    """Reject alternate encodings and duplicate keys before schema handling."""

    document = _prompt_bank_document()
    pretty = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
    duplicate = b'{"schema_version":1,"schema_version":1}'

    for encoded in (pretty, duplicate):
        _assert_failure_code(
            "runtime_integrity_failed",
            lambda encoded=encoded: song_svs_runtime._parse_prompt_bank_manifest(
                encoded
            ),
        )


def test_prompt_bank_rejects_empty_oversized_or_duplicate_entry_sets() -> None:
    """Require one to sixty-four prompts with globally unique stable IDs."""

    empty = _prompt_bank_document()
    empty["entries"] = []
    oversized = _prompt_bank_document()
    original = oversized["entries"]
    assert isinstance(original, list)
    oversized["entries"] = [copy.deepcopy(original[0]) for _ in range(65)]
    duplicate = _prompt_bank_document()
    original = duplicate["entries"]
    assert isinstance(original, list)
    original.append(copy.deepcopy(original[0]))

    for document in (empty, oversized, duplicate):
        _assert_failure_code(
            "runtime_integrity_failed",
            lambda document=document: song_svs_runtime._parse_prompt_bank_manifest(
                _canonical_json(document)
            ),
        )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), 1.0),
        (("privacy",), "local-only"),
        (("builder_sha256",), "A" * 64),
        (("entries", 0, "id"), "Uppercase-ID"),
        (("entries", 0, "role"), "fallback"),
        (("entries", 0, "selection_rank"), -1),
        (("entries", 0, "selection_rank"), 65),
        (("entries", 0, "style_proxy"), "dramatic"),
        (("entries", 0, "emotion"), "angry"),
        (("entries", 0, "audio", "path"), "../outside.wav"),
        (("entries", 0, "audio", "path"), "audio/nested/prompt.wav"),
        (("entries", 0, "metadata", "path"), "metadata/prompt.txt"),
        (("entries", 0, "audio", "bytes"), True),
        (("entries", 0, "audio", "sha256"), "4" * 63),
        (("entries", 0, "profile", "duration_seconds"), 2.99),
        (("entries", 0, "profile", "note_p10"), 80.0),
        (("entries", 0, "profile", "syllables_per_second"), 8.51),
        (("entries", 0, "validation", "source_role"), "unknown"),
        (("entries", 0, "validation", "source_role"), "emotion"),
        (("entries", 0, "validation", "transcript_sha256"), "F" * 64),
        (("entries", 0, "validation", "alignment_cost_ratio"), 1.01),
        (("entries", 0, "validation", "metadata_segments"), 2),
    ],
)
def test_prompt_bank_rejects_values_outside_the_closed_contract(
    path: tuple[str | int, ...],
    value: object,
) -> None:
    """Reject invalid identity, labels, paths, hashes, and numeric bounds."""

    document = _prompt_bank_document()
    _set_nested_value(document, path, value)

    _assert_failure_code(
        "runtime_integrity_failed",
        lambda: song_svs_runtime._parse_prompt_bank_manifest(
            _canonical_json(document)
        ),
    )


@pytest.mark.parametrize(
    "phonemes",
    [
        42,
        [],
        [""],
        [" zh_ai"],
        ["zh_ai "],
        ["x" * 65],
        ["zh_ai", "zh_ai"],
        ["p"] * 4097,
    ],
    ids=[
        "not-array",
        "empty-array",
        "empty-token",
        "leading-space",
        "trailing-space",
        "long-token",
        "duplicate-token",
        "too-many-tokens",
    ],
)
def test_prompt_bank_rejects_invalid_phoneme_arrays(phonemes: object) -> None:
    """Require one to 4096 distinct, trimmed, bounded phoneme strings."""

    document = _prompt_bank_document()
    _set_nested_value(document, ("entries", 0, "profile", "phonemes"), phonemes)

    _assert_failure_code(
        "runtime_integrity_failed",
        lambda: song_svs_runtime._parse_prompt_bank_manifest(
            _canonical_json(document)
        ),
    )


def test_prompt_bank_rejects_hardlinks_and_tampered_referenced_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require a single filesystem name and reviewed bytes for every asset."""

    runtime_root = tmp_path / "runtime"
    _, manifest_path = _write_prompt_bank(runtime_root, monkeypatch)
    audio_path = (
        runtime_root
        / "prompts"
        / "elysia-bank-v1"
        / "audio"
        / "synthetic-fixture-a.wav"
    )

    os.link(manifest_path, manifest_path.with_suffix(".linked.json"))
    _assert_failure_code(
        "unsafe_path",
        lambda: song_svs_runtime._verify_prompt_bank(runtime_root),
    )
    manifest_path.with_suffix(".linked.json").unlink()

    os.link(audio_path, audio_path.with_suffix(".linked.wav"))
    _assert_failure_code(
        "unsafe_path",
        lambda: song_svs_runtime._verify_prompt_bank(runtime_root),
    )
    audio_path.with_suffix(".linked.wav").unlink()

    audio_path.write_bytes(b"tampered")
    _assert_failure_code(
        "runtime_integrity_failed",
        lambda: song_svs_runtime._verify_prompt_bank(runtime_root),
    )


def test_offline_environment_has_no_inherited_proxy_or_network_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Construct a minimal offline child environment instead of inheriting secrets."""

    runtime_root = tmp_path / "one" / "two" / "three" / "soulx"
    source_root = runtime_root / "source"
    monkeypatch.setenv("HTTPS_PROXY", "http://secret.invalid")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-for-a-child")

    environment = song_svs_runtime._offline_environment(
        runtime_root,
        source_root,
        job_token="123e4567-e89b-42d3-a456-426614174000",
    )

    assert "HTTPS_PROXY" not in environment
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["TRANSFORMERS_OFFLINE"] == "1"
    assert environment["TORCH_FORCE_WEIGHTS_ONLY_LOAD"] == "1"
    assert environment["CUBLAS_WORKSPACE_CONFIG"] == ":4096:8"
    assert environment["NO_PROXY"] == "*"
    assert environment[song_svs_runtime._INTERNAL_JOB_TOKEN_ENV].endswith("4000")


def test_mandarin_hotword_wrapper_biases_every_funasr_generate_call() -> None:
    """Inject validated lyrics into vendor ASR without editing vendor source."""

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _Model:
        def generate(self, *args: object, **kwargs: object) -> str:
            calls.append((args, kwargs))
            return "recognized"

    class _ZhModel:
        model = _Model()

    class _Transcriber:
        zh_model = _ZhModel()

    class _Pipeline:
        lyric_transcriber = _Transcriber()

    pipeline = _Pipeline()
    song_svs_runtime._install_mandarin_hotword(pipeline, "无 瑕 歌 谣")

    result = pipeline.lyric_transcriber.zh_model.model.generate(
        "segment.wav",
        output_timestamp=True,
        hotword="untrusted override",
    )

    assert result == "recognized"
    assert calls == [
        (
            ("segment.wav",),
            {"output_timestamp": True, "hotword": "无 瑕 歌 谣"},
        )
    ]


def test_stage_entry_waits_for_exact_parent_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authorize vendor work only after the parent publishes matching identity."""

    token = "123e4567-e89b-42d3-a456-426614174000"
    stage_id = "a" * 32
    pid = 4312
    job_root = tmp_path / token
    job_root.mkdir()
    lease = song_svs_runtime._StageLease(
        version=1,
        job_token=token,
        stage="inference",
        stage_id=stage_id,
        pid=pid,
        process_group_id=pid,
        session_id=pid,
        start_time_ticks=99,
    )
    reads = iter([None, lease])
    monkeypatch.setenv(song_svs_runtime._INTERNAL_JOB_TOKEN_ENV, token)
    monkeypatch.setenv(song_svs_runtime._INTERNAL_STAGE_ID_ENV, stage_id)
    monkeypatch.setattr(song_svs_runtime.os, "getpid", lambda: pid)
    monkeypatch.setattr(
        song_svs_runtime,
        "_read_stage_lease",
        lambda _root: next(reads),
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_read_proc_identity",
        lambda _pid: ("R", pid, pid, 99),
    )
    monkeypatch.setattr(song_svs_runtime.time, "sleep", lambda _seconds: None)

    assert song_svs_runtime._authorize_stage_entry(job_root, "inference") == lease


def test_stage_entry_rejects_mismatched_or_missing_parent_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject replayed capabilities and children never registered by a parent."""

    token = "123e4567-e89b-42d3-a456-426614174000"
    stage_id = "b" * 32
    pid = 8123
    job_root = tmp_path / token
    job_root.mkdir()
    mismatched = song_svs_runtime._StageLease(
        version=1,
        job_token=token,
        stage="preprocess",
        stage_id="c" * 32,
        pid=pid,
        process_group_id=pid,
        session_id=pid,
        start_time_ticks=101,
    )
    monkeypatch.setenv(song_svs_runtime._INTERNAL_JOB_TOKEN_ENV, token)
    monkeypatch.setenv(song_svs_runtime._INTERNAL_STAGE_ID_ENV, stage_id)
    monkeypatch.setattr(song_svs_runtime.os, "getpid", lambda: pid)
    monkeypatch.setattr(
        song_svs_runtime,
        "_read_proc_identity",
        lambda _pid: ("R", pid, pid, 101),
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_read_stage_lease",
        lambda _root: mismatched,
    )
    _assert_failure_code(
        "stage_control_failed",
        lambda: song_svs_runtime._authorize_stage_entry(job_root, "inference"),
    )

    clock = iter([0.0, 6.0])
    monkeypatch.setattr(song_svs_runtime, "_read_stage_lease", lambda _root: None)
    monkeypatch.setattr(song_svs_runtime.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(song_svs_runtime.time, "sleep", lambda _seconds: None)
    _assert_failure_code(
        "stage_control_failed",
        lambda: song_svs_runtime._authorize_stage_entry(job_root, "inference"),
    )


def test_quiet_child_uses_argv_no_shell_and_discards_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Launch reviewed child argv without a shell or path-bearing diagnostics."""

    captured: dict[str, object] = {"lease": None}
    waits: list[float | None] = []
    token = "123e4567-e89b-42d3-a456-426614174000"
    job_root = tmp_path / token
    job_root.mkdir()

    class _Process:
        """Model one successful session leader without launching a process."""

        pid = 4312

        def __init__(self, command: list[str], **kwargs: object) -> None:
            """Capture the closed launch contract."""

            captured["command"] = command
            captured.update(kwargs)

        def wait(self, timeout: float | None = None) -> int:
            """Return successful completion for both waits."""

            waits.append(timeout)
            return 0

    lease = song_svs_runtime._StageLease(
        version=1,
        job_token=token,
        stage="preprocess",
        stage_id="a" * 32,
        pid=_Process.pid,
        process_group_id=_Process.pid,
        session_id=_Process.pid,
        start_time_ticks=99,
    )

    @contextmanager
    def _lock(_job_root: Path) -> Iterator[None]:
        """Replace the Linux advisory lock in this cross-platform unit test."""

        yield

    monkeypatch.setattr(song_svs_runtime.subprocess, "Popen", _Process)
    monkeypatch.setattr(song_svs_runtime, "_stage_control_lock", _lock)
    monkeypatch.setattr(song_svs_runtime, "_raise_if_cancelled", lambda _root: None)
    monkeypatch.setattr(
        song_svs_runtime,
        "_capture_stage_lease",
        lambda *_args, **_kwargs: lease,
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_write_stage_lease",
        lambda _root, value: captured.__setitem__("lease", value),
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_read_stage_lease",
        lambda _root: captured["lease"],
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_terminate_stage_session",
        lambda value: captured.__setitem__("terminated", value),
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_clear_stage_lease",
        lambda _root, _stage_id: captured.__setitem__("lease", None),
    )
    command = ["/fixed/python", "-B", "-c", "pass"]

    result = song_svs_runtime._run_quiet_process(
        command,
        job_root=job_root,
        job_token=token,
        stage="preprocess",
        cwd=tmp_path,
        environment={"SAFE": "1"},
        timeout_seconds=9,
    )

    assert result == 0
    assert captured["command"] == command
    assert captured["cwd"] == str(tmp_path)
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["SAFE"] == "1"
    assert environment[song_svs_runtime._INTERNAL_JOB_TOKEN_ENV] == token
    assert song_svs_runtime._STAGE_ID_PATTERN.fullmatch(
        environment[song_svs_runtime._INTERNAL_STAGE_ID_ENV]
    )
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.DEVNULL
    assert captured["shell"] is False
    assert captured["start_new_session"] is True
    assert captured["terminated"] == lease
    assert captured["lease"] is None


def test_cleanup_terminates_registered_session_before_clearing_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bind cleanup to one UUID lease and prevent any subsequent stage launch."""

    runtime_root = tmp_path / "runtime"
    token = "123e4567-e89b-42d3-a456-426614174000"
    job_root = _create_managed_job(runtime_root, token)
    lease = song_svs_runtime._StageLease(
        version=song_svs_runtime._STAGE_LEASE_VERSION,
        job_token=token,
        stage="inference",
        stage_id="b" * 32,
        pid=8123,
        process_group_id=8123,
        session_id=8123,
        start_time_ticks=444,
    )
    song_svs_runtime._write_stage_lease(job_root, lease)
    terminated: list[song_svs_runtime._StageLease] = []

    @contextmanager
    def _lock(_job_root: Path) -> Iterator[None]:
        """Replace Linux flock while preserving cleanup ordering."""

        yield

    monkeypatch.setattr(song_svs_runtime, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(song_svs_runtime, "_stage_control_lock", _lock)
    monkeypatch.setattr(
        song_svs_runtime,
        "_terminate_stage_session",
        lambda value: terminated.append(value),
    )
    monkeypatch.setattr(song_svs_runtime, "_stage_session_members", lambda _value: [])

    song_svs_runtime._terminate_job_processes_for_cleanup(job_root)

    assert terminated == [lease]
    assert not (job_root / song_svs_runtime._STAGE_LEASE_NAME).exists()
    assert (job_root / song_svs_runtime._CANCEL_REQUEST_NAME).is_file()
    _assert_failure_code(
        "cancelled",
        lambda: song_svs_runtime._raise_if_cancelled(job_root),
    )


def test_stage_timeout_terminates_exact_lease_before_reporting_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escalate a timed-out stage through its recorded session identity."""

    token = "123e4567-e89b-42d3-a456-426614174000"
    job_root = tmp_path / token
    job_root.mkdir()
    lease = song_svs_runtime._StageLease(
        version=1,
        job_token=token,
        stage="inference",
        stage_id="c" * 32,
        pid=9224,
        process_group_id=9224,
        session_id=9224,
        start_time_ticks=555,
    )
    state: dict[str, song_svs_runtime._StageLease | None] = {"lease": None}
    terminated: list[song_svs_runtime._StageLease] = []

    class _TimedOutProcess:
        """Model a child that exceeds its stage budget and is then reaped."""

        pid = lease.pid

        def __init__(self, _command: list[str], **_kwargs: object) -> None:
            """Create the deterministic fake process."""

            self.wait_count = 0

        def wait(self, timeout: float | None = None) -> int:
            """Time out once, then report signal termination during reap."""

            self.wait_count += 1
            if self.wait_count == 1:
                raise subprocess.TimeoutExpired(
                    "stage",
                    0.0 if timeout is None else timeout,
                )
            return -9

    @contextmanager
    def _lock(_job_root: Path) -> Iterator[None]:
        """Replace Linux flock for a deterministic timeout test."""

        yield

    monkeypatch.setattr(song_svs_runtime.subprocess, "Popen", _TimedOutProcess)
    monkeypatch.setattr(song_svs_runtime, "_stage_control_lock", _lock)
    monkeypatch.setattr(song_svs_runtime, "_raise_if_cancelled", lambda _root: None)
    monkeypatch.setattr(
        song_svs_runtime,
        "_capture_stage_lease",
        lambda *_args, **_kwargs: lease,
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_write_stage_lease",
        lambda _root, value: state.__setitem__("lease", value),
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_read_stage_lease",
        lambda _root: state["lease"],
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_terminate_stage_session",
        lambda value: terminated.append(value),
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_clear_stage_lease",
        lambda _root, _stage_id: state.__setitem__("lease", None),
    )

    _assert_failure_code(
        "stage_timeout",
        lambda: song_svs_runtime._run_quiet_process(
            ["/fixed/python", "-c", "pass"],
            job_root=job_root,
            job_token=token,
            stage="inference",
            cwd=tmp_path,
            environment={},
            timeout_seconds=7,
        ),
    )

    assert terminated == [lease]
    assert state["lease"] is None


def test_shared_runtime_budget_caps_each_stage_and_expires_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep stage limits subordinate to one documented WSL wall-clock budget."""

    now = 500.0
    monkeypatch.setattr(song_svs_runtime.time, "monotonic", lambda: now)

    assert song_svs_runtime._bounded_stage_timeout(
        now + 120,
        300,
    ) == 120
    assert song_svs_runtime._bounded_stage_timeout(
        now + 300,
        120,
    ) == 120
    _assert_failure_code(
        "stage_timeout",
        lambda: song_svs_runtime._bounded_stage_timeout(now, 120),
    )
    assert (
        song_svs_runtime._PREPROCESS_TIMEOUT_SECONDS
        + song_svs_runtime._INFERENCE_TIMEOUT_SECONDS
        == song_svs_runtime._TOTAL_RUNTIME_TIMEOUT_SECONDS
    )


def test_perform_inference_stage_builds_request_only_from_verified_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep model, prompts, score, and output inside the fixed private contract."""

    from scripts import song_svs_inference

    runtime_root = tmp_path / "runtime"
    source_root = runtime_root / "source"
    model_path = runtime_root / "models" / "SoulX-Singer" / "model.pt"
    config_path = source_root / "soulxsinger" / "config" / "soulxsinger.yaml"
    phoneset_path = (
        source_root
        / "soulxsinger"
        / "utils"
        / "phoneme"
        / "phone_set.json"
    )
    for path in (model_path, config_path, phoneset_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"reviewed")
    _write_prompt_bank(runtime_root, monkeypatch)
    prompt_bank = song_svs_runtime._verify_prompt_bank(runtime_root)

    job_root = tmp_path / "job"
    work_root = job_root / ".svs-work"
    output_root = work_root / "generated"
    output_root.mkdir(parents=True)
    target_vocal = work_root / "target_vocal.wav"
    _write_pcm_wave(target_vocal)
    (work_root / "target.corrected.json").write_text(
        '[{"text":"reviewed score"}]',
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def _run(request: object) -> object:
        captured["request"] = request
        generated = output_root / "generated.wav"
        _write_pcm_wave(generated)
        return SimpleNamespace(output_path=generated)

    monkeypatch.setattr(song_svs_inference, "run_svs_inference", _run)

    generated = song_svs_runtime._perform_inference_stage(
        job_root,
        runtime_root,
        source_root,
        prompt_bank,
    )

    request = captured["request"]
    assert isinstance(request, song_svs_inference.SvsInferenceRequest)
    assert request.model_path == model_path
    assert request.config_path == config_path
    assert request.phoneset_path == phoneset_path
    assert request.output_directory == output_root
    assert request.total_samples == 30_000
    assert request.target_audio_sha256 == song_svs_runtime._sha256(target_vocal)
    assert [prompt.prompt_id for prompt in request.prompts] == [
        "synthetic-fixture-a"
    ]
    assert request.prompts[0].audio.path.is_absolute()
    assert "elysia-v1" not in str(request.prompts[0].audio.path)
    assert generated == output_root / "generated.wav"


def test_invoke_inference_uses_only_fixed_quiet_child_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not expose vendor CLI flags or renderer-controlled prompt paths."""

    runtime_root = tmp_path / "one" / "two" / "three" / "soulx"
    source_root = runtime_root / "source"
    source_root.mkdir(parents=True)
    token = "123e4567-e89b-42d3-a456-426614174000"
    job_root = runtime_root / "jobs" / token
    (job_root / ".svs-work").mkdir(parents=True)
    captured: dict[str, object] = {}

    def _run(command: list[str], **options: object) -> int:
        captured["command"] = command
        captured.update(options)
        generated = job_root / ".svs-work" / "generated" / "generated.wav"
        _write_pcm_wave(generated)
        return 0

    monkeypatch.setattr(song_svs_runtime, "_run_quiet_process", _run)

    generated = song_svs_runtime._invoke_inference(
        job_root,
        runtime_root,
        source_root,
        timeout_seconds=123,
    )

    command = captured["command"]
    assert isinstance(command, list)
    assert command[:3] == [
        str(runtime_root / "infer-env" / "bin" / "python"),
        "-B",
        "-c",
    ]
    assert "_inference_stage_entry" in command[3]
    assert "--auto_shift" not in command
    assert "--prompt_wav_path" not in command
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment[song_svs_runtime._INTERNAL_JOB_TOKEN_ENV] == token
    assert generated.name == "generated.wav"


def test_inference_child_collapses_private_failures_to_fixed_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent model, prompt, CUDA, and path details from leaving the child."""

    runtime_root = tmp_path / "runtime"
    job_root = tmp_path / "job"
    source_root = tmp_path / "source"
    reached_inference = False
    monkeypatch.setenv(song_svs_runtime._INTERNAL_JOB_TOKEN_ENV, "fixed-token")
    monkeypatch.setattr(song_svs_runtime, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(
        song_svs_runtime,
        "_job_root_from_token",
        lambda _root, _token: job_root,
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_authorize_stage_entry",
        lambda _root, _stage: None,
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_verify_runtime_assets",
        lambda _root: _prompt_bank_document(),
    )
    monkeypatch.setattr(
        song_svs_runtime,
        "_verify_source_checkout",
        lambda _root: source_root,
    )
    def _fail_inside_inference(*_args: object) -> None:
        """Prove the child reaches the private operation before collapsing it."""

        nonlocal reached_inference
        reached_inference = True
        raise RuntimeError("/private/model.pt")

    monkeypatch.setattr(
        song_svs_runtime,
        "_perform_inference_stage",
        _fail_inside_inference,
    )

    with pytest.raises(SystemExit) as captured:
        song_svs_runtime._inference_stage_entry()

    assert reached_inference is True
    assert captured.value.code == song_svs_runtime._INFERENCE_EXIT_CODE


def test_run_job_publishes_fixed_vocal_and_cleans_intermediates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Orchestrate mocked prep and inference through the closed file contract."""

    runtime_root = tmp_path / "runtime"
    token = "123e4567-e89b-42d3-a456-426614174000"
    job_root = _create_managed_job(runtime_root, token)
    source_root = runtime_root / "source"
    source_root.mkdir()
    events: list[tuple[str, int]] = []

    monkeypatch.setattr(song_svs_runtime, "_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(song_svs_runtime, "_verify_runtime_assets", lambda _root: None)
    monkeypatch.setattr(
        song_svs_runtime,
        "_verify_source_checkout",
        lambda _root: source_root,
    )

    def _preprocess(
        actual_token: str,
        _runtime_root: Path,
        _source_root: Path,
        *,
        timeout_seconds: int,
    ) -> None:
        assert actual_token == token
        assert 1 <= timeout_seconds <= song_svs_runtime._PREPROCESS_TIMEOUT_SECONDS
        corrected = job_root / ".svs-work" / "target.corrected.json"
        corrected.write_text('[{"text":"无 瑕"}]', encoding="utf-8")

    def _infer(
        actual_job: Path,
        _runtime_root: Path,
        _source_root: Path,
        *,
        timeout_seconds: int,
    ) -> Path:
        assert actual_job == job_root
        assert 1 <= timeout_seconds <= song_svs_runtime._INFERENCE_TIMEOUT_SECONDS
        generated = actual_job / ".svs-work" / "generated" / "generated.wav"
        generated.parent.mkdir()
        _write_pcm_wave(generated)
        return generated

    monkeypatch.setattr(song_svs_runtime, "_invoke_preprocess", _preprocess)
    monkeypatch.setattr(song_svs_runtime, "_invoke_inference", _infer)
    monkeypatch.setattr(
        song_svs_runtime,
        "_emit",
        lambda stage, progress, _message, **_kwargs: events.append((stage, progress)),
    )

    song_svs_runtime._run_job(str(job_root))

    output = job_root / "generated_vocal.wav"
    assert output.is_file()
    assert song_svs_runtime._probe_wave(output, label="Published vocal") == pytest.approx(
        1.25
    )
    assert not (job_root / ".svs-work").exists()
    assert events == [
        ("validating", 4),
        ("transcribing", 18),
        ("aligning", 52),
        ("synthesizing", 58),
        ("ready", 100),
    ]


def test_main_collapses_unexpected_errors_without_leaking_paths(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Hide vendor exception details behind one bounded structured error event."""

    secret = "/home/private/models/secret-model.pt"

    def _raise(_job_root: str) -> None:
        raise RuntimeError(secret)

    monkeypatch.setattr(song_svs_runtime, "_run_job", _raise)

    assert song_svs_runtime.main(["/managed/job"]) == 1
    output = capsys.readouterr().out.strip()
    assert output.startswith(song_svs_runtime._EVENT_PREFIX)
    assert secret not in output
    payload = json.loads(output.removeprefix(song_svs_runtime._EVENT_PREFIX))
    assert payload["stage"] == "error"
    assert payload["errorCode"] == "runtime_failed"


def test_main_rejects_flags_and_missing_job_root(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Expose exactly one positional managed-root argument and no override flags."""

    assert song_svs_runtime.main([]) == 2
    first = capsys.readouterr().out
    assert '"errorCode":"invalid_request"' in first

    assert song_svs_runtime.main(["--model", "untrusted.pt"]) == 2
    second = capsys.readouterr().out
    assert '"errorCode":"invalid_request"' in second
