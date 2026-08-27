# Elysia AI

Elysia is a local-first AI companion. The Python Core owns Chats, Memory,
Projects, recovery, and Ollama model access; the Windows desktop client uses a
sandboxed React/Electron interface.

The project is currently in Stage 6. Modules 1–4 provide the desktop shell,
the versioned authenticated Electron-to-Python protocol, an evidence-based
shell decision, and a responsive design system with system/light/dark themes,
keyboard navigation, accessible feedback states, and a stable app layout.
Electron is frozen as the production desktop shell; the Tauri prototype and
its toolchain were removed after the decision, so there is only one supported
desktop implementation. The next product slice connects real Chat sidebar and
session actions to the existing Python services.
See the [desktop shell decision](docs/decisions/0001-desktop-shell.md), the
[desktop development guide](desktop/README.md), and the
[language-neutral protocol contract](desktop_protocol/README.md).

Runtime user data is stored under `workspace/` and is intentionally excluded
from Git. Do not delete that directory during source or build cleanup.
