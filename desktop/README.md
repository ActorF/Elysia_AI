# Elysia Desktop

Stage 6 Modules 1–4 connect a React + TypeScript interface to the existing
Python Brain through an Electron-owned child process and a strict, versioned
local protocol. The renderer now has a responsive application shell, semantic
design tokens, system/light/dark themes, keyboard navigation, and consistent
loading, empty, error, and fatal states. Electron is frozen as the production
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

- Open **Settings** or press `Ctrl+,` to choose System, Light, or Dark. The
  preference is stored only in the renderer's local application storage.
- Press `Ctrl+K` to open Chat search, `Escape` to close the current surface,
  and `Ctrl+B` to show or hide navigation.
- Enter sends a message; Shift+Enter inserts a new line. IME composition is
  never treated as a send action.
- Navigation becomes a modal drawer at narrow CSS widths, including high
  Windows display or Electron zoom levels. The Composer remains in normal
  layout flow so attachments, alerts, and multiline input cannot cover the
  final message.
- Projects and Memory currently show explicit empty states. Their real actions
  are added by the following product slices instead of being simulated in the
  renderer.

## Verification

```bat
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
- Each Python process must complete a version and capability handshake using a
  fresh local session token before Electron marks it connected.
- Python and TypeScript validate the same samples in
  `desktop_protocol/fixtures/v1.samples.json`.
- Python delegates persistence and streaming to the existing Stage 5 Brain.
