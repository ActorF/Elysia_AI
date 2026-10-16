"""Verify exclusive fd4 ownership without closing the test runner's descriptors."""

from io import BytesIO
from typing import Any

import pytest

import desktop_protocol.image_channel as channel


def test_inherited_image_pipe_is_fixed_unbuffered_and_not_inheritable(monkeypatch: Any) -> None:
    """Transfer only fd4 after disabling inheritance, independently of audio fd3."""

    stream = BytesIO()
    calls: list[object] = []
    monkeypatch.setattr(channel.os, "set_inheritable", lambda fd, value: calls.append((fd, value)))
    monkeypatch.setattr(channel, "_is_image_pipe_descriptor", lambda: True)

    def _open(fd: int, mode: str, **options: object) -> BytesIO:
        calls.append((fd, mode, options))
        return stream

    monkeypatch.setattr(channel.os, "fdopen", _open)
    writer = channel.ImageChannelWriter.from_inherited_fd4()
    assert calls == [(4, False), (4, "wb", {"buffering": 0, "closefd": True})]
    writer.close()
    assert stream.closed


@pytest.mark.parametrize("stage", ["inheritance", "descriptor", "fdopen", "constructor"])
def test_bootstrap_failure_releases_only_image_owner_and_detaches_errors(monkeypatch: Any, stage: str) -> None:
    """Reject file/console handles and close provisional owners without diagnostics."""

    stream = BytesIO()
    closed: list[int] = []

    def _fail(*_args: object, **_options: object) -> Any:
        raise OSError("private path and native details")

    monkeypatch.setattr(channel.os, "close", closed.append)
    monkeypatch.setattr(channel.os, "set_inheritable", _fail if stage == "inheritance" else lambda *_: None)
    monkeypatch.setattr(channel, "_is_image_pipe_descriptor", lambda: stage != "descriptor")
    monkeypatch.setattr(channel.os, "fdopen", _fail if stage == "fdopen" else lambda *_a, **_k: stream)
    if stage == "constructor":
        monkeypatch.setattr(channel.secrets, "token_bytes", _fail)
    with pytest.raises(channel.ImageChannelIOError) as caught:
        channel.ImageChannelWriter.from_inherited_fd4()
    assert "private" not in str(caught.value)
    assert caught.value.__context__ is None
    assert closed == ([] if stage == "constructor" else [4])
    assert stream.closed is (stage == "constructor")
