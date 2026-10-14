"""Exercise the closed source-singing request and independent asset trust gate."""

import json
from pathlib import Path

import pytest

from scripts import song_svc_assets as assets
from scripts import song_svs_runtime as runtime


@pytest.mark.parametrize("reference", ["fairy", "elysia"])
@pytest.mark.parametrize("shift", [-12, 0, 12])
def test_accepts_only_reviewed_reference_and_key(reference: str, shift: int) -> None:
    """Retain the exact explicit preset and endpoints without automatic key changes."""
    request = assets.parse_singing_request(json.dumps({
        "schema_version": 1, "reference": reference, "key_shift_semitones": shift,
    }).encode())
    assert request.reference == reference
    assert request.key_shift_semitones == shift


@pytest.mark.parametrize("raw", [
    b"{}", b"[]", b"null", b"x" * 4097,
    b'{"schema_version":1,"reference":"fairy","reference":"elysia","key_shift_semitones":0}',
    b'{"schema_version":1,"reference":"fairy","key_shift_semitones":NaN}',
    b'{"schema_version":true,"reference":"fairy","key_shift_semitones":0}',
    b'{"schema_version":1,"reference":[],"key_shift_semitones":0}',
    b'{"schema_version":1,"reference":"../private","key_shift_semitones":0}',
    b'{"schema_version":1,"reference":"fairy","key_shift_semitones":true}',
    b'{"schema_version":1,"reference":"fairy","key_shift_semitones":12.0}',
    b'{"schema_version":1,"reference":"fairy","key_shift_semitones":13}',
    b'{"schema_version":1,"reference":"fairy","key_shift_semitones":0,"cfg":3}',
])
def test_rejects_ambiguous_or_unbounded_requests(raw: bytes) -> None:
    """Refuse parser ambiguity, path injection, and unsupported inference controls."""
    with pytest.raises(ValueError):
        assets.parse_singing_request(raw)


def test_every_asset_is_fixed_and_references_are_private() -> None:
    """Ensure the SVC model and two selected waveform/F0 pairs have exact pins."""
    records = {item[0]: item[1:] for item in assets._ASSETS}
    assert {"model", "rmvpe", "whisper_weights", "reference_fairy",
            "reference_elysia", "reference_f0_fairy", "reference_f0_elysia"} <= records.keys()
    for key, (relative, size, digest) in records.items():
        assert not Path(relative).is_absolute() and ".." not in Path(relative).parts
        assert size > 0 and len(digest) == 64 and int(digest, 16) >= 0
        if key.startswith("reference_"):
            assert relative.startswith("prompts/singing-v1/")


def test_mismatched_asset_bytes_fail_before_model_loading(tmp_path: Path) -> None:
    """The shared digest gate refuses a same-size weight replacement."""
    candidate = tmp_path / "weights.bin"
    candidate.write_bytes(b"bad!")
    with pytest.raises(runtime._SvsRuntimeFailure, match="reviewed version"):
        runtime._require_asset(
            tmp_path, runtime._AssetSpec("weights.bin", 4, "0" * 64),
            require_single_link=True,
        )
