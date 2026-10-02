"""Rebuild Elysia's aligned Live2D facial source layers from the reviewed face master.

The runtime model uses a common-canvas layer stack rather than independent sprites.  This
authoring utility keeps the active eyes, lids, brows, nose, lips, cavity, and optional blush on
one measured coordinate system so a later MOC export cannot silently reintroduce the previous
childlike proportions or per-part offsets.  Pillow and NumPy are authoring-only dependencies;
the packaged application does not import this module.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MASTER = (
    PROJECT_ROOT / "data" / "characters" / "elysia-2dArt" / "live2d-face-master.png"
)
DEFAULT_LAYER_DIR = (
    PROJECT_ROOT / "data" / "characters" / "elysia-2dArt" / "live2d-source"
)

CANVAS_SIZE = 2048
MODEL_AXIS_X = 1024

_OLD_FACE_FILES = {
    "40_blush.png",
    "40_face_base.png",
    "41_blush.png",
    "45_eye_closed_l.png",
    "45_eye_l.png",
    "46_eye_closed_r.png",
    "46_eye_r.png",
    "47_eyebrow_l.png",
    "48_eyebrow_r.png",
    "49_nose.png",
    "50_mouth.png",
    "50_mouth_cavity.png",
    "51_mouth.png",
}


def _load_authoring_dependencies() -> tuple[Any, Any, Any, Any]:
    """Load optional image-authoring libraries with a focused installation error."""

    try:
        import numpy as np  # type: ignore[import-not-found]
        from PIL import Image, ImageDraw, ImageFilter  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on the local authoring environment.
        raise RuntimeError(
            "Live2D face authoring requires Pillow and NumPy; install them in the active "
            "authoring environment before running this script."
        ) from exc
    return np, Image, ImageDraw, ImageFilter


def _connected_skin_component(np: Any, rgba: Any) -> tuple[Any, Any]:
    """Return the reference face's skin component and a filled, hair-safe face silhouette.

    Colour thresholding alone also finds hands and chest skin.  Flooding from a reviewed point inside
    the face keeps this operation deterministic.  Row spans then close holes left by eyes, nose, and
    lips.  The forehead extension intentionally sits beneath the bangs: it prevents transparent seams
    when the head warp moves the face and front hair by sub-pixel amounts.
    """

    red, green, blue, alpha = [rgba[:, :, index].astype(np.int16) for index in range(4)]
    skin = (
        (alpha > 8)
        & (red > 205)
        & (green > 170)
        & (blue > 155)
        & (red >= green)
        & (green >= blue - 8)
        & ((red - blue) < 95)
    )

    seed_y, seed_x = 205, 630
    if not skin[seed_y, seed_x]:
        raise ValueError("The reviewed face seed no longer points to skin; master geometry changed.")

    connected = np.zeros(skin.shape, dtype=bool)
    connected[seed_y, seed_x] = True
    pending = [(seed_y, seed_x)]
    while pending:
        y, x = pending.pop()
        for delta_y, delta_x in (
            (1, 0),
            (-1, 0),
            (0, 1),
            (0, -1),
            (1, 1),
            (1, -1),
            (-1, 1),
            (-1, -1),
        ):
            next_y, next_x = y + delta_y, x + delta_x
            if (
                0 <= next_y < skin.shape[0]
                and 0 <= next_x < skin.shape[1]
                and skin[next_y, next_x]
                and not connected[next_y, next_x]
            ):
                connected[next_y, next_x] = True
                pending.append((next_y, next_x))

    silhouette = np.zeros(skin.shape, dtype=bool)
    for y in range(130, 226):
        xs = np.where(connected[y])[0]
        if len(xs):
            silhouette[y, xs.min() : xs.max() + 1] = True

    forehead_xs = np.where(connected[130])[0]
    if not len(forehead_xs):
        raise ValueError("The reviewed face master has no forehead span at y=130.")
    for y in range(88, 130):
        silhouette[y, forehead_xs.min() : forehead_xs.max() + 1] = True
    return connected, silhouette


def _inpaint_face_base(
    np: Any, image_module: Any, source_image: Any, image_filter: Any
) -> tuple[Any, Any, Any]:
    """Remove active facial features while retaining the master skin shading and V-shaped jaw."""

    rgba = np.array(source_image)
    connected, silhouette = _connected_skin_component(np, rgba)
    mask_image = image_module.fromarray((silhouette * 255).astype("uint8")).filter(
        image_filter.GaussianBlur(0.65)
    )
    face_mask = np.array(mask_image) > 16

    # Some pale eye whites and the subtle nose pass the skin threshold.  Excluding every active-feature
    # rectangle here ensures those pixels live only in their own movable layer.
    feature_region = np.zeros(face_mask.shape, dtype=bool)
    feature_region[140:168, 578:681] = True
    feature_region[170:193, 620:639] = True
    feature_region[194:210, 610:647] = True
    known = connected & face_mask & ~feature_region

    rgb = rgba[:, :, :3].astype(np.float64)
    filled = np.zeros_like(rgb)
    filled[known] = rgb[known]
    valid = known.copy()
    unresolved = face_mask & ~valid

    # Harmonic diffusion is deliberately local and deterministic.  It retains the original face's
    # soft illumination without smearing any feature texture into the neutral skin layer.
    for _ in range(220):
        if not unresolved.any():
            break
        sums = np.zeros_like(filled)
        counts = np.zeros(face_mask.shape, dtype=float)
        for delta_y, delta_x in (
            (1, 0),
            (-1, 0),
            (0, 1),
            (0, -1),
            (1, 1),
            (1, -1),
            (-1, 1),
            (-1, -1),
        ):
            source_y = slice(max(0, -delta_y), min(face_mask.shape[0], face_mask.shape[0] - delta_y))
            source_x = slice(max(0, -delta_x), min(face_mask.shape[1], face_mask.shape[1] - delta_x))
            target_y = slice(max(0, delta_y), min(face_mask.shape[0], face_mask.shape[0] + delta_y))
            target_x = slice(max(0, delta_x), min(face_mask.shape[1], face_mask.shape[1] + delta_x))
            neighbour_valid = valid[source_y, source_x]
            sums[target_y, target_x] += filled[source_y, source_x] * neighbour_valid[..., None]
            counts[target_y, target_x] += neighbour_valid
        newly_resolved = unresolved & (counts > 0)
        filled[newly_resolved] = sums[newly_resolved] / counts[newly_resolved, None]
        valid[newly_resolved] = True
        unresolved[newly_resolved] = False

    filled[face_mask & ~valid] = np.median(rgb[known], axis=0)
    face_rgba = np.zeros_like(rgba)
    face_rgba[:, :, :3] = np.clip(filled, 0, 255).astype("uint8")
    face_rgba[:, :, 3] = np.array(mask_image)

    # Only the lower edge receives an outline; the upper edge remains hidden beneath the fringe.
    inner = np.array(mask_image.filter(image_filter.MinFilter(3)))
    edge = np.clip(np.array(mask_image, dtype=np.int16) - inner.astype(np.int16), 0, 255)
    edge *= np.indices(edge.shape)[0] >= 150
    edge_alpha = (edge.astype(np.float32) / 255.0)[:, :, None]
    outline = np.array([190, 111, 122], dtype=np.float32)
    face_rgba[:, :, :3] = np.clip(
        face_rgba[:, :, :3] * (1 - edge_alpha * 0.58) + outline * edge_alpha * 0.58,
        0,
        255,
    ).astype("uint8")
    return image_module.fromarray(face_rgba, "RGBA"), rgba, face_mask


def _map_to_common_canvas(image_module: Any, source: Any) -> Any:
    """Map reviewed face coordinates onto the model's 2048-square common canvas."""

    # Generated master landmarks: face axis x=629, iris line y=155.  Target landmarks after this map:
    # axis x=1024, iris line y=286 (143 at the former 1024 canvas), and mouth centre y=380.
    scale_x = 1.07
    inverse = (1 / (2 * scale_x), 0, 629 - 512 / scale_x, 0, 0.5, 12)
    return source.transform(
        (CANVAS_SIZE, CANVAS_SIZE),
        image_module.Transform.AFFINE,
        inverse,
        resample=image_module.Resampling.BICUBIC,
    )


def _line_layer(image_module: Any, image_draw: Any, points: list[tuple[int, int]], color: tuple[int, ...], width: int) -> Any:
    """Draw one anti-aliased feature line in legacy 1024-canvas coordinates."""

    scratch = image_module.new("RGBA", (4096, 4096), (0, 0, 0, 0))
    image_draw.Draw(scratch).line(
        [(x * 4, y * 4) for x, y in points],
        fill=color,
        width=width * 4,
        joint="curve",
    )
    return scratch.resize((CANVAS_SIZE, CANVAS_SIZE), image_module.Resampling.LANCZOS)


def build_face_layers(master_path: Path, layer_dir: Path, preview_path: Path | None = None) -> list[Path]:
    """Write the aligned 2048-square facial layers and return every output path.

    Existing non-face source layers are enlarged once from 1024 to 2048 so all parts retain one canvas
    and origin.  Re-running the command is idempotent: already-2048 non-face layers are left unchanged.
    """

    np, image, image_draw, image_filter = _load_authoring_dependencies()
    master = image.open(master_path).convert("RGBA")
    if master.size != (1254, 1254):
        raise ValueError(f"Unexpected face-master size {master.size}; expected 1254 x 1254.")

    layer_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(layer_dir.glob("*.png")):
        if path.name in _OLD_FACE_FILES:
            path.unlink()
            continue
        current = image.open(path).convert("RGBA")
        if current.size == (1024, 1024):
            current.resize((CANVAS_SIZE, CANVAS_SIZE), image.Resampling.LANCZOS).save(
                path, optimize=True
            )
        elif current.size != (CANVAS_SIZE, CANVAS_SIZE):
            raise ValueError(f"Unexpected common-canvas size for {path}: {current.size}")

    face_source, master_rgba, face_mask = _inpaint_face_base(
        np, image, master, image_filter
    )
    outputs: dict[str, Any] = {
        "40_face_base.png": _map_to_common_canvas(image, face_source),
    }

    def open_eye(points: list[tuple[int, int]]) -> Any:
        mask = image.new("L", master.size, 0)
        image_draw.Draw(mask).polygon(points, fill=255)
        mask = mask.filter(image_filter.GaussianBlur(0.55))
        eye = master.copy()
        eye.putalpha(
            image.fromarray(
                np.minimum(np.array(mask), master_rgba[:, :, 3]).astype("uint8")
            )
        )
        return _map_to_common_canvas(image, eye)

    outputs["45_eye_l.png"] = open_eye(
        [(582, 151), (588, 146), (598, 144), (609, 144), (618, 149), (614, 157), (605, 163), (594, 164), (586, 159)]
    )
    outputs["46_eye_r.png"] = open_eye(
        [(642, 149), (649, 144), (660, 143), (670, 145), (677, 151), (672, 158), (662, 163), (651, 162), (645, 157)]
    )
    outputs["45_eye_closed_l.png"] = _line_layer(
        image, image_draw, [(466, 149), (474, 145), (480, 143), (487, 145), (497, 150)], (112, 73, 82, 245), 1
    )
    outputs["46_eye_closed_r.png"] = _line_layer(
        image, image_draw, [(527, 150), (537, 145), (544, 143), (551, 145), (559, 149)], (112, 73, 82, 245), 1
    )
    outputs["47_eyebrow_l.png"] = _line_layer(
        image, image_draw, [(466, 138), (477, 134), (490, 135), (499, 139)], (197, 116, 145, 218), 1
    )
    outputs["48_eyebrow_r.png"] = _line_layer(
        image, image_draw, [(525, 139), (534, 135), (547, 134), (558, 138)], (197, 116, 145, 218), 1
    )
    outputs["49_nose.png"] = _line_layer(
        image, image_draw, [(512, 166), (510, 174), (514, 176)], (222, 150, 145, 170), 1
    )
    outputs["51_mouth.png"] = _line_layer(
        image, image_draw, [(500, 189), (506, 191), (512, 192), (518, 191), (524, 189)], (169, 83, 101, 225), 1
    )

    cavity = image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    cavity_draw = image_draw.Draw(cavity)
    cavity_draw.ellipse((994, 371, 1054, 413), fill=(66, 30, 39, 255))
    cavity_draw.ellipse((1002, 395, 1046, 411), fill=(201, 93, 120, 220))
    outputs["50_mouth_cavity.png"] = cavity

    blush = image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    blush_draw = image_draw.Draw(blush)
    blush_draw.ellipse((918, 326, 966, 350), fill=(238, 126, 155, 58))
    blush_draw.ellipse((1082, 326, 1130, 350), fill=(238, 126, 155, 58))
    outputs["41_blush.png"] = blush.filter(image_filter.GaussianBlur(7))

    written: list[Path] = []
    for name, layer in outputs.items():
        destination = layer_dir / name
        layer.save(destination, optimize=True)
        written.append(destination)

    if preview_path is not None:
        preview = image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
        for path in sorted(layer_dir.glob("[0-9][0-9]_*.png")):
            # The cavity is geometrically collapsed by ParamMouthOpenY=0 in the MOC.  Omitting it here
            # makes the flat review image honestly represent the runtime's neutral pose.
            if path.name == "50_mouth_cavity.png":
                continue
            preview.alpha_composite(image.open(path).convert("RGBA"))
        preview.resize((1024, 1024), image.Resampling.LANCZOS).save(preview_path)
    return written


def main(argv: list[str] | None = None) -> int:
    """Run the deterministic face-layer authoring command."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", type=Path, default=DEFAULT_MASTER)
    parser.add_argument("--layers", type=Path, default=DEFAULT_LAYER_DIR)
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args(argv)
    written = build_face_layers(args.master, args.layers, args.preview)
    print(f"Wrote {len(written)} aligned facial layers to {args.layers}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
