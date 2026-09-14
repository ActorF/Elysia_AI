"""Test strict local GPT-SoVITS voice profile catalog loading and routing."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path, PurePosixPath
from typing import Callable

import pytest

from voice import (
    SYNTHESIS_MAX_AUDIO_BYTES,
    VOICE_PROFILE_ASSET_MAX_BYTES,
    VOICE_PROFILE_CATALOG_MAX_BYTES,
    VOICE_PROFILE_CATALOG_MAX_PROFILES,
    VOICE_PROFILE_CATALOG_SCHEMA_VERSION,
    JsonVoiceProfileCatalog,
    LocalVoiceAssetDeclaration,
    LocalVoiceProfile,
    LocalVoiceReference,
    SynthesisUnavailableError,
    VoiceProfileCatalogUnavailableError,
    VoiceProfileCatalogValidationError,
)


JsonDocument = dict[str, object]
DocumentMutation = Callable[[JsonDocument], None]


def _asset(
    path: object,
    *,
    size_bytes: object = 123,
    sha256: object = "a" * 64,
) -> JsonDocument:
    """Build one asset-shaped value, including malformed test variants."""

    return {"path": path, "bytes": size_bytes, "sha256": sha256}


def _reference(emotion: str = "neutral") -> JsonDocument:
    """Build one strict synthetic reference entry without third-party material."""

    return {
        "emotion": emotion,
        "audio": _asset(
            f"sample/references/{emotion}.wav",
            sha256="b" * 64,
        ),
        "prompt_text": f"Original {emotion} reference sentence.",
        "prompt_language": "en",
    }


def _profile(
    profile_id: str = "sample-one",
    *,
    base_url: str = "http://127.0.0.1:9880",
    gpt_weights: str = "sample/weights/voice.ckpt",
    sovits_weights: str = "sample/weights/voice.pth",
    rights_status: str = "verified",
) -> JsonDocument:
    """Build one complete synthetic profile with neutral and happy references."""

    return {
        "profile_id": profile_id,
        "display_name": f"Voice {profile_id}",
        "base_url": base_url,
        "gpt_weights": _asset(gpt_weights, sha256="c" * 64),
        "sovits_weights": _asset(sovits_weights, sha256="d" * 64),
        "speed_factor": 1.0,
        "audio_format": "wav",
        "rights_status": rights_status,
        "references": [_reference(), _reference("happy")],
    }


def _document(*profiles: JsonDocument) -> JsonDocument:
    """Build a schema-v2 catalog with the first profile as its default."""

    selected = list(profiles) if profiles else [_profile()]
    return {
        "schema_version": VOICE_PROFILE_CATALOG_SCHEMA_VERSION,
        "default_profile_id": selected[0]["profile_id"],
        "profiles": selected,
    }


def _write_catalog(path: Path, document: object) -> None:
    """Write deterministic UTF-8 JSON for one local loader test."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load(
    tmp_path: Path,
    document: object,
    *,
    allow_local_evaluation: bool = False,
) -> JsonVoiceProfileCatalog:
    """Load a document while keeping config separate from the local asset root."""

    asset_root = tmp_path / "assets"
    asset_root.mkdir(exist_ok=True)
    catalog_path = tmp_path / "workspace" / "settings" / "voice-profiles.json"
    _write_catalog(catalog_path, document)
    return JsonVoiceProfileCatalog.load(
        catalog_path.resolve(),
        asset_root.resolve(),
        allow_local_evaluation=allow_local_evaluation,
    )


def _profile_values(document: JsonDocument, index: int = 0) -> JsonDocument:
    """Return a typed mutable profile object from a synthetic document."""

    profiles = document["profiles"]
    assert isinstance(profiles, list)
    profile = profiles[index]
    assert isinstance(profile, dict)
    return profile


def _reference_values(
    document: JsonDocument,
    index: int = 0,
) -> JsonDocument:
    """Return a typed mutable reference object from a synthetic document."""

    profile = _profile_values(document)
    references = profile["references"]
    assert isinstance(references, list)
    reference = references[index]
    assert isinstance(reference, dict)
    return reference


def test_catalog_resolves_default_profile_and_exact_emotion(tmp_path: Path) -> None:
    """Map logical defaults to one endpoint while preserving exact prompt text."""

    catalog = _load(tmp_path, _document())

    neutral = catalog.resolve("default", "neutral")
    happy = catalog.resolve("sample-one", "happy")
    selection = catalog.resolve_selection("sample-one", "neutral")

    assert neutral.base_url == "http://127.0.0.1:9880"
    assert neutral.gpt_weights_path == (
        tmp_path / "assets" / "sample" / "weights" / "voice.ckpt"
    )
    assert neutral.sovits_weights_path == (
        tmp_path / "assets" / "sample" / "weights" / "voice.pth"
    )
    assert neutral.reference_audio_path == (
        tmp_path / "assets" / "sample" / "references" / "neutral.wav"
    )
    assert neutral.prompt_text == "Original neutral reference sentence."
    assert happy.prompt_text == "Original happy reference sentence."
    assert happy.prompt_language == "en"
    assert happy.speed_factor == 1.0
    assert happy.audio_format == "wav"
    assert selection.gpt_weights.relative_path.as_posix() == (
        "sample/weights/voice.ckpt"
    )
    assert selection.gpt_weights.size_bytes == 123
    assert selection.gpt_weights.sha256 == "c" * 64
    assert selection.reference_audio.sha256 == "b" * 64
    assert selection.prompt_text not in repr(selection)
    assert selection.gpt_weights.relative_path.as_posix() not in repr(selection)


def test_catalog_summaries_do_not_expose_sensitive_configuration(
    tmp_path: Path,
) -> None:
    """List choices without returning paths, prompts, weights, or service URLs."""

    first = _profile("first")
    second = _profile(
        "second",
        base_url="http://127.0.0.1:9881",
        gpt_weights="second/voice.ckpt",
        sovits_weights="second/voice.pth",
    )
    catalog = _load(tmp_path, _document(first, second))

    summaries = catalog.summaries()

    assert summaries[0].profile_id == "first"
    assert summaries[0].is_default is True
    assert summaries[0].emotions == ("neutral", "happy")
    assert summaries[1].profile_id == "second"
    assert summaries[1].is_default is False
    assert not hasattr(summaries[0], "base_url")
    assert not hasattr(summaries[0], "prompt_text")
    assert not hasattr(summaries[0], "gpt_weights_path")


def test_multiple_profiles_can_use_independent_model_endpoints(
    tmp_path: Path,
) -> None:
    """Support distinct voices without mutating one service's global weights."""

    first = _profile("first")
    second = _profile(
        "second",
        base_url="http://127.0.0.1:9881",
        gpt_weights="second/voice.ckpt",
        sovits_weights="second/voice.pth",
    )
    catalog = _load(tmp_path, _document(first, second))

    assert catalog.resolve("first", "neutral").base_url.endswith(":9880")
    assert catalog.resolve("second", "neutral").base_url.endswith(":9881")


def test_profiles_with_same_endpoint_must_declare_same_models(
    tmp_path: Path,
) -> None:
    """Prevent model-selection races on GPT-SoVITS server-global state."""

    first = _profile("first")
    second = _profile(
        "second",
        gpt_weights="other/voice.ckpt",
        sovits_weights="other/voice.pth",
    )

    with pytest.raises(VoiceProfileCatalogValidationError, match="invalid"):
        _load(tmp_path, _document(first, second))


def test_profiles_with_same_endpoint_may_share_one_model_pair(
    tmp_path: Path,
) -> None:
    """Allow logical variants that differ only in their reference selections."""

    first = _profile("first")
    second = _profile("second")
    second["references"] = [
        {
            **_reference(),
            "audio": _asset(
                "sample/references/alternate.wav",
                sha256="e" * 64,
            ),
            "prompt_text": "Another original neutral sentence.",
        }
    ]
    catalog = _load(tmp_path, _document(first, second))

    assert len(catalog.summaries()) == 2


def test_local_evaluation_profile_requires_explicit_opt_in(tmp_path: Path) -> None:
    """Do not treat an unverified local asset pack as an authorized voice."""

    document = _document(
        _profile("local-only", rights_status="local-evaluation-only")
    )
    disabled = _load(tmp_path, document)
    enabled = _load(
        tmp_path,
        document,
        allow_local_evaluation=True,
    )

    with pytest.raises(SynthesisUnavailableError, match="usage policy"):
        disabled.resolve("default", "neutral")
    assert enabled.resolve("default", "neutral").prompt_language == "en"


@pytest.mark.parametrize("selection", [("missing", "neutral"), ("default", "sad")])
def test_unknown_profile_or_emotion_uses_one_sanitized_error(
    tmp_path: Path,
    selection: tuple[str, str],
) -> None:
    """Avoid revealing the private catalog when a logical choice is unavailable."""

    catalog = _load(tmp_path, _document())

    with pytest.raises(SynthesisUnavailableError) as raised:
        catalog.resolve(*selection)

    assert str(raised.value) == (
        "Selected local voice profile or emotion is unavailable."
    )
    assert selection[0] not in str(raised.value)


def _remove_schema_version(document: JsonDocument) -> None:
    """Remove a required root field for malformed-document coverage."""

    del document["schema_version"]


def _add_unknown_root_field(document: JsonDocument) -> None:
    """Add one unsupported root field for strict-shape coverage."""

    document["extra"] = True


def _set_wrong_schema_version(document: JsonDocument) -> None:
    """Replace the supported catalog schema version."""

    document["schema_version"] = 1


def _set_missing_default_profile(document: JsonDocument) -> None:
    """Point the default identifier at a nonexistent profile."""

    document["default_profile_id"] = "missing"


def _set_duplicate_profiles(document: JsonDocument) -> None:
    """Duplicate one logical profile identifier."""

    profiles = document["profiles"]
    assert isinstance(profiles, list)
    profiles.append(deepcopy(profiles[0]))


def _remove_neutral_reference(document: JsonDocument) -> None:
    """Remove the required default neutral emotion."""

    _profile_values(document)["references"] = [_reference("happy")]


def _duplicate_emotion(document: JsonDocument) -> None:
    """Create ambiguous references for the same logical emotion."""

    _profile_values(document)["references"] = [_reference(), _reference()]


def _add_unknown_profile_field(document: JsonDocument) -> None:
    """Add one unsupported nested profile field."""

    _profile_values(document)["extra"] = "value"


def _add_unknown_reference_field(document: JsonDocument) -> None:
    """Add one unsupported nested reference field."""

    _reference_values(document)["extra"] = "value"


@pytest.mark.parametrize(
    "mutation",
    [
        _remove_schema_version,
        _add_unknown_root_field,
        _set_wrong_schema_version,
        _set_missing_default_profile,
        _set_duplicate_profiles,
        _remove_neutral_reference,
        _duplicate_emotion,
        _add_unknown_profile_field,
        _add_unknown_reference_field,
    ],
)
def test_catalog_rejects_missing_unknown_or_ambiguous_fields(
    tmp_path: Path,
    mutation: DocumentMutation,
) -> None:
    """Keep every catalog layer exact and cross-reference defaults safely."""

    document = _document()
    mutation(document)

    with pytest.raises(VoiceProfileCatalogValidationError, match="invalid"):
        _load(tmp_path, document)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("profile_id", "Uppercase"),
        ("profile_id", "../escape"),
        ("display_name", ""),
        ("display_name", " leading-space"),
        ("display_name", "control\x01name"),
        ("base_url", "http://example.com:9880"),
        ("gpt_weights", _asset("../outside.ckpt")),
        ("gpt_weights", _asset("C:/outside.ckpt")),
        ("gpt_weights", _asset("folder\\voice.ckpt")),
        ("gpt_weights", _asset("folder//voice.ckpt")),
        ("gpt_weights", _asset("folder/./voice.ckpt")),
        ("gpt_weights", _asset("folder/voice.ckpt.")),
        ("gpt_weights", _asset("folder/voice.ckpt ")),
        ("gpt_weights", _asset("folder/CON.ckpt")),
        ("gpt_weights", _asset("folder/COM1.ckpt")),
        ("gpt_weights", _asset("folder/CONIN$.ckpt")),
        ("gpt_weights", _asset("folder/CONOUT$.ckpt")),
        ("gpt_weights", _asset("folder/CLOCK$.ckpt")),
        ("gpt_weights", _asset("folder/COM¹.ckpt")),
        ("gpt_weights", _asset("folder/LPT³.ckpt")),
        ("gpt_weights", _asset("folder/bad?.ckpt")),
        ("gpt_weights", _asset("folder/bad*.ckpt")),
        ("gpt_weights", _asset("folder/bad|name.ckpt")),
        ("gpt_weights", _asset("folder/bad<name.ckpt")),
        ("gpt_weights", _asset("folder/bad>name.ckpt")),
        ("gpt_weights", _asset('folder/bad"name.ckpt')),
        ("gpt_weights", _asset(f"folder/{'a' * 256}.ckpt")),
        ("gpt_weights", _asset("folder/e\u0301.ckpt")),
        ("sovits_weights", _asset("/outside.pth")),
        ("speed_factor", 0.49),
        ("speed_factor", float("inf")),
        ("speed_factor", True),
        ("audio_format", "ogg"),
        ("audio_format", "mp3"),
        ("rights_status", "unknown"),
    ],
)
def test_catalog_rejects_invalid_profile_values(
    tmp_path: Path,
    field_name: str,
    value: object,
) -> None:
    """Reject unsafe URLs, paths, ranges, formats, and rights declarations."""

    document = _document()
    _profile_values(document)[field_name] = value

    with pytest.raises(VoiceProfileCatalogValidationError, match="invalid"):
        _load(tmp_path, document)


@pytest.mark.parametrize(
    "declaration",
    [
        "sample/weights/legacy.ckpt",
        {},
        {"path": "sample/weights/voice.ckpt", "bytes": 123},
        {
            **_asset("sample/weights/voice.ckpt"),
            "unexpected": True,
        },
        _asset("sample/weights/voice.ckpt", size_bytes=0),
        _asset("sample/weights/voice.ckpt", size_bytes=-1),
        _asset("sample/weights/voice.ckpt", size_bytes=True),
        _asset("sample/weights/voice.ckpt", size_bytes=1.5),
        _asset(
            "sample/weights/voice.ckpt",
            size_bytes=VOICE_PROFILE_ASSET_MAX_BYTES + 1,
        ),
        _asset("sample/weights/voice.ckpt", sha256="A" * 64),
        _asset("sample/weights/voice.ckpt", sha256="a" * 63),
        _asset("sample/weights/voice.ckpt", sha256="g" * 64),
    ],
)
def test_catalog_rejects_malformed_asset_declarations(
    tmp_path: Path,
    declaration: object,
) -> None:
    """Require exact length and lowercase digest metadata with no v1 fallback."""

    document = _document()
    _profile_values(document)["gpt_weights"] = declaration

    with pytest.raises(VoiceProfileCatalogValidationError, match="invalid"):
        _load(tmp_path, document)


@pytest.mark.parametrize(
    "size_bytes",
    [1, 11, SYNTHESIS_MAX_AUDIO_BYTES + 1],
)
def test_catalog_rejects_impossible_reference_audio_lengths(
    tmp_path: Path,
    size_bytes: int,
) -> None:
    """Keep a reference declaration within the bounded WAV transport envelope."""

    document = _document()
    _reference_values(document)["audio"] = _asset(
        "sample/references/neutral.wav",
        size_bytes=size_bytes,
    )

    with pytest.raises(VoiceProfileCatalogValidationError, match="invalid"):
        _load(tmp_path, document)


def test_catalog_rejects_casefolded_asset_aliases(tmp_path: Path) -> None:
    """Reject declarations that Windows would resolve to one ambiguous name."""

    first = _profile("first")
    second = _profile(
        "second",
        base_url="http://127.0.0.1:9881",
        gpt_weights="SAMPLE/weights/VOICE.ckpt",
    )

    with pytest.raises(VoiceProfileCatalogValidationError, match="invalid"):
        _load(tmp_path, _document(first, second))


def test_catalog_rejects_conflicting_identity_for_one_asset_path(
    tmp_path: Path,
) -> None:
    """Prevent one candidate path from carrying two claimed content identities."""

    first = _profile("first")
    second = _profile("second", base_url="http://127.0.0.1:9881")
    second["gpt_weights"] = _asset(
        "sample/weights/voice.ckpt",
        sha256="e" * 64,
    )

    with pytest.raises(VoiceProfileCatalogValidationError, match="invalid"):
        _load(tmp_path, _document(first, second))


@pytest.mark.parametrize("field_name", ["prompt_text", "prompt_language"])
def test_catalog_rejects_conflicting_context_for_one_reference_audio(
    tmp_path: Path,
    field_name: str,
) -> None:
    """Bind an exact recording to only one transcript and language pair."""

    first = _profile("first")
    second = _profile("second", base_url="http://127.0.0.1:9881")
    references = second["references"]
    assert isinstance(references, list)
    reference = references[0]
    assert isinstance(reference, dict)
    reference[field_name] = (
        "Different exact transcript."
        if field_name == "prompt_text"
        else "zh"
    )

    with pytest.raises(VoiceProfileCatalogValidationError, match="invalid"):
        _load(tmp_path, _document(first, second))


def test_direct_catalog_construction_rejects_a_foreign_asset_root(
    tmp_path: Path,
) -> None:
    """Keep public constructors from bypassing the catalog's fixed root."""

    expected_root = (tmp_path / "expected").resolve()
    foreign_root = (tmp_path / "foreign").resolve()
    expected_root.mkdir()
    foreign_root.mkdir()
    profile = LocalVoiceProfile(
        profile_id="sample",
        display_name="Sample",
        base_url="http://127.0.0.1:9880",
        gpt_weights=LocalVoiceAssetDeclaration(
            asset_root=foreign_root,
            relative_path=PurePosixPath("voice.ckpt"),
            size_bytes=1,
            sha256="a" * 64,
        ),
        sovits_weights=LocalVoiceAssetDeclaration(
            asset_root=expected_root,
            relative_path=PurePosixPath("voice.pth"),
            size_bytes=1,
            sha256="b" * 64,
        ),
        speed_factor=1.0,
        audio_format="wav",
        rights_status="verified",
        references=(
            LocalVoiceReference(
                emotion="neutral",
                audio=LocalVoiceAssetDeclaration(
                    asset_root=expected_root,
                    relative_path=PurePosixPath("neutral.wav"),
                    size_bytes=44,
                    sha256="c" * 64,
                ),
                prompt_text="Original reference sentence.",
                prompt_language="en",
            ),
        ),
    )

    with pytest.raises(ValueError, match="share the catalog asset root"):
        JsonVoiceProfileCatalog(
            expected_root,
            (profile,),
            default_profile_id="sample",
        )


def test_direct_asset_construction_rejects_a_dotdot_root(tmp_path: Path) -> None:
    """Apply the lexical root invariant outside the strict JSON load path."""

    with pytest.raises(ValueError, match="declaration is invalid"):
        LocalVoiceAssetDeclaration(
            asset_root=tmp_path / "safe" / ".." / "outside",
            relative_path=PurePosixPath("voice.ckpt"),
            size_bytes=1,
            sha256="a" * 64,
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("emotion", "Happy"),
        ("emotion", "../escape"),
        ("audio", _asset("../outside.wav")),
        ("audio", _asset("C:/outside.wav")),
        ("audio", _asset("folder\\reference.wav")),
        ("audio", _asset("folder//reference.wav")),
        ("audio", _asset("sample/reference.mp3")),
        ("prompt_text", ""),
        ("prompt_text", "\ufeff \n"),
        ("prompt_text", "\u200b\u2060"),
        ("prompt_text", "...——"),
        ("prompt_text", "contains\x00nul"),
        ("prompt_language", "auto"),
    ],
)
def test_catalog_rejects_invalid_reference_values(
    tmp_path: Path,
    field_name: str,
    value: object,
) -> None:
    """Require an exact safe transcript and one local WAV for each emotion."""

    document = _document()
    _reference_values(document)[field_name] = value

    with pytest.raises(VoiceProfileCatalogValidationError, match="invalid"):
        _load(tmp_path, document)


@pytest.mark.parametrize(
    "profiles",
    [[], [_profile(str(index)) for index in range(VOICE_PROFILE_CATALOG_MAX_PROFILES + 1)]],
)
def test_catalog_rejects_empty_or_excessive_profile_collections(
    tmp_path: Path,
    profiles: list[JsonDocument],
) -> None:
    """Bound catalog work before constructing nested profile objects."""

    document: JsonDocument = {
        "schema_version": VOICE_PROFILE_CATALOG_SCHEMA_VERSION,
        "default_profile_id": "0",
        "profiles": profiles,
    }

    with pytest.raises(VoiceProfileCatalogValidationError, match="invalid"):
        _load(tmp_path, document)


def test_catalog_accepts_integer_json_speed_as_normalized_float(tmp_path: Path) -> None:
    """Treat ordinary JSON integer 1 as the same valid speed as 1.0."""

    document = _document()
    _profile_values(document)["speed_factor"] = 1

    assert catalog_speed(_load(tmp_path, document)) == 1.0


def catalog_speed(catalog: JsonVoiceProfileCatalog) -> float:
    """Return one resolved speed through a documented public test helper."""

    return catalog.resolve("default", "neutral").speed_factor


def test_loader_rejects_duplicate_json_keys_and_nonstandard_constants(
    tmp_path: Path,
) -> None:
    """Reject parser ambiguities that Python JSON would otherwise accept."""

    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    catalog_path = tmp_path / "voice-profiles.json"
    for raw in (
        '{"schema_version":2,"schema_version":2,"default_profile_id":"x","profiles":[]}',
        '{"schema_version":2,"default_profile_id":"x","profiles":[{"bytes":1,"bytes":1}]}',
        '{"schema_version":2,"default_profile_id":"x","profiles":NaN}',
    ):
        catalog_path.write_text(raw, encoding="utf-8")
        with pytest.raises(VoiceProfileCatalogValidationError, match="invalid"):
            JsonVoiceProfileCatalog.load(
                catalog_path.resolve(),
                asset_root.resolve(),
            )


def test_loader_rejects_missing_oversized_and_invalid_utf8_catalogs(
    tmp_path: Path,
) -> None:
    """Distinguish unreadable local setup from malformed bounded content."""

    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    missing = tmp_path / "missing.json"

    with pytest.raises(VoiceProfileCatalogUnavailableError, match="not installed"):
        JsonVoiceProfileCatalog.load(missing.resolve(), asset_root.resolve())

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (VOICE_PROFILE_CATALOG_MAX_BYTES + 1))
    with pytest.raises(VoiceProfileCatalogUnavailableError, match="not installed"):
        JsonVoiceProfileCatalog.load(oversized.resolve(), asset_root.resolve())

    invalid_utf8 = tmp_path / "invalid.json"
    invalid_utf8.write_bytes(b"\xff\xfe\xfa")
    with pytest.raises(VoiceProfileCatalogValidationError, match="invalid"):
        JsonVoiceProfileCatalog.load(invalid_utf8.resolve(), asset_root.resolve())


def test_loader_requires_absolute_catalog_and_asset_paths(tmp_path: Path) -> None:
    """Reject paths whose meaning would depend on the process working directory."""

    with pytest.raises(VoiceProfileCatalogValidationError, match="invalid"):
        JsonVoiceProfileCatalog.load(
            Path("relative.json"),
            tmp_path.resolve(),
        )
    with pytest.raises(VoiceProfileCatalogValidationError, match="invalid"):
        JsonVoiceProfileCatalog.load(
            (tmp_path / "catalog.json").resolve(),
            Path("relative-assets"),
        )
    with pytest.raises(VoiceProfileCatalogValidationError, match="invalid"):
        JsonVoiceProfileCatalog.load(
            (tmp_path / "catalog.json").resolve(),
            tmp_path / "nested" / ".." / "assets",
        )


def test_loader_preserves_a_lexical_asset_root_for_later_reparse_checks(
    tmp_path: Path,
) -> None:
    """Do not erase a root link before the managed verifier can reject it."""

    target = tmp_path / "actual-assets"
    target.mkdir()
    linked_root = tmp_path / "linked-assets"
    try:
        linked_root.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"Directory symlinks are unavailable: {type(error).__name__}")
    catalog_path = tmp_path / "voice-profiles.json"
    _write_catalog(catalog_path, _document())

    catalog = JsonVoiceProfileCatalog.load(
        catalog_path.resolve(),
        linked_root,
    )

    assert catalog.resolve_selection(
        "default",
        "neutral",
    ).gpt_weights.asset_root == linked_root


def test_catalog_rejects_v1_without_a_legacy_fallback(tmp_path: Path) -> None:
    """Require an explicit identity-bearing migration from every v1 catalog."""

    document = _document()
    document["schema_version"] = 1
    _profile_values(document)["gpt_weights"] = "sample/weights/voice.ckpt"

    with pytest.raises(VoiceProfileCatalogValidationError, match="invalid"):
        _load(tmp_path, document)


def test_tracked_example_is_valid_without_shipping_assets(tmp_path: Path) -> None:
    """Keep the committed template parseable while referenced files stay local."""

    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    example = Path(__file__).resolve().parents[1] / "config" / (
        "voice_profiles.example.json"
    )

    catalog = JsonVoiceProfileCatalog.load(
        example,
        asset_root.resolve(),
        allow_local_evaluation=True,
    )

    assert catalog.summaries()[0].profile_id == "sample-voice"
    assert catalog.summaries()[0].rights_status == "local-evaluation-only"
