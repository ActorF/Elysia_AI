"""Test the structured local image-generation core without loading a model."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import struct
from typing import Callable
from uuid import uuid4
import zlib

import pytest

import image_generation.service as image_service
from image_generation.comfyui import ComfyOutputDescriptor, ComfyPromptHandle
from image_generation.domain import ImageGenerationSpec, ImageIntent, ImageLoraSelection
from image_generation.exceptions import (
    ImageGenerationCancelledError,
    ImageGenerationProfileError,
    ImageGenerationProtocolError,
    ImageGenerationSafetyError,
    ImageGenerationUnavailableError,
    ImageGenerationValidationError,
)
from image_generation.png import PNG_SIGNATURE, CanonicalPng, canonicalize_png
from image_generation.profiles import (
    ImageSizePreset,
    ModelArchitecture,
    ModelLoraProfile,
    ModelProfile,
    ModelProfileCatalog,
    PromptDialect,
)
from image_generation.prompting import compile_prompts
from image_generation.service import (
    ImageGenerationCore,
    ImageGenerationProgress,
    ImageGenerationStage,
)
from image_generation.workflow import compile_txt2img_workflow
from image_generation.workflow import CompiledWorkflow


def _chunk(kind: bytes, payload: bytes, *, corrupt_crc: bool = False) -> bytes:
    """Build one compact PNG chunk for validation fixtures."""

    checksum = zlib.crc32(kind)
    checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
    if corrupt_crc:
        checksum ^= 1
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", checksum)
    )


def _png_bytes(
    width: int = 64,
    height: int = 64,
    *,
    filter_byte: int = 0,
    corrupt_crc: bool = False,
    ancillary: bool = False,
) -> bytes:
    """Create a deterministic non-interlaced RGBA PNG without Pillow."""

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    row = bytes((filter_byte,)) + bytes(width * 4)
    chunks = [_chunk(b"IHDR", ihdr, corrupt_crc=corrupt_crc)]
    if ancillary:
        chunks.append(_chunk(b"tEXt", b"private=discard me"))
    chunks.extend((
        _chunk(b"IDAT", zlib.compress(row * height)),
        _chunk(b"IEND", b""),
    ))
    return PNG_SIGNATURE + b"".join(chunks)


def _profile(
    *,
    dialect: PromptDialect = PromptDialect.SDXL_NATURAL,
) -> ModelProfile:
    """Return one synthetic trusted profile with no private model asset."""

    return ModelProfile(
        profile_id="anime-local",
        display_name="Synthetic SDXL fixture",
        architecture=ModelArchitecture.SDXL,
        prompt_dialect=dialect,
        checkpoint_name="fixtures/anime-model.safetensors",
        checkpoint_sha256="0" * 64,
        checkpoint_source="Unit-test fixture; no model is distributed.",
        checkpoint_license="Synthetic test declaration.",
        size_presets=(ImageSizePreset("square", 512, 512),),
        default_size_preset_id="square",
    )


def _spec(**changes: object) -> ImageGenerationSpec:
    """Return one single-path structured request for unit tests."""

    values: dict[str, object] = {
        "profile_id": "anime-local",
        "size_preset_id": "square",
        "appearance": "long pink hair, blue eyes",
        "outfit": "white and violet fantasy dress",
        "pose": "standing portrait",
        "scene": "quiet garden",
        "seed": 42,
        "original_fictional_confirmed": True,
    }
    values.update(changes)
    return ImageGenerationSpec(**values)  # type: ignore[arg-type]


def _lora(**changes: object) -> ModelLoraProfile:
    """Return one pinned optional asset without a real checkpoint dependency."""

    values: dict[str, object] = {
        "lora_id": "reviewed-style",
        "display_name": "Reviewed fixture LoRA",
        "architecture": ModelArchitecture.SDXL,
        "lora_name": "fixtures/reviewed-style.safetensors",
        "lora_sha256": "a" * 64,
        "lora_source": "Unit-test fixture.",
        "lora_license": "Synthetic declaration.",
    }
    values.update(changes)
    return ModelLoraProfile(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("strength", [True, "1", None, float("nan"), float("inf"), -0.01, 2.01, 10 ** 1000])
def test_lora_selection_rejects_nonfinite_unbounded_and_non_numeric_strengths(strength: object) -> None:
    """Keep renderer-controlled weights finite and bounded without coercing booleans."""

    for field in ("strength_model", "strength_clip"):
        with pytest.raises(ImageGenerationValidationError):
            ImageLoraSelection("reviewed-style", **{field: strength})  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["../escape.safetensors", "C:/secret.safetensors", "/secret.safetensors", "a/./b.safetensors", "a//b.safetensors", "a.pickle"])
def test_lora_profile_rejects_paths_and_non_safetensors_assets(name: str) -> None:
    """Limit trusted declarations to canonical relative model keys."""

    with pytest.raises(ImageGenerationProfileError):
        _lora(lora_name=name)


def test_lora_allowlist_requires_unique_ids_names_and_exact_types() -> None:
    """Reject ambiguous asset lookups before any runtime operation can occur."""

    lora = _lora()
    for choices in ([lora], (lora, lora), (lora, replace(lora, lora_id="other"))):
        with pytest.raises(ImageGenerationProfileError):
            replace(_profile(), loras=choices)  # type: ignore[arg-type]
    profile = replace(_profile(), loras=(lora,))
    assert profile.lora(lora.lora_id) is lora
    with pytest.raises(ImageGenerationProfileError):
        profile.lora("unknown")
    with pytest.raises(ImageGenerationValidationError):
        _spec(lora={"lora_id": lora.lora_id})


@pytest.mark.parametrize("choices", [
    [ImageLoraSelection("style")],
    (ImageLoraSelection("style"), ImageLoraSelection("style", 0.5, 0.5)),
    tuple(ImageLoraSelection(f"style-{index}") for index in range(5)),
    ({"lora_id": "style"},),
])
def test_ordered_lora_selection_rejects_ambiguous_or_unbounded_collections(choices: object) -> None:
    """Reject duplicate IDs, mutable input, forged selections, and more than four patches."""

    with pytest.raises(ImageGenerationValidationError):
        _spec(loras=choices)
    with pytest.raises(ImageGenerationValidationError):
        _spec(lora=ImageLoraSelection("character"), loras=(ImageLoraSelection("style"),))


def test_singleton_lora_list_preserves_legacy_graph_seal_and_provenance() -> None:
    """Normalize new singleton callers without changing the reviewed v2 graph or seal."""

    lora = _lora()
    profile = replace(_profile(), loras=(lora,))
    selection = ImageLoraSelection(lora.lora_id, 0.8, 0.6)
    job_id = str(uuid4())
    legacy = compile_txt2img_workflow(_spec(lora=selection), profile, job_id=job_id)
    current = compile_txt2img_workflow(_spec(loras=(selection,)), profile, job_id=job_id)
    assert legacy == current
    assert legacy._compiler_seal == current._compiler_seal
    assert current.selected_lora_bindings == ((lora.lora_id, lora.lora_sha256),)


@pytest.mark.parametrize("count", [2, 4])
def test_multiple_loras_compile_a_bounded_ordered_model_and_clip_chain(count: int) -> None:
    """Compose character/style patches in request order while preserving checkpoint VAE."""

    assets = tuple(_lora(
        lora_id=f"patch-{index}", lora_name=f"fixtures/patch-{index}.safetensors",
        lora_sha256=str(index) * 64,
    ) for index in range(count))
    profile = replace(_profile(), loras=assets)
    selections = tuple(ImageLoraSelection(asset.lora_id, 0.7 + index / 10, 0.5) for index, asset in enumerate(assets))
    job_id = str(uuid4())
    compiled = compile_txt2img_workflow(_spec(loras=selections), profile, job_id=job_id)
    assert compiled.workflow_version == 3
    assert compiled.selected_lora_bindings == tuple((asset.lora_id, asset.lora_sha256) for asset in assets)
    graph = compiled.request_graph()
    assert set(graph) == {str(index) for index in range(1, 8 + count)}
    for index, asset in enumerate(assets):
        inputs = graph[str(8 + index)]["inputs"]
        previous = str(7 + index) if index else "1"
        assert inputs["model"] == [previous, 0]
        assert inputs["clip"] == [previous, 1]
        assert inputs["lora_name"] == asset.lora_name
        assert inputs["strength_model"] == selections[index].strength_model
    assert graph["2"]["inputs"]["clip"] == graph["3"]["inputs"]["clip"] == [str(7 + count), 1]
    assert graph["5"]["inputs"]["model"] == [str(7 + count), 0]
    assert graph["6"]["inputs"]["vae"] == ["1", 2]
    reversed_workflow = compile_txt2img_workflow(_spec(loras=selections[::-1]), profile, job_id=job_id)
    assert reversed_workflow.workflow_digest != compiled.workflow_digest
    object.__setattr__(compiled, "lora_bindings", compiled.lora_bindings[::-1])
    with pytest.raises(RuntimeError, match="seal"):
        compiled.request_graph()


@pytest.mark.parametrize("mutation", ["skip-model", "skip-clip", "extra-node", "substitute-hash", "strength"])
def test_multi_lora_chain_rejects_rewired_or_replaced_assets(mutation: str) -> None:
    """Fail closed on bypasses and coherent graph edits even after digest recomputation."""

    assets = (_lora(), _lora(lora_id="character", lora_name="character.safetensors"))
    compiled = compile_txt2img_workflow(
        _spec(loras=tuple(ImageLoraSelection(asset.lora_id) for asset in assets)),
        replace(_profile(), loras=assets), job_id=str(uuid4()),
    )
    graph = compiled.request_graph()
    if mutation == "skip-model":
        graph["9"]["inputs"]["model"] = ["1", 0]
    elif mutation == "skip-clip":
        graph["9"]["inputs"]["clip"] = ["1", 1]
    elif mutation == "extra-node":
        graph["10"] = graph["9"]
    elif mutation == "substitute-hash":
        object.__setattr__(compiled, "lora_bindings", ((assets[0].lora_id, "b" * 64), compiled.lora_bindings[1]))
    else:
        graph["9"]["inputs"]["strength_clip"] = 0.5
    _replace_workflow_graph(compiled, graph)
    with pytest.raises(RuntimeError):
        compiled.request_graph()


@pytest.mark.parametrize("appearance", ["", "Elysia", "eLySiA", "Ｅｌｙｓｉａ", "爱莉希雅", "愛莉希雅"])
def test_elysia_intent_anchors_only_the_fixed_fictional_adult_identity(appearance: str) -> None:
    """Permit the named product character without falsely asserting originality."""

    spec = _spec(
        intent=ImageIntent.ELYSIA, appearance=appearance,
        original_fictional_confirmed=False, fictional_adult_confirmed=True,
    )
    positive = compile_prompts(spec, _profile()).positive
    assert "Elysia, fictional adult woman, age 28, clearly adult" in positive
    assert "long pink hair, violet eyes" in positive
    assert "solo, one person" in positive
    assert "original fictional adult character" not in positive


@pytest.mark.parametrize(
    "scene",
    [
        "裸體的全身照，站在沙灘旁邊",
        "full-body view on a beach",
        "head to toe, facing the viewer",
    ],
)
def test_elysia_full_body_requests_compile_explicit_illustrious_composition_cues(
    scene: str,
) -> None:
    """Translate whole-body intent into tags that resist portrait-biased LoRAs."""

    spec = _spec(
        intent=ImageIntent.ELYSIA,
        appearance="",
        outfit="",
        pose="",
        scene=scene,
        original_fictional_confirmed=False,
        fictional_adult_confirmed=True,
    )

    prompts = compile_prompts(
        spec,
        _profile(dialect=PromptDialect.ILLUSTRIOUS_TAGS),
    )

    assert "full body, head-to-toe, entire body visible, feet visible, wide shot" in prompts.positive
    assert scene in prompts.positive
    assert "close-up, extreme close-up, portrait crop" in prompts.negative
    assert "cropped legs, cropped feet, out of frame" in prompts.negative


def test_elysia_portrait_request_does_not_force_a_full_body_composition() -> None:
    """Keep conditional composition guidance from overriding a requested close-up."""

    spec = _spec(
        intent=ImageIntent.ELYSIA,
        appearance="",
        outfit="",
        pose="",
        scene="face close-up portrait",
        original_fictional_confirmed=False,
        fictional_adult_confirmed=True,
    )

    prompts = compile_prompts(
        spec,
        _profile(dialect=PromptDialect.ILLUSTRIOUS_TAGS),
    )

    assert "head-to-toe" not in prompts.positive
    assert "portrait crop" not in prompts.negative


@pytest.mark.parametrize(
    "changes",
    [
        {"scene": "do not show full body, use a face close-up portrait"},
        {"scene": "不要全身照，只要脸部特写"},
        {"scene": "avoid a head-to-toe view, use a headshot"},
        {"outfit": "full body tattoo", "scene": "face close-up portrait"},
        {"scene": "full body tattoo, close-up portrait"},
    ],
)
def test_elysia_full_body_cues_do_not_reverse_portrait_or_noncomposition_text(
    changes: dict[str, str],
) -> None:
    """Ignore negated, conflicting, and outfit-only whole-body phrases."""

    values: dict[str, object] = {
        "intent": ImageIntent.ELYSIA,
        "appearance": "",
        "outfit": "",
        "pose": "",
        "scene": "",
        "original_fictional_confirmed": False,
        "fictional_adult_confirmed": True,
    }
    values.update(changes)
    spec = _spec(**values)

    prompts = compile_prompts(
        spec,
        _profile(dialect=PromptDialect.ILLUSTRIOUS_TAGS),
    )

    assert "head-to-toe, entire body visible" not in prompts.positive
    assert "portrait crop" not in prompts.negative


@pytest.mark.parametrize(
    "dialect",
    [PromptDialect.SDXL_NATURAL, PromptDialect.PONY_TAGS],
)
def test_elysia_full_body_cues_are_scoped_to_illustrious_profiles(
    dialect: PromptDialect,
) -> None:
    """Avoid injecting model-specific tags into other prompt dialects."""

    spec = _spec(
        intent=ImageIntent.ELYSIA,
        appearance="",
        outfit="",
        pose="",
        scene="full body on a beach",
        original_fictional_confirmed=False,
        fictional_adult_confirmed=True,
    )

    prompts = compile_prompts(spec, _profile(dialect=dialect))

    assert "head-to-toe, entire body visible" not in prompts.positive
    assert "portrait crop" not in prompts.negative


def test_elysia_adult_description_is_not_suppressed_by_system_negatives() -> None:
    """Keep adult and ordinary descriptions on the same fixed Elysia workflow."""

    spec = _spec(
        intent=ImageIntent.ELYSIA,
        appearance="",
        outfit="completely nude, no clothes",
        pose="standing, front view",
        scene="full body on a beach",
        original_fictional_confirmed=False,
        fictional_adult_confirmed=True,
    )

    prompts = compile_prompts(
        spec,
        _profile(dialect=PromptDialect.ILLUSTRIOUS_TAGS),
    )

    assert "completely nude, no clothes" in prompts.positive
    assert "nude" not in prompts.negative
    assert "clothes" not in prompts.negative
    assert "censor" not in prompts.negative


@pytest.mark.parametrize("confirmation", [False, 1, "true", None])
def test_elysia_intent_requires_literal_fictional_adult_confirmation(confirmation: object) -> None:
    """Keep the dedicated identity behind an explicit typed adult-fictional affirmation."""

    with pytest.raises((ImageGenerationValidationError, ImageGenerationSafetyError)):
        _spec(intent=ImageIntent.ELYSIA, fictional_adult_confirmed=confirmation)


@pytest.mark.parametrize("appearance", [
    "Elysia, age 17", "Elysia, young-looking", "Elysia with a real person likeness",
    "Elysia, two women", "cosplay as Elysia", "fanart of Elysia",
    "Taylor Swift", "Elysia, canon character", "Elysia, embedding:foreign",
])
def test_elysia_identity_exception_preserves_other_subject_boundaries(appearance: str) -> None:
    """Restrict the identity exception without accepting minors, people, or another cast."""

    with pytest.raises(ImageGenerationSafetyError):
        _spec(intent=ImageIntent.ELYSIA, fictional_adult_confirmed=True, appearance=appearance)


def test_elysia_negative_cannot_remove_the_fixed_identity() -> None:
    """Prevent a negative field from counteracting the reviewed character anchor."""

    with pytest.raises(ImageGenerationSafetyError):
        _spec(intent=ImageIntent.ELYSIA, fictional_adult_confirmed=True, negative_prompt="Elysia")


def test_unselected_lora_keeps_legacy_graph_and_profile_digest_compatibility() -> None:
    """Preserve the original seven-node bytes and omit empty LoRA digest fields."""

    profile = _profile()
    legacy_document = {
        "architecture": "sdxl", "cfg_scale": 6.0,
        "checkpoint_license": "Synthetic test declaration.",
        "checkpoint_name": "fixtures/anime-model.safetensors",
        "checkpoint_sha256": "0" * 64,
        "checkpoint_source": "Unit-test fixture; no model is distributed.",
        "default_size_preset_id": "square", "display_name": "Synthetic SDXL fixture",
        "profile_id": "anime-local", "prompt_dialect": "sdxl-natural",
        "sampler_name": "dpmpp_2m", "scheduler": "karras",
        "size_presets": [{"height": 512, "preset_id": "square", "width": 512}],
        "steps": 28,
    }
    legacy_digest = hashlib.sha256(json.dumps(
        legacy_document, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    assert profile.profile_digest == legacy_digest
    configured = replace(profile, loras=(_lora(),))
    job_id = str(uuid4())
    base = compile_txt2img_workflow(_spec(), profile, job_id=job_id)
    off = compile_txt2img_workflow(_spec(), configured, job_id=job_id)
    assert base.api_workflow_json == off.api_workflow_json
    assert base.workflow_digest == off.workflow_digest
    assert off.workflow_version == base.workflow_version == 1
    assert off.lora_id is None
    assert off.lora_sha256 is None
    assert configured.profile_digest != legacy_digest
    assert replace(configured, loras=(replace(_lora(), lora_sha256="b" * 64),)).profile_digest != configured.profile_digest


def test_selected_lora_uses_only_fixed_loader_and_seals_selected_identity_and_weights() -> None:
    """Bind one allowlisted file to both model and CLIP without changing VAE links."""

    lora = _lora()
    profile = replace(_profile(), loras=(lora,))
    job_id = str(uuid4())
    spec = _spec(lora=ImageLoraSelection(lora.lora_id, 0, 2))
    compiled = compile_txt2img_workflow(spec, profile, job_id=job_id)
    graph = compiled.request_graph()
    assert compiled.workflow_version == 2
    assert compiled.lora_id == lora.lora_id
    assert compiled.lora_sha256 == lora.lora_sha256
    assert set(graph) == {str(index) for index in range(1, 9)}
    assert graph["8"] == {"class_type": "LoraLoader", "inputs": {
        "model": ["1", 0], "clip": ["1", 1], "lora_name": lora.lora_name,
        "strength_model": 0.0, "strength_clip": 2.0,
    }}
    assert graph["2"]["inputs"]["clip"] == graph["3"]["inputs"]["clip"] == ["8", 1]
    assert graph["5"]["inputs"]["model"] == ["8", 0]
    assert graph["6"]["inputs"]["vae"] == ["1", 2]
    changed = compile_txt2img_workflow(
        _spec(lora=ImageLoraSelection(lora.lora_id, 1.0, 2.0)), profile, job_id=job_id,
    )
    assert changed.workflow_digest != compiled.workflow_digest
    object.__setattr__(compiled, "lora_sha256", "b" * 64)
    with pytest.raises(RuntimeError, match="seal"):
        compiled.request_graph()


@pytest.mark.parametrize("mutation", ["extra-loader", "wrong-model", "wrong-clip", "bad-strength", "changed-strength", "changed-name"])
def test_lora_graph_tampering_fails_before_submission(mutation: str) -> None:
    """Reject arbitrary added nodes and coherent edits to a valid selected graph."""

    compiled = compile_txt2img_workflow(
        _spec(lora=ImageLoraSelection("reviewed-style")),
        replace(_profile(), loras=(_lora(),)), job_id=str(uuid4()),
    )
    graph = compiled.request_graph()
    if mutation == "extra-loader":
        graph["9"] = graph["8"]
    elif mutation == "wrong-model":
        graph["8"]["inputs"]["model"] = ["5", 0]
    elif mutation == "wrong-clip":
        graph["3"]["inputs"]["clip"] = ["1", 1]
    elif mutation == "bad-strength":
        graph["8"]["inputs"]["strength_model"] = 2.1
    elif mutation == "changed-strength":
        graph["8"]["inputs"]["strength_model"] = 0.8
    else:
        graph["8"]["inputs"]["lora_name"] = "other.safetensors"
    _replace_workflow_graph(compiled, graph)
    with pytest.raises(RuntimeError):
        compiled.request_graph()


def test_spec_normalizes_components_and_preserves_order() -> None:
    """Keep UI component order deterministic while trimming benign whitespace."""

    spec = _spec(appearance="  pink hair  ", lighting="soft light")

    assert spec.appearance == "pink hair"
    assert spec.prompt_components == (
        "pink hair",
        "white and violet fantasy dress",
        "standing portrait",
        "quiet garden",
        "soft light",
    )


def test_every_request_requires_original_fictional_subject_confirmation() -> None:
    """Make the no-real-person boundary an explicit structured assertion."""

    with pytest.raises(ImageGenerationSafetyError, match="original fictional"):
        _spec(original_fictional_confirmed=False)


@pytest.mark.parametrize(
    "appearance",
    [
        "celebrity likeness",
        "looks like a real person",
        "真人明星",
    ],
)
def test_every_request_rejects_obvious_real_person_contradictions(
    appearance: str,
) -> None:
    """Reject text that contradicts the original-fictional assertion."""

    with pytest.raises(ImageGenerationSafetyError, match="real-person"):
        _spec(appearance=appearance)


@pytest.mark.parametrize(
    "appearance",
    [
        "18-year-old fictional adult character",
        "age 18 fictional adult character",
        "eighteen-year-old fictional adult character",
        "二十岁成年女性",
        "二十一歲成年女性",
        "三十七岁成年女性",
    ],
)
def test_adult_boundary_does_not_require_or_reject_adult_age_text(
    appearance: str,
) -> None:
    """Allow adult age wording while keeping numeric age entirely optional."""

    spec = _spec(
        appearance=appearance,
    )

    assert spec.appearance == appearance


@pytest.mark.parametrize(
    "appearance",
    [
        "underage character",
        "teen character",
        "schoolgirl",
        "loli",
        "未成年角色",
        "高中生",
        "16-year-old character",
        "16 years old character",
        "16岁角色",
        "age 17 character",
        "aged 17 character",
        "seventeen-year-old character",
        "十七岁角色",
        "young-looking girl",
        "baby girl",
        "adult woman holding a baby",
        "adult woman with a newborn",
        "成年女性抱着新生儿",
        "infant character",
        "adolescent character",
        "teenage character",
        "middle-schooler",
        "middle school student",
        "17 y/o character",
        "17周岁角色",
        "ｌｏｌｉ",
    ],
)
def test_request_rejects_obvious_contradictory_minor_terms(
    appearance: str,
) -> None:
    """Catch explicit contradictions to the fixed adult subject boundary."""

    with pytest.raises(ImageGenerationSafetyError, match="minor"):
        _spec(appearance=appearance)


@pytest.mark.parametrize(
    "appearance",
    [
        "underage character",
        "age 15 character",
        "young-looking person",
    ],
)
def test_request_rejects_additional_non_adult_subjects(appearance: str) -> None:
    """Keep alternate minor wording outside the adult-only contract."""

    with pytest.raises(ImageGenerationSafetyError, match="minor"):
        _spec(appearance=appearance)


@pytest.mark.parametrize(
    "appearance",
    [
        "nude anime woman",
        "explicit sex scene",
        "色情动漫角色",
        "sexual activity",
        "blowjob scene",
        "bare breasts",
        "no clothes",
        "pussy",
        "cock",
        "explicit XXX image",
    ],
)
def test_explicit_content_uses_the_same_generation_contract(
    appearance: str,
) -> None:
    """Accept explicit text without a separate mode or confirmation field."""

    spec = _spec(appearance=appearance)

    assert spec.appearance == appearance


def test_topless_content_uses_the_same_generation_contract() -> None:
    """Accept explicit outfit wording without a second request path."""

    spec = _spec(outfit="topless portrait")

    assert spec.outfit == "topless portrait"


def test_request_can_exclude_explicit_terms_in_negative_prompt() -> None:
    """Do not mistake a requested exclusion for positive explicit content."""

    spec = _spec(negative_prompt="nude, erotic")

    assert spec.negative_prompt == "nude, erotic"


@pytest.mark.parametrize(
    "negative_prompt",
    [
        "adult",
        "mature adult",
        "mature",
        "fully grown",
        "fictional",
        "original character",
        "solo",
        "single subject",
        "成年人",
    ],
)
def test_negative_prompt_cannot_negate_required_subject_anchors(
    negative_prompt: str,
) -> None:
    """Keep user negatives from reversing adult/original/single guarantees."""

    with pytest.raises(ImageGenerationSafetyError, match="contradicts"):
        _spec(negative_prompt=negative_prompt)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("appearance", "embedding:unreviewed, pink hair"),
        ("negative_prompt", "Ｅｍｂｅｄｄｉｎｇ：EasyNegative"),
    ],
)
def test_prompt_text_cannot_select_a_local_embedding_asset(
    field_name: str,
    value: str,
) -> None:
    """Keep textual-inversion lookups inside reviewed profile provenance."""

    with pytest.raises(ImageGenerationSafetyError, match="embedding"):
        _spec(**{field_name: value})


def test_minor_tripwire_does_not_mistake_a_color_name_for_a_baby() -> None:
    """Allow the established color phrase while rejecting child subjects."""

    spec = _spec(outfit="baby blue dress")

    assert spec.outfit == "baby blue dress"


@pytest.mark.parametrize(
    "appearance",
    [
        "two women",
        "romantic couple",
        "2girls",
        "2 women",
        "three women",
        "one woman and one man",
        "a woman beside a man",
        "双人构图",
    ],
)
def test_single_subject_workflow_rejects_obvious_multi_subject_text(
    appearance: str,
) -> None:
    """Keep the one-image workflow from silently becoming a group workflow."""

    with pytest.raises(ImageGenerationSafetyError, match="exactly one"):
        _spec(appearance=appearance)


@pytest.mark.parametrize(
    "appearance",
    [
        "fanart of an existing character",
        "cosplay as a canon character",
        "Elysia",
        "爱莉希雅",
        "原作角色",
    ],
)
def test_new_character_workflow_rejects_obvious_existing_character_text(
    appearance: str,
) -> None:
    """Reject direct contradictions to the original-character assertion."""

    with pytest.raises(ImageGenerationSafetyError, match="new original"):
        _spec(appearance=appearance)


def test_original_character_appearance_rejects_a_proper_person_name() -> None:
    """Keep named real or existing identities out of the first workflow."""

    with pytest.raises(ImageGenerationSafetyError, match="proper name"):
        _spec(appearance="Taylor Swift nude")


@pytest.mark.parametrize("removed_field", ["content_mode", "adult_confirmed"])
def test_spec_rejects_removed_content_mode_controls(removed_field: str) -> None:
    """Keep legacy mode switches out of the unified request contract."""

    with pytest.raises(TypeError, match="unexpected keyword"):
        _spec(**{removed_field: True})


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"profile_id": "../model"}, "logical identifier"),
        ({"appearance": "bad\x00text"}, "control"),
        ({"seed": True}, "JSON-safe"),
    ],
)
def test_spec_rejects_untyped_or_transport_unsafe_values(
    changes: dict[str, object],
    message: str,
) -> None:
    """Keep paths, booleans-as-integers, and open enum strings out of requests."""

    with pytest.raises(ImageGenerationValidationError, match=message):
        _spec(**changes)


def test_profile_rejects_checkpoint_traversal_and_incompatible_sizes() -> None:
    """Keep model paths trusted and latent sizes bounded by reviewed presets."""

    with pytest.raises(ImageGenerationProfileError, match="relative"):
        replace(_profile(), checkpoint_name="../private.safetensors")
    with pytest.raises(ImageGenerationProfileError, match="multiple of 64"):
        ImageSizePreset("bad", 513, 512)


def test_profile_catalog_rejects_duplicate_logical_ids() -> None:
    """Avoid order-dependent resolution when trusted configuration is invalid."""

    profile = _profile()

    with pytest.raises(ImageGenerationProfileError, match="duplicate"):
        ModelProfileCatalog((profile, profile))


def test_profile_text_rejects_invisible_format_controls() -> None:
    """Keep trusted provenance and labels from spoofing logs or UI direction."""

    with pytest.raises(ImageGenerationProfileError, match="display_name"):
        replace(_profile(), display_name="Safe\u202ename")


def test_profile_digest_changes_with_execution_identity() -> None:
    """Bind generated provenance to sampler and checkpoint declarations."""

    original = _profile()
    changed = replace(original, steps=29)

    assert original.profile_digest != changed.profile_digest
    assert len(original.profile_digest) == 64


@pytest.mark.parametrize(
    ("dialect", "prefix"),
    [
        (PromptDialect.SDXL_NATURAL, "high quality anime illustration"),
        (PromptDialect.PONY_TAGS, "score_9"),
        (PromptDialect.ILLUSTRIOUS_TAGS, "masterpiece"),
    ],
)
def test_prompt_compiler_uses_profile_specific_dialects(
    dialect: PromptDialect,
    prefix: str,
) -> None:
    """Never assume Pony, Illustrious, and natural SDXL grammar are identical."""

    prompts = compile_prompts(_spec(), _profile(dialect=dialect))

    assert prompts.positive.startswith(prefix)
    assert "original fictional adult character" in prompts.positive
    assert "underage" in prompts.negative


def test_prompt_always_has_adult_subject_anchor_without_numeric_age() -> None:
    """Anchor every request as clearly adult without inventing numeric age."""

    prompts = compile_prompts(
        _spec(appearance="fictional adult woman"),
        _profile(),
    )

    assert "clearly adult" in prompts.positive
    assert "18 years old" not in prompts.positive


def test_workflow_compiler_emits_only_the_reviewed_single_image_graph() -> None:
    """Bind all executable nodes to source-controlled classes and links."""

    compiled = compile_txt2img_workflow(
        _spec(),
        _profile(),
        job_id=str(uuid4()),
    )
    graph = compiled.request_graph()

    assert {node["class_type"] for node in graph.values()} == {
        "CheckpointLoaderSimple",
        "CLIPTextEncode",
        "EmptyLatentImage",
        "KSampler",
        "VAEDecode",
        "SaveImage",
    }
    assert graph["4"]["inputs"] == {
        "batch_size": 1,
        "height": 512,
        "width": 512,
    }
    assert graph["5"]["inputs"]["seed"] == 42
    assert graph["1"]["inputs"]["ckpt_name"] == _profile().checkpoint_name
    assert len(compiled.workflow_digest) == 64


def test_workflow_compiler_preserves_admitted_multiline_prompt_text() -> None:
    """Keep domain-approved line breaks and tabs valid at the graph boundary."""

    compiled = compile_txt2img_workflow(
        _spec(appearance="adult woman\nblue eyes", style="soft\twatercolor"),
        _profile(),
        job_id=str(uuid4()),
    )

    positive = compiled.request_graph()["2"]["inputs"]["text"]
    assert "adult woman\nblue eyes" in positive
    assert "soft\twatercolor" in positive


def test_workflow_returns_fresh_graph_copies() -> None:
    """Prevent one caller from mutating a later runtime submission."""

    compiled = compile_txt2img_workflow(
        _spec(),
        _profile(),
        job_id=str(uuid4()),
    )
    first = compiled.request_graph()
    first["4"]["inputs"]["batch_size"] = 99

    assert compiled.request_graph()["4"]["inputs"]["batch_size"] == 1


def _replace_workflow_graph(
    workflow: CompiledWorkflow,
    graph: dict[str, object],
) -> None:
    """Simulate hostile frozen-object mutation while retaining the old seal."""

    encoded = json.dumps(
        graph,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    object.__setattr__(workflow, "api_workflow_json", encoded)
    object.__setattr__(
        workflow,
        "workflow_digest",
        hashlib.sha256(encoded).hexdigest(),
    )


def test_compiled_workflow_rejects_manual_public_construction() -> None:
    """Require the reviewed compiler even when every public field was copied."""

    compiled = compile_txt2img_workflow(
        _spec(),
        _profile(),
        job_id=str(uuid4()),
    )

    with pytest.raises(RuntimeError, match="not issued by the compiler"):
        CompiledWorkflow(
            workflow_id=compiled.workflow_id,
            workflow_version=compiled.workflow_version,
            profile_id=compiled.profile_id,
            profile_digest=compiled.profile_digest,
            expected_output_node_id=compiled.expected_output_node_id,
            output_filename_prefix=compiled.output_filename_prefix,
            api_workflow_json=compiled.api_workflow_json,
            workflow_digest=compiled.workflow_digest,
            expected_width=compiled.expected_width,
            expected_height=compiled.expected_height,
        )


@pytest.mark.parametrize("tampering", ["extra-node", "class", "link"])
def test_workflow_invariants_reject_custom_executable_graphs(
    tampering: str,
) -> None:
    """Reject extra nodes, substituted classes, and rewired fixed-node links."""

    compiled = compile_txt2img_workflow(
        _spec(),
        _profile(),
        job_id=str(uuid4()),
    )
    graph: dict[str, object] = compiled.request_graph()
    if tampering == "extra-node":
        graph["8"] = {"class_type": "LoadImage", "inputs": {"image": "secret"}}
    elif tampering == "class":
        graph["6"]["class_type"] = "LoadImage"  # type: ignore[index]
    else:
        graph["7"]["inputs"]["images"] = ["2", 0]  # type: ignore[index]
    _replace_workflow_graph(compiled, graph)

    with pytest.raises(RuntimeError, match="invariant"):
        compiled.request_graph()


@pytest.mark.parametrize("tampering", ["prefix", "dimensions"])
def test_workflow_invariants_bind_output_identity_and_dimensions(
    tampering: str,
) -> None:
    """Reject mutually consistent field-and-graph changes made after compile."""

    compiled = compile_txt2img_workflow(
        _spec(),
        _profile(),
        job_id=str(uuid4()),
    )
    graph: dict[str, object] = compiled.request_graph()
    if tampering == "prefix":
        replacement = f"Elysia-{uuid4()}"
        graph["7"]["inputs"]["filename_prefix"] = replacement  # type: ignore[index]
        object.__setattr__(compiled, "output_filename_prefix", replacement)
    else:
        graph["4"]["inputs"]["width"] = 576  # type: ignore[index]
        object.__setattr__(compiled, "expected_width", 576)
    _replace_workflow_graph(compiled, graph)

    with pytest.raises(RuntimeError, match="compiler seal"):
        compiled.validate_invariants()


@pytest.mark.parametrize("field_name", ["profile_digest", "workflow_digest"])
def test_workflow_invariants_reject_altered_provenance_digests(
    field_name: str,
) -> None:
    """Bind both profile provenance and graph bytes to the compiler result."""

    compiled = compile_txt2img_workflow(
        _spec(),
        _profile(),
        job_id=str(uuid4()),
    )
    object.__setattr__(compiled, field_name, "b" * 64)

    with pytest.raises(RuntimeError, match="digest|compiler seal"):
        compiled.validate_invariants()


def test_png_canonicalizer_verifies_pixels_and_strips_metadata() -> None:
    """Rewrite valid pixels without retaining arbitrary ancillary chunks."""

    source = _png_bytes(64, 64, ancillary=True)
    image = canonicalize_png(source, expected_width=64, expected_height=64)

    assert image.width == 64
    assert image.height == 64
    assert b"tEXt" not in image.data
    assert image.sha256 == hashlib.sha256(image.data).hexdigest()
    assert canonicalize_png(image.data).data == image.data


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (b"not a png", "did not return"),
        (_png_bytes(corrupt_crc=True), "corrupt"),
        (_png_bytes(filter_byte=5), "filter"),
        (_png_bytes() + b"trailing", "after IEND"),
    ],
)
def test_png_canonicalizer_rejects_malformed_runtime_output(
    source: bytes,
    message: str,
) -> None:
    """Reject malformed bytes before a renderer or durable store sees them."""

    with pytest.raises(ImageGenerationProtocolError, match=message):
        canonicalize_png(source)


def test_png_canonicalizer_binds_dimensions_to_the_workflow() -> None:
    """Reject a valid but unrelated output returned for the current prompt."""

    with pytest.raises(ImageGenerationProtocolError, match="width"):
        canonicalize_png(
            _png_bytes(64, 64),
            expected_width=128,
            expected_height=64,
        )


def test_png_canonicalizer_rejects_rendering_semantic_ancillary_chunks() -> None:
    """Never strip transparency or color instructions that alter pixels."""

    source = _png_bytes()
    ihdr_end = len(PNG_SIGNATURE) + 12 + 13
    with_transparency = (
        source[:ihdr_end]
        + _chunk(b"tRNS", b"\x00\x00\x00\x00\x00\x00")
        + source[ihdr_end:]
    )

    with pytest.raises(ImageGenerationProtocolError, match="ancillary"):
        canonicalize_png(with_transparency)


class _FakeClient:
    """Record orchestration while returning one already verified fake image."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.workflow: object | None = None

    @property
    def is_managed_private_runtime(self) -> bool:
        """Identify this in-memory fixture as a private managed test session."""

        return True

    @property
    def managed_runtime_capability(self) -> object:
        """Return the private capability issued to this managed test session."""

        return image_service._MANAGED_RUNTIME_CAPABILITY

    def ensure_ready(
        self, profile: ModelProfile, *, lora: ModelLoraProfile | None = None,
        loras: tuple[ModelLoraProfile, ...] = (),
    ) -> None:
        """Record the selected trusted profile."""

        self.calls.append(f"ready:{profile.profile_id}")
        for asset in ((lora,) if lora is not None else loras):
            assert profile.lora(asset.lora_id) is asset
            self.calls.append(f"lora:{asset.lora_id}")

    def prepare(self, workflow: CompiledWorkflow) -> ComfyPromptHandle:
        """Mint and record a correlated handle before fake submission."""

        self.calls.append("prepare")
        self.workflow = workflow
        return ComfyPromptHandle(
            prompt_id=str(uuid4()),
            client_id=str(uuid4()),
            output_filename_prefix=workflow.output_filename_prefix,
        )

    def submit_prepared(
        self,
        handle: ComfyPromptHandle,
        workflow: CompiledWorkflow,
    ) -> None:
        """Record submission under the previously minted private handle."""

        assert handle.output_filename_prefix == workflow.output_filename_prefix
        assert self.workflow is workflow
        self.calls.append("submit")

    def wait_for_output(
        self,
        handle: ComfyPromptHandle,
        expected_output_node_id: str,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ComfyOutputDescriptor:
        """Return one safe descriptor without touching a filesystem."""

        assert handle.prompt_id
        assert expected_output_node_id == "7"
        assert cancel_requested is None or not cancel_requested()
        self.calls.append("wait")
        return ComfyOutputDescriptor("result.png", "", "output")

    def fetch_png(
        self,
        descriptor: ComfyOutputDescriptor,
        *,
        expected_width: int,
        expected_height: int,
    ) -> CanonicalPng:
        """Return one canonical image matching the admitted dimensions."""

        assert descriptor.filename == "result.png"
        self.calls.append("fetch")
        return canonicalize_png(
            _png_bytes(expected_width, expected_height),
            expected_width=expected_width,
            expected_height=expected_height,
        )

    def finalize(self, handle: ComfyPromptHandle) -> None:
        """Record the fake proof of terminal cleanup for this exact handle."""

        assert handle.prompt_id
        self.calls.append("finalize")

    def close(self) -> None:
        """Record deterministic ownership release for core lifecycle tests."""

        self.calls.append("close")


def test_core_passes_only_the_catalog_lora_to_managed_preflight() -> None:
    """Resolve the opaque request ID before any selected runtime or file access."""

    client = _FakeClient()
    lora = _lora()
    profile = replace(_profile(), loras=(lora,))
    core = ImageGenerationCore(ModelProfileCatalog((profile,)), client)

    result = core.generate(_spec(lora=ImageLoraSelection(lora.lora_id, 0.7, 0.6)))

    assert client.calls[:3] == ["ready:anime-local", "lora:reviewed-style", "prepare"]
    assert isinstance(client.workflow, CompiledWorkflow)
    assert client.workflow.lora_sha256 == lora.lora_sha256
    assert result.workflow_version == 2
    assert result.profile_digest == profile.profile_digest
    assert client.calls[-1] == "finalize"

    client.calls.clear()
    with pytest.raises(ImageGenerationProfileError):
        core.generate(_spec(lora=ImageLoraSelection("unknown")))
    assert client.calls == []


def test_core_resolves_every_ordered_lora_before_starting_native_work() -> None:
    """Pass only exact catalog objects and retain one cleanup boundary for the whole chain."""

    assets = (_lora(), _lora(lora_id="character", lora_name="character.safetensors"))
    profile = replace(_profile(), loras=assets)
    client = _FakeClient()
    core = ImageGenerationCore(ModelProfileCatalog((profile,)), client)
    result = core.generate(_spec(loras=tuple(ImageLoraSelection(asset.lora_id) for asset in assets)))
    assert result.workflow_version == 3
    assert client.calls[:4] == ["ready:anime-local", "lora:reviewed-style", "lora:character", "prepare"]
    assert client.calls[-1] == "finalize"
    client.calls.clear()
    with pytest.raises(ImageGenerationProfileError):
        core.generate(_spec(loras=(ImageLoraSelection(assets[0].lora_id), ImageLoraSelection("foreign"))))
    assert client.calls == []


def test_multi_lora_cancellation_after_preflight_never_allocates_a_job() -> None:
    """Cancel the combined request before process ownership begins, without a spurious finalizer."""

    assets = (_lora(), _lora(lora_id="character", lora_name="character.safetensors"))
    client = _FakeClient()
    core = ImageGenerationCore(ModelProfileCatalog((replace(_profile(), loras=assets),)), client)
    with pytest.raises(ImageGenerationCancelledError):
        core.generate(
            _spec(loras=tuple(ImageLoraSelection(asset.lora_id) for asset in assets)),
            cancel_requested=lambda: len(client.calls) >= 3,
        )
    assert client.calls == ["ready:anime-local", "lora:reviewed-style", "lora:character"]


class _UnmanagedFakeClient(_FakeClient):
    """Represent an adapter attached to a shared external runtime."""

    @property
    def is_managed_private_runtime(self) -> bool:
        """Refuse the private-runtime ownership claim."""

        return False


class _ForgedManagedFakeClient(_FakeClient):
    """Claim management without holding the supervisor-issued capability."""

    @property
    def managed_runtime_capability(self) -> object:
        """Return an unrelated object that cannot authorize composition."""

        return object()


class _CleanupFailureFakeClient(_FakeClient):
    """Simulate a supervisor that cannot prove private artifact cleanup."""

    def finalize(self, handle: ComfyPromptHandle) -> None:
        """Fail closed instead of allowing the generated result to publish."""

        super().finalize(handle)
        raise ImageGenerationUnavailableError("cleanup failed")


class _WrongPrefixFakeClient(_FakeClient):
    """Return a typed handle whose output prefix is not the compiled one."""

    def prepare(self, workflow: CompiledWorkflow) -> ComfyPromptHandle:
        """Allocate fake state but corrupt its otherwise valid handle prefix."""

        original = super().prepare(workflow)
        return ComfyPromptHandle(
            prompt_id=original.prompt_id,
            client_id=original.client_id,
            output_filename_prefix=f"Elysia-{uuid4()}",
        )


class _PhaseFailureFakeClient(_FakeClient):
    """Inject one failure after prepare to prove finalization convergence."""

    def __init__(self, failure_phase: str) -> None:
        """Select the single post-prepare phase that must fail."""

        super().__init__()
        self._failure_phase = failure_phase

    def submit_prepared(
        self,
        handle: ComfyPromptHandle,
        workflow: CompiledWorkflow,
    ) -> None:
        """Fail at submission after recording that native work was attempted."""

        super().submit_prepared(handle, workflow)
        if self._failure_phase == "submit":
            raise ImageGenerationUnavailableError("submit failed")

    def wait_for_output(
        self,
        handle: ComfyPromptHandle,
        expected_output_node_id: str,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ComfyOutputDescriptor:
        """Fail while waiting or return the normal correlated descriptor."""

        if self._failure_phase == "wait":
            self.calls.append("wait")
            raise ImageGenerationUnavailableError("wait failed")
        return super().wait_for_output(
            handle,
            expected_output_node_id,
            cancel_requested=cancel_requested,
        )

    def fetch_png(
        self,
        descriptor: ComfyOutputDescriptor,
        *,
        expected_width: int,
        expected_height: int,
    ) -> CanonicalPng:
        """Fail fetching or forge bytes for root-level revalidation failure."""

        if self._failure_phase == "fetch":
            self.calls.append("fetch")
            raise ImageGenerationUnavailableError("fetch failed")
        if self._failure_phase == "canonicalize":
            self.calls.append("fetch")
            return CanonicalPng(
                data=b"not-a-png",
                width=expected_width,
                height=expected_height,
                sha256="0" * 64,
            )
        return super().fetch_png(
            descriptor,
            expected_width=expected_width,
            expected_height=expected_height,
        )


def test_core_generates_verified_bytes_with_monotonic_progress() -> None:
    """Return reproducibility metadata only after all trusted stages complete."""

    client = _FakeClient()
    core = ImageGenerationCore(ModelProfileCatalog((_profile(),)), client)
    progress: list[ImageGenerationProgress] = []

    result = core.generate(_spec(), on_progress=progress.append)

    assert client.calls == [
        "ready:anime-local",
        "prepare",
        "submit",
        "wait",
        "fetch",
        "finalize",
    ]
    assert result.image.width == 512
    assert result.profile_digest == _profile().profile_digest
    assert result.seed == 42
    assert [item.stage for item in progress] == [
        ImageGenerationStage.VALIDATING,
        ImageGenerationStage.PROBING,
        ImageGenerationStage.SUBMITTING,
        ImageGenerationStage.SAMPLING,
        ImageGenerationStage.VERIFYING,
        ImageGenerationStage.COMPLETED,
    ]
    assert [item.percent for item in progress] == sorted(
        item.percent for item in progress
    )


def test_core_close_delegates_to_the_managed_runtime_owner() -> None:
    """Give application shutdown an explicit child-process cleanup path."""

    client = _FakeClient()
    core = ImageGenerationCore(ModelProfileCatalog((_profile(),)), client)

    core.close()

    assert client.calls == ["close"]


def test_core_cancellation_before_probe_has_no_runtime_side_effect() -> None:
    """Stop before native work when cancellation already owns the boundary."""

    client = _FakeClient()
    core = ImageGenerationCore(ModelProfileCatalog((_profile(),)), client)

    with pytest.raises(ImageGenerationCancelledError, match="cancelled"):
        core.generate(_spec(), cancel_requested=lambda: True)

    assert client.calls == []


def test_core_rejects_an_unmanaged_or_shared_runtime_adapter() -> None:
    """Keep raw shared ComfyUI persistence outside the trusted composition root."""

    client = _UnmanagedFakeClient()

    with pytest.raises(ImageGenerationUnavailableError, match="managed private"):
        ImageGenerationCore(ModelProfileCatalog((_profile(),)), client)


def test_core_rejects_a_forged_managed_runtime_claim() -> None:
    """Require the supervisor capability in addition to a boolean claim."""

    client = _ForgedManagedFakeClient()

    with pytest.raises(ImageGenerationUnavailableError, match="managed private"):
        ImageGenerationCore(ModelProfileCatalog((_profile(),)), client)


def test_core_finalizes_a_prepared_handle_when_cancellation_wins() -> None:
    """Clean the private session even when cancellation wins before submit."""

    client = _FakeClient()
    core = ImageGenerationCore(ModelProfileCatalog((_profile(),)), client)
    decisions = iter((False, False, True))

    with pytest.raises(ImageGenerationCancelledError, match="cancelled"):
        core.generate(_spec(), cancel_requested=lambda: next(decisions))

    assert client.calls == ["ready:anime-local", "prepare", "finalize"]


def test_core_finalizes_a_typed_handle_with_the_wrong_output_prefix() -> None:
    """Clean allocated state even when prepared-handle correlation is invalid."""

    client = _WrongPrefixFakeClient()
    core = ImageGenerationCore(ModelProfileCatalog((_profile(),)), client)

    with pytest.raises(ImageGenerationProtocolError, match="prompt handle"):
        core.generate(_spec())

    assert client.calls == ["ready:anime-local", "prepare", "finalize"]


@pytest.mark.parametrize(
    ("failure_phase", "error_type"),
    [
        ("submit", ImageGenerationUnavailableError),
        ("wait", ImageGenerationUnavailableError),
        ("fetch", ImageGenerationUnavailableError),
        ("canonicalize", ImageGenerationProtocolError),
    ],
)
def test_core_finalizes_every_post_prepare_failure(
    failure_phase: str,
    error_type: type[Exception],
) -> None:
    """Converge lost-submit, inference, fetch, and validation failures on cleanup."""

    client = _PhaseFailureFakeClient(failure_phase)
    core = ImageGenerationCore(ModelProfileCatalog((_profile(),)), client)
    progress: list[ImageGenerationProgress] = []

    with pytest.raises(error_type):
        core.generate(_spec(), on_progress=progress.append)

    assert client.calls[-1] == "finalize"
    assert ImageGenerationStage.COMPLETED not in {
        item.stage for item in progress
    }


def test_core_never_publishes_when_private_cleanup_cannot_be_proved() -> None:
    """Turn cleanup failure into a terminal error before completed progress."""

    client = _CleanupFailureFakeClient()
    core = ImageGenerationCore(ModelProfileCatalog((_profile(),)), client)
    progress: list[ImageGenerationProgress] = []

    with pytest.raises(ImageGenerationUnavailableError, match="cleanup"):
        core.generate(_spec(), on_progress=progress.append)

    assert client.calls[-1] == "finalize"
    assert ImageGenerationStage.COMPLETED not in {
        item.stage for item in progress
    }


def test_progress_callback_failure_does_not_strand_native_inference() -> None:
    """Treat UI progress as advisory while preserving final verification."""

    client = _FakeClient()
    core = ImageGenerationCore(ModelProfileCatalog((_profile(),)), client)

    result = core.generate(
        _spec(),
        on_progress=lambda _progress: (_ for _ in ()).throw(RuntimeError()),
    )

    assert result.image.width == 512
    assert client.calls[-1] == "finalize"
