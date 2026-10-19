"""Test the private ComfyUI process supervisor without starting native work.

The fixtures keep filesystem hashing and per-run directory removal real while
injecting deterministic process, guard, port-owner, transport, clock, and raw
HTTP doubles.  This combination exercises the supervisor's trust boundary and
cleanup state machine without requiring ComfyUI, a checkpoint, or a GPU.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path
from threading import Event, Thread
from typing import Any, cast
from uuid import uuid4

import pytest

import image_generation.managed_comfyui as managed
from image_generation._private_run_directory import (
    PrivateRunDirectory,
    create_private_run_directory,
    remove_private_run_directory,
)
from image_generation.comfyui import (
    ComfyOutputDescriptor,
    ComfyPromptHandle,
    ComfyUiConfig,
)
from image_generation.domain import ImageGenerationSpec, ImageLoraSelection
from image_generation.exceptions import (
    ImageGenerationProtocolError,
    ImageGenerationUnavailableError,
)
from image_generation.png import CanonicalPng
from image_generation.profiles import (
    ImageSizePreset,
    ModelArchitecture,
    ModelLoraProfile,
    ModelProfile,
    PromptDialect,
)
from image_generation.workflow import CompiledWorkflow, compile_txt2img_workflow


def _sha256(value: bytes) -> str:
    """Return the lowercase digest used by synthetic trusted files."""

    return hashlib.sha256(value).hexdigest()


class _FakeClock:
    """Advance deterministically whenever the supervisor reads monotonic time."""

    def __init__(self, *, step: float = 0.001) -> None:
        """Start at zero with a caller-selected increment."""

        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        """Return the current value and advance for the next deadline check."""

        current = self.value
        self.value += self.step
        return current


class _FakeProcess:
    """Model one Job-owned child process and its process-tree teardown proof."""

    def __init__(
        self,
        *,
        pid: int = 42_424,
        alive: bool = True,
        terminate_succeeds: bool = True,
        terminate_raises: bool = False,
    ) -> None:
        """Configure liveness and whether termination can be proven."""

        self._pid = pid
        self.alive = alive
        self._closed = False
        self.terminate_succeeds = terminate_succeeds
        self.terminate_raises = terminate_raises
        self.terminate_calls: list[float] = []
        self.is_alive_calls = 0

    @property
    def pid(self) -> int:
        """Return the deterministic synthetic child identifier."""

        return self._pid

    @property
    def closed(self) -> bool:
        """Return whether successful teardown closed the synthetic Job."""

        return self._closed

    def is_alive(self) -> bool:
        """Return the configured liveness state and record the observation."""

        self.is_alive_calls += 1
        return self.alive

    def terminate(self, timeout_seconds: float = 5.0) -> bool:
        """Stop and close the fake process when teardown is configured to work."""

        self.terminate_calls.append(timeout_seconds)
        if self.terminate_raises:
            raise OSError("synthetic process termination failure")
        if self.terminate_succeeds:
            self.alive = False
            self._closed = True
            return True
        return False


class _FakeGuard:
    """Represent immutable native file handles held for one child lifetime."""

    def __init__(self, *, close_failures: int = 0) -> None:
        """Allow a bounded number of close failures for retry tests."""

        self._closed = False
        self.close_failures = close_failures
        self.close_calls = 0

    @property
    def closed(self) -> bool:
        """Return whether every synthetic guarded handle was released."""

        return self._closed

    def close(self) -> None:
        """Release the guard or raise the configured transient failure."""

        self.close_calls += 1
        if self.close_failures:
            self.close_failures -= 1
            raise OSError("synthetic guard close failure")
        self._closed = True


class _FakeTransport:
    """Own the three launch handles until the launcher has duplicated them."""

    stdin_handle = 101
    stdout_handle = 102
    stderr_handle = 103

    def __init__(self, *, close_failures: int = 0) -> None:
        """Configure bounded close failures for startup rollback coverage."""

        self.close_failures = close_failures
        self.close_calls = 0
        self.closed = False

    def close(self) -> None:
        """Release the fake standard handles exactly as the real owner would."""

        self.close_calls += 1
        if self.close_failures:
            self.close_failures -= 1
            raise OSError("synthetic transport close failure")
        self.closed = True


class _FakeRawClient:
    """Implement the bounded ComfyUI adapter contract with injectable failures."""

    def __init__(self) -> None:
        """Initialize a successful immediate-output script."""

        self.calls: list[str] = []
        self.probe_failures = 0
        self.ready_failures = 0
        self.always_unavailable = False
        self.prepare_result: object | None = None
        self.wait_result: object | None = None
        self.cancel_error: BaseException | None = None

    def probe_ready(self) -> None:
        """Prove API route initialization or inject a retryable outage."""

        self.calls.append("probe")
        if self.always_unavailable or self.probe_failures:
            if self.probe_failures:
                self.probe_failures -= 1
            raise ImageGenerationUnavailableError("synthetic probe outage")

    def ensure_ready(self, profile: ModelProfile, *, lora: ModelLoraProfile | None = None) -> None:
        """Validate the selected synthetic checkpoint or inject an outage."""

        self.calls.append(f"ready:{profile.profile_id}")
        if lora is not None:
            assert profile.lora(lora.lora_id) is lora
            self.calls.append(f"lora:{lora.lora_id}")
        if self.always_unavailable or self.ready_failures:
            if self.ready_failures:
                self.ready_failures -= 1
            raise ImageGenerationUnavailableError("synthetic model outage")

    def prepare(self, workflow: CompiledWorkflow) -> ComfyPromptHandle:
        """Mint a handle whose output prefix matches the compiled workflow."""

        self.calls.append("prepare")
        if self.prepare_result is not None:
            return cast(ComfyPromptHandle, self.prepare_result)
        return ComfyPromptHandle(
            prompt_id=str(uuid4()),
            client_id=str(uuid4()),
            output_filename_prefix=workflow.output_filename_prefix,
        )

    def submit_prepared(
        self,
        handle: ComfyPromptHandle,
        workflow: CompiledWorkflow,
    ) -> None:
        """Record submission of the exact prepared workflow."""

        assert handle.output_filename_prefix == workflow.output_filename_prefix
        self.calls.append("submit")

    def wait_for_output(
        self,
        handle: ComfyPromptHandle,
        expected_output_node_id: str,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ComfyOutputDescriptor:
        """Return the one synthetic output descriptor after submission."""

        assert handle.prompt_id
        assert expected_output_node_id == "7"
        assert cancel_requested is None or not cancel_requested()
        self.calls.append("wait")
        if self.wait_result is not None:
            return cast(ComfyOutputDescriptor, self.wait_result)
        return ComfyOutputDescriptor("result.png", "", "output")

    def fetch_png(
        self,
        descriptor: ComfyOutputDescriptor,
        *,
        expected_width: int,
        expected_height: int,
    ) -> CanonicalPng:
        """Return path-free synthetic image metadata for delegation checks."""

        assert descriptor.filename == "result.png"
        self.calls.append("fetch")
        return CanonicalPng(
            data=b"synthetic canonical png",
            width=expected_width,
            height=expected_height,
            sha256="1" * 64,
        )

    def cancel(self, handle: ComfyPromptHandle) -> bool:
        """Record best-effort cancellation and optionally fail it."""

        assert handle.prompt_id
        self.calls.append("cancel")
        if self.cancel_error is not None:
            raise self.cancel_error
        return True


class _Harness:
    """Expose deterministic dependency seams and their lifecycle observations."""

    def __init__(self, root: Path) -> None:
        """Create default successful doubles below one test-owned directory."""

        self.root = root
        self.clock = _FakeClock()
        self.raw = _FakeRawClient()
        self.raw_clients: list[_FakeRawClient] = []
        self.processes: list[_FakeProcess] = []
        self.guards: list[_FakeGuard] = []
        self.transports: list[_FakeTransport] = []
        self.runs: list[PrivateRunDirectory] = []
        self.removed_runs: list[PrivateRunDirectory] = []
        self.launches: list[tuple[list[str], dict[str, object]]] = []
        self.guard_declarations: list[tuple[object, ...]] = []
        self.raw_configs: list[ComfyUiConfig] = []
        self.sleep_calls: list[float] = []
        self.platform_name = "nt"
        self.port = 43_123
        self.launch_alive = True
        self.launch_error: BaseException | None = None
        self.guard_error: BaseException | None = None
        self.transport_error: BaseException | None = None
        self.run_error: BaseException | None = None
        self.raw_factory_error: BaseException | None = None
        self.owner_available = True
        self.retain_owner_after_close = False
        self.terminate_succeeds = True
        self.terminate_raises = False
        self.guard_close_failures = 0
        self.transport_close_failures = 0
        self.remove_failures = 0
        self.create_extra_model_config_after_guard = False
        self.volume_checks: list[Path] = []

    def run_factory(self, trusted_root: Path) -> PrivateRunDirectory:
        """Create and record a genuine private run layout."""

        if self.run_error is not None:
            raise self.run_error
        run = create_private_run_directory(trusted_root)
        self.runs.append(run)
        return run

    def run_remover(self, run: PrivateRunDirectory) -> None:
        """Remove an exact real run tree or inject a bounded cleanup failure."""

        self.removed_runs.append(run)
        if self.remove_failures:
            self.remove_failures -= 1
            raise OSError("synthetic private-directory removal failure")
        remove_private_run_directory(run)

    def acquire_guard(self, declarations: tuple[object, ...]) -> _FakeGuard:
        """Record verified declarations before returning one fake guard."""

        if self.guard_error is not None:
            raise self.guard_error
        self.guard_declarations.append(declarations)
        guard = _FakeGuard(close_failures=self.guard_close_failures)
        self.guards.append(guard)
        if self.create_extra_model_config_after_guard:
            (self.root / "comfyui" / "extra_model_paths.yaml").write_text(
                "other: {}",
                encoding="utf-8",
            )
        return guard

    def guarded_paths(self, guard: _FakeGuard) -> tuple[str, ...]:
        """Return declaration paths as deterministic stand-ins for GUID paths."""

        assert guard in self.guards
        declarations = self.guard_declarations[-1]
        return tuple(str(cast(Any, declaration).path) for declaration in declarations)

    def verify_volume(self, path: Path) -> None:
        """Record each fixed-local-volume check without platform dependence."""

        self.volume_checks.append(path)

    def allocate_port(self) -> int:
        """Return the configured deterministic private listener port."""

        return self.port

    def create_transport(self) -> _FakeTransport:
        """Return launch handles or inject construction failure."""

        if self.transport_error is not None:
            raise self.transport_error
        transport = _FakeTransport(close_failures=self.transport_close_failures)
        self.transports.append(transport)
        return transport

    def child_environment(self, run: PrivateRunDirectory) -> dict[str, str]:
        """Return a closed synthetic environment tied to private directories."""

        return {
            "PATH": r"C:\Windows\System32",
            "PYTHONNOUSERSITE": "1",
            "TEMP": str(run.temp),
            "TMP": str(run.temp),
            "USERPROFILE": str(run.user),
        }

    def launch(self, argv: list[str], **kwargs: object) -> _FakeProcess:
        """Record the child contract and return one configurable process."""

        self.launches.append((list(argv), dict(kwargs)))
        if self.launch_error is not None:
            raise self.launch_error
        process = _FakeProcess(
            alive=self.launch_alive,
            terminate_succeeds=self.terminate_succeeds,
            terminate_raises=self.terminate_raises,
        )
        self.processes.append(process)
        return process

    def owns_listener(self, port: int, process_id: int) -> bool:
        """Bind listener ownership to the exact live fake process and port."""

        assert port == self.port
        matching = [process for process in self.processes if process.pid == process_id]
        if not matching or not self.owner_available:
            return False
        process = matching[-1]
        return process.alive or self.retain_owner_after_close

    def create_raw(self, config: ComfyUiConfig) -> _FakeRawClient:
        """Record a private adapter configuration and return the scripted client."""

        self.raw_configs.append(config)
        if self.raw_factory_error is not None:
            raise self.raw_factory_error
        self.raw_clients.append(self.raw)
        return self.raw

    def sleep(self, seconds: float) -> None:
        """Record bounded startup polling without delaying the test suite."""

        self.sleep_calls.append(seconds)

    def dependencies(self) -> managed._Dependencies:
        """Build the module-sealed dependency value accepted by test construction."""

        return managed._Dependencies(
            launcher=self.launch,
            guard_acquirer=cast(Any, self.acquire_guard),
            guarded_paths_getter=cast(Any, self.guarded_paths),
            raw_factory=self.create_raw,
            run_factory=self.run_factory,
            run_remover=self.run_remover,
            port_allocator=self.allocate_port,
            owner_verifier=self.owns_listener,
            transport_factory=self.create_transport,
            environment_factory=self.child_environment,
            path_volume_verifier=self.verify_volume,
            monotonic_clock=self.clock,
            sleeper=self.sleep,
            platform_name=self.platform_name,
        )


@dataclass(frozen=True, slots=True)
class _RuntimeFixture:
    """Bundle one trusted installation with its runtime and compiled graph."""

    runtime: managed.ManagedComfyUiRuntime
    harness: _Harness
    config: managed.ManagedComfyUiConfig
    profile: ModelProfile
    workflow: CompiledWorkflow


def _runtime_fixture(tmp_path: Path) -> _RuntimeFixture:
    """Create a hash-consistent synthetic installation and injected runtime."""

    runtime_root = tmp_path / "comfyui"
    models_root = tmp_path / "models"
    checkpoint_root = models_root / "checkpoints" / "fixtures"
    private_state_root = tmp_path / "private"
    runtime_root.mkdir(parents=True)
    checkpoint_root.mkdir(parents=True)
    private_state_root.mkdir()
    python_executable = tmp_path / "python.exe"
    main_script = runtime_root / "main.py"
    checkpoint = checkpoint_root / "anime-model.safetensors"
    python_bytes = b"synthetic isolated python"
    main_bytes = b"# synthetic comfyui main\n"
    checkpoint_bytes = b"synthetic checkpoint"
    python_executable.write_bytes(python_bytes)
    main_script.write_bytes(main_bytes)
    checkpoint.write_bytes(checkpoint_bytes)

    profile = ModelProfile(
        profile_id="anime-local",
        display_name="Synthetic managed runtime profile",
        architecture=ModelArchitecture.SDXL,
        prompt_dialect=PromptDialect.SDXL_NATURAL,
        checkpoint_name="fixtures/anime-model.safetensors",
        checkpoint_sha256=_sha256(checkpoint_bytes),
        checkpoint_source="Synthetic unit-test fixture.",
        checkpoint_license="Synthetic test declaration.",
        size_presets=(ImageSizePreset("square", 512, 512),),
        default_size_preset_id="square",
    )
    config = managed.ManagedComfyUiConfig(
        runtime_root=runtime_root,
        python_executable=python_executable,
        models_root=models_root,
        private_state_root=private_state_root,
        python_sha256=_sha256(python_bytes),
        comfyui_main_sha256=_sha256(main_bytes),
        startup_timeout_seconds=0.1,
        request_timeout_seconds=0.1,
        job_timeout_seconds=1.0,
        poll_interval_seconds=0.01,
        stop_timeout_seconds=0.1,
    )
    harness = _Harness(tmp_path)
    runtime = managed.ManagedComfyUiRuntime._create_for_testing(
        config,
        harness.dependencies(),
        dependency_seal=managed._TEST_DEPENDENCY_SEAL,
    )
    workflow = compile_txt2img_workflow(
        ImageGenerationSpec(
            profile_id=profile.profile_id,
            size_preset_id="square",
            appearance="adult character with pink hair",
            seed=7,
            original_fictional_confirmed=True,
        ),
        profile,
        job_id=str(uuid4()),
    )
    return _RuntimeFixture(runtime, harness, config, profile, workflow)


def _prepare(fixture: _RuntimeFixture) -> ComfyPromptHandle:
    """Run verified preflight and private startup for one fixture."""

    fixture.runtime.ensure_ready(fixture.profile)
    return fixture.runtime.prepare(fixture.workflow)


def _lora_fixture(tmp_path: Path) -> _RuntimeFixture:
    """Extend the real-file runtime fixture with one optional pinned LoRA."""

    fixture = _runtime_fixture(tmp_path)
    asset = fixture.config.models_root / "loras" / "fixtures" / "reviewed.safetensors"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"synthetic reviewed LoRA")
    lora = ModelLoraProfile(
        lora_id="reviewed", display_name="Reviewed fixture", architecture=ModelArchitecture.SDXL,
        lora_name="fixtures/reviewed.safetensors", lora_sha256=_sha256(asset.read_bytes()),
        lora_source="Synthetic unit-test fixture.", lora_license="Synthetic declaration.",
    )
    profile = replace(fixture.profile, loras=(lora,))
    workflow = compile_txt2img_workflow(
        ImageGenerationSpec(
            profile_id=profile.profile_id, size_preset_id="square", appearance="adult fictional portrait",
            original_fictional_confirmed=True, lora=ImageLoraSelection(lora.lora_id, 0.7, 0.8),
        ), profile, job_id=str(uuid4()),
    )
    return replace(fixture, profile=profile, workflow=workflow)


def test_selected_lora_adds_fourth_native_guard_and_retains_it_through_finalize(tmp_path: Path) -> None:
    """Pin selected LoRA bytes with the process and release only after termination."""

    fixture = _lora_fixture(tmp_path)
    lora = fixture.profile.loras[0]
    fixture.runtime.ensure_ready(fixture.profile, lora=lora)
    assert fixture.harness.guards == []
    assert fixture.harness.processes == []
    handle = fixture.runtime.prepare(fixture.workflow)

    declarations = fixture.harness.guard_declarations[0]
    assert len(declarations) == 4
    assert cast(Any, declarations[3]).path == fixture.config.models_root / "loras" / lora.lora_name
    assert cast(Any, declarations[3]).sha256 == lora.lora_sha256
    assert not fixture.harness.guards[0].closed
    assert f"lora:{lora.lora_id}" in fixture.harness.raw.calls
    fixture.runtime.submit_prepared(handle, fixture.workflow)
    assert not fixture.harness.guards[0].closed
    fixture.runtime.finalize(handle)
    assert fixture.harness.processes[0].closed
    assert fixture.harness.guards[0].closed
    assert not fixture.harness.runs[0].root.exists()


@pytest.mark.parametrize("mutation", ["missing", "changed", "alias-profile"])
def test_bad_selected_lora_does_not_disable_checkpoint_only_generation(tmp_path: Path, mutation: str) -> None:
    """Reject only the selected optional asset and leave a subsequent off run ready."""

    fixture = _lora_fixture(tmp_path)
    lora = fixture.profile.loras[0]
    asset = fixture.config.models_root / "loras" / lora.lora_name
    if mutation == "missing":
        asset.unlink()
    elif mutation == "changed":
        asset.write_bytes(b"changed LoRA")
    else:
        lora = replace(lora)
    with pytest.raises(ImageGenerationUnavailableError):
        fixture.runtime.ensure_ready(fixture.profile, lora=lora)
    assert fixture.harness.processes == []

    fixture.runtime.ensure_ready(fixture.profile)
    base = compile_txt2img_workflow(
        ImageGenerationSpec(
            profile_id=fixture.profile.profile_id, size_preset_id="square", appearance="adult fictional portrait",
            original_fictional_confirmed=True,
        ), fixture.profile, job_id=str(uuid4()),
    )
    handle = fixture.runtime.prepare(base)
    assert len(fixture.harness.guard_declarations[-1]) == 3
    assert not any(call.startswith("lora:") for call in fixture.harness.raw.calls)
    fixture.runtime.finalize(handle)


def test_lora_graph_requires_matching_selected_preflight(tmp_path: Path) -> None:
    """Reject a selected sealed graph when only the base files were checked."""

    fixture = _lora_fixture(tmp_path)
    fixture.runtime.ensure_ready(fixture.profile)
    with pytest.raises(ImageGenerationProtocolError, match="profile"):
        fixture.runtime.prepare(fixture.workflow)
    assert fixture.harness.runs == []
    assert fixture.harness.guards == []


def test_selected_lora_guard_must_resolve_beneath_same_model_volume_root(tmp_path: Path) -> None:
    """Reject a valid-looking LoRA guard that points outside the checkpoint root."""

    fixture = _lora_fixture(tmp_path)
    original = fixture.harness.guarded_paths

    def foreign_lora_paths(guard: _FakeGuard) -> tuple[str, ...]:
        """Return the selected fourth file on an unrelated model root."""

        values = original(guard)
        return (*values[:3], str(tmp_path / "foreign" / "loras" / "fixtures" / "reviewed.safetensors"))

    fixture.harness.guarded_paths = foreign_lora_paths  # type: ignore[method-assign]
    runtime = managed.ManagedComfyUiRuntime._create_for_testing(
        fixture.config, fixture.harness.dependencies(), dependency_seal=managed._TEST_DEPENDENCY_SEAL,
    )
    runtime.ensure_ready(fixture.profile, lora=fixture.profile.loras[0])
    with pytest.raises(ImageGenerationUnavailableError):
        runtime.prepare(fixture.workflow)
    assert fixture.harness.processes == []
    assert fixture.harness.guards[0].closed
    assert not fixture.harness.runs[0].root.exists()


def test_preflight_hashes_trusted_files_without_allocating_resources(
    tmp_path: Path,
) -> None:
    """Keep the cancellation boundary free of directories and native handles."""

    fixture = _runtime_fixture(tmp_path)

    fixture.runtime.ensure_ready(fixture.profile)

    assert fixture.harness.runs == []
    assert fixture.harness.guards == []
    assert fixture.harness.transports == []
    assert fixture.harness.processes == []
    assert fixture.harness.raw_configs == []


def test_preflight_rejects_windows_venv_redirector_without_starting_child(
    tmp_path: Path,
) -> None:
    """Explain unsupported redirectors before readiness or any process allocation."""

    from dataclasses import replace

    fixture = _runtime_fixture(tmp_path)
    scripts = tmp_path / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    executable = scripts / "python.exe"
    executable.write_bytes(fixture.config.python_executable.read_bytes())
    (scripts.parent / "pyvenv.cfg").write_text("home = test-only\n", encoding="utf-8")
    runtime = managed.ManagedComfyUiRuntime._create_for_testing(
        replace(fixture.config, python_executable=executable),
        fixture.harness.dependencies(),
        dependency_seal=managed._TEST_DEPENDENCY_SEAL,
    )
    with pytest.raises(ImageGenerationUnavailableError, match="virtual-environment redirectors"):
        runtime.ensure_ready(fixture.profile)
    assert fixture.harness.processes == []
    assert fixture.harness.guards == []
    assert fixture.harness.runs == []


def test_failed_revalidation_clears_an_earlier_verified_profile(
    tmp_path: Path,
) -> None:
    """Prevent prepare from reusing stale facts after a later hash failure."""

    fixture = _runtime_fixture(tmp_path)
    fixture.runtime.ensure_ready(fixture.profile)
    checkpoint = (
        fixture.config.models_root
        / "checkpoints"
        / fixture.profile.checkpoint_name
    )
    checkpoint.write_bytes(b"changed after first preflight")

    with pytest.raises(ImageGenerationUnavailableError, match="unavailable"):
        fixture.runtime.ensure_ready(fixture.profile)
    with pytest.raises(ImageGenerationUnavailableError, match="unavailable"):
        fixture.runtime.prepare(fixture.workflow)

    assert fixture.harness.runs == []


def test_prepare_launches_one_closed_private_comfyui_instance(tmp_path: Path) -> None:
    """Pin loopback, private directories, isolation flags, and a closed environment."""

    fixture = _runtime_fixture(tmp_path)

    handle = _prepare(fixture)

    assert handle.output_filename_prefix == fixture.workflow.output_filename_prefix
    assert len(fixture.harness.launches) == 1
    command, launch_options = fixture.harness.launches[0]
    assert command[:8] == [
        str(fixture.config.python_executable),
        "-E",
        "-s",
        "-B",
        "-u",
        "-X",
        "utf8",
        str(fixture.config.main_script),
    ]
    assert "-I" not in command
    run = fixture.harness.runs[0]
    expected_pairs = {
        "--listen": "127.0.0.1",
        "--port": str(fixture.harness.port),
        "--base-directory": str(run.root),
        "--models-directory": str(fixture.config.models_root),
        "--output-directory": str(run.output),
        "--input-directory": str(run.input),
        "--temp-directory": str(run.temp),
        "--user-directory": str(run.user),
        "--front-end-root": str(run.frontend),
        "--preview-method": "none",
    }
    for flag, expected_value in expected_pairs.items():
        index = command.index(flag)
        assert command[index + 1] == expected_value
    for flag in (
        "--disable-auto-launch",
        "--disable-metadata",
        "--disable-all-custom-nodes",
        "--offline",
        "--disable-assets",
        "--disable-partner-nodes",
    ):
        assert flag in command
    assert launch_options["cwd"] == str(run.root)
    assert launch_options["stdin_handle"] == 101
    assert launch_options["stdout_handle"] == 102
    assert launch_options["stderr_handle"] == 103
    environment = cast(dict[str, str], launch_options["environment"])
    assert environment == fixture.harness.child_environment(run)
    assert all("adult character" not in value for value in command)
    assert fixture.harness.transports[0].closed
    raw_config = fixture.harness.raw_configs[0]
    assert raw_config.base_url == "http://127.0.0.1:43123"
    assert raw_config.exclusive_instance is True


def test_successful_job_delegates_all_phases_then_erases_private_state(
    tmp_path: Path,
) -> None:
    """Publishable output requires process, listener, guard, and root cleanup."""

    fixture = _runtime_fixture(tmp_path)
    handle = _prepare(fixture)
    run = fixture.harness.runs[0]

    fixture.runtime.submit_prepared(handle, fixture.workflow)
    descriptor = fixture.runtime.wait_for_output(
        handle,
        fixture.workflow.expected_output_node_id,
    )
    image = fixture.runtime.fetch_png(
        descriptor,
        expected_width=512,
        expected_height=512,
    )
    fixture.runtime.finalize(handle)
    fixture.runtime.finalize(handle)

    assert image.width == 512
    assert fixture.harness.raw.calls == [
        "probe",
        "ready:anime-local",
        "prepare",
        "submit",
        "wait",
        "fetch",
        "cancel",
    ]
    assert fixture.harness.processes[0].closed
    assert fixture.harness.guards[0].closed
    assert not run.root.exists()
    assert fixture.harness.removed_runs == [run]
    assert fixture.harness.processes[0].terminate_calls == [0.1]


def test_prepare_requires_a_matching_verified_profile(tmp_path: Path) -> None:
    """Reject another sealed workflow before allocating private resources."""

    fixture = _runtime_fixture(tmp_path)
    other_profile = ModelProfile(
        profile_id="other-local",
        display_name=fixture.profile.display_name,
        architecture=fixture.profile.architecture,
        prompt_dialect=fixture.profile.prompt_dialect,
        checkpoint_name=fixture.profile.checkpoint_name,
        checkpoint_sha256=fixture.profile.checkpoint_sha256,
        checkpoint_source=fixture.profile.checkpoint_source,
        checkpoint_license=fixture.profile.checkpoint_license,
        size_presets=fixture.profile.size_presets,
        default_size_preset_id=fixture.profile.default_size_preset_id,
    )
    other_workflow = compile_txt2img_workflow(
        ImageGenerationSpec(
            profile_id="other-local",
            size_preset_id="square",
            appearance="original adult character",
            seed=9,
            original_fictional_confirmed=True,
        ),
        other_profile,
        job_id=str(uuid4()),
    )
    fixture.runtime.ensure_ready(fixture.profile)

    with pytest.raises(ImageGenerationProtocolError, match="verified model profile"):
        fixture.runtime.prepare(other_workflow)

    assert fixture.harness.runs == []
    assert fixture.harness.processes == []


@pytest.mark.parametrize(
    "failure_boundary",
    ["run", "guard", "transport", "launch"],
)
def test_startup_allocation_failures_rollback_every_acquired_resource(
    tmp_path: Path,
    failure_boundary: str,
) -> None:
    """Converge partial directory, guard, transport, and launch failures on cleanup."""

    fixture = _runtime_fixture(tmp_path)
    setattr(
        fixture.harness,
        f"{failure_boundary}_error",
        OSError(f"synthetic {failure_boundary} failure"),
    )
    fixture.runtime.ensure_ready(fixture.profile)

    with pytest.raises(ImageGenerationUnavailableError, match="unavailable"):
        fixture.runtime.prepare(fixture.workflow)

    assert all(not run.root.exists() for run in fixture.harness.runs)
    assert all(guard.closed for guard in fixture.harness.guards)
    assert all(transport.closed for transport in fixture.harness.transports)
    assert all(process.closed for process in fixture.harness.processes)


def test_child_exit_during_startup_is_rolled_back(tmp_path: Path) -> None:
    """Treat an early child exit as unavailable and erase its private root."""

    fixture = _runtime_fixture(tmp_path)
    fixture.harness.launch_alive = False
    fixture.runtime.ensure_ready(fixture.profile)

    with pytest.raises(ImageGenerationUnavailableError, match="unavailable"):
        fixture.runtime.prepare(fixture.workflow)

    assert fixture.harness.processes[0].closed
    assert fixture.harness.guards[0].closed
    assert not fixture.harness.runs[0].root.exists()


def test_wrong_pid_listener_ownership_times_out_and_rolls_back(tmp_path: Path) -> None:
    """Never contact a loopback server that is not owned by the launched PID."""

    fixture = _runtime_fixture(tmp_path)
    fixture.harness.owner_available = False
    fixture.harness.clock.step = 1.0
    fixture.runtime.ensure_ready(fixture.profile)

    with pytest.raises(ImageGenerationUnavailableError, match="unavailable"):
        fixture.runtime.prepare(fixture.workflow)

    assert fixture.harness.raw_configs == []
    assert fixture.harness.sleep_calls == []
    assert fixture.harness.processes[0].closed
    assert not fixture.harness.runs[0].root.exists()


def test_readiness_outage_retries_within_one_deadline_then_rolls_back(
    tmp_path: Path,
) -> None:
    """Bound retryable API startup failures by one non-renewable deadline."""

    fixture = _runtime_fixture(tmp_path)
    fixture.harness.raw.always_unavailable = True
    fixture.harness.clock.step = 0.04
    fixture.runtime.ensure_ready(fixture.profile)

    with pytest.raises(ImageGenerationUnavailableError, match="unavailable"):
        fixture.runtime.prepare(fixture.workflow)

    assert len(fixture.harness.raw_configs) >= 2
    assert fixture.harness.sleep_calls
    assert all(delay <= 0.05 for delay in fixture.harness.sleep_calls)
    assert fixture.harness.processes[0].closed
    assert fixture.harness.guards[0].closed
    assert not fixture.harness.runs[0].root.exists()


def test_invalid_raw_handle_is_a_protocol_failure_with_full_rollback(
    tmp_path: Path,
) -> None:
    """Preserve protocol attribution while cleaning a malformed adapter result."""

    fixture = _runtime_fixture(tmp_path)
    fixture.harness.raw.prepare_result = object()
    fixture.runtime.ensure_ready(fixture.profile)

    with pytest.raises(ImageGenerationProtocolError, match="invalid prompt handle"):
        fixture.runtime.prepare(fixture.workflow)

    assert fixture.harness.processes[0].closed
    assert fixture.harness.guards[0].closed
    assert not fixture.harness.runs[0].root.exists()


def test_startup_cleanup_failure_takes_priority_over_protocol_attribution(
    tmp_path: Path,
) -> None:
    """Never report a lesser adapter defect while private residue is unproven."""

    fixture = _runtime_fixture(tmp_path)
    fixture.harness.raw.prepare_result = object()
    fixture.harness.remove_failures = 1
    fixture.runtime.ensure_ready(fixture.profile)

    with pytest.raises(ImageGenerationUnavailableError, match="cleaned safely"):
        fixture.runtime.prepare(fixture.workflow)

    assert fixture.harness.runs[0].root.exists()
    with pytest.raises(ImageGenerationUnavailableError, match="cleaned safely"):
        fixture.runtime.close()


def test_prepare_rejects_an_invalid_port_before_process_launch(tmp_path: Path) -> None:
    """Refuse an invalid allocator result after releasing directory and guard."""

    fixture = _runtime_fixture(tmp_path)
    fixture.harness.port = 0
    fixture.runtime.ensure_ready(fixture.profile)

    with pytest.raises(ImageGenerationUnavailableError, match="unavailable"):
        fixture.runtime.prepare(fixture.workflow)

    assert fixture.harness.processes == []
    assert fixture.harness.guards[0].closed
    assert not fixture.harness.runs[0].root.exists()


def test_submission_requires_exact_handle_workflow_and_single_use(tmp_path: Path) -> None:
    """Reject forged handles, changed graphs, and duplicate native submission."""

    fixture = _runtime_fixture(tmp_path)
    handle = _prepare(fixture)
    forged = ComfyPromptHandle(
        prompt_id=str(uuid4()),
        client_id=handle.client_id,
        output_filename_prefix=handle.output_filename_prefix,
    )
    changed = compile_txt2img_workflow(
        ImageGenerationSpec(
            profile_id=fixture.profile.profile_id,
            size_preset_id="square",
            appearance="different original adult character",
            seed=11,
            original_fictional_confirmed=True,
        ),
        fixture.profile,
        job_id=str(uuid4()),
    )

    with pytest.raises(ImageGenerationProtocolError, match="active private runtime"):
        fixture.runtime.submit_prepared(forged, fixture.workflow)
    with pytest.raises(ImageGenerationProtocolError, match="active private workflow"):
        fixture.runtime.submit_prepared(handle, changed)
    fixture.runtime.submit_prepared(handle, fixture.workflow)
    with pytest.raises(ImageGenerationProtocolError, match="active private workflow"):
        fixture.runtime.submit_prepared(handle, fixture.workflow)

    fixture.runtime.finalize(handle)
    assert fixture.harness.raw.calls.count("submit") == 1


def test_lost_listener_ownership_stops_all_active_operations(tmp_path: Path) -> None:
    """Fail closed before submission if the listener no longer belongs to the child."""

    fixture = _runtime_fixture(tmp_path)
    handle = _prepare(fixture)
    fixture.harness.owner_available = False

    with pytest.raises(ImageGenerationUnavailableError, match="unavailable"):
        fixture.runtime.submit_prepared(handle, fixture.workflow)

    assert "submit" not in fixture.harness.raw.calls
    fixture.harness.owner_available = True
    fixture.runtime.finalize(handle)


def test_wait_and_fetch_enforce_phase_and_descriptor_correlation(tmp_path: Path) -> None:
    """Allow output bytes only for the descriptor observed after exact submission."""

    fixture = _runtime_fixture(tmp_path)
    handle = _prepare(fixture)
    unrelated = ComfyOutputDescriptor("unrelated.png", "", "output")

    with pytest.raises(ImageGenerationProtocolError, match="not been submitted"):
        fixture.runtime.wait_for_output(handle, "7")
    with pytest.raises(ImageGenerationProtocolError, match="does not belong"):
        fixture.runtime.fetch_png(
            unrelated,
            expected_width=512,
            expected_height=512,
        )
    fixture.runtime.submit_prepared(handle, fixture.workflow)
    descriptor = fixture.runtime.wait_for_output(handle, "7")
    with pytest.raises(ImageGenerationProtocolError, match="does not belong"):
        fixture.runtime.fetch_png(
            unrelated,
            expected_width=512,
            expected_height=512,
        )
    image = fixture.runtime.fetch_png(
        descriptor,
        expected_width=512,
        expected_height=512,
    )

    assert image.height == 512
    fixture.runtime.finalize(handle)


def test_invalid_output_descriptor_from_raw_adapter_is_rejected(tmp_path: Path) -> None:
    """Do not admit an untyped output identity into the active session."""

    fixture = _runtime_fixture(tmp_path)
    fixture.harness.raw.wait_result = object()
    handle = _prepare(fixture)
    fixture.runtime.submit_prepared(handle, fixture.workflow)

    with pytest.raises(ImageGenerationProtocolError, match="invalid output descriptor"):
        fixture.runtime.wait_for_output(handle, "7")

    fixture.runtime.finalize(handle)


def test_finalize_ignores_cancel_failure_but_still_proves_cleanup(tmp_path: Path) -> None:
    """Treat API cancellation as advisory when process-tree termination succeeds."""

    fixture = _runtime_fixture(tmp_path)
    fixture.harness.raw.cancel_error = RuntimeError("synthetic adapter defect")
    handle = _prepare(fixture)
    run = fixture.harness.runs[0]

    fixture.runtime.finalize(handle)

    assert fixture.harness.raw.calls[-1] == "cancel"
    assert fixture.harness.processes[0].closed
    assert fixture.harness.guards[0].closed
    assert not run.root.exists()


def test_failed_process_teardown_poison_runtime_and_preserves_private_state(
    tmp_path: Path,
) -> None:
    """Never remove files or release guards while a child may still use them."""

    fixture = _runtime_fixture(tmp_path)
    fixture.harness.terminate_succeeds = False
    handle = _prepare(fixture)
    run = fixture.harness.runs[0]

    with pytest.raises(ImageGenerationUnavailableError, match="cleaned safely"):
        fixture.runtime.finalize(handle)

    assert run.root.exists()
    assert not fixture.harness.guards[0].closed
    assert fixture.harness.removed_runs == []
    with pytest.raises(ImageGenerationUnavailableError, match="unavailable"):
        fixture.runtime.ensure_ready(fixture.profile)


def test_closed_job_is_authoritative_even_if_the_old_pid_is_reused(
    tmp_path: Path,
) -> None:
    """Avoid leaking private files because an old PID was reused after teardown."""

    fixture = _runtime_fixture(tmp_path)
    handle = _prepare(fixture)
    run = fixture.harness.runs[0]
    fixture.harness.retain_owner_after_close = True

    fixture.runtime.finalize(handle)

    assert fixture.harness.processes[0].closed
    assert fixture.harness.guards[0].closed
    assert not run.root.exists()


def test_directory_cleanup_failure_can_be_retried_without_repeating_cancel(
    tmp_path: Path,
) -> None:
    """Retain correlation state until exact-root deletion eventually succeeds."""

    fixture = _runtime_fixture(tmp_path)
    fixture.harness.remove_failures = 1
    handle = _prepare(fixture)
    run = fixture.harness.runs[0]

    with pytest.raises(ImageGenerationUnavailableError, match="cleaned safely"):
        fixture.runtime.finalize(handle)

    assert run.root.exists()
    fixture.runtime.finalize(handle)
    assert not run.root.exists()
    assert fixture.harness.raw.calls.count("cancel") == 1
    assert len(fixture.harness.processes[0].terminate_calls) == 1
    assert fixture.harness.guards[0].close_calls == 1
    assert fixture.harness.removed_runs == [run, run]


def test_wrong_finalize_handle_cannot_destroy_an_active_job(tmp_path: Path) -> None:
    """Reject an unrelated handle without touching the owned private session."""

    fixture = _runtime_fixture(tmp_path)
    handle = _prepare(fixture)
    wrong = ComfyPromptHandle(
        prompt_id=str(uuid4()),
        client_id=handle.client_id,
        output_filename_prefix=handle.output_filename_prefix,
    )

    with pytest.raises(ImageGenerationProtocolError, match="does not belong"):
        fixture.runtime.finalize(wrong)

    assert fixture.harness.processes[0].alive
    assert fixture.harness.raw.calls[-1] == "prepare"
    fixture.runtime.finalize(handle)


def test_close_is_idempotent_for_idle_preflight_and_active_states(
    tmp_path: Path,
) -> None:
    """Give application shutdown one safe path before and after child startup."""

    idle = _runtime_fixture(tmp_path / "idle")
    idle.runtime.ensure_ready(idle.profile)
    idle.runtime.close()
    idle.runtime.close()
    assert idle.harness.runs == []
    with pytest.raises(ImageGenerationUnavailableError, match="unavailable"):
        idle.runtime.ensure_ready(idle.profile)

    active = _runtime_fixture(tmp_path / "active")
    _prepare(active)
    run = active.harness.runs[0]
    active.runtime.close()
    active.runtime.close()
    assert active.harness.processes[0].closed
    assert active.harness.guards[0].closed
    assert not run.root.exists()


def test_context_manager_closes_an_active_private_child(tmp_path: Path) -> None:
    """Converge structured application lifetime on the same cleanup proof."""

    fixture = _runtime_fixture(tmp_path)
    with fixture.runtime as runtime:
        runtime.ensure_ready(fixture.profile)
        runtime.prepare(fixture.workflow)
        assert fixture.harness.processes[0].alive

    assert fixture.harness.processes[0].closed
    assert not fixture.harness.runs[0].root.exists()


def test_finalize_and_close_serialize_the_complete_cleanup_transaction(
    tmp_path: Path,
) -> None:
    """Prevent concurrent terminal callers from deleting or reopening twice."""

    fixture = _runtime_fixture(tmp_path)
    handle = _prepare(fixture)
    process = fixture.harness.processes[0]
    original_terminate = process.terminate
    terminate_entered = Event()
    permit_terminate = Event()
    close_started = Event()
    close_finished = Event()
    errors: list[BaseException] = []

    def blocking_terminate(timeout_seconds: float = 5.0) -> bool:
        """Hold the first cleanup inside process teardown for race coverage."""

        terminate_entered.set()
        assert permit_terminate.wait(2.0)
        return original_terminate(timeout_seconds)

    def finalize_job() -> None:
        """Run the normal per-job finalizer and capture unexpected failures."""

        try:
            fixture.runtime.finalize(handle)
        except BaseException as error:
            errors.append(error)

    def close_runtime() -> None:
        """Request permanent closure while finalization owns the terminal lock."""

        close_started.set()
        try:
            fixture.runtime.close()
        except BaseException as error:
            errors.append(error)
        finally:
            close_finished.set()

    process.terminate = blocking_terminate  # type: ignore[method-assign]
    finalizer = Thread(target=finalize_job)
    closer = Thread(target=close_runtime)
    finalizer.start()
    assert terminate_entered.wait(2.0)
    closer.start()
    assert close_started.wait(2.0)
    assert not close_finished.wait(0.05)
    permit_terminate.set()
    finalizer.join(2.0)
    closer.join(2.0)

    assert not finalizer.is_alive()
    assert not closer.is_alive()
    assert errors == []
    assert fixture.harness.removed_runs == [fixture.harness.runs[0]]
    with pytest.raises(ImageGenerationUnavailableError, match="unavailable"):
        fixture.runtime.ensure_ready(fixture.profile)


def test_preflight_rejects_private_state_residue_without_deleting_it(
    tmp_path: Path,
) -> None:
    """Fail closed on hard-crash residue instead of adopting private artifacts."""

    fixture = _runtime_fixture(tmp_path)
    residue = fixture.config.private_state_root / str(uuid4())
    residue.mkdir()
    secret = residue / "prompt.json"
    secret.write_text("private prompt", encoding="utf-8")

    with pytest.raises(ImageGenerationUnavailableError, match="unavailable"):
        fixture.runtime.ensure_ready(fixture.profile)

    assert secret.read_text(encoding="utf-8") == "private prompt"
    assert fixture.harness.runs == []
    assert fixture.harness.processes == []


def test_extra_model_config_created_after_preflight_aborts_startup(
    tmp_path: Path,
) -> None:
    """Repeat the model-root check immediately before the child launch."""

    fixture = _runtime_fixture(tmp_path)
    fixture.runtime.ensure_ready(fixture.profile)
    fixture.harness.create_extra_model_config_after_guard = True

    with pytest.raises(ImageGenerationUnavailableError, match="unavailable"):
        fixture.runtime.prepare(fixture.workflow)

    assert fixture.harness.processes == []
    assert fixture.harness.guards[0].closed
    assert not fixture.harness.runs[0].root.exists()


def test_initialized_runtime_configuration_error_is_not_retried(
    tmp_path: Path,
) -> None:
    """Treat missing nodes or checkpoints after readiness as a stable failure."""

    fixture = _runtime_fixture(tmp_path)
    fixture.harness.raw.ready_failures = 1
    fixture.runtime.ensure_ready(fixture.profile)

    with pytest.raises(ImageGenerationUnavailableError, match="unavailable"):
        fixture.runtime.prepare(fixture.workflow)

    assert fixture.harness.raw_configs == [fixture.harness.raw_configs[0]]
    assert fixture.harness.sleep_calls == []
    assert not fixture.harness.runs[0].root.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows environment contract")
def test_production_environment_redirects_known_caches_into_private_state(
    tmp_path: Path,
) -> None:
    """Keep common model and compiler caches inside the deletable run root."""

    trusted_root = tmp_path / "private"
    trusted_root.mkdir()
    run = create_private_run_directory(trusted_root)
    try:
        environment = managed._closed_environment(run)
        for name in (
            "APPDATA",
            "CUDA_CACHE_PATH",
            "HF_HOME",
            "HUGGINGFACE_HUB_CACHE",
            "LOCALAPPDATA",
            "MPLCONFIGDIR",
            "NUMBA_CACHE_DIR",
            "PIP_CACHE_DIR",
            "TORCH_HOME",
            "TRITON_CACHE_DIR",
            "XDG_CACHE_HOME",
        ):
            assert environment[name] == str(run.cache)
        assert environment["HOME"] == str(run.user)
        assert environment["USERPROFILE"] == str(run.user)
        assert "HTTP_PROXY" not in environment
    finally:
        remove_private_run_directory(run)


@pytest.mark.parametrize("protected_field", ["runtime_root", "models_root"])
def test_config_rejects_private_state_overlap(
    tmp_path: Path,
    protected_field: str,
) -> None:
    """Keep whole-run deletion disjoint from runtime and model installations."""

    fixture = _runtime_fixture(tmp_path)
    values = {
        "runtime_root": fixture.config.runtime_root,
        "python_executable": fixture.config.python_executable,
        "models_root": fixture.config.models_root,
        "private_state_root": getattr(fixture.config, protected_field) / "private",
        "python_sha256": fixture.config.python_sha256,
        "comfyui_main_sha256": fixture.config.comfyui_main_sha256,
    }

    with pytest.raises(ValueError, match="must not overlap"):
        managed.ManagedComfyUiConfig(**values)


@pytest.mark.parametrize(
    "mutation",
    ["python", "main", "checkpoint", "extra-model-roots", "platform"],
)
def test_preflight_rejects_untrusted_runtime_inputs_without_side_effects(
    tmp_path: Path,
    mutation: str,
) -> None:
    """Reject changed executables, changed models, model aliases, and non-Windows."""

    fixture = _runtime_fixture(tmp_path)
    if mutation == "python":
        fixture.config.python_executable.write_bytes(b"changed python")
    elif mutation == "main":
        fixture.config.main_script.write_bytes(b"changed main")
    elif mutation == "checkpoint":
        checkpoint = (
            fixture.config.models_root
            / "checkpoints"
            / fixture.profile.checkpoint_name
        )
        checkpoint.write_bytes(b"changed checkpoint")
    elif mutation == "extra-model-roots":
        (fixture.config.runtime_root / "extra_model_paths.yaml").write_text(
            "other: {}",
            encoding="utf-8",
        )
    else:
        fixture.harness.platform_name = "posix"
        runtime = managed.ManagedComfyUiRuntime._create_for_testing(
            fixture.config,
            fixture.harness.dependencies(),
            dependency_seal=managed._TEST_DEPENDENCY_SEAL,
        )
    if mutation != "platform":
        runtime = fixture.runtime

    with pytest.raises(ImageGenerationUnavailableError, match="unavailable"):
        runtime.ensure_ready(fixture.profile)

    assert fixture.harness.runs == []
    assert fixture.harness.guards == []
    assert fixture.harness.processes == []


def test_test_constructor_rejects_an_unsealed_dependency_set(tmp_path: Path) -> None:
    """Prevent external callers from substituting privileged runtime seams."""

    fixture = _runtime_fixture(tmp_path)

    with pytest.raises(ImageGenerationUnavailableError, match="unavailable"):
        managed.ManagedComfyUiRuntime._create_for_testing(
            fixture.config,
            fixture.harness.dependencies(),
            dependency_seal=object(),
        )
