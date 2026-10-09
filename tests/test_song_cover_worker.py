"""Verify the bounded offline singing-cover worker without loading private models."""

from __future__ import annotations

from array import array
from collections.abc import Callable
import hashlib
import importlib.machinery
import json
import math
import os
from pathlib import Path
import random
import subprocess
import wave

import pytest

from scripts import song_cover_worker


def _singing_runtime_digest(root: Path) -> tuple[int, int, str]:
    """Mirror the production aggregate for one deliberately tiny test runtime."""

    reviewed: list[tuple[str, Path]] = []
    for package_name in song_cover_worker._SINGING_RUNTIME_PACKAGES:
        for candidate in (root / package_name).rglob("*.py"):
            reviewed.append((candidate.relative_to(root).as_posix(), candidate))
    for relative_name in (
        "retrying.py",
        "lameenc.cp39-win_amd64.pyd",
        "demucs/remote/files.txt",
        "demucs/remote/htdemucs.yaml",
    ):
        reviewed.append((relative_name, root / relative_name))

    digest = hashlib.sha256()
    total_bytes = 0
    for relative_name, candidate in sorted(reviewed):
        content = candidate.read_bytes()
        total_bytes += len(content)
        digest.update(relative_name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
    return len(reviewed), total_bytes, digest.hexdigest()


def _create_singing_runtime_fixture(root: Path) -> None:
    """Create only the import providers and model-selection data under review."""

    for package_name in song_cover_worker._SINGING_RUNTIME_PACKAGES:
        package_root = root / package_name
        package_root.mkdir(parents=True)
        (package_root / "__init__.py").write_text(
            f'"""{package_name} fixture."""\n',
            encoding="utf-8",
        )
    remote_root = root / "demucs" / "remote"
    remote_root.mkdir()
    (remote_root / "files.txt").write_text(
        "root: hybrid_transformer/\n955717e8-8726e21a.th\n",
        encoding="utf-8",
    )
    (remote_root / "htdemucs.yaml").write_text(
        "models: ['955717e8']\n",
        encoding="utf-8",
    )
    (root / "retrying.py").write_text(
        '"""Retrying fixture."""\n',
        encoding="utf-8",
    )
    (root / "lameenc.cp39-win_amd64.pyd").write_bytes(b"fixed-extension")


def _completed_probe(payload: object) -> subprocess.CompletedProcess[str]:
    """Return one successful ffprobe-shaped process fixture."""

    return subprocess.CompletedProcess(
        args=["ffprobe"],
        returncode=0,
        stdout=json.dumps(payload),
        stderr="",
    )


def _write_pcm16_mono(path: Path, samples: array[int]) -> None:
    """Write one canonical mono fixture for consonant-layer tests."""

    content = array("h", samples)
    if song_cover_worker.sys.byteorder != "little":
        content.byteswap()
    with wave.open(str(path), "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(44_100)
        destination.writeframes(content.tobytes())


@pytest.mark.parametrize(
    ("invoke", "expected_message"),
    [
        (
            lambda: song_cover_worker._run_command(
                ["private-ffmpeg.exe"],
                "The selected song could not be converted.",
            ),
            "The selected song could not be converted.",
        ),
        (
            lambda: song_cover_worker._probe_duration(
                Path("private-ffprobe.exe"),
                Path("private-song.wav"),
            ),
            "The selected song could not be read as audio.",
        ),
    ],
)
def test_subprocess_timeouts_map_to_safe_worker_failures(
    monkeypatch: pytest.MonkeyPatch,
    invoke: Callable[[], object],
    expected_message: str,
) -> None:
    """Collapse native command timeouts into the intended renderer-safe error."""

    private_diagnostic = "D:/private/native-timeout-details"

    def raise_timeout(
        arguments: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        """Simulate a native process that exceeds its caller-supplied deadline."""

        timeout = kwargs["timeout"]
        assert isinstance(timeout, (int, float))
        raise subprocess.TimeoutExpired(
            arguments,
            float(timeout),
            stderr=private_diagnostic,
        )

    monkeypatch.setattr(song_cover_worker.subprocess, "run", raise_timeout)

    with pytest.raises(song_cover_worker._SongCoverFailure) as caught:
        invoke()

    assert str(caught.value) == expected_message
    assert private_diagnostic not in str(caught.value)
    assert isinstance(caught.value.__cause__, subprocess.TimeoutExpired)


def test_remove_bytecode_caches_deletes_nested_generated_code(
    tmp_path: Path,
) -> None:
    """Remove nested bytecode caches without disturbing reviewed source files."""

    source = tmp_path / "package" / "module.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n", encoding="utf-8")
    cache = source.parent / "__pycache__"
    cache.mkdir()
    (cache / "module.cpython-39.pyc").write_bytes(b"unreviewed-bytecode")

    song_cover_worker._remove_bytecode_caches(tmp_path, label="Runtime")

    assert not cache.exists()
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_remove_bytecode_caches_rejects_a_symbolic_link(tmp_path: Path) -> None:
    """Reject linked caches rather than deleting content outside the runtime root."""

    external_cache = tmp_path / "external-cache"
    external_cache.mkdir()
    marker = external_cache / "keep.pyc"
    marker.write_bytes(b"outside-runtime")
    cache_link = tmp_path / "runtime" / "package" / "__pycache__"
    cache_link.parent.mkdir(parents=True)
    try:
        cache_link.symlink_to(external_cache, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symbolic links is not permitted on this host.")

    with pytest.raises(song_cover_worker._SongCoverFailure, match="could not be cleared"):
        song_cover_worker._remove_bytecode_caches(
            tmp_path / "runtime",
            label="Runtime",
        )

    assert marker.read_bytes() == b"outside-runtime"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_remove_bytecode_caches_rejects_a_windows_junction(
    tmp_path: Path,
) -> None:
    """Reject a junction without deleting its external bytecode target."""

    external_cache = tmp_path / "external-junction-cache"
    external_cache.mkdir()
    marker = external_cache / "keep.pyc"
    marker.write_bytes(b"outside-runtime")
    cache_link = tmp_path / "runtime" / "package" / "__pycache__"
    cache_link.parent.mkdir(parents=True)
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(cache_link), str(external_cache)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if created.returncode != 0:
        pytest.skip("Creating Windows junctions is not permitted on this host.")
    try:
        with pytest.raises(
            song_cover_worker._SongCoverFailure,
            match="could not be cleared",
        ):
            song_cover_worker._remove_bytecode_caches(
                tmp_path / "runtime",
                label="Runtime",
            )
        assert marker.read_bytes() == b"outside-runtime"
    finally:
        os.rmdir(cache_link)


def test_probe_duration_accepts_one_bounded_audio_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """Accept a finite duration only when ffprobe reports the selected audio stream."""

    monkeypatch.setattr(
        song_cover_worker.subprocess,
        "run",
        lambda *args, **kwargs: _completed_probe(
            {
                "streams": [{"codec_type": "audio", "duration": "209.308"}],
            }
        ),
    )

    assert song_cover_worker._probe_duration(
        Path("ffprobe.exe"),
        Path("private-song.wav"),
    ) == pytest.approx(209.308)


@pytest.mark.parametrize(
    "payload",
    [
        {"streams": [{"codec_type": "audio", "duration": "0.5"}]},
        {"streams": [{"codec_type": "audio", "duration": "721"}]},
        {"streams": []},
        {"streams": ["not-a-stream"]},
        {"streams": [{"codec_type": "audio"}]},
    ],
)
def test_probe_duration_rejects_unbounded_or_malformed_metadata(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> None:
    """Reject metadata that could bypass the worker's duration and stream bounds."""

    monkeypatch.setattr(
        song_cover_worker.subprocess,
        "run",
        lambda *args, **kwargs: _completed_probe(payload),
    )

    with pytest.raises(song_cover_worker._SongCoverFailure):
        song_cover_worker._probe_duration(
            Path("ffprobe.exe"),
            Path("private-song.wav"),
        )


def test_mix_cover_applies_the_clear_vocal_quality_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apply presence, linear vocal recovery, ducking, and bounded limiting."""

    commands: list[list[str]] = []

    def record_command(arguments: list[str], _failure_message: str) -> None:
        """Capture an exact native command instead of starting FFmpeg."""

        commands.append(list(arguments))

    monkeypatch.setattr(song_cover_worker, "_run_command", record_command)

    song_cover_worker._mix_cover(
        Path("ffmpeg.exe"),
        Path("vocals.wav"),
        Path("consonants.wav"),
        Path("instrumental.wav"),
        Path("cover.wav"),
        Path("cover.mp3"),
        target_samples=441_000,
    )

    assert len(commands) == 2
    filter_graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "volume=0.125893" in filter_graph
    assert "highpass=f=60" in filter_graph
    assert "equalizer=f=3000:t=q:w=0.8:g=0.5" in filter_graph
    assert "acompressor" not in filter_graph
    assert "volume=1.161449" in filter_graph
    assert "acrossover=split='2200 5200':order=4th" in filter_graph
    assert "sample_fmts=dbl" in filter_graph
    assert "sidechaincompress=threshold=0.05:ratio=2.5" in filter_graph
    assert "amix=inputs=3:duration=longest:dropout_transition=0,volume=3" in filter_graph
    assert "alimiter=limit=0.891251:level=false" in filter_graph
    assert "apad=whole_len=441000" in filter_graph
    assert "atrim=end_sample=441000" in filter_graph
    assert "normalize" not in filter_graph
    assert commands[1][commands[1].index("-b:a") + 1] == "320k"


def test_decode_maps_the_same_first_audio_stream_that_was_probed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent FFmpeg auto-selection from switching to another audio track."""

    commands: list[list[str]] = []
    monkeypatch.setattr(
        song_cover_worker,
        "_run_command",
        lambda arguments, _failure: commands.append(list(arguments)),
    )

    song_cover_worker._decode_song(
        Path("ffmpeg.exe"),
        Path("multi-track.m4a"),
        10.5,
        Path("decoded.wav"),
    )

    assert len(commands) == 1
    assert commands[0][commands[0].index("-map") + 1] == "0:a:0"
    assert commands[0][commands[0].index("-t") + 1] == "10.500000"
    audio_filter = commands[0][commands[0].index("-af") + 1]
    assert "apad=whole_len=463050" in audio_filter
    assert "atrim=end_sample=463050" in audio_filter


@pytest.mark.parametrize("key_shift", [-2, 2])
def test_transpose_audio_uses_one_bounded_key_for_exact_length(
    monkeypatch: pytest.MonkeyPatch,
    key_shift: int,
) -> None:
    """Shift the accompaniment up or down while restoring its sample count."""

    commands: list[list[str]] = []
    monkeypatch.setattr(
        song_cover_worker,
        "_run_command",
        lambda arguments, _failure: commands.append(list(arguments)),
    )

    song_cover_worker._transpose_audio(
        Path("ffmpeg.exe"),
        Path("source.wav"),
        Path("shifted.wav"),
        key_shift_semitones=key_shift,
        target_samples=463_050,
    )

    assert len(commands) == 1
    audio_filter = commands[0][commands[0].index("-af") + 1]
    expected_rate = round(44_100 * (2 ** (key_shift / 12)))
    assert f"asetrate={expected_rate}" in audio_filter
    assert "aresample=44100" in audio_filter
    assert f"atempo={44_100 / expected_rate:.9f}" in audio_filter
    assert "apad=whole_len=463050" in audio_filter
    assert "atrim=end_sample=463050" in audio_filter


@pytest.mark.parametrize("key_shift", [-3, 3])
def test_transpose_audio_rejects_an_unreviewed_key_shift(
    key_shift: int,
) -> None:
    """Reject pitch factors outside the closed quality-preserving allowlist."""

    with pytest.raises(song_cover_worker._SongCoverFailure, match="key shift"):
        song_cover_worker._transpose_audio(
            Path("ffmpeg.exe"),
            Path("source.wav"),
            Path("shifted.wav"),
            key_shift_semitones=key_shift,
            target_samples=44_100,
        )


def test_parser_accepts_song_and_stem_source_contracts() -> None:
    """Expose only the two reviewed source modes and five safe key choices."""

    parser = song_cover_worker._parser()
    common = [
        "--job-dir", "job",
        "--ffmpeg", "ffmpeg.exe",
        "--ffprobe", "ffprobe.exe",
        "--demucs-site", "demucs",
        "--torch-home", "torch",
        "--svc-root", "svc",
        "--svc-model-root", "model",
        "--job-token", "00000000-0000-4000-8000-000000000000",
    ]

    song = parser.parse_args(["--input", "song.wav", *common])
    stems = parser.parse_args(
        [
            "--source-mode", "stems",
            "--vocal-input", "vocal.wav",
            "--accompaniment-input", "instrumental.wav",
            "--key-shift-semitones", "2",
            *common,
        ]
    )

    assert song.source_mode == "song"
    assert song.key_shift_semitones == 0
    assert stems.source_mode == "stems"
    assert stems.key_shift_semitones == 2


def test_source_contract_rejects_mixed_or_incomplete_inputs() -> None:
    """Prevent ambiguous native paths from crossing the worker boundary."""

    parser = song_cover_worker._parser()
    common = [
        "--job-dir", "job",
        "--ffmpeg", "ffmpeg.exe",
        "--ffprobe", "ffprobe.exe",
        "--demucs-site", "demucs",
        "--torch-home", "torch",
        "--svc-root", "svc",
        "--svc-model-root", "model",
        "--job-token", "00000000-0000-4000-8000-000000000000",
    ]
    mixed = parser.parse_args(
        [
            "--input", "song.wav",
            "--vocal-input", "vocal.wav",
            "--accompaniment-input", "instrumental.wav",
            *common,
        ]
    )
    incomplete = parser.parse_args(
        ["--source-mode", "stems", "--vocal-input", "vocal.wav", *common]
    )

    with pytest.raises(song_cover_worker._SongCoverFailure, match="source inputs"):
        song_cover_worker._validate_source_arguments(mixed)
    with pytest.raises(song_cover_worker._SongCoverFailure, match="source inputs"):
        song_cover_worker._validate_source_arguments(incomplete)


def test_demucs_uses_the_reviewed_high_quality_overlap_and_shift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Run the pinned separator with one stabilization shift and 50% overlap."""

    received: list[str] = []

    class FakeDemucs:
        """Expose the exact reviewed Demucs version to the worker."""

        __version__ = song_cover_worker._EXPECTED_DEMUCS_VERSION

    class FakeSeparate:
        """Create deterministic stem fixtures for one captured invocation."""

        @staticmethod
        def main(arguments: list[str]) -> None:
            """Record CLI options and materialize the two expected stem files."""

            received.extend(arguments)
            stem_root = tmp_path / "separated" / "htdemucs" / "source"
            stem_root.mkdir(parents=True)
            (stem_root / "vocals.wav").write_bytes(b"vocal")
            (stem_root / "no_vocals.wav").write_bytes(b"instrumental")

    decoded = tmp_path / "source.wav"
    decoded.write_bytes(b"decoded")
    demucs_site = tmp_path / "demucs-site"
    demucs_site.mkdir()
    torch_home = tmp_path / "torch"
    torch_home.mkdir()
    monkeypatch.setattr(
        song_cover_worker,
        "_load_singing_runtime_modules",
        lambda _root: (FakeDemucs(), FakeSeparate()),
    )

    song_cover_worker._separate_vocals(
        decoded,
        tmp_path / "separated",
        demucs_site,
        torch_home,
    )

    assert received[received.index("--shifts") + 1] == "1"
    assert received[received.index("--overlap") + 1] == "0.5"


@pytest.mark.parametrize("key_shift", [-2, 2])
def test_convert_vocals_uses_fcpe_and_clarity_parameters(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    key_shift: int,
) -> None:
    """Invoke FCPE and move vocal pitch inside SVC without resampling it."""

    svc_root = tmp_path / "svc"
    (svc_root / "raw").mkdir(parents=True)
    (svc_root / "results").mkdir()
    model = tmp_path / "G.pth"
    config = tmp_path / "G.json"
    vocals = tmp_path / "vocals.wav"
    output = tmp_path / "converted.wav"
    for candidate in (model, config, vocals):
        candidate.write_bytes(b"fixture")
    captured_argv: list[str] = []

    def fake_run_path(_path: str, *, run_name: str) -> None:
        """Capture arguments and emit the exact FCPE result name."""

        assert run_name == "__main__"
        captured_argv.extend(song_cover_worker.sys.argv)
        raw_name = "elysia_cover_00000000000040008000000000000000.wav"
        result = (
            svc_root
            / "results"
            / f"{raw_name}_{key_shift}key_Elysia_sovits_fcpe.wav"
        )
        result.write_bytes(b"converted")

    monkeypatch.setattr(song_cover_worker, "_run_command", lambda *_args: None)
    monkeypatch.setattr(song_cover_worker.runpy, "run_path", fake_run_path)

    song_cover_worker._convert_vocals(
        ffmpeg=Path("ffmpeg.exe"),
        vocals=vocals,
        output=output,
        svc_root=svc_root,
        svc_model=model,
        svc_config=config,
        job_token="00000000000040008000000000000000",
        key_shift_semitones=key_shift,
    )

    assert captured_argv[captured_argv.index("-f0p") + 1] == "fcpe"
    assert captured_argv[captured_argv.index("-sd") + 1] == "-48"
    assert captured_argv[captured_argv.index("-ns") + 1] == "0.16"
    assert captured_argv[captured_argv.index("-lea") + 1] == "0.5"
    assert captured_argv[captured_argv.index("-t") + 1] == str(key_shift)
    assert output.read_bytes() == b"converted"
    assert not any((svc_root / "results").iterdir())


def test_fcpe_asset_identity_is_pinned_to_the_reviewed_local_weight() -> None:
    """Keep unsafe or silently replaced FCPE pickle data outside inference."""

    assert song_cover_worker._EXPECTED_FCPE_BYTES == 69_005_189
    assert song_cover_worker._EXPECTED_FCPE_SHA256 == (
        "c3a8dd2dbd51baf19ed295006f2ac25dba6dd60adc7ec578ae5fbd94970951da"
    )


def test_consonant_classifier_requires_aperiodic_high_frequency_energy() -> None:
    """Keep clear fricative-like noise while rejecting a periodic high tone."""

    sample_rate = 44_100
    frame_size = 1_024
    tone = array(
        "h",
        (
            round(8_000 * math.sin(2 * math.pi * 5_000 * index / sample_rate))
            for index in range(frame_size)
        ),
    )
    generator = random.Random(7)
    noise = array("h", (generator.randint(-8_000, 8_000) for _ in range(frame_size)))

    assert not song_cover_worker._frame_has_clear_consonant(tone, tone)
    assert song_cover_worker._frame_has_clear_consonant(noise, noise)


def test_low_confidence_consonant_masks_collapse_to_silence() -> None:
    """Suppress sparse or implausibly pervasive residual high-frequency masks."""

    assert song_cover_worker._stabilize_consonant_masks(
        [False, True, False, False],
    ) == [0.0, 0.0, 0.0, 0.0]
    assert song_cover_worker._stabilize_consonant_masks(
        [True] * 8 + [False] * 2,
    ) == [0.0] * 10
    stable = song_cover_worker._stabilize_consonant_masks(
        [False] * 20 + [True] * 4 + [False] * 20,
    )
    assert max(stable) > 0.0
    assert stable[-1] < stable[24]


def test_consonant_layer_keeps_only_a_short_confident_noise_burst(
    tmp_path: Path,
) -> None:
    """Create an aligned layer while suppressing periodic and silent frames."""

    frame_size = song_cover_worker._CONSONANT_FRAME_SAMPLES
    generator = random.Random(11)
    silence = [0] * (20 * frame_size)
    burst = [generator.randint(-8_000, 8_000) for _ in range(4 * frame_size)]
    samples = array("h", [*silence, *burst, *silence])
    vocals = tmp_path / "vocals.wav"
    highpass = tmp_path / "highpass.wav"
    output = tmp_path / "consonants.wav"
    _write_pcm16_mono(vocals, samples)
    _write_pcm16_mono(highpass, samples)

    masks = song_cover_worker._analyze_consonant_frames(vocals, highpass)
    song_cover_worker._write_consonant_layer(highpass, output, masks)

    assert max(masks) > 0.0
    with wave.open(str(output), "rb") as rendered:
        assert rendered.getnframes() == len(samples)
        rendered_samples = array("h")
        rendered_samples.frombytes(rendered.readframes(rendered.getnframes()))
    assert all(sample == 0 for sample in rendered_samples[: 20 * frame_size])
    assert any(
        sample != 0
        for sample in rendered_samples[20 * frame_size : 24 * frame_size]
    )


def test_consonant_layer_interpolates_gain_across_frame_boundaries(
    tmp_path: Path,
) -> None:
    """Avoid an electrical click when a protected consonant becomes active."""

    frame_size = song_cover_worker._CONSONANT_FRAME_SAMPLES
    highpass = tmp_path / "highpass.wav"
    output = tmp_path / "consonants.wav"
    _write_pcm16_mono(highpass, array("h", [10_000] * (frame_size * 3)))

    song_cover_worker._write_consonant_layer(
        highpass,
        output,
        [0.0, 0.99, 0.55],
    )

    with wave.open(str(output), "rb") as rendered:
        rendered_samples = array("h")
        rendered_samples.frombytes(rendered.readframes(rendered.getnframes()))
    attack_start = rendered_samples[frame_size]
    attack_end = rendered_samples[(frame_size * 2) - 1]
    release_start = rendered_samples[frame_size * 2]
    release_end = rendered_samples[-1]
    assert 0 < attack_start < 500
    assert 9_800 < attack_end < 10_000
    assert abs(release_start - attack_end) < 100
    assert 5_400 < release_end < 5_600


def test_main_rejects_non_uuid_job_before_resolving_private_paths(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fail a forged job identity before touching any selected or model path."""

    private_marker = "do-not-leak-private-song-path"
    result = song_cover_worker.main(
        [
            "--input",
            private_marker,
            "--job-dir",
            "missing-job-directory",
            "--ffmpeg",
            "missing-ffmpeg",
            "--ffprobe",
            "missing-ffprobe",
            "--demucs-site",
            "missing-demucs",
            "--torch-home",
            "missing-torch-home",
            "--svc-root",
            "missing-svc-root",
            "--svc-model-root",
            "missing-model-root",
            "--job-token",
            "not-a-job-id",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "identity is invalid" in captured.err
    assert private_marker not in captured.err
    assert captured.out == ""


def test_require_file_rejects_a_symbolic_link(tmp_path: Path) -> None:
    """Reject linked native tools so a reviewed path cannot be redirected later."""

    target = tmp_path / "runtime.exe"
    target.write_bytes(b"fixture")
    linked = tmp_path / "linked-runtime.exe"
    try:
        linked.symlink_to(target)
    except OSError:
        pytest.skip("Creating symbolic links is not permitted on this host.")

    with pytest.raises(song_cover_worker._SongCoverFailure, match="unsafe"):
        song_cover_worker._require_file(linked, "Runtime")


def test_singing_runtime_pins_model_metadata_and_import_providers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject changed Demucs model selection and an import-shadowing extension."""

    _create_singing_runtime_fixture(tmp_path)
    file_count, total_bytes, digest = _singing_runtime_digest(tmp_path)
    monkeypatch.setattr(
        song_cover_worker,
        "_EXPECTED_SINGING_RUNTIME_FILES",
        file_count,
    )
    monkeypatch.setattr(
        song_cover_worker,
        "_EXPECTED_SINGING_RUNTIME_BYTES",
        total_bytes,
    )
    monkeypatch.setattr(
        song_cover_worker,
        "_EXPECTED_SINGING_RUNTIME_SHA256",
        digest,
    )

    song_cover_worker._require_singing_runtime(tmp_path)

    model_selection = tmp_path / "demucs" / "remote" / "htdemucs.yaml"
    model_selection.write_text("models: ['unreviewed']\n", encoding="utf-8")
    with pytest.raises(song_cover_worker._SongCoverFailure, match="does not match"):
        song_cover_worker._require_singing_runtime(tmp_path)

    model_selection.write_text("models: ['955717e8']\n", encoding="utf-8")
    (tmp_path / "retrying.cp39-win_amd64.pyd").write_bytes(b"shadow")
    with pytest.raises(song_cover_worker._SongCoverFailure, match="conflicting"):
        song_cover_worker._require_singing_runtime(tmp_path)


def test_singing_loader_rejects_an_earlier_import_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject an earlier module even when the reviewed aggregate is unchanged."""

    earlier_provider = tmp_path / "earlier" / "dora.py"
    earlier_provider.parent.mkdir()
    earlier_provider.write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
    original_find_spec = song_cover_worker.importlib.util.find_spec

    def find_spec(module_name: str) -> importlib.machinery.ModuleSpec | None:
        """Expose one forged earlier provider and hide unrelated test imports."""

        if module_name == "dora":
            return importlib.machinery.ModuleSpec(
                module_name,
                loader=None,
                origin=str(earlier_provider),
            )
        if module_name in song_cover_worker._SINGING_RUNTIME_IMPORTS:
            return None
        return original_find_spec(module_name)

    monkeypatch.setattr(song_cover_worker.importlib.util, "find_spec", find_spec)

    with pytest.raises(song_cover_worker._SongCoverFailure, match="unexpected"):
        song_cover_worker._load_singing_runtime_modules(tmp_path)
