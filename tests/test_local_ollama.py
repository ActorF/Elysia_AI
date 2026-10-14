"""Prove local Chat and discovery cannot escape through origins, proxies or redirects."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from urllib.error import HTTPError

import pytest

from config.desktop_settings import (
    DesktopSettingsValidationError, validate_model_name, validate_ollama_host,
)
from core.exceptions import ChatModelResponseError
from core.langchain_ollama_chat_model import LangChainOllamaChatModel
from core.local_ollama import (
    is_cloud_ollama_model_name, normalize_local_ollama_origin, open_local_ollama_tags,
)
from core.ollama_chat_model import OllamaChatModel


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://localhost:11434/", "http://127.0.0.1:11434"),
        ("http://LOCALHOST", "http://127.0.0.1"),
        ("http://127.12.34.56:1234", "http://127.12.34.56:1234"),
        ("http://[::1]:11434", "http://[::1]:11434"),
        ("http://[0:0:0:0:0:0:0:1]/", "http://[::1]"),
    ],
)
def test_local_origins_are_canonicalized_without_dns(value: str, expected: str) -> None:
    """Keep every accepted host literal while preserving the selected local port."""
    assert normalize_local_ollama_origin(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None, "", "http://example.test", "http://192.168.1.1:11434",
        "http://10.0.0.1", "http://169.254.169.254", "https://localhost:11434",
        "http://localhost.example.test", "http://localhost.", "http://127.1",
        "http://2130706433", "http://0177.0.0.1", "http://[::ffff:127.0.0.1]",
        "http://[::1%25zone]", "http://localhost:", "http://localhost:0",
        "http://localhost:65536", "http://localhost:11434/api", "http://localhost//",
        "http://localhost?", "http://localhost#", "http://localhost?token=private",
        "http://user:private@localhost", "http://localhost\\@example.test",
        " http://localhost", "http://local\thost", "http://localhost\n",
    ],
)
def test_unsafe_origin_is_rejected_before_any_adapter_is_created(value: object) -> None:
    """Reject private/LAN/remote aliases, URL smuggling and unsafe origin syntax."""
    with pytest.raises(ValueError, match="Remote Ollama"):
        normalize_local_ollama_origin(value)
    with pytest.raises(DesktopSettingsValidationError, match="Remote Ollama"):
        validate_ollama_host(value)
    if isinstance(value, str):
        with pytest.raises(ValueError, match="Remote Ollama"):
            OllamaChatModel("test-model", value)
        with pytest.raises(ValueError, match="Remote Ollama"):
            LangChainOllamaChatModel("test-model", value)


@contextmanager
def _server(
    *, redirect: str | None = None,
    authorizations: list[str | None] | None = None,
) -> Iterator[tuple[str, list[str]]]:
    """Run an isolated loopback fixture and collect every accepted request."""
    paths: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        """Serve local Ollama-shaped data or a controlled redirect target."""

        def do_GET(self) -> None:
            """Record discovery and send one bounded test response."""
            self._respond()

        def do_POST(self) -> None:
            """Consume the bounded synthetic prompt before responding."""
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self._respond()

        def log_message(self, format: str, *args: object) -> None:
            """Suppress fixture access logging; assertions inspect captured paths."""

        def _respond(self) -> None:
            paths.append(self.path)
            if authorizations is not None:
                authorizations.append(self.headers.get("Authorization"))
            if redirect is not None:
                self.send_response(302)
                self.send_header("Location", redirect)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            value = (
                {"models": [{"name": "test-model"}]}
                if self.path == "/api/tags"
                else {
                    "model": "test-model", "done": True, "done_reason": "stop",
                    "message": {"role": "assistant", "content": "local reply"},
                }
            )
            body = (json.dumps(value) + "\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", paths
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_real_chat_and_discovery_ignore_ambient_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise real HTTP clients with proxy traps and explicit localhost selection."""
    with _server() as (proxy, proxy_paths), _server() as (origin, paths):
        for key in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.setenv(key, proxy)
        for key in ("NO_PROXY", "no_proxy"):
            monkeypatch.setenv(key, "")
        localhost = origin.replace("127.0.0.1", "localhost")
        with open_local_ollama_tags(localhost) as response:
            assert json.load(response)["models"] == [{"name": "test-model"}]
        for adapter in (OllamaChatModel, LangChainOllamaChatModel):
            model = adapter("test-model", localhost, timeout_seconds=2)
            model.ensure_model_available()
            assert model.generate_reply([{"role": "user", "content": "synthetic test"}]) == "local reply"
        assert paths.count("/api/tags") == 3
        assert paths.count("/api/chat") == 2
        assert proxy_paths == []


def test_real_chat_and_discovery_never_follow_redirects() -> None:
    """A loopback redirect must not leak even a synthetic prompt to another endpoint."""
    with _server() as (target, target_paths), _server(redirect=f"{target}/stolen") as (origin, _):
        with pytest.raises(HTTPError):
            with open_local_ollama_tags(origin) as response:
                response.read()
        for adapter in (OllamaChatModel, LangChainOllamaChatModel):
            model = adapter("test-model", origin, timeout_seconds=2)
            with pytest.raises(ChatModelResponseError):
                model.generate_reply([{"role": "user", "content": "synthetic test"}])
        assert target_paths == []


def test_sdk_environment_key_never_reaches_sync_or_async_local_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise both real SDK clients after its environment-secret injection."""
    monkeypatch.setenv("OLLAMA_API_KEY", "synthetic-secret-must-not-be-sent")
    authorizations: list[str | None] = []
    with _server(authorizations=authorizations) as (origin, _):
        model = LangChainOllamaChatModel("test-model", origin, timeout_seconds=2)
        assert model.generate_reply([{"role": "user", "content": "synthetic test"}]) == "local reply"

        async def _invoke_async_client() -> None:
            """Use the actual async SDK constructed by the adapter's shared policy."""
            response = await model._langchain_model.ainvoke("synthetic async test")
            assert response.content == "local reply"
            await model._langchain_model._async_client._client.aclose()

        asyncio.run(_invoke_async_client())
        assert authorizations == [None, None]


@pytest.mark.parametrize("name", ["example:cloud", "example:120b-cloud", "example-CLOUD"])
def test_explicit_cloud_models_are_rejected(name: str) -> None:
    """Known cloud suffixes must not reach an otherwise local Ollama daemon."""
    assert is_cloud_ollama_model_name(name)
    with pytest.raises(DesktopSettingsValidationError, match="installed local"):
        validate_model_name(name)
    for adapter in (OllamaChatModel, LangChainOllamaChatModel):
        with pytest.raises(ValueError, match="installed local"):
            adapter(name, "http://localhost:11434")


def test_model_names_containing_cloud_are_not_guessed_to_be_remote() -> None:
    """Keep arbitrary ordinary model names valid unless a recognized suffix matches."""
    name = "cloud-study:local"
    assert not is_cloud_ollama_model_name(name)
    assert validate_model_name(name) == name
