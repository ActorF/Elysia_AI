"""Verify static embedded Python review and explicit recoverable setup changes."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from image_generation import python_environment as environment
from scripts.prepare_image_python import main


def _fixture(tmp_path: Path, content: bytes = b"../ComfyUI\r\npython313.zip\r\n.\r\n\r\n#import site\r\nimport site\r\n") -> tuple[Path, Path, Path]:
    python_root = tmp_path / "python_embeded"
    runtime = tmp_path / "ComfyUI"
    python_root.mkdir()
    runtime.mkdir()
    executable = python_root / "python.exe"
    executable.write_bytes(b"trusted fixture executable")
    (python_root / "python313.zip").write_bytes(b"trusted fixture standard library")
    (python_root / "Lib" / "site-packages").mkdir(parents=True)
    pth = python_root / "python313._pth"
    pth.write_bytes(content)
    return executable, runtime, pth


def test_relative_embedded_configuration_requires_explicit_preparation(tmp_path: Path) -> None:
    """Static readiness and preview cannot silently rewrite an installation."""

    executable, runtime, pth = _fixture(tmp_path)
    before = pth.read_bytes()
    with pytest.raises(environment.PythonEnvironmentPreparationRequiredError, match="--apply"):
        environment.check_python_environment(executable, runtime)
    preview = environment.prepare_python_environment(executable, runtime)
    assert preview.applied is False
    assert preview.review.requires_preparation
    assert preview.review.adds_site_packages
    assert preview.backup_name is None
    assert pth.read_bytes() == before
    assert set(pth.parent.glob("*.bak")) == set()


def test_explicit_preparation_preserves_backup_comments_line_endings_and_other_files(tmp_path: Path) -> None:
    """Only declared roots and explicit native site-packages admission may change."""

    executable, runtime, pth = _fixture(tmp_path)
    before = pth.read_bytes()
    untouched = pth.parent / "python313.dll"
    untouched.write_bytes(b"untouched DLL")
    result = environment.prepare_python_environment(executable, runtime, apply=True)
    expected = "\r\n".join([str(runtime), str(pth.parent / "python313.zip"), str(pth.parent), "", "#import site",
                               str(pth.parent / "Lib" / "site-packages"), "import site", ""]).encode()
    assert pth.read_bytes() == expected
    assert result.applied
    assert result.review.before_sha256 == hashlib.sha256(before).hexdigest()
    assert result.review.after_sha256 == hashlib.sha256(expected).hexdigest()
    assert result.backup_name is not None
    assert (pth.parent / result.backup_name).read_bytes() == before
    assert untouched.read_bytes() == b"untouched DLL"
    assert executable.read_bytes() == b"trusted fixture executable"
    ready = environment.check_python_environment(executable, runtime)
    assert ready.mode == "embedded" and not ready.requires_preparation
    assert environment.prepare_python_environment(executable, runtime, apply=True).applied is False


@pytest.mark.parametrize("declaration", [
    "../../outside", "../ComfyUI/custom_nodes", "import os", "import\tsite", "import site;print(1)",
    "import site ", "C:relative", "C:", "//server/share", "\\\\?\\Volume{invalid}\\site", ".;payload", ". ",
])
def test_unreviewed_roots_code_and_aliases_fail_without_mutation(tmp_path: Path, declaration: str) -> None:
    """The setup helper cannot approve unrelated roots, code directives, or aliases."""

    executable, runtime, pth = _fixture(tmp_path, (declaration + "\n").encode())
    before = pth.read_bytes()
    with pytest.raises(environment.PythonEnvironmentError):
        environment.prepare_python_environment(executable, runtime, apply=True)
    assert pth.read_bytes() == before
    assert not list(pth.parent.glob("*.bak"))


def test_duplicate_and_ambiguous_configuration_is_rejected(tmp_path: Path) -> None:
    """A hidden competing path file or duplicate root cannot choose startup policy."""

    executable, runtime, pth = _fixture(tmp_path, b".\n./\n")
    with pytest.raises(environment.PythonEnvironmentError, match="repeats"):
        environment.prepare_python_environment(executable, runtime)
    pth.write_bytes(b".\n")
    (pth.parent / "python._pth").write_bytes(b".\n")
    with pytest.raises(environment.PythonEnvironmentError, match="ambiguous"):
        environment.prepare_python_environment(executable, runtime)


def test_regular_single_link_and_byte_limits_are_enforced(tmp_path: Path) -> None:
    """Hard-link aliases and oversized configuration cannot cross the setup boundary."""

    executable, runtime, pth = _fixture(tmp_path)
    alias = tmp_path / "alias._pth"
    os.link(pth, alias)
    with pytest.raises(environment.PythonEnvironmentError, match="regular file"):
        environment.prepare_python_environment(executable, runtime)
    alias.unlink()
    pth.write_bytes(b"#" * 65_537)
    with pytest.raises(environment.PythonEnvironmentError):
        environment.prepare_python_environment(executable, runtime)


def test_symlinked_configuration_is_rejected(tmp_path: Path) -> None:
    """A redirected path file must never be read or replaced by the operator tool."""

    executable, runtime, pth = _fixture(tmp_path)
    original = tmp_path / "original._pth"
    pth.rename(original)
    try:
        pth.symlink_to(original)
    except OSError:
        pytest.skip("The host does not permit creating a symlink fixture.")
    with pytest.raises(environment.PythonEnvironmentError, match="redirected"):
        environment.prepare_python_environment(executable, runtime, apply=True)
    assert original.read_bytes().startswith(b"../ComfyUI")


def test_full_interpreter_passes_and_known_windows_venv_redirector_fails(tmp_path: Path) -> None:
    """Actual full Python needs no path rewrite; a venv redirector is incompatible."""

    executable, runtime, pth = _fixture(tmp_path)
    pth.unlink()
    assert environment.check_python_environment(executable, runtime).mode == "standard"
    scripts = tmp_path / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    redirector = scripts / "python.exe"
    redirector.write_bytes(b"redirector")
    (scripts.parent / "pyvenv.cfg").write_text("home=reviewed-base")
    with pytest.raises(environment.PythonEnvironmentError, match="redirectors"):
        environment.check_python_environment(redirector, runtime)


def test_configuration_without_final_newline_still_adds_a_distinct_site_root(tmp_path: Path) -> None:
    """Adding an explicit dependency root cannot concatenate two path declarations."""

    executable, runtime, pth = _fixture(tmp_path, b"\xef\xbb\xbf.")
    environment.prepare_python_environment(executable, runtime, apply=True)
    assert pth.read_bytes().startswith(b"\xef\xbb\xbf")
    assert pth.read_text(encoding="utf-8-sig").splitlines() == [str(pth.parent), str(pth.parent / "Lib" / "site-packages")]
    environment.check_python_environment(executable, runtime)


def test_compare_before_publication_preserves_a_concurrent_editor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A setup lock does not authorize overwriting another editor's changed bytes."""

    executable, runtime, pth = _fixture(tmp_path)
    original_write = environment._write_exclusive

    def _edit_after_staging(target: Path, data: bytes) -> tuple[int, int, int, int]:
        identity = original_write(target, data)
        if target.name.endswith(".tmp"):
            pth.write_bytes(b"# concurrent editor\n")
        return identity

    monkeypatch.setattr(environment, "_write_exclusive", _edit_after_staging)
    with pytest.raises(environment.PythonEnvironmentError):
        environment.prepare_python_environment(executable, runtime, apply=True)
    assert pth.read_bytes() == b"# concurrent editor\n"


def test_failure_after_atomic_replace_rolls_back_and_keeps_original_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ambiguous publication failure restores only the exact new file we own."""

    executable, runtime, pth = _fixture(tmp_path)
    before = pth.read_bytes()
    replace = os.replace
    calls = 0

    def _fail_after_first_replace(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        replace(source, target)
        if calls == 1:
            raise OSError("private native detail")

    monkeypatch.setattr(environment.os, "replace", _fail_after_first_replace)
    with pytest.raises(environment.PythonEnvironmentError) as caught:
        environment.prepare_python_environment(executable, runtime, apply=True)
    assert "private native" not in str(caught.value)
    assert pth.read_bytes() == before
    assert len(list(pth.parent.glob("*.bak"))) == 1
    assert not list(pth.parent.glob("*.lock"))
    assert not list(pth.parent.glob("*.tmp*"))


def test_existing_conflicting_backup_and_lock_fail_closed(tmp_path: Path) -> None:
    """Original backups and another operator's reservation are never overwritten."""

    executable, runtime, pth = _fixture(tmp_path)
    before = pth.read_bytes()
    backup = pth.with_name(pth.name + ".elysia-backup-" + hashlib.sha256(before).hexdigest() + ".bak")
    backup.write_bytes(b"different original")
    with pytest.raises(environment.PythonEnvironmentError):
        environment.prepare_python_environment(executable, runtime, apply=True)
    assert pth.read_bytes() == before and backup.read_bytes() == b"different original"
    backup.unlink()
    lock = pth.with_name(pth.name + ".elysia-prepare.lock")
    lock.write_bytes(b"another operator")
    with pytest.raises(environment.PythonEnvironmentError):
        environment.prepare_python_environment(executable, runtime, apply=True)
    assert lock.read_bytes() == b"another operator"


def test_conditional_rollback_never_overwrites_a_later_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A different destination owner survives failed post-publication verification."""

    executable, runtime, pth = _fixture(tmp_path)
    replace = os.replace

    def _replace_then_external_edit(source: Path, target: Path) -> None:
        replace(source, target)
        target.unlink()
        target.write_bytes(b"# a later operator replacement\n")

    monkeypatch.setattr(environment.os, "replace", _replace_then_external_edit)
    with pytest.raises(environment.PythonEnvironmentError):
        environment.prepare_python_environment(executable, runtime, apply=True)
    assert pth.read_bytes() == b"# a later operator replacement\n"


def test_failed_rollback_retains_the_exact_original_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If native rollback fails, recovery still has the complete original bytes."""

    executable, runtime, pth = _fixture(tmp_path)
    before = pth.read_bytes()
    replace = os.replace
    calls = 0

    def _unreliable_replace(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            replace(source, target)
        raise OSError("private rollback failure")

    monkeypatch.setattr(environment.os, "replace", _unreliable_replace)
    with pytest.raises(environment.PythonEnvironmentError, match="backup is retained"):
        environment.prepare_python_environment(executable, runtime, apply=True)
    backups = list(pth.parent.glob("*.bak"))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert pth.read_bytes() != before


@pytest.mark.parametrize("content", [b"\xff\xfe", b".\x00\n", b"import site\nimport site\n", b"#x\n" * 129])
def test_invalid_text_or_repeated_directives_are_rejected(tmp_path: Path, content: bytes) -> None:
    """Encoding, control bytes, and directive limits cannot hide another policy."""

    executable, runtime, pth = _fixture(tmp_path, content)
    with pytest.raises(environment.PythonEnvironmentError):
        environment.prepare_python_environment(executable, runtime, apply=True)
    assert pth.read_bytes() == content


def test_cli_check_is_read_only_and_reports_safe_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Operator command checks need explicit apply and never echo private paths."""

    executable, runtime, pth = _fixture(tmp_path)
    before = pth.read_bytes()
    arguments = ["--python", str(executable), "--runtime-root", str(runtime)]
    assert main([*arguments, "--check"]) == 1
    assert str(tmp_path) not in capsys.readouterr().out
    assert pth.read_bytes() == before
    assert main([*arguments, "--apply"]) == 0
    assert '"applied": true' in capsys.readouterr().out
    assert main([*arguments, "--check"]) == 0


@pytest.mark.parametrize("foreign_name", ["temporary", "rollback"])
def test_failed_exclusive_create_preserves_foreign_transaction_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, foreign_name: str,
) -> None:
    """An O_EXCL collision grants no authority to clean another file by its name."""

    executable, runtime, pth = _fixture(tmp_path)
    token = "f" * 32
    monkeypatch.setattr(environment, "uuid4", lambda: SimpleNamespace(hex=token))
    temporary = pth.with_name("." + pth.name + ".elysia-" + token + ".tmp")
    foreign = temporary if foreign_name == "temporary" else temporary.with_name(temporary.name + ".rollback")
    foreign.write_bytes(b"foreign transaction file")
    if foreign_name == "rollback":
        replace = os.replace

        def _failed_publication(source: Path, target: Path) -> None:
            replace(source, target)
            raise OSError("ambiguous publication")

        monkeypatch.setattr(environment.os, "replace", _failed_publication)
    with pytest.raises(environment.PythonEnvironmentError):
        environment.prepare_python_environment(executable, runtime, apply=True)
    assert foreign.read_bytes() == b"foreign transaction file"


@pytest.mark.parametrize("replaced_name", ["temporary", "lock"])
def test_replaced_staging_or_lock_is_never_published_or_unlinked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replaced_name: str,
) -> None:
    """Cleanup uses the acquired identity even if a later editor reuses its name."""

    executable, runtime, pth = _fixture(tmp_path)
    before = pth.read_bytes()
    original_write = environment._write_exclusive
    replaced: list[Path] = []

    def _replace_owned_name(target: Path, data: bytes) -> tuple[int, int, int, int]:
        identity = original_write(target, data)
        if target.name.endswith(".tmp"):
            selected = target if replaced_name == "temporary" else pth.with_name(pth.name + ".elysia-prepare.lock")
            selected.unlink()
            selected.write_bytes(b"later operator owns this file")
            replaced.append(selected)
        return identity

    monkeypatch.setattr(environment, "_write_exclusive", _replace_owned_name)
    with pytest.raises(environment.PythonEnvironmentError):
        environment.prepare_python_environment(executable, runtime, apply=True)
    assert replaced[0].read_bytes() == b"later operator owns this file"
    if replaced_name == "temporary":
        assert pth.read_bytes() == before
