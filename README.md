# Elysia AI

Elysia is a local-first AI companion. The Python Core owns Chats, Memory,
Projects, recovery, and Ollama model access; the Windows desktop client uses a
sandboxed React/Electron interface.

The project is currently in Stage 6. Modules 1–2 provide the desktop shell and
the versioned, authenticated Electron-to-Python protocol. Development setup
and verification commands are documented in
[`desktop/README.md`](desktop/README.md), while the language-neutral protocol
contract lives in [`desktop_protocol/README.md`](desktop_protocol/README.md).

Runtime user data is stored under `workspace/` and is intentionally excluded
from Git. Do not delete that directory during source or build cleanup.
