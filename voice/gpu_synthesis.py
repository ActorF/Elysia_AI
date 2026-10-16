"""Run managed desktop speech as one physically drained GPU lease per sentence.

The private wrapper postpones guarded GPT-SoVITS startup until Main grants GPU
admission. No model process remains resident between sentences. Cancellation
uses exact queue tokens, including tombstones for aborts arriving before the
queue worker enters this wrapper; failed native cleanup poisons global reuse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from threading import Event, Lock, Thread, get_ident
from typing import Protocol

from gpu_admission import GpuAdmissionCancelledError, GpuLease, gpu_lease
from voice.managed_gpt_sovits import (
    MANAGED_GPT_SOVITS_MAX_CANCEL_TOMBSTONES,
    MANAGED_GPT_SOVITS_MAX_REQUESTS_PER_LEASE,
    ManagedGptSovitsCancelledError,
    ManagedGptSovitsConfig,
    ManagedGptSovitsRuntime,
    ManagedGptSovitsUnavailableError,
    ManagedGptSovitsValidationError,
    wait_for_managed_cleanup,
)
from voice.synthesis import SynthesisLanguage, SynthesisResult


_TOKEN = re.compile(r"^[0-9a-f]{64}$")
_UNAVAILABLE = "Managed desktop speech is unavailable."
_CANCELLED = "Managed desktop speech was cancelled."


class _RuntimeLease(Protocol):
    """Expose only attested managed synthesis and exact-token abort."""

    def synthesize(self, text: str, language: str, operation_token: str) -> SynthesisResult:
        """Return authenticated canonical audio for the exact active token."""
        ...

    def abort(self, operation_token: str) -> bool:
        """Cancel or tombstone the supplied token without affecting another operation."""
        ...


class _Runtime(Protocol):
    """Retain a trusted supervisor and its authoritative native shutdown."""

    def acquire_lease(self, selection: object) -> _RuntimeLease:
        """Guard the catalog selection and attest one freshly owned worker."""
        ...

    def shutdown(self) -> None:
        """Stop admission and initiate or join bounded native cleanup."""
        ...


@dataclass(slots=True)
class _SentenceOperation:
    """Keep cancellation and ownership attached to one immutable queue token."""

    token: str
    thread_id: int
    cancelled: Event = field(default_factory=Event)
    finished: Event = field(default_factory=Event)
    runtime: _Runtime | None = None
    lease: _RuntimeLease | None = None
    gpu: GpuLease | None = None
    abort_thread: Thread | None = None
    cleanup_failed: bool = False


class _GpuSentenceSynthesisLease:
    """Implement the queue's managed contract without retaining an idle model.

    Only desktop composition constructs this private wrapper. It cannot claim
    cache eligibility, and every actual audio result still passes through the
    supervisor's guarded catalog verification and READY challenge. Wrapper
    locks protect state only; no broker wait, native call, or thread join runs
    while that lock is held.
    """

    def __init__(self, config: ManagedGptSovitsConfig, selection: object) -> None:
        """Freeze the trusted supervisor configuration and resolved voice selection."""

        self._config = config
        self._selection = selection
        self._lock = Lock()
        self._active: _SentenceOperation | None = None
        self._used: set[str] = set()
        self._tombstones: set[str] = set()
        self._closed = False
        self._poisoned = False

    @property
    def cache_eligible(self) -> bool:
        """Keep lazy per-sentence supervisor bindings permanently non-cacheable."""

        return False

    @property
    def poisoned(self) -> bool:
        """Report permanent native cleanup uncertainty or bounded-ledger exhaustion."""

        with self._lock:
            return self._poisoned

    def synthesize(self, text: str, language: SynthesisLanguage, operation_token: str) -> SynthesisResult:
        """Hold global GPU ownership from before launch through proven cleanup."""

        token = _token(operation_token)
        with self._lock:
            if self._closed or self._poisoned or self._active is not None:
                raise ManagedGptSovitsUnavailableError(_UNAVAILABLE)
            if token in self._used:
                raise ManagedGptSovitsValidationError(_UNAVAILABLE)
            if len(self._used) >= MANAGED_GPT_SOVITS_MAX_REQUESTS_PER_LEASE:
                self._poisoned = True
                raise ManagedGptSovitsUnavailableError(_UNAVAILABLE)
            self._used.add(token)
            if token in self._tombstones:
                self._tombstones.remove(token)
                raise ManagedGptSovitsCancelledError(_CANCELLED)
            operation = _SentenceOperation(token, get_ident())
            self._active = operation
        try:
            try:
                with gpu_lease("gpt-sovits", cancel_requested=operation.cancelled.is_set) as gpu:
                    with self._lock:
                        operation.gpu = gpu
                    try:
                        self._check_cancelled(operation)
                        runtime = ManagedGptSovitsRuntime(self._config)
                        with self._lock:
                            operation.runtime = runtime
                        self._check_cancelled(operation)
                        lease = runtime.acquire_lease(self._selection)
                        with self._lock:
                            operation.lease = lease
                        self._check_cancelled(operation)
                        result = lease.synthesize(text, language, token)
                        self._check_cancelled(operation)
                    finally:
                        self._finish_native(operation)
                    self._check_cancelled(operation)
                    return result
            except GpuAdmissionCancelledError:
                raise ManagedGptSovitsCancelledError(_CANCELLED) from None
        finally:
            with self._lock:
                if self._active is operation:
                    self._active = None
                operation.finished.set()

    def abort(self, operation_token: str) -> bool:
        """Cancel only the exact live token or retain a bounded pre-start tombstone.

        Startup can be blocked in READY before an attested lease exists. In
        that phase a retained non-daemon cleanup thread closes the supervisor
        and wakes startup; the synthesis owner still proves cleanup before
        releasing its global GPU grant.
        """

        token = _token(operation_token)
        runtime: _Runtime | None = None
        lease: _RuntimeLease | None = None
        with self._lock:
            operation = self._active
            if operation is None or operation.token != token:
                if self._closed or token in self._used or token in self._tombstones:
                    return False
                if len(self._tombstones) >= MANAGED_GPT_SOVITS_MAX_CANCEL_TOMBSTONES:
                    self._poisoned = True
                    raise ManagedGptSovitsUnavailableError(_UNAVAILABLE)
                self._tombstones.add(token)
                return True
            if operation.cancelled.is_set():
                return False
            operation.cancelled.set()
            lease = operation.lease
            runtime = operation.runtime
        if lease is not None:
            try:
                lease.abort(token)
            except BaseException:
                with self._lock:
                    operation.cleanup_failed = True
        elif runtime is not None:
            self._abort_starting_runtime(operation, runtime)
        return True

    def shutdown(self) -> None:
        """Stop future sentences and bound the drain of the current GPU owner."""

        with self._lock:
            self._closed = True
            operation = self._active
        if operation is None:
            return
        self.abort(operation.token)
        if operation.thread_id == get_ident():
            return
        if not operation.finished.wait(self._config.stop_timeout_seconds):
            with self._lock:
                self._poisoned = True
                gpu = operation.gpu
            if gpu is not None:
                gpu.poison()

    def _check_cancelled(self, operation: _SentenceOperation) -> None:
        if operation.cancelled.is_set() or (operation.gpu is not None and operation.gpu.cancelled):
            raise ManagedGptSovitsCancelledError(_CANCELLED)

    def _abort_starting_runtime(self, operation: _SentenceOperation, runtime: _Runtime) -> None:
        def _stop() -> None:
            try:
                runtime.shutdown()
            except BaseException:
                with self._lock:
                    operation.cleanup_failed = True

        try:
            cleanup = Thread(target=_stop, name="elysia-gpu-speech-startup-abort", daemon=False)
            cleanup.start()
        except BaseException:
            # Falling back synchronously preserves the native owner if thread
            # allocation/start fails; no wrapper lock spans this OS operation.
            _stop()
        else:
            # Publish only a started thread. Synthesis may enter final cleanup
            # concurrently, and joining a thread before start would raise and
            # could otherwise escape the authoritative GPU release boundary.
            with self._lock:
                operation.abort_thread = cleanup

    def _finish_native(self, operation: _SentenceOperation) -> None:
        cleanup_ok = True
        runtime = operation.runtime
        if runtime is not None:
            try:
                runtime.shutdown()
            except BaseException:
                cleanup_ok = False
        abort_thread = operation.abort_thread
        if abort_thread is not None:
            try:
                abort_thread.join(self._config.stop_timeout_seconds)
                cleanup_ok = cleanup_ok and not abort_thread.is_alive()
            except BaseException:
                cleanup_ok = False
        try:
            cleanup_ok = wait_for_managed_cleanup(self._config.stop_timeout_seconds) and cleanup_ok
        except BaseException:
            cleanup_ok = False
        with self._lock:
            cleanup_ok = cleanup_ok and not operation.cleanup_failed
            if not cleanup_ok:
                self._poisoned = True
        if not cleanup_ok:
            if operation.gpu is not None:
                operation.gpu.poison()
            raise ManagedGptSovitsUnavailableError(_UNAVAILABLE)


def _token(value: object) -> str:
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        raise ManagedGptSovitsValidationError(_UNAVAILABLE)
    return value
