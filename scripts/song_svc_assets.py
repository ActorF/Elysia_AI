"""Authenticate the fixed offline singing-conversion assets and request.

Public model weights live in the existing private WSL runtime, while the two
user-selected singing excerpts remain private prompt data. The renderer selects
only a stable reference ID and an integer key; it never supplies model paths,
arbitrary inference settings, or source-song-specific repair instructions.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class SingingRequest:
    """Hold the bounded singing preset and F0-only transposition for one job."""

    reference: str
    key_shift_semitones: int


_ASSETS = (
    ("model", "models/SoulX-SVC/model-svc.pt", 2_793_965_154,
     "38e72d9e15be6eef3cc937daaf81421a7b5b546816786530bb9aa346cee5d43c"),
    ("whisper_config", "models/SoulX-SVC/whisper-base/config.json", 1983,
     "a153c53883a6799b6f056b4a8d1a515c9926d03994682ba88a7616618d7da0c1"),
    ("whisper_generation", "models/SoulX-SVC/whisper-base/generation_config.json", 3807,
     "444b3f636d2fff89dd9ecf549e2a085b61f7ff0fa0246d4628bac6a3b8cc9ba4"),
    ("whisper_weights", "models/SoulX-SVC/whisper-base/model.safetensors", 290_403_936,
     "07cadb9f25677c8d50df603e66a98fbd842cce45047139baeb16e6219a1e807b"),
    ("whisper_preprocessor", "models/SoulX-SVC/whisper-base/preprocessor_config.json", 184_990,
     "9b5cd03a36fbb8a627c64d98a5b5b126ead95a77720723944487311f0110b666"),
    ("rmvpe", "models/SoulX-Singer-Preprocess/rmvpe/rmvpe.pt", 181_184_272,
     "6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193"),
    ("reference_fairy", "prompts/singing-v1/fairy.wav", 720_102,
     "0f9709efcb73a5ea544d356d114956e55b968ecd255ca811f89f7291764f8bc7"),
    ("reference_elysia", "prompts/singing-v1/elysia.wav", 720_102,
     "46356e5b54e11624449a922952ea4823f372094fc10f07f7048943cc2e794d7a"),
    ("reference_f0_fairy", "prompts/singing-v1/fairy.npy", 4128,
     "c1721dcd80f62ddb4d1c388516a8e1bab3eadd1b3577ebea05e34ddb36604c05"),
    ("reference_f0_elysia", "prompts/singing-v1/elysia.npy", 4128,
     "06d1d9a5fc6653116458b546b7432372b039d463d29ac227624f24a78269bfce"),
)


def parse_singing_request(raw: bytes) -> SingingRequest:
    """Reject duplicate/extra fields, paths, and unbounded conversion controls."""
    from scripts import song_svs_runtime as runtime

    if not 1 <= len(raw) <= 4096:
        raise ValueError("The singing request has an unsupported size.")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=runtime._reject_duplicate_json_object,
            parse_constant=runtime._reject_json_constant,
        )
    except (ValueError, UnicodeError, runtime._SvsRuntimeFailure) as error:
        raise ValueError("The singing request is invalid.") from error
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "reference", "key_shift_semitones"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or not isinstance(value["reference"], str)
        or value["reference"] not in {"fairy", "elysia"}
        or type(value["key_shift_semitones"]) is not int
        or not -12 <= value["key_shift_semitones"] <= 12
    ):
        raise ValueError("The singing request contains unsupported choices.")
    return SingingRequest(value["reference"], value["key_shift_semitones"])


def verify_svc_assets(runtime_root: Path) -> dict[str, Path]:
    """Verify immutable SVC assets and private references before any model load.

    This shares the existing trusted-tree and digest boundary with lyrics SVS,
    without requiring its Mandarin alignment weights or dialogue prompt bank.
    Interpreter links must still resolve to the pinned local Python binary.
    """
    from scripts import song_svs_runtime as runtime

    runtime._require_directory(runtime_root, "Singing runtime")
    runtime._verify_trusted_runtime_tree(runtime_root)
    for spec in runtime._CORE_RUNTIME_ASSETS[:2]:
        # uv may hard-link immutable installed package binaries. The original
        # runtime trusts their ownership/ancestry and exact bytes; only private
        # prompts and independently provisioned model files require one link.
        runtime._require_asset(runtime_root, spec)
    expected_python = runtime_root / runtime._CORE_RUNTIME_ASSETS[0].relative_path
    for relative in ("prep-env/bin/python", "infer-env/bin/python"):
        link = runtime_root / relative
        if not link.is_symlink() or link.resolve(strict=True) != expected_python:
            runtime._fail("runtime_integrity_failed", "A singing interpreter is invalid.")
    result = {}
    for key, relative, size, digest in _ASSETS:
        private = key.startswith("reference_")
        if private:
            runtime._require_private_prompt_permissions(
                runtime_root / "prompts" / "singing-v1",
                label="Singing reference directory",
                require_directory=True,
            )
        result[key] = runtime._require_asset(
            runtime_root,
            runtime._AssetSpec(relative, size, digest),
            require_single_link=True,
            require_private_prompt_permissions=private,
        )
    result["whisper"] = result["whisper_config"].parent
    return result
