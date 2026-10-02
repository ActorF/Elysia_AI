"""Reject unsafe packaging inclusions and verify reviewed public assets.

The repository intentionally keeps model weights, reference recordings, runtime
caches, and user data outside Git and desktop packages.  Ignore rules alone do
not enforce that boundary because files can be force-added or copied into an
otherwise allowed build directory.  This checker therefore audits the Git
index and the Electron Builder allowlist, with optional scans for an unpacked
application tree and a text listing produced by ``asar list``.

The small set of approved third-party or generated public assets is pinned by
repository-relative path, byte length, and SHA-256.  That positive allowlist
prevents an asset replacement from silently inheriting an earlier rights and
distribution review.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
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
# Cubism runtime and editor files carry compound suffixes that ordinary
# ``Path.suffix`` checks reduce to ``.json``.  Matching the complete normalized
# filename ending makes the boundary independent of a paid pack's directory
# name or an expected ``character/live2d`` destination.  Generic textures are
# covered when they remain beside a known pack, while every usable copied model
# necessarily retains at least its manifest or compiled model file.
_FORBIDDEN_LIVE2D_MODEL_SUFFIXES: Final = (
    ".can3",
    ".cdi3.json",
    ".cmo3",
    ".cmp3",
    ".exp3.json",
    ".moc3",
    ".model3.json",
    ".motion3.json",
    ".physics3.json",
    ".pose3.json",
    ".userdata3.json",
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
_REVIEWED_VISUAL_ASSET_SUFFIXES: Final = frozenset(
    {
        ".avif",
        ".bmp",
        ".clip",
        ".dds",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".kra",
        ".ktx",
        ".ktx2",
        ".png",
        ".psb",
        ".psd",
        ".tga",
        ".webp",
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
_FORBIDDEN_PAID_ASSET_COMPONENTS: Final = frozenset(
    {"爱莉希雅原版猫猫版总合集_34be1"}
)
_BUILD_OUTPUT_COMPONENTS: Final = frozenset({"dist", "dist-electron"})
_FORBIDDEN_RETIRED_DESKTOP_MODULE_STEMS: Final = (
    "character-performance-contracts",
    "desktop-pet-main",
    "desktop-pet-model-library",
    "desktop-pet-preload",
    "live2d-assets",
    "live2d-runtime",
    "speech-mouth",
)
_LIVE2D_DIRECTORY_COMPONENTS: Final = frozenset({"live2d"})
_FORBIDDEN_RETIRED_LIVE2D_RUNTIME_FILE_NAMES: Final = frozenset(
    {"license-purismcore.txt", "purismcore.js"}
)
_ALLOWED_UNPACKED_BIN_PATHS: Final = frozenset(
    {"snapshot_blob.bin", "v8_context_snapshot.bin"}
)
_ALLOWED_GIT_VISUAL_ASSET_PATHS: Final = frozenset(
    {
        "data/characters/elysia-2dart/01-character-turnaround.png",
        "data/characters/elysia-2dart/02-activity-states.png",
        "data/characters/elysia-2dart/03-expression-atlas.png",
        "data/characters/elysia-2dart/04-facial-rig-atlas.png",
        "data/characters/elysia-2dart/05-chibi-stickers.png",
        "data/characters/elysia-2dart/05a-row1.png",
        "data/characters/elysia-2dart/05b-row2.png",
        "data/characters/elysia-2dart/05c-row3.png",
        "data/characters/elysia-2dart/05d-row4.png",
        "data/characters/elysia-2dart/05e-row5.png",
        "data/characters/elysia-2dart/06-ui-illustrations.png",
        "data/characters/elysia-2dart/07-desktop-pet-key-poses.png",
        "data/characters/elysia-2dart/07a-row1.png",
        "data/characters/elysia-2dart/07b-row2.png",
        "data/characters/elysia-2dart/07c-row3.png",
        "data/characters/elysia-2dart/07d-row4.png",
        "data/characters/elysia-2dart/08-layer-separation-guide.png",
        "data/characters/elysia-2dart/09-color-and-detail-master.png",
        "desktop/assets/elysia-icon.ico",
        "desktop/public/character/elysia-expression-atlas.png",
        "desktop/public/character/elysia-portrait.png",
        "desktop/public/character/elysia-speech-atlas.png",
        "desktop/public/character/elysia-state-atlas.png",
        "desktop/public/elysia-icon.png",
    }
)
_ALLOWED_ASAR_VISUAL_ASSET_PATHS: Final = frozenset(
    {
        "dist/character/elysia-expression-atlas.png",
        "dist/character/elysia-portrait.png",
        "dist/character/elysia-speech-atlas.png",
        "dist/character/elysia-state-atlas.png",
        "dist/elysia-icon.png",
    }
)
_ALLOWED_UNPACKED_VISUAL_ASSET_PATHS: Final[frozenset[str]] = frozenset()
_MAX_JSON_BYTES: Final = 1024 * 1024
_MAX_ASAR_LISTING_BYTES: Final = 16 * 1024 * 1024
_MAX_ASAR_ENTRIES: Final = 200_000
_MAX_PATH_CODE_POINTS: Final = 4096
_HASH_CHUNK_BYTES: Final = 1024 * 1024
_REVIEWED_PORTRAIT_SIZE: Final = 2_223_154
_REVIEWED_PORTRAIT_SHA256: Final = (
    "359c2620ac5286cc6c77d533e5c53d1b63fd0fe08fdf42f5952136b7c5bcafb2"
)
_REVIEWED_CHARACTER_ATLAS_SIZE: Final = 2_303_963
_REVIEWED_CHARACTER_ATLAS_SHA256: Final = (
    "54eb2525673c2a849819be10eb88eb2f670eb1911e86fd154e69b578cbb4c25c"
)
_REVIEWED_EXPRESSION_ATLAS_SIZE: Final = 2_500_647
_REVIEWED_EXPRESSION_ATLAS_SHA256: Final = (
    "fbf7a515b2651b3a881cf9b838a5605c316befd0b174dde046780d8e441d7f93"
)
_REVIEWED_SPEECH_ATLAS_SIZE: Final = 2_054_767
_REVIEWED_SPEECH_ATLAS_SHA256: Final = (
    "21bf4496acc4417d491ff0163c9ee1d38593e376ca25a3d452fd393c6157f9ab"
)
_REVIEWED_ICON_PNG_SIZE: Final = 241_299
_REVIEWED_ICON_PNG_SHA256: Final = (
    "4a2e248382700a03270172aa420835c1a7f1b92d82dc503dd0047a48f7cd8b01"
)
_REVIEWED_ICON_ICO_SIZE: Final = 113_389
_REVIEWED_ICON_ICO_SHA256: Final = (
    "c44d2db9282ea84f519d09be64400ce8ef0024fd20d18128b69b4a1b3692feb1"
)
_REVIEWED_ASAR_PORTRAIT_PATH: Final = "dist/character/elysia-portrait.png"
_REVIEWED_ASAR_CHARACTER_ATLAS_PATH: Final = (
    "dist/character/elysia-state-atlas.png"
)
_REVIEWED_ASAR_EXPRESSION_ATLAS_PATH: Final = (
    "dist/character/elysia-expression-atlas.png"
)
_REVIEWED_ASAR_SPEECH_ATLAS_PATH: Final = (
    "dist/character/elysia-speech-atlas.png"
)
_REVIEWED_ASAR_ICON_PATH: Final = "dist/elysia-icon.png"
_REVIEWED_DISTRIBUTION_ASSETS: Final[dict[str, tuple[int, str]]] = {
    "desktop/assets/elysia-icon.ico": (
        _REVIEWED_ICON_ICO_SIZE,
        _REVIEWED_ICON_ICO_SHA256,
    ),
    "desktop/public/elysia-icon.png": (
        _REVIEWED_ICON_PNG_SIZE,
        _REVIEWED_ICON_PNG_SHA256,
    ),
    "desktop/public/character/elysia-portrait.png": (
        _REVIEWED_PORTRAIT_SIZE,
        _REVIEWED_PORTRAIT_SHA256,
    ),
    "desktop/public/character/elysia-state-atlas.png": (
        _REVIEWED_CHARACTER_ATLAS_SIZE,
        _REVIEWED_CHARACTER_ATLAS_SHA256,
    ),
    "desktop/public/character/elysia-expression-atlas.png": (
        _REVIEWED_EXPRESSION_ATLAS_SIZE,
        _REVIEWED_EXPRESSION_ATLAS_SHA256,
    ),
    "desktop/public/character/elysia-speech-atlas.png": (
        _REVIEWED_SPEECH_ATLAS_SIZE,
        _REVIEWED_SPEECH_ATLAS_SHA256,
    ),
}
_REVIEWED_ASAR_ASSETS: Final[dict[str, tuple[int, str]]] = {
    _REVIEWED_ASAR_ICON_PATH: (
        _REVIEWED_ICON_PNG_SIZE,
        _REVIEWED_ICON_PNG_SHA256,
    ),
    _REVIEWED_ASAR_PORTRAIT_PATH: (
        _REVIEWED_PORTRAIT_SIZE,
        _REVIEWED_PORTRAIT_SHA256,
    ),
    _REVIEWED_ASAR_CHARACTER_ATLAS_PATH: (
        _REVIEWED_CHARACTER_ATLAS_SIZE,
        _REVIEWED_CHARACTER_ATLAS_SHA256,
    ),
    _REVIEWED_ASAR_EXPRESSION_ATLAS_PATH: (
        _REVIEWED_EXPRESSION_ATLAS_SIZE,
        _REVIEWED_EXPRESSION_ATLAS_SHA256,
    ),
    _REVIEWED_ASAR_SPEECH_ATLAS_PATH: (
        _REVIEWED_SPEECH_ATLAS_SIZE,
        _REVIEWED_SPEECH_ATLAS_SHA256,
    ),
}
_REQUIRED_ASAR_ENTRY_PATHS: Final = tuple(_REVIEWED_ASAR_ASSETS)


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
    allowed_visual_asset_paths: frozenset[str] | None,
) -> str | None:
    """Return the first policy violation for one distribution-relative path."""

    components = _normalize_distribution_path(raw_path)
    normalized_path = "/".join(components)
    file_name = components[-1]
    suffix = PurePosixPath(file_name).suffix.casefold()

    if file_name in _FORBIDDEN_FILE_NAMES or file_name.startswith(".env."):
        return "private environment or local voice configuration is forbidden"
    if any(component in _FORBIDDEN_COMPONENTS for component in components):
        return "runtime user data or logs are forbidden"
    if _contains_pair(components, "models", _FORBIDDEN_MODEL_SUBDIRECTORIES):
        return "local model, cache, manifest, or metadata paths are forbidden"
    if any(
        file_name.endswith(suffix)
        for suffix in _FORBIDDEN_LIVE2D_MODEL_SUFFIXES
    ):
        return "external Live2D model files are forbidden in distributions"
    if file_name in _FORBIDDEN_RETIRED_LIVE2D_RUNTIME_FILE_NAMES:
        return "the retired embedded Live2D runtime is forbidden"
    if any(component in _FORBIDDEN_RUNTIME_COMPONENTS for component in components):
        return "known local voice runtime or asset-pack paths are forbidden"
    if any(component in _FORBIDDEN_PAID_ASSET_COMPONENTS for component in components):
        return "the user-owned paid Live2D source directory must remain local"
    if (
        any(component in _BUILD_OUTPUT_COMPONENTS for component in components)
        and any(
            retired_stem in file_name
            for retired_stem in _FORBIDDEN_RETIRED_DESKTOP_MODULE_STEMS
        )
    ):
        # TypeScript does not remove outputs for deleted source files.  Reject
        # their emitted names in either build tree so stale modules cannot
        # survive an incremental build and enter the packaged ASAR.
        return "retired desktop module output is forbidden"
    if (
        _contains_pair(components, "character", _LIVE2D_DIRECTORY_COMPONENTS)
    ):
        # The companion is now the creator's complete external executable. No
        # model, texture, compatibility runtime, or old renderer asset belongs
        # in Git or a package, even if a paid folder is renamed before copy.
        return "bundled Live2D assets are forbidden; use the external program"

    if (
        allowed_visual_asset_paths is not None
        and suffix in _REVIEWED_VISUAL_ASSET_SUFFIXES
        and normalized_path not in allowed_visual_asset_paths
    ):
        # Texture filenames are generic, so suffix deny rules cannot
        # distinguish a paid model texture from ordinary artwork.  Exact path
        # admission makes every new visual asset an explicit rights review.
        return "unreviewed visual assets are forbidden at this boundary"
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
    allowed_visual_asset_paths: frozenset[str] | None = None,
) -> tuple[DistributionProblem, ...]:
    """Audit paths using deny rules and an optional visual-asset allowlist."""

    problems: list[DistributionProblem] = []
    for raw_path in paths:
        try:
            message = _path_policy_message(
                raw_path,
                allow_unpacked_runtime_bins=allow_unpacked_runtime_bins,
                allowed_visual_asset_paths=allowed_visual_asset_paths,
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
    return audit_distribution_paths(
        decoded_paths,
        source="git-index",
        allowed_visual_asset_paths=_ALLOWED_GIT_VISUAL_ASSET_PATHS,
    )


def _audit_exact_asset(
    asset_path: Path,
    *,
    source: str,
    logical_path: str,
    expected_size: int,
    expected_digest: str,
) -> tuple[DistributionProblem, ...]:
    """Authenticate one regular file with bounded reads and stable diagnostics.

    Size is checked before hashing so a malicious oversized replacement cannot
    make CI read an unbounded file.  The hash loop is capped at one byte beyond
    the reviewed size as defense against a concurrent append after that check.
    """

    try:
        if (
            asset_path.is_symlink()
            or (
                hasattr(os.path, "isjunction")
                and os.path.isjunction(asset_path)
            )
            or not asset_path.is_file()
        ):
            raise OSError("asset is not a regular file")
        reported_size = asset_path.stat().st_size
    except OSError:
        return (
            DistributionProblem(
                source,
                logical_path,
                "reviewed public asset is missing, linked, or unreadable",
            ),
        )

    if reported_size != expected_size:
        return (
            DistributionProblem(
                source,
                logical_path,
                f"byte length must remain exactly {expected_size}",
            ),
        )

    digest = hashlib.sha256()
    byte_count = 0
    try:
        with asset_path.open("rb") as asset_stream:
            remaining = expected_size + 1
            while remaining:
                chunk = asset_stream.read(min(_HASH_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                byte_count += len(chunk)
                remaining -= len(chunk)
                digest.update(chunk)
    except OSError:
        return (
            DistributionProblem(
                source,
                logical_path,
                "reviewed public asset became unreadable during verification",
            ),
        )

    if byte_count != expected_size:
        return (
            DistributionProblem(
                source,
                logical_path,
                f"byte length must remain exactly {expected_size}",
            ),
        )
    if digest.hexdigest() != expected_digest:
        return (
            DistributionProblem(
                source,
                logical_path,
                "SHA-256 does not match the reviewed public asset",
            ),
        )
    return ()


def audit_reviewed_assets(repository_root: Path) -> tuple[DistributionProblem, ...]:
    """Require every reviewed repository asset to retain its approved bytes.

    A generated or third-party image can remain at an innocuous ``.png`` path
    while its content changes completely.  The ordinary extension denylist
    cannot detect that replacement, so these exceptional distributable assets
    are authenticated separately by canonical path, length, and digest.
    """

    root = repository_root.resolve()
    problems: list[DistributionProblem] = []
    for relative_path, (expected_size, expected_digest) in (
        _REVIEWED_DISTRIBUTION_ASSETS.items()
    ):
        asset_path = root.joinpath(*PurePosixPath(relative_path).parts)
        problems.extend(
            _audit_exact_asset(
                asset_path,
                source="reviewed-asset",
                logical_path=relative_path,
                expected_size=expected_size,
                expected_digest=expected_digest,
            )
        )
    return tuple(problems)


def audit_extracted_asar_portrait(
    extracted_portrait: Path,
) -> tuple[DistributionProblem, ...]:
    """Authenticate the portrait bytes extracted from the packaged ASAR.

    The caller must extract the exact logical path named by
    ``_REVIEWED_ASAR_PORTRAIT_PATH`` from the just-built archive.  Pairing this
    check with ``audit_asar_listing`` proves both archive placement/cardinality
    and the bytes actually stored in that archive.
    """

    return _audit_exact_asset(
        extracted_portrait,
        source="asar-reviewed-asset",
        logical_path=_REVIEWED_ASAR_PORTRAIT_PATH,
        expected_size=_REVIEWED_PORTRAIT_SIZE,
        expected_digest=_REVIEWED_PORTRAIT_SHA256,
    )


def audit_extracted_asar_character_atlas(
    extracted_atlas: Path,
) -> tuple[DistributionProblem, ...]:
    """Authenticate the state-atlas bytes extracted from the packaged ASAR."""

    return _audit_exact_asset(
        extracted_atlas,
        source="asar-reviewed-asset",
        logical_path=_REVIEWED_ASAR_CHARACTER_ATLAS_PATH,
        expected_size=_REVIEWED_CHARACTER_ATLAS_SIZE,
        expected_digest=_REVIEWED_CHARACTER_ATLAS_SHA256,
    )


def audit_extracted_asar_expression_atlas(
    extracted_atlas: Path,
) -> tuple[DistributionProblem, ...]:
    """Authenticate the expression-atlas bytes extracted from the ASAR."""

    return _audit_exact_asset(
        extracted_atlas,
        source="asar-reviewed-asset",
        logical_path=_REVIEWED_ASAR_EXPRESSION_ATLAS_PATH,
        expected_size=_REVIEWED_EXPRESSION_ATLAS_SIZE,
        expected_digest=_REVIEWED_EXPRESSION_ATLAS_SHA256,
    )


def audit_extracted_asar_speech_atlas(
    extracted_atlas: Path,
) -> tuple[DistributionProblem, ...]:
    """Authenticate the speech-atlas bytes extracted from the packaged ASAR."""

    return _audit_exact_asset(
        extracted_atlas,
        source="asar-reviewed-asset",
        logical_path=_REVIEWED_ASAR_SPEECH_ATLAS_PATH,
        expected_size=_REVIEWED_SPEECH_ATLAS_SIZE,
        expected_digest=_REVIEWED_SPEECH_ATLAS_SHA256,
    )


def audit_extracted_asar_reviewed_assets(
    extracted_root: Path,
) -> tuple[DistributionProblem, ...]:
    """Authenticate every reviewed asset in one safely extracted ASAR tree.

    A whole-tree proof avoids adding a new command-line option whenever the
    reviewed character bundle grows.  The ASAR listing still proves exact
    archive cardinality; this function proves that the bytes stored at every
    required path match the repository review.
    """

    if not extracted_root.is_dir() or extracted_root.is_symlink():
        raise DistributionAuditError(
            "Extracted ASAR tree is unavailable or is a symbolic link."
        )
    problems: list[DistributionProblem] = []
    for relative_path, (expected_size, expected_digest) in (
        _REVIEWED_ASAR_ASSETS.items()
    ):
        asset_path = extracted_root.joinpath(*PurePosixPath(relative_path).parts)
        problems.extend(
            _audit_exact_asset(
                asset_path,
                source="asar-reviewed-asset",
                logical_path=relative_path,
                expected_size=expected_size,
                expected_digest=expected_digest,
            )
        )
    return tuple(problems)


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
            allowed_visual_asset_paths=_ALLOWED_UNPACKED_VISUAL_ASSET_PATHS,
        )
    )
    return tuple(problems)


def audit_asar_listing(listing_path: Path) -> tuple[DistributionProblem, ...]:
    """Audit a bounded ASAR listing and require critical entries exactly once."""

    try:
        if not listing_path.is_file() or listing_path.stat().st_size > _MAX_ASAR_LISTING_BYTES:
            raise OSError("ASAR listing is unavailable or oversized")
        lines = listing_path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as error:
        raise DistributionAuditError("ASAR listing could not be read safely.") from error
    if len(lines) > _MAX_ASAR_ENTRIES:
        raise DistributionAuditError("ASAR listing contains too many entries.")
    paths = [line.strip() for line in lines if line.strip()]
    problems = list(
        audit_distribution_paths(
            paths,
            source="asar-listing",
            allowed_visual_asset_paths=_ALLOWED_ASAR_VISUAL_ASSET_PATHS,
        )
    )

    # The listing is the archive's source of truth for placement and
    # cardinality.  Slash direction and one archive-root marker vary by host,
    # but case and Unicode spelling remain exact so aliases cannot satisfy the
    # reviewed path contract.
    for expected_path in _REQUIRED_ASAR_ENTRY_PATHS:
        expected_components = _normalize_distribution_path(expected_path)
        exact_count = 0
        alias_count = 0
        for path in paths:
            portable_path = path.replace("\\", "/")
            if portable_path.startswith("/"):
                portable_path = portable_path[1:]
            if portable_path == expected_path:
                exact_count += 1
            try:
                if _normalize_distribution_path(path) == expected_components:
                    alias_count += 1
            except DistributionAuditError:
                # The generic path audit already reports malformed entries;
                # they cannot count toward a reviewed-asset contract.
                continue
        if exact_count != 1 or alias_count != 1:
            problems.append(
                DistributionProblem(
                    "asar-listing",
                    expected_path,
                    "required package entry must appear exactly once in the ASAR "
                    "listing without case or Unicode aliases "
                    f"(found {exact_count} exact, "
                    f"{alias_count} normalized)",
                )
            )
    return tuple(problems)


def audit_repository(
    repository_root: Path,
    *,
    unpacked_root: Path | None = None,
    asar_listing: Path | None = None,
    extracted_asar_portrait: Path | None = None,
    extracted_asar_character_atlas: Path | None = None,
    extracted_asar_expression_atlas: Path | None = None,
    extracted_asar_speech_atlas: Path | None = None,
    extracted_asar_tree: Path | None = None,
) -> tuple[DistributionProblem, ...]:
    """Run the required repository checks plus any requested artifact scans."""

    legacy_asset_inputs = (
        extracted_asar_portrait,
        extracted_asar_character_atlas,
        extracted_asar_expression_atlas,
        extracted_asar_speech_atlas,
    )
    legacy_assets_requested = any(
        value is not None for value in legacy_asset_inputs
    )
    if legacy_assets_requested and (
        asar_listing is None
        or any(value is None for value in legacy_asset_inputs)
    ):
        raise DistributionAuditError(
            "ASAR listing and all extracted reviewed character assets must be "
            "audited together."
        )
    if extracted_asar_tree is not None and (
        asar_listing is None or legacy_assets_requested
    ):
        raise DistributionAuditError(
            "An extracted ASAR tree requires its listing and cannot be mixed "
            "with individual extracted-asset inputs."
        )
    if asar_listing is not None and not (
        legacy_assets_requested or extracted_asar_tree is not None
    ):
        raise DistributionAuditError(
            "An ASAR listing requires either the complete extracted tree or "
            "all legacy extracted character assets."
        )

    root = repository_root.resolve()
    problems = [
        *audit_git_index(root),
        *audit_reviewed_assets(root),
        *audit_desktop_builder(root / "desktop" / "package.json"),
    ]
    if unpacked_root is not None:
        problems.extend(audit_unpacked_tree(unpacked_root.resolve()))
    if asar_listing is not None:
        problems.extend(audit_asar_listing(asar_listing.resolve()))
    if extracted_asar_portrait is not None:
        problems.extend(
            # Do not resolve this path before the regular-file check: resolving
            # would hide a symlink supplied in place of the extracted payload.
            audit_extracted_asar_portrait(extracted_asar_portrait)
        )
    if extracted_asar_character_atlas is not None:
        problems.extend(
            # Preserve the link itself for the regular-file check, matching the
            # portrait boundary above.
            audit_extracted_asar_character_atlas(
                extracted_asar_character_atlas
            )
        )
    if extracted_asar_expression_atlas is not None:
        problems.extend(
            audit_extracted_asar_expression_atlas(
                extracted_asar_expression_atlas
            )
        )
    if extracted_asar_speech_atlas is not None:
        problems.extend(
            audit_extracted_asar_speech_atlas(extracted_asar_speech_atlas)
        )
    if extracted_asar_tree is not None:
        # Preserve the submitted root itself so a directory symlink cannot be
        # hidden by Path.resolve() before the boundary check.
        problems.extend(
            audit_extracted_asar_reviewed_assets(extracted_asar_tree)
        )
    return tuple(problems)


def _parse_arguments(arguments: Sequence[str] | None) -> argparse.Namespace:
    """Parse command-line inputs for local and CI distribution checks."""

    parser = argparse.ArgumentParser(
        description=(
            "Check distribution exclusions and authenticate reviewed public assets."
        ),
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
    parser.add_argument(
        "--extracted-asar-portrait",
        type=Path,
        help=(
            "Portrait extracted from dist/character/elysia-portrait.png in the "
            "same ASAR represented by --asar-listing."
        ),
    )
    parser.add_argument(
        "--extracted-asar-character-atlas",
        type=Path,
        help=(
            "State atlas extracted from "
            "dist/character/elysia-state-atlas.png in the same ASAR represented "
            "by --asar-listing."
        ),
    )
    parser.add_argument(
        "--extracted-asar-expression-atlas",
        type=Path,
        help=(
            "Expression atlas extracted from "
            "dist/character/elysia-expression-atlas.png in the same ASAR "
            "represented by --asar-listing."
        ),
    )
    parser.add_argument(
        "--extracted-asar-speech-atlas",
        type=Path,
        help=(
            "Speech atlas extracted from dist/character/elysia-speech-atlas.png "
            "in the same ASAR represented by --asar-listing."
        ),
    )
    parser.add_argument(
        "--extracted-asar-tree",
        type=Path,
        help=(
            "Complete tree extracted from the same ASAR represented by "
            "--asar-listing; authenticates every reviewed runtime asset."
        ),
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
            extracted_asar_portrait=options.extracted_asar_portrait,
            extracted_asar_character_atlas=(
                options.extracted_asar_character_atlas
            ),
            extracted_asar_expression_atlas=(
                options.extracted_asar_expression_atlas
            ),
            extracted_asar_speech_atlas=options.extracted_asar_speech_atlas,
            extracted_asar_tree=options.extracted_asar_tree,
        )
    except DistributionAuditError as error:
        print(f"Distribution asset check failed: {error}")
        return 1

    if problems:
        for problem in problems:
            print(problem.render())
        print(f"Distribution asset check failed: {len(problems)} problem(s).")
        return 1

    optional_count = (
        int(options.unpacked_tree is not None)
        + int(options.asar_listing is not None)
        + int(options.extracted_asar_portrait is not None)
        + int(options.extracted_asar_character_atlas is not None)
        + int(options.extracted_asar_expression_atlas is not None)
        + int(options.extracted_asar_speech_atlas is not None)
        + int(options.extracted_asar_tree is not None)
    )
    print(
        "Distribution asset check passed: Git index, reviewed public assets, "
        "Electron Builder, "
        f"and {optional_count} optional artifact input(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
