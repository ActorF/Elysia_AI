# Elysia Desktop

Stage 6 Modules 1–3 connect a React + TypeScript interface to the existing
Python Brain through an Electron-owned child process and a strict, versioned
local protocol. Electron is frozen as the production shell. The Tauri source
and toolchain were removed after the comparison; the rationale, recorded
measurements, and revisit gates are in
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

## Verification

```bat
npm run lint
npm run typecheck
npm test
npm run build
npm audit --audit-level=high
npm run package
```

`npm run package` creates an unpacked desktop build in `desktop\out`.
On Windows, `npm run make` additionally creates an unsigned NSIS installer.

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
