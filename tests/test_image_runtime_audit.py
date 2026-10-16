"""Verify the metadata-only image dependency audit against tampered miniature installs."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
from pathlib import Path
import sys
from typing import Any

import pytest

from scripts import audit_image_runtime as audit


def _digest(payload: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()


def _install(root: Path, *, extra_rows: list[list[str]] | None = None) -> tuple[Path, Path]:
    site = root / "Lib/site-packages"
    directory = site / "example-1.0.dist-info"
    directory.mkdir(parents=True)
    metadata = (b"Metadata-Version: 2.4\nName: example\nVersion: 1.0\nLicense-Expression: MIT\n"
                b"License-File: LICENSE\nRequires-Dist: dependency>=1\n\nA fixture.\n")
    (directory / "METADATA").write_bytes(metadata)
    (directory / "licenses").mkdir()
    (directory / "licenses/LICENSE").write_bytes(b"Fixture license\n")
    (site / "example.py").write_bytes(b"raise RuntimeError('Never import an audited package')\n")
    rows = [["example.py", _digest((site / "example.py").read_bytes()), str((site / "example.py").stat().st_size)],
            ["example-1.0.dist-info/METADATA", _digest(metadata), str(len(metadata))],
            ["example-1.0.dist-info/licenses/LICENSE", _digest(b"Fixture license\n"), "16"],
            ["example-1.0.dist-info/RECORD", "", ""], ["__pycache__/example.pyc", "", ""]]
    rows.extend(extra_rows or [])
    output = io.StringIO()
    csv.writer(output, lineterminator="\n").writerows(rows)
    (directory / "RECORD").write_text(output.getvalue(), encoding="utf-8")
    return site, directory


def test_hashes_installed_files_and_records_license_without_importing(tmp_path: Path) -> None:
    """Declared hashes and license files are verified while executable code remains inert."""

    site, _ = _install(tmp_path)
    report = audit.audit_site_packages(site, tmp_path)
    assert report["distributionCount"] == 1
    assert report["verifiedSha256Files"] == 3
    assert report["expectedUnhashedFiles"] == 2
    assert report["issueCount"] == report["unrecordedFileCount"] == 0
    package = report["packages"][0]
    assert package["licenseExpression"] == "MIT"
    assert package["requiresDist"] == ["dependency>=1"]
    assert len(package["licenseFiles"]) == 1


@pytest.mark.parametrize("change,reason", [
    ("tamper", "sha256-mismatch"), ("delete", "missing-unsafe-or-changing-file"),
    ("size", "record-size-mismatch"),
])
def test_changed_missing_and_wrong_size_files_are_findings(tmp_path: Path, change: str, reason: str) -> None:
    """Each corruption remains a failed integrity finding instead of becoming verified."""

    site, directory = _install(tmp_path)
    if change == "tamper":
        (site / "example.py").write_bytes(b"unexpected code")
    elif change == "delete":
        (site / "example.py").unlink()
    else:
        record = directory / "RECORD"
        record.write_text(record.read_text().replace(",16\n", ",17\n"), encoding="utf-8")
    report = audit.audit_site_packages(site, tmp_path)
    assert report["verifiedSha256Files"] == 2
    assert reason in {item["reason"] for item in report["issues"]}


def test_record_traversal_can_reach_scripts_but_cannot_leave_runtime(tmp_path: Path) -> None:
    """Wheel console scripts stay in scope while external filesystem entries are rejected."""

    root = tmp_path / "runtime"
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    (root / "Scripts").mkdir(parents=True)
    (root / "Scripts/tool.exe").write_bytes(b"tool")
    site, _ = _install(root, extra_rows=[
        ["../../Scripts/tool.exe", _digest(b"tool"), "4"],
        ["../../../outside.txt", _digest(b"outside"), "7"],
    ])
    report = audit.audit_site_packages(site, root)
    assert report["verifiedSha256Files"] == 4
    assert any(item["reason"] == "record-escape" for item in report["issues"])


def test_multiple_hardlinks_do_not_count_as_stable_private_files(tmp_path: Path) -> None:
    """An external alias can change bytes and therefore prevents an integrity claim."""

    site, _ = _install(tmp_path)
    os.link(site / "example.py", tmp_path / "alias.py")
    report = audit.audit_site_packages(site, tmp_path)
    assert report["verifiedSha256Files"] == 2
    assert any(item["reason"] == "missing-unsafe-or-changing-file" for item in report["issues"])


def test_unknown_hash_unhashed_payload_and_unrecorded_code_are_visible(tmp_path: Path) -> None:
    """Coverage omissions are distinct from verified files and expected bytecode omissions."""

    site, _ = _install(tmp_path, extra_rows=[["raw.bin", "", "1"], ["other.bin", "sha512=abc", "1"]])
    (site / "raw.bin").write_bytes(b"x")
    (site / "other.bin").write_bytes(b"y")
    (site / "unrecorded.py").write_bytes(b"raise AssertionError('Do not execute')")
    report = audit.audit_site_packages(site, tmp_path)
    assert report["otherUnhashedFiles"] == 1
    assert report["unrecordedFileCount"] == 1
    assert report["unrecordedFiles"] == ["Lib/site-packages/unrecorded.py"]
    assert {item["reason"] for item in report["issues"]} == {
        "unhashed-installed-file", "unsupported-or-invalid-record-hash",
    }


def test_missing_record_is_not_silently_an_empty_verified_package(tmp_path: Path) -> None:
    """Installers may omit RECORD, but the resulting coverage gap must survive the report."""

    site, directory = _install(tmp_path)
    (directory / "RECORD").unlink()
    report = audit.audit_site_packages(site, tmp_path)
    assert report["verifiedSha256Files"] == 0
    assert report["issues"][0]["reason"] == "missing-record"
    assert report["unrecordedFileCount"] == 3


def test_duplicate_record_rows_are_all_checked_and_verified_files_count_once(tmp_path: Path) -> None:
    """An unhashed first row cannot suppress a later hash or hide a conflicting duplicate."""

    site, _ = _install(tmp_path, extra_rows=[
        ["example.py", "", ""], ["example.py", _digest(b"conflicting bytes"), "17"],
    ])
    report = audit.audit_site_packages(site, tmp_path)
    assert report["verifiedSha256Files"] == 3
    assert report["otherUnhashedFiles"] == 0
    assert report["issueCountsByReason"] == {"duplicate-record-path": 2, "sha256-mismatch": 1}
    assert report["issueCountsByPackage"] == {"example": 3}


@pytest.mark.parametrize("limits", [audit.AuditLimits(distributions=0), audit.AuditLimits(file_bytes=1),
                                    audit.AuditLimits(total_bytes=1), audit.AuditLimits(records=1)])
def test_inventory_budget_exhaustion_stops_instead_of_returning_partial_success(tmp_path: Path,
                                                                              limits: audit.AuditLimits) -> None:
    """Count, file, aggregate byte and traversal bounds are mandatory audit limits."""

    site, _ = _install(tmp_path)
    with pytest.raises(audit.ImageRuntimeAuditError):
        audit.audit_site_packages(site, tmp_path, limits=limits)


def test_audited_site_cannot_escape_allowed_runtime(tmp_path: Path) -> None:
    """The caller's package directory must belong to its explicitly selected runtime."""

    with pytest.raises(audit.ImageRuntimeAuditError):
        audit.audit_site_packages(tmp_path / "outside", tmp_path / "runtime")


def test_cached_ancestor_replacement_is_detected_before_snapshot_returns(tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Directory caching must retain its final identity verification after hashing."""

    site, _ = _install(tmp_path, extra_rows=[["assets/data.bin", _digest(b"data"), "4"]])
    assets = site / "assets"
    assets.mkdir()
    (assets / "data.bin").write_bytes(b"data")
    original = audit._hash_file

    def swap(path: Path, root: Path, budget: Any) -> tuple[str, int]:
        result = original(path, root, budget)
        if path == assets / "data.bin":
            assets.rename(site / "old-assets")
            assets.mkdir()
            (assets / "data.bin").write_bytes(b"data")
        return result

    monkeypatch.setattr(audit, "_hash_file", swap)
    with pytest.raises(audit.ImageRuntimeAuditError, match="directory changed"):
        audit.audit_site_packages(site, tmp_path)


def test_osv_queries_local_and_public_versions_and_retains_pagination() -> None:
    """CUDA wheel tags retain exact queries and public upstream coverage across all pages."""

    calls = []

    def request(body: dict[str, Any]) -> dict[str, Any]:
        calls.append(body)
        if len(calls) == 1:
            return {"results": [{"vulns": [{"id": "GHSA-first"}], "next_page_token": "page2"}, {}]}
        assert body["queries"][0]["page_token"] == "page2"
        return {"results": [{"vulns": [{"id": "GHSA-second"}]}]}

    report = audit.query_osv([{"name": "torch", "version": "2.14.0+cu130"}], request=request)
    assert report["status"] == "complete"
    assert report["completedQueries"] == report["requestedQueries"] == 2
    assert {item["version"] for item in calls[0]["queries"]} == {"2.14.0", "2.14.0+cu130"}
    assert [item["id"] for item in report["affectedPackages"][0]["advisories"]] == ["GHSA-first", "GHSA-second"]


@pytest.mark.parametrize("response", [{}, {"results": []}, {"results": [{"vulns": [{"id": "../invalid"}]}]}])
def test_invalid_advisory_response_is_incomplete(response: dict[str, Any]) -> None:
    """Invalid service output cannot be reported as a successful zero-advisory scan."""

    report = audit.query_osv([{"name": "example", "version": "1"}], request=lambda _: response)
    assert report["status"] == "incomplete"


def test_advisory_network_failure_and_endless_pagination_remain_incomplete() -> None:
    """Offline and perpetually paginated databases preserve a visible audit gap."""

    def offline(_: dict[str, Any]) -> dict[str, Any]:
        raise OSError("private transport error")

    assert audit.query_osv([{"name": "example", "version": "1"}], request=offline)["status"] == "incomplete"
    calls = []

    def endless(body: dict[str, Any]) -> dict[str, Any]:
        calls.append(body)
        return {"results": [{"next_page_token": "again"}]}

    report = audit.query_osv([{"name": "example", "version": "1"}], request=endless)
    assert report["status"] == "incomplete"
    assert len(calls) == 8


def test_provenance_checks_bytes_and_revision_independently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A matching checkout does not excuse a mismatched checkpoint digest."""

    (tmp_path / "comfy").mkdir()
    (tmp_path / "comfy/main.py").write_bytes(b"# Entrypoint\n")
    (tmp_path / "model.safetensors").write_bytes(b"model")
    monkeypatch.setattr(audit, "_run_command", lambda args: (0, b"revision\n" if "rev-parse" in args else b"", b""))
    report = audit.audit_provenance(tmp_path, {
        "comfyRelativeRoot": "comfy", "comfyRevision": "revision",
        "artifacts": [{"relativePath": "model.safetensors", "sha256": "0" * 64}],
    })
    assert report["revisionMatches"] and report["trackedCheckoutClean"]
    assert not report["artifacts"][0]["matches"]


def test_subprocess_capture_rejects_oversized_output_and_deadlines() -> None:
    """Auditing trusted pip still bounds its output and wall time without GPU work."""

    with pytest.raises(audit.ImageRuntimeAuditError, match="output budget"):
        audit._run_command([sys.executable, "-c", "print('x' * 1000000)"], output_limit=1024)
    with pytest.raises(audit.ImageRuntimeAuditError, match="deadline"):
        audit._run_command([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.05)
