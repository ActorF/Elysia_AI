"""Compile the reviewed Elysia source layers into the three-file runtime Live2D bundle.

The binary MOC writer is supplied by the Apache-2.0 ``image2live2d`` project at the pinned commit
recorded below.  It is kept outside this repository because it is an authoring dependency, not
application runtime code.  This wrapper owns Elysia-specific validation, deterministic face render
order, and the minimal manifest shipped by Electron.
"""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LAYER_DIR = (
    PROJECT_ROOT / "data" / "characters" / "elysia-2dArt" / "live2d-source"
)
DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT / "desktop" / "public" / "character" / "live2d" / "elysia"
)
PINNED_IMAGE2LIVE2D_COMMIT = "714e7cc9191f1ef6c9a1732c80e8b566ec127d6b"

_FACE_ORDER = {
    "face_base": 40,
    "blush": 41,
    "eye_l": 43,
    "eye_r": 44,
    "eye_closed_l": 45,
    "eye_closed_r": 46,
    "eyebrow_l": 47,
    "eyebrow_r": 48,
    "nose": 49,
    "mouth_cavity": 50,
    "mouth": 51,
    # Front hair must be after the brows and eyes.  This lets opaque bangs cover those features while
    # transparent gaps still reveal them, instead of painting brow strokes on top of the fringe.
    "hair_front": 60,
    "accessory": 70,
}


def _git_commit(repository: Path) -> str:
    """Return the checked-out authoring dependency commit, or an empty string outside a Git clone."""

    result = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _load_generator(generator_root: Path) -> dict[str, Any]:
    """Load the pinned generator modules after validating its checkout identity."""

    commit = _git_commit(generator_root)
    if commit != PINNED_IMAGE2LIVE2D_COMMIT:
        raise ValueError(
            "Expected image2live2d commit "
            f"{PINNED_IMAGE2LIVE2D_COMMIT}, found {commit or 'no Git commit'}."
        )
    source_root = generator_root / "src"
    if not source_root.is_dir():
        raise ValueError(f"image2live2d source directory does not exist: {source_root}")
    sys.path.insert(0, str(source_root))
    try:
        return {
            "decompose": importlib.import_module("image2live2d.core.decompose"),
            "pipeline": importlib.import_module("image2live2d.pipeline"),
            "moc3_binary": importlib.import_module(
                "image2live2d.backends.live2d.moc3_binary"
            ),
            "moc3_emit": importlib.import_module(
                "image2live2d.backends.live2d.moc3_emit"
            ),
        }
    finally:
        sys.path.pop(0)


def _normalize_face_render_order(rig: Any) -> None:
    """Apply Elysia's reviewed occlusion order without changing parameter or mesh geometry."""

    for part in rig.parts_in_draw_order():
        role = getattr(part.semantic_role, "value", str(part.semantic_role))
        if role in _FACE_ORDER:
            part.draw_order = _FACE_ORDER[role]


def _manifest() -> dict[str, Any]:
    """Return the fixed manifest accepted by the restricted Electron asset boundary."""

    return {
        "Version": 3,
        "FileReferences": {
            "Moc": "model.moc3",
            "Textures": ["textures/atlas.png"],
        },
        "Groups": [
            {
                "Target": "Parameter",
                "Name": "EyeBlink",
                "Ids": ["ParamEyeLOpen", "ParamEyeROpen"],
            },
            {
                "Target": "Parameter",
                "Name": "LipSync",
                "Ids": ["ParamMouthOpenY"],
            },
        ],
        "HitAreas": [
            {"Id": "20_torso", "Name": "Body"},
            {"Id": "40_face_base", "Name": "Head"},
        ],
    }


def build_bundle(generator_root: Path, layer_dir: Path, output_dir: Path) -> list[Path]:
    """Compile one validated MOC, atlas, and minimal manifest from the common-canvas source stack."""

    modules = _load_generator(generator_root)
    stack = modules["decompose"].from_layer_dir(layer_dir)
    if (stack.canvas_width, stack.canvas_height) != (2048, 2048):
        raise ValueError(
            "Elysia Live2D layers must share the reviewed 2048 x 2048 common canvas."
        )
    rig = modules["pipeline"].rig_from_stack(stack, name="model", source=str(layer_dir))
    _normalize_face_render_order(rig)

    output_dir.mkdir(parents=True, exist_ok=True)
    texture_dir = output_dir / "textures"
    texture_dir.mkdir(exist_ok=True)
    atlas, uv_remap = modules["moc3_emit"].build_atlas(rig, layer_dir)
    atlas_path = texture_dir / "atlas.png"
    atlas.save(atlas_path, optimize=True)

    moc_path = output_dir / "model.moc3"
    moc_document = modules["moc3_emit"].rig_to_moc3(rig, atlas_uv=uv_remap)
    moc_path.write_bytes(modules["moc3_binary"].write_moc3(moc_document))

    manifest_path = output_dir / "model.model3.json"
    manifest_path.write_text(
        json.dumps(_manifest(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return [manifest_path, moc_path, atlas_path]


def main(argv: list[str] | None = None) -> int:
    """Run the pinned Elysia Live2D bundle compiler."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--generator-root",
        required=True,
        type=Path,
        help="Path to an image2live2d checkout at the pinned commit.",
    )
    parser.add_argument("--layers", type=Path, default=DEFAULT_LAYER_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    written = build_bundle(args.generator_root, args.layers, args.output)
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
