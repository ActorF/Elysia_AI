# Elysia AI

Elysia is a local-first AI companion. The Python Core owns Chats, Memory,
Projects, recovery, and Ollama model access; the Windows desktop client uses a
sandboxed React/Electron interface.

Stage 6 is complete. Modules 1–10 provide the desktop shell,
the versioned authenticated Electron-to-Python protocol, an evidence-based
shell decision, the responsive design system, persistent Chat and Project
surfaces, resilient message interactions, revisioned local Settings, and
scoped local attachment storage. The first local text-chat MVP now preserves
Chat drafts across renderer or application restarts, reconnects non-Chat views,
and reattaches a refreshed renderer to an Electron-owned streamed reply. Chat
attachments and Project sources can be selected or dropped, previewed, removed,
and recovered across failures without exposing source paths to React; document
parsing and RAG remain future work.
Electron is frozen as the production desktop shell; the Tauri prototype and
its toolchain were removed after the decision, so there is only one supported
desktop implementation. Settings now distinguish Global defaults from existing
Project inheritance and Chat-pinned model snapshots without exposing secrets.
See the [desktop shell decision](docs/decisions/0001-desktop-shell.md), the
[desktop development guide](desktop/README.md), and the
[language-neutral protocol contract](desktop_protocol/README.md).

Runtime user data is stored under `workspace/` and is intentionally excluded
from Git. Do not delete that directory during source or build cleanup.
