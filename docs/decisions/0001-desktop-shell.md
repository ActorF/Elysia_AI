# ADR 0001 · Electron desktop shell

Accepted on **2026-08-26** for Windows desktop work from Stage 6 Module 4.

## Decision

Keep Electron as the production shell and remove the Tauri prototype,
toolchain, tests, and build outputs. Product development targets one shell;
React and the versioned Python protocol retain shell-neutral semantics.

Electron already owned authenticated Python lifecycle, frame validation,
renderer/file/microphone boundaries, tray behavior, shutdown, and packaging.
A Tauri migration required a measurable product benefit and controlled cost.

## Comparison

The historical comparison used Electron 43.4.1 and Tauri CLI 2.11.4 /
Rust crate 2.11.5 / WebView2 151.0.4129.107, with identical release React/Vite
assets and the real `.venv\Scripts\python.exe desktop_backend.py` Backend.

Both installers were shell-only artifacts without Python, Ollama, model
weights, or production data layout. Tauri used a separate product identity
and required `ELYSIA_PROJECT_ROOT` pointing to a checkout; its source-tree
fallback was not portable packaging.

### Method

- Windows 11 Pro 10.0.26200; i9-12900K, 24 logical processors, 31.7 GiB RAM.
- Node 24.19.0, npm 11.17.0, rustc/cargo 1.98.0.
- Ten release runs per shell on one workstation, 2026-08-26.
- Each shell began with an empty temporary profile. Run 1 created it;
  runs 2–10 formed the warm sample. Percentiles use R-7 interpolation.
- Both windows started hidden and showed after React committed and sent
  `rendererReady`. Startup ended when the native window was visible and
  not minimized. Memory was sampled three idle seconds later.
- Shell and Python process trees were measured separately.
- `WM_CLOSE` allowed five seconds for unforced root exit and three more for
  tracked descendants before reporting orphans.

This measures **visible-window startup**, not Backend readiness or first Chat.
Backend initialization overlaps rendering, and the native adapters schedule
it differently. Run 1 is profile initialization, not a controlled cold boot.
Historical close measurements recorded neither root exit code nor the internal
Backend shutdown response. Summed Working Set may double-count shared pages.

### Results

| Metric | Electron | Tauri |
| --- | ---: | ---: |
| Run 1 visible window | 333.87 ms | 442.23 ms |
| Warm startup p50 / p90 | 206.25 / 217.08 ms | 139.30 / 147.38 ms |
| Warm Shell Working Set p50 / p90 | 316.23 / 319.16 MiB | 380.87 / 383.33 MiB |
| Warm Shell Private p50 / p90 | 250.17 / 251.90 MiB | 269.66 / 270.45 MiB |
| Warm Backend Working Set p50 | 91.47 MiB | 90.18 MiB |
| Shell process count | 4 | 7 |
| Harness-unforced root exits | 10/10 | 10/10 |
| Orphan-free exits | 10/10 | 10/10 |
| Unsigned NSIS shell | 104,218,366 bytes (99.39 MiB) | 2,043,923 bytes (1.95 MiB) |

Tauri improved warm startup p50/p90 by 32.5%/32.1%, but used
20.4%/20.1% more Working Set and 7.8%/7.4% more Private memory.
Its 98.0% smaller installer used an online WebView2 bootstrap; Electron ships
Chromium. Neither includes future Python/Ollama/model distribution costs.

Supporting, non-equivalent artifacts: Electron's unpacked directory was
382,187,273 bytes (364.48 MiB); Tauri's executable was 9,253,888 bytes (8.83 MiB).

Warm startup samples, in run order:

- Electron: 227.18, 200.94, 212.51, 205.62, 206.61, 199.11, 201.00,
  214.56, 206.25 ms.
- Tauri: 145.65, 139.30, 138.97, 137.73, 141.35, 138.59, 154.32,
  143.23, 138.31 ms.

### Electron after cleanup

A separate ten-run Electron-only check recorded:

| Metric | Result |
| --- | ---: |
| Profile initialization | 329.00 ms |
| Warm startup p50 / p90 | 199.91 / 201.52 ms |
| Shell Working Set p50 / p90 | 317.34 / 320.08 MiB |
| Shell Private p50 / p90 | 250.77 / 256.08 MiB |
| Backend Working Set p50 | 91.41 MiB |
| Unsigned installer | 104,214,693 bytes (99.39 MiB) |
| Unpacked directory | 382,171,086 bytes (364.47 MiB) |

All ten roots handled `WM_CLOSE`, exited unforced with code 0, and left no
tracked survivor. These checks validate the selected tree; they are not mixed
into the historical comparison ratios.

## Rationale and limits

Tauri's faster startup and smaller shell did not outweigh greater measured
memory use, another native lifecycle, Rust/MSVC/WebView2 maintenance, and
unproven security parity.

Both prototypes implemented file picking and tray behavior, outside the
benchmark. Electron had real-manager protocol contracts and manually verified
audio-only, trusted-origin, main-frame microphone policy. Tauri had CSP and
capability scoping plus fake-transport handshake/restart tests, but no equivalent
validated microphone boundary or end-to-end readiness evidence.

Neither comparison validated transparent windows, real Live2D/WebGL content,
or a clean-machine installed Python sidecar. Tauri's documented support for
sidecars, tray, dialogs, capabilities, and WebView2 shows feasibility, not
Elysia-specific parity. Matching Electron's microphone policy would require
lower-level, version-sensitive WebView integration.

Maintaining two shells would add regression work without a demonstrated
user-facing capability. Electron retains a ten-second renderer-ready fallback
so a preload/renderer/bridge failure cannot leave the window hidden forever.
At decision time, packaging remained a shell smoke artifact pending runtime,
signing, update, data-layout, and recovery work.

## Reproduce

The retained Electron benchmark requires PowerShell 7.4+, from `desktop`:

```powershell
npm run make
pwsh -NoProfile -File .\benchmarks\measure-shell.ps1 `
  -Executable .\out\win-unpacked\Elysia.exe -Runs 10
```

Tauri's figures are a historical snapshot; its removed source/toolchain cannot
be reproduced from the current tree.

## Revisit gates

Reopen only if Electron becomes a measured release, startup, or memory
constraint. A replacement proposal must demonstrate all of these on the same
machine and a clean Windows VM:

- Portable installed Python and production data layout.
- Backend-ready and first-Chat measurements; warm p50 at least 25% better.
- Shell memory no more than 5% above Electron under the same real workload.
- Audio-only, trusted-origin, main-frame microphone policy.
- File picker, tray, transparent-window, and real Live2D/WebGL validation.
- Epoch-safe restart and close behavior without orphan processes.
- Signing, offline/online WebView2 policy, updater, and antivirus checks.
- A migration estimate fitting one explicitly approved work unit.

## References

- Electron: [process model](https://www.electronjs.org/docs/latest/tutorial/process-model),
  [security](https://www.electronjs.org/docs/latest/tutorial/security),
  [permission handlers](https://www.electronjs.org/docs/latest/api/session#sessetpermissionrequesthandlerhandler).
- Tauri: [Windows prerequisites](https://v2.tauri.app/start/prerequisites/),
  [WebView2 installers](https://v2.tauri.app/distribute/windows-installer/),
  [sidecars](https://v2.tauri.app/develop/sidecar/),
  [capabilities](https://v2.tauri.app/security/capabilities/),
  [permissions](https://v2.tauri.app/security/permissions/),
  [local tool cache](https://v2.tauri.app/reference/config/#uselocaltoolsdir),
  [permission-handler request](https://github.com/tauri-apps/tauri/issues/14753).
- [Live2D Cubism SDK for Web](https://docs.live2d.com/en/cubism-sdk-manual/cubism-sdk-for-web/).
