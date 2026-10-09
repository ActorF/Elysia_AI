"""Test Git and Electron distribution boundaries for reviewed local assets."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts import check_distribution_assets


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_REQUIRED_ASAR_LISTING_ENTRIES = (
    "\\dist\\elysia-icon.png",
    "\\dist\\character\\elysia-portrait.png",
    "\\dist\\character\\elysia-state-atlas.png",
    "\\dist\\character\\elysia-expression-atlas.png",
    "\\dist\\character\\elysia-speech-atlas.png",
)


def _required_asar_entries(*, excluding: str | None = None) -> list[str]:
    """Return the complete critical-entry fixture except one focused target."""

    return [
        entry
        for entry in _REQUIRED_ASAR_LISTING_ENTRIES
        if entry.lstrip("\\").replace("\\", "/") != excluding
    ]


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
        "copied/private-job/lyrics.lrc",
        "copied/private-job/lyrics.txt",
        "copied/private-job/lyrics-manifest.json",
        "release/local-voice.7Z",
        "copied/GPT-SoVITS-v2-240821/runtime/python.exe",
        "data/characters/爱莉希雅原版猫猫版总合集_34be1/model/model.moc3",
        "dist/character/live2d/copied/model.moc3",
        "desktop/public/character/live2d/copied/textures/texture_00.png",
    ],
)
def test_forced_add_style_paths_are_rejected(path: str) -> None:
    """Catch files that ignore rules cannot stop a contributor force-adding."""

    problems = check_distribution_assets.audit_distribution_paths(
        [path],
        source="synthetic-index",
    )

    assert len(problems) == 1


@pytest.mark.parametrize(
    "file_name",
    [
        "renamed.can3",
        "renamed.cdi3.json",
        "renamed.cmo3",
        "renamed.cmp3",
        "renamed.exp3.json",
        "renamed.moc3",
        "renamed.model3.json",
        "renamed.motion3.json",
        "renamed.physics3.json",
        "renamed.pose3.json",
        "renamed.userdata3.json",
        "ＲＥＮＡＭＥＤ．ＭＯＤＥＬ３．ＪＳＯＮ",
    ],
)
def test_live2d_model_suffixes_are_globally_rejected(file_name: str) -> None:
    """Reject renamed Cubism payloads outside every known asset directory."""

    problems = check_distribution_assets.audit_distribution_paths(
        [f"unrelated/themes/rose-companion/{file_name}"],
        source="synthetic-index",
    )

    assert len(problems) == 1
    assert "external Live2D model" in problems[0].message


def test_git_index_rejects_renamed_paid_live2d_copy(tmp_path: Path) -> None:
    """Catch a paid model copied and renamed beneath an innocuous Git path."""

    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    copied_model = tmp_path / "themes" / "rose-companion" / "avatar.moc3"
    copied_manifest = copied_model.with_name("avatar.model3.json")
    copied_model.parent.mkdir(parents=True)
    copied_model.write_bytes(b"synthetic paid-model copy")
    copied_manifest.write_text("{}", encoding="utf-8")
    subprocess.run(
        ["git", "add", "--all"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    problems = check_distribution_assets.audit_git_index(tmp_path)

    assert {problem.path for problem in problems} == {
        "themes/rose-companion/avatar.moc3",
        "themes/rose-companion/avatar.model3.json",
    }
    assert all(
        "external Live2D model" in problem.message for problem in problems
    )


def test_git_index_rejects_paid_texture_under_an_innocent_path(
    tmp_path: Path,
) -> None:
    """Require explicit review before any new raster asset can enter Git."""

    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    copied_texture = tmp_path / "themes" / "rose" / "texture_00.png"
    copied_texture.parent.mkdir(parents=True)
    copied_texture.write_bytes(b"synthetic paid texture copy")
    subprocess.run(
        ["git", "add", "--all"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    problems = check_distribution_assets.audit_git_index(tmp_path)

    assert len(problems) == 1
    assert problems[0].path == "themes/rose/texture_00.png"
    assert "unreviewed visual asset" in problems[0].message


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
            "desktop/public/character/elysia-portrait.png",
            "desktop/public/character/elysia-state-atlas.png",
            "desktop/public/character/elysia-expression-atlas.png",
            "desktop/public/character/elysia-speech-atlas.png",
        ],
        source="synthetic-index",
    )

    assert problems == ()


def test_retired_embedded_live2d_runtime_is_rejected_everywhere() -> None:
    """Keep the deleted compatibility runtime out of Git and packages."""

    rejected = check_distribution_assets.audit_distribution_paths(
        [
            "desktop/public/character/live2d/runtime/purismcore.js",
            "dist/character/live2d/runtime/LICENSE-PurismCore.txt",
            "vendor/copied/purismcore.js",
            "assets/copied/LICENSE-PurismCore.txt",
        ],
        source="synthetic-distribution",
    )

    assert len(rejected) == 4
    assert all("retired embedded Live2D" in problem.message for problem in rejected)


def _copy_reviewed_portrait(destination_root: Path) -> Path:
    """Copy the real approved portrait into one isolated audit fixture."""

    source = (
        _REPOSITORY_ROOT
        / "desktop"
        / "public"
        / "character"
        / "elysia-portrait.png"
    )
    destination = (
        destination_root
        / "desktop"
        / "public"
        / "character"
        / "elysia-portrait.png"
    )
    destination.parent.mkdir(parents=True)
    shutil.copyfile(source, destination)
    return destination


def _copy_reviewed_atlas(destination_root: Path) -> Path:
    """Copy the approved state atlas into one isolated audit fixture."""

    source = (
        _REPOSITORY_ROOT
        / "desktop"
        / "public"
        / "character"
        / "elysia-state-atlas.png"
    )
    destination = (
        destination_root
        / "desktop"
        / "public"
        / "character"
        / "elysia-state-atlas.png"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination


def _copy_reviewed_expression_atlas(destination_root: Path) -> Path:
    """Copy the approved expression atlas into one isolated audit fixture."""

    source = (
        _REPOSITORY_ROOT
        / "desktop"
        / "public"
        / "character"
        / "elysia-expression-atlas.png"
    )
    destination = (
        destination_root
        / "desktop"
        / "public"
        / "character"
        / "elysia-expression-atlas.png"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination


def _copy_reviewed_speech_atlas(destination_root: Path) -> Path:
    """Copy the approved speech atlas into one isolated audit fixture."""

    source = (
        _REPOSITORY_ROOT
        / "desktop"
        / "public"
        / "character"
        / "elysia-speech-atlas.png"
    )
    destination = (
        destination_root
        / "desktop"
        / "public"
        / "character"
        / "elysia-speech-atlas.png"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination


def _copy_reviewed_brand_assets(destination_root: Path) -> tuple[Path, Path]:
    """Copy both pinned application-icon formats into an audit fixture."""

    relative_paths = (
        "desktop/public/elysia-icon.png",
        "desktop/assets/elysia-icon.ico",
    )
    copied: list[Path] = []
    for relative_path in relative_paths:
        source = _REPOSITORY_ROOT / relative_path
        destination = destination_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        copied.append(destination)
    return copied[0], copied[1]


def _copy_reviewed_character_assets(
    destination_root: Path,
) -> tuple[Path, Path, Path, Path]:
    """Copy every required character asset into an isolated audit fixture."""

    assets = (
        _copy_reviewed_portrait(destination_root),
        _copy_reviewed_atlas(destination_root),
        _copy_reviewed_expression_atlas(destination_root),
        _copy_reviewed_speech_atlas(destination_root),
    )
    _copy_reviewed_brand_assets(destination_root)
    return assets


def _copy_reviewed_asar_tree(destination_root: Path) -> Path:
    """Build an extracted-ASAR fixture containing every pinned public asset."""

    source_and_archive_paths = (
        ("elysia-icon.png", "elysia-icon.png"),
        (
            "character/elysia-portrait.png",
            "character/elysia-portrait.png",
        ),
        (
            "character/elysia-state-atlas.png",
            "character/elysia-state-atlas.png",
        ),
        (
            "character/elysia-expression-atlas.png",
            "character/elysia-expression-atlas.png",
        ),
        (
            "character/elysia-speech-atlas.png",
            "character/elysia-speech-atlas.png",
        ),
    )
    for source_relative, archive_relative in source_and_archive_paths:
        source = _REPOSITORY_ROOT / "desktop" / "public" / source_relative
        destination = destination_root / "dist" / archive_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return destination_root


def test_reviewed_character_portrait_matches_exact_contract(tmp_path: Path) -> None:
    """Accept only character bytes covered by the distribution review."""

    _copy_reviewed_character_assets(tmp_path)

    assert check_distribution_assets.audit_reviewed_assets(tmp_path) == ()


def test_extracted_asar_tree_authenticates_reviewed_static_assets(
    tmp_path: Path,
) -> None:
    """Accept a package tree when every reviewed static asset matches."""

    extracted = _copy_reviewed_asar_tree(tmp_path / "extracted")

    assert (
        check_distribution_assets.audit_extracted_asar_reviewed_assets(
            extracted
        )
        == ()
    )


def test_runtime_state_atlas_is_the_reviewed_source_without_reencoding() -> None:
    """Keep the packaged atlas byte-identical to the accepted review image."""

    source = (
        _REPOSITORY_ROOT
        / "data"
        / "characters"
        / "elysia-2dArt"
        / "02-activity-states.png"
    )
    runtime = (
        _REPOSITORY_ROOT
        / "desktop"
        / "public"
        / "character"
        / "elysia-state-atlas.png"
    )

    assert source.read_bytes() == runtime.read_bytes()


@pytest.mark.parametrize(
    ("source_name", "runtime_name"),
    [
        ("03-expression-atlas.png", "elysia-expression-atlas.png"),
        ("04-facial-rig-atlas.png", "elysia-speech-atlas.png"),
    ],
)
def test_runtime_face_atlases_are_reviewed_sources_without_reencoding(
    source_name: str,
    runtime_name: str,
) -> None:
    """Keep each packaged face atlas byte-identical to its approved source."""

    source = (
        _REPOSITORY_ROOT
        / "data"
        / "characters"
        / "elysia-2dArt"
        / source_name
    )
    runtime = (
        _REPOSITORY_ROOT
        / "desktop"
        / "public"
        / "character"
        / runtime_name
    )

    assert source.read_bytes() == runtime.read_bytes()


def test_reviewed_character_portrait_content_mutation_is_rejected(
    tmp_path: Path,
) -> None:
    """Reject a same-length replacement that would evade a size-only check."""

    portrait, _atlas, _expression, _speech = _copy_reviewed_character_assets(
        tmp_path
    )
    with portrait.open("r+b") as portrait_stream:
        first_byte = portrait_stream.read(1)
        portrait_stream.seek(0)
        portrait_stream.write(bytes((first_byte[0] ^ 0xFF,)))

    problems = check_distribution_assets.audit_reviewed_assets(tmp_path)

    assert len(problems) == 1
    assert "SHA-256" in problems[0].message


def test_reviewed_character_portrait_length_mutation_is_rejected(
    tmp_path: Path,
) -> None:
    """Reject truncation even though the reviewed path and format still match."""

    portrait, _atlas, _expression, _speech = _copy_reviewed_character_assets(
        tmp_path
    )
    with portrait.open("r+b") as portrait_stream:
        portrait_stream.truncate(portrait.stat().st_size - 1)

    problems = check_distribution_assets.audit_reviewed_assets(tmp_path)

    assert any("byte length" in problem.message for problem in problems)


@pytest.mark.parametrize(
    "missing_name",
    [
        "elysia-portrait.png",
        "elysia-state-atlas.png",
        "elysia-expression-atlas.png",
        "elysia-speech-atlas.png",
    ],
)
def test_reviewed_character_assets_are_required_at_exact_paths(
    tmp_path: Path,
    missing_name: str,
) -> None:
    """Fail closed when either approved distributable image is absent."""

    assets = _copy_reviewed_character_assets(tmp_path)
    missing = next(asset for asset in assets if asset.name == missing_name)
    missing.unlink()
    problems = check_distribution_assets.audit_reviewed_assets(tmp_path)

    assert len(problems) == 1
    assert problems[0].path == f"desktop/public/character/{missing_name}"
    assert "missing, linked, or unreadable" in problems[0].message


def test_extracted_asar_portrait_matches_exact_contract(tmp_path: Path) -> None:
    """Accept the exact reviewed bytes after extraction from an ASAR package."""

    portrait = _copy_reviewed_portrait(tmp_path)

    assert check_distribution_assets.audit_extracted_asar_portrait(portrait) == ()


def test_extracted_asar_portrait_mutation_is_rejected(tmp_path: Path) -> None:
    """Reject packaged bytes that differ from the reviewed repository image."""

    portrait = _copy_reviewed_portrait(tmp_path)
    with portrait.open("r+b") as portrait_stream:
        portrait_stream.seek(1024)
        original_byte = portrait_stream.read(1)
        portrait_stream.seek(1024)
        portrait_stream.write(bytes((original_byte[0] ^ 0xFF,)))

    problems = check_distribution_assets.audit_extracted_asar_portrait(portrait)

    assert len(problems) == 1
    assert problems[0].path == "dist/character/elysia-portrait.png"
    assert "SHA-256" in problems[0].message


def test_extracted_asar_character_atlas_matches_exact_contract(
    tmp_path: Path,
) -> None:
    """Accept the reviewed state atlas after extraction from an ASAR package."""

    atlas = _copy_reviewed_atlas(tmp_path)

    assert (
        check_distribution_assets.audit_extracted_asar_character_atlas(atlas)
        == ()
    )


def test_extracted_asar_character_atlas_mutation_is_rejected(
    tmp_path: Path,
) -> None:
    """Reject packaged state-atlas bytes that differ from the review."""

    atlas = _copy_reviewed_atlas(tmp_path)
    with atlas.open("r+b") as atlas_stream:
        atlas_stream.seek(2048)
        original_byte = atlas_stream.read(1)
        atlas_stream.seek(2048)
        atlas_stream.write(bytes((original_byte[0] ^ 0xFF,)))

    problems = check_distribution_assets.audit_extracted_asar_character_atlas(
        atlas
    )

    assert len(problems) == 1
    assert problems[0].path == "dist/character/elysia-state-atlas.png"
    assert "SHA-256" in problems[0].message


def test_extracted_asar_expression_atlas_matches_exact_contract(
    tmp_path: Path,
) -> None:
    """Accept the reviewed expression atlas extracted from the ASAR."""

    atlas = _copy_reviewed_expression_atlas(tmp_path)

    assert (
        check_distribution_assets.audit_extracted_asar_expression_atlas(atlas)
        == ()
    )


def test_extracted_asar_expression_atlas_mutation_is_rejected(
    tmp_path: Path,
) -> None:
    """Reject packaged expression-atlas bytes that differ from review."""

    atlas = _copy_reviewed_expression_atlas(tmp_path)
    with atlas.open("r+b") as atlas_stream:
        atlas_stream.seek(3072)
        original_byte = atlas_stream.read(1)
        atlas_stream.seek(3072)
        atlas_stream.write(bytes((original_byte[0] ^ 0xFF,)))

    problems = check_distribution_assets.audit_extracted_asar_expression_atlas(
        atlas
    )

    assert len(problems) == 1
    assert problems[0].path == "dist/character/elysia-expression-atlas.png"
    assert "SHA-256" in problems[0].message


def test_extracted_asar_speech_atlas_matches_exact_contract(
    tmp_path: Path,
) -> None:
    """Accept the reviewed speech atlas extracted from the ASAR."""

    atlas = _copy_reviewed_speech_atlas(tmp_path)

    assert check_distribution_assets.audit_extracted_asar_speech_atlas(atlas) == ()


def test_extracted_asar_speech_atlas_mutation_is_rejected(
    tmp_path: Path,
) -> None:
    """Reject packaged speech-atlas bytes that differ from review."""

    atlas = _copy_reviewed_speech_atlas(tmp_path)
    with atlas.open("r+b") as atlas_stream:
        atlas_stream.seek(4096)
        original_byte = atlas_stream.read(1)
        atlas_stream.seek(4096)
        atlas_stream.write(bytes((original_byte[0] ^ 0xFF,)))

    problems = check_distribution_assets.audit_extracted_asar_speech_atlas(atlas)

    assert len(problems) == 1
    assert problems[0].path == "dist/character/elysia-speech-atlas.png"
    assert "SHA-256" in problems[0].message


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


def test_unpacked_tree_rejects_renamed_paid_live2d_copy(
    tmp_path: Path,
) -> None:
    """Find a renamed external Cubism model anywhere in a produced tree."""

    copied_root = tmp_path / "resources" / "themes" / "rose-companion"
    copied_root.mkdir(parents=True)
    (copied_root / "avatar.moc3").write_bytes(b"synthetic paid-model copy")
    (copied_root / "avatar.model3.json").write_text("{}", encoding="utf-8")

    problems = check_distribution_assets.audit_unpacked_tree(tmp_path)

    assert {problem.path for problem in problems} == {
        "resources/themes/rose-companion/avatar.moc3",
        "resources/themes/rose-companion/avatar.model3.json",
    }
    assert all(
        "external Live2D model" in problem.message for problem in problems
    )


def test_unpacked_tree_rejects_paid_texture_without_model_files(
    tmp_path: Path,
) -> None:
    """Reject a standalone paid texture even when model suffixes are absent."""

    copied_texture = tmp_path / "resources" / "themes" / "texture_00.png"
    copied_texture.parent.mkdir(parents=True)
    copied_texture.write_bytes(b"synthetic paid texture copy")

    problems = check_distribution_assets.audit_unpacked_tree(tmp_path)

    assert len(problems) == 1
    assert problems[0].path == "resources/themes/texture_00.png"
    assert "unreviewed visual asset" in problems[0].message


@pytest.mark.parametrize(
    "path",
    [
        "dist-electron/character-performance-contracts.js",
        "dist-electron/character-performance-contracts.js.map",
        "dist-electron/desktop-pet-model-library.js",
        "dist-electron/desktop-pet-preload.cjs",
        "dist-electron/live2d-assets.js",
        "dist/assets/desktop-pet-main-deadbeef.js",
        "dist/assets/live2d-runtime-deadbeef.js",
        "dist-electron/speech-mouth.js",
        "dist/assets/speech-mouth-deadbeef.js",
    ],
)
def test_build_outputs_reject_retired_desktop_modules(path: str) -> None:
    """Stop stale emitted modules from surviving source-file deletion."""

    problems = check_distribution_assets.audit_distribution_paths(
        [path],
        source="synthetic-build",
    )

    assert len(problems) == 1
    assert "retired desktop module" in problems[0].message


def test_asar_listing_rejects_unicode_paths_and_concealed_archives(
    tmp_path: Path,
) -> None:
    """Apply the same alias-resistant checks inside an ASAR file listing."""

    listing = tmp_path / "asar-listing.txt"
    listing.write_text(
        "\n".join([
            "\\dist\\index.html",
            *_required_asar_entries(),
            "\\ＭＯＤＥＬＳ\\ＷＥＩＧＨＴＳ\\voice.ckpt",
            "\\dist\\assets\\voice-pack.zip",
        ]) + "\n",
        encoding="utf-8",
    )

    problems = check_distribution_assets.audit_asar_listing(listing)

    assert len(problems) == 2


def test_asar_listing_rejects_renamed_paid_live2d_copy(
    tmp_path: Path,
) -> None:
    """Find renamed external Cubism payloads in an otherwise valid ASAR."""

    copied_entries = (
        "\\dist\\themes\\rose-companion\\avatar.moc3",
        "\\dist\\themes\\rose-companion\\avatar.model3.json",
    )
    listing = tmp_path / "asar-listing.txt"
    listing.write_text(
        "\n".join([
            "\\dist\\index.html",
            *_required_asar_entries(),
            *copied_entries,
        ]) + "\n",
        encoding="utf-8",
    )

    problems = check_distribution_assets.audit_asar_listing(listing)

    assert {problem.path for problem in problems} == set(copied_entries)
    assert all(
        "external Live2D model" in problem.message for problem in problems
    )


def test_asar_listing_rejects_paid_texture_without_model_files(
    tmp_path: Path,
) -> None:
    """Admit only reviewed visual paths in the final application archive."""

    copied_texture = "\\dist\\themes\\rose\\texture_00.png"
    listing = tmp_path / "asar-listing.txt"
    listing.write_text(
        "\n".join([
            "\\dist\\index.html",
            *_required_asar_entries(),
            copied_texture,
        ]) + "\n",
        encoding="utf-8",
    )

    problems = check_distribution_assets.audit_asar_listing(listing)

    assert len(problems) == 1
    assert problems[0].path == copied_texture
    assert "unreviewed visual asset" in problems[0].message


def test_asar_listing_rejects_retired_desktop_module_outputs(
    tmp_path: Path,
) -> None:
    """Reject stale retired modules in an otherwise complete desktop ASAR."""

    retired_entries = (
        "\\dist-electron\\character-performance-contracts.js",
        "\\dist-electron\\desktop-pet-model-library.js",
        "\\dist-electron\\desktop-pet-preload.cjs",
        "\\dist-electron\\live2d-assets.js",
        "\\dist-electron\\speech-mouth.js.map",
    )
    listing = tmp_path / "asar-listing.txt"
    listing.write_text(
        "\n".join([
            "\\dist\\index.html",
            *_required_asar_entries(),
            *retired_entries,
        ]) + "\n",
        encoding="utf-8",
    )

    problems = check_distribution_assets.audit_asar_listing(listing)

    assert {problem.path for problem in problems} == set(retired_entries)
    assert all(
        "retired desktop module" in problem.message for problem in problems
    )


@pytest.mark.parametrize(
    ("portrait_entries", "expected_exact", "expected_normalized"),
    [
        ([], 0, 0),
        (
            [
                "\\dist\\character\\elysia-portrait.png",
                "\\dist\\character\\elysia-portrait.png",
            ],
            2,
            2,
        ),
        (
            [
                "\\dist\\character\\elysia-portrait.png",
                "\\DIST\\CHARACTER\\ELYSIA-PORTRAIT.PNG",
            ],
            1,
            2,
        ),
        (
            [
                "\\dist\\character\\elysia-portrait.png",
                "\\ｄｉｓｔ\\ｃｈａｒａｃｔｅｒ\\ｅｌｙｓｉａ－ｐｏｒｔｒａｉｔ．ｐｎｇ",
            ],
            1,
            2,
        ),
    ],
)
def test_asar_listing_requires_exactly_one_reviewed_portrait(
    tmp_path: Path,
    portrait_entries: list[str],
    expected_exact: int,
    expected_normalized: int,
) -> None:
    """Reject missing or duplicate portrait entries in the actual ASAR index."""

    listing = tmp_path / "asar-listing.txt"
    listing.write_text(
        "\n".join([
            "\\dist\\index.html",
            *_required_asar_entries(
                excluding="dist/character/elysia-portrait.png"
            ),
            *portrait_entries,
        ]) + "\n",
        encoding="utf-8",
    )

    problems = check_distribution_assets.audit_asar_listing(listing)

    assert len(problems) == 1
    assert problems[0].path == "dist/character/elysia-portrait.png"
    assert f"found {expected_exact} exact" in problems[0].message
    assert f"{expected_normalized} normalized" in problems[0].message


@pytest.mark.parametrize(
    ("atlas_entries", "expected_exact", "expected_normalized"),
    [
        ([], 0, 0),
        (
            [
                "\\dist\\character\\elysia-state-atlas.png",
                "\\dist\\character\\elysia-state-atlas.png",
            ],
            2,
            2,
        ),
        (
            [
                "\\dist\\character\\elysia-state-atlas.png",
                "\\DIST\\CHARACTER\\ELYSIA-STATE-ATLAS.PNG",
            ],
            1,
            2,
        ),
    ],
)
def test_asar_listing_requires_exactly_one_reviewed_state_atlas(
    tmp_path: Path,
    atlas_entries: list[str],
    expected_exact: int,
    expected_normalized: int,
) -> None:
    """Reject missing or duplicate state-atlas entries in the ASAR index."""

    listing = tmp_path / "asar-listing.txt"
    listing.write_text(
        "\n".join([
            "\\dist\\index.html",
            *_required_asar_entries(
                excluding="dist/character/elysia-state-atlas.png"
            ),
            *atlas_entries,
        ]) + "\n",
        encoding="utf-8",
    )

    problems = check_distribution_assets.audit_asar_listing(listing)

    assert len(problems) == 1
    assert problems[0].path == "dist/character/elysia-state-atlas.png"
    assert f"found {expected_exact} exact" in problems[0].message
    assert f"{expected_normalized} normalized" in problems[0].message


@pytest.mark.parametrize(
    ("logical_name", "entries", "expected_exact", "expected_normalized"),
    [
        ("elysia-expression-atlas.png", [], 0, 0),
        (
            "elysia-expression-atlas.png",
            [
                "\\dist\\character\\elysia-expression-atlas.png",
                "\\dist\\character\\elysia-expression-atlas.png",
            ],
            2,
            2,
        ),
        (
            "elysia-expression-atlas.png",
            [
                "\\dist\\character\\elysia-expression-atlas.png",
                "\\DIST\\CHARACTER\\ELYSIA-EXPRESSION-ATLAS.PNG",
            ],
            1,
            2,
        ),
        ("elysia-speech-atlas.png", [], 0, 0),
        (
            "elysia-speech-atlas.png",
            [
                "\\dist\\character\\elysia-speech-atlas.png",
                "\\dist\\character\\elysia-speech-atlas.png",
            ],
            2,
            2,
        ),
        (
            "elysia-speech-atlas.png",
            [
                "\\dist\\character\\elysia-speech-atlas.png",
                "\\DIST\\CHARACTER\\ELYSIA-SPEECH-ATLAS.PNG",
            ],
            1,
            2,
        ),
    ],
)
def test_asar_listing_requires_each_reviewed_face_atlas_once(
    tmp_path: Path,
    logical_name: str,
    entries: list[str],
    expected_exact: int,
    expected_normalized: int,
) -> None:
    """Reject missing or aliased expression and speech atlas entries."""

    logical_path = f"dist/character/{logical_name}"
    baseline = [
        "\\dist\\index.html",
        *_required_asar_entries(excluding=logical_path),
    ]
    listing = tmp_path / "asar-listing.txt"
    listing.write_text(
        "\n".join([*baseline, *entries]) + "\n",
        encoding="utf-8",
    )

    problems = check_distribution_assets.audit_asar_listing(listing)

    assert len(problems) == 1
    assert problems[0].path == f"dist/character/{logical_name}"
    assert f"found {expected_exact} exact" in problems[0].message
    assert f"{expected_normalized} normalized" in problems[0].message


def test_repository_requires_all_packaged_character_proofs_together(
    tmp_path: Path,
) -> None:
    """Prevent a package audit from omitting any character integrity proof."""

    listing = tmp_path / "asar-listing.txt"
    complete_inputs = {
        "asar_listing": listing,
        "extracted_asar_portrait": tmp_path / "elysia-portrait.png",
        "extracted_asar_character_atlas": tmp_path / "elysia-state-atlas.png",
        "extracted_asar_expression_atlas": (
            tmp_path / "elysia-expression-atlas.png"
        ),
        "extracted_asar_speech_atlas": tmp_path / "elysia-speech-atlas.png",
    }

    for omitted_name in complete_inputs:
        partial_inputs = {
            name: value
            for name, value in complete_inputs.items()
            if name != omitted_name
        }
        with pytest.raises(check_distribution_assets.DistributionAuditError):
            check_distribution_assets.audit_repository(
                _REPOSITORY_ROOT,
                **partial_inputs,
            )


def test_current_repository_passes_required_distribution_checks() -> None:
    """Keep the real Git index and Electron package declaration within policy."""

    assert check_distribution_assets.audit_repository(_REPOSITORY_ROOT) == ()


def test_cli_success_counts_all_extracted_character_assets(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep the CLI summary aligned with every accepted artifact proof."""

    monkeypatch.setattr(
        check_distribution_assets,
        "audit_repository",
        lambda *args, **kwargs: (),
    )

    status = check_distribution_assets.main([
        "--unpacked-tree",
        "unpacked",
        "--asar-listing",
        "listing.txt",
        "--extracted-asar-portrait",
        "portrait.png",
        "--extracted-asar-character-atlas",
        "atlas.png",
        "--extracted-asar-expression-atlas",
        "expression.png",
        "--extracted-asar-speech-atlas",
        "speech.png",
    ])

    assert status == 0
    assert "6 optional artifact input(s)" in capsys.readouterr().out
