"""Check bounded real-image acceptance inputs and durable failure reporting."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import smoke_local_image_generation as smoke


@pytest.mark.parametrize("row", ("", "N/A, 12000", "1, 2, 3", "-1, 12000", "13000, 12000", "0, 0"))
def test_gpu_memory_rejects_missing_or_impossible_measurements(row: str) -> None:
    """Avoid reporting missing driver data or impossible values as measured peaks."""

    with pytest.raises(ValueError):
        smoke._gpu_memory(row)


def test_gpu_memory_preserves_global_used_and_total_mib() -> None:
    """Keep the actual whole-device sample without inventing process attribution."""

    assert smoke._gpu_memory(" 2345, 12282 \n") == (2345, 12282)


def test_process_observation_retains_surviving_orphans(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail cleanup observation even after a tracked child's parent disappears."""

    sampler = smoke._GpuSampler()
    sampler._observed_pids.update({101, 102})
    monkeypatch.setattr(smoke, "_process_parent_ids", lambda: {102: 101, 500: 1})
    monkeypatch.setattr(sampler._thread, "join", lambda timeout: None)
    summary = sampler._finish()
    assert summary["remainingOwnedProcessIds"] == [102]


@pytest.mark.parametrize("arguments", (["--cycles", "0"], ["--cycles", "51"], ["--soak-seconds", "3601"], ["--steps", "7"]))
def test_acceptance_rejects_unbounded_work(arguments: list[str]) -> None:
    """Bound repeated native processes and GPU work before installation access."""

    with pytest.raises(SystemExit) as error:
        smoke._arguments(arguments)
    assert error.value.code == 2


def test_report_survives_generation_and_cleanup_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retain both failures so a broken native teardown cannot erase evidence."""

    class _Sampler:
        def _start(self) -> None:
            pass

        def _finish(self) -> dict[str, int]:
            return {"sampleCount": 0}

    class _App:
        def close(self) -> None:
            """Simulate a failed native cleanup without touching a real child."""

            raise OSError("fixture cleanup failure")

    def fail_generation(*arguments: Any) -> dict[str, Any]:
        """Fail the job after service creation so finally must still write evidence."""

        raise ValueError("fixture generation failure")

    monkeypatch.setattr(smoke, "_REPOSITORY", tmp_path)
    monkeypatch.setattr(smoke, "_GpuSampler", _Sampler)
    monkeypatch.setattr(smoke, "_installation", lambda *args: SimpleNamespace(python_sha256="0" * 64, comfyui_main_sha256="1" * 64))
    monkeypatch.setattr(smoke, "_service", lambda *args: _App())
    monkeypatch.setattr(smoke, "_generate", fail_generation)
    assert smoke.main(["--cycles", "1"]) == 1
    reports = tuple((tmp_path / "tmp" / "image-real-acceptance").glob("*/acceptance.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["passed"] is False
    assert report["failureType"] == "ValueError"
    assert report["cleanupFailureType"] == "OSError"
    assert report["gpuMemory"] == {"sampleCount": 0}
