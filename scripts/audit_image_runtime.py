"""Audit an isolated image runtime without importing model packages or using CUDA.

The report records pip's dependency view, installed metadata/license declarations,
bounded SHA-256 verification of wheel RECORD entries, unrecorded files, pinned
runtime/model provenance, and an optional dated OSV query. Installed RECORDs are
local consistency evidence, not publisher signatures or a complete native SBOM.
Nothing in this tool installs, upgrades, repairs, or launches ComfyUI.
"""

from __future__ import annotations

import argparse
import base64
from collections.abc import Callable
import csv
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from threading import Event, Thread
from time import monotonic
from typing import Any, BinaryIO
import urllib.request


class ImageRuntimeAuditError(Exception):
    """Stop an incomplete audit when a work, file, subprocess, or schema bound fails."""


class _AuditLimitError(ImageRuntimeAuditError):
    """Preserve exhausted-budget failures instead of treating them as file findings."""


@dataclass(frozen=True)
class AuditLimits:
    """Bound read-only inventory work independently of the size of a local install."""

    distributions: int = 512
    records: int = 500_000
    file_bytes: int = 8 * 1024**3
    total_bytes: int = 64 * 1024**3
    seconds: float = 1200.0


@dataclass
class _Budget:
    limits: AuditLimits
    deadline: float
    bytes_read: int = 0
    records: int = 0
    directories: dict[Path, tuple[int, int]] = field(default_factory=dict)

    def check(self, *, size: int = 0, records: int = 0) -> None:
        """Consume one shared work budget and reject files before oversized reads."""

        self.bytes_read += size
        self.records += records
        if (monotonic() > self.deadline or self.bytes_read > self.limits.total_bytes
                or self.records > self.limits.records):
            raise _AuditLimitError("The audit work limit was exceeded.")

    def verify_directories(self) -> None:
        """Reject persistent directory replacement during a quiescent inventory pass."""

        for path, identity in self.directories.items():
            self.check()
            value = path.lstat()
            if (not stat.S_ISDIR(value.st_mode) or stat.S_ISLNK(value.st_mode)
                    or getattr(value, "st_file_attributes", 0) & 0x400
                    or (value.st_dev, value.st_ino) != identity):
                raise ImageRuntimeAuditError("An inventory directory changed during inspection.")


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _identity(value: os.stat_result) -> tuple[int, int, int, int]:
    # Windows path-stat and CRT fstat expose different ctime semantics in
    # supported Python releases; file ID, device, size and mtime agree across
    # those APIs and are also used by the application's trusted file readers.
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _check_ancestors(path: Path, budget: _Budget | None) -> None:
    # Audit a trusted, quiescent installation rather than trying to sandbox a
    # malicious concurrent owner. Cache reviewed ancestor identities and recheck
    # all of them at the end; repeating every ancestor stat for every wheel file
    # otherwise makes large Windows CUDA installs prohibitively slow. Individual
    # files still receive fresh path/handle identity checks around every read.
    ancestors = []
    cursor = path.parent
    while budget is None or cursor not in budget.directories:
        ancestors.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    for directory in reversed(ancestors):
        value = directory.lstat()
        if (not stat.S_ISDIR(value.st_mode) or stat.S_ISLNK(value.st_mode)
                or getattr(value, "st_file_attributes", 0) & 0x400):
            raise ImageRuntimeAuditError("An inventory path contains a link or reparse point.")
        if budget is not None:
            budget.directories[directory] = value.st_dev, value.st_ino


def _checked_path(path: Path, root: Path, budget: _Budget | None = None) -> tuple[Path, os.stat_result]:
    path = Path(os.path.abspath(path))
    if not path.is_relative_to(root):
        raise ImageRuntimeAuditError("An inventory path leaves its allowed root.")
    _check_ancestors(path, budget)
    value = path.lstat()
    if (not stat.S_ISREG(value.st_mode) or value.st_nlink != 1
            or getattr(value, "st_file_attributes", 0) & 0x400):
        raise ImageRuntimeAuditError("An inventory entry is not a single-link regular file.")
    return path, value


def _read_file(path: Path, root: Path, budget: _Budget, *, maximum: int) -> bytes:
    path, before = _checked_path(path, root, budget)
    if before.st_size > maximum:
        raise _AuditLimitError("An inventory metadata file is too large.")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    chunks = bytearray()
    try:
        if _identity(os.fstat(descriptor)) != _identity(before):
            raise ImageRuntimeAuditError(f"An inventory file changed before inspection: {path.relative_to(root).as_posix()}.")
        while chunk := os.read(descriptor, min(64 * 1024, maximum + 1 - len(chunks))):
            budget.check(size=len(chunk))
            chunks.extend(chunk)
            if len(chunks) > maximum:
                raise _AuditLimitError("An inventory metadata file grew beyond its budget.")
        if (len(chunks) != before.st_size or _identity(before) != _identity(os.fstat(descriptor))
                or _identity(before) != _identity(path.lstat())):
            raise ImageRuntimeAuditError("An inventory file changed during inspection.")
    finally:
        os.close(descriptor)
    return bytes(chunks)


def _hash_file(path: Path, root: Path, budget: _Budget) -> tuple[str, int]:
    path, before = _checked_path(path, root, budget)
    if before.st_size > budget.limits.file_bytes:
        raise _AuditLimitError("An inventory file exceeds the per-file budget.")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    try:
        if _identity(os.fstat(descriptor)) != _identity(before):
            raise ImageRuntimeAuditError("An inventory file changed before hashing.")
        while chunk := os.read(descriptor, 1024**2):
            budget.check(size=len(chunk))
            digest.update(chunk)
        if (_identity(os.fstat(descriptor)) != _identity(before)
                or _identity(path.lstat()) != _identity(before)):
            raise ImageRuntimeAuditError("An inventory file changed while hashing.")
    finally:
        os.close(descriptor)
    return digest.hexdigest(), before.st_size


def audit_site_packages(site_packages: Path, allowed_root: Path, *,
                        limits: AuditLimits = AuditLimits()) -> dict[str, Any]:
    """Verify recorded files and report omissions using metadata without imports.

    Relative RECORD paths may reach the interpreter's Scripts/share directories,
    as permitted by the wheel format, but cannot leave the supplied runtime root.
    Bytecode and RECORD's own empty hash are counted as expected omissions.
    """

    root = Path(os.path.abspath(allowed_root))
    site = Path(os.path.abspath(site_packages))
    if not site.is_relative_to(root):
        raise ImageRuntimeAuditError("The package directory leaves the runtime root.")
    budget = _Budget(limits, monotonic() + limits.seconds)
    # Review the selected directory even when it contains no distribution
    # metadata; os.walk would otherwise follow a linked starting directory.
    _check_ancestors(site / ".audit-inventory-sentinel", budget)
    distributions = sorted(site.glob("*.dist-info"))
    if len(distributions) > limits.distributions:
        raise ImageRuntimeAuditError("The distribution count exceeds the audit budget.")
    packages: list[dict[str, Any]] = []
    recorded: set[Path] = set()
    issues: list[dict[str, str]] = []
    issue_count = 0
    reason_counts: dict[str, int] = {}
    package_counts: dict[str, int] = {}
    names: set[str] = set()

    def issue(package: str, path: str, reason: str) -> None:
        nonlocal issue_count
        issue_count += 1
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        package_counts[package] = package_counts.get(package, 0) + 1
        if len(issues) < 256:
            issues.append({"package": package, "path": path[:512], "reason": reason})

    for directory in distributions:
        budget.check()
        metadata_bytes = _read_file(directory / "METADATA", root, budget, maximum=4 * 1024**2)
        metadata = BytesParser().parsebytes(metadata_bytes, headersonly=True)
        name, version = str(metadata.get("Name", "")), str(metadata.get("Version", ""))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", name) or not version or len(version) > 128:
            raise ImageRuntimeAuditError("Installed package identity is invalid.")
        if _canonical(name) in names:
            issue(name, directory.name, "duplicate-distribution")
        names.add(_canonical(name))
        license_text = str(metadata.get("License", "")).strip()
        package: dict[str, Any] = {
            "name": name, "version": version,
            "licenseExpression": str(metadata.get("License-Expression", "")),
            "licenseDeclaration": license_text if len(license_text) <= 256 else "[full text retained in installed METADATA]",
            "licenseClassifiers": [str(item) for item in metadata.get_all("Classifier", [])
                                   if str(item).startswith("License ::")],
            "declaredLicenseFiles": [str(item) for item in metadata.get_all("License-File", [])],
            "requiresDist": [str(item) for item in metadata.get_all("Requires-Dist", [])],
            "verifiedSha256Files": 0, "verifiedBytes": 0, "expectedUnhashedFiles": 0,
            "otherUnhashedFiles": 0, "licenseFiles": [],
        }
        record = directory / "RECORD"
        if not record.exists():
            issue(name, directory.name, "missing-record")
            packages.append(package)
            continue
        rows = csv.reader(io.StringIO(_read_file(record, root, budget, maximum=16 * 1024**2).decode("utf-8")), strict=True)
        seen_paths: set[Path] = set()
        verified_paths: set[Path] = set()
        expected_unhashed: set[Path] = set()
        other_unhashed: set[Path] = set()
        for row in rows:
            budget.check(records=1)
            if len(row) != 3 or not row[0] or "\x00" in row[0]:
                issue(name, directory.name, "invalid-record-row")
                continue
            relative, encoded, declared_size = row
            path = Path(os.path.abspath(site / relative.replace("\\", "/")))
            if not path.is_relative_to(root):
                issue(name, "[outside runtime]", "record-escape")
                continue
            display = path.relative_to(root).as_posix()
            if path in seen_paths:
                issue(name, display, "duplicate-record-path")
                # Some upstream wheels repeat an unhashed row before a hashed
                # one. Retain that malformed-manifest finding while still
                # checking every digest; ignoring duplicates could hide either
                # a valid coverage proof or a conflicting second hash.
            seen_paths.add(path)
            recorded.add(path)
            if not encoded:
                expected = path.suffix == ".pyc" or path == record
                (expected_unhashed if expected else other_unhashed).add(path)
                continue
            if not re.fullmatch(r"sha256=[A-Za-z0-9_-]{43}", encoded):
                issue(name, display, "unsupported-or-invalid-record-hash")
                continue
            try:
                digest, size = _hash_file(path, root, budget)
            except (OSError, ImageRuntimeAuditError) as error:
                if isinstance(error, _AuditLimitError):
                    raise
                issue(name, display, "missing-unsafe-or-changing-file")
                continue
            actual = base64.urlsafe_b64encode(bytes.fromhex(digest)).rstrip(b"=").decode("ascii")
            if actual != encoded[7:]:
                issue(name, display, "sha256-mismatch")
                continue
            if declared_size and (not declared_size.isascii() or not declared_size.isdecimal() or int(declared_size) != size):
                issue(name, display, "record-size-mismatch")
                continue
            if path in verified_paths:
                continue
            verified_paths.add(path)
            package["verifiedSha256Files"] += 1
            package["verifiedBytes"] += size
            if ("/licenses/" in display.lower() or path.name.lower().startswith(("license", "copying", "notice"))) and directory.name in display:
                package["licenseFiles"].append({"path": display, "sha256": digest})
        package["expectedUnhashedFiles"] = len(expected_unhashed - verified_paths)
        package["otherUnhashedFiles"] = len(other_unhashed - verified_paths)
        for path in sorted(other_unhashed - verified_paths):
            issue(name, path.relative_to(root).as_posix(), "unhashed-installed-file")
        packages.append(package)

    unrecorded: list[str] = []
    unrecorded_count = 0
    for folder, directories, files in os.walk(site, followlinks=False):
        budget.check(records=len(directories) + len(files))
        for name in tuple(directories):
            candidate = Path(folder) / name
            value = candidate.lstat()
            if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
                directories.remove(name)
                issue("[inventory]", candidate.relative_to(root).as_posix(), "unscanned-linked-directory")
        for name in files:
            path = Path(folder) / name
            if path.suffix != ".pyc" and path not in recorded:
                unrecorded_count += 1
                if len(unrecorded) < 128:
                    unrecorded.append(path.relative_to(root).as_posix())
    budget.verify_directories()
    return {"packages": packages, "distributionCount": len(packages),
            "verifiedSha256Files": sum(item["verifiedSha256Files"] for item in packages),
            "verifiedBytes": sum(item["verifiedBytes"] for item in packages),
            "expectedUnhashedFiles": sum(item["expectedUnhashedFiles"] for item in packages),
            "otherUnhashedFiles": sum(item["otherUnhashedFiles"] for item in packages),
            "issueCount": issue_count, "issues": issues, "issuesTruncated": issue_count > len(issues),
            "issueCountsByReason": reason_counts, "issueCountsByPackage": package_counts,
            "unrecordedFileCount": unrecorded_count, "unrecordedFiles": sorted(unrecorded),
            "unrecordedFilesTruncated": unrecorded_count > len(unrecorded)}


def _run_command(arguments: list[str], *, timeout: float = 90.0,
                 output_limit: int = 16 * 1024**2) -> tuple[int, bytes, bytes]:
    # Separate bounded drains avoid both pipe backpressure deadlocks and an
    # unbounded capture when installed metadata contains a giant description.
    process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    buffers = [bytearray(), bytearray()]
    overflow = Event()

    def drain(stream: BinaryIO | None, destination: bytearray) -> None:
        if stream is None:
            return
        try:
            while chunk := stream.read(64 * 1024):
                if len(destination) + len(chunk) > output_limit:
                    overflow.set()
                    try:
                        process.kill()
                    except OSError:
                        pass
                    break
                destination.extend(chunk)
        finally:
            stream.close()

    readers = [Thread(target=drain, args=(stream, buffers[index]), daemon=True)
               for index, stream in enumerate((process.stdout, process.stderr))]
    for reader in readers:
        reader.start()
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        raise ImageRuntimeAuditError("An audit subprocess exceeded its deadline.") from None
    finally:
        for reader in readers:
            reader.join(5)
    if overflow.is_set() or any(reader.is_alive() for reader in readers):
        raise ImageRuntimeAuditError("An audit subprocess exceeded its output budget.")
    return code, bytes(buffers[0]), bytes(buffers[1])


def _osv_request(body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request("https://api.osv.dev/v1/querybatch",
                                     json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = response.read(8 * 1024**2 + 1)
    if len(payload) > 8 * 1024**2:
        raise ImageRuntimeAuditError("The advisory response exceeds its budget.")
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise ImageRuntimeAuditError("The advisory service returned an invalid response.")
    return result


def query_osv(packages: list[dict[str, Any]], *,
              request: Callable[[dict[str, Any]], dict[str, Any]] = _osv_request) -> dict[str, Any]:
    """Query exact installed PyPI versions, retaining pagination and offline gaps.

    Local wheel versions also query their public version, since CUDA build tags
    are not separate upstream Python releases. An incomplete request never
    becomes an assertion that the environment has no known vulnerabilities.
    """

    deadline = monotonic() + 120.0
    identities = [(item["name"], item["version"], version) for item in packages
                  for version in sorted({item["version"], item["version"].split("+", 1)[0]})]
    findings: dict[tuple[str, str], set[str]] = {}
    advisory_count = 0
    completed = 0
    try:
        for start in range(0, len(identities), 32):
            batch = identities[start:start + 32]
            pending = [(identity, {"package": {"ecosystem": "PyPI", "name": identity[0]},
                                   "version": identity[2]}) for identity in batch]
            for _ in range(8):
                if monotonic() > deadline:
                    raise ImageRuntimeAuditError("Advisory lookup exceeded its deadline.")
                result = request({"queries": [query for _, query in pending]})
                results = result.get("results")
                if not isinstance(results, list) or len(results) != len(pending):
                    raise ImageRuntimeAuditError("Advisory response count does not match the query.")
                following = []
                for (identity, query), entry in zip(pending, results, strict=True):
                    if not isinstance(entry, dict) or not isinstance(entry.get("vulns", []), list):
                        raise ImageRuntimeAuditError("Advisory result is invalid.")
                    for advisory in entry.get("vulns", []):
                        identifier = advisory.get("id") if isinstance(advisory, dict) else None
                        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", identifier):
                            raise ImageRuntimeAuditError("Advisory identity is invalid.")
                        seen = findings.setdefault(identity[:2], set())
                        if identifier not in seen:
                            advisory_count += 1
                            if advisory_count > 10_000:
                                raise ImageRuntimeAuditError("Advisory count exceeds its bound.")
                            seen.add(identifier)
                    token = entry.get("next_page_token")
                    if token:
                        if not isinstance(token, str) or len(token) > 4096:
                            raise ImageRuntimeAuditError("Advisory pagination is invalid.")
                        following.append((identity, {**query, "page_token": token}))
                    else:
                        completed += 1
                pending = following
                if not pending:
                    break
            if pending:
                raise ImageRuntimeAuditError("Advisory pagination exceeded its bound.")
        status = "complete"
    except Exception:
        status = "incomplete"
    return {"provider": "https://api.osv.dev/v1/querybatch", "status": status,
            "requestedQueries": len(identities), "completedQueries": completed,
            "affectedPackages": [{"name": name, "version": version,
                                  "advisories": [{"id": item, "url": f"https://osv.dev/vulnerability/{item}"}
                                                 for item in sorted(advisories)]}
                                 for (name, version), advisories in sorted(findings.items())]}


def audit_provenance(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Match local artifact bytes and ComfyUI revision to reviewed public pins."""

    root = Path(os.path.abspath(root))
    budget = _Budget(AuditLimits(), monotonic() + 1200)
    artifacts = []
    entries = manifest.get("artifacts")
    if not isinstance(entries, list) or len(entries) > 32:
        raise ImageRuntimeAuditError("The provenance manifest is invalid.")
    for entry in entries:
        if not isinstance(entry, dict) or not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", "")):
            raise ImageRuntimeAuditError("A provenance digest is invalid.")
        digest, size = _hash_file(root / entry["relativePath"], root, budget)
        artifacts.append({**entry, "actualSha256": digest, "bytes": size, "matches": digest == entry["sha256"]})
    comfy = root / manifest["comfyRelativeRoot"]
    _checked_path(comfy / "main.py", root)
    code, revision, _ = _run_command(["git", "-C", str(comfy), "rev-parse", "HEAD"])
    status_code, status, _ = _run_command(["git", "-C", str(comfy), "status", "--porcelain", "--untracked-files=no"])
    budget.verify_directories()
    return {"artifacts": artifacts, "expectedComfyRevision": manifest["comfyRevision"],
            "actualComfyRevision": revision.decode("ascii").strip(),
            "revisionMatches": code == 0 and revision.decode("ascii").strip() == manifest["comfyRevision"],
            "trackedCheckoutClean": status_code == 0 and not status.strip()}


def main() -> int:
    """Write a dated audit report and return failure for findings or incomplete checks."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--acceptance-root", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--query-osv", action="store_true")
    arguments = parser.parse_args()
    try:
        code, payload, _ = _run_command([str(arguments.python), "-X", "utf8", "-I", "-c",
                                       "import json,sys,sysconfig; print(json.dumps({'version':sys.version,'site':sysconfig.get_path('purelib')}))"])
        if code:
            raise ImageRuntimeAuditError("The target interpreter cannot report its metadata directory.")
        interpreter = json.loads(payload)
        package_root = Path(interpreter["site"])
        runtime_root = arguments.python.absolute().parent
        # Embedded Python otherwise inherits the Windows ANSI output codec;
        # non-ASCII package descriptions would truncate pip's JSON inventory.
        check_code, check, _ = _run_command([str(arguments.python), "-X", "utf8", "-I", "-m", "pip", "check"])
        inspect_code, inspect_data, _ = _run_command([str(arguments.python), "-X", "utf8", "-I", "-m", "pip", "inspect", "--local"])
        if inspect_code:
            raise ImageRuntimeAuditError("pip inspect failed for the target runtime.")
        inspected = json.loads(inspect_data)
        print("Verifying installed distribution metadata and RECORD files...", file=sys.stderr, flush=True)
        inventory = audit_site_packages(package_root, runtime_root)
        local_versions = {_canonical(item["name"]): item["version"] for item in inventory["packages"]}
        pip_versions = {_canonical(item["metadata"]["name"]): item["metadata"]["version"]
                        for item in inspected["installed"]}
        with arguments.provenance.open("rb") as manifest_file:
            manifest_bytes = manifest_file.read(1024**2 + 1)
        if len(manifest_bytes) > 1024**2:
            raise ImageRuntimeAuditError("The provenance manifest is too large.")
        print("Verifying pinned runtime, checkpoint and license artifacts...", file=sys.stderr, flush=True)
        provenance = audit_provenance(arguments.acceptance_root, json.loads(manifest_bytes))
        if arguments.query_osv:
            print("Querying dated PyPI package advisories from OSV...", file=sys.stderr, flush=True)
        advisory = query_osv(inventory["packages"]) if arguments.query_osv else {"status": "not-requested"}
        report = {"schemaVersion": 1, "generatedAt": datetime.now(UTC).isoformat(),
                  "python": interpreter["version"], "pipVersion": inspected.get("pip_version"),
                  "pipCheck": {"passed": check_code == 0, "output": check.decode("utf-8").strip()},
                  "pipInspectMatchesInventory": local_versions == pip_versions,
                  "inventory": inventory, "provenance": provenance, "advisories": advisory,
                  "scope": ["No CUDA or ComfyUI imports or launches; no dependency mutation.",
                            "RECORD is locally supplied consistency evidence, not a signed upstream manifest.",
                            "The read-only audit expects a quiescent trusted install; it is not an atomic filesystem snapshot against hostile concurrent swaps.",
                            "Unhashed bytecode, bundled native libraries/CPython, unrecorded files and live-source imports need separate review.",
                            "License declarations are inventoried, not legal clearance for redistribution.",
                            "OSV is a dated PyPI advisory lookup, not exhaustive vulnerability or native-binary coverage."]}
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=arguments.output.parent, delete=False) as output:
                temporary = Path(output.name)
                json.dump(report, output, indent=2, ensure_ascii=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, arguments.output)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        print(json.dumps({"distributions": inventory["distributionCount"],
                          "verifiedFiles": inventory["verifiedSha256Files"], "verifiedBytes": inventory["verifiedBytes"],
                          "integrityIssues": inventory["issueCount"], "unrecordedFiles": inventory["unrecordedFileCount"],
                          "pipCheck": check_code == 0, "advisories": advisory["status"],
                          "affectedPackages": len(advisory.get("affectedPackages", []))}))
        complete = (check_code == 0 and local_versions == pip_versions and not inventory["issueCount"]
                    and not inventory["unrecordedFileCount"] and provenance["revisionMatches"]
                    and provenance["trackedCheckoutClean"] and all(item["matches"] for item in provenance["artifacts"])
                    and advisory["status"] == "complete" and not advisory.get("affectedPackages"))
        return 0 if complete else 1
    except (OSError, ValueError, KeyError, csv.Error, ImageRuntimeAuditError) as error:
        detail = str(error) if isinstance(error, ImageRuntimeAuditError) else type(error).__name__
        print(f"The image runtime audit could not complete: {detail}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
