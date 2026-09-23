"""Reject local model, voice, runtime-data, and unsafe packaging inclusions.

The repository intentionally keeps model weights, reference recordings, runtime
caches, and user data outside Git and desktop packages.  Ignore rules alone do
not enforce that boundary because files can be force-added or copied into an
otherwise allowed build directory.  This checker therefore audits the Git
index and the Electron Builder allowlist, with optional scans for an unpacked
application tree and a text listing produced by ``asar list``.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Final
import unicodedata


_EXPECTED_BUILDER_FILES: Final = (
    "dist/**/*",
    "dist-electron/**/*",
    "package.json",
)
_EXPECTED_BUILDER_CONFIGURATION: Final[dict[str, object]] = {
    "appId": "ai.elysia.desktop",
    "productName": "Elysia",
    "asar": True,
    "directories": {
        "output": "out",
        "buildResources": "assets",
    },
    "files": list(_EXPECTED_BUILDER_FILES),
    "win": {
        "target": "nsis",
        "icon": "elysia-icon.ico",
        "artifactName": "Elysia-Setup-${version}.${ext}",
    },
}
_FORBIDDEN_BUILDER_KEYS: Final = frozenset(
    {"extraResources", "extraFiles", "asarUnpack", "extends"}
)
_FORBIDDEN_MODEL_SUFFIXES: Final = frozenset(
    {
        ".bin",
        ".caffemodel",
        ".ckpt",
        ".gguf",
        ".h5",
        ".hdf5",
        ".mlmodel",
        ".npy",
        ".npz",
        ".onnx",
        ".pb",
        ".pt",
        ".pth",
        ".safetensors",
        ".tflite",
    }
)
_FORBIDDEN_AUDIO_SUFFIXES: Final = frozenset(
    {
        ".aac",
        ".flac",
        ".m4a",
        ".mp3",
        ".ogg",
        ".opus",
        ".wav",
        ".webm",
        ".wma",
    }
)
_FORBIDDEN_ARCHIVE_SUFFIXES: Final = (
    ".7z",
    ".bz2",
    ".gz",
    ".rar",
    ".tar",
    ".tar.bz2",
    ".tar.gz",
    ".tar.xz",
    ".tar.zst",
    ".tgz",
    ".txz",
    ".xz",
    ".zip",
    ".zst",
)
_FORBIDDEN_MODEL_SUBDIRECTORIES: Final = frozenset(
    {"blobs", "cache", "manifests", "metadata", "weights"}
)
_FORBIDDEN_COMPONENTS: Final = frozenset({"logs", "workspace"})
_FORBIDDEN_FILE_NAMES: Final = frozenset({".env", "voice-profiles.json"})
_FORBIDDEN_RUNTIME_COMPONENTS: Final = frozenset(
    {
        "gpt-sovits-v2-240821",
        "gpt_weights_v2",
        "sovits_weights_v2",
        "参考音频",
    }
)
_ALLOWED_UNPACKED_BIN_PATHS: Final = frozenset(
    {"snapshot_blob.bin", "v8_context_snapshot.bin"}
)
_MAX_JSON_BYTES: Final = 1024 * 1024
_MAX_ASAR_LISTING_BYTES: Final = 16 * 1024 * 1024
_MAX_ASAR_ENTRIES: Final = 200_000
_MAX_PATH_CODE_POINTS: Final = 4096


@dataclass(frozen=True, slots=True)
class DistributionProblem:
    """Describe one path or packaging rule that violates distribution policy."""

    source: str
    path: str
    message: str

    def render(self) -> str:
        """Return one stable human-readable diagnostic without local absolutes."""

        location = f"{self.source}:{self.path}" if self.path else self.source
        return f"{location}: {self.message}"


class DistributionAuditError(Exception):
    """Report that an audit input could not be read or interpreted safely."""


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON keys instead of silently keeping the last value."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DistributionAuditError("Desktop package JSON has duplicate keys.")
        result[key] = value
    return result


def _normalize_component(component: str) -> str:
    """Return a compatibility-normalized key for alias-resistant comparisons."""

    # NFKC catches full-width and compatibility spellings that can visually
    # disguise a forbidden asset directory.  Trailing dots and spaces are also
    # aliases on Windows and therefore cannot weaken a distribution check.
    return unicodedata.normalize("NFKC", component).rstrip(" .").casefold()


def _normalize_distribution_path(raw_path: str) -> tuple[str, ...]:
    """Return safe normalized relative components for Git or archive paths."""

    if not isinstance(raw_path, str) or not raw_path:
        raise DistributionAuditError("Distribution path is empty.")
    if len(raw_path) > _MAX_PATH_CODE_POINTS:
        raise DistributionAuditError("Distribution path exceeds the safe limit.")
    if any(ord(character) < 32 or ord(character) == 127 for character in raw_path):
        raise DistributionAuditError("Distribution path contains control characters.")

    portable = unicodedata.normalize("NFKC", raw_path).replace("\\", "/")
    if any(ord(character) < 32 or ord(character) == 127 for character in portable):
        raise DistributionAuditError("Distribution path contains control characters.")
    while portable.startswith("./"):
        portable = portable[2:]
    # ``asar list`` renders root-relative entries with a leading slash.  Git
    # paths and unpacked-tree paths do not, so one leading separator is the only
    # absolute-looking form accepted here.
    portable = portable.lstrip("/")
    raw_components = portable.split("/")
    if not raw_components or any(
        component in ("", ".", "..") for component in raw_components
    ):
        raise DistributionAuditError("Distribution path is not canonical.")
    if any(":" in component or "\x00" in component for component in raw_components):
        raise DistributionAuditError("Distribution path contains an unsafe alias.")

    components = tuple(_normalize_component(component) for component in raw_components)
    if any(not component for component in components):
        raise DistributionAuditError("Distribution path has an empty Windows alias.")
    return components


def _contains_pair(
    components: Sequence[str],
    first: str,
    seconds: frozenset[str],
) -> bool:
    """Return whether adjacent normalized components match a forbidden pair."""

    return any(
        components[index] == first and components[index + 1] in seconds
        for index in range(len(components) - 1)
    )


def _path_policy_message(
    raw_path: str,
    *,
    allow_unpacked_runtime_bins: bool,
) -> str | None:
    """Return the first policy violation for one distribution-relative path."""

    components = _normalize_distribution_path(raw_path)
    normalized_path = "/".join(components)
    file_name = components[-1]

    if file_name in _FORBIDDEN_FILE_NAMES or file_name.startswith(".env."):
        return "private environment or local voice configuration is forbidden"
    if any(component in _FORBIDDEN_COMPONENTS for component in components):
        return "runtime user data or logs are forbidden"
    if _contains_pair(components, "models", _FORBIDDEN_MODEL_SUBDIRECTORIES):
        return "local model, cache, manifest, or metadata paths are forbidden"
    if any(component in _FORBIDDEN_RUNTIME_COMPONENTS for component in components):
        return "known local voice runtime or asset-pack paths are forbidden"

    suffix = PurePosixPath(file_name).suffix.casefold()
    if suffix in _FORBIDDEN_MODEL_SUFFIXES:
        if allow_unpacked_runtime_bins and normalized_path in _ALLOWED_UNPACKED_BIN_PATHS:
            return None
        return "model-weight file extension is forbidden"
    if suffix in _FORBIDDEN_AUDIO_SUFFIXES:
        return "audio asset file extension is forbidden"
    if any(file_name.endswith(suffix) for suffix in _FORBIDDEN_ARCHIVE_SUFFIXES):
        return "archive files are forbidden because they can conceal local assets"
    return None


def audit_distribution_paths(
    paths: Iterable[str],
    *,
    source: str,
    allow_unpacked_runtime_bins: bool = False,
) -> tuple[DistributionProblem, ...]:
    """Audit caller-supplied relative paths using the distribution deny policy."""

    problems: list[DistributionProblem] = []
    for raw_path in paths:
        try:
            message = _path_policy_message(
                raw_path,
                allow_unpacked_runtime_bins=allow_unpacked_runtime_bins,
            )
        except DistributionAuditError as error:
            problems.append(
                DistributionProblem(source, "<invalid-path>", str(error))
            )
            continue
        if message is not None:
            problems.append(DistributionProblem(source, raw_path, message))
    return tuple(problems)


def audit_git_index(repository_root: Path) -> tuple[DistributionProblem, ...]:
    """Audit every path in the repository's current Git index."""

    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=repository_root,
            check=False,
            capture_output=True,
        )
    except OSError as error:
        raise DistributionAuditError("Git index could not be inspected.") from error
    if completed.returncode != 0:
        raise DistributionAuditError("Git index could not be inspected.")

    decoded_paths: list[str] = []
    for encoded_path in completed.stdout.split(b"\0"):
        if not encoded_path:
            continue
        try:
            decoded_paths.append(encoded_path.decode("utf-8"))
        except UnicodeDecodeError:
            return (
                DistributionProblem(
                    "git-index",
                    "<non-utf8-path>",
                    "Git path is not valid UTF-8 and cannot be distributed safely",
                ),
            )
    return audit_distribution_paths(decoded_paths, source="git-index")


def _load_package_json(package_path: Path) -> Mapping[str, object]:
    """Load bounded strict JSON for the Electron package declaration."""

    try:
        if not package_path.is_file() or package_path.stat().st_size > _MAX_JSON_BYTES:
            raise OSError("package JSON is unavailable or oversized")
        document: object = json.loads(
            package_path.read_text(encoding="utf-8-sig"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except DistributionAuditError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DistributionAuditError("Desktop package JSON is invalid.") from error
    if not isinstance(document, dict) or not all(
        isinstance(key, str) for key in document
    ):
        raise DistributionAuditError("Desktop package JSON root must be an object.")
    return document


def _reject_json_constant(_constant: str) -> object:
    """Reject non-standard NaN and Infinity values in the package manifest."""

    raise DistributionAuditError("Desktop package JSON is not strict JSON.")


def _find_forbidden_builder_keys(
    value: object,
    *,
    path: str = "build",
) -> Iterable[tuple[str, str]]:
    """Yield forbidden Electron Builder keys even when nested in target blocks."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                continue
            child_path = f"{path}.{key}"
            if key in _FORBIDDEN_BUILDER_KEYS:
                yield child_path, key
            yield from _find_forbidden_builder_keys(child, path=child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _find_forbidden_builder_keys(
                child,
                path=f"{path}[{index}]",
            )


def audit_desktop_builder(package_path: Path) -> tuple[DistributionProblem, ...]:
    """Require the exact reviewed Electron Builder configuration.

    Electron Builder merges inherited, platform-specific, hook, and installer
    configuration. Checking only the root ``files`` array would therefore let
    another key add or mutate package content after this audit. Freezing the
    complete small configuration makes every new packaging capability require
    an explicit policy review before CI accepts it.
    """

    package = _load_package_json(package_path)
    build = package.get("build")
    if not isinstance(build, dict):
        return (
            DistributionProblem(
                "desktop-builder",
                "build",
                "Electron Builder configuration must be an object",
            ),
        )

    problems: list[DistributionProblem] = []
    if build != _EXPECTED_BUILDER_CONFIGURATION:
        problems.append(
            DistributionProblem(
                "desktop-builder",
                "build",
                "configuration must exactly match the reviewed packaging policy",
            )
        )
    files = build.get("files")
    if not isinstance(files, list) or tuple(files) != _EXPECTED_BUILDER_FILES:
        problems.append(
            DistributionProblem(
                "desktop-builder",
                "build.files",
                "file allowlist must be exactly dist/**/*, dist-electron/**/*, package.json",
            )
        )
    if build.get("asar") is not True:
        problems.append(
            DistributionProblem(
                "desktop-builder",
                "build.asar",
                "ASAR packaging must remain explicitly enabled",
            )
        )
    for key_path, key in _find_forbidden_builder_keys(build):
        problems.append(
            DistributionProblem(
                "desktop-builder",
                key_path,
                f"{key} can bypass the desktop package allowlist",
            )
        )
    return tuple(problems)


def audit_unpacked_tree(unpacked_root: Path) -> tuple[DistributionProblem, ...]:
    """Audit an optional unpacked application tree without following links."""

    if not unpacked_root.is_dir():
        raise DistributionAuditError("Unpacked application tree is unavailable.")

    problems: list[DistributionProblem] = []
    relative_paths: list[str] = []
    for directory, child_directories, file_names in os.walk(
        unpacked_root,
        topdown=True,
        followlinks=False,
    ):
        directory_path = Path(directory)
        retained_children: list[str] = []
        for child_name in child_directories:
            child = directory_path / child_name
            relative = child.relative_to(unpacked_root).as_posix()
            if child.is_symlink() or (
                hasattr(os.path, "isjunction") and os.path.isjunction(child)
            ):
                problems.append(
                    DistributionProblem(
                        "unpacked-tree",
                        relative,
                        "links and junctions are forbidden in distribution trees",
                    )
                )
            else:
                retained_children.append(child_name)
                relative_paths.append(relative)
        child_directories[:] = retained_children
        for file_name in file_names:
            child = directory_path / file_name
            relative = child.relative_to(unpacked_root).as_posix()
            if child.is_symlink():
                problems.append(
                    DistributionProblem(
                        "unpacked-tree",
                        relative,
                        "links are forbidden in distribution trees",
                    )
                )
            else:
                relative_paths.append(relative)
    problems.extend(
        audit_distribution_paths(
            relative_paths,
            source="unpacked-tree",
            allow_unpacked_runtime_bins=True,
        )
    )
    return tuple(problems)


def audit_asar_listing(listing_path: Path) -> tuple[DistributionProblem, ...]:
    """Audit a bounded UTF-8 text listing previously produced by ``asar list``."""

    try:
        if not listing_path.is_file() or listing_path.stat().st_size > _MAX_ASAR_LISTING_BYTES:
            raise OSError("ASAR listing is unavailable or oversized")
        lines = listing_path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as error:
        raise DistributionAuditError("ASAR listing could not be read safely.") from error
    if len(lines) > _MAX_ASAR_ENTRIES:
        raise DistributionAuditError("ASAR listing contains too many entries.")
    paths = [line.strip() for line in lines if line.strip()]
    return audit_distribution_paths(paths, source="asar-listing")


def audit_repository(
    repository_root: Path,
    *,
    unpacked_root: Path | None = None,
    asar_listing: Path | None = None,
) -> tuple[DistributionProblem, ...]:
    """Run the required repository checks plus any requested artifact scans."""

    root = repository_root.resolve()
    problems = [
        *audit_git_index(root),
        *audit_desktop_builder(root / "desktop" / "package.json"),
    ]
    if unpacked_root is not None:
        problems.extend(audit_unpacked_tree(unpacked_root.resolve()))
    if asar_listing is not None:
        problems.extend(audit_asar_listing(asar_listing.resolve()))
    return tuple(problems)


def _parse_arguments(arguments: Sequence[str] | None) -> argparse.Namespace:
    """Parse command-line inputs for local and CI distribution checks."""

    parser = argparse.ArgumentParser(
        description="Check that local model and voice assets cannot be distributed.",
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root to inspect (defaults to this script's repository).",
    )
    parser.add_argument(
        "--unpacked-tree",
        type=Path,
        help="Optional unpacked Electron application directory to inspect.",
    )
    parser.add_argument(
        "--asar-listing",
        type=Path,
        help="Optional UTF-8 output captured from `asar list`.",
    )
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the distribution audit and return a process status for CI."""

    options = _parse_arguments(arguments)
    try:
        problems = audit_repository(
            options.repository_root,
            unpacked_root=options.unpacked_tree,
            asar_listing=options.asar_listing,
        )
    except DistributionAuditError as error:
        print(f"Distribution asset check failed: {error}")
        return 1

    if problems:
        for problem in problems:
            print(problem.render())
        print(f"Distribution asset check failed: {len(problems)} problem(s).")
        return 1

    optional_count = int(options.unpacked_tree is not None) + int(
        options.asar_listing is not None
    )
    print(
        "Distribution asset check passed: Git index, Electron Builder, "
        f"and {optional_count} optional artifact input(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
