"""Test the loopback ComfyUI adapter against an offline fake HTTP server."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import struct
from threading import Thread
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
import zlib

import pytest
import requests

import image_generation.comfyui as comfyui
from image_generation.comfyui import (
    ComfyOutputDescriptor,
    ComfyPromptHandle,
    ComfyUiConfig,
    HttpComfyUiClient,
    normalize_comfyui_base_url,
)
from image_generation.domain import ImageGenerationSpec, ImageLoraSelection
from image_generation.exceptions import (
    ImageGenerationFailedError,
    ImageGenerationProtocolError,
    ImageGenerationUnavailableError,
)
from image_generation.png import PNG_SIGNATURE
from image_generation.profiles import (
    ImageSizePreset,
    ModelArchitecture,
    ModelLoraProfile,
    ModelProfile,
    PromptDialect,
)
from image_generation.workflow import compile_txt2img_workflow
from image_generation.workflow import CompiledWorkflow


def _chunk(kind: bytes, payload: bytes) -> bytes:
    """Encode one valid PNG chunk for the fake view endpoint."""

    checksum = zlib.crc32(kind)
    checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", checksum)
    )


def _png(width: int = 512, height: int = 512) -> bytes:
    """Return a deterministic RGBA PNG accepted by the production validator."""

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    row = b"\x00" + bytes(width * 4)
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(row * height))
        + _chunk(b"IEND", b"")
    )


def _profile() -> ModelProfile:
    """Return the trusted synthetic profile used by fake server tests."""

    return ModelProfile(
        profile_id="anime-local",
        display_name="Synthetic SDXL fixture",
        architecture=ModelArchitecture.SDXL,
        prompt_dialect=PromptDialect.SDXL_NATURAL,
        checkpoint_name="fixtures/anime-model.safetensors",
        checkpoint_sha256="0" * 64,
        checkpoint_source="Unit-test fixture; no model is distributed.",
        checkpoint_license="Synthetic test declaration.",
        size_presets=(ImageSizePreset("square", 512, 512),),
        default_size_preset_id="square",
    )


def _workflow() -> CompiledWorkflow:
    """Compile one immutable graph for adapter tests."""

    return compile_txt2img_workflow(
        ImageGenerationSpec(
            profile_id="anime-local",
            size_preset_id="square",
            appearance="adult character with pink hair",
            seed=7,
            original_fictional_confirmed=True,
        ),
        _profile(),
        job_id=str(uuid4()),
    )


class _ServerState:
    """Hold deterministic fake ComfyUI responses and observed requests."""

    def __init__(self) -> None:
        """Initialize one successful immediate-completion runtime script."""

        self.prompt_id = str(uuid4())
        self.models = [_profile().checkpoint_name]
        self.loras: object = []
        self.missing_nodes: set[str] = set()
        self.running: list[list[object]] = []
        self.pending: list[list[object]] = []
        self.history_override: object | None = None
        self.redirect_paths: set[str] = set()
        self.requests: list[tuple[str, str, object | None]] = []
        self.submitted: dict[str, Any] | None = None
        self.client_id: str | None = None
        self.system_stats: object = {
            "system": {
                "comfyui_version": "0.3.65",
                "argv": ["main.py", "--sensitive-fixture-token"],
            },
            "devices": [{"name": "synthetic-device"}],
        }
        self.output_filename = "Elysia-00000000-0000-4000-8000-000000000000_00001_.png"
        self.image = _png()


class _DeadlineSocket:
    """Record the shrinking timeouts applied before scripted body reads."""

    def __init__(self) -> None:
        """Start with no observed receive timeout."""

        self.timeouts: list[float] = []

    def settimeout(self, timeout: float) -> None:
        """Capture one timeout without performing real socket I/O."""

        self.timeouts.append(timeout)


class _DeadlineConnection:
    """Expose the pinned urllib3 connection shape used by the adapter."""

    def __init__(self, socket: _DeadlineSocket) -> None:
        """Attach the fake active socket."""

        self.sock = socket


class _DeadlineRaw:
    """Return one scripted body arrival for every raw ``read1`` call."""

    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        """Store body arrivals and the fake active response socket."""

        self._chunks = list(chunks)
        self.socket = _DeadlineSocket()
        self._connection = _DeadlineConnection(self.socket)
        self.closed = False

    def read1(self, size: int, *, decode_content: bool) -> bytes:
        """Return one arrival so each drip crosses a fresh deadline check."""

        assert size == 64 * 1024
        assert decode_content is False
        if not self._chunks:
            self.closed = True
            return b""
        chunk = self._chunks.pop(0)
        if not self._chunks:
            self.closed = True
        return chunk


class _Handler(BaseHTTPRequestHandler):
    """Serve the narrow raw ComfyUI API used by the production adapter."""

    server: _StateServer

    def log_message(self, _format: str, *_args: object) -> None:
        """Suppress test-server access logs."""

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        location: str | None = None,
    ) -> None:
        """Write one length-delimited fake response."""

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if location is not None:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value: object) -> None:
        """Write one canonical JSON response."""

        self._send(
            200,
            json.dumps(value, separators=(",", ":")).encode("utf-8"),
            "application/json",
        )

    def do_GET(self) -> None:
        """Serve probes, queue/history observation, and verified image bytes."""

        parsed = urlsplit(self.path)
        state = self.server.state
        state.requests.append(("GET", parsed.path, parse_qs(parsed.query)))
        if parsed.path in state.redirect_paths:
            self._send(302, b"", "text/plain", location="http://example.com/")
            return
        if parsed.path == "/system_stats":
            self._json(state.system_stats)
            return
        if parsed.path.startswith("/object_info/"):
            node_class = parsed.path.rsplit("/", 1)[1]
            if node_class in state.missing_nodes:
                self._json({})
                return
            self._json({node_class: {"input": {}}})
            return
        if parsed.path == "/models/checkpoints":
            self._json(state.models)
            return
        if parsed.path == "/models/loras":
            self._json(state.loras)
            return
        if parsed.path.startswith("/history/"):
            prompt_id = parsed.path.rsplit("/", 1)[1]
            if state.history_override is not None:
                self._json(state.history_override)
                return
            self._json({
                prompt_id: {
                    "prompt": [
                        1,
                        prompt_id,
                        {},
                        {"client_id": state.client_id},
                    ],
                    "outputs": {
                        "7": {
                            "images": [{
                                "filename": state.output_filename,
                                "subfolder": "",
                                "type": "output",
                                "id": "asset-fixture",
                                "metadata": {
                                    "kind": "output",
                                    "width": 512,
                                    "height": 512,
                                },
                            }],
                        },
                    },
                    "status": {"completed": True, "status_str": "success"},
                },
            })
            return
        if parsed.path == "/queue":
            self._json({
                "queue_pending": state.pending,
                "queue_running": state.running,
            })
            return
        if parsed.path == "/view":
            self._send(200, state.image, "image/png")
            return
        self._send(404, b"missing", "text/plain")

    def do_POST(self) -> None:
        """Accept prompt submission and record safe cancellation controls."""

        parsed = urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0"), 10)
        body = self.rfile.read(length)
        try:
            document = json.loads(body) if body else None
        except json.JSONDecodeError:
            document = None
        state = self.server.state
        state.requests.append(("POST", parsed.path, document))
        if parsed.path == "/prompt":
            assert isinstance(document, dict)
            state.submitted = document
            state.client_id = document["client_id"]
            state.prompt_id = document["prompt_id"]
            state.output_filename = (
                document["prompt"]["7"]["inputs"]["filename_prefix"]
                + "_00001_.png"
            )
            self._json({"node_errors": {}, "prompt_id": state.prompt_id})
            return
        if parsed.path in {"/queue", "/interrupt"}:
            self._send(200, b"", "application/octet-stream")
            return
        self._send(404, b"missing", "text/plain")


class _StateServer(ThreadingHTTPServer):
    """Attach typed mutable state to Python's local threaded HTTP server."""

    state: _ServerState


@contextmanager
def _fake_server() -> Iterator[tuple[str, _ServerState]]:
    """Run one loopback-only fake ComfyUI server for a bounded test scope."""

    state = _ServerState()
    server = _StateServer(("127.0.0.1", 0), _Handler)
    server.state = state
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = server.server_address[0]
        port = server.server_address[1]
        assert type(host) is str
        assert type(port) is int
        yield f"http://{host}:{port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def _client(base_url: str, *, exclusive: bool = False) -> HttpComfyUiClient:
    """Return one short-deadline adapter for the fake local server."""

    return HttpComfyUiClient(ComfyUiConfig(
        base_url=base_url,
        probe_timeout_seconds=1.0,
        request_timeout_seconds=1.0,
        job_timeout_seconds=1.0,
        poll_interval_seconds=0.01,
        exclusive_instance=exclusive,
    ))


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8188",
        "http://example.com:8188",
        "http://user:pass@127.0.0.1:8188",
        "http://127.0.0.1:8188/api",
        "http://127.0.0.1:8188?x=1",
    ],
)
def test_base_url_rejects_remote_or_ambiguous_origins(url: str) -> None:
    """Keep runtime discovery from becoming an SSRF or credential channel."""

    with pytest.raises(ValueError, match="ComfyUI"):
        normalize_comfyui_base_url(url)


def test_base_url_canonicalizes_localhost_before_io() -> None:
    """Pin the admitted hostname to a literal loopback address."""

    assert (
        normalize_comfyui_base_url("http://localhost:8188")
        == "http://127.0.0.1:8188"
    )


def test_adapter_probes_submits_correlates_and_fetches_one_png() -> None:
    """Exercise the successful fixed workflow without a GPU or real ComfyUI."""

    with _fake_server() as (base_url, state):
        client = _client(base_url)
        profile = _profile()
        workflow = _workflow()

        client.ensure_ready(profile)
        handle = client.submit(workflow)
        descriptor = client.wait_for_output(handle, "7")
        image = client.fetch_png(
            descriptor,
            expected_width=512,
            expected_height=512,
        )

    assert handle.prompt_id == state.prompt_id
    assert state.submitted is not None
    assert state.submitted["prompt_id"] == handle.prompt_id
    assert state.submitted["prompt"]["4"]["inputs"]["batch_size"] == 1
    assert image.width == 512
    assert image.height == 512


def test_prepare_mints_identity_before_submission_network_io() -> None:
    """Let durable callers persist a handle before ComfyUI accepts its job."""

    with _fake_server() as (base_url, state):
        client = _client(base_url)
        workflow = _workflow()

        handle = client.prepare(workflow)
        assert state.requests == []
        client.submit_prepared(handle, workflow)

    assert state.submitted is not None
    assert state.submitted["prompt_id"] == handle.prompt_id
    assert state.submitted["client_id"] == handle.client_id


def test_raw_adapter_never_claims_a_managed_private_runtime() -> None:
    """Keep external raw ComfyUI outside private managed-runtime trust paths."""

    with _fake_server() as (base_url, _state):
        assert _client(base_url).is_managed_private_runtime is False


def test_readiness_probe_accepts_initialized_runtime_without_returning_stats(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Discard system statistics because current ComfyUI includes argv secrets."""

    caplog.set_level("DEBUG")
    with _fake_server() as (base_url, state):
        result = _client(base_url).probe_ready()  # type: ignore[func-returns-value]

    assert result is None
    assert ("GET", "/system_stats", {}) in state.requests
    assert "sensitive-fixture-token" not in caplog.text


@pytest.mark.parametrize(
    "document",
    [
        None,
        [],
        {},
        {"system": {}, "devices": []},
        {"system": {"comfyui_version": ""}, "devices": []},
        {"system": {"comfyui_version": 3}, "devices": []},
        {"system": {"comfyui_version": "0.3.65"}, "devices": {}},
        {
            "system": {"comfyui_version": "0.3.65"},
            "devices": [{}] * 257,
        },
    ],
)
def test_readiness_probe_rejects_malformed_system_stats(document: object) -> None:
    """Fail closed when a listener is not an initialized compatible runtime."""

    with _fake_server() as (base_url, state):
        state.system_stats = document

        with pytest.raises(ImageGenerationProtocolError, match="invalid"):
            _client(base_url).probe_ready()


def test_probe_fails_closed_when_the_checkpoint_is_not_installed() -> None:
    """Do not silently substitute a mutable or similarly named checkpoint."""

    with _fake_server() as (base_url, state):
        state.models = ["another-model.safetensors"]

        with pytest.raises(ImageGenerationUnavailableError, match="checkpoint"):
            _client(base_url).ensure_ready(_profile())


@pytest.mark.parametrize("problem", ["none", "missing-file", "missing-node", "invalid-list"])
def test_selected_lora_readiness_is_conditional_and_checks_exact_key(problem: str) -> None:
    """Probe the fixed loader and selected model only when the optional asset is used."""

    lora = ModelLoraProfile(
        lora_id="reviewed", display_name="Reviewed fixture", architecture=ModelArchitecture.SDXL,
        lora_name="fixtures/reviewed.safetensors", lora_sha256="a" * 64,
        lora_source="Synthetic fixture", lora_license="Synthetic declaration",
    )
    profile = replace(_profile(), loras=(lora,))
    with _fake_server() as (base_url, state):
        client = _client(base_url)
        client.ensure_ready(profile)
        assert not any(path in {"/models/loras", "/object_info/LoraLoader"} for _, path, _ in state.requests)
        state.loras = [lora.lora_name]
        if problem == "missing-file":
            state.loras = ["different.safetensors"]
        elif problem == "missing-node":
            state.missing_nodes = {"LoraLoader"}
        elif problem == "invalid-list":
            state.loras = {"loras": [lora.lora_name]}
        if problem == "none":
            client.ensure_ready(profile, lora=lora)
            assert ("GET", "/models/loras", {}) in state.requests
        else:
            error = ImageGenerationProtocolError if problem == "invalid-list" else ImageGenerationUnavailableError
            with pytest.raises(error):
                client.ensure_ready(profile, lora=lora)


def test_adapter_submits_selected_sealed_lora_graph_without_extending_graph_inputs() -> None:
    """Send the fixed optional loader through the same correlated PNG pipeline."""

    lora = ModelLoraProfile(
        lora_id="reviewed", display_name="Reviewed fixture", architecture=ModelArchitecture.SDXL,
        lora_name="fixtures/reviewed.safetensors", lora_sha256="a" * 64,
        lora_source="Synthetic fixture", lora_license="Synthetic declaration",
    )
    profile = replace(_profile(), loras=(lora,))
    workflow = compile_txt2img_workflow(
        ImageGenerationSpec(
            profile_id=profile.profile_id, size_preset_id="square", appearance="original adult portrait",
            original_fictional_confirmed=True, lora=ImageLoraSelection(lora.lora_id, 0.75, 0.5),
        ), profile, job_id=str(uuid4()),
    )
    with _fake_server() as (base_url, state):
        state.loras = [lora.lora_name]
        client = _client(base_url)
        client.ensure_ready(profile, lora=lora)
        handle = client.submit(workflow)
        descriptor = client.wait_for_output(handle, "7")
        image = client.fetch_png(descriptor, expected_width=512, expected_height=512)

    assert image.width == image.height == 512
    assert state.submitted is not None
    graph = state.submitted["prompt"]
    assert len(graph) == 8
    assert graph["8"]["inputs"]["lora_name"] == lora.lora_name
    assert graph["8"]["inputs"]["strength_model"] == 0.75
    assert graph["8"]["inputs"]["strength_clip"] == 0.5


@pytest.mark.parametrize("missing_index", [None, 0, 1])
def test_multi_lora_readiness_requires_every_selected_key(missing_index: int | None) -> None:
    """Probe one built-in loader and reject a missing character or style asset equally."""

    first = ModelLoraProfile(
        lora_id="character", display_name="Character fixture", architecture=ModelArchitecture.SDXL,
        lora_name="fixtures/character.safetensors", lora_sha256="a" * 64,
        lora_source="Synthetic fixture", lora_license="Synthetic declaration",
    )
    second = replace(first, lora_id="style", lora_name="fixtures/style.safetensors")
    assets = (first, second)
    profile = replace(_profile(), loras=assets)
    with _fake_server() as (base_url, state):
        state.loras = [asset.lora_name for index, asset in enumerate(assets) if index != missing_index]
        client = _client(base_url)
        if missing_index is None:
            client.ensure_ready(profile, loras=assets)
            assert sum(path == "/models/loras" for _, path, _ in state.requests) == 1
            assert sum(path == "/object_info/LoraLoader" for _, path, _ in state.requests) == 1
        else:
            with pytest.raises(ImageGenerationUnavailableError):
                client.ensure_ready(profile, loras=assets)


def test_raw_adapter_rejects_foreign_duplicate_and_overlapping_lora_declarations_before_http() -> None:
    """Do not expose unreviewed logical declarations to the runtime model-list boundary."""

    asset = ModelLoraProfile(
        lora_id="character", display_name="Character fixture", architecture=ModelArchitecture.SDXL,
        lora_name="character.safetensors", lora_sha256="a" * 64,
        lora_source="Synthetic fixture", lora_license="Synthetic declaration",
    )
    profile = replace(_profile(), loras=(asset,))
    with _fake_server() as (base_url, state):
        client = _client(base_url)
        for selected in ((asset, asset), (replace(asset),)):
            with pytest.raises(TypeError):
                client.ensure_ready(profile, loras=selected)
        with pytest.raises(TypeError):
            client.ensure_ready(profile, lora=asset, loras=(asset,))
        assert state.requests == []


def test_redirect_is_rejected_without_following_its_location() -> None:
    """Prevent a compromised loopback service from redirecting requests away."""

    with _fake_server() as (base_url, state):
        state.redirect_paths.add("/models/checkpoints")

        with pytest.raises(ImageGenerationUnavailableError, match="not running"):
            _client(base_url).ensure_ready(_profile())

        assert not any(
            request[1] == "/" for request in state.requests
        )


def test_body_reader_pins_each_slow_drip_to_one_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop a body whose small arrivals each satisfy a renewable timeout."""

    raw = _DeadlineRaw((b"{", b"}"))
    response = requests.Response()
    response.raw = cast(Any, raw)
    moments = iter((0.0, 0.01, 0.02, 0.03, 0.04, 0.2))
    monkeypatch.setattr(comfyui, "monotonic", lambda: next(moments))

    with pytest.raises(comfyui._WallClockDeadlineExceeded):
        comfyui._read_bounded_identity_body(
            response,
            deadline=0.1,
            max_bytes=16,
        )

    assert raw.socket.timeouts == [pytest.approx(0.09), pytest.approx(0.06)]


def test_history_rejects_a_foreign_prompt_record() -> None:
    """Never attach output from a prompt not correlated to our private handle."""

    with _fake_server() as (base_url, state):
        state.history_override = {
            str(uuid4()): {"outputs": {}},
        }
        handle = ComfyPromptHandle(
            prompt_id=state.prompt_id,
            client_id=str(uuid4()),
            output_filename_prefix=(
                f"Elysia-{uuid4()}"
            ),
        )

        with pytest.raises(ImageGenerationProtocolError, match="invalid"):
            _client(base_url).wait_for_output(handle, "7")


def test_history_rejects_output_without_an_explicit_terminal_status() -> None:
    """Do not infer success merely because an untrusted record has an image."""

    with _fake_server() as (base_url, state):
        handle = ComfyPromptHandle(
            prompt_id=state.prompt_id,
            client_id=str(uuid4()),
            output_filename_prefix=f"Elysia-{uuid4()}",
        )
        state.client_id = handle.client_id
        state.output_filename = handle.output_filename_prefix + "_00001_.png"
        state.history_override = {
            handle.prompt_id: {
                "prompt": [
                    1,
                    handle.prompt_id,
                    {},
                    {"client_id": handle.client_id},
                ],
                "outputs": {
                    "7": {
                        "images": [{
                            "filename": state.output_filename,
                            "subfolder": "",
                            "type": "output",
                        }],
                    },
                },
            },
        }

        with pytest.raises(ImageGenerationProtocolError, match="invalid"):
            _client(base_url).wait_for_output(handle, "7")


@pytest.mark.parametrize(
    "status",
    [
        {"completed": False, "status_str": "running"},
        {"completed": True, "status_str": "error"},
        {"completed": True, "status_str": "unexpected"},
    ],
)
def test_history_never_publishes_output_with_a_non_success_status(
    status: dict[str, object],
) -> None:
    """Require terminal success even when an inconsistent record has images."""

    with _fake_server() as (base_url, state):
        handle = ComfyPromptHandle(
            prompt_id=state.prompt_id,
            client_id=str(uuid4()),
            output_filename_prefix=f"Elysia-{uuid4()}",
        )
        state.client_id = handle.client_id
        state.output_filename = handle.output_filename_prefix + "_00001_.png"
        state.history_override = {
            handle.prompt_id: {
                "prompt": [
                    1,
                    handle.prompt_id,
                    {},
                    {"client_id": handle.client_id},
                ],
                "outputs": {
                    "7": {
                        "images": [{
                            "filename": state.output_filename,
                            "subfolder": "",
                            "type": "output",
                        }],
                    },
                },
                "status": status,
            },
        }

        if status["completed"] is False:
            with pytest.raises(ImageGenerationUnavailableError, match="time budget"):
                _client(base_url).wait_for_output(handle, "7")
        elif status["status_str"] == "error":
            with pytest.raises(ImageGenerationFailedError, match="failed"):
                _client(base_url).wait_for_output(handle, "7")
        else:
            with pytest.raises(ImageGenerationProtocolError, match="invalid"):
                _client(base_url).wait_for_output(handle, "7")


def test_pending_cancel_deletes_only_the_correlated_prompt() -> None:
    """Use ComfyUI's precise queued deletion instead of a global interrupt."""

    with _fake_server() as (base_url, state):
        handle = ComfyPromptHandle(
            prompt_id=state.prompt_id,
            client_id=str(uuid4()),
            output_filename_prefix=f"Elysia-{uuid4()}",
        )
        state.pending = [[
            1,
            state.prompt_id,
            {},
            {"client_id": handle.client_id},
            [],
        ]]

        assert _client(base_url).cancel(handle) is True

    post = [item for item in state.requests if item[:2] == ("POST", "/queue")]
    assert post == [("POST", "/queue", {"delete": [state.prompt_id]})]


def test_shared_active_cancel_never_interrupts_somebody_elses_instance() -> None:
    """Leave physical work running when the active endpoint is globally scoped."""

    with _fake_server() as (base_url, state):
        handle = ComfyPromptHandle(
            prompt_id=state.prompt_id,
            client_id=str(uuid4()),
            output_filename_prefix=f"Elysia-{uuid4()}",
        )
        state.running = [[
            1,
            state.prompt_id,
            {},
            {"client_id": handle.client_id},
            [],
        ]]

        assert _client(base_url, exclusive=False).cancel(handle) is False

    assert not any(path == "/interrupt" for _, path, _ in state.requests)


def test_exclusive_active_cancel_interrupts_only_our_sole_running_prompt() -> None:
    """Target our sole running prompt when exclusive ownership is configured."""

    with _fake_server() as (base_url, state):
        handle = ComfyPromptHandle(
            prompt_id=state.prompt_id,
            client_id=str(uuid4()),
            output_filename_prefix=f"Elysia-{uuid4()}",
        )
        state.running = [[
            1,
            state.prompt_id,
            {},
            {"client_id": handle.client_id},
            [],
        ]]

        assert _client(base_url, exclusive=True).cancel(handle) is True

    interrupts = [
        item for item in state.requests if item[:2] == ("POST", "/interrupt")
    ]
    assert interrupts == [
        ("POST", "/interrupt", {"prompt_id": state.prompt_id}),
    ]


@pytest.mark.parametrize("duplicate_location", ["running", "pending", "both"])
def test_cancel_rejects_ambiguous_duplicate_queue_ownership(
    duplicate_location: str,
) -> None:
    """Never mutate a queue whose prompt ownership snapshot is inconsistent."""

    with _fake_server() as (base_url, state):
        handle = ComfyPromptHandle(
            prompt_id=state.prompt_id,
            client_id=str(uuid4()),
            output_filename_prefix=f"Elysia-{uuid4()}",
        )
        record = [
            1,
            state.prompt_id,
            {},
            {"client_id": handle.client_id},
            [],
        ]
        if duplicate_location == "running":
            state.running = [record, list(record)]
        elif duplicate_location == "pending":
            state.pending = [record, list(record)]
        else:
            state.running = [record]
            state.pending = [list(record)]

        with pytest.raises(ImageGenerationProtocolError, match="invalid"):
            _client(base_url, exclusive=True).cancel(handle)

    assert not any(
        path in {"/queue", "/interrupt"} and method == "POST"
        for method, path, _body in state.requests
    )


@pytest.mark.parametrize(
    ("filename", "subfolder"),
    [
        ("../image.png", ""),
        ("image.png", "../escape"),
        ("C:image.png", ""),
        ("image.jpg", ""),
    ],
)
def test_output_descriptor_rejects_path_aliases(
    filename: str,
    subfolder: str,
) -> None:
    """Keep server-returned names as bounded query values, never local paths."""

    with pytest.raises(ImageGenerationProtocolError, match="invalid output"):
        ComfyOutputDescriptor(filename, subfolder, "output")
