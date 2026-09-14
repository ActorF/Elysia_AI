# Elysia Desktop

The Stage 6 desktop foundation connects a React + TypeScript interface to the
existing Python Brain through an Electron-owned child process and a strict,
versioned local protocol. The first Stage 7 Voice slice adds host-local
microphone and speaker selection, native permission status, and bounded input
and output tests without retaining audio. The bounded capture slice adds
explicit one-utterance recording: the renderer downmixes and resamples input to
16 kHz mono `s16le`, and local VAD submits only valid speech transiently for
Python contract validation. The renderer also has persistent Chat and Project
surfaces, resilient streamed message actions, revisioned Settings, Chat
attachments, Project source storage, semantic design tokens, system/light/dark
themes, keyboard and screen-reader navigation, durable per-Chat drafts,
renderer-refresh stream recovery, and consistent loading, empty, error,
offline, and fatal states.
Electron is frozen as the production
shell. The Tauri source and toolchain were removed after the comparison; the
rationale, recorded measurements, and revisit gates are in
[`docs/decisions/0001-desktop-shell.md`](../docs/decisions/0001-desktop-shell.md).

## Development

Prerequisites:

- The repository Python virtual environment exists at `.venv`.
- Ollama is running and the model configured in the root `.env` is installed.
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

## Interface

- Open **Settings** or press `Ctrl+,` to manage the default Ollama model and
  origin, Memory limits, file import size, and appearance. Backend values are
  atomically stored in `workspace/settings/global.json`; appearance remains in
  this device's renderer storage and applies immediately.
- The Voice section selects a system-default or exact microphone and speaker,
  reports Windows microphone access, and runs short local tests. Desired opaque
  device IDs are stored separately in `workspace/settings/audio-device.json`;
  device labels, permission state, availability, and test audio never enter
  Python.
- In a Chat, **Start voice** and the phone button open the Voice capture page
  without requesting microphone access. Only **Start microphone** begins one
  bounded capture. The renderer downmixes and resamples input, and local VAD
  waits for valid speech before sending temporary 16 kHz mono `s16le` PCM to
  Python. A successful validation shows **Speech captured** and **Validated
  locally** metadata; it does not add a message to Chat history.
- Settings shows Global defaults beside the active Project's inheritance and
  the active Chat's pinned model. Backend-backed changes clearly request a
  restart before they are reported as active.
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
  assignment, archive, and restore. Speech recognition, speech output, Work
  permissions, and later file processing controls remain read-only until their
  service boundaries exist. The bounded Voice capture does not yet perform
  speech recognition or create a Chat Turn.

## Manual Voice capture smoke test

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

Open a Chat, choose **Start voice**, then choose **Start microphone**. Speak and
pause for about 0.6 seconds. Confirm that the page reports **Speech captured**
and **Validated locally**, shows only accepted metadata, and has not added a
Chat-history message. Repeat once while staying silent for about 10 seconds,
and once using **Cancel capture**; neither path should add a message. Stop the
test immediately if Windows reports that microphone access is denied.

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
- Settings accepts an exact non-sensitive allowlist, uses optimistic revisions
  and atomic replacement, and remains repairable after Backend initialization
  rejects a saved model or Ollama origin.
- Audio-device preferences use an independent optimistic revision and remain
  repairable while Chat generation is active or Brain initialization has
  failed. Electron owns hardware enumeration, Windows permission state, and
  immediate resource cleanup when a test, capture, or visible context ends.
  Microphone and speaker-selection permissions are limited to the trusted main
  renderer. Opening the Voice page does not request microphone access; capture
  begins only from the user's explicit control. Bounded VAD discards silence
  and short input, and accepted PCM exists only until Python returns safe
  metadata, duration, and SHA-256. Neither process persists it or creates a Chat
  Turn in this slice.
- Native selection and drop paths remain inside the trusted preload/Electron
  boundary. Python copies validated regular files into opaque, scope-specific
  storage, and protocol responses expose only safe metadata and attachment IDs.
