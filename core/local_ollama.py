"""Keep Chat and model discovery on explicit, proxy-free loopback HTTP origins.

Local model traffic stays independent of the optional Internet permission.
Canonical IP literals prevent DNS or hosts-file changes from moving a request
off the machine; redirects and ambient proxies must not undo that boundary.
"""

from __future__ import annotations

from http.client import HTTPResponse
from ipaddress import ip_address
import math
from typing import Any, cast
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


LOCAL_OLLAMA_ORIGIN_ERROR = (
    "Ollama must use local HTTP: choose localhost, 127.0.0.1, or [::1] "
    "with the local service port. Remote Ollama, credentials, and URL paths "
    "are not supported."
)
LOCAL_OLLAMA_MODEL_ERROR = (
    "Choose an installed local Ollama model; cloud model names are not supported."
)


def is_cloud_ollama_model_name(value: str) -> bool:
    """Recognize explicit cloud suffixes without guessing arbitrary model aliases.

    This rejects Ollama's obvious cloud selections, but cannot police a trusted
    local daemon's configuration or prove the behavior behind an arbitrary tag.
    """

    return value.strip().lower().endswith((":cloud", "-cloud"))


def model_name_with_default_tag(name: str) -> str:
    """Make only Ollama's omitted latest tag explicit for identity comparison.

    A registry port belongs to a preceding slash-delimited component. Explicit
    tags remain exact, so another quantization cannot satisfy the request.
    Callers must validate names separately before treating them as identities.
    """

    return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"


def normalize_local_ollama_origin(value: object) -> str:
    """Return a canonical loopback HTTP origin or raise a value-free error.

    A single trailing slash is accepted for compatibility with existing
    settings. No DNS lookup, credentials, query, fragment, or scoped IPv6
    address can participate in resolving the destination.
    """

    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2_048
        or any(character.isspace() or ord(character) < 32 for character in value)
        or any(character in value for character in "\\?#%")
    ):
        raise ValueError(LOCAL_OLLAMA_ORIGIN_ERROR)
    try:
        parsed = urlsplit(value)
        port = parsed.port
        hostname = parsed.hostname
        if (
            parsed.scheme != "http"
            or hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.netloc.endswith(":")
            or port is not None and not 1 <= port <= 65_535
        ):
            raise ValueError(LOCAL_OLLAMA_ORIGIN_ERROR)
        address = ip_address("127.0.0.1" if hostname == "localhost" else hostname)
        if not address.is_loopback or (address.version == 6 and str(address) != "::1"):
            raise ValueError(LOCAL_OLLAMA_ORIGIN_ERROR)
    except ValueError:
        raise ValueError(LOCAL_OLLAMA_ORIGIN_ERROR) from None
    authority = f"[{address}]" if address.version == 6 else str(address)
    if port is not None:
        authority = f"{authority}:{port}"
    return urlunsplit(("http", authority, "", "", ""))


class _RejectRedirects(HTTPRedirectHandler):
    """Refuse all redirect targets before urllib opens another connection."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        """Reject a redirect without disclosing the untrusted target URL."""
        raise HTTPError(req.full_url, code, "Local Ollama redirects are disabled.", headers, fp)


def open_local_ollama_tags(
    origin: object,
    *,
    timeout_seconds: float = 3.0,
) -> HTTPResponse:
    """Open only the local model catalog; callers own response closing/decoding.

    This private opener ignores machine proxy configuration and refuses
    redirects rather than modifying urllib's process-global opener.
    """

    normalized = normalize_local_ollama_origin(origin)
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Ollama discovery timeout must be finite and positive.")
    request = Request(f"{normalized}/api/tags", headers={"Accept": "application/json"})
    opener = build_opener(ProxyHandler({}), _RejectRedirects())
    return cast(HTTPResponse, opener.open(request, timeout=timeout_seconds))
