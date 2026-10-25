# Elysia Desktop

**0.1.0 · 2026-10-25 delivery snapshot.** Electron + React/TypeScript with a local Python backend. For the installer and first launch, use the [project README](../README.md) or [English overview](../README.en.md).

## Run from source

Requires Windows x64, Python 3.14 with pip, Node.js 24, Git, and a running local Ollama instance. Use **CMD**; replace the example checkout path if needed:

```bat
cd /d "D:\Elysia_AI"
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
ollama pull qwen3.5:9b
cd desktop
npm.cmd ci
npm.cmd run dev
```

Later launches need only the directory change and `npm.cmd run dev`. Repeat dependency installation after requirements or lockfile changes. A root `.env` is optional for basic chat.

The development command builds Main/Preload, starts Vite, and opens Electron with the repository's Python backend. Closing the app or pressing Ctrl+C ends the managed development processes. `npm.cmd run dev:renderer` is a browser preview; native tools, backend integration, and audio require Electron.

## Data and configuration

The installed app defaults to `%APPDATA%\Elysia\data`. Electron keeps its profile and bootstrap pointer under `%APPDATA%\Elysia`; source development may use a different Electron profile. Always use **Settings → Data & storage** to find the active root.

| Location | Contents |
| --- | --- |
| `<data-root>/workspace` | Chats, Projects, memory, sources, settings, gallery, and saved Work/Vision records |
| `<data-root>/logs` | Diagnostic logs |
| `<data-root>/cache` | Rebuildable application cache |
| `<data-root>/models` | Installed-app speech assets and runtimes |
| `<checkout>/models` | Source-development speech assets and runtimes |

A data move carries assets inside the active root, but does not relocate the Electron profile, Ollama models, source-checkout models, or external runtimes. Close Elysia before backing up both the active root and Electron profile. Use the application's temporary-data cleanup; `models/cache` contains installed runtimes and is not disposable cache. [Full storage contract](../docs/13-PRODUCTION-DATA-LAYOUT.md)

Chat defaults to `qwen3.5:9b` at `http://localhost:11434`. Settings accepts local Ollama models and loopback addresses; remote/cloud models are unsupported. Interface language, theme, and the saved user name are configurable in Settings. Model weights are never downloaded automatically by Elysia.

## Interface and permissions

- `Ctrl+,` opens Settings; `Ctrl+K` searches chats; `Ctrl+B` toggles navigation. Enter sends and Shift+Enter inserts a line.
- The icon rail, project conversation tree, central chat, and bottom composer share one rounded frame. Light mode uses pearl white, rose gray, pink actions, and lilac selections; dark mode uses plum surfaces. A faint champagne outline and native caption colors follow the theme.
- Hover, focus, or click information icons for help. Escape dismisses the current layer. Commands, file changes, sources, and errors remain inspectable.
- Work defaults to **Ask before acting**. Each CMD command requires an inline Yes / No decision. **Allow CMD commands** runs commands directly with the current Windows user's access, including files and network. Structured file tools stay within the selected Workspace; CMD is not limited to that folder.
- Stop terminates managed command processes. CMD changes have no automatic Undo. Work retries reuse results without rerunning actions; ordinary chat retries can refresh time and online evidence.
- Deep thinking requests a model capability. Elysia's thoughts summarizes actual processing stages rather than exposing private model reasoning.
- Chat attachments are not automatically indexed. Add Project sources and explicitly enable their use for a grounded answer. Memory participates in context, without a separate memory editor.

For complete behavior, see [Work](../docs/17-WORKFLOW-AND-DESKTOP-AGENT.md), [Vision](../docs/18-VISION-AND-SCREEN.md), [Internet](../docs/19-INTERNET-AND-SERVICES.md), and [Project sources](../docs/12-KNOWLEDGE-UI-TESTING.md).

## Optional features

| Capability | Setup |
| --- | --- |
| Project sources | `ollama pull qwen3-embedding:0.6b`, then import and index Project files. |
| Internet | Off by default. Enable in Settings for model-selected search and city weather; external writes require a separate review. CMD permissions are independent of this switch. |
| OCR and image questions | Tesseract with language data for OCR, or a compatible installed local Ollama vision model. Captures and analysis require explicit actions. |
| Image generation | Trusted ComfyUI, full Python interpreter, and compatible SDXL model selected in Settings. [Guide](../docs/16-LOCAL-IMAGE-GENERATION.md) |
| Song covers | Source-only CUDA/WSL runtimes and authorized assets; not supported by the installer. [Guide](../docs/15-SONG-COVER.md) |
| Desktop pet | A supported external program, disabled by default. [Guide](../docs/14-LIVE2D-RUNTIME.md) |

Optional runtimes and large models are not bundled. Their absence does not block text chat. Hardware performance, voice quality, and third-party runtime compatibility must be checked on the actual machine.

## Optional voice setup

The shared Python runtime includes Faster-Whisper libraries and its small Silero VAD asset, but no recognition checkpoints. `requirements-stt.txt` is a compatibility entry point to the same runtime list; installing `requirements.txt` already includes these libraries.

1. Use the installed app's active data root or, for development, the checkout as the model asset root.
2. Place a complete recognition model at `models/weights/faster-whisper/small/` under that root. Supported names are `tiny`, `base`, `small`, `medium`, `large-v3`, and `turbo`. Select it in Settings and restart the backend. CPU is the default.
3. For speech playback, prepare the GPT-SoVITS runtime under `models/cache/GPT-SoVITS-v2-240821/` and authorized weights/reference audio under `models/weights/gpt-sovits/`.
4. Copy [voice_profiles.example.json](../config/voice_profiles.example.json) to the active data root's `workspace/settings/voice-profiles.json`. Replace every Schema v2 asset placeholder with its real relative path, byte size, and SHA256, plus the exact reference text and language. Use `/` in relative paths.
5. Select the configured profile in Settings and restart the backend. The desktop owns its speech worker; stop any independently started GPT-SoVITS API before desktop playback.

For profiles marked `local-evaluation-only`, source development requires an explicit `GPT_SOVITS_ALLOW_LOCAL_EVALUATION=True` opt-in after reviewing asset rights and paths; it grants no redistribution permission.

Dictate appends text to the draft without sending. Voice Call sends completed utterances to the current chat; Mute pauses capture, and End call/Escape releases it. Text chat is silent unless read-aloud is requested. Speech volume applies to each new clip; model, profile, emotion, and rate changes may require the restart indicated in Settings.

Use the [voice validation guide](../docs/03-VOICE-PERFORMANCE-SAFETY-RIGHTS.md) for device checks and local synthesis verification. Automated tests do not establish microphone quality, echo cancellation, GPU speed, or long-session stability.

## Verify changes

From the repository root in CMD:

```bat
call .venv\Scripts\activate.bat
python scripts/check_python_documentation.py
python scripts/check_distribution_assets.py
python -m pytest -q
cd desktop
npm.cmd run docs:check
npm.cmd run lint
npm.cmd run typecheck
npm.cmd test
npm.cmd audit --audit-level=high
```

`npm.cmd test` runs contract and UI tests. Focused commands are `npm.cmd run test:contract` and `npm.cmd run test:ui`. UI checks launch real Electron windows; run them sequentially and avoid interacting with those windows. The test preload is excluded from distribution. Source documentation must satisfy [AGENTS.md](../AGENTS.md).

## Build the Windows EXE

From `desktop` in CMD, activate the repository's Python 3.14 environment so the packaging script uses the correct interpreter:

```bat
call ..\.venv\Scripts\activate.bat
npm.cmd ci
npm.cmd run make
```

Output:

- `out/Elysia-Setup-0.1.0.exe` — installer.
- `out/win-unpacked/Elysia.exe` — unpacked application; keep its complete surrounding directory.

`make` stages the backend, builds the app, creates the NSIS installer, and runs package/installer smoke checks. For only an unpacked build, use `npm.cmd run package`. These are alternatives; running both repeats the build. Build downloads require internet access.

The package contains isolated CPython 3.14, application-local Microsoft runtime DLLs, allowlisted backend resources, and dependencies pinned by `requirements-runtime.txt`. It excludes developer `.env`, private data, large models, reference audio, and external runtimes. [backend-runtime.json](./backend-runtime.json) and [package_backend.py](../scripts/package_backend.py) define the runtime source, hashes, and allowlist. Packaged startup ignores development Python overrides.

Recheck an existing build with `npm.cmd run smoke:package` and `npm.cmd run smoke:installer`. These use isolated data; installer smoke extracts and runs the payload in a fresh path without registering an installation. They do not certify every model, peripheral, or clean-machine upgrade/uninstall scenario.

### Audit distribution assets

After building, run from `desktop` in CMD:

```bat
set "ELYSIA_AUDIT_DIR=%TEMP%\elysia-package-audit-%RANDOM%-%RANDOM%"
mkdir "%ELYSIA_AUDIT_DIR%"
npx.cmd --no-install asar list out\win-unpacked\resources\app.asar > "%ELYSIA_AUDIT_DIR%\asar-listing.txt"
npx.cmd --no-install asar extract out\win-unpacked\resources\app.asar "%ELYSIA_AUDIT_DIR%\extracted"
..\.venv\Scripts\python.exe ..\scripts\check_distribution_assets.py --unpacked-tree out\win-unpacked --asar-listing "%ELYSIA_AUDIT_DIR%\asar-listing.txt" --extracted-asar-tree "%ELYSIA_AUDIT_DIR%\extracted"
```

The audit checks package paths, archive contents, and reviewed artwork. Keep runtime license notices. Read [MODEL_LICENSE.md](../MODEL_LICENSE.md) before distribution; the source [MIT License](../LICENSE) does not license third-party character artwork, audio, or model weights.

## Release

The [Windows release workflow](../.github/workflows/release.yml) runs validation and builds the installer. Manual runs retain downloadable build artifacts. A `v<version>` tag must match `desktop/package.json`. After successful validation, a tagged run preserves any existing Release and its assets; only when no Release exists for that tag does it create a **draft** with the EXE and `SHA256SUMS.txt`. Publishing a new draft is a separate maintainer action. A local build does not upload anything.

The 0.1.0 installer is unsigned. Verify the release checksum and test installation, upgrade, and uninstall on the intended Windows environment before broader distribution.

## Architecture references

The renderer uses the sandboxed preload contract; Electron Main owns native permissions, processes, persistence, and the Python connection. Model, voice, image, and Work execution share managed cancellation and resource ownership.

[Source guide](../SOURCE_FILE_GUIDE.md) · [Desktop protocol](../desktop_protocol/README.md) · [IPC contracts](./electron/contracts.ts) · [Architecture decision](../docs/decisions/0001-desktop-shell.md) · [Documentation index](../docs/README.md)
