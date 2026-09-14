# Elysia Desktop

The Stage 6 desktop foundation connects a React + TypeScript interface to the
existing Python Brain through an Electron-owned child process and a strict,
versioned local protocol. The first Stage 7 Voice slice adds host-local
microphone and speaker selection, native permission status, and bounded input
and output tests without retaining audio. The bounded capture slice adds
explicit one-utterance recording: the renderer downmixes and resamples input to
16 kHz mono `s16le`, and local VAD submits only valid speech transiently for
local Faster-Whisper transcription. A bounded final transcript returns through
Electron to an editable review surface; the user must explicitly place it in
the current Chat composer, where it replaces an empty draft or is appended
after existing draft text. The renderer also has persistent Chat and Project
surfaces, resilient streamed message actions, revisioned Settings, Chat
attachments, Project source storage, semantic design tokens, system/light/dark
themes, keyboard and screen-reader navigation, durable per-Chat drafts,
renderer-refresh stream recovery, and consistent loading, empty, error,
offline, and fatal states. This Voice slice exposes final text only; real-time
partial transcripts and continuous conversation remain future work. Separately,
Python now has one-shot TTS contracts, a strict ignored local Voice Profile
catalog, readiness reporting, and a loopback-only GPT-SoVITS adapter with
repeated/multi-emotion smoke coverage. That foundation is Python/CLI-only:
Desktop Protocol, Electron/React transport, playback, and continuous speech are
not connected.
Electron is frozen as the production
shell. The Tauri source and toolchain were removed after the comparison; the
rationale, recorded measurements, and revisit gates are in
[`docs/decisions/0001-desktop-shell.md`](../docs/decisions/0001-desktop-shell.md).

## Development

Prerequisites:

- The repository Python virtual environment exists at `.venv`.
- Ollama is running and the model configured in the root `.env` is installed.
- Local STT additionally requires `requirements-stt.txt` and a complete model
  directory at `models/weights/faster-whisper/<model>`; neither is installed or
  downloaded automatically.
- Optional Python-only synthesis requires a separately installed GPT-SoVITS
  runtime, checkpoints/reference audio under the ignored
  `models/weights/gpt-sovits/` tree, and an ignored
  `workspace/settings/voice-profiles.json` catalog. None is downloaded,
  committed, or packaged by this project, and none is required to run the
  current desktop UI.
- Run all npm commands from the `desktop` directory.

Start Vite in the first terminal:

```bat
cd /d D:\Elysia_AI\desktop
npm run dev
```

Start Electron in the second terminal:

```bat
cd /d D:\Elysia_AI\desktop
npm run electron:dev
```

Electron starts `D:\Elysia_AI\.venv\Scripts\python.exe`, runs
`desktop_backend.py`, and stops that child process when the app quits.

To enable the default `small` speech model on CPU, install the optional runtime
from the repository root and place the model before starting Electron:

```bat
cd /d D:\Elysia_AI
.venv\Scripts\python.exe -m pip install -r requirements-stt.txt
```

The model must be a complete local Faster-Whisper directory at
`models\weights\faster-whisper\small\`. Other selectable directories are
`tiny`, `base`, `medium`, `large-v3`, and `turbo`. These weight directories are
Git-ignored and must not be committed or packaged with the application.

## Interface

- Open **Settings** or press `Ctrl+,` to manage the default Ollama model and
  origin, Memory limits, file import size, local STT model/device/language, and
  appearance. Backend values are atomically stored in
  `workspace/settings/global.json` and apply after a Backend restart;
  appearance remains in this device's renderer storage and applies immediately.
- The Voice section selects a system-default or exact microphone and speaker,
  reports Windows microphone access, and runs short local tests. Desired opaque
  device IDs are stored separately in `workspace/settings/audio-device.json`;
  device labels, permission state, availability, and test audio never enter
  Python.
- In a Chat, **Start voice** and the phone button open the Voice capture page
  without requesting microphone access. Only **Start microphone** begins one
  bounded capture. The renderer downmixes and resamples input, and local VAD
  waits for valid speech before sending temporary 16 kHz mono `s16le` PCM to
  Python once. A successful recognition displays an editable **Final
  transcript**. **Use transcript in message** places it in an empty composer;
  **Append transcript to message** preserves existing draft text first. Neither
  action sends the message or adds a Chat-history Turn automatically.
- Settings shows Global defaults beside the active Project's inheritance and
  the active Chat's pinned model. Speech recognition selects
  `tiny` / `base` / `small` / `medium` / `large-v3` / `turbo`,
  `auto` / `cuda` / `cpu`, and `auto` / `zh` / `en`. Backend-backed changes
  clearly request a restart before they are reported as active.
- Voice and Settings translate sanitized readiness enums into recovery actions.
  Missing optional dependencies or a model, unavailable CUDA, and initialization
  failures never expose model paths, native exception text, or library details
  to React. `auto` may fall back to CPU; a real CPU transcription smoke path has
  passed, while this guide makes no claim of successful real-GPU validation.
- Press `Ctrl+K` to open Chat search, `Escape` to close the current surface,
  and `Ctrl+B` to show or hide navigation.
- Enter sends a message; Shift+Enter inserts a new line. IME composition is
  never treated as a send action.
- Unsent Chat text is stored per Chat on this device. Refreshing or reopening
  the renderer restores that draft, while a renderer refresh during generation
  reconnects to the request still owned by Electron.
- Use the paperclip or drag and drop to stage files for the exact active Chat,
  or add local files to a Project's Sources surface. Selection cancellation is
  a no-op, failed sends keep the Chat draft, and removing a file never affects
  another Chat or Project. Files are stored locally but are not parsed or
  indexed yet.
- Navigation becomes a modal drawer at narrow CSS widths, including high
  Windows display or Electron zoom levels. The Composer remains in normal
  layout flow so attachments, alerts, and multiline input cannot cover the
  final message. The compact character panel is also modal, traps focus, and
  has its own close control.
- Projects support persisted metadata, instructions, workspace binding, Chat
  assignment, archive, and restore. Although Python/CLI one-shot synthesis is
  available, desktop speech output, continuous Voice, Work permissions, and
  later file-processing controls remain unavailable.

## Manual local transcription smoke test

Use two Command Prompt windows, not PowerShell. Start Vite in the first:

```bat
cd /d D:\Elysia_AI\desktop
npm run dev
```

Start Electron in the second:

```bat
cd /d D:\Elysia_AI\desktop
npm run electron:dev
```

Install `requirements-stt.txt`, place a complete model in the matching
`models\weights\faster-whisper\<model>\` directory, then select that model and
**CPU only** under **Settings → Speech recognition**. Save and restart the
Backend. Open a Chat, enter a short draft if you want to exercise append, choose
**Start voice**, and then choose **Start microphone**. Speak and pause for about
0.6 seconds.

Confirm that an editable **Final transcript** appears. Change its text, choose
**Use transcript in message** or **Append transcript to message**, and confirm
that the Composer contains the edited text without sending it. Repeat once
while silent for about 10 seconds, once with **Cancel capture**, and once with
**Cancel transcription**; none may add a Chat-history Turn. The page shows only
generic progress, not partial recognized text. Stop immediately if Windows
reports that microphone access is denied.

## Manual local synthesis smoke test

This test exercises only the Python synthesis boundary; it does not make the
desktop speak. Prepare a GPT-SoVITS runtime and assets that you have the right
to use, copy `config\voice_profiles.example.json` to the ignored
`workspace\settings\voice-profiles.json`, and replace the example values with
accurate local relative paths, reference text, and language. The adapter
accepts only loopback HTTP endpoints and resolves model/reference assets only
under `models\weights\gpt-sovits\`.

If the Profile is marked `local-evaluation-only`, leave
`GPT_SOVITS_ALLOW_LOCAL_EVALUATION=False` until you have explicitly confirmed
its rights status and paths; then opt in locally without committing `.env`.
After starting the configured runtime, run from CMD:

```bat
cd /d D:\Elysia_AI
.venv\Scripts\python.exe scripts\smoke_gpt_sovits.py --profile default --emotion neutral --emotion happy --emotion sad
```

The command synthesizes one fixed Chinese sentence twice per emotion and keeps
the audio in memory. Success output contains only readiness, format, byte
count, duration, and SHA-256 metadata. `service_binding_unverified` means the
loopback API is reachable but cannot attest that the catalog-declared weights
are loaded; it must not be read as model-identity verification. Stopping the
runtime must produce the stable `service_unreachable` error. See the root
[`README.en.md`](../README.en.md) for the current acceptance-runtime CMD example
and the full configuration boundary.

## Verification

```bat
npm run docs:check
npm run lint
npm run typecheck
npm test
npm run test:ui
npm run build
npm audit --audit-level=high
npm run package
```

`npm run package` creates an unpacked desktop build in `desktop\out`.
On Windows, `npm run make` additionally creates an unsigned NSIS installer.
Neither output contains the GPT-SoVITS runtime, Voice Profile catalog, model
weights, or reference audio.

The application PNG and Windows ICO are derived from the official *Honkai
Impact 3rd* Elysia signet at the project owner's express direction for this
unofficial, non-commercial fan project. They are third-party assets, are not
covered by any source-code license, and do not imply HoYoverse / miHoYo
endorsement. See the root `MODEL_LICENSE.md` before publishing a build.

`npm run docs:check` enforces file-purpose comments plus public class,
function, class-method, and exported interface-method documentation. The
semantic why/how requirements remain part of review under the root
`AGENTS.md` policy.

`npm test` runs both the shared protocol contract suite and Electron renderer
UI tests. `npm run test:ui` can be used independently while working on layout.
The UI suite loads the production renderer through a dedicated sandboxed test
preload; its mock Backend and control surface are never included by the
production preload or packaged application.

The Stage 6 installer remains a shell smoke test. Stage 14 will freeze and bundle
the Python runtime and define the production data layout. Until then, a
packaged shell can be pointed at a development checkout with
`ELYSIA_PROJECT_ROOT` and `ELYSIA_PYTHON`.

## Electron performance benchmark

The retained benchmark requires PowerShell 7.4 or newer. It measures the
renderer-ready-gated, visible, non-minimized Electron window, followed by three
idle seconds and an unforced zero-code exit plus orphan check:

```powershell
pwsh -NoProfile -File .\benchmarks\measure-shell.ps1 `
  -Executable .\out\win-unpacked\Elysia.exe -Runs 10
```

This startup measurement is not Backend-ready time and does not prove that the
first Chat request can be sent. The accepted decision records the complete
method, results, capability gaps, and limitations.

## Security boundary

- React cannot access Node.js, Python, Chat files, or Memory files directly.
- The sandboxed preload exposes only the methods in `electron/contracts.ts`.
- Electron validates the exact renderer origin and top frame before handling
  any desktop IPC.
- Electron admits only one application instance, and Python holds an exclusive
  attachment-store lock, so another Backend cannot release or reuse in-flight
  claims.
- Each Python process must complete a version and capability handshake using a
  fresh local session token before Electron marks it connected.
- Python and TypeScript validate the same samples in
  `desktop_protocol/fixtures/v1.samples.json`.
- Python delegates persistence and streaming to the existing Stage 5 Brain.
- The Python GPT-SoVITS adapter is deliberately outside the desktop protocol:
  it permits only loopback HTTP, ignores environment proxies, rejects
  redirects, uses bounded requests/responses, and reports sanitized readiness.
  Its ignored catalog maps logical Profile/emotion identifiers to local assets;
  private paths, prompts, weights, reference audio, and synthesized bytes do not
  currently cross Electron or React.
- Settings accepts an exact non-sensitive allowlist, including the closed STT
  model/device/language enums, uses optimistic revisions
  and atomic replacement, and remains repairable after Backend initialization
  rejects a saved model or Ollama origin.
- Audio-device preferences use an independent optimistic revision and remain
  repairable while Chat generation is active or Brain initialization has
  failed. Electron owns hardware enumeration, Windows permission state, and
  immediate resource cleanup when a test, capture, or visible context ends.
  Microphone and speaker-selection permissions are limited to the trusted main
  renderer. Opening the Voice page does not request microphone access; capture
  begins only from the user's explicit control. Bounded VAD discards silence
  and short input, and accepted PCM exists only during the correlated local
  transcription request. The final result contains bounded text and safe
  language metadata, never PCM, model paths, or native diagnostics. Neither
  process persists audio or creates a Chat Turn automatically.
- Native selection and drop paths remain inside the trusted preload/Electron
  boundary. Python copies validated regular files into opaque, scope-specific
  storage, and protocol responses expose only safe metadata and attachment IDs.
