"""Build the isolated Windows Python runtime consumed by Electron Builder.

Only explicit source roots and runtime wheels enter the staging directory.
The official embeddable interpreter is digest-pinned and isolated from machine
Python installations, developer .env files, and per-user site packages. Runtime
data always belongs to Electron's separately managed writable data directory.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import sysconfig
from urllib.request import urlopen
from zipfile import ZipFile
from xml.etree import ElementTree


_ROOT = Path(__file__).resolve().parents[1]
_BUILD_ROOT = _ROOT / "desktop" / "build"
_STAGING = _BUILD_ROOT / "backend"


def _digest(file_path: Path) -> str:
    with file_path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _remove_build_directory(directory: Path) -> None:
    # Build cleanup must never follow a developer-created junction into a user
    # data root. Resolve both endpoints before each recursive deletion.
    if not directory.exists():
        return
    if directory.is_symlink() or directory.is_junction():
        raise RuntimeError(f"Refusing linked build directory: {directory}")
    directory.resolve().relative_to(_BUILD_ROOT.resolve())
    shutil.rmtree(directory)


def _install_cpp_runtime(runtime: Path, configuration: dict[str, object]) -> None:
    """Extract the two C++ runtime dependencies and license from Microsoft's pinned bundle."""
    archive = _BUILD_ROOT / "vc-runtime-verified.exe"
    if not archive.exists() or _digest(archive) != configuration["vcRuntimeSha256"]:
        with urlopen(str(configuration["vcRuntimeUrl"]), timeout=60) as response:
            archive.write_bytes(response.read(64 * 1024 * 1024))
    if _digest(archive) != configuration["vcRuntimeSha256"]:
        raise RuntimeError("Microsoft VC runtime SHA-256 does not match the reviewed pin.")
    extracted = _BUILD_ROOT / "vc-runtime-extracted"
    _remove_build_directory(extracted)
    extracted.mkdir()
    expand = Path(os.environ["SystemRoot"]) / "System32" / "expand.exe"
    content = archive.read_bytes()
    # WiX Burn stores bootstrap metadata and payloads in separate CABs. Extract
    # both from the already authenticated artifact without running its installer
    # or modifying machine-wide runtimes. The CAB length includes its header.
    offset = 0
    cabinets = 0
    while (offset := content.find(b"MSCF", offset)) != -1:
        length = struct.unpack_from("<I", content, offset + 8)[0]
        if 36 <= length <= len(content) - offset:
            cabinet = extracted / f"container-{cabinets}.cab"
            cabinet.write_bytes(content[offset:offset + length])
            subprocess.run([str(expand), "-F:*", str(cabinet), str(extracted)],
                           check=True, capture_output=True)
            cabinets += 1
        offset += 4
    payloads = [element.attrib for element in ElementTree.parse(extracted / "0").iter()
                if element.tag.endswith("Payload")]
    minimum = next(payload for payload in payloads
                   if payload.get("FilePath") == "packages\\vcRuntimeMinimum_amd64\\cab1.cab")
    subprocess.run([str(expand), "-F:*", str(extracted / minimum["SourcePath"]), str(extracted)],
                   check=True, capture_output=True)
    # CPython already ships matching VCRUNTIME DLLs. Native speech wheels also
    # need the C++ standard-library DLLs, which clean Windows does not guarantee.
    for name in ("msvcp140.dll", "msvcp140_1.dll"):
        shutil.copyfile(extracted / f"{name}_amd64", runtime / name)
    # Newer Microsoft bundles link their license instead of embedding an RTF.
    # Preserve the official license text from its separately pinned document.
    license_archive = _BUILD_ROOT / "vc-runtime-license.docx"
    if not license_archive.exists() or _digest(license_archive) != configuration["vcLicenseSha256"]:
        with urlopen(str(configuration["vcLicenseUrl"]), timeout=60) as response:
            license_archive.write_bytes(response.read(2 * 1024 * 1024))
    if _digest(license_archive) != configuration["vcLicenseSha256"]:
        raise RuntimeError("Microsoft VC license SHA-256 does not match the reviewed pin.")
    with ZipFile(license_archive) as document:
        license_xml = ElementTree.fromstring(document.read("word/document.xml"))
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = ["".join(node.text or "" for node in paragraph.findall(".//w:t", namespace))
                  for paragraph in license_xml.findall(".//w:p", namespace)]
    (runtime / "MICROSOFT-VC-RUNTIME-LICENSE.txt").write_text("\n\n".join(paragraphs) + "\n", encoding="utf-8")
    (runtime / "MICROSOFT-VC-RUNTIME-NOTICE.txt").write_text(
        f"Microsoft Visual C++ Redistributable {configuration['vcRuntimeVersion']} x64\n"
        f"Source: {configuration['vcRuntimeUrl']}\n"
        f"SHA-256: {configuration['vcRuntimeSha256']}\n"
        "Application-local msvcp140.dll and msvcp140_1.dll; the original license is included.\n",
        encoding="utf-8",
    )


def main() -> None:
    """Stage a redistributable x64 backend, failing closed on missing inputs."""
    if sysconfig.get_platform() != "win-amd64" or sys.version_info[:2] != (3, 14):
        raise RuntimeError("Windows x64 CPython 3.14 is required to build the backend.")
    configuration = json.loads((_ROOT / "desktop" / "backend-runtime.json").read_text("utf-8"))
    if _BUILD_ROOT.is_symlink() or _BUILD_ROOT.is_junction():
        raise RuntimeError("Refusing linked backend build root.")
    _BUILD_ROOT.resolve().relative_to(_ROOT.resolve())
    _BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    archive = _BUILD_ROOT / f"python-{configuration['pythonVersion']}-embed-amd64.zip"
    if not archive.exists() or _digest(archive) != configuration["pythonSha256"]:
        with urlopen(configuration["pythonArchive"], timeout=60) as response:
            archive.write_bytes(response.read(32 * 1024 * 1024))
    if _digest(archive) != configuration["pythonSha256"]:
        raise RuntimeError("Official Python archive SHA-256 does not match the reviewed pin.")

    _remove_build_directory(_STAGING)
    runtime = _STAGING / "python"
    runtime.mkdir(parents=True)
    with ZipFile(archive) as source:
        source.extractall(runtime)
    # _pth disables registry, PYTHONPATH and user-site discovery. Explicit
    # relative roots keep the runtime relocatable without enabling site hooks.
    (runtime / "python314._pth").write_text(
        "python314.zip\n.\nLib/site-packages\n..\n", encoding="utf-8"
    )
    _install_cpp_runtime(runtime, configuration)
    dependencies = runtime / "Lib" / "site-packages"
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
         "--no-compile", "--only-binary=:all:", "--target", str(dependencies),
         "--report", str(_BUILD_ROOT / "backend-dependencies.json"),
         "-r", str(_ROOT / "requirements-runtime.txt")],
        check=True,
    )
    # Wheel test fixtures contain sample media and model-like binary files.
    # They are not imported by production modules and do not belong in releases.
    for directory in sorted(dependencies.rglob("*"), reverse=True):
        if directory.is_dir() and directory.name in {"tests", "test", "__pycache__"}:
            _remove_build_directory(directory)
    _remove_build_directory(dependencies / "onnxruntime" / "datasets")
    # pip's CLI wrappers embed the builder machine's interpreter path. The app
    # imports these libraries directly, so shipping unusable wrappers adds no
    # capability and would leak the development installation layout.
    _remove_build_directory(dependencies / "bin")
    sources = set(configuration["sourceFiles"])
    # Model folders may contain ignored third-party runtimes. Consult Git's
    # source boundary so a local installation cannot enter the release merely
    # because it also contains Python files.
    indexed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.py"],
        cwd=_ROOT, check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout.splitlines()
    sources.update(relative for relative in indexed
                   if relative.split("/", 1)[0] in configuration["sourceDirectories"])
    for relative in sorted(sources):
        source = _ROOT / relative
        if source.is_symlink() or not source.is_file():
            raise RuntimeError(f"Missing or linked backend source: {relative}")
        target = _STAGING / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    shutil.copyfile(_ROOT / "requirements-runtime.txt", _STAGING / "requirements-runtime.txt")
    manifest = {
        "pythonVersion": configuration["pythonVersion"],
        "pythonArchiveSha256": configuration["pythonSha256"],
        "vcRuntimeVersion": configuration["vcRuntimeVersion"],
        "vcRuntimeArchiveSha256": configuration["vcRuntimeSha256"],
        "files": {
            file.relative_to(_STAGING).as_posix(): _digest(file)
            for file in sorted(_STAGING.rglob("*")) if file.is_file()
        },
    }
    (_STAGING / "distribution-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Staged {len(manifest['files'])} files at {_STAGING}")


if __name__ == "__main__":
    main()
