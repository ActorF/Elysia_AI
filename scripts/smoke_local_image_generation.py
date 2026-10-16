"""Exercise the real isolated ComfyUI runtime without changing desktop settings.

This operator tool uses an already verified official SDXL installation, the
production application factory, a fresh private data root, and the fixed trusted
workflow. It never downloads models, selects arbitrary workflows, terminates
unrelated processes, or writes the user's normal Gallery/configuration. Reports
contain local deployment paths and belong in the ignored ``tmp`` directory.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
from time import monotonic
from typing import Any
from uuid import uuid4

_REPOSITORY = Path(__file__).resolve().parents[1]
if str(_REPOSITORY) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY))

from desktop_image_settings import TrustedImageInstallation
from image_generation.application import ImageGenerationApplicationService
from image_generation.composition import create_managed_image_application_service
from image_generation.domain import ImageGenerationSpec
from image_generation.exceptions import (
    ImageGenerationCancelledError,
    ImageGenerationError,
    ImageGenerationUnavailableError,
)
from image_generation.managed_comfyui import ManagedComfyUiConfig
from image_generation.profiles import (
    ImageSizePreset,
    ModelArchitecture,
    ModelProfile,
    ModelProfileCatalog,
    PromptDialect,
)
from image_generation.service import ImageGenerationProgress, ImageGenerationStage

_MODEL_SHA256 = "31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b"


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _gpu_memory(row: str) -> tuple[int, int]:
    """Parse one bounded GPU memory row without attributing other applications."""

    values = row.strip().split(",")
    if len(values) != 2:
        raise ValueError("Unexpected GPU memory result.")
    used, total = (int(value.strip()) for value in values)
    if used < 0 or total <= 0 or used > total:
        raise ValueError("Invalid GPU memory result.")
    return used, total


def _process_parent_ids() -> dict[int, int]:
    """Snapshot process parent links without opening or stopping any process."""

    if os.name != "nt":
        return {}

    class _ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
            ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
            ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
            ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        function = getattr(kernel, name)
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
        function.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError("Process observation is unavailable.")
    parents: dict[int, int] = {}
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        present = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while present:
            parents[int(entry.pid)] = int(entry.parent)
            present = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return parents


def _descendant_pids(parent_pid: int) -> set[int]:
    """Observe the caller's process tree without opening or stopping other jobs."""

    parents = _process_parent_ids()
    # Follow parent identities only within the snapshot. The native runtime's
    # pinned Job handles remain the authoritative process ownership boundary;
    # this independent observation checks that no visible descendant remains.
    descendants: set[int] = set()
    frontier = {parent_pid}
    while frontier:
        children = {pid for pid, parent in parents.items() if parent in frontier}
        frontier = children - descendants - {parent_pid}
        descendants.update(frontier)
    return descendants


class _GpuSampler:
    """Record whole-device memory while preserving every unrelated GPU process."""

    def __init__(self) -> None:
        self._stop = Event()
        self._thread = Thread(target=self._sample, daemon=True)
        self.samples: list[tuple[int, int]] = []
        self._observed_pids: set[int] = set()
        self._process_observation_failed = False

    def _sample(self) -> None:
        while not self._stop.is_set():
            try:
                self._observed_pids.update(_descendant_pids(os.getpid()))
            except OSError:
                self._process_observation_failed = True
            try:
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                    check=True, capture_output=True, text=True, timeout=5.0,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                self.samples.append(_gpu_memory(result.stdout.splitlines()[0]))
            except (OSError, subprocess.SubprocessError, ValueError, IndexError):
                pass
            self._stop.wait(0.5)

    def _start(self) -> None:
        self._thread.start()

    def _finish(self) -> dict[str, object]:
        self._stop.set()
        self._thread.join(6.0)
        try:
            # Remember observed children even if an intermediate parent exited;
            # a fresh descendant walk alone would overlook surviving orphans.
            # PID reuse can conservatively fail this observation, never excuse
            # a leak. Native Job handles provide the authoritative identity.
            remaining = sorted(self._observed_pids.intersection(_process_parent_ids()))
        except OSError:
            remaining = []
            self._process_observation_failed = True
        return {
            "sampleCount": len(self.samples),
            "baselineUsedMiB": self.samples[0][0] if self.samples else None,
            "peakUsedMiB": max((used for used, _ in self.samples), default=None),
            "lastUsedMiB": self.samples[-1][0] if self.samples else None,
            "totalMiB": self.samples[0][1] if self.samples else None,
            "scope": "Whole GPU; includes pre-existing applications and is not per-process attribution.",
            "observedOwnedProcessIds": sorted(self._observed_pids),
            "remainingOwnedProcessIds": remaining,
            "processObservationFailed": self._process_observation_failed,
        }


def _installation(root: Path, python: Path | None, steps: int) -> TrustedImageInstallation:
    portable = root / "ComfyUI_windows_portable"
    interpreter = python or portable / "python_embeded" / "python.exe"
    runtime = portable / "ComfyUI"
    profile = ModelProfile(
        profile_id="official-sdxl", display_name="Official SDXL Base 1.0",
        architecture=ModelArchitecture.SDXL, prompt_dialect=PromptDialect.SDXL_NATURAL,
        checkpoint_name="sd_xl_base_1.0.safetensors", checkpoint_sha256=_MODEL_SHA256,
        checkpoint_source="https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/tree/76d28af79639c28a79fa5c6c6468febd3490a37e",
        checkpoint_license="CreativeML Open RAIL++-M",
        size_presets=(ImageSizePreset("square", 1024, 1024),),
        default_size_preset_id="square", steps=steps,
    )
    return TrustedImageInstallation(
        runtime_root=runtime, python_executable=interpreter, models_root=root / "models",
        python_sha256=_digest(interpreter), comfyui_main_sha256=_digest(runtime / "main.py"),
        profiles=ModelProfileCatalog((profile,)), default_profile_id=profile.profile_id,
    )


def _service(installation: TrustedImageInstallation, data: Path) -> ImageGenerationApplicationService:
    private = data / "workspace" / "image-runs"
    private.mkdir(parents=True, exist_ok=True)
    config = ManagedComfyUiConfig(
        runtime_root=installation.runtime_root, python_executable=installation.python_executable,
        models_root=installation.models_root, private_state_root=private,
        python_sha256=installation.python_sha256,
        comfyui_main_sha256=installation.comfyui_main_sha256,
        startup_timeout_seconds=180.0, job_timeout_seconds=240.0,
    )
    return create_managed_image_application_service(installation.profiles, config, data)


def _spec(seed: int) -> ImageGenerationSpec:
    return ImageGenerationSpec(
        profile_id="official-sdxl", size_preset_id="square", seed=seed,
        appearance="original adult woman age 30, silver hair, violet eyes",
        outfit="modest white dress", pose="standing alone", scene="quiet flower garden",
        style="soft digital illustration", lighting="soft afternoon light",
        original_fictional_confirmed=True,
    )


def _assert_clean(data: Path) -> None:
    # A run directory is deleted only by its production owner, after the native
    # Job confirms all descendants stopped. Never sweep unknown leftovers here.
    if any((data / "workspace" / "image-runs").iterdir()):
        raise AssertionError("Private runtime directories survived terminal cleanup.")


def _generate(app: ImageGenerationApplicationService, data: Path, seed: int) -> dict[str, Any]:
    started = monotonic()
    stages: list[str] = []

    def progress(value: ImageGenerationProgress) -> None:
        stages.append(value.stage.value)
        print(json.dumps({"stage": value.stage.value, "percent": value.percent}), flush=True)

    item = app.generate(app.create_operation_id(), _spec(seed), on_progress=progress)
    png = app.open(item.artifact_id)
    if (png.width, png.height) != (1024, 1024) or _digest_bytes(png.data) != item.sha256:
        raise AssertionError("Gallery PNG integrity did not match its committed manifest.")
    if not stages or stages[-1] != "completed":
        raise AssertionError("The committed image did not publish terminal progress.")
    (data / "sample.png").write_bytes(png.data)
    _assert_clean(data)
    return {"artifactId": item.artifact_id, "sha256": item.sha256,
            "width": png.width, "height": png.height, "seconds": round(monotonic() - started, 3)}


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cancel(app: ImageGenerationApplicationService, data: Path) -> dict[str, object]:
    before = len(app.list())
    operation = app.create_operation_id()
    sampling = False
    outcome = ""
    started = monotonic()

    def progress(value: ImageGenerationProgress) -> None:
        nonlocal sampling, outcome
        if value.stage is ImageGenerationStage.SAMPLING:
            sampling = True
            # ComfyUI executes asynchronously after prompt acceptance. Let the
            # real child begin work before testing interrupt/Job teardown.
            Event().wait(4.0)
            outcome = app.cancel(operation).value

    try:
        app.generate(operation, _spec(100), on_progress=progress)
    except ImageGenerationCancelledError:
        pass
    else:
        raise AssertionError("Cancellation unexpectedly committed an image.")
    if not sampling or outcome != "requested" or len(app.list()) != before:
        raise AssertionError("Cancellation failed its no-commit contract.")
    _assert_clean(data)
    return {"samplingReached": sampling, "outcome": outcome, "seconds": round(monotonic() - started, 3)}


def _shutdown(installation: TrustedImageInstallation, data: Path) -> dict[str, object]:
    app = _service(installation, data)
    sampling = Event()
    outcomes: list[str] = []

    def generate() -> None:
        try:
            app.generate(app.create_operation_id(), _spec(200), on_progress=lambda p: sampling.set() if p.stage is ImageGenerationStage.SAMPLING else None)
        except ImageGenerationCancelledError:
            outcomes.append("cancelled")
        except BaseException as error:
            outcomes.append(type(error).__name__)
        else:
            outcomes.append("unexpected_commit")

    thread = Thread(target=generate, daemon=True)
    thread.start()
    try:
        if not sampling.wait(300.0):
            raise AssertionError("Shutdown probe never reached real sampling.")
        started = monotonic()
        if app.request_shutdown():
            raise AssertionError("Gallery commit unexpectedly won the shutdown test.")
        app.close()
        thread.join(30.0)
        if thread.is_alive() or outcomes != ["cancelled"]:
            raise AssertionError("Shutdown did not drain the cancelled generation.")
        _assert_clean(data)
        return {"outcome": outcomes[0], "drainSeconds": round(monotonic() - started, 3)}
    finally:
        app.close()


def _failure(installation: TrustedImageInstallation, data: Path) -> dict[str, object]:
    models = data / "invalid-models"
    checkpoints = models / "checkpoints"
    checkpoints.mkdir(parents=True)
    payload = b"acceptance fixture: deliberately invalid safetensors"
    (checkpoints / "invalid.safetensors").write_bytes(payload)
    profile = replace(installation.profiles.profiles[0], checkpoint_name="invalid.safetensors", checkpoint_sha256=_digest_bytes(payload))
    broken = replace(installation, models_root=models, profiles=ModelProfileCatalog((profile,)))
    app = _service(broken, data)
    started = monotonic()
    try:
        try:
            app.generate(app.create_operation_id(), _spec(300))
        except ImageGenerationError as error:
            failure = type(error).__name__
        else:
            raise AssertionError("The corrupt real checkpoint unexpectedly generated.")
        if app.list():
            raise AssertionError("Failed generation published a Gallery artifact.")
        _assert_clean(data)
        return {"exception": failure, "seconds": round(monotonic() - started, 3)}
    finally:
        app.close()


def _recovery(installation: TrustedImageInstallation, data: Path, expected: list[str]) -> dict[str, object]:
    private = data / "workspace" / "image-runs"
    orphan = private / str(uuid4())
    orphan.mkdir()
    marker = orphan / "acceptance-owned-marker.txt"
    payload = b"Synthetic orphan fixture; never a real unfinished user job."
    marker.write_bytes(payload)
    try:
        app = _service(installation, data)
    except ImageGenerationUnavailableError:
        pass
    else:
        app.close()
        raise AssertionError("Unknown private state was silently adopted.")
    if marker.read_bytes() != payload:
        raise AssertionError("Recovery changed retained private data.")
    # Remove only the exact marker and empty directory created by this probe.
    # A real unknown directory is intentionally never auto-deleted by runtime.
    marker.unlink()
    orphan.rmdir()
    app = _service(installation, data)
    try:
        if {item.artifact_id for item in app.list()} != set(expected):
            raise AssertionError("Recovery changed committed Gallery artifacts.")
        for artifact in expected:
            if app.open(artifact).sha256 != app.get(artifact).sha256:
                raise AssertionError("Gallery integrity changed across service restart.")
        _assert_clean(data)
    finally:
        app.close()
    return {"unknownStateRejected": True, "unknownStatePreserved": True,
            "fixtureCleanup": "Only the exact owned marker and empty synthetic directory.",
            "galleryArtifactsRetained": len(expected)}


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installation-root", type=Path, default=_REPOSITORY / "models/cache/image-acceptance")
    parser.add_argument("--python-executable", type=Path)
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--soak-seconds", type=int, default=0)
    parser.add_argument("--steps", type=int, default=28)
    args = parser.parse_args(argv)
    if not 1 <= args.cycles <= 50 or not 0 <= args.soak_seconds <= 3600 or not 8 <= args.steps <= 50:
        parser.error("cycles must be 1..50, soak-seconds 0..3600, and steps 8..50")
    return args


def main(argv: list[str] | None = None) -> int:
    """Run bounded real generation/cancel/failure/shutdown and retain a JSON report."""

    args = _arguments(argv)
    data = _REPOSITORY / "tmp" / "image-real-acceptance" / uuid4().hex
    data.mkdir(parents=True)
    report: dict[str, Any] = {
        "startedAt": datetime.now(timezone.utc).isoformat(), "dataRoot": str(data),
        "passed": False, "generations": [], "requestedCycles": args.cycles,
        "requestedSoakSeconds": args.soak_seconds, "steps": args.steps,
    }
    sampler = _GpuSampler()
    sampler._start()
    app: ImageGenerationApplicationService | None = None
    started = monotonic()
    try:
        installation = _installation(args.installation_root.resolve(), args.python_executable, args.steps)
        report["pins"] = {"pythonSha256": installation.python_sha256, "mainSha256": installation.comfyui_main_sha256, "checkpointSha256": _MODEL_SHA256}
        app = _service(installation, data)
        generation_started = monotonic()
        deadline = monotonic() + args.soak_seconds
        while len(report["generations"]) < 50 and (len(report["generations"]) < args.cycles or monotonic() < deadline):
            report["generations"].append(_generate(app, data, 42 + len(report["generations"])))
            print(json.dumps({"event": "generation_completed", "count": len(report["generations"]), "seconds": report["generations"][-1]["seconds"]}), flush=True)
        report["generationWindowSeconds"] = round(monotonic() - generation_started, 3)
        report["generationCount"] = len(report["generations"])
        if monotonic() < deadline:
            raise AssertionError("The 50-job safety bound was reached before the requested soak duration.")
        print(json.dumps({"case": "cancel"}), flush=True)
        report["cancel"] = _cancel(app, data)
        app.close()
        app = None
        print(json.dumps({"case": "recovery"}), flush=True)
        report["recovery"] = _recovery(installation, data, [item["artifactId"] for item in report["generations"]])
        print(json.dumps({"case": "failure"}), flush=True)
        report["failure"] = _failure(installation, data / "failure")
        print(json.dumps({"case": "shutdown"}), flush=True)
        report["shutdown"] = _shutdown(installation, data / "shutdown")
        report["passed"] = True
    except BaseException as error:
        report["failureType"] = type(error).__name__
        report["failureMessage"] = str(error)
    finally:
        if app is not None:
            try:
                app.close()
            except BaseException as error:
                # A failed teardown is itself acceptance failure. Preserve its
                # evidence even when it would otherwise replace the job error.
                report["passed"] = False
                report["cleanupFailureType"] = type(error).__name__
        report["gpuMemory"] = sampler._finish()
        if report["gpuMemory"].get("remainingOwnedProcessIds") or report["gpuMemory"].get("processObservationFailed"):
            report["passed"] = False
            report["processCleanupFailed"] = True
        report["elapsedSeconds"] = round(monotonic() - started, 3)
        report["finishedAt"] = datetime.now(timezone.utc).isoformat()
        (data / "acceptance.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "report": str(data / "acceptance.json")}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
