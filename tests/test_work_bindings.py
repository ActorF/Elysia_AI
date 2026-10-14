"""Verify Chat-scoped workspace persistence, revocation, and root replacement."""

from pathlib import Path

import pytest

from work_tools.bindings import WorkspaceBindings


def test_bindings_survive_restart_and_do_not_cross_chat(tmp_path: Path) -> None:
    """One selected Chat keeps its root while unrelated Chats remain unbound."""

    root = tmp_path / "selected"
    root.mkdir()
    store = WorkspaceBindings(tmp_path / "private")
    saved = store.set("chat_one", str(root))
    assert saved is not None
    saved.validate_identity()
    assert WorkspaceBindings(tmp_path / "private").get("chat_one") == saved
    assert store.get("chat_two") is None
    store.set("chat_one", None)
    assert store.get("chat_one") is None


def test_replaced_root_revokes_prior_identity(tmp_path: Path) -> None:
    """Recreating the same path must not reuse a prior user-selected capability."""

    root = tmp_path / "selected"
    root.mkdir()
    binding = WorkspaceBindings(tmp_path / "private").set("chat_one", str(root))
    assert binding is not None
    root.rename(tmp_path / "old-root")
    root.mkdir()
    with pytest.raises(ValueError, match="changed"):
        binding.validate_identity()


@pytest.mark.parametrize("chat_id", ["../escape", "chat_../escape", "other_one", "chat_"])
def test_binding_identifier_cannot_escape_private_store(tmp_path: Path, chat_id: str) -> None:
    """Opaque identifiers never become arbitrary private-data paths."""

    with pytest.raises(ValueError):
        WorkspaceBindings(tmp_path).get(chat_id)
