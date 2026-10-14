"""Validate bounded visual evidence for advisory Work prompts without granting tool authority.

Only text and image provenance cross the desktop Backend boundary. Pixel bytes,
native paths, capture grants, and model-generated actions are deliberately absent.
The desktop resolves the user's short-lived review receipt before this validator;
this object can enrich advice but never replace a plan's explicit tool calls.
"""

from __future__ import annotations

import json
import re
from typing import Any

from work_tools.contracts import ToolProtocolError

# Match JavaScript String.trim at the native wire boundary. Python's default
# strip treats U+0085 as whitespace and omits FEFF, producing different contracts.
_WIRE_WHITESPACE = (
    "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
    "\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)


def _record(value: object, fields: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise ToolProtocolError()
    return value


def _text(value: object, maximum: int, *, empty: bool = False) -> str:
    if type(value) is not str or (not empty and not value.strip(_WIRE_WHITESPACE)):
        raise ToolProtocolError()
    if any(ord(character) < 32 and character not in "\t\n\r" or ord(character) == 127 for character in value):
        raise ToolProtocolError()
    try:
        if len(value.encode("utf-8")) > maximum:
            raise ToolProtocolError()
    except UnicodeError:
        raise ToolProtocolError() from None
    return value


def validate_vision_context(value: object) -> dict[str, Any]:
    """Detach exact image/OCR/inference/user fields within one 32 KiB advisory envelope.

    Inference remains labelled as inference, even when it contains convincing
    permission claims. No field supplies a workspace path, tool call, or approval.
    """

    context = _record(value, {"imageReference", "ocrExtraction", "modelInference", "userDescription"})
    image = _record(context["imageReference"], {"imageId", "sha256", "width", "height", "source"})
    if (type(image["imageId"]) is not str or re.fullmatch(r"vision_[0-9a-f]{32}", image["imageId"]) is None
        or type(image["sha256"]) is not str or re.fullmatch(r"[0-9a-f]{64}", image["sha256"]) is None):
        raise ToolProtocolError()
    width, height = image["width"], image["height"]
    if type(width) is not int or type(height) is not int or not 1 <= width <= 8192 or not 1 <= height <= 8192 or width * height > 16 * 1024 * 1024:
        raise ToolProtocolError()
    source = _record(image["source"], {"kind", "label"})
    if type(source["kind"]) is not str or source["kind"] not in {"file", "display", "window", "region"}:
        raise ToolProtocolError()
    _text(source["label"], 1024)
    ocr = context["ocrExtraction"]
    if ocr is not None:
        ocr = _record(ocr, {"language", "text", "truncated"})
        if type(ocr["language"]) is not str or ocr["language"] not in {"eng", "chi_sim+eng", "chi_tra+eng"} or type(ocr["truncated"]) is not bool:
            raise ToolProtocolError()
        _text(ocr["text"], 8192, empty=True)
    inference = context["modelInference"]
    if inference is not None:
        inference = _record(inference, {"model", "question", "text", "truncated"})
        _text(inference["model"], 256)
        _text(inference["question"], 4096)
        _text(inference["text"], 8192, empty=True)
        if type(inference["truncated"]) is not bool:
            raise ToolProtocolError()
    _text(context["userDescription"], 4096, empty=True)
    encoded = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 32 * 1024:
        raise ToolProtocolError()
    # The provider receives its own tree: mutating the UI request cannot alter
    # a running plan's evidence after it has passed review and admission.
    detached: dict[str, Any] = json.loads(encoded)
    return detached
