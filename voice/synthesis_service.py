"""Compose optional local speech synthesis without affecting text Chat startup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from .gpt_sovits import (
    GptSovitsConfig,
    GptSovitsStatus,
    GptSovitsSynthesizer,
)
from .profiles import (
    JsonVoiceProfileCatalog,
    LocalVoiceProfileSummary,
    VoiceProfileCatalogUnavailableError,
    VoiceProfileCatalogValidationError,
)
from .synthesis import (
    SynthesisRequest,
    SynthesisResult,
    SynthesisUnavailableError,
)


_CATALOG_MISSING_MESSAGE = "Local speech synthesis is not configured."
_CATALOG_INVALID_MESSAGE = "Local speech synthesis configuration is invalid."


@dataclass(frozen=True, slots=True)
class LocalSpeechSynthesisStatus:
    """Combine sanitized adapter readiness with safe profile summaries."""

    adapter: GptSovitsStatus
    profiles: tuple[LocalVoiceProfileSummary, ...]

    def __post_init__(self) -> None:
        """Reject malformed status snapshots before later protocol exposure."""

        if not isinstance(self.adapter, GptSovitsStatus):
            raise TypeError("adapter must be a GptSovitsStatus.")
        if not isinstance(self.profiles, tuple) or not all(
            isinstance(profile, LocalVoiceProfileSummary)
            for profile in self.profiles
        ):
            raise TypeError("profiles must contain LocalVoiceProfileSummary values.")


class LocalSpeechSynthesisService:
    """Load local profiles lazily and serialize optional TTS operations.

    Construction performs no filesystem or network access. Text-only startup
    can therefore create or import this service even when GPT-SoVITS, its
    catalog, and all voice assets are absent. Calls reload the local catalog so
    a user can repair setup without restarting the text Chat process.
    """

    def __init__(
        self,
        base_dir: Path,
        *,
        allow_local_evaluation: bool = False,
        request_timeout_seconds: float = 120.0,
        probe_timeout_seconds: float = 1.0,
        deterministic_seed: int = 42,
    ) -> None:
        """Derive fixed local paths and validate bounded runtime configuration."""

        if not isinstance(base_dir, Path) or not base_dir.is_absolute():
            raise ValueError("base_dir must be an absolute Path.")
        if not isinstance(allow_local_evaluation, bool):
            raise TypeError("allow_local_evaluation must be a Boolean.")
        self._asset_root = base_dir / "models" / "weights" / "gpt-sovits"
        self._catalog_path = (
            base_dir / "workspace" / "settings" / "voice-profiles.json"
        )
        self._config = GptSovitsConfig(
            asset_root=self._asset_root,
            request_timeout_seconds=request_timeout_seconds,
            probe_timeout_seconds=probe_timeout_seconds,
            deterministic_seed=deterministic_seed,
        )
        self._allow_local_evaluation = allow_local_evaluation
        # This independent loopback/smoke path does not use the managed Desktop
        # sentence queue. A service-level lock also covers catalog reload plus
        # adapter construction, whose per-instance lock would not serialize calls.
        self._lock = RLock()

    def get_status(
        self,
        *,
        profile_id: str = "default",
        emotion: str = "neutral",
    ) -> LocalSpeechSynthesisStatus:
        """Return fresh sanitized catalog, asset, and API readiness."""

        with self._lock:
            if not self._catalog_path.is_file():
                return LocalSpeechSynthesisStatus(
                    GptSovitsStatus("unavailable", "catalog_missing"),
                    (),
                )
            if not self._asset_root.is_dir():
                return LocalSpeechSynthesisStatus(
                    GptSovitsStatus("unavailable", "assets_unavailable"),
                    (),
                )
            try:
                catalog = self._load_catalog()
            except VoiceProfileCatalogUnavailableError:
                return LocalSpeechSynthesisStatus(
                    GptSovitsStatus("unavailable", "catalog_missing"),
                    (),
                )
            except VoiceProfileCatalogValidationError:
                return LocalSpeechSynthesisStatus(
                    GptSovitsStatus("unavailable", "catalog_invalid"),
                    (),
                )
            adapter = GptSovitsSynthesizer(self._config, catalog)
            return LocalSpeechSynthesisStatus(
                adapter.get_status(
                    profile_id=profile_id,
                    emotion=emotion,
                ),
                catalog.summaries(),
            )

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        """Synthesize through a fresh strict catalog or raise a stable error."""

        if not isinstance(request, SynthesisRequest):
            raise SynthesisUnavailableError(_CATALOG_INVALID_MESSAGE)
        with self._lock:
            try:
                catalog = self._load_catalog()
            except VoiceProfileCatalogUnavailableError as error:
                raise SynthesisUnavailableError(
                    _CATALOG_MISSING_MESSAGE
                ) from error
            except VoiceProfileCatalogValidationError as error:
                raise SynthesisUnavailableError(
                    _CATALOG_INVALID_MESSAGE
                ) from error
            return GptSovitsSynthesizer(self._config, catalog).synthesize(request)

    def _load_catalog(self) -> JsonVoiceProfileCatalog:
        """Load the one fixed device-local catalog and model asset root."""

        return JsonVoiceProfileCatalog.load(
            self._catalog_path,
            self._asset_root,
            allow_local_evaluation=self._allow_local_evaluation,
        )


def create_local_speech_synthesis_service(
    base_dir: Path,
    *,
    allow_local_evaluation: bool = False,
    request_timeout_seconds: float = 120.0,
    probe_timeout_seconds: float = 1.0,
    deterministic_seed: int = 42,
) -> LocalSpeechSynthesisService:
    """Create the lazy production service from immutable application settings."""

    return LocalSpeechSynthesisService(
        base_dir,
        allow_local_evaluation=allow_local_evaluation,
        request_timeout_seconds=request_timeout_seconds,
        probe_timeout_seconds=probe_timeout_seconds,
        deterministic_seed=deterministic_seed,
    )
