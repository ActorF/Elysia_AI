"""Test the guarded parent-side GPT-SoVITS worker handshake and lease."""

from __future__ import annotations

from dataclasses import dataclass
import gc
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from queue import Queue
from threading import Event, Thread
from time import monotonic
from typing import Any, BinaryIO, Callable
import wave
import weakref

import pytest

import voice as voice_package
import voice.managed_gpt_sovits as managed_module
from scripts.gpt_sovits_protocol import (
    FrameKind,
    ProtocolFrame,
    read_frame,
    write_frame,
)
from scripts.gpt_sovits_worker import compute_runtime_manifest_digest
from voice.managed_gpt_sovits import (
    ManagedGptSovitsCancelledError,
    ManagedGptSovitsConfig,
    ManagedGptSovitsLease,
    ManagedGptSovitsRuntime,
    ManagedGptSovitsStartupError,
    ManagedGptSovitsSynthesisError,
    ManagedGptSovitsUnavailableError,
    ManagedGptSovitsValidationError,
)
from voice.profiles import (
    JsonVoiceProfileCatalog,
    LocalVoiceAssetDeclaration,
    LocalVoiceProfile,
    LocalVoiceReference,
)


pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="The managed parent runtime requires native Windows path and handle semantics.",
)

_CHALLENGE = "b" * 64
_VOLUME_ROOT = Path("\\\\?\\Volume{11111111-1111-1111-1111-111111111111}\\")


def test_voice_package_exports_the_managed_runtime_contract() -> None:
    """Expose the guarded runtime through Voice's supported public surface."""

    assert voice_package.ManagedGptSovitsConfig is ManagedGptSovitsConfig
    assert voice_package.ManagedGptSovitsRuntime is ManagedGptSovitsRuntime
    assert voice_package.ManagedGptSovitsLease is ManagedGptSovitsLease


def _operation_token(label: str) -> str:
    """Create one deterministic canonical queue token for a test operation."""

    return hashlib.sha256(label.encode("utf-8")).hexdigest()


class _PersistentBytesIO(BytesIO):
    """Retain captured wire bytes after production ownership calls close."""

    def close(self) -> None:
        """Leave the in-memory capture readable for assertions."""


@dataclass(frozen=True, repr=False)
class _Declaration:
    """Capture one candidate declaration without touching a real file."""

    path: Path
    size_bytes: int
    sha256: str


class _FakeGuard:
    """Record deterministic guard cleanup without exposing declarations."""

    def __init__(self, events: list[str], event_name: str = "guard-close") -> None:
        """Create one open fake guard attached to an event ledger."""

        self._events = events
        self._event_name = event_name
        self.closed = False

    def close(self) -> None:
        """Record exactly one guard release."""

        if not self.closed:
            self.closed = True
            self._events.append(self._event_name)

    def __repr__(self) -> str:
        """Suppress all fake declaration values."""

        return "_FakeGuard(redacted=True)"


class _FakeProcess:
    """Record Job termination and provide a completion signal for async tests."""

    def __init__(
        self,
        events: list[str],
        *,
        termination_result: bool = True,
        termination_raises: bool = False,
    ) -> None:
        """Create one active fake process."""

        self._events = events
        self._termination_result = termination_result
        self._termination_raises = termination_raises
        self.terminated = Event()

    @property
    def pid(self) -> int:
        """Return one inert positive child identifier for protocol conformance."""

        return 4242

    def terminate(self, timeout_seconds: float = 5.0) -> bool:
        """Record one idempotent termination request."""

        if not self.terminated.is_set():
            self._events.append("process-terminate")
            self.terminated.set()
        if self._termination_raises:
            raise OSError("simulated native termination failure")
        return self._termination_result


class _FakeTransport:
    """Provide inert handles plus in-memory parent protocol streams."""

    def __init__(self, events: list[str], closed_event: Event) -> None:
        """Create one transport whose close wakes a scripted reader."""

        self.request_stream: BinaryIO = _PersistentBytesIO()
        self.response_stream: BinaryIO = _PersistentBytesIO()
        self.stdin_handle = 101
        self.stdout_handle = 102
        self.stderr_handle = 103
        self._events = events
        self._closed_event = closed_event
        self._closed = False

    def close_child_ends(self) -> None:
        """Record release of simulated child endpoints."""

        self._events.append("child-ends-close")

    def close(self) -> None:
        """Wake blocked reads and record exactly one parent-pipe close."""

        if not self._closed:
            self._closed = True
            self._events.append("transport-close")
            self._closed_event.set()
        self.request_stream.close()
        self.response_stream.close()


class _FailOnceCloseTransport(_FakeTransport):
    """Model one ambiguous native transport close before a safe retry."""

    def __init__(self, events: list[str], closed_event: Event) -> None:
        """Create a transport whose first close attempt reports failure."""

        super().__init__(events, closed_event)
        self._fail_close_once = True

    def close(self) -> None:
        """Fail once without releasing ownership, then use normal cleanup."""

        if self._fail_close_once:
            self._fail_close_once = False
            self._events.append("transport-close-failed")
            raise OSError("simulated native close failure")
        super().close()


class _FailUntilReleasedTransport(_FakeTransport):
    """Keep transport ownership ambiguous until a test explicitly releases it."""

    def __init__(self, events: list[str], closed_event: Event) -> None:
        """Create a transport that initially rejects every close attempt."""

        super().__init__(events, closed_event)
        self.allow_close = False
        self.close_attempts = 0

    def close(self) -> None:
        """Fail without releasing endpoints until cleanup is explicitly allowed."""

        self.close_attempts += 1
        if not self.allow_close:
            self._events.append("transport-close-failed")
            raise OSError("simulated persistent native close failure")
        super().close()


class _ScriptedProtocol:
    """Generate worker responses from fully serialized parent requests."""

    def __init__(
        self,
        *,
        ready_mutation: Callable[[dict[str, object]], None] | None = None,
        audio_mutation: Callable[[dict[str, object]], None] | None = None,
        startup_error: str | None = None,
        synthesis_error: str | None = None,
        block_startup: bool = False,
        block_synthesis: bool = False,
        block_stop: bool = False,
        closed_event: Event | None = None,
    ) -> None:
        """Configure one deterministic worker-side behavior."""

        self.writes: list[ProtocolFrame] = []
        self._responses: Queue[ProtocolFrame] = Queue()
        self._ready_mutation = ready_mutation
        self._audio_mutation = audio_mutation
        self._startup_error = startup_error
        self._synthesis_error = synthesis_error
        self._block_startup = block_startup
        self._block_synthesis = block_synthesis
        self._block_stop = block_stop
        self._closed_event = closed_event or Event()
        self.synthesis_started = Event()
        self.stop_started = Event()
        self.allow_stop = Event()

    def write(self, stream: BinaryIO, frame: ProtocolFrame) -> None:
        """Serialize a complete frame and enqueue its scripted response."""

        write_frame(stream, frame)
        self.writes.append(frame)
        if frame.kind is FrameKind.INIT:
            if self._block_startup:
                return
            if self._startup_error is not None:
                self._responses.put(
                    ProtocolFrame(
                        FrameKind.ERROR,
                        0,
                        {"code": self._startup_error},
                    )
                )
                return
            metadata = frame.metadata
            binding_input = dict(metadata)
            challenge = binding_input.pop("challenge")
            canonical = json.dumps(
                binding_input,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            ready: dict[str, object] = {
                "binding_sha256": hashlib.sha256(
                    b"ELYTTS-BINDING-V1\0" + canonical
                ).hexdigest(),
                "challenge": challenge,
                "schema": 1,
            }
            if self._ready_mutation is not None:
                self._ready_mutation(ready)
            self._responses.put(ProtocolFrame(FrameKind.READY, 0, ready))
        elif frame.kind is FrameKind.SYNTHESIZE:
            self.synthesis_started.set()
            if self._block_synthesis:
                return
            if self._synthesis_error is not None:
                self._responses.put(
                    ProtocolFrame(
                        FrameKind.ERROR,
                        frame.request_id,
                        {"code": self._synthesis_error},
                    )
                )
                return
            metadata = frame.metadata
            audio_metadata: dict[str, object] = {
                "challenge": metadata["challenge"],
                "format": "wav",
                "sample_rate": 24_000,
                "schema": 1,
            }
            if self._audio_mutation is not None:
                self._audio_mutation(audio_metadata)
            self._responses.put(
                ProtocolFrame(
                    FrameKind.AUDIO,
                    frame.request_id,
                    audio_metadata,
                    _wav_bytes(),
                )
            )
        elif frame.kind is FrameKind.STOP:
            self.stop_started.set()
            if self._block_stop:
                self.allow_stop.wait()
            self._responses.put(
                ProtocolFrame(
                    FrameKind.STOPPED,
                    0,
                    {"challenge": frame.metadata["challenge"], "schema": 1},
                )
            )

    def read(self, _stream: BinaryIO) -> ProtocolFrame:
        """Return one response or model a pipe wake after cancellation."""

        if (
            (self._block_startup and not self.writes)
            or (
                self._block_synthesis
                and self.writes
                and self.writes[-1].kind is FrameKind.SYNTHESIZE
            )
        ):
            self._closed_event.wait()
            raise OSError("simulated pipe closure")
        if self._block_startup and self.writes[-1].kind is FrameKind.INIT:
            self._closed_event.wait()
            raise OSError("simulated pipe closure")
        return self._responses.get()


class _Harness:
    """Assemble injected boundaries and retain observable lifecycle state."""

    def __init__(self, protocol: _ScriptedProtocol | None = None) -> None:
        """Create one all-fake guarded process environment."""

        self.events: list[str] = []
        self.closed_event = Event()
        self.protocol = protocol or _ScriptedProtocol(closed_event=self.closed_event)
        self.transport = _FakeTransport(self.events, self.closed_event)
        self.guard = _FakeGuard(self.events)
        self.bootstrap_guard = _FakeGuard(
            self.events, "bootstrap-guard-close"
        )
        self.process = _FakeProcess(self.events)
        self.declarations: tuple[_Declaration, ...] = ()
        self.launch_calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def acquire_guard(self, declarations: tuple[Any, ...]) -> _FakeGuard:
        """Capture the complete declaration tuple and return one held guard."""

        self.events.append("guard-acquire")
        self.declarations = declarations
        return self.guard

    def guarded_paths(self, _guard: object) -> tuple[str, ...]:
        """Map every fake declaration to one stable Volume-GUID spelling."""

        self.events.append("stable-paths")
        paths: list[str] = []
        for declaration in self.declarations:
            source = declaration.path
            paths.append(str(_VOLUME_ROOT.joinpath(*source.parts[1:])))
        return tuple(paths)

    def manifest_digest(
        self,
        _runtime_root: Path,
        _worker_path: Path,
        _protocol_path: Path,
    ) -> str:
        """Return the digest of the declarations already accepted by the guard."""

        self.events.append("manifest")
        return managed_module._guarded_runtime_manifest_digest(self.declarations)

    def launch(self, argv: list[str], **kwargs: object) -> _FakeProcess:
        """Capture the closed launch vector and environment."""

        self.events.append("launch")
        self.launch_calls.append((tuple(argv), dict(kwargs)))
        return self.process

    def bootstrap(
        self, layout: managed_module._GuardedLayout
    ) -> managed_module._BootstrapLaunch:
        """Return one held fake bootstrap through a mapped drive spelling."""

        self.events.append("bootstrap")
        return managed_module._BootstrapLaunch(
            r"R:\runtime\.elysia-managed-test\python.exe",
            r"R:\runtime",
            layout.runtime_root / ".elysia-managed-test/python.exe",
            managed_module._DeclaredIdentity(10, "f" * 64),
            self.bootstrap_guard,
        )

    def environment(self) -> dict[str, str]:
        """Return one deterministic secret-free Windows child environment."""

        self.events.append("environment")
        return {
            "LANGUAGE": "en_US",
            "PATH": r"C:\Windows\System32",
            "SystemRoot": r"C:\Windows",
            "USERPROFILE": r"C:\Windows",
        }


def _wav_bytes(sample_rate: int = 24_000) -> bytes:
    """Build one canonical mono signed-16 PCM WAV with two nonzero samples."""

    stream = BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x01\x00\xff\xff")
    return stream.getvalue()


def _selection(tmp_path: Path) -> object:
    """Build one provenance-valid private catalog selection without file I/O."""

    root = tmp_path.resolve()
    gpt = LocalVoiceAssetDeclaration(
        root, PurePosixPath("models/voice.ckpt"), 123, "c" * 64
    )
    sovits = LocalVoiceAssetDeclaration(
        root, PurePosixPath("models/voice.pth"), 456, "d" * 64
    )
    reference_asset = LocalVoiceAssetDeclaration(
        root, PurePosixPath("references/neutral.wav"), 120, "e" * 64
    )
    profile = LocalVoiceProfile(
        profile_id="sample",
        display_name="Sample",
        base_url="http://127.0.0.1:9880",
        gpt_weights=gpt,
        sovits_weights=sovits,
        speed_factor=1.0,
        audio_format="wav",
        rights_status="verified",
        references=(
            LocalVoiceReference(
                emotion="neutral",
                audio=reference_asset,
                prompt_text="Private reference words.",
                prompt_language="en",
            ),
        ),
    )
    return JsonVoiceProfileCatalog(
        root,
        (profile,),
        default_profile_id="sample",
    ).resolve_selection("sample", "neutral")


def _materialized_selection(tmp_path: Path) -> object:
    """Build a selection whose tiny fake assets match real guard declarations."""

    root = (tmp_path / "assets").resolve()
    values = (
        (PurePosixPath("models/voice.ckpt"), b"gpt-checkpoint"),
        (PurePosixPath("models/voice.pth"), b"sovits-checkpoint"),
        (PurePosixPath("references/neutral.wav"), b"reference-audio-data"),
    )
    declarations: list[LocalVoiceAssetDeclaration] = []
    for relative, content in values:
        path = root.joinpath(*relative.parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        declarations.append(
            LocalVoiceAssetDeclaration(
                root,
                relative,
                len(content),
                hashlib.sha256(content).hexdigest(),
            )
        )
    profile = LocalVoiceProfile(
        profile_id="materialized",
        display_name="Materialized",
        base_url="http://127.0.0.1:9880",
        gpt_weights=declarations[0],
        sovits_weights=declarations[1],
        speed_factor=1.0,
        audio_format="wav",
        rights_status="verified",
        references=(
            LocalVoiceReference(
                emotion="neutral",
                audio=declarations[2],
                prompt_text="Guard integration reference.",
                prompt_language="en",
            ),
        ),
    )
    return JsonVoiceProfileCatalog(
        root,
        (profile,),
        default_profile_id="materialized",
    ).resolve_selection("materialized", "neutral")


def _config(tmp_path: Path, **changes: object) -> ManagedGptSovitsConfig:
    """Build one lexical absolute test configuration with bounded timeouts."""

    values: dict[str, object] = {
        "runtime_root": (tmp_path / "runtime-root").resolve(),
        "worker_script": (tmp_path / "gpt_sovits_worker.py").resolve(),
        "startup_timeout_seconds": 0.25,
        "synthesis_timeout_seconds": 0.25,
        "stop_timeout_seconds": 0.25,
        "device": "cpu",
        "seed": 7,
    }
    values.update(changes)
    return ManagedGptSovitsConfig(**values)  # type: ignore[arg-type]


def _bootstrap_failure_layout(tmp_path: Path) -> managed_module._GuardedLayout:
    """Build the minimal closed runtime-role layout needed by factory fault tests."""

    runtime_root = (tmp_path / "stable-runtime").resolve()
    candidate_root = (tmp_path / "candidate-runtime").resolve()
    anchors = tuple(
        runtime_root / Path(relative)
        for _role, relative in managed_module._RUNTIME_MANIFEST_RELATIVE_FILES
    )
    identities = tuple(
        managed_module._DeclaredIdentity(1, "0" * 64) for _path in anchors
    )
    worker = (tmp_path / "stable-worker.py").resolve()
    return managed_module._GuardedLayout(
        worker,
        worker,
        worker,
        worker,
        worker.with_name("gpt_sovits_protocol.py"),
        runtime_root,
        candidate_root,
        anchors,
        identities,
    )


def _runtime(
    tmp_path: Path,
    harness: _Harness,
    **changes: object,
) -> ManagedGptSovitsRuntime:
    """Create one runtime whose external boundaries are all observable fakes."""

    options: dict[str, object] = {
        "launcher": harness.launch,
        "guard_acquirer": harness.acquire_guard,
        "guarded_paths_getter": harness.guarded_paths,
        "declaration_factory": _Declaration,
        "manifest_digest_factory": harness.manifest_digest,
        "candidate_hash_factory": lambda _path: (10, "f" * 64),
        "transport_factory": lambda: harness.transport,
        "challenge_factory": lambda: _CHALLENGE,
        "reader": harness.protocol.read,
        "writer": harness.protocol.write,
        "bootstrap_factory": harness.bootstrap,
        "environment_factory": harness.environment,
        "platform_name": "nt",
    }
    options.update(changes)
    dependencies = managed_module._RuntimeDependencies(
        **options  # type: ignore[arg-type]
    )
    return ManagedGptSovitsRuntime._create_for_testing(
        _config(tmp_path),
        dependency_seal=managed_module._TEST_DEPENDENCY_SEAL,
        dependencies=dependencies,
    )


def _assert_production_cleanup_latch(tmp_path: Path) -> None:
    """Prove the global cleanup latch rejects a new production acquisition."""

    runtime = ManagedGptSovitsRuntime(_config(tmp_path))
    # Force the platform branch on non-Windows CI; the production dependency
    # identities remain intact, so the global latch is still the first external
    # lifecycle gate and no filesystem probe can occur.
    runtime._platform_name = "nt"
    runtime._state = "idle"
    with pytest.raises(ManagedGptSovitsUnavailableError):
        runtime.acquire_lease(_selection(tmp_path))
    assert runtime.get_status().state == "poisoned"


def _wait_for(event: Event) -> None:
    """Wait briefly for one expected daemon cleanup transition."""

    assert event.wait(1.0)


def test_constructor_and_status_perform_no_external_work(tmp_path: Path) -> None:
    """Keep construction/status pure even when every boundary would explode."""

    supplied_config = _config(tmp_path)
    original_root = supplied_config.runtime_root
    runtime = ManagedGptSovitsRuntime(supplied_config)
    assert runtime._config is not supplied_config
    object.__setattr__(
        supplied_config, "runtime_root", (tmp_path / "mutated").resolve()
    )
    assert runtime._config.runtime_root == original_root

    expected_state = "idle" if managed_module.os.name == "nt" else "unavailable"
    assert runtime.get_status().state == expected_state
    assert runtime.get_status().available is (expected_state == "idle")
    assert runtime.get_status().cache_eligible is False

    with pytest.raises(TypeError):
        ManagedGptSovitsRuntime(  # type: ignore[call-arg]
            _config(tmp_path), _launcher=lambda: None
        )


def test_private_factories_reject_public_and_subclass_forgery(
    tmp_path: Path,
) -> None:
    """Require module seals and exact classes for every trusted object issuer."""

    harness = _Harness()
    dependencies = managed_module._RuntimeDependencies(
        launcher=harness.launch,
        guard_acquirer=harness.acquire_guard,
        guarded_paths_getter=harness.guarded_paths,
        declaration_factory=_Declaration,
        manifest_digest_factory=harness.manifest_digest,
        candidate_hash_factory=lambda _path: (10, "f" * 64),
        transport_factory=lambda: harness.transport,
        challenge_factory=lambda: _CHALLENGE,
        reader=harness.protocol.read,
        writer=harness.protocol.write,
        bootstrap_factory=harness.bootstrap,
        environment_factory=harness.environment,
        platform_name="nt",
    )
    with pytest.raises(ManagedGptSovitsUnavailableError):
        ManagedGptSovitsRuntime._create_for_testing(
            _config(tmp_path),
            dependency_seal=object(),
            dependencies=dependencies,
        )

    class _RuntimeSubclass(ManagedGptSovitsRuntime):
        """Attempt to redirect the sealed test factory through a subclass."""

    with pytest.raises(ManagedGptSovitsUnavailableError):
        _RuntimeSubclass._create_for_testing(
            _config(tmp_path),
            dependency_seal=managed_module._TEST_DEPENDENCY_SEAL,
            dependencies=dependencies,
        )
    with pytest.raises(ManagedGptSovitsUnavailableError):
        ManagedGptSovitsLease()

    class _LeaseSubclass(ManagedGptSovitsLease):
        """Attempt to redirect the private issuer through subclass dispatch."""

    resources = managed_module._OwnedResources(
        harness.transport, harness.process, harness.guard
    )
    with pytest.raises(ManagedGptSovitsUnavailableError):
        _LeaseSubclass._issue(
            issuer_seal=managed_module._LEASE_ISSUER_SEAL,
            resources=resources,
            challenge=_CHALLENGE,
            speed_factor=1.0,
            synthesis_timeout=0.1,
            stop_timeout=0.1,
            reader=harness.protocol.read,
            writer=harness.protocol.write,
            on_finished=lambda _lease, _poisoned: None,
        )
    resources.close(0.1)


def test_parent_runtime_anchor_list_tracks_worker_manifest() -> None:
    """Fail visibly if parent guards drift from the worker's hashed anchors."""

    worker_anchors = compute_runtime_manifest_digest.__globals__[
        "_RUNTIME_MANIFEST_RELATIVE_FILES"
    ]
    assert managed_module._RUNTIME_MANIFEST_RELATIVE_FILES == worker_anchors


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("startup_timeout_seconds", 0),
        ("synthesis_timeout_seconds", float("inf")),
        ("stop_timeout_seconds", 601),
        ("device", "auto"),
        ("seed", True),
    ],
)
def test_config_rejects_unbounded_or_open_policy_values(
    tmp_path: Path, field: str, value: object
) -> None:
    """Accept only closed devices, strict seeds, and finite timeout bounds."""

    with pytest.raises(ManagedGptSovitsValidationError):
        _config(tmp_path, **{field: value})


def test_config_rejects_dynamic_subclasses_and_freezes_builtin_snapshots(
    tmp_path: Path,
) -> None:
    """Never retain caller-defined Path, string, or numeric behavior."""

    concrete_path_type = type(Path())

    class _DynamicPath(concrete_path_type):  # type: ignore[misc, valid-type]
        """Represent a Path subclass that could override future operations."""

    class _DynamicString(str):
        """Represent a string subclass with caller-controlled behavior."""

    class _DynamicFloat(float):
        """Represent a numeric subclass with caller-controlled conversion."""

    with pytest.raises(ManagedGptSovitsValidationError):
        ManagedGptSovitsConfig(
            runtime_root=_DynamicPath(str((tmp_path / "runtime").resolve())),
            worker_script=(tmp_path / "worker.py").resolve(),
        )
    with pytest.raises(ManagedGptSovitsValidationError):
        _config(tmp_path, device=_DynamicString("cpu"))
    with pytest.raises(ManagedGptSovitsValidationError):
        _config(tmp_path, startup_timeout_seconds=_DynamicFloat(1.0))

    config = _config(
        tmp_path,
        startup_timeout_seconds=1,
        synthesis_timeout_seconds=2,
        stop_timeout_seconds=3,
    )
    assert type(config.runtime_root) is concrete_path_type
    assert type(config.worker_script) is concrete_path_type
    assert type(config.startup_timeout_seconds) is float
    assert type(config.synthesis_timeout_seconds) is float
    assert type(config.stop_timeout_seconds) is float
    assert type(config.device) is str
    assert type(config.seed) is int


def test_runtime_and_selection_require_exact_types_and_catalog_provenance(
    tmp_path: Path,
) -> None:
    """Reject config subclasses and forged private selection instances."""

    class _ConfigSubclass(ManagedGptSovitsConfig):
        """Attempt to attach behavior outside the frozen config slots."""

    config_subclass = _ConfigSubclass(
        runtime_root=(tmp_path / "runtime").resolve(),
        worker_script=(tmp_path / "worker.py").resolve(),
    )
    with pytest.raises(TypeError):
        ManagedGptSovitsRuntime(config_subclass)

    harness = _Harness()
    runtime = _runtime(tmp_path, harness)
    selection = _selection(tmp_path)
    selection_type = type(selection)

    class _SelectionSubclass(selection_type):  # type: ignore[misc, valid-type]
        """Attempt to bypass exact private catalog selection identity."""

    forged_subclass = object.__new__(_SelectionSubclass)
    with pytest.raises(ManagedGptSovitsValidationError):
        runtime.acquire_lease(forged_subclass)

    object.__setattr__(selection, "_provenance", object())
    with pytest.raises(ManagedGptSovitsValidationError):
        runtime.acquire_lease(selection)
    assert runtime.get_status().state == "idle"
    assert harness.events == []


def test_selection_is_detached_before_candidate_hashing_and_guard_io(
    tmp_path: Path,
) -> None:
    """Use one owned snapshot even if a caller mutates frozen inputs later."""

    harness = _Harness()
    snapshot_complete = Event()
    continue_hashing = Event()
    first_hash = True

    def blocking_hash(_path: Path) -> tuple[int, str]:
        """Expose the first post-snapshot filesystem boundary to this test."""

        nonlocal first_hash
        if first_hash:
            first_hash = False
            snapshot_complete.set()
            assert continue_hashing.wait(1.0)
        return 10, "f" * 64

    selection = _selection(tmp_path)
    original_asset = selection.gpt_weights  # type: ignore[attr-defined]
    outcomes: Queue[object] = Queue()
    runtime = _runtime(
        tmp_path, harness, candidate_hash_factory=blocking_hash
    )

    def acquire() -> None:
        """Acquire while the caller retains and later mutates the selection."""

        try:
            outcomes.put(runtime.acquire_lease(selection))
        except BaseException as error:
            outcomes.put(error)

    thread = Thread(target=acquire)
    thread.start()
    assert snapshot_complete.wait(1.0)
    object.__setattr__(selection, "prompt_text", "mutated secret")
    object.__setattr__(original_asset, "size_bytes", 999_999)
    continue_hashing.set()
    thread.join(2.0)
    assert not thread.is_alive()
    outcome = outcomes.get_nowait()
    assert isinstance(outcome, ManagedGptSovitsLease)
    init = harness.protocol.writes[0].metadata
    assert init["prompt_text"] == "Private reference words."
    assert isinstance(init["gpt_weights"], dict)
    assert init["gpt_weights"]["bytes"] == 123
    assert harness.declarations[0].size_bytes == 123
    outcome.close()


def test_non_windows_status_and_acquisition_are_stably_unavailable(
    tmp_path: Path,
) -> None:
    """Import safely and avoid all probes when Windows primitives are absent."""

    harness = _Harness()
    runtime = _runtime(tmp_path, harness, platform_name="posix")

    assert runtime.get_status().state == "unavailable"
    with pytest.raises(ManagedGptSovitsUnavailableError):
        runtime.acquire_lease(_selection(tmp_path))
    assert harness.events == []


@pytest.mark.skipif(os.name != "nt", reason="Windows directory APIs are required.")
def test_production_environment_ignores_parent_secrets_and_systemroot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Derive both OS paths from kernel32 and expose no inherited variables."""

    monkeypatch.setenv("SystemRoot", r"D:\AttackerRoot")
    monkeypatch.setenv("USERPROFILE", r"D:\SecretProfile")
    monkeypatch.setenv("PYTHONPATH", r"D:\secret-python")
    monkeypatch.setenv("ELYISIA_TEST_SECRET", "must-not-leak")

    environment = managed_module._minimal_environment()
    assert environment == {
        "LANGUAGE": "en_US",
        "PATH": managed_module._authoritative_windows_directory(
            "GetSystemDirectoryW"
        ),
        "SystemRoot": managed_module._authoritative_windows_directory(
            "GetSystemWindowsDirectoryW"
        ),
        "USERPROFILE": managed_module._authoritative_windows_directory(
            "GetSystemWindowsDirectoryW"
        ),
    }
    assert "AttackerRoot" not in repr(environment)
    assert "SecretProfile" not in repr(environment)
    assert "secret" not in repr(environment)


@pytest.mark.parametrize(
    ("operation", "failed_descriptor_index"),
    (("write", 0), ("copy", 1)),
)
def test_bootstrap_descriptor_close_failure_retains_exact_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    failed_descriptor_index: int,
) -> None:
    """Fail closed instead of forgetting an ambiguous bootstrap descriptor."""

    source = tmp_path / "source.bin"
    source.write_bytes(b"guarded bootstrap bytes")
    destination = tmp_path / f"{operation}.bin"
    original_open = managed_module.os.open
    original_close = managed_module.os.close
    opened: list[int] = []

    def observed_open(*args: object, **kwargs: object) -> int:
        """Record each real descriptor while preserving normal open behavior."""

        descriptor = original_open(*args, **kwargs)  # type: ignore[arg-type]
        opened.append(descriptor)
        return descriptor

    def fail_selected_close(descriptor: int) -> None:
        """Leave only the selected descriptor open with an ambiguous result."""

        if descriptor == opened[failed_descriptor_index]:
            raise OSError("simulated descriptor close failure")
        original_close(descriptor)

    previous_latch = managed_module._GLOBAL_CLEANUP_POISONED
    before = len(managed_module._QUARANTINED_GUARDS)
    owner: object | None = None
    try:
        with monkeypatch.context() as scoped_patch:
            scoped_patch.setattr(managed_module.os, "open", observed_open)
            scoped_patch.setattr(managed_module.os, "close", fail_selected_close)
            with pytest.raises(ManagedGptSovitsStartupError):
                if operation == "write":
                    managed_module._write_exclusive_file(destination, b"content")
                else:
                    managed_module._copy_exclusive_file(source, destination)

        assert len(opened) == failed_descriptor_index + 1
        assert len(managed_module._QUARANTINED_GUARDS) == before + 1
        owner = managed_module._QUARANTINED_GUARDS[-1]
        assert type(owner) is managed_module._DescriptorOwner
        assert owner._descriptor == opened[failed_descriptor_index]  # type: ignore[attr-defined]
        assert managed_module._GLOBAL_CLEANUP_POISONED is True
    finally:
        del managed_module._QUARANTINED_GUARDS[before:]
        managed_module._GLOBAL_CLEANUP_POISONED = previous_latch
        if type(owner) is managed_module._DescriptorOwner:
            owner.close()  # type: ignore[attr-defined]


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_bootstrap_factory_owns_partial_destination_before_helper_returns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_fails: bool,
) -> None:
    """Clean or quarantine a partial file that its helper never returned."""

    token = "partial-bootstrap"
    layout = _bootstrap_failure_layout(tmp_path)
    directory = layout.runtime_root / f".elysia-managed-{token}"
    partial = directory / managed_module._BOOTSTRAP_COPY_ROLES[0][1]
    mapping = _FakeGuard([], "mapping-close")
    previous_latch = managed_module._GLOBAL_CLEANUP_POISONED
    before = len(managed_module._QUARANTINED_GUARDS)
    cleanup_owner: object | None = None
    managed_module._GLOBAL_CLEANUP_POISONED = False

    def create_directory(path: Path) -> None:
        """Model one successful exclusive directory creation."""

        path.mkdir(parents=True)

    def leave_partial_then_fail(_source: Path, destination: Path) -> tuple[int, str]:
        """Create a partial destination before withholding the helper result."""

        destination.write_bytes(b"partial")
        raise ManagedGptSovitsStartupError("simulated partial copy")

    try:
        with monkeypatch.context() as scoped_patch:
            scoped_patch.setattr(managed_module.secrets, "token_hex", lambda _size: token)
            scoped_patch.setattr(
                managed_module,
                "_create_dos_device_mapping",
                lambda _path: mapping,
            )
            scoped_patch.setattr(
                managed_module,
                "_create_restricted_bootstrap_directory",
                create_directory,
            )
            scoped_patch.setattr(
                managed_module, "_copy_exclusive_file", leave_partial_then_fail
            )
            if cleanup_fails:
                scoped_patch.setattr(
                    managed_module,
                    "_cleanup_bootstrap_paths",
                    lambda _directory, _files: False,
                )
            with pytest.raises(ManagedGptSovitsStartupError):
                managed_module._default_bootstrap_factory(layout)

        assert mapping.closed is True
        if cleanup_fails:
            assert directory.is_dir()
            assert partial.read_bytes() == b"partial"
            assert len(managed_module._QUARANTINED_GUARDS) == before + 1
            cleanup_owner = managed_module._QUARANTINED_GUARDS[-1]
            assert type(cleanup_owner) is managed_module._BootstrapCleanupOwner
            assert getattr(cleanup_owner, "_directory_created") is True
            assert getattr(cleanup_owner, "_files") == [partial]
            assert managed_module._GLOBAL_CLEANUP_POISONED is True
        else:
            assert not directory.exists()
            assert len(managed_module._QUARANTINED_GUARDS) == before
            assert managed_module._GLOBAL_CLEANUP_POISONED is False
    finally:
        del managed_module._QUARANTINED_GUARDS[before:]
        if type(cleanup_owner) is managed_module._BootstrapCleanupOwner:
            getattr(cleanup_owner, "close")()
        managed_module._GLOBAL_CLEANUP_POISONED = previous_latch


def test_bootstrap_factory_never_removes_an_unowned_name_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave a same-name directory intact when exclusive creation never succeeded."""

    token = "existing-bootstrap"
    layout = _bootstrap_failure_layout(tmp_path)
    directory = layout.runtime_root / f".elysia-managed-{token}"
    directory.mkdir(parents=True)
    sentinel = directory / "owner-data.bin"
    sentinel.write_bytes(b"not this transaction")
    mapping = _FakeGuard([], "mapping-close")
    previous_latch = managed_module._GLOBAL_CLEANUP_POISONED
    before = len(managed_module._QUARANTINED_GUARDS)
    managed_module._GLOBAL_CLEANUP_POISONED = False

    def reject_collision(path: Path) -> None:
        """Model CreateDirectoryW rejecting the already-owned name."""

        assert path == directory
        raise ManagedGptSovitsStartupError("simulated name collision")

    try:
        monkeypatch.setattr(managed_module.secrets, "token_hex", lambda _size: token)
        monkeypatch.setattr(
            managed_module, "_create_dos_device_mapping", lambda _path: mapping
        )
        monkeypatch.setattr(
            managed_module,
            "_create_restricted_bootstrap_directory",
            reject_collision,
        )
        with pytest.raises(ManagedGptSovitsStartupError):
            managed_module._default_bootstrap_factory(layout)

        assert sentinel.read_bytes() == b"not this transaction"
        assert mapping.closed is True
        assert len(managed_module._QUARANTINED_GUARDS) == before
        assert managed_module._GLOBAL_CLEANUP_POISONED is False
    finally:
        managed_module._GLOBAL_CLEANUP_POISONED = previous_latch
        sentinel.unlink()
        directory.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Windows DACLs are required.")
def test_sealed_bootstrap_directory_rejects_new_entries(tmp_path: Path) -> None:
    """Permit bootstrap cleanup while denying late Python or DLL injection."""

    directory = tmp_path / "bootstrap"
    managed_module._create_restricted_bootstrap_directory(directory)
    existing = directory / "python.exe"
    existing.write_bytes(b"bootstrap")
    managed_module._seal_restricted_bootstrap_directory(directory)

    try:
        assert existing.read_bytes() == b"bootstrap"
        with pytest.raises(OSError):
            (directory / "hashlib.py").write_bytes(b"shadow")
        with pytest.raises(OSError):
            (directory / "version.dll").write_bytes(b"shadow")
    finally:
        existing.unlink()
        directory.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Windows directory handles are required.")
def test_bootstrap_directory_owner_rejects_preopened_add_file_handle(
    tmp_path: Path,
) -> None:
    """Detect a write-capable handle acquired before the directory DACL seal."""

    import ctypes
    from ctypes import wintypes

    directory = tmp_path / "bootstrap-preopened"
    existing = directory / "python.exe"
    managed_module._create_restricted_bootstrap_directory(directory)
    existing.write_bytes(b"bootstrap")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    add_file_handle = kernel32.CreateFileW(
        str(directory),
        0x00000002,  # FILE_ADD_FILE
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x02000000,
        None,
    )
    add_file_value = ctypes.cast(add_file_handle, ctypes.c_void_p).value
    assert add_file_value not in (None, ctypes.c_void_p(-1).value)
    owner = managed_module._BootstrapDirectoryOwner()
    try:
        managed_module._seal_restricted_bootstrap_directory(directory)
        with pytest.raises(ManagedGptSovitsStartupError):
            owner.acquire(directory)
    finally:
        assert kernel32.CloseHandle(add_file_handle)

    try:
        owner.acquire(directory)
        assert owner._handle != 0
    finally:
        owner.close()
        assert managed_module._cleanup_bootstrap_paths(
            directory, (existing,)
        )


def test_handshake_synthesis_ids_full_frames_environment_and_graceful_close(
    tmp_path: Path,
) -> None:
    """Bind exact INIT/READY, return WAVs, and serialize monotonic requests."""

    harness = _Harness()
    runtime = _runtime(tmp_path, harness)
    lease = runtime.acquire_lease(_selection(tmp_path))

    assert len(harness.declarations) == (
        5 + len(managed_module._RUNTIME_MANIFEST_RELATIVE_FILES)
    )
    assert harness.events[:7] == [
        "guard-acquire",
        "stable-paths",
        "manifest",
        "bootstrap",
        "environment",
        "launch",
        "child-ends-close",
    ]
    argv, launch = harness.launch_calls[0]
    assert argv[1:4] == ("-I", "-B", "-u")
    assert argv[-2].endswith("gpt_sovits_worker.py")
    assert argv[0].startswith("R:\\")
    assert argv[-2].startswith(str(_VOLUME_ROOT))
    assert argv[-1] == r"R:\runtime"
    assert isinstance(launch["cwd"], str)
    assert launch["cwd"].startswith(str(_VOLUME_ROOT))
    assert str(_config(tmp_path).runtime_root) not in argv
    environment = launch["environment"]
    assert isinstance(environment, dict)
    assert set(environment) == {
        "LANGUAGE",
        "PATH",
        "SystemRoot",
        "USERPROFILE",
    }
    assert environment["LANGUAGE"] == "en_US"
    assert "PYTHONPATH" not in environment
    assert "PYTHONHOME" not in environment
    assert environment["PATH"] == r"C:\Windows\System32"
    assert environment["USERPROFILE"] == r"C:\Windows"
    assert lease.cache_eligible is False
    assert not hasattr(lease, "binding_verified")
    safe_repr = repr(lease) + repr(runtime) + repr(_config(tmp_path))
    assert _CHALLENGE not in safe_repr
    assert "Private reference words" not in safe_repr
    assert str(tmp_path) not in safe_repr

    first = lease.synthesize("First sentence.", "en", _operation_token("job-1"))
    second = lease.synthesize("第二句话。", "zh", _operation_token("job-2"))
    assert first.audio == _wav_bytes()
    assert first.audio_format == "wav"
    assert second.audio == _wav_bytes()
    synth_frames = [
        frame
        for frame in harness.protocol.writes
        if frame.kind is FrameKind.SYNTHESIZE
    ]
    assert [frame.request_id for frame in synth_frames] == [1, 2]
    assert synth_frames[0].metadata == {
        "challenge": _CHALLENGE,
        "schema": 1,
        "text": "First sentence.",
        "text_language": "en",
    }

    raw = harness.transport.request_stream.getvalue()  # type: ignore[attr-defined]
    wire = BytesIO(raw)
    init_frame = read_frame(wire)
    assert init_frame.kind is FrameKind.INIT
    init_metadata = init_frame.metadata
    runtime_root = init_metadata["runtime_root"]
    gpt_weights = init_metadata["gpt_weights"]
    assert isinstance(runtime_root, str)
    assert isinstance(gpt_weights, dict)
    gpt_path = gpt_weights["path"]
    assert isinstance(gpt_path, str)
    assert runtime_root.startswith(str(_VOLUME_ROOT))
    assert gpt_path.startswith(str(_VOLUME_ROOT))
    assert read_frame(wire).request_id == 1
    assert read_frame(wire).request_id == 2
    assert wire.read() == b""

    lease.close()
    assert runtime.get_status().state == "idle"
    assert harness.events[-4:] == [
        "process-terminate",
        "transport-close",
        "bootstrap-guard-close",
        "guard-close",
    ]


@pytest.mark.skipif(os.name != "nt", reason="Volume-GUID guards require Windows.")
def test_real_guard_exports_stable_paths_used_by_every_launch_surface(
    tmp_path: Path,
) -> None:
    """Integrate the native guard accessor without loading models or a child."""

    runtime_root = (tmp_path / "runtime-root").resolve()
    for index, (_role, relative) in enumerate(
        managed_module._RUNTIME_MANIFEST_RELATIVE_FILES
    ):
        path = runtime_root / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"anchor-{index}".encode("ascii"))
    harness = _Harness()
    dependencies = managed_module._RuntimeDependencies(
        launcher=harness.launch,
        guard_acquirer=managed_module._default_guard_acquirer,
        guarded_paths_getter=managed_module._default_guarded_paths_getter,
        declaration_factory=managed_module._default_declaration_factory,
        manifest_digest_factory=managed_module._default_manifest_digest_factory,
        candidate_hash_factory=managed_module._hash_candidate_file,
        transport_factory=lambda: harness.transport,
        challenge_factory=lambda: _CHALLENGE,
        reader=harness.protocol.read,
        writer=harness.protocol.write,
        bootstrap_factory=harness.bootstrap,
        environment_factory=harness.environment,
        platform_name="nt",
    )
    runtime = ManagedGptSovitsRuntime._create_for_testing(
        ManagedGptSovitsConfig(
            runtime_root=runtime_root,
            worker_script=managed_module._CANONICAL_WORKER_SCRIPT,
            startup_timeout_seconds=1.0,
            synthesis_timeout_seconds=1.0,
            stop_timeout_seconds=1.0,
            device="cpu",
        ),
        dependency_seal=managed_module._TEST_DEPENDENCY_SEAL,
        dependencies=dependencies,
    )

    lease = runtime.acquire_lease(_materialized_selection(tmp_path))
    argv, launch = harness.launch_calls[0]
    assert argv[0].startswith("R:\\")
    assert argv[-2].casefold().startswith(r"\\?\volume{")
    assert argv[-1] == r"R:\runtime"
    assert isinstance(launch["cwd"], str)
    assert launch["cwd"].casefold().startswith(r"\\?\volume{")
    environment = launch["environment"]
    assert isinstance(environment, dict)
    assert environment["PATH"] == r"C:\Windows\System32"
    init = harness.protocol.writes[0].metadata
    assert isinstance(init["runtime_root"], str)
    assert init["runtime_root"].casefold().startswith(r"\\?\volume{")
    for key in ("gpt_weights", "sovits_weights", "reference_audio"):
        declaration = init[key]
        assert isinstance(declaration, dict)
        assert isinstance(declaration["path"], str)
        assert declaration["path"].casefold().startswith(r"\\?\volume{")
    lease.close()


@pytest.mark.skipif(os.name != "nt", reason="Bundled Python requires Windows.")
def test_guarded_bootstrap_runs_bundled_python_with_stable_import_paths() -> None:
    """Launch bundled Python through a verified temporary DOS-device alias.

    The probe imports every guarded compiled extension plus SSL/SQLite and
    executes the guarded worker as a non-main module.  This is the regression
    test for CPython 3.9 rejecting Volume-GUID and GLOBALROOT ``.pyd`` paths.
    """

    runtime_root = (
        Path(__file__).resolve().parents[1]
        / "models/cache/GPT-SoVITS-v2-240821"
    )
    if not (runtime_root / "runtime/python.exe").is_file():
        pytest.skip("The optional bundled GPT-SoVITS runtime is absent.")

    role_relatives = dict(managed_module._RUNTIME_MANIFEST_RELATIVE_FILES)
    guarded_roles = tuple(
        dict.fromkeys(
            [role for role, _filename in managed_module._BOOTSTRAP_COPY_ROLES]
            + [
                "runtime-python-path",
                "runtime-stdlib-zip",
                "runtime-hashlib-extension",
                "runtime-ssl-extension",
                "runtime-sqlite-extension",
                "runtime-ctypes-extension",
                "runtime-socket-extension",
                "runtime-select-extension",
                "runtime-unicode-extension",
            ]
        )
    )
    raw_paths = [
        managed_module._CANONICAL_WORKER_SCRIPT,
        managed_module._CANONICAL_WORKER_SCRIPT.with_name(
            "gpt_sovits_protocol.py"
        ),
        *(runtime_root / Path(role_relatives[role]) for role in guarded_roles),
    ]
    declarations = []
    for path in raw_paths:
        size_bytes, sha256 = managed_module._hash_candidate_file(path)
        declarations.append(
            managed_module._default_declaration_factory(
                path, size_bytes, sha256
            )
        )

    source_guard = managed_module._default_guard_acquirer(tuple(declarations))
    stable_values = managed_module._default_guarded_paths_getter(source_guard)
    stable_worker = Path(stable_values[0])
    stable_protocol = Path(stable_values[1])
    stable_by_role = {
        role: Path(value)
        for role, value in zip(guarded_roles, stable_values[2:])
    }
    stable_runtime_root = stable_by_role["runtime-python"].parent.parent
    identity_by_role = {
        role: managed_module._DeclaredIdentity(
            declaration.size_bytes, declaration.sha256
        )
        for role, declaration in zip(guarded_roles, declarations[2:])
    }
    layout = managed_module._GuardedLayout(
        stable_worker,
        stable_worker,
        stable_worker,
        stable_worker,
        stable_protocol,
        stable_runtime_root,
        runtime_root,
        tuple(
            stable_runtime_root / Path(relative)
            for _role, relative in managed_module._RUNTIME_MANIFEST_RELATIVE_FILES
        ),
        tuple(
            identity_by_role.get(
                role, managed_module._DeclaredIdentity(1, "0" * 64)
            )
            for role, _relative in managed_module._RUNTIME_MANIFEST_RELATIVE_FILES
        ),
    )

    bootstrap: managed_module._BootstrapLaunch | None = None
    process: object | None = None
    read_descriptor = write_descriptor = -1
    stdin_stream = stderr_stream = None
    combined: managed_module._CombinedGuard | None = None
    bootstrap_directory: Path | None = None
    try:
        bootstrap = managed_module._default_bootstrap_factory(layout)
        bootstrap_directory = bootstrap.stable_executable.parent
        managed_module._verify_production_bootstrap(bootstrap)
        with pytest.raises(OSError):
            # The directory handle denies write sharing, so an added module or
            # DLL cannot enter loader search after the exact-set inspection.
            (bootstrap_directory / "hashlib.py").write_bytes(b"shadow")
        combined = managed_module._CombinedGuard(
            source_guard, bootstrap.guard
        )
        import msvcrt

        read_descriptor, write_descriptor = os.pipe()
        stdin_stream = open(os.devnull, "rb")
        stderr_stream = open(os.devnull, "wb")
        code = (
            "import _ctypes,_hashlib,_socket,_sqlite3,_ssl,select;"
            "import sqlite3,ssl,unicodedata,runpy,sys;"
            "assert ssl.OPENSSL_VERSION;"
            "sqlite3.connect(':memory:').close();"
            "scope=runpy.run_path(sys.argv[1],run_name='_elysia_probe');"
            "root=scope['_require_mapped_import_root'](sys.argv[2],"
            "__import__('pathlib').Path(sys.argv[3]));"
            "scope['_closed_bootstrap_sys_path'](root);"
            "sys.stdout.write('BOOTSTRAP_OK\\n');sys.stdout.flush()"
        )
        process = managed_module.launch_windows_managed_process(
            [
                bootstrap.executable,
                "-I",
                "-B",
                "-u",
                "-c",
                code,
                str(stable_worker),
                bootstrap.import_root,
                str(stable_runtime_root),
            ],
            stdin_handle=msvcrt.get_osfhandle(stdin_stream.fileno()),
            stdout_handle=msvcrt.get_osfhandle(write_descriptor),
            stderr_handle=msvcrt.get_osfhandle(write_descriptor),
            cwd=str(stable_runtime_root),
            environment=managed_module._minimal_environment(),
        )
        managed_module._verify_launched_process_image(process, bootstrap)
        os.close(write_descriptor)
        write_descriptor = -1
        exit_code = process.wait(20.0)  # type: ignore[attr-defined]
        output = os.read(read_descriptor, 16_384)
        assert exit_code == 0, output.decode("utf-8", errors="replace")
        assert output == b"BOOTSTRAP_OK\r\n"
        assert process.terminate(2.0) is True  # type: ignore[attr-defined]
        process = None
        combined.close()
        combined = None
        assert bootstrap_directory is not None
        assert not os.path.exists(str(bootstrap_directory))
        assert managed_module._query_dos_device(
            bootstrap.executable[:2]
        ) == ()
    finally:
        if process is not None and combined is not None:
            managed_module._terminate_then_release_or_quarantine(
                process, combined, 2.0  # type: ignore[arg-type]
            )
            combined = None
        if combined is not None:
            combined.close()
        elif bootstrap is not None:
            # Factory verification can fail before the two guards are combined.
            # Preserve each independent owner so the test cannot leak the DOS
            # alias or source handles into subsequent Windows test cases.
            managed_module._close_or_quarantine(bootstrap.guard)
            source_guard.close()
        else:
            source_guard.close()
        if write_descriptor >= 0:
            os.close(write_descriptor)
        if read_descriptor >= 0:
            os.close(read_descriptor)
        if stdin_stream is not None:
            stdin_stream.close()
        if stderr_stream is not None:
            stderr_stream.close()


def test_dos_mapping_skips_collisions_and_removes_exact_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use a free randomized drive and remove only its verified raw target."""

    native_target = r"\Device\HarddiskVolume9"
    volume_name = "Volume{11111111-1111-1111-1111-111111111111}"
    devices: dict[str, tuple[str, ...]] = {
        volume_name: (native_target,),
        "R:": (r"\Device\HarddiskVolume8",),
    }
    calls: list[tuple[int, str, str]] = []

    def define(flags: int, name: str, target: str) -> bool:
        """Model exact create/remove behavior while retaining all arguments."""

        calls.append((flags, name, target))
        if flags & 0x00000002:
            if devices.get(name) == (target,):
                devices.pop(name)
                return True
            return False
        devices[name] = (target,)
        return True

    monkeypatch.setattr(
        managed_module, "_candidate_dos_device_names", lambda: ("R:", "S:")
    )
    monkeypatch.setattr(managed_module, "_logical_drive_mask", lambda: 0)
    monkeypatch.setattr(
        managed_module, "_query_dos_device", lambda name: devices.get(name, ())
    )
    monkeypatch.setattr(managed_module, "_define_dos_device", define)
    monkeypatch.setattr(
        managed_module,
        "_query_volume_name",
        lambda root: (
            "\\\\?\\Volume{11111111-1111-1111-1111-111111111111}\\"
            if root == "S:\\"
            else ""
        ),
    )

    mapping = managed_module._create_dos_device_mapping(
        _VOLUME_ROOT / "runtime"
    )
    assert mapping.drive == "S:"
    assert mapping.path_for(_VOLUME_ROOT / "runtime/python.exe") == (
        r"S:\runtime\python.exe"
    )
    mapping.close()
    assert devices.get("S:", ()) == ()
    assert calls == [
        (0x00000009, "S:", native_target),
        (0x0000000F, "S:", native_target),
    ]


def test_dos_mapping_postcheck_failure_is_sanitized_and_rolled_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rollback the exact definition when its reached volume is inconsistent."""

    native_target = r"\Device\HarddiskVolume9"
    volume_name = "Volume{11111111-1111-1111-1111-111111111111}"
    devices: dict[str, tuple[str, ...]] = {volume_name: (native_target,)}
    removed: list[tuple[int, str, str]] = []

    def define(flags: int, name: str, target: str) -> bool:
        """Create normally but record the exact rollback operation."""

        if flags & 0x00000002:
            removed.append((flags, name, target))
            devices.pop(name, None)
        else:
            devices[name] = (target,)
        return True

    monkeypatch.setattr(
        managed_module, "_candidate_dos_device_names", lambda: ("S:",)
    )
    monkeypatch.setattr(managed_module, "_logical_drive_mask", lambda: 0)
    monkeypatch.setattr(
        managed_module, "_query_dos_device", lambda name: devices.get(name, ())
    )
    monkeypatch.setattr(managed_module, "_define_dos_device", define)
    monkeypatch.setattr(
        managed_module,
        "_query_volume_name",
        lambda _root: (
            "\\\\?\\Volume{22222222-2222-2222-2222-222222222222}\\"
        ),
    )

    with pytest.raises(ManagedGptSovitsStartupError) as captured:
        managed_module._create_dos_device_mapping(_VOLUME_ROOT / "runtime")
    assert str(_VOLUME_ROOT) not in str(captured.value)
    assert devices.get("S:", ()) == ()
    assert removed == [(0x0000000F, "S:", native_target)]


def test_pipe_child_end_close_failure_retains_descriptor_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep native descriptor ownership when the first close attempt fails."""

    transport = managed_module._PipeTransport()
    original_close = managed_module.os.close
    failed_descriptor = transport._child_fds[0]
    first = True

    def flaky_close(descriptor: int) -> None:
        """Fail one selected descriptor once and close every other descriptor."""

        nonlocal first
        if descriptor == failed_descriptor and first:
            first = False
            raise OSError("private descriptor detail")
        original_close(descriptor)

    monkeypatch.setattr(managed_module.os, "close", flaky_close)
    with pytest.raises(ManagedGptSovitsStartupError):
        transport.close_child_ends()
    assert transport._child_fds == [failed_descriptor]
    transport.close_child_ends()
    transport.close()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.__setitem__("challenge", "0" * 64),
        lambda value: value.__setitem__("binding_sha256", "0" * 64),
        lambda value: value.__setitem__("schema", 2),
        lambda value: value.__setitem__("extra", "field"),
    ],
)
def test_ready_mismatch_poisoned_without_retry(
    tmp_path: Path,
    mutation: Callable[[dict[str, object]], None],
) -> None:
    """Reject every READY ambiguity, clean up, and permanently forbid retry."""

    protocol = _ScriptedProtocol(ready_mutation=mutation)
    harness = _Harness(protocol)
    protocol._closed_event = harness.closed_event
    runtime = _runtime(tmp_path, harness)

    with pytest.raises(ManagedGptSovitsStartupError) as captured:
        runtime.acquire_lease(_selection(tmp_path))
    assert str(tmp_path) not in str(captured.value)
    _wait_for(harness.process.terminated)
    assert runtime.get_status().state == "poisoned"
    with pytest.raises(ManagedGptSovitsUnavailableError):
        runtime.acquire_lease(_selection(tmp_path))
    assert len(harness.launch_calls) == 1


def test_startup_error_and_timeout_are_sanitized_and_cleanup_once(
    tmp_path: Path,
) -> None:
    """Treat code-only ERROR and absent READY as terminal startup failures."""

    error_harness = _Harness(_ScriptedProtocol(startup_error="engine_failed"))
    error_runtime = _runtime(tmp_path, error_harness)
    with pytest.raises(ManagedGptSovitsStartupError):
        error_runtime.acquire_lease(_selection(tmp_path))
    _wait_for(error_harness.process.terminated)

    closed_event = Event()
    blocked_protocol = _ScriptedProtocol(
        block_startup=True, closed_event=closed_event
    )
    timeout_harness = _Harness(blocked_protocol)
    timeout_harness.closed_event = closed_event
    timeout_harness.transport._closed_event = closed_event
    timeout_dependencies = managed_module._RuntimeDependencies(
        launcher=timeout_harness.launch,
        guard_acquirer=timeout_harness.acquire_guard,
        guarded_paths_getter=timeout_harness.guarded_paths,
        declaration_factory=_Declaration,
        manifest_digest_factory=timeout_harness.manifest_digest,
        candidate_hash_factory=lambda _path: (10, "f" * 64),
        transport_factory=lambda: timeout_harness.transport,
        challenge_factory=lambda: _CHALLENGE,
        reader=blocked_protocol.read,
        writer=blocked_protocol.write,
        bootstrap_factory=timeout_harness.bootstrap,
        environment_factory=timeout_harness.environment,
        platform_name="nt",
    )
    timeout_runtime = ManagedGptSovitsRuntime._create_for_testing(
        _config(tmp_path, startup_timeout_seconds=0.02),
        dependency_seal=managed_module._TEST_DEPENDENCY_SEAL,
        dependencies=timeout_dependencies,
    )
    started = monotonic()
    with pytest.raises(ManagedGptSovitsStartupError):
        timeout_runtime.acquire_lease(_selection(tmp_path))
    assert monotonic() - started < 0.5
    _wait_for(timeout_harness.process.terminated)
    assert timeout_runtime.get_status().state == "poisoned"


def test_guard_failure_prevents_process_creation(tmp_path: Path) -> None:
    """Never spawn when the atomic held-file transaction does not succeed."""

    harness = _Harness()

    def fail_guard(_declarations: tuple[Any, ...]) -> _FakeGuard:
        """Model one sanitized guard acquisition failure."""

        raise RuntimeError("private guard detail")

    runtime = _runtime(tmp_path, harness, guard_acquirer=fail_guard)
    with pytest.raises(ManagedGptSovitsStartupError) as captured:
        runtime.acquire_lease(_selection(tmp_path))
    assert "private guard detail" not in str(captured.value)
    assert harness.launch_calls == []
    assert runtime.get_status().state == "poisoned"


def test_prelaunch_transport_close_failure_cannot_skip_guard_cleanup(
    tmp_path: Path,
) -> None:
    """Retain the preallocated composite when its transport close is ambiguous."""

    harness = _Harness()
    transport = _FailOnceCloseTransport(harness.events, harness.closed_event)
    harness.transport = transport

    def fail_launch(_argv: list[str], **_options: object) -> _FakeProcess:
        """Fail after pipe creation but before process ownership exists."""

        raise RuntimeError("simulated launch failure")

    runtime = _runtime(tmp_path, harness, launcher=fail_launch)
    previous_latch = managed_module._GLOBAL_CLEANUP_POISONED
    before = len(managed_module._QUARANTINED_GUARDS)
    quarantined: object | None = None
    try:
        with pytest.raises(ManagedGptSovitsStartupError):
            runtime.acquire_lease(_selection(tmp_path))

        assert runtime.get_status().state == "poisoned"
        assert harness.guard.closed is True
        assert harness.bootstrap_guard.closed is True
        assert managed_module._GLOBAL_CLEANUP_POISONED is True
        assert len(managed_module._QUARANTINED_GUARDS) == before + 1
        quarantined = managed_module._QUARANTINED_GUARDS[-1]
        assert type(quarantined) is managed_module._ProcessTransportGuardOwner
        assert getattr(quarantined, "_process") is None
        assert getattr(quarantined, "_transport") is transport
        assert type(getattr(quarantined, "_guard")) is managed_module._CombinedGuard
    finally:
        del managed_module._QUARANTINED_GUARDS[before:]
        managed_module._GLOBAL_CLEANUP_POISONED = previous_latch
        if type(quarantined) is managed_module._ProcessTransportGuardOwner:
            quarantined.close()


def test_child_endpoint_close_failure_does_not_touch_parent_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid closing a parent reader while its child writer may remain open."""

    transport = object.__new__(managed_module._PipeTransport)
    request_stream = BytesIO()
    response_stream = BytesIO()
    transport.request_stream = request_stream
    transport.response_stream = response_stream
    transport.stdin_handle = 101
    transport.stdout_handle = 102
    transport.stderr_handle = 103
    transport._child_fds = [201, 202, 203]
    transport._closed = False
    closed_descriptors: list[int] = []

    def close_descriptor(descriptor: int) -> None:
        """Keep the response writer ambiguous while closing its peers."""

        if descriptor == 202:
            raise OSError("simulated response-writer close failure")
        closed_descriptors.append(descriptor)

    monkeypatch.setattr(managed_module.os, "close", close_descriptor)

    with pytest.raises(ManagedGptSovitsStartupError):
        transport.close()

    assert closed_descriptors == [201, 203]
    assert transport._child_fds == [202]
    assert request_stream.closed is False
    assert response_stream.closed is False
    assert transport._closed is False


@pytest.mark.parametrize("failure", ["drive-path", "wrong-order", "manifest"])
def test_stable_guarded_layout_and_manifest_are_required_before_launch(
    tmp_path: Path,
    failure: str,
) -> None:
    """Reject raw aliases, reordered anchors, or a second-pass hash mismatch."""

    harness = _Harness()

    def failing_paths(_guard: object) -> tuple[str, ...]:
        """Return the selected malformed path-set variant."""

        if failure == "drive-path":
            return tuple(str(value.path) for value in harness.declarations)
        stable = list(harness.guarded_paths(_guard))
        stable[5], stable[6] = stable[6], stable[5]
        return tuple(stable)

    changes: dict[str, object] = {}
    if failure == "manifest":
        changes["manifest_digest_factory"] = (
            lambda _root, _worker, _protocol: "0" * 64
        )
    else:
        changes["guarded_paths_getter"] = failing_paths
    runtime = _runtime(tmp_path, harness, **changes)

    with pytest.raises(ManagedGptSovitsStartupError):
        runtime.acquire_lease(_selection(tmp_path))
    assert harness.launch_calls == []
    assert harness.guard.closed is True
    assert runtime.get_status().state == "poisoned"


def test_owner_construction_failure_precedes_launch_and_closes_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Allocate the whole cleanup owner before any process can be created."""

    harness = _Harness()

    def fail_owner(*_args: object, **_kwargs: object) -> Any:
        """Model persistent cleanup-owner allocation failure before launch."""

        raise MemoryError("simulated owner allocation failure")

    monkeypatch.setattr(managed_module, "_OwnedResources", fail_owner)
    runtime = _runtime(tmp_path, harness)
    previous_latch = managed_module._GLOBAL_CLEANUP_POISONED
    before = len(managed_module._QUARANTINED_GUARDS)

    with pytest.raises(ManagedGptSovitsStartupError):
        runtime.acquire_lease(_selection(tmp_path))
    assert harness.launch_calls == []
    assert harness.process.terminated.is_set() is False
    assert harness.transport._closed is True
    assert harness.guard.closed is True
    assert harness.bootstrap_guard.closed is True
    assert len(managed_module._QUARANTINED_GUARDS) == before
    assert managed_module._GLOBAL_CLEANUP_POISONED is previous_latch


def test_persistent_cleanup_lock_failure_occurs_before_process_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject cleanup-lock allocation while every resource is still unlaunched."""

    harness = _Harness()
    real_lock = managed_module.Lock
    lock_calls = 0

    def fail_after_combined_guard() -> object:
        """Allow the combined guard lock, then fail every cleanup-owner lock."""

        nonlocal lock_calls
        lock_calls += 1
        if lock_calls == 1:
            return real_lock()
        raise MemoryError("simulated persistent lock allocation failure")

    monkeypatch.setattr(managed_module, "Lock", fail_after_combined_guard)
    runtime = _runtime(tmp_path, harness)

    with pytest.raises(ManagedGptSovitsStartupError):
        runtime.acquire_lease(_selection(tmp_path))

    assert lock_calls >= 2
    assert harness.launch_calls == []
    assert harness.process.terminated.is_set() is False
    assert harness.transport._closed is True
    assert harness.guard.closed is True
    assert harness.bootstrap_guard.closed is True
    assert runtime.get_status().state == "poisoned"


def test_interrupted_process_adoption_is_rescued_before_guard_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transfer a live launcher result even when normal adoption is interrupted."""

    harness = _Harness()

    def interrupt_adoption(
        _resources: object, _process: object
    ) -> None:
        """Inject an asynchronous-style failure at the ownership boundary."""

        raise KeyboardInterrupt("simulated adoption interruption")

    monkeypatch.setattr(
        managed_module._OwnedResources,
        "adopt_process",
        interrupt_adoption,
    )
    runtime = _runtime(tmp_path, harness)
    before = len(managed_module._QUARANTINED_GUARDS)

    with pytest.raises(ManagedGptSovitsStartupError):
        runtime.acquire_lease(_selection(tmp_path))

    assert harness.process.terminated.is_set()
    assert harness.guard.closed is True
    assert harness.bootstrap_guard.closed is True
    assert len(managed_module._QUARANTINED_GUARDS) == before
    assert harness.events.index("process-terminate") < harness.events.index(
        "guard-close"
    )
    assert runtime.get_status().state == "poisoned"


@pytest.mark.parametrize("termination_raises", [False, True])
def test_ambiguous_termination_quarantine_is_the_exact_process_owner(
    tmp_path: Path,
    termination_raises: bool,
) -> None:
    """Retain process plus guard without relying on a harness-owned process."""

    events: list[str] = []
    guard = _FakeGuard(events)
    process = _FakeProcess(
        events,
        termination_result=False,
        termination_raises=termination_raises,
    )
    process_reference = weakref.ref(process)
    previous_latch = managed_module._GLOBAL_CLEANUP_POISONED
    before = len(managed_module._QUARANTINED_GUARDS)
    quarantined: object | None = None
    managed_module._GLOBAL_CLEANUP_POISONED = False
    try:
        assert (
            managed_module._terminate_then_release_or_quarantine(
                process, guard, 0.1
            )
            is False
        )
        del process
        gc.collect()

        assert len(managed_module._QUARANTINED_GUARDS) == before + 1
        quarantined = managed_module._QUARANTINED_GUARDS[-1]
        retained_process = process_reference()
        assert retained_process is not None
        assert type(quarantined) is managed_module._ProcessGuardOwner
        assert getattr(quarantined, "_process") is retained_process
        assert getattr(quarantined, "_guard") is guard
        assert guard.closed is False
        assert managed_module._GLOBAL_CLEANUP_POISONED is True
        _assert_production_cleanup_latch(tmp_path)
    finally:
        del managed_module._QUARANTINED_GUARDS[before:]
        retained_process = process_reference()
        if retained_process is not None:
            retained_process._termination_raises = False
            retained_process._termination_result = True
        if type(quarantined) is managed_module._ProcessGuardOwner:
            getattr(quarantined, "close")()
        managed_module._GLOBAL_CLEANUP_POISONED = previous_latch


def test_persistent_transport_close_failure_retains_exact_owner(
    tmp_path: Path,
) -> None:
    """Quarantine one composite owner after process-first pipe close fails."""

    events: list[str] = []
    closed_event = Event()
    transport = _FailUntilReleasedTransport(
        events, closed_event
    )
    process = _FakeProcess(events)
    guard = _FakeGuard(events)
    transport_reference = weakref.ref(transport)
    process_reference = weakref.ref(process)
    guard_reference = weakref.ref(guard)
    resources = managed_module._OwnedResources(
        transport,
        process,
        guard,
    )
    previous_latch = managed_module._GLOBAL_CLEANUP_POISONED
    before = len(managed_module._QUARANTINED_GUARDS)
    quarantined: object | None = None
    managed_module._GLOBAL_CLEANUP_POISONED = False
    try:
        assert resources.close(0.1) is False
        assert transport.close_attempts == 1
        del transport
        del process
        del guard
        del resources
        gc.collect()

        retained_transport = transport_reference()
        retained_process = process_reference()
        retained_guard = guard_reference()
        assert retained_transport is not None
        assert retained_process is not None
        assert retained_guard is not None
        quarantined = managed_module._QUARANTINED_GUARDS[-1]
        assert type(quarantined) is managed_module._ProcessTransportGuardOwner
        assert getattr(quarantined, "_transport") is retained_transport
        assert getattr(quarantined, "_process") is retained_process
        assert getattr(quarantined, "_guard") is retained_guard
        assert managed_module._GLOBAL_CLEANUP_POISONED is True
        _assert_production_cleanup_latch(tmp_path)
    finally:
        del managed_module._QUARANTINED_GUARDS[before:]
        retained_transport = transport_reference()
        if retained_transport is not None:
            retained_transport.allow_close = True
        if type(quarantined) is managed_module._ProcessTransportGuardOwner:
            getattr(quarantined, "close")()
        managed_module._GLOBAL_CLEANUP_POISONED = previous_latch


def test_cleanup_owner_survives_quarantine_list_allocation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retain the composite through its preallocated link if append cannot grow."""

    class _RejectingLedger(list[object]):
        """Expose deterministic allocation failure from the quarantine ledger."""

        def append(self, _value: object) -> None:
            """Reject every attempted ledger growth."""

            raise MemoryError("simulated quarantine allocation failure")

    events: list[str] = []
    transport = _FakeTransport(events, Event())
    process = _FakeProcess(events, termination_result=False)
    guard = _FakeGuard(events)
    resources = managed_module._OwnedResources(transport, process, guard)
    owner = getattr(resources, "_cleanup_owner")
    previous_head = managed_module._EMERGENCY_CLEANUP_OWNER_HEAD
    previous_latch = managed_module._GLOBAL_CLEANUP_POISONED
    previous_pending = managed_module._GLOBAL_CLEANUP_PENDING
    monkeypatch.setattr(managed_module, "_QUARANTINED_GUARDS", _RejectingLedger())
    try:
        assert resources.close(0.1) is False
        assert resources.close(0.0) is False
        assert managed_module._GLOBAL_CLEANUP_POISONED is True
        assert managed_module._GLOBAL_CLEANUP_PENDING == previous_pending
        assert managed_module._EMERGENCY_CLEANUP_OWNER_HEAD is owner
        assert getattr(owner, "_process") is process
        assert guard.closed is False
    finally:
        process._termination_result = True
        owner.close()
        managed_module._EMERGENCY_CLEANUP_OWNER_HEAD = previous_head
        managed_module._GLOBAL_CLEANUP_POISONED = previous_latch
        managed_module._GLOBAL_CLEANUP_PENDING = previous_pending


def test_repeated_guard_quarantine_allocation_failures_retain_every_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chain existing failure tracebacks when no quarantine list can grow."""

    class _RejectingLedger(list[object]):
        """Reject all appends with distinct exceptions for retention chaining."""

        def append(self, _value: object) -> None:
            """Raise one fresh allocation failure per attempted owner."""

            raise MemoryError("simulated persistent ledger allocation failure")

    previous_fallback = managed_module._EMERGENCY_QUARANTINE_GUARD
    previous_errors = managed_module._EMERGENCY_QUARANTINE_ERROR_HEAD
    previous_latch = managed_module._GLOBAL_CLEANUP_POISONED
    monkeypatch.setattr(managed_module, "_QUARANTINED_GUARDS", _RejectingLedger())
    first = _FakeGuard([])
    second = _FakeGuard([])
    first_reference = weakref.ref(first)
    second_reference = weakref.ref(second)
    try:
        managed_module._quarantine_guard(first)
        managed_module._quarantine_guard(second)
        del first
        del second
        gc.collect()

        assert first_reference() is not None
        assert second_reference() is not None
        assert managed_module._GLOBAL_CLEANUP_POISONED is True
        assert managed_module._EMERGENCY_QUARANTINE_ERROR_HEAD is not None
    finally:
        managed_module._EMERGENCY_QUARANTINE_GUARD = previous_fallback
        managed_module._EMERGENCY_QUARANTINE_ERROR_HEAD = previous_errors
        managed_module._GLOBAL_CLEANUP_POISONED = previous_latch


def test_cleanup_uses_composite_owner_adopted_during_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid allocating a fallible resource holder after cleanup detaches it."""

    events: list[str] = []
    transport = _FakeTransport(events, Event())
    process = _FakeProcess(events)
    guard = _FakeGuard(events)
    resources = managed_module._OwnedResources(transport, process, guard)
    adopted_owner = getattr(resources, "_cleanup_owner")

    def fail_late_owner(*_args: object, **_kwargs: object) -> Any:
        """Prove teardown never constructs another composite owner."""

        raise MemoryError("simulated teardown owner allocation failure")

    monkeypatch.setattr(
        managed_module,
        "_ProcessTransportGuardOwner",
        fail_late_owner,
    )

    assert resources.close(0.1) is True
    assert getattr(resources, "_cleanup_owner") is adopted_owner
    assert events.index("process-terminate") < events.index("transport-close")
    assert events.index("transport-close") < events.index("guard-close")


def test_synthesis_error_and_invalid_audio_permanently_poison_lease(
    tmp_path: Path,
) -> None:
    """Fail closed for worker ERROR and independently malformed audio metadata."""

    error_harness = _Harness(
        _ScriptedProtocol(synthesis_error="synthesis_failed")
    )
    error_runtime = _runtime(tmp_path, error_harness)
    error_lease = error_runtime.acquire_lease(_selection(tmp_path))
    with pytest.raises(ManagedGptSovitsSynthesisError):
        error_lease.synthesize("Hello.", "en", _operation_token("error-job"))
    assert error_lease.poisoned is True
    assert error_runtime.get_status().state == "poisoned"

    bad_harness = _Harness(
        _ScriptedProtocol(
            audio_mutation=lambda value: value.__setitem__(
                "sample_rate", 48_000
            )
        )
    )
    bad_runtime = _runtime(tmp_path, bad_harness)
    bad_lease = bad_runtime.acquire_lease(_selection(tmp_path))
    with pytest.raises(ManagedGptSovitsSynthesisError):
        bad_lease.synthesize(
            "Hello.", "en", _operation_token("bad-audio-job")
        )
    assert bad_lease.poisoned is True

    with pytest.raises(managed_module.ManagedGptSovitsProtocolError):
        managed_module._require_managed_wav(
            _wav_bytes()[:44] + b"\x00\x00\x00\x00", 24_000
        )


def test_tokens_cannot_be_reused_and_stale_abort_is_noop(tmp_path: Path) -> None:
    """Prevent old cancellation identities from targeting later requests."""

    harness = _Harness()
    lease = _runtime(tmp_path, harness).acquire_lease(_selection(tmp_path))
    token = _operation_token("one-use-token")
    lease.synthesize("Hello.", "en", token)

    with pytest.raises(ManagedGptSovitsValidationError):
        lease.synthesize("Again.", "en", token)
    assert lease.abort(token) is False
    assert harness.process.terminated.is_set() is False
    assert lease.poisoned is False
    lease.close()


@pytest.mark.parametrize("token", ["a" * 63, "A" * 64, "g" * 64, "job-1"])
def test_lease_rejects_noncanonical_operation_tokens(
    tmp_path: Path, token: str
) -> None:
    """Accept only the queue's lowercase 256-bit operation identity."""

    harness = _Harness()
    lease = _runtime(tmp_path, harness).acquire_lease(_selection(tmp_path))
    writes_before = len(harness.protocol.writes)

    with pytest.raises(ManagedGptSovitsValidationError):
        lease.abort(token)
    with pytest.raises(ManagedGptSovitsValidationError):
        lease.synthesize("Must not start.", "en", token)
    assert len(harness.protocol.writes) == writes_before
    assert lease.poisoned is False
    lease.close()


def test_prestart_cancel_consumes_tombstone_before_id_or_pipe_io(
    tmp_path: Path,
) -> None:
    """Honor cancellation that wins the race before synthesis is submitted."""

    harness = _Harness()
    lease = _runtime(tmp_path, harness).acquire_lease(_selection(tmp_path))
    writes_before = len(harness.protocol.writes)

    prestart_token = _operation_token("prestart-token")
    assert lease.abort(prestart_token) is True
    with pytest.raises(ManagedGptSovitsCancelledError):
        lease.synthesize("Must never reach worker.", "en", prestart_token)
    assert len(harness.protocol.writes) == writes_before
    assert lease.abort(prestart_token) is False

    result = lease.synthesize(
        "First real request.", "en", _operation_token("live-token")
    )
    assert result.audio_format == "wav"
    synth_frames = [
        frame
        for frame in harness.protocol.writes
        if frame.kind is FrameKind.SYNTHESIZE
    ]
    assert [frame.request_id for frame in synth_frames] == [1]
    lease.close()


def test_cancel_tombstone_capacity_exhaustion_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Poison instead of evicting an acknowledged pre-start cancellation."""

    monkeypatch.setattr(
        managed_module, "MANAGED_GPT_SOVITS_MAX_CANCEL_TOMBSTONES", 2
    )
    harness = _Harness()
    runtime = _runtime(tmp_path, harness)
    lease = runtime.acquire_lease(_selection(tmp_path))

    assert lease.abort(_operation_token("cancel-1")) is True
    assert lease.abort(_operation_token("cancel-2")) is True
    with pytest.raises(ManagedGptSovitsUnavailableError):
        lease.abort(_operation_token("cancel-3"))
    _wait_for(harness.process.terminated)
    assert lease.poisoned is True
    assert runtime.get_status().state == "poisoned"
    assert not any(
        frame.kind is FrameKind.SYNTHESIZE
        for frame in harness.protocol.writes
    )


def test_matching_abort_wakes_io_quickly_and_poisoned_generation_stays_dead(
    tmp_path: Path,
) -> None:
    """Atomically match the active token, close pipes, and kill without retry."""

    closed_event = Event()
    protocol = _ScriptedProtocol(
        block_synthesis=True, closed_event=closed_event
    )
    harness = _Harness(protocol)
    harness.closed_event = closed_event
    harness.transport._closed_event = closed_event
    runtime = _runtime(tmp_path, harness)
    lease = runtime.acquire_lease(_selection(tmp_path))
    failures: list[BaseException] = []

    def synthesize() -> None:
        """Run one request that waits until abort closes its response pipe."""

        try:
            lease.synthesize(
                "Long sentence.", "en", _operation_token("current-token")
            )
        except BaseException as error:
            failures.append(error)

    worker = Thread(target=synthesize)
    worker.start()
    assert protocol.synthesis_started.wait(0.5)
    assert lease.abort(_operation_token("older-token")) is True
    started = monotonic()
    assert lease.abort(_operation_token("current-token")) is True
    assert monotonic() - started < 0.2
    worker.join(1.0)
    assert not worker.is_alive()
    _wait_for(harness.process.terminated)
    assert len(failures) == 1
    assert isinstance(failures[0], ManagedGptSovitsSynthesisError)
    assert lease.poisoned is True
    assert runtime.get_status().state == "poisoned"
    with pytest.raises(ManagedGptSovitsUnavailableError):
        lease.synthesize("Never retry.", "en", _operation_token("new-token"))


def test_runtime_shutdown_is_idempotent_and_hides_private_values(
    tmp_path: Path,
) -> None:
    """Detach one active lease once and keep all public diagnostics secret-safe."""

    harness = _Harness()
    runtime = _runtime(tmp_path, harness)
    lease = runtime.acquire_lease(_selection(tmp_path))

    runtime.shutdown()
    assert harness.process.terminated.is_set()
    assert harness.guard.closed is True
    assert harness.bootstrap_guard.closed is True
    runtime.shutdown()
    assert runtime.get_status().state == "closed"
    assert lease.closed is True
    assert harness.events.count("process-terminate") == 1
    assert harness.events.index("process-terminate") < harness.events.index(
        "guard-close"
    )


def test_async_cleanup_uses_a_non_daemon_process_exit_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep bounded cancellation cleanup alive through interpreter shutdown."""

    harness = _Harness()
    resources = managed_module._OwnedResources(
        harness.transport,
        harness.process,
        harness.guard,
    )
    captured: dict[str, object] = {}

    class _ImmediateThread:
        """Run one captured cleanup target without introducing test races."""

        def __init__(self, target: Callable[[], None]) -> None:
            """Retain the exact target supplied to the fake thread factory."""

            self._target = target

        def start(self) -> None:
            """Execute the captured target exactly once."""

            self._target()

    def capture_thread(
        *, target: Callable[[], None], name: str, daemon: bool
    ) -> _ImmediateThread:
        """Capture cleanup-thread policy and return a deterministic runner."""

        captured.update({"name": name, "daemon": daemon})
        return _ImmediateThread(target)

    monkeypatch.setattr(managed_module, "Thread", capture_thread)

    resources.close_async(1.0)

    assert captured == {
        "name": "elysia-managed-tts-cleanup",
        "daemon": False,
    }
    assert harness.process.terminated.is_set()
    assert harness.guard.closed is True


def test_async_cleanup_thread_construction_failure_finishes_synchronously(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recover ownership when even allocating the cleanup thread fails."""

    events: list[str] = []
    transport = _FakeTransport(events, Event())
    process = _FakeProcess(events)
    guard = _FakeGuard(events)
    resources = managed_module._OwnedResources(transport, process, guard)

    def fail_thread(*_args: object, **_kwargs: object) -> Any:
        """Inject allocation failure before a thread object owns the target."""

        raise MemoryError("simulated cleanup thread allocation failure")

    monkeypatch.setattr(managed_module, "Thread", fail_thread)

    resources.close_async(0.1)

    assert resources.close(0.0) is True
    assert process.terminated.is_set()
    assert transport._closed is True
    assert guard.closed is True
    assert events.index("process-terminate") < events.index("transport-close")
    assert events.index("transport-close") < events.index("guard-close")


def test_async_cleanup_thread_start_failure_cannot_run_cleanup_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ignore a fallback after a faulty start already executed its target."""

    events: list[str] = []
    resources = managed_module._OwnedResources(
        _FakeTransport(events, Event()),
        _FakeProcess(events),
        _FakeGuard(events),
    )
    previous_latch = managed_module._GLOBAL_CLEANUP_POISONED
    previous_pending = managed_module._GLOBAL_CLEANUP_PENDING

    class _StartedThenRaisedThread:
        """Model a start implementation that reports failure after execution."""

        def __init__(self, target: Callable[[], None]) -> None:
            """Retain the target for one synchronous start."""

            self._target = target

        def start(self) -> None:
            """Run the target once, then raise like a faulty thread boundary."""

            self._target()
            raise RuntimeError("simulated post-start failure")

    def faulty_thread(
        *, target: Callable[[], None], name: str, daemon: bool
    ) -> _StartedThenRaisedThread:
        """Return the deterministic post-start failure double."""

        assert name == "elysia-managed-tts-cleanup"
        assert daemon is False
        return _StartedThenRaisedThread(target)

    monkeypatch.setattr(managed_module, "Thread", faulty_thread)

    resources.close_async(0.1)

    assert resources.close(0.0) is True
    assert events.count("process-terminate") == 1
    assert managed_module._GLOBAL_CLEANUP_PENDING == previous_pending
    assert managed_module._GLOBAL_CLEANUP_POISONED is previous_latch


def test_async_cleanup_terminates_job_before_waiting_on_transport() -> None:
    """Return promptly even when pipe close requires child termination first."""

    events: list[str] = []
    terminated = Event()

    class _TerminationOrderedTransport(_FakeTransport):
        """Refuse to close until the fake Job termination has started."""

        def close(self) -> None:
            """Expose the Windows pipe ordering dependency deterministically."""

            assert terminated.wait(0.5)
            super().close()

    class _OrderingProcess(_FakeProcess):
        """Publish termination before transport cleanup may continue."""

        def terminate(self, timeout_seconds: float = 5.0) -> bool:
            """Record termination and release the transport close gate."""

            result = super().terminate(timeout_seconds)
            terminated.set()
            return result

    transport = _TerminationOrderedTransport(events, Event())
    resources = managed_module._OwnedResources(
        transport,
        _OrderingProcess(events),
        _FakeGuard(events),
    )

    started = monotonic()
    resources.close_async(1.0)
    assert monotonic() - started < 0.2
    assert resources.close(1.0) is True
    assert events.index("process-terminate") < events.index("transport-close")
    assert events.index("transport-close") < events.index("guard-close")


def test_cleanup_joiner_obeys_its_timeout_while_owner_finishes() -> None:
    """Bound a second close caller without abandoning the cleanup owner."""

    events: list[str] = []
    allow_transport_close = Event()
    transport_close_started = Event()

    class _HeldTransport(_FakeTransport):
        """Hold post-termination pipe cleanup behind an explicit test gate."""

        def close(self) -> None:
            """Wait until the test has observed one bounded joiner timeout."""

            transport_close_started.set()
            assert allow_transport_close.wait(2.0)
            super().close()

    transport = _HeldTransport(events, Event())
    resources = managed_module._OwnedResources(
        transport,
        _FakeProcess(events),
        _FakeGuard(events),
    )
    resources.close_async(1.0)
    try:
        assert transport_close_started.wait(0.5)
        started = monotonic()
        assert resources.close(0.02) is False
        assert monotonic() - started < 0.2
    finally:
        allow_transport_close.set()
    assert resources.close(1.0) is True


def test_pending_cleanup_blocks_later_production_launch_before_outcome(
    tmp_path: Path,
) -> None:
    """Linearize production admission behind an unresolved cleanup attempt."""

    events: list[str] = []
    termination_started = Event()
    allow_termination = Event()

    class _BlockedAmbiguousProcess(_FakeProcess):
        """Hold one termination attempt before reporting an ambiguous result."""

        def terminate(self, timeout_seconds: float = 5.0) -> bool:
            """Publish cleanup ownership, then wait for the test to resolve it."""

            if not self.terminated.is_set():
                self._events.append("process-terminate")
                self.terminated.set()
            termination_started.set()
            assert allow_termination.wait(2.0)
            return self._termination_result

    process = _BlockedAmbiguousProcess(events, termination_result=False)
    resources = managed_module._OwnedResources(
        _FakeTransport(events, Event()),
        process,
        _FakeGuard(events),
    )
    previous_latch = managed_module._GLOBAL_CLEANUP_POISONED
    previous_pending = managed_module._GLOBAL_CLEANUP_PENDING
    before = len(managed_module._QUARANTINED_GUARDS)
    quarantined: object | None = None
    managed_module._GLOBAL_CLEANUP_POISONED = False
    try:
        resources.close_async(0.1)
        assert termination_started.wait(0.5)
        assert managed_module._GLOBAL_CLEANUP_PENDING == previous_pending + 1
        _assert_production_cleanup_latch(tmp_path)

        allow_termination.set()
        assert resources.close(1.0) is False
        assert managed_module._GLOBAL_CLEANUP_PENDING == previous_pending
        assert managed_module._GLOBAL_CLEANUP_POISONED is True
        quarantined = managed_module._QUARANTINED_GUARDS[-1]
        assert type(quarantined) is managed_module._ProcessTransportGuardOwner
    finally:
        allow_termination.set()
        del managed_module._QUARANTINED_GUARDS[before:]
        process._termination_result = True
        if type(quarantined) is managed_module._ProcessTransportGuardOwner:
            quarantined.close()
        managed_module._GLOBAL_CLEANUP_POISONED = previous_latch
        managed_module._GLOBAL_CLEANUP_PENDING = previous_pending


def test_final_launch_gate_rechecks_cleanup_after_early_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse CreateProcess when cleanup starts after the early gate check."""

    verification_started = Event()
    allow_verification = Event()
    termination_started = Event()
    allow_termination = Event()
    cleanup_events: list[str] = []

    class _BlockedSuccessfulProcess(_FakeProcess):
        """Keep one successful cleanup pending across the launch recheck."""

        def terminate(self, timeout_seconds: float = 5.0) -> bool:
            """Block until the competing acquisition reaches its final gate."""

            if not self.terminated.is_set():
                self._events.append("process-terminate")
                self.terminated.set()
            termination_started.set()
            assert allow_termination.wait(2.0)
            return True

    cleanup_resources = managed_module._OwnedResources(
        _FakeTransport(cleanup_events, Event()),
        _BlockedSuccessfulProcess(cleanup_events),
        _FakeGuard(cleanup_events),
    )
    harness = _Harness()

    def production_bootstrap(
        layout: managed_module._GuardedLayout,
    ) -> managed_module._BootstrapLaunch:
        """Use the fake bootstrap while retaining production-factory identity."""

        return harness.bootstrap(layout)

    def block_bootstrap_verification(
        _bootstrap: managed_module._BootstrapLaunch,
    ) -> None:
        """Pause after the early gate and before the final launch gate."""

        verification_started.set()
        assert allow_verification.wait(2.0)

    monkeypatch.setattr(
        managed_module, "_default_bootstrap_factory", production_bootstrap
    )
    monkeypatch.setattr(
        managed_module,
        "_verify_production_bootstrap",
        block_bootstrap_verification,
    )
    monkeypatch.setattr(
        managed_module,
        "_verify_launched_process_image",
        lambda _process, _bootstrap: None,
    )
    runtime = _runtime(
        tmp_path,
        harness,
        bootstrap_factory=production_bootstrap,
    )
    failures: list[BaseException] = []

    def acquire() -> None:
        """Run the production-identity acquisition through both gate checks."""

        try:
            runtime.acquire_lease(_selection(tmp_path))
        except BaseException as error:
            failures.append(error)

    previous_latch = managed_module._GLOBAL_CLEANUP_POISONED
    previous_pending = managed_module._GLOBAL_CLEANUP_PENDING
    managed_module._GLOBAL_CLEANUP_POISONED = False
    worker = Thread(target=acquire)
    try:
        worker.start()
        assert verification_started.wait(0.5)
        cleanup_resources.close_async(0.1)
        assert termination_started.wait(0.5)
        assert managed_module._GLOBAL_CLEANUP_PENDING == previous_pending + 1

        allow_verification.set()
        worker.join(1.0)
        assert not worker.is_alive()
        assert len(failures) == 1
        assert isinstance(failures[0], ManagedGptSovitsUnavailableError)
        assert harness.launch_calls == []
    finally:
        allow_verification.set()
        allow_termination.set()
        worker.join(1.0)
        assert cleanup_resources.close(1.0) is True
        managed_module._GLOBAL_CLEANUP_POISONED = previous_latch
        managed_module._GLOBAL_CLEANUP_PENDING = previous_pending


def test_live_production_owner_blocks_launch_until_failed_startup_cleans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hold the global owner token across a post-launch validation failure."""

    termination_started = Event()
    allow_termination = Event()
    first_harness = _Harness()
    second_harness = _Harness()

    class _BlockedCleanupProcess(_FakeProcess):
        """Pause successful termination while another runtime tries to launch."""

        def terminate(self, timeout_seconds: float = 5.0) -> bool:
            """Publish cleanup entry and wait without making its result ambiguous."""

            if not self.terminated.is_set():
                self._events.append("process-terminate")
                self.terminated.set()
            termination_started.set()
            assert allow_termination.wait(2.0)
            return True

    first_harness.process = _BlockedCleanupProcess(first_harness.events)
    first_root = _config(tmp_path / "first").runtime_root

    def production_bootstrap(
        layout: managed_module._GuardedLayout,
    ) -> managed_module._BootstrapLaunch:
        """Route the production-identity bootstrap to the matching harness."""

        harness = (
            first_harness
            if layout.candidate_runtime_root == first_root
            else second_harness
        )
        return harness.bootstrap(layout)

    def fail_first_process_image(
        process: object,
        _bootstrap: managed_module._BootstrapLaunch,
    ) -> None:
        """Fail after the first live process has entered its global owner slot."""

        if process is first_harness.process:
            raise ManagedGptSovitsStartupError("simulated image mismatch")

    monkeypatch.setattr(
        managed_module, "_default_bootstrap_factory", production_bootstrap
    )
    monkeypatch.setattr(
        managed_module,
        "_verify_production_bootstrap",
        lambda _bootstrap: None,
    )
    monkeypatch.setattr(
        managed_module,
        "_verify_launched_process_image",
        fail_first_process_image,
    )
    first_runtime = _runtime(
        tmp_path / "first",
        first_harness,
        bootstrap_factory=production_bootstrap,
    )
    second_runtime = _runtime(
        tmp_path / "second",
        second_harness,
        bootstrap_factory=production_bootstrap,
    )
    first_failures: list[BaseException] = []

    def acquire_first() -> None:
        """Own the live failed-startup cleanup in a separate thread."""

        try:
            first_runtime.acquire_lease(_selection(tmp_path / "first"))
        except BaseException as error:
            first_failures.append(error)

    previous_latch = managed_module._GLOBAL_CLEANUP_POISONED
    previous_pending = managed_module._GLOBAL_CLEANUP_PENDING
    managed_module._GLOBAL_CLEANUP_POISONED = False
    worker = Thread(target=acquire_first)
    try:
        worker.start()
        assert termination_started.wait(0.5)
        assert managed_module._GLOBAL_CLEANUP_PENDING == previous_pending + 1

        with pytest.raises(ManagedGptSovitsUnavailableError):
            second_runtime.acquire_lease(_selection(tmp_path / "second"))
        assert second_harness.launch_calls == []

        allow_termination.set()
        worker.join(1.0)
        assert not worker.is_alive()
        assert len(first_failures) == 1
        assert isinstance(first_failures[0], ManagedGptSovitsStartupError)
        assert managed_module._GLOBAL_CLEANUP_PENDING == previous_pending
    finally:
        allow_termination.set()
        worker.join(1.0)
        managed_module._GLOBAL_CLEANUP_POISONED = previous_latch
        managed_module._GLOBAL_CLEANUP_PENDING = previous_pending


@pytest.mark.skipif(os.name != "nt", reason="Windows pipe close semantics")
def test_async_cleanup_wakes_a_real_blocked_windows_pipe_read() -> None:
    """Kill the child writer before closing a FileIO used by another thread."""

    events: list[str] = []
    read_descriptor, write_descriptor = os.pipe()
    response_stream = os.fdopen(read_descriptor, "rb", buffering=0)
    read_started = Event()
    read_values: list[bytes] = []

    class _RealPipeTransport:
        """Own one actual response pipe plus inert protocol fields."""

        def __init__(self) -> None:
            """Expose the minimal transport contract around the real pipe."""

            self.request_stream: BinaryIO = _PersistentBytesIO()
            self.response_stream: BinaryIO = response_stream
            self.stdin_handle = 201
            self.stdout_handle = 202
            self.stderr_handle = 203

        def close_child_ends(self) -> None:
            """Leave the simulated child writer with its process owner."""

        def close(self) -> None:
            """Close parent streams only after the writer reaches EOF."""

            events.append("transport-close")
            self.request_stream.close()
            self.response_stream.close()

    class _PipeWriterProcess(_FakeProcess):
        """Model Job death by closing the last child-side pipe writer."""

        def __init__(self) -> None:
            """Adopt the writer descriptor exactly once."""

            super().__init__(events)
            self._write_descriptor: int | None = write_descriptor

        def terminate(self, timeout_seconds: float = 5.0) -> bool:
            """Close the child writer so the blocked read observes EOF."""

            result = super().terminate(timeout_seconds)
            descriptor, self._write_descriptor = self._write_descriptor, None
            if descriptor is not None:
                os.close(descriptor)
            return result

    def read_one_byte() -> None:
        """Block in the same unbuffered FileIO operation used by production."""

        read_started.set()
        read_values.append(response_stream.read(1))

    process = _PipeWriterProcess()
    resources = managed_module._OwnedResources(
        _RealPipeTransport(),  # type: ignore[arg-type]
        process,
        _FakeGuard(events),
    )
    reader = Thread(target=read_one_byte)
    reader.start()
    assert read_started.wait(0.5)
    Event().wait(0.02)

    cleanup_finished = Event()

    def release_regression_deadlock() -> None:
        """Keep a broken future cleanup order from hanging the test process."""

        if not cleanup_finished.wait(1.0):
            process.terminate()

    watchdog = Thread(target=release_regression_deadlock, daemon=True)
    watchdog.start()
    try:
        started = monotonic()
        resources.close_async(1.0)
        elapsed = monotonic() - started
        result = resources.close(1.0)
    finally:
        cleanup_finished.set()
        process.terminate()
        reader.join(1.0)
        watchdog.join(1.0)

    assert elapsed < 0.2
    assert result is True
    assert not reader.is_alive()
    assert read_values == [b""]
    assert process.terminated.is_set()
    assert events.index("process-terminate") < events.index("transport-close")


def test_concurrent_idle_close_has_one_owner_and_joiners_do_not_send_stop(
    tmp_path: Path,
) -> None:
    """Serialize graceful close and let concurrent callers join its result."""

    protocol = _ScriptedProtocol(block_stop=True)
    harness = _Harness(protocol)
    runtime = _runtime(tmp_path, harness)
    lease = runtime.acquire_lease(_selection(tmp_path))
    first_done = Event()
    second_done = Event()

    def close_first() -> None:
        """Own the intentionally blocked graceful close transaction."""

        lease.close()
        first_done.set()

    def close_second() -> None:
        """Join the existing close transaction without writing another STOP."""

        lease.close()
        second_done.set()

    first = Thread(target=close_first)
    first.start()
    assert protocol.stop_started.wait(0.5)
    second = Thread(target=close_second)
    second.start()
    assert second_done.wait(0.02) is False
    assert sum(
        frame.kind is FrameKind.STOP for frame in protocol.writes
    ) == 1

    protocol.allow_stop.set()
    first.join(1.0)
    second.join(1.0)
    assert first_done.is_set()
    assert second_done.is_set()
    assert sum(
        frame.kind is FrameKind.STOP for frame in protocol.writes
    ) == 1
    assert runtime.get_status().state == "idle"
    assert harness.events.count("process-terminate") == 1


def test_stopped_frame_with_ambiguous_termination_poisoned_not_idle(
    tmp_path: Path,
) -> None:
    """Require STOPPED, proven Job death, and guard cleanup before reuse."""

    harness = _Harness()
    harness.process = _FakeProcess(
        harness.events, termination_result=False
    )
    runtime = _runtime(tmp_path, harness)
    lease = runtime.acquire_lease(_selection(tmp_path))
    before = len(managed_module._QUARANTINED_GUARDS)

    lease.close()
    assert lease.poisoned is True
    assert runtime.get_status().state == "poisoned"
    assert sum(
        frame.kind is FrameKind.STOP for frame in harness.protocol.writes
    ) == 1
    assert len(managed_module._QUARANTINED_GUARDS) == before + 1

    quarantined = managed_module._QUARANTINED_GUARDS.pop()
    assert type(quarantined) is managed_module._ProcessTransportGuardOwner
    harness.process._termination_result = True
    quarantined.close()


def test_mapping_cleanup_failure_after_stopped_frame_poisoned_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Treat an exact-match DOS-device removal failure as terminal poison."""

    target = r"\Device\HarddiskVolume9"
    mapping = managed_module._DosDeviceMapping(
        "S:", target, str(_VOLUME_ROOT)
    )
    harness = _Harness()
    harness.bootstrap_guard = mapping  # type: ignore[assignment]
    runtime = _runtime(tmp_path, harness)
    previous_latch = managed_module._GLOBAL_CLEANUP_POISONED
    before = len(managed_module._QUARANTINED_GUARDS)
    monkeypatch.setattr(
        managed_module,
        "_query_dos_device",
        lambda name: (target,) if name == "S:" else (),
    )
    monkeypatch.setattr(
        managed_module, "_define_dos_device", lambda *_args: False
    )

    lease = runtime.acquire_lease(_selection(tmp_path))
    lease.close()
    assert lease.poisoned is True
    assert runtime.get_status().state == "poisoned"
    assert managed_module._GLOBAL_CLEANUP_POISONED is True
    assert len(managed_module._QUARANTINED_GUARDS) > before

    del managed_module._QUARANTINED_GUARDS[before:]
    managed_module._GLOBAL_CLEANUP_POISONED = previous_latch


def test_ambiguous_job_termination_quarantines_guard_instead_of_releasing_it(
    tmp_path: Path,
) -> None:
    """Retain immutable-file ownership unless process termination is proven."""

    harness = _Harness()
    harness.process = _FakeProcess(
        harness.events, termination_result=False
    )
    runtime = _runtime(tmp_path, harness)
    runtime.acquire_lease(_selection(tmp_path))
    before = len(managed_module._QUARANTINED_GUARDS)

    runtime.shutdown()
    _wait_for(harness.process.terminated)
    deadline = monotonic() + 1.0
    while (
        len(managed_module._QUARANTINED_GUARDS) == before
        and monotonic() < deadline
    ):
        Event().wait(0.005)
    assert harness.guard.closed is False
    assert harness.bootstrap_guard.closed is False
    quarantined = managed_module._QUARANTINED_GUARDS[-1]
    assert type(quarantined) is managed_module._ProcessTransportGuardOwner
    assert getattr(quarantined, "_process") is harness.process
    assert type(getattr(quarantined, "_guard")) is managed_module._CombinedGuard
    assert getattr(quarantined, "_transport") is harness.transport
    assert "guard-close" not in harness.events

    # The production quarantine intentionally lasts for the parent process.
    # Remove this fake after proving ownership so the test itself stays clean.
    managed_module._QUARANTINED_GUARDS.pop()
    harness.process._termination_result = True
    quarantined.close()
