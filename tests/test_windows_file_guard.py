"""Test the Windows immutable-file handle and verification boundary."""

from __future__ import annotations

import _thread
import ctypes
import hashlib
import os
from pathlib import Path, WindowsPath
import subprocess
from threading import Event, Lock, Thread, current_thread

import pytest

import voice._windows_file_guard as file_guard_module
from voice._windows_file_guard import (
    GuardedFileDeclaration,
    WindowsReadOnlyFileGuardAcquireError,
    WindowsReadOnlyFileGuardError,
    WindowsReadOnlyFileGuardSet,
    WindowsReadOnlyFileGuardUnavailableError,
    WindowsReadOnlyFileGuardValidationError,
)


_WINDOWS_ONLY = pytest.mark.skipif(
    os.name != "nt", reason="Win32 sharing and reparse behavior requires Windows."
)
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_DDD_RAW_TARGET_PATH = 0x00000001
_DDD_REMOVE_DEFINITION = 0x00000002
_DDD_EXACT_MATCH_ON_REMOVE = 0x00000004
_DDD_NO_BROADCAST_SYSTEM = 0x00000008


def _declaration(path: Path) -> GuardedFileDeclaration:
    """Describe the current bytes of one test-owned regular file."""

    content = path.read_bytes()
    return GuardedFileDeclaration(
        path=path,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _open_existing_writer(path: Path) -> int:
    """Open a writer that shares broadly enough to isolate reciprocal sharing."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    handle = kernel32.CreateFileW(
        str(path),
        _GENERIC_WRITE,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        0,
        None,
    )
    value = 0 if handle is None else int(handle)
    assert value not in {0, _INVALID_HANDLE_VALUE}
    return value


def _close_native_handle(handle: int) -> None:
    """Release a test-owned Win32 handle and assert the close succeeded."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    assert kernel32.CloseHandle(ctypes.c_void_p(handle))


def _process_handle_count() -> int:
    """Return this pytest process's native handle count for rollback checks."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.GetProcessHandleCount.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    kernel32.GetProcessHandleCount.restype = ctypes.c_int
    count = ctypes.c_uint32()
    assert kernel32.GetProcessHandleCount(
        kernel32.GetCurrentProcess(), ctypes.byref(count)
    )
    return int(count.value)


def _define_raw_dos_device(
    drive: str,
    target: str,
    *,
    remove: bool = False,
) -> bool:
    """Create or remove one test-owned raw DOS-device mapping without broadcast."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.DefineDosDeviceW.argtypes = [
        ctypes.c_uint32,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
    ]
    kernel32.DefineDosDeviceW.restype = ctypes.c_int
    flags = _DDD_RAW_TARGET_PATH | _DDD_NO_BROADCAST_SYSTEM
    if remove:
        flags |= _DDD_REMOVE_DEFINITION | _DDD_EXACT_MATCH_ON_REMOVE
    return bool(kernel32.DefineDosDeviceW(flags, drive, target))


@_WINDOWS_ONLY
def test_acquire_verifies_hash_and_context_manager_closes(tmp_path: Path) -> None:
    """Retain a correctly declared file until deterministic context exit."""

    model = tmp_path / "model.bin"
    model.write_bytes(b"trusted model bytes")
    declaration = _declaration(model)

    with WindowsReadOnlyFileGuardSet.acquire((declaration,)) as guard:
        assert guard.closed is False
        assert guard.__enter__() is guard
        assert model.read_bytes() == b"trusted model bytes"

    assert guard.closed is True
    guard.close()


@_WINDOWS_ONLY
def test_private_accessor_returns_ordered_readable_volume_guid_paths(
    tmp_path: Path,
) -> None:
    """Expose stable leaf spellings only through the sealed private boundary."""

    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"first stable volume path")
    second.write_bytes(b"second stable volume path")
    guard = WindowsReadOnlyFileGuardSet.acquire(
        (_declaration(first), _declaration(second))
    )
    try:
        paths = file_guard_module._get_guarded_volume_paths(
            guard,
            access_seal=file_guard_module._GUARDED_VOLUME_PATH_ACCESS_SEAL,
        )
        assert len(paths) == 2
        assert paths[0].casefold().startswith("\\\\?\\volume{")
        assert paths[1].casefold().startswith("\\\\?\\volume{")
        assert Path(paths[0]).read_bytes() == b"first stable volume path"
        assert Path(paths[1]).read_bytes() == b"second stable volume path"
        assert not hasattr(guard, "volume_paths")
    finally:
        guard.close()


@_WINDOWS_ONLY
def test_private_volume_path_accessor_rejects_wrong_calls_and_closed_guard(
    tmp_path: Path,
) -> None:
    """Require exact guard provenance, private seal, and active handle state."""

    secret = "DO-NOT-DISCLOSE-VOLUME-PATH"
    model = tmp_path / secret / "model.bin"
    model.parent.mkdir()
    model.write_bytes(b"private path")
    guard = WindowsReadOnlyFileGuardSet.acquire((_declaration(model),))
    stable_path = file_guard_module._get_guarded_volume_paths(
        guard,
        access_seal=file_guard_module._GUARDED_VOLUME_PATH_ACCESS_SEAL,
    )[0]
    failures: list[WindowsReadOnlyFileGuardError] = []
    try:
        with pytest.raises(WindowsReadOnlyFileGuardError) as wrong_seal:
            file_guard_module._get_guarded_volume_paths(
                guard, access_seal=object()
            )
        failures.append(wrong_seal.value)
        with pytest.raises(WindowsReadOnlyFileGuardError) as wrong_guard:
            file_guard_module._get_guarded_volume_paths(
                object(),
                access_seal=file_guard_module._GUARDED_VOLUME_PATH_ACCESS_SEAL,
            )
        failures.append(wrong_guard.value)
    finally:
        guard.close()

    with pytest.raises(WindowsReadOnlyFileGuardError) as closed_guard:
        file_guard_module._get_guarded_volume_paths(
            guard,
            access_seal=file_guard_module._GUARDED_VOLUME_PATH_ACCESS_SEAL,
        )
    failures.append(closed_guard.value)
    for failure in failures:
        rendered = f"{failure!s} {failure!r}"
        assert secret not in rendered
        assert stable_path not in rendered
    assert secret not in repr(guard)
    assert stable_path not in repr(guard)


@_WINDOWS_ONLY
def test_private_accessor_rejects_partially_closed_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never publish stable paths after even one protective handle is released."""

    model = tmp_path / "model.bin"
    model.write_bytes(b"partial close")
    guard = WindowsReadOnlyFileGuardSet.acquire((_declaration(model),))
    original_close = file_guard_module._close_handle
    failed_once = False

    def fail_one_handle(handle: int) -> bool:
        """Retain exactly one handle while allowing the remaining locks to close."""

        nonlocal failed_once
        if not failed_once:
            failed_once = True
            return False
        return original_close(handle)

    monkeypatch.setattr(file_guard_module, "_close_handle", fail_one_handle)
    with pytest.raises(WindowsReadOnlyFileGuardError):
        guard.close()
    assert "close-failed" in repr(guard)
    with pytest.raises(WindowsReadOnlyFileGuardValidationError):
        file_guard_module._get_guarded_volume_paths(
            guard,
            access_seal=file_guard_module._GUARDED_VOLUME_PATH_ACCESS_SEAL,
        )

    guard.close()
    assert guard.closed is True


@_WINDOWS_ONLY
def test_non_guid_and_duplicate_stable_paths_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject malformed or repeated stable identities before guard publication."""

    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    declarations = (_declaration(first), _declaration(second))
    original_normalized = file_guard_module._normalized_path_from_handle

    def non_guid_path(handle: int, volume_name: int) -> str:
        """Return a DOS spelling when the guard requests a GUID namespace."""

        if volume_name == file_guard_module._VOLUME_NAME_GUID:
            return r"\\?\D:\not-a-volume-guid.bin"
        return original_normalized(handle, volume_name)

    with monkeypatch.context() as scoped_patch:
        scoped_patch.setattr(
            file_guard_module, "_normalized_path_from_handle", non_guid_path
        )
        with pytest.raises(WindowsReadOnlyFileGuardValidationError):
            WindowsReadOnlyFileGuardSet.acquire((declarations[0],))

    duplicate = r"\\?\Volume{11111111-2222-3333-4444-555555555555}\same.bin"

    def duplicate_guid_path(handle: int, volume_name: int) -> str:
        """Return one valid GUID spelling for two distinct leaf handles."""

        if volume_name == file_guard_module._VOLUME_NAME_GUID:
            return duplicate
        return original_normalized(handle, volume_name)

    with monkeypatch.context() as scoped_patch:
        scoped_patch.setattr(
            file_guard_module, "_normalized_path_from_handle", duplicate_guid_path
        )
        with pytest.raises(WindowsReadOnlyFileGuardValidationError):
            WindowsReadOnlyFileGuardSet.acquire(declarations)

    first.write_bytes(b"both failures rolled back")


@_WINDOWS_ONLY
def test_verification_never_reopens_the_leaf_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hash through the locked native handle even if path reads are unavailable."""

    model = tmp_path / "model.bin"
    model.write_bytes(b"same native handle")
    declaration = _declaration(model)

    def reject_path_read(_path: Path) -> bytes:
        """Fail if verification regresses to a second path-based open."""

        raise AssertionError("verification reopened the leaf path")

    monkeypatch.setattr(Path, "read_bytes", reject_path_read)
    WindowsReadOnlyFileGuardSet.acquire((declaration,)).close()


@_WINDOWS_ONLY
@pytest.mark.parametrize("mismatch", ["size", "hash"])
def test_acquire_rejects_size_and_hash_mismatches(
    tmp_path: Path,
    mismatch: str,
) -> None:
    """Reject catalog bytes that do not exactly describe the opened handle."""

    model = tmp_path / "model.bin"
    model.write_bytes(b"expected")
    valid = _declaration(model)
    declaration = GuardedFileDeclaration(
        path=model,
        size_bytes=valid.size_bytes + (1 if mismatch == "size" else 0),
        sha256=("0" * 64 if mismatch == "hash" else valid.sha256),
    )

    with pytest.raises(WindowsReadOnlyFileGuardValidationError):
        WindowsReadOnlyFileGuardSet.acquire((declaration,))


@_WINDOWS_ONLY
def test_acquire_rejects_leaf_and_parent_reparse_points(tmp_path: Path) -> None:
    """Never follow a file symlink or a reparse directory during path traversal."""

    target_file = tmp_path / "target.bin"
    target_file.write_bytes(b"target")
    file_link = tmp_path / "file-link.bin"
    target_directory = tmp_path / "target-directory"
    target_directory.mkdir()
    nested_file = target_directory / "nested.bin"
    nested_file.write_bytes(b"nested")
    directory_junction = tmp_path / "directory-junction"
    junction_result = subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            "mklink",
            "/J",
            str(directory_junction),
            str(target_directory),
        ],
        check=False,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if junction_result.returncode != 0:
        pytest.skip("This Windows filesystem cannot create a test junction.")

    try:
        with pytest.raises(WindowsReadOnlyFileGuardError):
            WindowsReadOnlyFileGuardSet.acquire(
                (
                    GuardedFileDeclaration(
                        directory_junction / nested_file.name,
                        nested_file.stat().st_size,
                        hashlib.sha256(nested_file.read_bytes()).hexdigest(),
                    ),
                )
            )

        try:
            file_link.symlink_to(target_file)
        except OSError:
            # A directory junction still exercises OPEN_REPARSE_POINT on hosts
            # where file-symlink creation requires an unavailable privilege.
            return
        with pytest.raises(WindowsReadOnlyFileGuardError):
            WindowsReadOnlyFileGuardSet.acquire(
                (
                    GuardedFileDeclaration(
                        file_link,
                        target_file.stat().st_size,
                        hashlib.sha256(target_file.read_bytes()).hexdigest(),
                    ),
                )
            )
    finally:
        if directory_junction.exists():
            directory_junction.rmdir()


@_WINDOWS_ONLY
def test_existing_writer_prevents_guard_acquisition(tmp_path: Path) -> None:
    """Use reciprocal Windows sharing checks to reject an already-open writer."""

    model = tmp_path / "model.bin"
    model.write_bytes(b"stable")
    declaration = _declaration(model)
    writer = _open_existing_writer(model)
    try:
        with pytest.raises(WindowsReadOnlyFileGuardError):
            WindowsReadOnlyFileGuardSet.acquire((declaration,))
    finally:
        _close_native_handle(writer)

    WindowsReadOnlyFileGuardSet.acquire((declaration,)).close()


@_WINDOWS_ONLY
def test_guard_blocks_write_delete_and_rename_but_allows_read(
    tmp_path: Path,
) -> None:
    """Hold both leaf and ancestor names immutable while allowing other readers."""

    parent = tmp_path / "assets"
    parent.mkdir()
    model = parent / "model.bin"
    model.write_bytes(b"locked")
    renamed_model = parent / "renamed.bin"
    renamed_parent = tmp_path / "renamed-assets"
    guard = WindowsReadOnlyFileGuardSet.acquire((_declaration(model),))
    try:
        assert model.read_bytes() == b"locked"
        with pytest.raises(OSError):
            model.write_bytes(b"changed")
        with pytest.raises(OSError):
            model.unlink()
        with pytest.raises(OSError):
            model.rename(renamed_model)
        with pytest.raises(OSError):
            parent.rename(renamed_parent)
    finally:
        guard.close()

    model.write_bytes(b"changed")
    model.rename(renamed_model)
    parent.rename(renamed_parent)
    assert (renamed_parent / renamed_model.name).read_bytes() == b"changed"


@_WINDOWS_ONLY
def test_hard_link_writer_is_blocked_and_duplicate_alias_is_rejected(
    tmp_path: Path,
) -> None:
    """Apply sharing and identity checks to another name for the same file object."""

    model = tmp_path / "model.bin"
    alias = tmp_path / "alias.bin"
    model.write_bytes(b"one identity")
    try:
        os.link(model, alias)
    except OSError as error:
        pytest.skip(f"This filesystem cannot create a test hard link: {error.winerror}")

    model_declaration = _declaration(model)
    alias_declaration = _declaration(alias)
    guard = WindowsReadOnlyFileGuardSet.acquire((model_declaration,))
    try:
        assert alias.read_bytes() == b"one identity"
        with pytest.raises(OSError):
            alias.write_bytes(b"changed")
    finally:
        guard.close()

    with pytest.raises(WindowsReadOnlyFileGuardValidationError):
        WindowsReadOnlyFileGuardSet.acquire(
            (model_declaration, alias_declaration)
        )


@_WINDOWS_ONLY
def test_case_alias_and_ambiguous_declarations_are_rejected(tmp_path: Path) -> None:
    """Reject duplicate case aliases and non-canonical digest/size fields."""

    model = tmp_path / "MixedCase.bin"
    model.write_bytes(b"case-insensitive")
    valid = _declaration(model)
    case_alias = GuardedFileDeclaration(
        Path(str(model).upper()), valid.size_bytes, valid.sha256
    )

    with pytest.raises(WindowsReadOnlyFileGuardValidationError):
        WindowsReadOnlyFileGuardSet.acquire((valid, case_alias))
    with pytest.raises(WindowsReadOnlyFileGuardValidationError):
        WindowsReadOnlyFileGuardSet.acquire(
            (GuardedFileDeclaration(model, True, valid.sha256),)
        )
    with pytest.raises(WindowsReadOnlyFileGuardValidationError):
        WindowsReadOnlyFileGuardSet.acquire(
            (GuardedFileDeclaration(model, valid.size_bytes, valid.sha256.upper()),)
        )


@_WINDOWS_ONLY
def test_subclass_controlled_declarations_and_scalars_are_rejected(
    tmp_path: Path,
) -> None:
    """Snapshot only exact trusted input types before any path or hash operation."""

    model = tmp_path / "model.bin"
    model.write_bytes(b"real digest must not be bypassed")
    valid = _declaration(model)

    class EvilDeclaration(GuardedFileDeclaration):
        """Declaration subclass that must be rejected before reading fields."""

        field_reads = 0

        def __getattribute__(self, name: str) -> object:
            """Record access to any inherited declaration field."""

            if name in {"path", "size_bytes", "sha256"}:
                type(self).field_reads += 1
            return super().__getattribute__(name)

    class EvilPath(WindowsPath):
        """Path subclass that records any attacker-controlled rendering call."""

        render_calls = 0

        def __str__(self) -> str:
            """Return a plausible path while recording unsafe evaluation."""

            type(self).render_calls += 1
            return super().__str__()

    class EvilInt(int):
        """Integer subclass that could override numeric comparison behavior."""

    class EvilHash(str):
        """String subclass that lies about digest equality and rendering."""

        def __eq__(self, _other: object) -> bool:
            """Pretend an intentionally wrong digest equals every value."""

            return True

        def __ne__(self, _other: object) -> bool:
            """Pretend an intentionally wrong digest differs from no value."""

            return False

        def __str__(self) -> str:
            """Return valid-looking hex independently of stored string behavior."""

            return "0" * 64

    class EvilTuple(tuple[GuardedFileDeclaration, ...]):
        """Tuple subclass that must be rejected before dynamic length checks."""

        length_calls = 0

        def __len__(self) -> int:
            """Record evaluation that exact-container validation must avoid."""

            type(self).length_calls += 1
            return super().__len__()

    evil_path = EvilPath(str(model))
    EvilPath.render_calls = 0
    wrong_hash = EvilHash("0" * 64)
    cases: tuple[object, ...] = (
        EvilTuple((valid,)),
        (EvilDeclaration(valid.path, valid.size_bytes, valid.sha256),),
        (GuardedFileDeclaration(evil_path, valid.size_bytes, valid.sha256),),
        (GuardedFileDeclaration(valid.path, EvilInt(valid.size_bytes), valid.sha256),),
        (GuardedFileDeclaration(valid.path, True, valid.sha256),),
        (GuardedFileDeclaration(valid.path, valid.size_bytes, wrong_hash),),
    )
    count_before = _process_handle_count()

    for declarations in cases:
        with pytest.raises(WindowsReadOnlyFileGuardValidationError):
            WindowsReadOnlyFileGuardSet.acquire(declarations)  # type: ignore[arg-type]
        assert _process_handle_count() == count_before

    assert EvilTuple.length_calls == 0
    assert EvilDeclaration.field_reads == 0
    assert EvilPath.render_calls == 0
    model.write_bytes(b"all attacker values rejected before open")


@_WINDOWS_ONLY
def test_acquire_uses_private_declaration_snapshots_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ignore caller dataclass mutation after exact scalar snapshots are created."""

    original = tmp_path / "original.bin"
    replacement = tmp_path / "replacement.bin"
    original.write_bytes(b"original trusted bytes")
    replacement.write_bytes(b"replacement attacker bytes")
    caller_declaration = _declaration(original)
    replacement_declaration = _declaration(replacement)
    original_capture = file_guard_module._capture_drive_mappings
    observed_private_copy = False

    def mutate_caller_after_validation(
        trusted: tuple[GuardedFileDeclaration, ...],
    ) -> dict[str, str]:
        """Swap every caller field after the validator has returned its copy."""

        nonlocal observed_private_copy
        observed_private_copy = trusted[0] is not caller_declaration
        object.__setattr__(
            caller_declaration, "path", replacement_declaration.path
        )
        object.__setattr__(
            caller_declaration,
            "size_bytes",
            replacement_declaration.size_bytes,
        )
        object.__setattr__(
            caller_declaration, "sha256", replacement_declaration.sha256
        )
        return original_capture(trusted)

    monkeypatch.setattr(
        file_guard_module, "_capture_drive_mappings", mutate_caller_after_validation
    )
    guard = WindowsReadOnlyFileGuardSet.acquire((caller_declaration,))
    try:
        assert observed_private_copy is True
        with pytest.raises(OSError):
            original.write_bytes(b"must remain locked")
        replacement.write_bytes(b"replacement remains unlocked")
        stable = file_guard_module._get_guarded_volume_paths(
            guard,
            access_seal=file_guard_module._GUARDED_VOLUME_PATH_ACCESS_SEAL,
        )[0]
        assert Path(stable).read_bytes() == b"original trusted bytes"
    finally:
        guard.close()


@_WINDOWS_ONLY
def test_canonical_handle_path_mismatch_rolls_back_as_an_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a short-name or namespace alias reported by the opened handle."""

    model = tmp_path / "Long Model Asset Name.bin"
    model.write_bytes(b"canonical spelling")
    original_canonical = file_guard_module._canonical_path_from_handle
    calls = 0
    leaf_call = len(file_guard_module._path_directories(model)) + 1

    def inject_leaf_alias(handle: int) -> str:
        """Change only the leaf's normalized spelling as an alias simulation."""

        nonlocal calls
        calls += 1
        canonical = original_canonical(handle)
        if calls == leaf_call:
            return f"{canonical}.alias"
        return canonical

    count_before = _process_handle_count()
    monkeypatch.setattr(
        file_guard_module, "_canonical_path_from_handle", inject_leaf_alias
    )
    with pytest.raises(WindowsReadOnlyFileGuardValidationError):
        WindowsReadOnlyFileGuardSet.acquire((_declaration(model),))

    assert calls == leaf_call
    assert _process_handle_count() == count_before
    model.write_bytes(b"alias rollback complete")


@_WINDOWS_ONLY
def test_drive_mapping_change_during_acquire_fails_and_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bind the opening transaction to one unchanged fixed-volume namespace."""

    model = tmp_path / "model.bin"
    model.write_bytes(b"drive mapping")
    original_query = file_guard_module._query_fixed_drive_mapping
    query_calls = 0

    def change_second_mapping(drive: str) -> str:
        """Simulate a valid-looking DOS mapping swap after verification."""

        nonlocal query_calls
        query_calls += 1
        mapping = original_query(drive)
        if query_calls == 2:
            return f"{mapping}999"
        return mapping

    count_before = _process_handle_count()
    monkeypatch.setattr(
        file_guard_module, "_query_fixed_drive_mapping", change_second_mapping
    )
    with pytest.raises(WindowsReadOnlyFileGuardAcquireError):
        WindowsReadOnlyFileGuardSet.acquire((_declaration(model),))

    assert query_calls == 2
    assert _process_handle_count() == count_before
    model.write_bytes(b"mapping rollback complete")


@_WINDOWS_ONLY
def test_volume_guid_path_survives_drive_letter_remap(tmp_path: Path) -> None:
    """Keep reopening the locked file after its temporary DOS letter is remapped."""

    model = tmp_path / "model.bin"
    content = b"volume identity survives namespace ABA"
    model.write_bytes(content)
    source_drive = model.drive.upper()
    source_mapping = file_guard_module._query_fixed_drive_mapping(source_drive)
    selected_drive = ""
    for letter in "ZYXWVUTSRQPONMLKJIHGFED":
        candidate = f"{letter}:"
        if Path(f"{candidate}\\").exists():
            continue
        if _define_raw_dos_device(candidate, source_mapping):
            selected_drive = candidate
            break
    if not selected_drive:
        pytest.skip("No temporary raw DOS drive mapping is available.")

    active_target = source_mapping
    guard: WindowsReadOnlyFileGuardSet | None = None
    try:
        alias = Path(f"{selected_drive}{str(model)[2:]}")
        try:
            guard = WindowsReadOnlyFileGuardSet.acquire((_declaration(alias),))
        except WindowsReadOnlyFileGuardValidationError:
            pytest.skip(
                "This filesystem reports another DOS letter as the canonical path."
            )
        stable_path = file_guard_module._get_guarded_volume_paths(
            guard,
            access_seal=file_guard_module._GUARDED_VOLUME_PATH_ACCESS_SEAL,
        )[0]
        assert Path(stable_path).read_bytes() == content

        if not _define_raw_dos_device(
            selected_drive, source_mapping, remove=True
        ):
            pytest.skip("The temporary drive mapping cannot be removed while open.")
        active_target = ""
        replacement = r"\Device\HarddiskVolume999999"
        if not _define_raw_dos_device(selected_drive, replacement):
            pytest.skip("The temporary drive mapping cannot be redirected.")
        active_target = replacement

        assert Path(stable_path).read_bytes() == content
        assert file_guard_module._get_guarded_volume_paths(
            guard,
            access_seal=file_guard_module._GUARDED_VOLUME_PATH_ACCESS_SEAL,
        ) == (stable_path,)
    finally:
        if guard is not None:
            guard.close()
        if active_target:
            _define_raw_dos_device(
                selected_drive, active_target, remove=True
            )


@_WINDOWS_ONLY
def test_stable_guid_reopen_does_not_consult_later_drive_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read the held file by GUID after simulating a declared-letter ABA change."""

    model = tmp_path / "model.bin"
    content = b"stable path bypasses later DOS namespace"
    model.write_bytes(content)
    guard = WindowsReadOnlyFileGuardSet.acquire((_declaration(model),))
    try:
        stable_path = file_guard_module._get_guarded_volume_paths(
            guard,
            access_seal=file_guard_module._GUARDED_VOLUME_PATH_ACCESS_SEAL,
        )[0]
        monkeypatch.setattr(
            file_guard_module,
            "_query_fixed_drive_mapping",
            lambda _drive: r"\Device\HarddiskVolume999999",
        )
        assert Path(stable_path).read_bytes() == content
    finally:
        guard.close()


@_WINDOWS_ONLY
def test_subst_drive_mapping_is_rejected_when_available(tmp_path: Path) -> None:
    """Reject a fixed-storage SUBST alias whose DOS mapping is path-based."""

    model = tmp_path / "model.bin"
    model.write_bytes(b"subst alias")
    selected_drive = ""
    for letter in "ZYXWVUTSRQPONMLKJIHGFED":
        candidate = f"{letter}:"
        if Path(f"{candidate}\\").exists():
            continue
        result = subprocess.run(
            ["subst.exe", candidate, str(tmp_path)],
            check=False,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0:
            selected_drive = candidate
            break
    if not selected_drive:
        pytest.skip("No temporary SUBST drive letter is available.")

    try:
        alias = Path(f"{selected_drive}\\{model.name}")
        with pytest.raises(WindowsReadOnlyFileGuardValidationError):
            WindowsReadOnlyFileGuardSet.acquire((_declaration(alias),))
    finally:
        subprocess.run(
            ["subst.exe", selected_drive, "/D"],
            check=False,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


@_WINDOWS_ONLY
def test_partial_failure_releases_every_provisional_handle(tmp_path: Path) -> None:
    """Roll back good ancestors and leaves when a later declaration mismatches."""

    parent = tmp_path / "assets"
    parent.mkdir()
    first = parent / "first.bin"
    second = parent / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    invalid_second = GuardedFileDeclaration(
        path=second,
        size_bytes=second.stat().st_size,
        sha256="0" * 64,
    )
    count_before = _process_handle_count()

    with pytest.raises(WindowsReadOnlyFileGuardValidationError):
        WindowsReadOnlyFileGuardSet.acquire(
            (_declaration(first), invalid_second)
        )

    assert _process_handle_count() == count_before
    first.write_bytes(b"unlocked")
    moved = tmp_path / "moved-assets"
    parent.rename(moved)
    assert (moved / first.name).read_bytes() == b"unlocked"


@_WINDOWS_ONLY
def test_partial_failure_retries_one_transient_close_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finish rollback when one provisional handle close transiently fails."""

    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    invalid_second = GuardedFileDeclaration(
        second, second.stat().st_size, "0" * 64
    )
    original_close = file_guard_module._close_handle
    failed_once = False

    def fail_once(handle: int) -> bool:
        """Leave one valid handle owned so rollback must retry that exact value."""

        nonlocal failed_once
        if not failed_once:
            failed_once = True
            return False
        return original_close(handle)

    count_before = _process_handle_count()
    monkeypatch.setattr(file_guard_module, "_close_handle", fail_once)
    with pytest.raises(WindowsReadOnlyFileGuardValidationError):
        WindowsReadOnlyFileGuardSet.acquire(
            (_declaration(first), invalid_second)
        )

    assert failed_once is True
    assert file_guard_module._DEFERRED_ROLLBACK_HANDLES == set()
    assert _process_handle_count() == count_before
    first.write_bytes(b"rollback completed")


@_WINDOWS_ONLY
def test_persistent_rollback_failure_retains_ownership_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep persistently unclosed handles tracked until a later safe drain."""

    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    invalid_second = GuardedFileDeclaration(
        second, second.stat().st_size, "0" * 64
    )
    count_before = _process_handle_count()
    with monkeypatch.context() as scoped_patch:
        scoped_patch.setattr(
            file_guard_module, "_close_handle", lambda _handle: False
        )
        with pytest.raises(WindowsReadOnlyFileGuardError):
            WindowsReadOnlyFileGuardSet.acquire(
                (_declaration(first), invalid_second)
            )
        assert file_guard_module._DEFERRED_ROLLBACK_HANDLES
        assert _process_handle_count() > count_before

    file_guard_module._drain_deferred_rollback_handles()
    assert file_guard_module._DEFERRED_ROLLBACK_HANDLES == set()
    assert _process_handle_count() == count_before
    first.write_bytes(b"deferred rollback completed")


@_WINDOWS_ONLY
def test_destructor_transfers_persistent_close_failures_to_deferred_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep abandoned guard handles tracked when destructor closes keep failing."""

    model = tmp_path / "model.bin"
    model.write_bytes(b"destructor ownership")
    guard = WindowsReadOnlyFileGuardSet.acquire((_declaration(model),))
    count_before_drain = _process_handle_count()
    with monkeypatch.context() as scoped_patch:
        scoped_patch.setattr(
            file_guard_module, "_close_handle", lambda _handle: False
        )
        guard.__del__()
        assert guard.closed is True
        assert file_guard_module._DEFERRED_ROLLBACK_HANDLES
        assert _process_handle_count() == count_before_drain

    file_guard_module._drain_deferred_rollback_handles()
    assert file_guard_module._DEFERRED_ROLLBACK_HANDLES == set()
    assert _process_handle_count() < count_before_drain
    model.write_bytes(b"destructor rollback completed")


@_WINDOWS_ONLY
@pytest.mark.parametrize("failure_site", ["directory-snapshot", "leaf-type"])
def test_open_validation_failure_keeps_new_handle_in_rollback_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_site: str,
) -> None:
    """Register each new handle before snapshot or type validation can fail."""

    model = tmp_path / "model.bin"
    model.write_bytes(b"provisional ownership")
    declaration = _declaration(model)
    directory_count = len(file_guard_module._path_directories(model))
    original_snapshot = file_guard_module._snapshot
    original_file_type = file_guard_module._file_type
    snapshot_calls = 0
    type_calls = 0

    def injected_snapshot(handle: int) -> object:
        """Fail the first directory snapshot only for its selected scenario."""

        nonlocal snapshot_calls
        snapshot_calls += 1
        if failure_site == "directory-snapshot" and snapshot_calls == 1:
            raise WindowsReadOnlyFileGuardAcquireError(
                "Injected sanitized snapshot failure."
            )
        return original_snapshot(handle)

    def injected_file_type(handle: int) -> int:
        """Return a non-disk type exactly when the newly opened leaf is checked."""

        nonlocal type_calls
        type_calls += 1
        if failure_site == "leaf-type" and type_calls == directory_count + 1:
            return 0
        return original_file_type(handle)

    count_before = _process_handle_count()
    with monkeypatch.context() as scoped_patch:
        scoped_patch.setattr(file_guard_module, "_snapshot", injected_snapshot)
        scoped_patch.setattr(file_guard_module, "_file_type", injected_file_type)
        scoped_patch.setattr(
            file_guard_module, "_close_handle", lambda _handle: False
        )
        with pytest.raises(WindowsReadOnlyFileGuardError):
            WindowsReadOnlyFileGuardSet.acquire((declaration,))
        assert file_guard_module._DEFERRED_ROLLBACK_HANDLES
        assert _process_handle_count() > count_before

    file_guard_module._drain_deferred_rollback_handles()
    assert file_guard_module._DEFERRED_ROLLBACK_HANDLES == set()
    assert _process_handle_count() == count_before
    model.write_bytes(b"ownership recovered")


@_WINDOWS_ONLY
def test_leaf_open_safely_closes_when_ownership_append_raises(
    tmp_path: Path,
) -> None:
    """Keep a fresh HANDLE locally owned until ledger append actually succeeds."""

    model = tmp_path / "model.bin"
    model.write_bytes(b"append failure")

    class RejectingLedger(list[int]):
        """Test ledger that simulates an extreme allocation failure."""

        def append(self, _handle: int) -> None:
            """Reject ownership transfer before mutating the ledger."""

            raise MemoryError("injected append failure")

    count_before = _process_handle_count()
    with pytest.raises(MemoryError, match="injected append failure"):
        file_guard_module._open_leaf(str(model), RejectingLedger())

    assert _process_handle_count() == count_before
    assert file_guard_module._DEFERRED_ROLLBACK_HANDLES == set()
    model.write_bytes(b"new handle was closed")


def test_direct_construction_cannot_adopt_arbitrary_native_handles() -> None:
    """Require acquire provenance before a guard can own or close any handle."""

    secret_handle = 918_273_645
    with pytest.raises(WindowsReadOnlyFileGuardValidationError) as raised:
        WindowsReadOnlyFileGuardSet(
            (secret_handle,),
            file_count=1,
        )
    rendered_error = f"{raised.value!s} {raised.value!r}"
    assert str(secret_handle) not in rendered_error


@_WINDOWS_ONLY
def test_subclass_factory_cannot_hijack_verified_handle_transfer(
    tmp_path: Path,
) -> None:
    """Reject subclass factories before opening files or invoking hostile init."""

    model = tmp_path / "model.bin"
    model.write_bytes(b"must remain unowned")
    declaration = _declaration(model)

    class MaliciousGuard(WindowsReadOnlyFileGuardSet):
        """Test double that would discard handles if its initializer ran."""

        initializer_called = False

        def __init__(self, *_arguments: object, **_keywords: object) -> None:
            """Record an attempted untrusted ownership transfer."""

            type(self).initializer_called = True

    count_before = _process_handle_count()
    with pytest.raises(WindowsReadOnlyFileGuardValidationError):
        MaliciousGuard.acquire((declaration,))

    assert MaliciousGuard.initializer_called is False
    assert _process_handle_count() == count_before
    model.write_bytes(b"still writable")


@_WINDOWS_ONLY
def test_repr_and_errors_never_render_paths_hashes_or_handles(tmp_path: Path) -> None:
    """Keep private catalog values out of declarations, guards, and failures."""

    secret = "DO-NOT-RENDER-VOICE-ASSET"
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    missing = tmp_path / secret / "model.bin"
    declaration = GuardedFileDeclaration(missing, len(secret), digest)
    rendered_declaration = repr(declaration)
    assert secret not in rendered_declaration
    assert digest not in rendered_declaration

    with pytest.raises(WindowsReadOnlyFileGuardError) as raised:
        WindowsReadOnlyFileGuardSet.acquire((declaration,))
    rendered_error = f"{raised.value!s} {raised.value!r}"
    assert secret not in rendered_error
    assert digest not in rendered_error

    model = tmp_path / "model.bin"
    model.write_bytes(b"safe repr")
    guard = WindowsReadOnlyFileGuardSet.acquire((_declaration(model),))
    try:
        rendered_guard = repr(guard)
        assert str(model) not in rendered_guard
        assert _declaration(model).sha256 not in rendered_guard
        assert "handle" not in rendered_guard.casefold()
    finally:
        guard.close()


@_WINDOWS_ONLY
def test_concurrent_close_has_one_owner_and_does_not_publish_early(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Make concurrent closers join cleanup before publishing the closed state."""

    model = tmp_path / "model.bin"
    model.write_bytes(b"concurrent")
    guard = WindowsReadOnlyFileGuardSet.acquire((_declaration(model),))
    first_close_entered = Event()
    release_first_close = Event()
    second_close_started = Event()
    second_close_finished = Event()
    call_lock = Lock()
    errors: list[BaseException] = []
    call_count = 0
    original_close = file_guard_module._close_handle

    def paused_close(handle: int) -> bool:
        """Pause the first native close while the guard remains unpublished."""

        nonlocal call_count
        with call_lock:
            call_count += 1
            selected_call = call_count
        if selected_call == 1:
            first_close_entered.set()
            assert release_first_close.wait(5.0)
        return original_close(handle)

    def close_guard(started: Event | None = None, finished: Event | None = None) -> None:
        """Close from one thread while recording unexpected failures."""

        if started is not None:
            started.set()
        try:
            guard.close()
        except BaseException as error:
            errors.append(error)
        finally:
            if finished is not None:
                finished.set()

    monkeypatch.setattr(file_guard_module, "_close_handle", paused_close)
    first = Thread(target=close_guard, daemon=True)
    second = Thread(
        target=close_guard,
        args=(second_close_started, second_close_finished),
        daemon=True,
    )
    try:
        first.start()
        assert first_close_entered.wait(2.0)
        assert guard.closed is False
        assert "closing" in repr(guard)
        with pytest.raises(WindowsReadOnlyFileGuardValidationError):
            file_guard_module._get_guarded_volume_paths(
                guard,
                access_seal=file_guard_module._GUARDED_VOLUME_PATH_ACCESS_SEAL,
            )
        second.start()
        assert second_close_started.wait(2.0)
        assert second_close_finished.wait(0.1) is False
        assert guard.closed is False

        release_first_close.set()
        first.join(5.0)
        second.join(5.0)
        assert first.is_alive() is False
        assert second.is_alive() is False
        assert errors == []
        assert guard.closed is True
    finally:
        release_first_close.set()
        first.join(1.0)
        second.join(1.0)
        guard.close()


@_WINDOWS_ONLY
def test_acquisition_transaction_serializes_rollback_and_deferred_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent a second acquisition from passing a rollback not yet published."""

    failing_file = tmp_path / "failing.bin"
    succeeding_file = tmp_path / "succeeding.bin"
    failing_file.write_bytes(b"fails hash verification")
    succeeding_file.write_bytes(b"valid second transaction")
    invalid = GuardedFileDeclaration(
        failing_file, failing_file.stat().st_size, "0" * 64
    )
    valid = _declaration(succeeding_file)
    rollback_entered = Event()
    release_rollback = Event()
    second_started = Event()
    second_finished = Event()
    second_reached_gate = Event()
    errors: dict[str, BaseException] = {}
    second_guard: list[WindowsReadOnlyFileGuardSet] = []
    original_close = file_guard_module._close_handle
    original_drain = file_guard_module._drain_deferred_rollback_handles
    first_thread: Thread

    def controlled_close(handle: int) -> bool:
        """Keep first-transaction handles owned until its rollback is published."""

        if current_thread() is first_thread:
            if not rollback_entered.is_set():
                rollback_entered.set()
                assert release_rollback.wait(20.0)
            return False
        return original_close(handle)

    def observed_drain() -> None:
        """Mark when the second transaction reaches the fail-closed gate."""

        if current_thread() is not first_thread and second_started.is_set():
            second_reached_gate.set()
        original_drain()

    def acquire_invalid() -> None:
        """Run a transaction whose persistent close failures enter deferred ownership."""

        try:
            WindowsReadOnlyFileGuardSet.acquire((invalid,))
        except BaseException as error:
            errors["first"] = error

    def acquire_valid() -> None:
        """Attempt a second transaction while the first still owns the process lock."""

        second_started.set()
        try:
            second_guard.append(WindowsReadOnlyFileGuardSet.acquire((valid,)))
        except BaseException as error:
            errors["second"] = error
        finally:
            second_finished.set()

    monkeypatch.setattr(file_guard_module, "_close_handle", controlled_close)
    monkeypatch.setattr(
        file_guard_module, "_drain_deferred_rollback_handles", observed_drain
    )
    first_thread = Thread(target=acquire_invalid, daemon=True)
    try:
        first_thread.start()
        assert rollback_entered.wait(2.0)
        # Low-level start returns immediately even when the new thread blocks
        # before threading.Thread can finish its startup handshake.
        _thread.start_new_thread(acquire_valid, ())
        assert second_started.wait(2.0)
        assert second_reached_gate.wait(0.1) is False

        release_rollback.set()
        first_thread.join(5.0)
        assert second_finished.wait(5.0)
        assert first_thread.is_alive() is False
        assert isinstance(errors.get("first"), WindowsReadOnlyFileGuardError)
        assert "second" not in errors
        assert second_reached_gate.is_set()
        assert len(second_guard) == 1
        assert file_guard_module._DEFERRED_ROLLBACK_HANDLES == set()
    finally:
        release_rollback.set()
        first_thread.join(1.0)
        second_finished.wait(1.0)
        for guard in second_guard:
            guard.close()
        if file_guard_module._DEFERRED_ROLLBACK_HANDLES:
            file_guard_module._drain_deferred_rollback_handles()


@pytest.mark.parametrize(
    "bad_declarations",
    [
        (),
        [],
        (object(),),
        (GuardedFileDeclaration(Path("relative.bin"), 0, "0" * 64),),
        (GuardedFileDeclaration(Path("c:\\lowercase.bin"), 0, "0" * 64),),
        (GuardedFileDeclaration(Path("C:\\bad\\..\\alias.bin"), 0, "0" * 64),),
        (
            GuardedFileDeclaration(
                Path("\\\\server\\share\\model.bin"), 0, "0" * 64
            ),
        ),
    ],
)
def test_invalid_declaration_shapes_are_rejected_on_windows(
    bad_declarations: object,
) -> None:
    """Keep declaration containers, paths, sizes, and hashes closed and canonical."""

    if os.name != "nt":
        pytest.skip("Path canonicalization in this test is Windows-specific.")
    with pytest.raises(WindowsReadOnlyFileGuardValidationError):
        WindowsReadOnlyFileGuardSet.acquire(bad_declarations)  # type: ignore[arg-type]


def test_unavailable_platform_fails_before_inspecting_declarations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remain importable elsewhere and reject calls with one stable error type."""

    monkeypatch.setattr(file_guard_module, "_IS_WINDOWS", False)
    with pytest.raises(WindowsReadOnlyFileGuardUnavailableError):
        WindowsReadOnlyFileGuardSet.acquire((object(),))  # type: ignore[arg-type]
