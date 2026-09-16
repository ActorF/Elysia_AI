"""Test the isolated GPT-SoVITS worker without importing or loading models."""

from __future__ import annotations

from array import array
import ast
from io import BytesIO, TextIOWrapper
import hashlib
import json
import ntpath
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace
import wave
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pytest

from scripts import gpt_sovits_worker as worker
from scripts.gpt_sovits_protocol import (
    FrameKind,
    ProtocolFrame,
    read_frame,
    write_frame,
)


_CHALLENGE = "a" * 64


def _stable_worker_config() -> worker._WorkerConfig:
    r"""Return a synthetic config using guarded ``\\?\Volume`` spellings."""

    root = Path(
        "\\\\?\\Volume{11111111-1111-1111-1111-111111111111}\\runtime"
    )

    def descriptor(relative_path: str) -> worker._AssetDescriptor:
        """Create one structurally valid fake descriptor below the volume."""

        return worker._AssetDescriptor(
            Path(ntpath.join(str(root), relative_path)), 16, "b" * 64
        )

    return worker._WorkerConfig(
        _CHALLENGE,
        root,
        descriptor("private.ckpt"),
        descriptor("private.pth"),
        descriptor("reference.wav"),
        "prompt",
        "en",
        1000,
        42,
        "cpu",
        "c" * 64,
        "d" * 64,
        Path("Q:\\runtime"),
    )


def _asset(path: Path, content: bytes) -> Dict[str, object]:
    """Return one exact asset descriptor for bytes already written to disk."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "bytes": len(content),
        "path": os.path.normpath(str(path)),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _init_metadata(tmp_path: Path) -> Dict[str, object]:
    """Create one complete valid INIT document and its fake local assets."""

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True)
    for index, (_role, relative_path) in enumerate(
        worker._RUNTIME_MANIFEST_RELATIVE_FILES
    ):
        _asset(
            runtime_root / Path(relative_path),
            ("runtime-anchor-{0}".format(index)).encode("ascii"),
        )
    return {
        "schema": 1,
        "challenge": _CHALLENGE,
        "runtime_root": os.path.normpath(str(runtime_root)),
        "gpt_weights": _asset(tmp_path / "assets" / "voice.ckpt", b"gpt-checkpoint"),
        "sovits_weights": _asset(tmp_path / "assets" / "voice.pth", b"sovits-checkpoint"),
        "reference_audio": _asset(
            tmp_path / "assets" / "reference.wav",
            b"reference-audio-data",
        ),
        "prompt_text": "A private reference prompt.",
        "prompt_language": "en",
        "speed_milli": 1000,
        "seed": 42,
        "device": "cpu",
        "runtime_manifest_digest": worker.compute_runtime_manifest_digest(
            runtime_root
        ),
    }


def _request_bytes(frames: Iterable[ProtocolFrame]) -> bytes:
    """Serialize a sequence with the public protocol sender implementation."""

    stream = BytesIO()
    for frame in frames:
        write_frame(stream, frame)
    return stream.getvalue()


def _response_frames(encoded: bytes) -> List[ProtocolFrame]:
    """Decode every complete response frame in one in-memory pipe capture."""

    stream = BytesIO(encoded)
    frames = []
    while stream.tell() < len(encoded):
        frames.append(read_frame(stream))
    return frames


def _one_audio(
    sample_rate: int = 32000,
    samples: Optional[object] = None,
) -> object:
    """Return one generator yielding a fake native int16 waveform exactly once."""

    selected = array("h", (1, -2, 3)) if samples is None else samples

    def generate() -> object:
        """Yield the configured sample tuple through the upstream shape."""

        yield sample_rate, selected

    return generate()


class _FakeEngine:
    """Record worker calls and return injectable inference iterables."""

    def __init__(
        self,
        results: Optional[List[object]] = None,
        error: Optional[BaseException] = None,
        close_error: Optional[BaseException] = None,
    ) -> None:
        """Store deterministic fake inference and cleanup behavior."""

        self.results = list(results or [_one_audio()])
        self.error = error
        self.close_error = close_error
        self.calls = []  # type: List[Tuple[str, str]]
        self.close_calls = 0

    def synthesize(self, text: str, text_language: str) -> object:
        """Record one request and return or raise the configured fake result."""

        self.calls.append((text, text_language))
        if self.error is not None:
            raise self.error
        if not self.results:
            raise AssertionError("worker retried inference")
        return self.results.pop(0)

    def close(self) -> None:
        """Count worker cleanup and optionally model a native cleanup failure."""

        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class _Factory:
    """Capture the sensitive worker configuration without rendering it."""

    def __init__(self, engine: _FakeEngine) -> None:
        """Store the one engine that a session is allowed to construct."""

        self.engine = engine
        self.calls = []  # type: List[Any]

    def __call__(self, config: object) -> _FakeEngine:
        """Capture the binding and return the configured fake engine."""

        self.calls.append(config)
        return self.engine


class _FakeFloatArray:
    """Expose the sole ndarray method used by the guarded decoder adapter."""

    def __init__(self, value: object) -> None:
        """Retain the marker returned by a fake ``frombuffer`` call."""

        self.value = value

    def flatten(self) -> Tuple[str, object]:
        """Return a visible marker without importing NumPy."""

        return "flattened", self.value


class _FakeNumpy:
    """Record conversion of decoder bytes without loading native NumPy."""

    float32 = "float32"

    def __init__(self) -> None:
        """Create an empty call ledger."""

        self.calls = []  # type: List[Tuple[bytes, object]]

    def frombuffer(self, value: bytes, dtype: object) -> _FakeFloatArray:
        """Capture bytes and dtype before returning a flattenable fake."""

        self.calls.append((value, dtype))
        return _FakeFloatArray(value)


class _FakeDecoderProcess:
    """Model the bounded ``Popen`` surface used for one FFmpeg invocation."""

    def __init__(
        self,
        output: bytes,
        *,
        return_code: int = 0,
        wait_timeout: bool = False,
    ) -> None:
        """Store deterministic stdout, exit, timeout, and reaping behavior."""

        self.stdout = BytesIO(output)
        self.return_code = return_code
        self.wait_timeout = wait_timeout
        self.wait_calls = []  # type: List[float]
        self.killed = False
        self.finished = False

    def poll(self) -> Optional[int]:
        """Report a running process until wait or kill completes it."""

        return self.return_code if self.finished else None

    def wait(self, timeout: float) -> int:
        """Return the configured code or model a bounded first wait timeout."""

        self.wait_calls.append(timeout)
        if self.wait_timeout and not self.killed:
            raise subprocess.TimeoutExpired("guarded-ffmpeg", timeout)
        self.finished = True
        return self.return_code

    def kill(self) -> None:
        """Record forceful cleanup and make the next reap succeed."""

        self.killed = True
        self.finished = True


class _PartialReader:
    """Limit each protocol read without changing the byte sequence."""

    def __init__(self, data: bytes, maximum: int = 3) -> None:
        """Store one source and a positive artificial read size."""

        self._stream = BytesIO(data)
        self._maximum = maximum

    def read(self, size: int = -1) -> bytes:
        """Read at most the configured number of bytes."""

        selected = self._maximum if size < 0 else min(size, self._maximum)
        return self._stream.read(selected)


class _PartialWriter:
    """Limit each protocol write and expose the resulting complete bytes."""

    def __init__(self, maximum: int = 4) -> None:
        """Create an output capture and positive artificial write size."""

        self.output = BytesIO()
        self._maximum = maximum
        self.flush_calls = 0

    def write(self, data: object) -> int:
        """Write one bounded prefix of a bytes-like protocol fragment."""

        view = memoryview(data)  # type: ignore[arg-type]
        count = min(len(view), self._maximum)
        self.output.write(view[:count])
        return count

    def flush(self) -> None:
        """Record the worker's response publication boundary."""

        self.flush_calls += 1


class _FailingWriter:
    """Fail every response write with a deliberately sensitive native detail."""

    def __init__(self) -> None:
        """Start with no attempted response writes."""

        self.write_calls = 0

    def write(self, _data: object) -> int:
        """Raise before accepting any protocol bytes."""

        self.write_calls += 1
        raise OSError("private response pipe path")

    def flush(self) -> None:
        """Provide the expected stream surface if no write occurs."""


class _PrefixThenFailWriter:
    """Accept one frame prefix and fail every later write attempt."""

    def __init__(self) -> None:
        """Create one inspectable partial response and attempt counter."""

        self.output = bytearray()
        self.write_calls = 0

    def write(self, data: object) -> int:
        """Accept five bytes once, then model a broken response pipe."""

        self.write_calls += 1
        if self.write_calls > 1:
            raise OSError("private partial response pipe path")
        prefix = bytes(memoryview(data)[:5])  # type: ignore[arg-type]
        self.output.extend(prefix)
        return len(prefix)


def _run(
    metadata: Dict[str, object],
    trailing: Iterable[ProtocolFrame],
    engine: Optional[_FakeEngine] = None,
) -> Tuple[int, List[ProtocolFrame], _Factory]:
    """Run one complete in-memory worker session and decode its responses."""

    selected_engine = engine or _FakeEngine()
    factory = _Factory(selected_engine)
    requests = [ProtocolFrame(FrameKind.INIT, 0, metadata)] + list(trailing)
    response = BytesIO()
    status = worker.run_worker(BytesIO(_request_bytes(requests)), response, factory)
    return status, _response_frames(response.getvalue()), factory


def test_happy_session_attests_binding_and_emits_complete_pcm_wav(
    tmp_path: Path,
) -> None:
    """Serve multiple increasing IDs, then close before acknowledging STOP."""

    metadata = _init_metadata(tmp_path)
    engine = _FakeEngine([_one_audio(16000), _one_audio(24000)])
    status, responses, factory = _run(
        metadata,
        [
            ProtocolFrame(
                FrameKind.SYNTHESIZE,
                1,
                {
                    "schema": 1,
                    "challenge": _CHALLENGE,
                    "text": "hello",
                    "text_language": "en",
                },
            ),
            ProtocolFrame(
                FrameKind.SYNTHESIZE,
                9,
                {
                    "schema": 1,
                    "challenge": _CHALLENGE,
                    "text": "你好",
                    "text_language": "zh",
                },
            ),
            ProtocolFrame(
                FrameKind.STOP,
                0,
                {"schema": 1, "challenge": _CHALLENGE},
            ),
        ],
        engine,
    )

    assert status == 0
    assert [frame.kind for frame in responses] == [
        FrameKind.READY,
        FrameKind.AUDIO,
        FrameKind.AUDIO,
        FrameKind.STOPPED,
    ]
    assert responses[0].metadata == {
        "binding_sha256": factory.calls[0].binding_sha256,
        "challenge": _CHALLENGE,
        "schema": 1,
    }
    assert responses[1].request_id == 1
    assert responses[2].request_id == 9
    assert engine.calls == [("hello", "en"), ("你好", "zh")]
    assert engine.close_calls == 1
    for frame, expected_rate in zip(responses[1:3], (16000, 24000)):
        assert frame.metadata == {
            "challenge": _CHALLENGE,
            "format": "wav",
            "sample_rate": expected_rate,
            "schema": 1,
        }
        with wave.open(BytesIO(frame.payload), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == expected_rate
            assert wav_file.readframes(wav_file.getnframes()) == array(
                "h", (1, -2, 3)
            ).tobytes()


def test_binding_digest_uses_domain_separated_canonical_init(
    tmp_path: Path,
) -> None:
    """Pin the attestation algorithm independently of the worker helper."""

    metadata = _init_metadata(tmp_path)
    status, responses, _factory = _run(
        metadata,
        [ProtocolFrame(FrameKind.STOP, 0, {"schema": 1, "challenge": _CHALLENGE})],
    )
    canonical = dict(metadata)
    canonical.pop("challenge")
    expected = hashlib.sha256(
        b"ELYTTS-BINDING-V1\0"
        + json.dumps(
            canonical,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    assert status == 0
    assert responses[0].metadata["binding_sha256"] == expected


def test_partial_protocol_reads_and_writes_are_supported(tmp_path: Path) -> None:
    """Preserve framing when both OS handles make only partial progress."""

    metadata = _init_metadata(tmp_path)
    requests = _request_bytes(
        [
            ProtocolFrame(FrameKind.INIT, 0, metadata),
            ProtocolFrame(
                FrameKind.SYNTHESIZE,
                2,
                {
                    "schema": 1,
                    "challenge": _CHALLENGE,
                    "text": "partial",
                    "text_language": "auto",
                },
            ),
            ProtocolFrame(FrameKind.STOP, 0, {"schema": 1, "challenge": _CHALLENGE}),
        ]
    )
    response = _PartialWriter()

    status = worker.run_worker(
        _PartialReader(requests),  # type: ignore[arg-type]
        response,  # type: ignore[arg-type]
        _Factory(_FakeEngine()),
    )

    assert status == 0
    assert [frame.kind for frame in _response_frames(response.output.getvalue())] == [
        FrameKind.READY,
        FrameKind.AUDIO,
        FrameKind.STOPPED,
    ]
    assert response.flush_calls == 3


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(schema=2),
        lambda value: value.update(challenge="A" * 64),
        lambda value: value.update(challenge="a" * 63),
        lambda value: value.update(extra="not-allowed"),
        lambda value: value.pop("device"),
        lambda value: value.update(device="cuda:0"),
        lambda value: value.update(speed_milli=True),
        lambda value: value.update(runtime_manifest_digest="private-path"),
    ],
)
def test_init_schema_challenge_and_exact_fields_fail_closed(
    tmp_path: Path,
    mutate: Callable[[Dict[str, object]], object],
) -> None:
    """Poison malformed INIT metadata before constructing an engine."""

    metadata = _init_metadata(tmp_path)
    mutate(metadata)
    factory = _Factory(_FakeEngine())
    response = BytesIO()

    status = worker.run_worker(
        BytesIO(_request_bytes([ProtocolFrame(FrameKind.INIT, 0, metadata)])),
        response,
        factory,
    )

    frames = _response_frames(response.getvalue())
    assert status == 1
    assert len(frames) == 1
    assert frames[0].kind is FrameKind.ERROR
    assert frames[0].metadata == {"code": "protocol_invalid"}
    assert factory.calls == []


def test_first_frame_must_be_init_and_is_never_resynchronized() -> None:
    """Reject the first unexpected kind even if a plausible frame follows it."""

    requests = _request_bytes(
        [
            ProtocolFrame(FrameKind.STOP, 0, {}),
            ProtocolFrame(FrameKind.INIT, 0, {}),
        ]
    )
    response = BytesIO()

    status = worker.run_worker(BytesIO(requests), response, _Factory(_FakeEngine()))

    assert status == 1
    assert [frame.metadata for frame in _response_frames(response.getvalue())] == [
        {"code": "protocol_invalid"}
    ]


@pytest.mark.parametrize(
    "change",
    [
        "size",
        "hash",
    ],
)
def test_asset_size_and_hash_mismatch_prevent_engine_construction(
    tmp_path: Path,
    change: str,
) -> None:
    """Refuse declarations that do not identify the bytes opened by the worker."""

    metadata = _init_metadata(tmp_path)
    descriptor = metadata["gpt_weights"]
    assert isinstance(descriptor, dict)
    if change == "size":
        descriptor["bytes"] = int(descriptor["bytes"]) + 1
    else:
        descriptor["sha256"] = "0" * 64

    status, responses, factory = _run(metadata, [])

    assert status == 1
    assert responses[0].metadata == {"code": "binding_failed"}
    assert factory.calls == []


def test_symlinked_asset_is_rejected_before_hashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a leaf symlink rather than attesting its mutable target."""

    metadata = _init_metadata(tmp_path)
    real = tmp_path / "assets" / "real.ckpt"
    real.write_bytes(b"linked-private-model")
    linked = tmp_path / "assets" / "linked.ckpt"
    try:
        linked.symlink_to(real)
    except OSError:
        original_lstat = worker.os.lstat

        def simulated_lstat(path: str) -> object:
            """Return symlink mode for the selected path on restricted Windows."""

            if os.path.normcase(path) == os.path.normcase(str(linked)):
                return SimpleNamespace(st_mode=stat.S_IFLNK)
            return original_lstat(path)

        monkeypatch.setattr(worker.os, "lstat", simulated_lstat)
    metadata["gpt_weights"] = {
        "bytes": real.stat().st_size,
        "path": os.path.normpath(str(linked)),
        "sha256": hashlib.sha256(real.read_bytes()).hexdigest(),
    }

    status, responses, factory = _run(metadata, [])

    assert status == 1
    assert responses[0].metadata == {"code": "binding_failed"}
    assert factory.calls == []


def test_reparse_marked_asset_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat Windows reparse files as links even when their mode is regular."""

    metadata = _init_metadata(tmp_path)
    target = Path(str(metadata["gpt_weights"]["path"]))  # type: ignore[index]
    target_inode = os.lstat(str(target)).st_ino
    original = worker._is_reparse

    def marked(info: os.stat_result) -> bool:
        """Mark only the selected fake checkpoint as a reparse point."""

        return info.st_ino == target_inode or original(info)

    monkeypatch.setattr(worker, "_is_reparse", marked)

    status, responses, factory = _run(metadata, [])

    assert status == 1
    assert responses[0].metadata == {"code": "binding_failed"}
    assert factory.calls == []


def test_asset_identity_change_during_stream_hash_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a descriptor identity that changes between bracketed fstats."""

    metadata = _init_metadata(tmp_path)
    target = Path(str(metadata["gpt_weights"]["path"]))  # type: ignore[index]
    target_inode = os.lstat(str(target)).st_ino
    original = worker._stat_identity
    seen = 0

    def changing(info: os.stat_result) -> Tuple[int, int, int, int, int, int]:
        """Alter the selected inode identity only after hashing has begun."""

        nonlocal seen
        identity = original(info)
        if info.st_ino == target_inode:
            seen += 1
            if seen >= 3:
                return identity[:3] + (identity[3] + 1,) + identity[4:]
        return identity

    monkeypatch.setattr(worker, "_stat_identity", changing)

    status, responses, factory = _run(metadata, [])

    assert status == 1
    assert responses[0].metadata == {"code": "binding_failed"}
    assert factory.calls == []


def test_runtime_manifest_is_recomputed_and_rejects_changed_anchor(
    tmp_path: Path,
) -> None:
    """Refuse READY when one executable/model anchor changes after declaration."""

    metadata = _init_metadata(tmp_path)
    runtime_root = Path(str(metadata["runtime_root"]))
    relative_path = worker._RUNTIME_MANIFEST_RELATIVE_FILES[0][1]
    anchor = runtime_root / Path(relative_path)
    original = anchor.read_bytes()
    anchor.write_bytes(b"X" * len(original))

    status, responses, factory = _run(metadata, [])

    assert status == 1
    assert responses[0].metadata == {"code": "binding_failed"}
    assert factory.calls == []


def test_runtime_manifest_digest_does_not_depend_on_install_path(
    tmp_path: Path,
) -> None:
    """Use logical roles and bytes so equivalent installs share one identity."""

    first = _init_metadata(tmp_path / "first")
    second = _init_metadata(tmp_path / "second")

    assert first["runtime_manifest_digest"] == second["runtime_manifest_digest"]


def test_runtime_manifest_accepts_explicit_stable_worker_protocol_paths(
    tmp_path: Path,
) -> None:
    """Hash caller-pinned script paths without resolving their namespace."""

    metadata = _init_metadata(tmp_path)
    runtime_root = Path(str(metadata["runtime_root"]))
    script_root = tmp_path / "guarded-scripts"
    script_root.mkdir()
    worker_path = script_root / "gpt_sovits_worker.py"
    protocol_path = script_root / "gpt_sovits_protocol.py"
    worker_path.write_bytes(worker._SCRIPT_PATH.read_bytes())
    protocol_path.write_bytes(worker._PROTOCOL_PATH.read_bytes())

    explicit = worker.compute_runtime_manifest_digest(
        runtime_root,
        worker_path=worker_path,
        protocol_path=protocol_path,
    )
    default = worker.compute_runtime_manifest_digest(runtime_root)

    assert explicit == default
    with pytest.raises(worker._WorkerFailure) as raised:
        worker.compute_runtime_manifest_digest(
            runtime_root,
            worker_path=worker_path,
            protocol_path=tmp_path / "different" / "gpt_sovits_protocol.py",
        )
    assert raised.value.code == "binding_failed"


def test_lexical_module_path_preserves_volume_guid_spelling() -> None:
    r"""Do not collapse a guarded ``\\?\Volume`` path back to a drive letter."""

    stable = Path(
        "\\\\?\\Volume{11111111-1111-1111-1111-111111111111}\\"
        "project\\scripts\\gpt_sovits_worker.py"
    )

    assert worker._require_lexical_module_path(str(stable)) == stable


def test_import_root_requires_the_same_fixed_volume_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a drive alias unless its sole target matches INIT's volume."""

    config = _stable_worker_config()
    targets = {
        "Volume{11111111-1111-1111-1111-111111111111}": (
            "\\Device\\HarddiskVolume7",
        ),
        "Q:": ("\\Device\\HarddiskVolume7",),
    }
    monkeypatch.setattr(
        worker, "_query_dos_device", lambda name: targets.get(name, ())
    )

    assert worker._require_mapped_import_root(
        "Q:\\runtime", config.runtime_root
    ) == Path("Q:\\runtime")
    for invalid in ("C:\\runtime", "Q:\\other", "Q:\\runtime\\..\\runtime"):
        with pytest.raises(worker._WorkerFailure) as raised:
            worker._require_mapped_import_root(invalid, config.runtime_root)
        assert raised.value.code == "engine_failed"

    targets["Q:"] = ("\\Device\\HarddiskVolume8",)
    with pytest.raises(worker._WorkerFailure) as raised:
        worker._require_mapped_import_root("Q:\\runtime", config.runtime_root)
    assert raised.value.code == "engine_failed"


def test_bootstrap_sys_path_is_a_closed_mapped_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accept only fixed shared runtime entries, never the bootstrap directory."""

    import_root = Path("Q:\\runtime")
    expected = [
        *(
            ntpath.join(str(import_root), *relative.split("/"))
            for relative in worker._BOOTSTRAP_SYS_PATH_RELATIVE_ENTRIES
        ),
    ]
    monkeypatch.setattr(worker.sys, "path", expected.copy())

    assert worker._closed_bootstrap_sys_path(import_root) == tuple(expected)
    for invalid in (
        ["Q:\\runtime\\.elysia-managed-" + "a" * 64, *expected],
        [*expected, "Q:\\untrusted"],
        [expected[0].upper(), *expected[1:]],
    ):
        monkeypatch.setattr(worker.sys, "path", invalid)
        with pytest.raises(worker._WorkerFailure) as raised:
            worker._closed_bootstrap_sys_path(import_root)
        assert raised.value.code == "engine_failed"


def test_upstream_import_uses_verified_alias_as_working_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep upstream relative-path lookups on the mapped import drive.

    GPT-SoVITS' locale helper evaluates ``relpath(__file__)`` while importing.
    A Volume-GUID working directory and DOS-aliased module path are different
    Windows drives, so using the stable asset namespace as CWD would fail before
    model loading. The alias is already verified before and after this window.
    """

    stable_root = Path(
        "\\\\?\\Volume{11111111-1111-1111-1111-111111111111}\\runtime"
    )
    import_root = Path("Q:\\runtime")
    bootstrap_entries = ("Q:\\runtime\\runtime\\lib.zip",)
    changed_to = []  # type: List[str]
    opened = []  # type: List[str]
    reconfigured = []  # type: List[Tuple[str, Dict[str, object]]]
    fake_sys = SimpleNamespace(
        path=list(bootstrap_entries),
        modules={},
        stdout=SimpleNamespace(
            reconfigure=lambda **options: reconfigured.append(
                ("stdout", options)
            )
        ),
        stderr=SimpleNamespace(
            reconfigure=lambda **options: reconfigured.append(
                ("stderr", options)
            )
        ),
    )
    monkeypatch.setattr(
        worker,
        "_require_mapped_import_root",
        lambda value, root: import_root
        if value == str(import_root) and root == stable_root
        else pytest.fail("unexpected import-root validation"),
    )
    monkeypatch.setattr(
        worker,
        "_closed_bootstrap_sys_path",
        lambda root: bootstrap_entries
        if root == import_root
        else pytest.fail("unexpected bootstrap root"),
    )
    monkeypatch.setattr(
        worker.os,
        "open",
        lambda path, _flags: opened.append(path) or 90,
    )
    monkeypatch.setattr(worker.os, "dup2", lambda _source, _target: None)
    monkeypatch.setattr(worker.os, "close", lambda _descriptor: None)
    monkeypatch.setattr(worker.os, "chdir", changed_to.append)
    monkeypatch.setattr(worker, "sys", fake_sys)

    result = worker._prepare_upstream_import(stable_root, import_root)

    assert result == import_root
    assert changed_to == [str(import_root)]
    assert opened == [worker.os.devnull]
    assert reconfigured == [
        ("stdout", {"encoding": "utf-8", "errors": "replace"}),
        ("stderr", {"encoding": "utf-8", "errors": "replace"}),
    ]
    assert fake_sys.path[:2] == [
        str(import_root),
        "Q:\\runtime\\GPT_SoVITS",
    ]


def test_silenced_text_streams_replace_an_inherited_legacy_code_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow discarded non-ASCII status text without reopening any output."""

    stdout_bytes = BytesIO()
    stderr_bytes = BytesIO()
    stdout = TextIOWrapper(stdout_bytes, encoding="cp1252")
    stderr = TextIOWrapper(stderr_bytes, encoding="cp1252")
    monkeypatch.setattr(
        worker,
        "sys",
        SimpleNamespace(stdout=stdout, stderr=stderr),
    )
    try:
        worker._reconfigure_silenced_text_streams()
        stdout.write("【model】")
        stderr.write("【reference】")
        stdout.flush()
        stderr.flush()

        assert stdout.encoding.casefold() == "utf-8"
        assert stderr.encoding.casefold() == "utf-8"
        assert stdout_bytes.getvalue() == "【model】".encode("utf-8")
        assert stderr_bytes.getvalue() == "【reference】".encode("utf-8")
    finally:
        stdout.detach()
        stderr.detach()


def test_silenced_text_stream_reconfiguration_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject startup if either inherited text wrapper cannot be hardened."""

    def reject_reconfiguration(**_options: object) -> None:
        """Model an incompatible or already-detached text stream."""

        raise OSError("private output stream detail")

    monkeypatch.setattr(
        worker,
        "sys",
        SimpleNamespace(
            stdout=SimpleNamespace(reconfigure=reject_reconfiguration),
            stderr=SimpleNamespace(reconfigure=lambda **_options: None),
        ),
    )

    with pytest.raises(worker._WorkerFailure) as raised:
        worker._reconfigure_silenced_text_streams()

    assert raised.value.code == "engine_failed"


def test_device_volume_path_never_falls_back_to_a_drive_letter() -> None:
    r"""Use the process-compatible ``\\.\Volume`` namespace only."""

    stable = Path(
        "\\\\?\\Volume{11111111-1111-1111-1111-111111111111}\\"
        "runtime\\ffmpeg.exe"
    )

    assert worker._device_volume_path(stable) == (
        "\\\\.\\Volume{11111111-1111-1111-1111-111111111111}\\"
        "runtime\\ffmpeg.exe"
    )
    with pytest.raises(worker._WorkerFailure) as raised:
        worker._device_volume_path(Path("D:\\runtime\\ffmpeg.exe"))
    assert raised.value.code == "engine_failed"


def test_guarded_load_audio_accepts_only_bound_reference_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject alternate paths and rates before any decoder process is run."""

    config = _stable_worker_config()
    numpy_module = _FakeNumpy()
    calls = []  # type: List[Tuple[Path, Path, int]]

    def decode(ffmpeg_path: Path, reference_path: Path, sample_rate: int) -> bytes:
        """Record the exact guarded invocation and return one float sample."""

        calls.append((ffmpeg_path, reference_path, sample_rate))
        return b"\x00\x00\x00\x00"

    monkeypatch.setattr(worker, "_decode_reference_audio", decode)
    load_audio = worker._make_guarded_load_audio(config, numpy_module)

    assert load_audio(str(config.reference_audio.path), 32000) == (
        "flattened",
        b"\x00\x00\x00\x00",
    )
    assert calls == [
        (
            Path(ntpath.join(str(config.runtime_root), "ffmpeg.exe")),
            config.reference_audio.path,
            32000,
        )
    ]
    for path, sample_rate in (
        (str(config.reference_audio.path) + ".other", 32000),
        (str(config.reference_audio.path), 16000),
        (str(config.reference_audio.path), True),
    ):
        with pytest.raises(worker._WorkerFailure) as raised:
            load_audio(path, sample_rate)
        assert raised.value.code == "engine_failed"
    assert len(calls) == 1


def test_upstream_loader_installs_decoder_before_tts_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure TTS captures the worker-local override, not upstream FFmpeg PATH."""

    config = _stable_worker_config()
    numpy_module = _FakeNumpy()
    original = object()
    assert config.import_root is not None
    my_utils = SimpleNamespace(
        __file__=ntpath.join(
            str(config.import_root), "tools", "my_utils.py"
        ),
        load_audio=original,
        np=numpy_module,
    )

    class FakeConfig:
        """Provide a callable class marker for the import boundary."""

    class FakeTTS:
        """Provide a callable class marker for the import boundary."""

    tts_module = SimpleNamespace(TTS_Config=FakeConfig, TTS=FakeTTS)
    imports = []  # type: List[str]

    def fake_import(name: str) -> object:
        """Return fake upstream modules while recording import order."""

        imports.append(name)
        if name == "tools.my_utils":
            return my_utils
        if name == "GPT_SoVITS.TTS_infer_pack.TTS":
            assert my_utils.load_audio is not original
            return tts_module
        raise AssertionError(name)

    monkeypatch.setattr(
        worker,
        "_prepare_upstream_import",
        lambda _root, import_root: import_root,
    )
    monkeypatch.setattr(
        worker,
        "_require_mapped_import_root",
        lambda value, _root: Path(value),
    )
    monkeypatch.setattr(worker.importlib, "import_module", fake_import)

    assert worker._load_upstream_api(config) == (FakeConfig, FakeTTS)
    assert imports == ["tools.my_utils", "GPT_SoVITS.TTS_infer_pack.TTS"]


def test_reference_decoder_uses_stable_absolute_ffmpeg_and_closed_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the no-PATH command and subprocess isolation flags."""

    config = _stable_worker_config()
    process = _FakeDecoderProcess(b"\x00\x00\x00\x00" * 2)
    captured = {}  # type: Dict[str, object]

    def fake_popen(command: object, **options: object) -> _FakeDecoderProcess:
        """Capture one decoder launch without creating a process."""

        captured["command"] = command
        captured["options"] = options
        return process

    monkeypatch.setattr(worker.subprocess, "Popen", fake_popen)

    decoded = worker._decode_reference_audio(
        Path(ntpath.join(str(config.runtime_root), "ffmpeg.exe")),
        config.reference_audio.path,
        32000,
    )

    command = captured["command"]
    assert isinstance(command, tuple)
    expected_executable = (
        "\\\\.\\Volume{11111111-1111-1111-1111-111111111111}\\"
        "runtime\\ffmpeg.exe"
    )
    assert command == (
        expected_executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-threads",
        "0",
        "-protocol_whitelist",
        "file",
        "-f",
        "wav",
        "-i",
        str(config.reference_audio.path),
        "-vn",
        "-sn",
        "-dn",
        "-t",
        str(worker._MAX_REFERENCE_SECONDS),
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "-ac",
        "1",
        "-ar",
        "32000",
        "-fs",
        str(worker._MAX_DECODED_REFERENCE_BYTES),
        "pipe:1",
    )
    assert captured["options"] == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "shell": False,
        "close_fds": True,
    }
    assert decoded == b"\x00\x00\x00\x00" * 2
    assert process.killed is False
    assert process.finished is True


@pytest.mark.parametrize(
    ("output", "return_code", "wait_timeout"),
    [
        (b"\x00\x00\x00\x00", 7, False),
        (b"\x00\x00\x00", 0, False),
        (b"X" * (worker._MAX_DECODED_REFERENCE_BYTES + 1), 0, False),
        (b"\x00\x00\x00\x00", 0, True),
    ],
    ids=("nonzero", "partial-float", "oversize", "timeout"),
)
def test_reference_decoder_fails_closed_and_reaps_bad_children(
    monkeypatch: pytest.MonkeyPatch,
    output: bytes,
    return_code: int,
    wait_timeout: bool,
) -> None:
    """Reject nonzero, malformed, excessive, and timed-out decoder results."""

    config = _stable_worker_config()
    process = _FakeDecoderProcess(
        output,
        return_code=return_code,
        wait_timeout=wait_timeout,
    )
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(worker._WorkerFailure) as raised:
        worker._decode_reference_audio(
            Path(ntpath.join(str(config.runtime_root), "ffmpeg.exe")),
            config.reference_audio.path,
            32000,
        )

    assert raised.value.code == "engine_failed"
    assert process.finished is True
    if wait_timeout:
        assert process.killed is True


@pytest.mark.parametrize(
    "frame",
    [
        ProtocolFrame(FrameKind.READY, 0, {}),
        ProtocolFrame(
            FrameKind.SYNTHESIZE,
            1,
            {
                "schema": 1,
                "challenge": "c" * 64,
                "text": "wrong challenge",
                "text_language": "en",
            },
        ),
        ProtocolFrame(
            FrameKind.SYNTHESIZE,
            1,
            {
                "schema": 2,
                "challenge": _CHALLENGE,
                "text": "wrong schema",
                "text_language": "en",
            },
        ),
        ProtocolFrame(
            FrameKind.SYNTHESIZE,
            1,
            {
                "schema": 1,
                "challenge": _CHALLENGE,
                "text": "extra",
                "text_language": "en",
                "seed": 4,
            },
        ),
        ProtocolFrame(
            FrameKind.STOP,
            0,
            {"schema": 1, "challenge": "d" * 64},
        ),
    ],
)
def test_bad_ready_state_kind_schema_or_challenge_poison_session(
    tmp_path: Path,
    frame: ProtocolFrame,
) -> None:
    """Stop permanently after any validly framed but unauthorized message."""

    metadata = _init_metadata(tmp_path)
    engine = _FakeEngine()
    status, responses, _factory = _run(metadata, [frame], engine)

    assert status == 1
    assert responses[-1].kind is FrameKind.ERROR
    assert responses[-1].metadata == {"code": "protocol_invalid"}
    assert engine.calls == []
    assert engine.close_calls == 1


def test_request_ids_must_increase_strictly(tmp_path: Path) -> None:
    """Reject replay of a completed positive synthesis identifier."""

    metadata = _init_metadata(tmp_path)
    request = {
        "schema": 1,
        "challenge": _CHALLENGE,
        "text": "first",
        "text_language": "en",
    }
    engine = _FakeEngine([_one_audio(), _one_audio()])
    status, responses, _factory = _run(
        metadata,
        [
            ProtocolFrame(FrameKind.SYNTHESIZE, 7, request),
            ProtocolFrame(FrameKind.SYNTHESIZE, 7, request),
        ],
        engine,
    )

    assert status == 1
    assert [frame.kind for frame in responses] == [
        FrameKind.READY,
        FrameKind.AUDIO,
        FrameKind.ERROR,
    ]
    assert responses[-1].request_id == 0
    assert len(engine.calls) == 1
    assert engine.close_calls == 1


def test_truncated_frame_after_ready_is_not_retried_or_resynchronized(
    tmp_path: Path,
) -> None:
    """Poison the stream after a partial next header and close the engine."""

    metadata = _init_metadata(tmp_path)
    encoded = _request_bytes([ProtocolFrame(FrameKind.INIT, 0, metadata)]) + b"ELY"
    engine = _FakeEngine()
    response = BytesIO()

    status = worker.run_worker(BytesIO(encoded), response, _Factory(engine))

    assert status == 1
    assert [frame.kind for frame in _response_frames(response.getvalue())] == [
        FrameKind.READY,
        FrameKind.ERROR,
    ]
    assert engine.close_calls == 1


def _synthesis_failure_session(
    tmp_path: Path,
    result: object,
) -> Tuple[int, List[ProtocolFrame], _FakeEngine]:
    """Run one bad fake inference result through the worker failure boundary."""

    metadata = _init_metadata(tmp_path)
    engine = _FakeEngine([result])
    status, responses, _factory = _run(
        metadata,
        [
            ProtocolFrame(
                FrameKind.SYNTHESIZE,
                1,
                {
                    "schema": 1,
                    "challenge": _CHALLENGE,
                    "text": "synthesize",
                    "text_language": "en",
                },
            )
        ],
        engine,
    )
    return status, responses, engine


def test_all_zero_upstream_sentinel_is_rejected(tmp_path: Path) -> None:
    """Recognize upstream's all-zero exception sentinel as failure, not audio."""

    status, responses, engine = _synthesis_failure_session(
        tmp_path,
        _one_audio(samples=array("h", (0, 0, 0))),
    )

    assert status == 1
    assert responses[-1].metadata == {"code": "synthesis_failed"}
    assert engine.close_calls == 1


def test_all_zero_upstream_sentinel_is_not_resumed(tmp_path: Path) -> None:
    """Close at the sentinel yield before upstream can run its reload branch."""

    resumed = False

    def zero_then_reload() -> object:
        """Model the upstream yield-before-destructive-reload exception path."""

        nonlocal resumed
        yield 32000, array("h", (0, 0, 0))
        resumed = True
        raise AssertionError("destructive model reload was resumed")

    status, responses, _engine = _synthesis_failure_session(
        tmp_path,
        zero_then_reload(),
    )

    assert status == 1
    assert responses[-1].metadata == {"code": "synthesis_failed"}
    assert resumed is False


def test_second_generator_yield_is_rejected(tmp_path: Path) -> None:
    """Refuse fragmented or duplicate results from non-streaming inference."""

    def two_results() -> object:
        """Yield two otherwise plausible arrays to violate the worker contract."""

        yield 32000, array("h", (1, 2))
        yield 32000, array("h", (3, 4))

    status, responses, engine = _synthesis_failure_session(tmp_path, two_results())

    assert status == 1
    assert responses[-1].metadata == {"code": "synthesis_failed"}
    assert engine.close_calls == 1


def test_generator_drain_error_is_rejected(tmp_path: Path) -> None:
    """Fail if cleanup work after the first yield raises a native exception."""

    def drain_error() -> object:
        """Yield once and then expose a sensitive fake native failure."""

        yield 32000, array("h", (1, 2))
        raise OSError("private prompt and model path")

    status, responses, engine = _synthesis_failure_session(tmp_path, drain_error())

    assert status == 1
    assert responses[-1].metadata == {"code": "synthesis_failed"}
    assert b"private prompt" not in _request_bytes(responses)
    assert engine.close_calls == 1


@pytest.mark.parametrize(
    "result",
    [
        _one_audio(0),
        _one_audio(32000, b"not-int16"),
        _one_audio(32000, array("i", (1, 2))),
        iter(()),
        iter(((32000, array("h")),)),
        iter((("32000", array("h", (1,))),)),
    ],
)
def test_invalid_sample_rate_shape_dtype_or_empty_audio_is_rejected(
    tmp_path: Path,
    result: object,
) -> None:
    """Accept only non-empty one-dimensional native signed-16 PCM arrays."""

    status, responses, _engine = _synthesis_failure_session(tmp_path, result)

    assert status == 1
    assert responses[-1].metadata == {"code": "synthesis_failed"}


def test_audio_larger_than_protocol_limit_is_rejected_without_emission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apply the payload bound after adding the complete 44-byte WAV header."""

    monkeypatch.setattr(worker, "_MAX_AUDIO_BYTES", 49)
    status, responses, _engine = _synthesis_failure_session(
        tmp_path,
        _one_audio(samples=array("h", (1, 2, 3))),
    )

    assert status == 1
    assert [frame.kind for frame in responses] == [FrameKind.READY, FrameKind.ERROR]


def test_engine_exception_is_not_retried_and_error_is_sanitized(
    tmp_path: Path,
) -> None:
    """Invoke inference once, emit one closed code, then close permanently."""

    metadata = _init_metadata(tmp_path)
    engine = _FakeEngine(error=OSError("secret model path and prompt"))
    status, responses, _factory = _run(
        metadata,
        [
            ProtocolFrame(
                FrameKind.SYNTHESIZE,
                4,
                {
                    "schema": 1,
                    "challenge": _CHALLENGE,
                    "text": "private user prompt",
                    "text_language": "en",
                },
            ),
            ProtocolFrame(FrameKind.STOP, 0, {"schema": 1, "challenge": _CHALLENGE}),
        ],
        engine,
    )
    encoded = _request_bytes(responses)

    assert status == 1
    assert len(engine.calls) == 1
    assert engine.close_calls == 1
    assert responses[-1].metadata == {"code": "synthesis_failed"}
    assert b"secret model" not in encoded
    assert b"private user prompt" not in encoded


def test_sensitive_config_and_engine_repr_hide_paths_and_prompt(tmp_path: Path) -> None:
    """Keep INIT secrets out of dataclass representations and stable errors."""

    metadata = _init_metadata(tmp_path)
    config = worker._parse_init(metadata)
    engine = _FakeEngine()
    factory = _Factory(engine)
    status, _responses, _factory = _run(
        metadata,
        [ProtocolFrame(FrameKind.STOP, 0, {"schema": 1, "challenge": _CHALLENGE})],
        engine,
    )
    rendered = repr(config) + repr(worker._ProductionEngine)

    assert status == 0
    assert str(metadata["prompt_text"]) not in rendered
    assert str(metadata["runtime_root"]) not in rendered
    assert "reference.wav" not in rendered
    assert "prompt_text" not in repr(factory.calls)


def test_close_failure_returns_error_instead_of_stopped(tmp_path: Path) -> None:
    """Do not claim STOPPED when native model cleanup did not complete."""

    metadata = _init_metadata(tmp_path)
    engine = _FakeEngine(close_error=OSError("private cleanup failure"))
    status, responses, _factory = _run(
        metadata,
        [ProtocolFrame(FrameKind.STOP, 0, {"schema": 1, "challenge": _CHALLENGE})],
        engine,
    )

    assert status == 1
    assert [frame.kind for frame in responses] == [FrameKind.READY, FrameKind.ERROR]
    assert responses[-1].metadata == {"code": "engine_failed"}
    assert engine.close_calls == 1


def test_response_pipe_failure_closes_engine_without_retry(tmp_path: Path) -> None:
    """Close after the first failed READY write without retrying a corrupt pipe."""

    metadata = _init_metadata(tmp_path)
    engine = _FakeEngine()
    response = _FailingWriter()

    status = worker.run_worker(
        BytesIO(_request_bytes([ProtocolFrame(FrameKind.INIT, 0, metadata)])),
        response,  # type: ignore[arg-type]
        _Factory(engine),
    )

    assert status == 1
    assert response.write_calls == 1
    assert engine.close_calls == 1


def test_partial_response_prefix_is_never_followed_by_error(
    tmp_path: Path,
) -> None:
    """Close a poisoned pipe without appending ERROR after a frame prefix."""

    metadata = _init_metadata(tmp_path)
    engine = _FakeEngine()
    response = _PrefixThenFailWriter()

    status = worker.run_worker(
        BytesIO(_request_bytes([ProtocolFrame(FrameKind.INIT, 0, metadata)])),
        response,  # type: ignore[arg-type]
        _Factory(engine),
    )

    assert status == 1
    assert response.write_calls == 2
    assert len(response.output) == 5
    assert engine.close_calls == 1


def test_production_adapter_uses_in_memory_v2_config_and_fixed_controls(
    tmp_path: Path,
) -> None:
    """Exercise the real adapter shape with fake upstream classes only."""

    config = worker._parse_init(_init_metadata(tmp_path))
    captured = {}  # type: Dict[str, object]

    class FakeConfig:
        """Model upstream TTS_Config without filesystem or package imports."""

        def __init__(self, value: Dict[str, object]) -> None:
            """Capture the in-memory dictionary and expose fixed v2 state."""

            captured["config"] = value
            custom = value["custom"]
            assert isinstance(custom, dict)
            for name, selected in custom.items():
                setattr(self, name, selected)
            self.version = value["version"]

        def save_configs(self, _path: object = None) -> None:
            """Fail if the worker did not replace persistence before creation."""

            raise AssertionError("save_configs was not disabled")

    class FakeTTS:
        """Model the narrow upstream TTS behavior used by the adapter."""

        def __init__(self, value: FakeConfig) -> None:
            """Store configuration and invoke the already-disabled save hook."""

            self.configs = value
            value.save_configs()
            self.reference = None  # type: Optional[str]
            self.prompt_cache = {"ref_audio_path": None}  # type: Dict[str, object]
            self.last_inputs = None  # type: Optional[Dict[str, object]]
            self.cleaned = False

        def set_ref_audio(self, path: str) -> None:
            """Record the one reference preload performed at construction."""

            self.reference = path
            self.prompt_cache["ref_audio_path"] = path

        def init_t2s_weights(self, _path: str) -> None:
            """Represent the upstream GPT hot-reload method before hardening."""

        def init_vits_weights(self, _path: str) -> None:
            """Represent the upstream SoVITS hot-reload method before hardening."""

        def run(self, values: Dict[str, object]) -> object:
            """Capture fixed controls and return one fake waveform."""

            self.last_inputs = values
            return _one_audio()

        def empty_cache(self) -> None:
            """Record cleanup without importing torch."""

            self.cleaned = True

    def fake_loader(_config: worker._WorkerConfig) -> Tuple[object, object]:
        """Return fake upstream classes without touching the real model tree."""

        return FakeConfig, FakeTTS

    adapter = worker._ProductionEngine(config, fake_loader)
    upstream = adapter._tts
    result = adapter.synthesize("request text", "en")
    sample_rate, wav = worker._build_wav(result)

    assert sample_rate == 32000
    assert wav.startswith(b"RIFF")
    assert captured["config"]["version"] == "v2"  # type: ignore[index]
    custom = captured["config"]["custom"]  # type: ignore[index]
    assert custom["version"] == "v2"
    assert custom["t2s_weights_path"] == str(config.gpt_weights.path)
    assert custom["vits_weights_path"] == str(config.sovits_weights.path)
    assert upstream.reference == str(config.reference_audio.path)
    assert upstream.last_inputs["text"] == "request text"
    assert upstream.last_inputs["text_lang"] == "en"
    assert upstream.last_inputs["seed"] == 42
    assert upstream.last_inputs["speed_factor"] == 1
    assert upstream.last_inputs["ref_audio_path"] is None
    with pytest.raises(RuntimeError, match="reload"):
        upstream.init_t2s_weights("different-private-model")
    adapter.close()
    assert upstream.cleaned is True


def test_production_adapter_rejects_upstream_path_fallback(
    tmp_path: Path,
) -> None:
    """Stop before TTS construction if TTS_Config substitutes a default path."""

    config = worker._parse_init(_init_metadata(tmp_path))
    tts_constructions = 0

    class FallingBackConfig:
        """Model upstream's silent fallback for one missing custom checkpoint."""

        def __init__(self, value: Dict[str, object]) -> None:
            """Expose all requested values except the substituted GPT path."""

            custom = value["custom"]
            assert isinstance(custom, dict)
            for name, selected in custom.items():
                setattr(self, name, selected)
            self.version = value["version"]
            self.t2s_weights_path = "bundled/default.ckpt"

    class UnexpectedTTS:
        """Fail the test if a mismatched configuration reaches model loading."""

        def __init__(self, _value: object) -> None:
            """Record the forbidden construction attempt."""

            nonlocal tts_constructions
            tts_constructions += 1

    def fallback_loader(_config: worker._WorkerConfig) -> Tuple[object, object]:
        """Return fake classes that reproduce TTS_Config fallback behavior."""

        return FallingBackConfig, UnexpectedTTS

    with pytest.raises(worker._WorkerFailure) as raised:
        worker._ProductionEngine(config, fallback_loader)

    assert raised.value.code == "engine_failed"
    assert tts_constructions == 0


def test_source_loads_protocol_absolutely_and_parses_as_python_39() -> None:
    """Pin isolated sibling loading and bundled Python 3.9 grammar support."""

    source_path = Path(worker.__file__).resolve()
    source = source_path.read_text(encoding="utf-8")

    ast.parse(source, filename=str(source_path), feature_version=(3, 9))
    assert "spec_from_file_location" in source
    assert 'import_module("GPT_SoVITS.TTS_infer_pack.TTS")' in source
    assert "os.dup(sys.stdout.fileno())" in source
    assert "os.dup2(null_descriptor, 1)" in source
    assert "Path(__file__).resolve" not in source
    assert "from voice" not in source
    assert "from tools" not in source
