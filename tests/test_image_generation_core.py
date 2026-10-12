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
from image_generation.domain import ContentMode, ImageGenerationSpec
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
    """Return one general-mode structured request for unit tests."""

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


def test_general_spec_normalizes_components_and_preserves_order() -> None:
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


def test_adult_mode_requires_explicit_fictional_adult_confirmation() -> None:
    """Do not let selecting a content label silently authorize adult output."""

    with pytest.raises(ImageGenerationSafetyError, match="confirmation"):
        _spec(content_mode=ContentMode.ADULT)

    accepted = _spec(
        content_mode=ContentMode.ADULT,
        adult_confirmed=True,
        appearance="fictional adult woman with silver hair",
    )

    assert accepted.adult_confirmed is True


def test_every_mode_requires_original_fictional_subject_confirmation() -> None:
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
def test_every_mode_rejects_obvious_real_person_contradictions(
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
        content_mode=ContentMode.ADULT,
        adult_confirmed=True,
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
def test_adult_mode_rejects_obvious_contradictory_minor_terms(
    appearance: str,
) -> None:
    """Catch explicit contradictions even after the adult affirmation is set."""

    with pytest.raises(ImageGenerationSafetyError, match="minor"):
        _spec(
            content_mode=ContentMode.ADULT,
            adult_confirmed=True,
            appearance=appearance,
        )


@pytest.mark.parametrize(
    "appearance",
    [
        "underage character",
        "age 15 character",
        "young-looking person",
    ],
)
def test_general_mode_also_rejects_non_adult_subjects(appearance: str) -> None:
    """Keep general mode from bypassing the adult-only subject contract."""

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
def test_general_mode_routes_explicit_content_through_adult_opt_in(
    appearance: str,
) -> None:
    """Require the categorical adult affirmation for obvious explicit text."""

    with pytest.raises(ImageGenerationSafetyError, match="adult mode"):
        _spec(appearance=appearance)


def test_general_mode_routes_topless_content_through_adult_opt_in() -> None:
    """Treat explicit undress wording as adult content, not ordinary styling."""

    with pytest.raises(ImageGenerationSafetyError, match="adult mode"):
        _spec(outfit="topless portrait")


def test_general_mode_can_exclude_explicit_terms_in_negative_prompt() -> None:
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
        _spec(
            content_mode=ContentMode.ADULT,
            adult_confirmed=True,
            appearance="Taylor Swift nude",
        )


def test_general_mode_rejects_stale_adult_confirmation() -> None:
    """Prevent a stale checkbox from being mistaken for the selected mode."""

    with pytest.raises(ImageGenerationValidationError, match="only valid"):
        _spec(adult_confirmed=True)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"profile_id": "../model"}, "logical identifier"),
        ({"appearance": "bad\x00text"}, "control"),
        ({"seed": True}, "JSON-safe"),
        ({"content_mode": "adult"}, "closed value"),
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


def test_adult_prompt_has_adult_subject_anchor_without_numeric_age() -> None:
    """Represent the boundary as a categorical affirmation, not a fake age."""

    prompts = compile_prompts(
        _spec(
            content_mode=ContentMode.ADULT,
            adult_confirmed=True,
            appearance="fictional adult woman",
        ),
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

    def ensure_ready(self, profile: ModelProfile) -> None:
        """Record the selected trusted profile."""

        self.calls.append(f"ready:{profile.profile_id}")

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
