"""Validate Elysia's common-canvas Live2D facial landmarks without image libraries.

The face layers are intentionally checked in CI even though the packaged model is also pinned by
hash.  A valid replacement bundle could otherwise preserve all runtime contracts while silently
moving an eye, nose, or mouth.  This checker decodes only the PNG alpha channel with the standard
library, measures reviewed landmarks, and rejects both per-part drift and whole-face translation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct
import sys
from typing import Final
import zlib


PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_LAYER_DIRECTORY: Final = (
    PROJECT_ROOT / "data" / "characters" / "elysia-2dArt" / "live2d-source"
)
CANVAS_SIZE: Final = 2048
FACE_AXIS_X: Final = 1024.0
ALPHA_THRESHOLD: Final = 8

_EXPECTED_LAYER_NAMES: Final = frozenset(
    {
        "00_hair_back.png",
        "10_leg_l.png",
        "11_leg_r.png",
        "20_torso.png",
        "30_arm_l.png",
        "31_hand_l.png",
        "32_arm_r.png",
        "33_hand_r.png",
        "40_face_base.png",
        "41_blush.png",
        "45_eye_closed_l.png",
        "45_eye_l.png",
        "46_eye_closed_r.png",
        "46_eye_r.png",
        "47_eyebrow_l.png",
        "48_eyebrow_r.png",
        "49_nose.png",
        "50_mouth_cavity.png",
        "51_mouth.png",
        "60_hair_front.png",
        "70_accessory.png",
    }
)
_MEASURED_LAYER_NAMES: Final = frozenset(
    {
        "40_face_base.png",
        "45_eye_closed_l.png",
        "45_eye_l.png",
        "46_eye_closed_r.png",
        "46_eye_r.png",
        "47_eyebrow_l.png",
        "48_eyebrow_r.png",
        "49_nose.png",
        "50_mouth_cavity.png",
        "51_mouth.png",
    }
)
_REVIEWED_CENTRES: Final[dict[str, tuple[float, float, float]]] = {
    # The final number is the maximum Euclidean drift in source pixels.  These tolerances allow
    # lossless PNG encoder changes while requiring an intentional review for visible art movement.
    "45_eye_l.png": (962.0, 283.0, 4.0),
    "46_eye_r.png": (1089.0, 282.0, 4.0),
    "45_eye_closed_l.png": (964.0, 292.0, 4.0),
    "46_eye_closed_r.png": (1087.0, 292.0, 4.0),
    "47_eyebrow_l.png": (965.0, 272.0, 4.0),
    "48_eyebrow_r.png": (1083.0, 272.0, 4.0),
    "49_nose.png": (1023.0, 344.0, 3.0),
    "50_mouth_cavity.png": (1024.0, 392.0, 3.0),
    "51_mouth.png": (1024.0, 381.0, 3.0),
}


@dataclass(frozen=True)
class LayerGeometry:
    """Describe one visible alpha region on the shared Live2D source canvas."""

    width: int
    height: int
    left: int
    top: int
    right: int
    bottom: int
    center_x: float
    center_y: float


def _paeth_predictor(left: int, above: int, upper_left: int) -> int:
    """Return the PNG Paeth prediction for one alpha-channel sample."""

    prediction = left + above - upper_left
    left_distance = abs(prediction - left)
    above_distance = abs(prediction - above)
    upper_left_distance = abs(prediction - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def _read_png_chunks(path: Path) -> tuple[int, int, bytes]:
    """Read one constrained 8-bit RGBA PNG and return its dimensions and compressed payload."""

    payload = path.read_bytes()
    if payload[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path.name} is not a PNG file")

    width = height = 0
    idat = bytearray()
    position = 8
    saw_end = False
    while position + 12 <= len(payload):
        length = struct.unpack(">I", payload[position : position + 4])[0]
        chunk_type = payload[position + 4 : position + 8]
        chunk_end = position + 12 + length
        if chunk_end > len(payload):
            raise ValueError(f"{path.name} contains a truncated PNG chunk")
        chunk_data = payload[position + 8 : position + 8 + length]
        if chunk_type == b"IHDR":
            if length != 13:
                raise ValueError(f"{path.name} has an invalid IHDR")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", chunk_data
            )
            if (depth, color, compression, filtering, interlace) != (8, 6, 0, 0, 0):
                raise ValueError(
                    f"{path.name} must be a non-interlaced 8-bit RGBA PNG"
                )
        elif chunk_type == b"IDAT":
            idat.extend(chunk_data)
        elif chunk_type == b"IEND":
            saw_end = True
            break
        position = chunk_end

    if not width or not height or not idat or not saw_end:
        raise ValueError(f"{path.name} is missing required PNG chunks")
    return width, height, bytes(idat)


def _measure_alpha(path: Path) -> LayerGeometry:
    """Decode only RGBA alpha samples and measure the visible region above the review threshold.

    PNG filters never mix colour channels: their horizontal neighbour is four bytes away for RGBA.
    Reconstructing only every alpha byte therefore produces the exact alpha plane while avoiding
    roughly three quarters of the work and memory of a general-purpose image decoder.
    """

    width, height, compressed = _read_png_chunks(path)
    decompressed = zlib.decompress(compressed)
    row_bytes = width * 4
    expected_bytes = height * (row_bytes + 1)
    if len(decompressed) != expected_bytes:
        raise ValueError(f"{path.name} has an unexpected decompressed size")

    previous = bytearray(width)
    left_bound, top_bound = width, height
    right_bound = bottom_bound = 0
    x_total = y_total = visible_count = 0

    for y in range(height):
        row_start = y * (row_bytes + 1)
        filter_type = decompressed[row_start]
        if filter_type > 4:
            raise ValueError(f"{path.name} uses unsupported PNG filter {filter_type}")
        filtered_row = decompressed[row_start + 1 : row_start + 1 + row_bytes]
        current = bytearray(width)
        left = 0
        upper_left = 0
        for x in range(width):
            filtered_alpha = filtered_row[x * 4 + 3]
            above = previous[x]
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            else:
                predictor = _paeth_predictor(left, above, upper_left)
            alpha = (filtered_alpha + predictor) & 0xFF
            current[x] = alpha
            if alpha > ALPHA_THRESHOLD:
                left_bound = min(left_bound, x)
                top_bound = min(top_bound, y)
                right_bound = max(right_bound, x + 1)
                bottom_bound = max(bottom_bound, y + 1)
                x_total += x
                y_total += y
                visible_count += 1
            left = alpha
            upper_left = above
        previous = current

    if not visible_count:
        raise ValueError(f"{path.name} has no reviewed visible pixels")
    return LayerGeometry(
        width=width,
        height=height,
        left=left_bound,
        top=top_bound,
        right=right_bound,
        bottom=bottom_bound,
        center_x=x_total / visible_count,
        center_y=y_total / visible_count,
    )


def _pair_problem(
    geometries: dict[str, LayerGeometry], left_name: str, right_name: str, label: str
) -> list[str]:
    """Return symmetry problems for one reviewed left/right facial-feature pair."""

    left = geometries[left_name]
    right = geometries[right_name]
    problems: list[str] = []
    midpoint = (left.center_x + right.center_x) / 2
    if abs(midpoint - FACE_AXIS_X) > 2:
        problems.append(f"{label} midpoint {midpoint:.2f} is not on x={FACE_AXIS_X:.0f}")
    if abs(left.center_y - right.center_y) > 2:
        problems.append(
            f"{label} vertical mismatch is {abs(left.center_y - right.center_y):.2f}px"
        )
    return problems


def audit_face_alignment(layer_directory: Path = DEFAULT_LAYER_DIRECTORY) -> tuple[str, ...]:
    """Return every source-layer geometry violation, or an empty tuple for the reviewed stack."""

    problems: list[str] = []
    paths = {path.name: path for path in layer_directory.glob("*.png")}
    missing = sorted(_EXPECTED_LAYER_NAMES - paths.keys())
    unexpected = sorted(paths.keys() - _EXPECTED_LAYER_NAMES)
    if missing:
        problems.append(f"missing source layers: {', '.join(missing)}")
    if unexpected:
        problems.append(f"unexpected source layers: {', '.join(unexpected)}")

    geometries: dict[str, LayerGeometry] = {}
    for name, path in sorted(paths.items()):
        try:
            width, height, _compressed = _read_png_chunks(path)
        except (OSError, ValueError, struct.error) as exc:
            problems.append(str(exc))
            continue
        if (width, height) != (CANVAS_SIZE, CANVAS_SIZE):
            problems.append(
                f"{name} is {width}x{height}; expected {CANVAS_SIZE}x{CANVAS_SIZE}"
            )
        if name in _MEASURED_LAYER_NAMES:
            try:
                geometries[name] = _measure_alpha(path)
            except (OSError, ValueError, zlib.error) as exc:
                problems.append(str(exc))

    if geometries.keys() != _MEASURED_LAYER_NAMES:
        return tuple(problems)

    for left_name, right_name, label in (
        ("45_eye_l.png", "46_eye_r.png", "open eyes"),
        ("45_eye_closed_l.png", "46_eye_closed_r.png", "closed eyes"),
        ("47_eyebrow_l.png", "48_eyebrow_r.png", "eyebrows"),
    ):
        problems.extend(_pair_problem(geometries, left_name, right_name, label))

    for name, (expected_x, expected_y, tolerance) in _REVIEWED_CENTRES.items():
        actual = geometries[name]
        distance = (
            (actual.center_x - expected_x) ** 2 + (actual.center_y - expected_y) ** 2
        ) ** 0.5
        if distance > tolerance:
            problems.append(
                f"{name} moved {distance:.2f}px from its reviewed landmark "
                f"({expected_x:.0f}, {expected_y:.0f})"
            )

    face = geometries["40_face_base.png"]
    for name in _MEASURED_LAYER_NAMES - {"40_face_base.png"}:
        feature = geometries[name]
        if not (
            face.left <= feature.left
            and feature.right <= face.right
            and face.top <= feature.top
            and feature.bottom <= face.bottom
        ):
            problems.append(f"{name} extends outside the face silhouette bounds")

    eye_y = (
        geometries["45_eye_l.png"].center_y + geometries["46_eye_r.png"].center_y
    ) / 2
    brow_y = (
        geometries["47_eyebrow_l.png"].center_y
        + geometries["48_eyebrow_r.png"].center_y
    ) / 2
    nose_y = geometries["49_nose.png"].center_y
    mouth_y = geometries["51_mouth.png"].center_y
    if not brow_y < eye_y < nose_y < mouth_y:
        problems.append("facial landmarks are not ordered brow, eye, nose, mouth")

    cavity = geometries["50_mouth_cavity.png"]
    mouth = geometries["51_mouth.png"]
    if abs(cavity.center_x - mouth.center_x) > 1:
        problems.append("mouth line and cavity do not share the same horizontal centre")
    if not cavity.top <= mouth.center_y <= cavity.bottom:
        problems.append("mouth line no longer crosses the mouth-cavity opening")
    return tuple(problems)


def main() -> int:
    """Print alignment violations and return a non-zero status when review fails."""

    problems = audit_face_alignment()
    if problems:
        for problem in problems:
            print(f"Live2D face alignment: {problem}", file=sys.stderr)
        return 1
    print("Live2D face alignment is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
