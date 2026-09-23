"""Test Git and Electron distribution boundaries for local voice assets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import check_distribution_assets


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _write_package(path: Path, build: object) -> None:
    """Write one minimal package document for focused builder-policy tests."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": "test", "build": build}), encoding="utf-8")


def _valid_build() -> dict[str, object]:
    """Return the exact safe Electron Builder declaration."""

    return {
        "appId": "ai.elysia.desktop",
        "productName": "Elysia",
        "asar": True,
        "directories": {
            "output": "out",
            "buildResources": "assets",
        },
        "files": ["dist/**/*", "dist-electron/**/*", "package.json"],
        "win": {
            "target": "nsis",
            "icon": "elysia-icon.ico",
            "artifactName": "Elysia-Setup-${version}.${ext}",
        },
    }


@pytest.mark.parametrize(
    "path",
    [
        "models/weights/gpt-sovits/voice.ckpt",
        "desktop/MODELS/CACHE/runtime.py",
        "copied/models/blobs/sha256-deadbeef",
        "workspace/settings/voice-profiles.json",
        "desktop/LOGS/speech.log",
        ".ENV",
        "elsewhere/voice.PTH",
        "assets/reference.WAV",
        "release/local-voice.7Z",
        "copied/GPT-SoVITS-v2-240821/runtime/python.exe",
    ],
)
def test_forced_add_style_paths_are_rejected(path: str) -> None:
    """Catch files that ignore rules cannot stop a contributor force-adding."""

    problems = check_distribution_assets.audit_distribution_paths(
        [path],
        source="synthetic-index",
    )

    assert len(problems) == 1


def test_case_and_unicode_compatibility_aliases_are_rejected() -> None:
    """Normalize case and full-width spellings before applying path policy."""

    problems = check_distribution_assets.audit_distribution_paths(
        [
            "ＭＯＤＥＬＳ/ＷＥＩＧＨＴＳ/voice.CKPT",
            "ＷＯＲＫＳＰＡＣＥ/settings/global.json",
            "nested/ＷＯＲＫＳＰＡＣＥ／settings／global.json",
        ],
        source="synthetic-index",
    )

    assert len(problems) == 3


def test_maintained_source_and_brand_assets_are_allowed() -> None:
    """Keep source modules, examples, and reviewed branding outside the denylist."""

    problems = check_distribution_assets.audit_distribution_paths(
        [
            "models/__init__.py",
            "voice/gpt_sovits.py",
            "config/voice_profiles.example.json",
            "desktop/public/elysia-icon.png",
            "desktop/assets/elysia-icon.ico",
        ],
        source="synthetic-index",
    )

    assert problems == ()


def test_builder_accepts_only_the_frozen_allowlist(tmp_path: Path) -> None:
    """Accept the production package only when its narrow allowlist is unchanged."""

    package = tmp_path / "package.json"
    _write_package(package, _valid_build())

    assert check_distribution_assets.audit_desktop_builder(package) == ()


@pytest.mark.parametrize("key", ["extraResources", "extraFiles", "asarUnpack"])
def test_builder_copy_escape_keys_are_rejected_at_any_depth(
    tmp_path: Path,
    key: str,
) -> None:
    """Reject builder features that can copy files around the main allowlist."""

    package = tmp_path / "package.json"
    build = _valid_build()
    build["win"] = {key: ["../models/weights"]}
    _write_package(package, build)

    problems = check_distribution_assets.audit_desktop_builder(package)

    assert any(key in problem.message for problem in problems)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("extends", "../unsafe-builder.json"),
        ("beforePack", "../copy-local-assets.cjs"),
        ("afterPack", "../copy-local-assets.cjs"),
        ("electronDist", "../custom-electron"),
        ("nsis", {"include": "../unsafe-installer.nsh"}),
    ],
)
def test_builder_rejects_unreviewed_inheritance_hooks_and_inputs(
    tmp_path: Path,
    key: str,
    value: object,
) -> None:
    """Freeze the complete build shape so indirect copy mechanisms fail."""

    package = tmp_path / "package.json"
    build = _valid_build()
    build[key] = value
    _write_package(package, build)

    problems = check_distribution_assets.audit_desktop_builder(package)

    assert any(problem.path == "build" for problem in problems)


def test_builder_rejects_app_root_and_platform_specific_files(
    tmp_path: Path,
) -> None:
    """Reject nested inputs that Electron Builder merges with root settings."""

    package = tmp_path / "package.json"
    build = _valid_build()
    build["directories"] = {
        "output": "out",
        "buildResources": "assets",
        "app": "../outside-desktop",
    }
    build["win"] = {
        "target": "nsis",
        "icon": "elysia-icon.ico",
        "artifactName": "Elysia-Setup-${version}.${ext}",
        "files": ["../models/weights/**/*"],
    }
    _write_package(package, build)

    problems = check_distribution_assets.audit_desktop_builder(package)

    assert any(problem.path == "build" for problem in problems)


@pytest.mark.parametrize(
    "mutation",
    [
        {"files": ["**/*"]},
        {"files": ["dist/**/*", "dist-electron/**/*", "package.json", "../models"]},
        {"asar": False},
    ],
)
def test_builder_allowlist_regressions_are_rejected(
    tmp_path: Path,
    mutation: dict[str, object],
) -> None:
    """Fail when packaging broadens its inputs or disables the expected ASAR."""

    package = tmp_path / "package.json"
    build = _valid_build()
    build.update(mutation)
    _write_package(package, build)

    assert check_distribution_assets.audit_desktop_builder(package)


def test_unpacked_tree_rejects_assets_but_allows_electron_snapshot_bins(
    tmp_path: Path,
) -> None:
    """Scan produced files while allowing only Electron's known root snapshots."""

    (tmp_path / "snapshot_blob.bin").write_bytes(b"electron runtime")
    (tmp_path / "v8_context_snapshot.bin").write_bytes(b"electron runtime")
    hidden_asset = tmp_path / "resources" / "copied" / "reference.flac"
    hidden_asset.parent.mkdir(parents=True)
    hidden_asset.write_bytes(b"not real audio")

    problems = check_distribution_assets.audit_unpacked_tree(tmp_path)

    assert len(problems) == 1
    assert problems[0].path.endswith("reference.flac")


def test_asar_listing_rejects_unicode_paths_and_concealed_archives(
    tmp_path: Path,
) -> None:
    """Apply the same alias-resistant checks inside an ASAR file listing."""

    listing = tmp_path / "asar-listing.txt"
    listing.write_text(
        "\\dist\\index.html\n"
        "\\ＭＯＤＥＬＳ\\ＷＥＩＧＨＴＳ\\voice.ckpt\n"
        "\\dist\\assets\\voice-pack.zip\n",
        encoding="utf-8",
    )

    problems = check_distribution_assets.audit_asar_listing(listing)

    assert len(problems) == 2


def test_current_repository_passes_required_distribution_checks() -> None:
    """Keep the real Git index and Electron package declaration within policy."""

    assert check_distribution_assets.audit_repository(_REPOSITORY_ROOT) == ()
