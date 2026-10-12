"""Validate and canonicalize bounded PNG output from an untrusted runtime.

ComfyUI output is fetched over HTTP and never treated as a trusted local path.
The first version accepts only non-interlaced 8-bit RGB/RGBA PNG.  It verifies
every chunk CRC, decompresses the complete scanline stream under an exact
pixel-derived ceiling, then writes a metadata-free canonical PNG.  This avoids
passing arbitrary ancillary chunks or compressed bombs to the renderer.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct
from typing import Final
import zlib

from .exceptions import (
    ImageGenerationLimitError,
    ImageGenerationProtocolError,
)
from .profiles import MAX_IMAGE_EDGE_PIXELS, MAX_IMAGE_PIXELS


PNG_MEDIA_TYPE: Final = "image/png"
PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
MAX_GENERATED_PNG_BYTES: Final = 32 * 1024 * 1024
MAX_PNG_CHUNKS: Final = 4_096
_IHDR = b"IHDR"
_IDAT = b"IDAT"
_IEND = b"IEND"
_STRIPPABLE_ANCILLARY_CHUNKS: Final = frozenset({
    b"eXIf",
    b"iTXt",
    b"tEXt",
    b"tIME",
    b"zTXt",
})


@dataclass(frozen=True, slots=True)
class CanonicalPng:
    """Carry one sanitized generated image and its path-free identity."""

    data: bytes
    width: int
    height: int
    sha256: str
    media_type: str = PNG_MEDIA_TYPE


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    """Encode one canonical PNG chunk with a newly calculated CRC."""

    checksum = zlib.crc32(chunk_type)
    checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", checksum)
    )


def _decompress_scanlines(compressed: bytes, expected_bytes: int) -> bytes:
    """Expand IDAT data without allowing output beyond the canvas budget."""

    decompressor = zlib.decompressobj()
    try:
        raw = decompressor.decompress(compressed, expected_bytes + 1)
        if len(raw) > expected_bytes or decompressor.unconsumed_tail:
            raise ImageGenerationLimitError(
                "Generated PNG expands beyond its declared pixel canvas."
            )
        if not decompressor.eof:
            remaining = expected_bytes + 1 - len(raw)
            raw += decompressor.flush(remaining)
    except zlib.error as error:
        raise ImageGenerationProtocolError(
            "Local image runtime returned invalid PNG compression."
        ) from error
    if (
        len(raw) != expected_bytes
        or not decompressor.eof
        or decompressor.unused_data
        or decompressor.unconsumed_tail
    ):
        raise ImageGenerationProtocolError(
            "Local image runtime returned an incomplete PNG scanline stream."
        )
    return raw


def canonicalize_png(
    value: object,
    *,
    expected_width: int | None = None,
    expected_height: int | None = None,
) -> CanonicalPng:
    """Return a minimal verified PNG or reject malformed runtime output.

    Expected dimensions bind the downloaded bytes to the admitted workflow.
    Callers may omit them only for isolated validation utilities; generation
    paths should always pass both values.
    """

    if type(value) is not bytes:
        raise ImageGenerationProtocolError("Generated image bytes are invalid.")
    data = value
    if not data or len(data) > MAX_GENERATED_PNG_BYTES:
        raise ImageGenerationLimitError(
            "Generated PNG exceeds its fixed encoded-byte budget."
        )
    if not data.startswith(PNG_SIGNATURE):
        raise ImageGenerationProtocolError(
            "Local image runtime did not return a PNG image."
        )

    offset = len(PNG_SIGNATURE)
    chunk_count = 0
    width = 0
    height = 0
    channels = 0
    ihdr_payload: bytes | None = None
    idat_parts: list[bytes] = []
    saw_idat = False
    ended_idat = False
    saw_iend = False
    while offset < len(data):
        chunk_count += 1
        if chunk_count > MAX_PNG_CHUNKS:
            raise ImageGenerationLimitError(
                "Generated PNG contains too many chunks."
            )
        if len(data) - offset < 12:
            raise ImageGenerationProtocolError(
                "Generated PNG has a truncated chunk header."
            )
        length = struct.unpack_from(">I", data, offset)[0]
        chunk_type = data[offset + 4:offset + 8]
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            raise ImageGenerationProtocolError(
                "Generated PNG has a truncated chunk body."
            )
        payload = data[offset + 8:offset + 8 + length]
        if len(chunk_type) != 4 or not all(
            65 <= byte <= 90 or 97 <= byte <= 122
            for byte in chunk_type
        ):
            raise ImageGenerationProtocolError(
                "Generated PNG contains an invalid chunk type."
            )
        recorded_crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
        actual_crc = zlib.crc32(chunk_type)
        actual_crc = zlib.crc32(payload, actual_crc) & 0xFFFFFFFF
        if actual_crc != recorded_crc:
            raise ImageGenerationProtocolError(
                "Generated PNG contains a corrupt chunk."
            )

        if chunk_count == 1 and chunk_type != _IHDR:
            raise ImageGenerationProtocolError(
                "Generated PNG must begin with IHDR."
            )
        if chunk_type == _IHDR:
            if ihdr_payload is not None or length != 13:
                raise ImageGenerationProtocolError(
                    "Generated PNG has an invalid IHDR chunk."
                )
            (
                width,
                height,
                bit_depth,
                color_type,
                compression,
                filter_method,
                interlace,
            ) = struct.unpack(">IIBBBBB", payload)
            if (
                not 1 <= width <= MAX_IMAGE_EDGE_PIXELS
                or not 1 <= height <= MAX_IMAGE_EDGE_PIXELS
                or width * height > MAX_IMAGE_PIXELS
            ):
                raise ImageGenerationLimitError(
                    "Generated PNG exceeds its fixed pixel budget."
                )
            if (
                bit_depth != 8
                or color_type not in (2, 6)
                or compression != 0
                or filter_method != 0
                or interlace != 0
            ):
                raise ImageGenerationProtocolError(
                    "Generated PNG uses an unsupported pixel format."
                )
            channels = 3 if color_type == 2 else 4
            ihdr_payload = payload
        elif chunk_type == _IDAT:
            if ihdr_payload is None or ended_idat or saw_iend:
                raise ImageGenerationProtocolError(
                    "Generated PNG has invalid IDAT ordering."
                )
            saw_idat = True
            idat_parts.append(payload)
        elif chunk_type == _IEND:
            if not saw_idat or saw_iend or length != 0:
                raise ImageGenerationProtocolError(
                    "Generated PNG has an invalid IEND chunk."
                )
            saw_iend = True
            offset = chunk_end
            if offset != len(data):
                raise ImageGenerationProtocolError(
                    "Generated PNG contains bytes after IEND."
                )
            break
        else:
            if saw_idat:
                ended_idat = True
            # PNG reserves uppercase first letters for critical chunks.  Only
            # inert textual/time metadata is safe to discard.  Color,
            # transparency, background, and physical-display chunks affect
            # rendering semantics, so stripping them would change the image.
            if chunk_type[:1].isalpha() and chunk_type[:1].isupper():
                raise ImageGenerationProtocolError(
                    "Generated PNG contains an unsupported critical chunk."
                )
            if chunk_type not in _STRIPPABLE_ANCILLARY_CHUNKS:
                raise ImageGenerationProtocolError(
                    "Generated PNG contains an unsupported ancillary chunk."
                )
        offset = chunk_end

    if ihdr_payload is None or not saw_idat or not saw_iend:
        raise ImageGenerationProtocolError(
            "Generated PNG is missing required chunks."
        )
    if expected_width is not None and width != expected_width:
        raise ImageGenerationProtocolError(
            "Generated PNG width does not match the admitted workflow."
        )
    if expected_height is not None and height != expected_height:
        raise ImageGenerationProtocolError(
            "Generated PNG height does not match the admitted workflow."
        )
    if (expected_width is None) != (expected_height is None):
        raise ImageGenerationProtocolError(
            "Expected PNG dimensions must be supplied together."
        )

    expected_raw_bytes = height * (1 + width * channels)
    raw = _decompress_scanlines(b"".join(idat_parts), expected_raw_bytes)
    row_bytes = 1 + width * channels
    for row_start in range(0, len(raw), row_bytes):
        if raw[row_start] > 4:
            raise ImageGenerationProtocolError(
                "Generated PNG contains an invalid scanline filter."
            )

    canonical = (
        PNG_SIGNATURE
        + _png_chunk(_IHDR, ihdr_payload)
        + _png_chunk(_IDAT, zlib.compress(raw, level=9))
        + _png_chunk(_IEND, b"")
    )
    if len(canonical) > MAX_GENERATED_PNG_BYTES:
        raise ImageGenerationLimitError(
            "Canonical PNG exceeds its fixed encoded-byte budget."
        )
    return CanonicalPng(
        data=canonical,
        width=width,
        height=height,
        sha256=hashlib.sha256(canonical).hexdigest(),
    )


__all__ = [
    "CanonicalPng",
    "MAX_GENERATED_PNG_BYTES",
    "MAX_PNG_CHUNKS",
    "PNG_MEDIA_TYPE",
    "PNG_SIGNATURE",
    "canonicalize_png",
]
