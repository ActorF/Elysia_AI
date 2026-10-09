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


def _write_pcm16_mono(
    path: Path,
    samples: array[int],
    *,
    sample_rate: int = 44_100,
) -> None:
    """Write one canonical mono PCM fixture for worker audio tests."""

    content = array("h", samples)
    if song_cover_worker.sys.byteorder != "little":
        content.byteswap()
    with wave.open(str(path), "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(sample_rate)
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


def test_python_tree_audit_rejects_an_added_importable_module(tmp_path: Path) -> None:
    """Reject Python code added after the reviewed RVC tree was pinned."""

    source = tmp_path / "runtime.py"
    content = b'"""Reviewed fixture."""\nVALUE = 1\n'
    source.write_bytes(content)
    digest = hashlib.sha256()
    digest.update(b"runtime.py")
    digest.update(b"\0")
    digest.update(str(len(content)).encode("ascii"))
    digest.update(b"\0")
    digest.update(content)

    song_cover_worker._require_python_tree(
        tmp_path,
        label="RVC Python runtime",
        expected_files=1,
        expected_bytes=len(content),
        expected_sha256=digest.hexdigest(),
    )
    (tmp_path / "shadow.py").write_text(
        '"""Unreviewed import provider."""\n',
        encoding="utf-8",
    )

    with pytest.raises(song_cover_worker._SongCoverFailure, match="does not match"):
        song_cover_worker._require_python_tree(
            tmp_path,
            label="RVC Python runtime",
            expected_files=1,
            expected_bytes=len(content),
            expected_sha256=digest.hexdigest(),
        )


@pytest.mark.parametrize("shadow_name", ["ffmpeg.exe", "launch.cmd", "build.ps1"])
def test_python_tree_audit_rejects_native_command_shadows(
    tmp_path: Path,
    shadow_name: str,
) -> None:
    """Reject executable or command-script payloads beside reviewed RVC code."""

    source = tmp_path / "runtime.py"
    content = b'"""Reviewed fixture."""\nVALUE = 1\n'
    source.write_bytes(content)
    digest = hashlib.sha256()
    digest.update(b"runtime.py")
    digest.update(b"\0")
    digest.update(str(len(content)).encode("ascii"))
    digest.update(b"\0")
    digest.update(content)
    (tmp_path / shadow_name).write_bytes(b"unreviewed-command")

    with pytest.raises(song_cover_worker._SongCoverFailure, match="executable"):
        song_cover_worker._require_python_tree(
            tmp_path,
            label="RVC Python runtime",
            expected_files=1,
            expected_bytes=len(content),
            expected_sha256=digest.hexdigest(),
        )


def test_rvc_environment_uses_only_pinned_tools_and_rejects_python_sibling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Close PATH and reject a command shadow beside the child interpreter."""

    rvc_root = tmp_path / "rvc"
    python_root = tmp_path / "python"
    native_root = tmp_path / "native"
    for directory in (rvc_root, python_root, native_root):
        directory.mkdir()
    ffmpeg = native_root / "ffmpeg.exe"
    ffprobe = native_root / "ffprobe.exe"
    ffmpeg.write_bytes(b"trusted-ffmpeg")
    ffprobe.write_bytes(b"trusted-ffprobe")
    monkeypatch.setattr(
        song_cover_worker.sys,
        "executable",
        str(python_root / "python.exe"),
    )

    environment = song_cover_worker._rvc_subprocess_environment(
        rvc_root=rvc_root,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
    )

    assert environment["PATH"] == str(native_root.resolve())
    assert environment["PATHEXT"] == ".EXE"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"

    (python_root / "ffmpeg.exe").write_bytes(b"shadow")
    with pytest.raises(song_cover_worker._SongCoverFailure, match="unsafe"):
        song_cover_worker._rvc_subprocess_environment(
            rvc_root=rvc_root,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )


def test_runtime_data_manifest_rejects_changed_json(tmp_path: Path) -> None:
    """Reject model configuration data changed after runtime review."""

    relative_name = "configs/v2/40k.json"
    config = tmp_path / relative_name
    config.parent.mkdir(parents=True)
    content = b'{"value": 1}\n'
    config.write_bytes(content)
    digest = hashlib.sha256()
    digest.update(relative_name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(len(content)).encode("ascii"))
    digest.update(b"\0")
    digest.update(content)

    song_cover_worker._require_file_manifest(
        tmp_path,
        label="RVC runtime data",
        expected_names=(relative_name,),
        expected_files=1,
        expected_bytes=len(content),
        expected_sha256=digest.hexdigest(),
    )
    config.write_bytes(b'{"value": 2}\n')

    with pytest.raises(song_cover_worker._SongCoverFailure, match="does not match"):
        song_cover_worker._require_file_manifest(
            tmp_path,
            label="RVC runtime data",
            expected_names=(relative_name,),
            expected_files=1,
            expected_bytes=len(content),
            expected_sha256=digest.hexdigest(),
        )


def test_locale_tree_rejects_an_added_system_locale(tmp_path: Path) -> None:
    """Reject a locale file the vendor could select from the host setting."""

    locale_root = tmp_path / "i18n" / "locale"
    locale_root.mkdir(parents=True)
    reviewed = locale_root / "en_US.json"
    content = b'{"locale": "reviewed"}\n'
    reviewed.write_bytes(content)
    digest = hashlib.sha256()
    digest.update(b"en_US.json")
    digest.update(b"\0")
    digest.update(str(len(content)).encode("ascii"))
    digest.update(b"\0")
    digest.update(content)

    song_cover_worker._require_asset_tree(
        locale_root,
        label="RVC locale data",
        expected_names=("en_US.json",),
        expected_files=1,
        expected_bytes=len(content),
        expected_sha256=digest.hexdigest(),
    )
    (locale_root / "de_DE.json").write_text(
        '{"locale": "unreviewed"}\n',
        encoding="utf-8",
    )

    with pytest.raises(song_cover_worker._SongCoverFailure, match="does not match"):
        song_cover_worker._require_asset_tree(
            locale_root,
            label="RVC locale data",
            expected_names=("en_US.json",),
            expected_files=1,
            expected_bytes=len(content),
            expected_sha256=digest.hexdigest(),
        )


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


def test_mix_cover_omits_the_original_consonant_layer_for_rvc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid mixing the source singer back into an RVC-converted vocal."""

    commands: list[list[str]] = []
    monkeypatch.setattr(
        song_cover_worker,
        "_run_command",
        lambda arguments, _failure: commands.append(list(arguments)),
    )

    song_cover_worker._mix_cover(
        Path("ffmpeg.exe"),
        Path("rvc-vocals.wav"),
        None,
        Path("instrumental.wav"),
        Path("cover.wav"),
        Path("cover.mp3"),
        target_samples=441_000,
    )

    mix_command = commands[0]
    filter_graph = mix_command[mix_command.index("-filter_complex") + 1]
    assert mix_command.count("-i") == 2
    assert "consonants.wav" not in mix_command
    assert (
        f"volume={song_cover_worker._CONSONANT_LAYER_GAIN:.6f}[c]"
        not in filter_graph
    )
    assert "[vc][c]" not in filter_graph
    assert "highpass=f=60" in filter_graph
    assert "apad=whole_len=441000" in filter_graph
    assert "atrim=end_sample=441000" in filter_graph


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
        "--rvc-root", "rvc",
        "--rvc-model", "model.pth",
        "--rvc-index", "model.index",
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
    assert song.rvc_root == Path("rvc")
    assert song.rvc_model == Path("model.pth")
    assert song.rvc_index == Path("model.index")
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
        "--rvc-root", "rvc",
        "--rvc-model", "model.pth",
        "--rvc-index", "model.index",
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


def test_rvc_index_preflight_rejects_silent_retrieval_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject an index shape that the vendor pipeline would silently ignore."""

    class IndexIVFFlat:
        """Expose the reviewed FAISS metadata without reading a private index."""

        d = 768
        ntotal = 10_000
        nlist = 256
        nprobe = 1
        is_trained = True

    index = IndexIVFFlat()

    class FakeFaiss:
        """Return the deterministic in-memory index fixture."""

        @staticmethod
        def read_index(_path: str) -> IndexIVFFlat:
            """Mirror FAISS's single-file loader contract."""

            return index

    monkeypatch.setitem(song_cover_worker.sys.modules, "faiss", FakeFaiss)
    song_cover_worker._require_rvc_index_compatibility(Path("elysia.index"))

    index.nprobe = 2
    with pytest.raises(song_cover_worker._SongCoverFailure, match="incompatible"):
        song_cover_worker._require_rvc_index_compatibility(Path("elysia.index"))


def test_rvc_output_duration_rejects_a_truncated_conversion(tmp_path: Path) -> None:
    """Allow an analysis-frame difference but reject a padded partial result."""

    near_complete = tmp_path / "near-complete.wav"
    truncated = tmp_path / "truncated.wav"
    _write_pcm16_mono(
        near_complete,
        array("h", [0]) * 399_200,
        sample_rate=40_000,
    )
    _write_pcm16_mono(
        truncated,
        array("h", [0]) * 200_000,
        sample_rate=40_000,
    )

    song_cover_worker._require_rvc_output_duration(
        near_complete,
        target_samples=441_000,
    )
    with pytest.raises(song_cover_worker._SongCoverFailure, match="incomplete"):
        song_cover_worker._require_rvc_output_duration(
            truncated,
            target_samples=441_000,
        )


@pytest.mark.parametrize("key_shift", [-2, 2])
def test_convert_vocals_runs_rvc_and_normalizes_exact_length(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    key_shift: int,
) -> None:
    """Invoke the closed RVC adapter then resample without time stretching."""

    rvc_root = tmp_path / "rvc"
    rvc_root.mkdir()
    runtime = tmp_path / "song_rvc_runtime.py"
    model = tmp_path / "elysia.pth"
    index = tmp_path / "elysia.index"
    hubert = rvc_root / "assets" / "hubert_base"
    hubert.mkdir(parents=True)
    rmvpe = rvc_root / "assets" / "rmvpe" / "rmvpe.pt"
    rmvpe.parent.mkdir()
    vocals = tmp_path / "vocals.wav"
    output = tmp_path / "converted.wav"
    ffprobe = tmp_path / "ffprobe.exe"
    for candidate in (runtime, model, index, rmvpe, vocals):
        candidate.write_bytes(b"fixture")
    commands: list[list[str]] = []
    environments: list[object] = []
    closed_environment = {"PATH": "trusted-native-directory"}

    def fake_run(
        arguments: list[str],
        _failure_message: str,
        *,
        environment: object = None,
    ) -> None:
        """Capture both processes and materialize each expected output."""

        command = list(arguments)
        commands.append(command)
        environments.append(environment)
        if "--output" in command:
            expected_samples = round(44_123 * 40_000 / 44_100)
            _write_pcm16_mono(
                Path(command[command.index("--output") + 1]),
                array("h", [0]) * expected_samples,
                sample_rate=40_000,
            )
        else:
            Path(command[-1]).write_bytes(b"normalized-44k1")

    monkeypatch.setattr(song_cover_worker, "_run_command", fake_run)
    monkeypatch.setattr(
        song_cover_worker,
        "_rvc_subprocess_environment",
        lambda **_arguments: closed_environment,
    )

    song_cover_worker._convert_vocals(
        ffmpeg=Path("ffmpeg.exe"),
        ffprobe=ffprobe,
        vocals=vocals,
        output=output,
        rvc_runtime=runtime,
        rvc_root=rvc_root,
        rvc_model=model,
        rvc_index=index,
        hubert=hubert,
        rmvpe=rmvpe,
        key_shift_semitones=key_shift,
        target_samples=44_123,
    )

    assert len(commands) == 2
    rvc_command, normalize_command = commands
    assert environments == [closed_environment, None]
    assert rvc_command[0] == str(Path(song_cover_worker.sys.executable))
    assert rvc_command[1] == str(runtime)
    assert rvc_command[rvc_command.index("--source-root") + 1] == str(rvc_root)
    assert rvc_command[rvc_command.index("--model") + 1] == str(model)
    assert rvc_command[rvc_command.index("--index") + 1] == str(index)
    assert rvc_command[rvc_command.index("--hubert") + 1] == str(hubert)
    assert rvc_command[rvc_command.index("--rmvpe") + 1] == str(rmvpe)
    assert rvc_command[rvc_command.index("--input") + 1] == str(vocals)
    assert rvc_command[rvc_command.index("--key-shift") + 1] == str(key_shift)
    audio_filter = normalize_command[normalize_command.index("-af") + 1]
    assert "aresample=44100" in audio_filter
    assert "apad=whole_len=44123" in audio_filter
    assert "atrim=end_sample=44123" in audio_filter
    assert "asetpts=N/SR/TB" in audio_filter
    assert "atempo" not in audio_filter
    assert "asetrate" not in audio_filter
    assert normalize_command[normalize_command.index("-ac") + 1] == "1"
    assert output.read_bytes() == b"normalized-44k1"
    assert not output.with_name(".converted-rvc-40k.wav").exists()


def test_convert_vocals_clears_private_scratch_after_normalization_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Delete raw RVC and partial normalized audio when FFmpeg fails."""

    output = tmp_path / "converted.wav"
    calls = 0

    def fail_normalization(
        arguments: list[str],
        _failure_message: str,
        *,
        environment: object = None,
    ) -> None:
        """Let RVC finish, then leave a partial FFmpeg file before failing."""

        nonlocal calls
        calls += 1
        command = list(arguments)
        if calls == 1:
            _write_pcm16_mono(
                Path(command[command.index("--output") + 1]),
                array("h", [0]) * 40_000,
                sample_rate=40_000,
            )
            return
        output.write_bytes(b"partial-private-output")
        raise song_cover_worker._SongCoverFailure("normalization failed")

    monkeypatch.setattr(song_cover_worker, "_run_command", fail_normalization)
    monkeypatch.setattr(
        song_cover_worker,
        "_rvc_subprocess_environment",
        lambda **_arguments: {"PATH": "trusted-native-directory"},
    )

    with pytest.raises(song_cover_worker._SongCoverFailure, match="normalization"):
        song_cover_worker._convert_vocals(
            ffmpeg=Path("ffmpeg.exe"),
            ffprobe=Path("ffprobe.exe"),
            vocals=tmp_path / "vocals.wav",
            output=output,
            rvc_runtime=tmp_path / "song_rvc_runtime.py",
            rvc_root=tmp_path / "rvc",
            rvc_model=tmp_path / "elysia.pth",
            rvc_index=tmp_path / "elysia.index",
            hubert=tmp_path / "hubert",
            rmvpe=tmp_path / "rmvpe.pt",
            key_shift_semitones=0,
            target_samples=44_100,
        )

    assert not output.exists()
    assert not output.with_name(".converted-rvc-40k.wav").exists()


def test_rvc_base_asset_identities_are_pinned() -> None:
    """Keep silently replaced HuBERT or RMVPE weights outside inference."""

    assert song_cover_worker._EXPECTED_HUBERT_FILES == 3
    assert song_cover_worker._EXPECTED_HUBERT_BYTES == 189_207_510
    assert song_cover_worker._EXPECTED_HUBERT_SHA256 == (
        "c4843b2163be0aebac54b770579ad8c2b107f42c2fbc658d7b8ce7d244a54560"
    )
    assert song_cover_worker._EXPECTED_RVC_PYTHON_FILES == 130
    assert song_cover_worker._EXPECTED_RVC_PYTHON_BYTES == 1_382_718
    assert song_cover_worker._EXPECTED_RVC_PYTHON_SHA256 == (
        "a391270fe0b38307c3c966c5cb394d947d177990fae64307cb38313ae762b6c5"
    )
    assert song_cover_worker._EXPECTED_RVC_RUNTIME_DATA_FILES == 18
    assert song_cover_worker._EXPECTED_RVC_RUNTIME_DATA_BYTES == 354_205
    assert song_cover_worker._EXPECTED_RVC_RUNTIME_DATA_SHA256 == (
        "3e7dcf0b44cfd379d6eb658c3719159d2d3f9234e7ec4338b0cdd90924b1793b"
    )
    assert song_cover_worker._EXPECTED_RVC_LOCALE_FILES == 13
    assert song_cover_worker._EXPECTED_RVC_LOCALE_BYTES == 348_857
    assert song_cover_worker._EXPECTED_RVC_LOCALE_SHA256 == (
        "f1f2621ebf78c3b34ce70f861bd006e65f368708b44cd365877f236586b57459"
    )
    assert song_cover_worker._EXPECTED_RMVPE_BYTES == 181_184_272
    assert song_cover_worker._EXPECTED_RMVPE_SHA256 == (
        "6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193"
    )
    assert song_cover_worker._EXPECTED_RVC_MODEL_BYTES == 55_232_507
    assert song_cover_worker._EXPECTED_RVC_MODEL_SHA256 == (
        "cb3fec4d975eafd7c6b73bd096a6e6cdd6b9c3ca1d6793320d48fb17bea2b9c8"
    )
    assert song_cover_worker._EXPECTED_RVC_INDEX_BYTES == 31_588_619
    assert song_cover_worker._EXPECTED_RVC_INDEX_SHA256 == (
        "86a2da597f7a09d8cb27bd2dad3f1bcfa6fd6a561268622f4daf94ab1de8737e"
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
            "--rvc-root",
            "missing-rvc-root",
            "--rvc-model",
            "missing-model.pth",
            "--rvc-index",
            "missing-model.index",
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
