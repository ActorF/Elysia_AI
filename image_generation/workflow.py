"""Compile reviewed image specifications into one fixed ComfyUI API graph."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
import hashlib
import hmac
import json
import math
from pathlib import PurePosixPath, PureWindowsPath
import re
import secrets
import unicodedata
from typing import Any, Final, cast

from .domain import (
    ImageGenerationSpec,
    MAX_COMPILED_PROMPT_CODEPOINTS,
    MAX_JSON_SAFE_SEED,
    MAX_SELECTED_LORAS,
)
from .exceptions import ImageGenerationProfileError
from .profiles import (
    MAX_CHECKPOINT_NAME_CODEPOINTS,
    MAX_IMAGE_EDGE_PIXELS,
    MAX_IMAGE_PIXELS,
    ModelArchitecture,
    ModelProfile,
)
from .prompting import CompiledPrompts, compile_prompts


TXT2IMG_WORKFLOW_ID: Final = "core-sdxl-txt2img"
TXT2IMG_WORKFLOW_VERSION: Final = 1
TXT2IMG_LORA_WORKFLOW_VERSION: Final = 2
TXT2IMG_MULTI_LORA_WORKFLOW_VERSION: Final = 3
TXT2IMG_OUTPUT_NODE_ID: Final = "7"
MAX_WORKFLOW_JSON_BYTES: Final = 256 * 1024
_JOB_ID_PATTERN: Final = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_PROFILE_ID_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_OUTPUT_PREFIX_PATTERN: Final = re.compile(
    r"^Elysia-([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})$"
)
_FIXED_NODE_CLASSES: Final = {
    "1": "CheckpointLoaderSimple",
    "2": "CLIPTextEncode",
    "3": "CLIPTextEncode",
    "4": "EmptyLatentImage",
    "5": "KSampler",
    "6": "VAEDecode",
    "7": "SaveImage",
    "8": "LoraLoader",
    "9": "LoraLoader",
    "10": "LoraLoader",
    "11": "LoraLoader",
}
_FIXED_INPUT_KEYS: Final = {
    "1": frozenset({"ckpt_name"}),
    "2": frozenset({"clip", "text"}),
    "3": frozenset({"clip", "text"}),
    "4": frozenset({"batch_size", "height", "width"}),
    "5": frozenset({
        "cfg",
        "denoise",
        "latent_image",
        "model",
        "negative",
        "positive",
        "sampler_name",
        "scheduler",
        "seed",
        "steps",
    }),
    "6": frozenset({"samples", "vae"}),
    "7": frozenset({"filename_prefix", "images"}),
    "8": frozenset({"model", "clip", "lora_name", "strength_model", "strength_clip"}),
    "9": frozenset({"model", "clip", "lora_name", "strength_model", "strength_clip"}),
    "10": frozenset({"model", "clip", "lora_name", "strength_model", "strength_clip"}),
    "11": frozenset({"model", "clip", "lora_name", "strength_model", "strength_clip"}),
}
_SAMPLERS: Final = frozenset({
    "dpmpp_2m",
    "dpmpp_2m_sde",
    "dpmpp_3m_sde",
    "euler",
    "euler_ancestral",
    "heun",
    "lms",
})
_SCHEDULERS: Final = frozenset({
    "normal",
    "karras",
    "exponential",
    "sgm_uniform",
    "simple",
    "ddim_uniform",
    "beta",
})
_COMPILER_CAPABILITY: Final = object()
_COMPILER_SEAL_KEY: Final = secrets.token_bytes(32)


def _invariant_failure(reason: str) -> RuntimeError:
    """Build one stable exception without leaking prompt or checkpoint text."""

    return RuntimeError(f"Compiled workflow invariant was violated: {reason}.")


def _decode_canonical_graph(encoded: object) -> dict[str, Any]:
    """Decode one bounded canonical JSON object and reject key aliases."""

    if type(encoded) is not bytes or not encoded or len(encoded) > MAX_WORKFLOW_JSON_BYTES:
        raise _invariant_failure("the graph encoding is invalid")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        """Reject duplicate JSON keys before ordinary dict parsing hides them."""

        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _invariant_failure("the graph contains duplicate keys")
            result[key] = value
        return result

    try:
        parsed = json.loads(
            encoded,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                _invariant_failure("the graph contains a non-finite number")
            ),
        )
    except RuntimeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise _invariant_failure("the graph is not valid UTF-8 JSON") from error
    if type(parsed) is not dict:
        raise _invariant_failure("the graph root is not an object")
    try:
        canonical = json.dumps(
            parsed,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise _invariant_failure("the graph cannot be canonicalized") from error
    if not hmac.compare_digest(encoded, canonical):
        raise _invariant_failure("the graph encoding is not canonical")
    return cast(dict[str, Any], parsed)


def _require_node(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    """Return one exact fixed node after rejecting extra executable fields."""

    node = graph.get(node_id)
    if (
        type(node) is not dict
        or set(node) != {"class_type", "inputs"}
        or node.get("class_type") != _FIXED_NODE_CLASSES[node_id]
        or type(node.get("inputs")) is not dict
        or set(node["inputs"]) != _FIXED_INPUT_KEYS[node_id]
    ):
        raise _invariant_failure(f"node {node_id} is not the reviewed fixed node")
    return cast(dict[str, Any], node["inputs"])


def _is_link(value: object, node_id: str, output_index: int) -> bool:
    """Return whether a JSON value is one exact ComfyUI node link."""

    return (
        type(value) is list
        and len(value) == 2
        and value[0] == node_id
        and type(value[1]) is int
        and value[1] == output_index
    )


def _is_safe_prompt(value: object) -> bool:
    """Return whether prompt text still satisfies the compiler text boundary."""

    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and value == unicodedata.normalize("NFC", value)
        and len(value) <= MAX_COMPILED_PROMPT_CODEPOINTS
        and not any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs"}
            and character not in "\n\t"
            for character in value
        )
    )


def _is_safe_checkpoint_name(value: object) -> bool:
    """Return whether a checkpoint key remains a bounded relative safetensors key."""

    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > MAX_CHECKPOINT_NAME_CODEPOINTS
        or not value.lower().endswith(".safetensors")
        or ":" in value
        or any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs"}
            for character in value
        )
    ):
        return False
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value.replace("\\", "/"))
    return (
        not windows.is_absolute()
        and not posix.is_absolute()
        and not any(part in {"", ".", ".."} for part in value.replace("\\", "/").split("/"))
    )


def _validate_fixed_graph(
    graph: dict[str, Any],
    *,
    output_filename_prefix: str,
    expected_width: int,
    expected_height: int,
    lora_count: int = 0,
) -> None:
    """Verify every executable class, field, link, and dynamic value boundary."""

    expected_nodes = {str(index) for index in range(1, 8)} | {
        str(index) for index in range(8, 8 + lora_count)
    }
    if set(graph) != expected_nodes:
        raise _invariant_failure("the graph does not contain exactly the reviewed nodes")
    inputs = {node_id: _require_node(graph, node_id) for node_id in expected_nodes}
    model_node = str(7 + lora_count) if lora_count else "1"
    for index in range(lora_count):
        node_id = str(8 + index)
        previous = str(7 + index) if index else "1"
        lora = inputs[node_id]
        if (
            not _is_link(lora["model"], previous, 0)
            or not _is_link(lora["clip"], previous, 1)
            or not _is_safe_checkpoint_name(lora["lora_name"])
            or any(
                type(lora[key]) is not float
                or not math.isfinite(lora[key])
                or not 0.0 <= lora[key] <= 2.0
                for key in ("strength_model", "strength_clip")
            )
        ):
            raise _invariant_failure("the LoRA loader is outside the reviewed contract")

    if not _is_safe_checkpoint_name(inputs["1"]["ckpt_name"]):
        raise _invariant_failure("the checkpoint key is invalid")
    if not _is_link(inputs["2"]["clip"], model_node, 1) or not _is_safe_prompt(
        inputs["2"]["text"]
    ):
        raise _invariant_failure("the positive encoder inputs are invalid")
    if not _is_link(inputs["3"]["clip"], model_node, 1) or not _is_safe_prompt(
        inputs["3"]["text"]
    ):
        raise _invariant_failure("the negative encoder inputs are invalid")
    latent = inputs["4"]
    if (
        type(latent["batch_size"]) is not int
        or latent["batch_size"] != 1
        or type(latent["height"]) is not int
        or latent["height"] != expected_height
        or type(latent["width"]) is not int
        or latent["width"] != expected_width
    ):
        raise _invariant_failure("the latent canvas does not match the output contract")

    sampler = inputs["5"]
    if (
        type(sampler["cfg"]) is not float
        or not math.isfinite(sampler["cfg"])
        or not 1.0 <= sampler["cfg"] <= 20.0
        or type(sampler["denoise"]) is not float
        or sampler["denoise"] != 1.0
        or not _is_link(sampler["latent_image"], "4", 0)
        or not _is_link(sampler["model"], model_node, 0)
        or not _is_link(sampler["negative"], "3", 0)
        or not _is_link(sampler["positive"], "2", 0)
        or type(sampler["sampler_name"]) is not str
        or sampler["sampler_name"] not in _SAMPLERS
        or type(sampler["scheduler"]) is not str
        or sampler["scheduler"] not in _SCHEDULERS
        or type(sampler["seed"]) is not int
        or not 0 <= sampler["seed"] <= MAX_JSON_SAFE_SEED
        or type(sampler["steps"]) is not int
        or not 1 <= sampler["steps"] <= 80
    ):
        raise _invariant_failure("the sampler inputs are outside the reviewed contract")
    if not _is_link(inputs["6"]["samples"], "5", 0) or not _is_link(
        inputs["6"]["vae"], "1", 2
    ):
        raise _invariant_failure("the decoder links are invalid")
    if (
        inputs["7"]["filename_prefix"] != output_filename_prefix
        or not _is_link(inputs["7"]["images"], "6", 0)
    ):
        raise _invariant_failure("the output node is not bound to the request")


def _compute_compiler_seal(
    *,
    workflow_id: str,
    workflow_version: int,
    profile_id: str,
    profile_digest: str,
    expected_output_node_id: str,
    output_filename_prefix: str,
    api_workflow_json: bytes,
    workflow_digest: str,
    expected_width: int,
    expected_height: int,
    lora_id: str | None = None,
    lora_sha256: str | None = None,
    lora_bindings: tuple[tuple[str, str], ...] = (),
) -> bytes:
    """Bind one in-memory compiler result so field replacement fails closed."""

    document = {
        "api_workflow_json": api_workflow_json.decode("utf-8"),
        "expected_height": expected_height,
        "expected_output_node_id": expected_output_node_id,
        "expected_width": expected_width,
        "output_filename_prefix": output_filename_prefix,
        "profile_digest": profile_digest,
        "profile_id": profile_id,
        "workflow_digest": workflow_digest,
        "workflow_id": workflow_id,
        "workflow_version": workflow_version,
    }
    if lora_id is not None:
        document["lora"] = {"lora_id": lora_id, "lora_sha256": lora_sha256}
    if lora_bindings:
        # Keep the byte-identical legacy seal document for zero/one selection.
        # Multi-selection provenance is ordered because patch order changes
        # model semantics even when the set of selected assets is identical.
        document["loras"] = [
            {"lora_id": identifier, "lora_sha256": digest}
            for identifier, digest in lora_bindings
        ]
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.digest(_COMPILER_SEAL_KEY, encoded, "sha256")


@dataclass(frozen=True, slots=True)
class CompiledWorkflow:
    """Store one compiler-sealed fixed graph plus path-free provenance.

    Instances are process-local execution capabilities, not a deserialization
    format.  Construction therefore requires the private compiler capability,
    while every graph read rechecks both the fixed schema and a seal over all
    public fields.  This catches manual construction, ``object.__setattr__``
    tampering, and stale field replacement before an adapter submits code.
    """

    workflow_id: str
    workflow_version: int
    profile_id: str
    profile_digest: str
    expected_output_node_id: str
    output_filename_prefix: str
    api_workflow_json: bytes
    workflow_digest: str
    expected_width: int
    expected_height: int
    lora_id: str | None = None
    lora_sha256: str | None = None
    lora_bindings: tuple[tuple[str, str], ...] = ()
    _compiler_seal: bytes = field(default=b"", repr=False, compare=False)
    _compiler_capability: InitVar[object | None] = None

    def __post_init__(self, _compiler_capability: object | None) -> None:
        """Reject values not produced by this process's reviewed compiler."""

        if _compiler_capability is not _COMPILER_CAPABILITY:
            raise _invariant_failure("the value was not issued by the compiler")
        self.validate_invariants()

    def validate_invariants(self) -> None:
        """Revalidate provenance and the entire executable graph before use."""

        if (
            type(self.lora_bindings) is not tuple
            or (self.lora_bindings and not 2 <= len(self.lora_bindings) <= MAX_SELECTED_LORAS)
            or any(
                type(binding) is not tuple or len(binding) != 2
                or type(binding[0]) is not str or _PROFILE_ID_PATTERN.fullmatch(binding[0]) is None
                or type(binding[1]) is not str or _SHA256_PATTERN.fullmatch(binding[1]) is None
                for binding in self.lora_bindings
            )
            or len({binding[0] for binding in self.lora_bindings}) != len(self.lora_bindings)
            or (self.lora_bindings and (self.lora_id is not None or self.lora_sha256 is not None))
        ):
            raise _invariant_failure("the ordered LoRA provenance is invalid")
        if (
            self.workflow_id != TXT2IMG_WORKFLOW_ID
            or type(self.workflow_version) is not int
            or self.workflow_version != (
                TXT2IMG_MULTI_LORA_WORKFLOW_VERSION if self.lora_bindings
                else TXT2IMG_WORKFLOW_VERSION if self.lora_id is None
                else TXT2IMG_LORA_WORKFLOW_VERSION
            )
            or (
                self.lora_id is None and self.lora_sha256 is not None
            )
            or (
                self.lora_id is not None
                and (
                    type(self.lora_id) is not str
                    or _PROFILE_ID_PATTERN.fullmatch(self.lora_id) is None
                    or type(self.lora_sha256) is not str
                    or _SHA256_PATTERN.fullmatch(self.lora_sha256) is None
                )
            )
            or type(self.profile_id) is not str
            or _PROFILE_ID_PATTERN.fullmatch(self.profile_id) is None
            or type(self.profile_digest) is not str
            or _SHA256_PATTERN.fullmatch(self.profile_digest) is None
            or self.expected_output_node_id != TXT2IMG_OUTPUT_NODE_ID
            or type(self.output_filename_prefix) is not str
            or _OUTPUT_PREFIX_PATTERN.fullmatch(self.output_filename_prefix) is None
            or type(self.workflow_digest) is not str
            or _SHA256_PATTERN.fullmatch(self.workflow_digest) is None
            or type(self.expected_width) is not int
            or type(self.expected_height) is not int
            or not 512 <= self.expected_width <= MAX_IMAGE_EDGE_PIXELS
            or not 512 <= self.expected_height <= MAX_IMAGE_EDGE_PIXELS
            or self.expected_width % 64 != 0
            or self.expected_height % 64 != 0
            or self.expected_width * self.expected_height > MAX_IMAGE_PIXELS
            or type(self._compiler_seal) is not bytes
            or len(self._compiler_seal) != 32
        ):
            raise _invariant_failure("the provenance envelope is invalid")
        graph = _decode_canonical_graph(self.api_workflow_json)
        actual_digest = hashlib.sha256(self.api_workflow_json).hexdigest()
        if not hmac.compare_digest(self.workflow_digest, actual_digest):
            raise _invariant_failure("the workflow digest does not match the graph")
        _validate_fixed_graph(
            graph,
            output_filename_prefix=self.output_filename_prefix,
            expected_width=self.expected_width,
            expected_height=self.expected_height,
            lora_count=len(self.selected_lora_bindings),
        )
        expected_seal = _compute_compiler_seal(
            workflow_id=self.workflow_id,
            workflow_version=self.workflow_version,
            profile_id=self.profile_id,
            profile_digest=self.profile_digest,
            expected_output_node_id=self.expected_output_node_id,
            output_filename_prefix=self.output_filename_prefix,
            api_workflow_json=self.api_workflow_json,
            workflow_digest=self.workflow_digest,
            expected_width=self.expected_width,
            expected_height=self.expected_height,
            lora_id=self.lora_id,
            lora_sha256=self.lora_sha256,
            lora_bindings=self.lora_bindings,
        )
        if not hmac.compare_digest(self._compiler_seal, expected_seal):
            raise _invariant_failure("the compiler seal does not match")

    @property
    def selected_lora_bindings(self) -> tuple[tuple[str, str], ...]:
        """Return ordered asset bindings across legacy and chained workflow versions."""

        if self.lora_bindings:
            return self.lora_bindings
        if self.lora_id is not None and self.lora_sha256 is not None:
            return ((self.lora_id, self.lora_sha256),)
        return ()

    def request_graph(self) -> dict[str, Any]:
        """Return a fresh graph copy suitable for one ComfyUI request."""

        self.validate_invariants()
        parsed = json.loads(self.api_workflow_json)
        if not isinstance(parsed, dict):
            raise RuntimeError("Compiled workflow invariant was violated.")
        return cast(dict[str, Any], parsed)


def compile_txt2img_workflow(
    spec: ImageGenerationSpec,
    profile: ModelProfile,
    *,
    job_id: str,
) -> CompiledWorkflow:
    """Compile one fixed SDXL graph with at most four ordered reviewed LoRAs.

    Node classes and links are source-controlled here.  Only values admitted by
    ``ImageGenerationSpec`` and ``ModelProfile`` enter node inputs, so neither a
    renderer nor a language model can select arbitrary nodes, paths, scripts,
    batches, or output destinations.
    """

    if type(spec) is not ImageGenerationSpec:
        raise TypeError("spec must be ImageGenerationSpec.")
    if type(profile) is not ModelProfile:
        raise TypeError("profile must be ModelProfile.")
    if _JOB_ID_PATTERN.fullmatch(job_id) is None:
        raise ValueError("job_id must be one lowercase UUID.")
    if spec.profile_id != profile.profile_id:
        raise ImageGenerationProfileError(
            "The request does not select the supplied trusted profile."
        )
    if profile.architecture is not ModelArchitecture.SDXL:
        raise ImageGenerationProfileError(
            "The fixed txt2img workflow requires an SDXL profile."
        )
    size = profile.size_preset(spec.size_preset_id)
    prompts: CompiledPrompts = compile_prompts(spec, profile)

    output_filename_prefix = f"Elysia-{job_id}"
    graph: dict[str, Any] = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": profile.checkpoint_name},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["1", 1], "text": prompts.positive},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["1", 1], "text": prompts.negative},
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "batch_size": 1,
                "height": size.height,
                "width": size.width,
            },
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "cfg": profile.cfg_scale,
                "denoise": 1.0,
                "latent_image": ["4", 0],
                "model": ["1", 0],
                "negative": ["3", 0],
                "positive": ["2", 0],
                "sampler_name": profile.sampler_name,
                "scheduler": profile.scheduler,
                "seed": spec.seed,
                "steps": profile.steps,
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        TXT2IMG_OUTPUT_NODE_ID: {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": output_filename_prefix,
                "images": ["6", 0],
            },
        },
    }
    selected_loras = tuple(profile.lora(selection.lora_id) for selection in spec.selected_loras)
    for index, (selection, selected_lora) in enumerate(zip(spec.selected_loras, selected_loras)):
        # The source-controlled chain changes only model/CLIP links.
        # It cannot replace the checkpoint VAE or introduce arbitrary graph
        # nodes.  Strength zero still selects and verifies the pinned file.
        node_id = str(8 + index)
        previous = str(7 + index) if index else "1"
        graph[node_id] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": [previous, 0],
                "clip": [previous, 1],
                "lora_name": selected_lora.lora_name,
                "strength_model": selection.strength_model,
                "strength_clip": selection.strength_clip,
            },
        }
        graph["2"]["inputs"]["clip"] = [node_id, 1]
        graph["3"]["inputs"]["clip"] = [node_id, 1]
        graph["5"]["inputs"]["model"] = [node_id, 0]
    encoded = json.dumps(
        graph,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_WORKFLOW_JSON_BYTES:
        raise RuntimeError("Fixed workflow unexpectedly exceeded its budget.")
    workflow_digest = hashlib.sha256(encoded).hexdigest()
    fields: dict[str, Any] = {
        "workflow_id": TXT2IMG_WORKFLOW_ID,
        "workflow_version": (
            TXT2IMG_MULTI_LORA_WORKFLOW_VERSION if len(selected_loras) > 1
            else TXT2IMG_LORA_WORKFLOW_VERSION if selected_loras
            else TXT2IMG_WORKFLOW_VERSION
        ),
        "profile_id": profile.profile_id,
        "profile_digest": profile.profile_digest,
        "expected_output_node_id": TXT2IMG_OUTPUT_NODE_ID,
        "output_filename_prefix": output_filename_prefix,
        "api_workflow_json": encoded,
        "workflow_digest": workflow_digest,
        "expected_width": size.width,
        "expected_height": size.height,
        "lora_id": selected_loras[0].lora_id if len(selected_loras) == 1 else None,
        "lora_sha256": selected_loras[0].lora_sha256 if len(selected_loras) == 1 else None,
        "lora_bindings": tuple(
            (lora.lora_id, lora.lora_sha256) for lora in selected_loras
        ) if len(selected_loras) > 1 else (),
    }
    compiler_seal = _compute_compiler_seal(**fields)
    return CompiledWorkflow(
        **fields,
        _compiler_seal=compiler_seal,
        _compiler_capability=_COMPILER_CAPABILITY,
    )


__all__ = [
    "CompiledWorkflow",
    "MAX_WORKFLOW_JSON_BYTES",
    "TXT2IMG_OUTPUT_NODE_ID",
    "TXT2IMG_LORA_WORKFLOW_VERSION",
    "TXT2IMG_MULTI_LORA_WORKFLOW_VERSION",
    "TXT2IMG_WORKFLOW_ID",
    "TXT2IMG_WORKFLOW_VERSION",
    "compile_txt2img_workflow",
]
