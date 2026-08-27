# ADR 0001: Freeze Electron as the desktop shell

- Status: Accepted
- Decision date: 2026-08-26
- Scope: Windows desktop shell beginning with Stage 6 Module 4

## Decision

Elysia will continue with Electron as its production desktop shell. The Tauri
prototype and its Rust/Node toolchain are removed after this decision rather
than retained as a second client. Product work extends only the Electron shell;
this record keeps the comparison evidence and formal revisit gates.

The React renderer and the versioned Python protocol remain shell-neutral so a
future comparison does not require rewriting product or Backend semantics.

## Context

The existing Electron shell already owned the real Python process, an
authenticated JSONL protocol, strict frame validation, renderer source checks,
file and microphone boundaries, tray behavior, graceful shutdown, and Windows
packaging. A Tauri 2 prototype was built against the same renderer, Python
entry point, virtual environment, protocol fixtures, and checkout.

The comparison gate favored keeping Electron unless Tauri demonstrated a clear
product benefit and a controlled migration cost. Marketing claims or framework
binary size alone were not sufficient evidence.

## Compared implementations

- Electron 43.4.1 with its sandboxed preload and main-process owner.
- Tauri CLI 2.11.4, Rust crate 2.11.5, and WebView2 151.0.4129.107.
- The same release React/Vite assets and the same real
  `.venv\Scripts\python.exe desktop_backend.py` Backend.
- Both installers are shell-only comparison artifacts. Neither contains the
  Python runtime, Ollama, model weights, or the Stage 14 production data layout.

The evaluated Tauri prototype had a separate product name and identifier. Its
release runtime required `ELYSIA_PROJECT_ROOT` to point to a development
checkout; its source-tree fallback was not portable packaging.

## Measurement environment and method

- Microsoft Windows 11 Pro 10.0.26200.
- Intel Core i9-12900K, 24 logical processors, 31.7 GiB visible RAM.
- Node 24.19.0, npm 11.17.0, rustc/cargo 1.98.0.
- One workstation, release builds, ten runs per shell on 2026-08-26.
- Each shell received its own initially empty temporary profile. Run 1 created
  that profile; runs 2–10 reused it and form the warm sample.
- Both shells started hidden and explicitly showed the window only after React
  committed the application tree and sent the same `rendererReady` signal.
  Startup ended when that native main window was visible and not minimized.
  The process tree then idled for three seconds before memory was sampled.
- Shell processes and the Python process tree were measured separately.
- A standard Win32 `WM_CLOSE` was posted to the measured native window. The
  root shell had five seconds to exit without harness termination; tracked
  descendants then had up to three seconds to exit before survivors were
  reported as orphans.
- Warm p50 and p90 use R-7 linear interpolation over runs 2–10.

This is visible-window startup, not Backend-ready time and not time-to-first
Chat. Backend initialization overlaps renderer startup and is scheduled through
different native adapters, so `rendererReady` deliberately does not claim a
shared Backend lifecycle milestone. Run 1 is an isolated-profile initialization
observation, not a controlled cold boot. The historical close result proves
only that the harness did not force the root and found no tracked survivor; it
did not record the root exit code or observe the internal Backend shutdown
response. Working Set sums process values and can count shared pages more than
once.

The selected Electron benchmark remains reproducible with PowerShell 7.4 or
newer from `desktop`:

```powershell
npm run make

pwsh -NoProfile -File .\benchmarks\measure-shell.ps1 `
  -Executable .\out\win-unpacked\Elysia.exe -Runs 10
```

The Tauri samples below are the recorded comparison snapshot. Its source and
toolchain were deliberately removed after acceptance, so the cross-shell run is
not reproducible from the current tree.

## Results

| Metric | Electron | Tauri prototype | Interpretation |
| --- | ---: | ---: | --- |
| Run 1 visible window | 333.87 ms | 442.23 ms | Profile-initialization observation only |
| Warm visible-window startup p50 | 206.25 ms | 139.30 ms | Tauri 32.5% faster |
| Warm visible-window startup p90 | 217.08 ms | 147.38 ms | Tauri 32.1% faster |
| Warm Shell Working Set p50 | 316.23 MiB | 380.87 MiB | Tauri 20.4% higher |
| Warm Shell Working Set p90 | 319.16 MiB | 383.33 MiB | Tauri 20.1% higher |
| Warm Shell Private p50 | 250.17 MiB | 269.66 MiB | Tauri 7.8% higher |
| Warm Shell Private p90 | 251.90 MiB | 270.45 MiB | Tauri 7.4% higher |
| Warm Backend Working Set p50 | 91.47 MiB | 90.18 MiB | Similar Python cost |
| Shell process count | 4 | 7 | Measured after the idle window |
| Harness-unforced root exits | 10/10 | 10/10 | Exit code was not recorded; not a Backend shutdown-response assertion |
| Orphan-free exits | 10/10 | 10/10 | No tracked descendant survived |
| Unsigned NSIS shell | 104,218,366 bytes (99.39 MiB) | 2,043,923 bytes (1.95 MiB) | Tauri 98.0% smaller, but uses an online WebView2 bootstrap |

Electron's unpacked directory was 382,187,273 bytes (364.48 MiB). The Tauri
release executable was 9,253,888 bytes (8.83 MiB). Those two forms are included
only as supporting observations and are not directly equivalent artifacts.

Warm startup samples in run order were:

- Electron: 227.18, 200.94, 212.51, 205.62, 206.61, 199.11, 201.00,
  214.56, and 206.25 ms.
- Tauri: 145.65, 139.30, 138.97, 137.73, 141.35, 138.59, 154.32,
  143.23, and 138.31 ms.

## Selected Electron verification after cleanup

After removing the rejected implementation and rebuilding from the Electron-
only dependency tree, a fresh ten-run check produced a 329.00 ms profile-
initialization observation, 199.91 ms warm p50, and 201.52 ms warm p90. Warm
Shell Working Set was 317.34 MiB p50 / 320.08 MiB p90; Shell Private was
250.77 MiB p50 / 256.08 MiB p90; Backend Working Set p50 was 91.41 MiB.

All 10 roots handled `WM_CLOSE`, exited without harness termination with exit
code 0, and left no tracked survivor. The Electron-only unsigned installer was
104,214,693 bytes (99.39 MiB), and its unpacked directory was 382,171,086 bytes
(364.47 MiB). These values validate the selected final tree; they are not mixed
into the historical cross-shell ratios above.

## Capability evidence

| Capability | Electron baseline | Tauri prototype |
| --- | --- | --- |
| Real Python lifecycle | Exercised by release benchmark; no Backend-ready gate | Exercised by release benchmark; no Backend-ready gate |
| Authenticated protocol | Implemented and contract-tested against the real Electron manager | Implemented; fake-transport adapter tests cover handshake and restart epoch, not end-to-end readiness |
| Restart isolation | Generation-bound process manager | Epoch-bound events; automated stale wire/exit test |
| Close path and orphan check | WM_CLOSE produced 10/10 harness-unforced exits and no tracked survivor | WM_CLOSE produced 10/10 harness-unforced exits and no tracked survivor |
| File picker returning name and size only | Implemented; outside this benchmark | Implemented; outside this benchmark |
| Tray show/quit behavior | Implemented; outside this benchmark | Implemented; outside this benchmark |
| Microphone boundary | Manually verified; audio-only, trusted-origin, main-frame policy | No equivalent high-level origin/frame policy validated |
| Transparent window | Not configured or validated | Not configured or validated |
| Real Live2D/WebGL content | Absent | Absent |
| Installed Python sidecar | Absent until Stage 14 | Absent; installer is not portable |
| Renderer/native security parity | Source, sender, preload, CSP, and protocol tests | CSP and capability scope implemented; parity not established |

Tauri officially supports Windows sidecars, tray icons, dialogs, capabilities,
and multiple WebView2 installer modes. Live2D's Web SDK is WebGL-based, so the
system WebView is a plausible host. These facts establish feasibility, not
Elysia-specific parity. Tauri's high-level window API did not provide the same
tested microphone permission boundary as the existing Electron session policy;
closing that gap would require lower-level WebView integration and additional
version-sensitive security work.

## Rationale

Tauri produced a much faster visible window and a dramatically smaller shell
installer on this machine. Those are real advantages. They do not satisfy the
migration gate on their own:

1. Tauri used more measured Shell memory, so it did not provide an across-the-
   board runtime improvement.
2. Its tiny installer relies on a system or downloaded WebView2 runtime, while
   Electron ships its Chromium runtime. Neither result includes the future
   Python/Ollama/model distribution cost.
3. Migrating would add Rust, MSVC, WebView2 behavior, another native lifecycle
   implementation, and another regression surface to an already validated
   Electron boundary.
4. Microphone policy parity, transparent rendering, real Live2D/WebGL, and a
   clean-machine installed Python sidecar remain unverified.
5. Maintaining both shells would slow the design and product work without
   providing a user-facing capability.

Electron therefore has the better risk-adjusted outcome for the current
product. Tauri is not rejected permanently; it simply did not clear the gate.

## Consequences

- Stage 6 Module 4 and later desktop work targets Electron only.
- The Tauri source, adapter, tests, package dependency, CI job, and build output
  are removed. Only the recorded decision evidence remains.
- Electron has a ten-second native renderer-ready fallback so preload, renderer,
  or bridge failure cannot leave the application permanently hidden.
- Shared renderer code and the Python protocol must not depend on Electron-only
  product semantics.
- Packaging remains a shell smoke artifact until the production Python runtime,
  signing, updater, data layout, and recovery plan are completed.

## Revisit gates

Re-open this decision only if Electron becomes a measured release, startup, or
memory constraint. A new Tauri proposal must first demonstrate all of the
following on the same machine and on a clean Windows VM:

- A portable installed Python sidecar and production data layout.
- Backend-ready and time-to-first-Chat measurements, with warm p50 at least 25%
  better than Electron.
- Shell memory no more than 5% above Electron under the same real workload.
- An audio-only, trusted-origin, main-frame microphone policy.
- File picker, tray, transparent window, and real Live2D/WebGL validation.
- Restart and close behavior with no orphan process, including epoch tests.
- Signing, offline/online WebView2 policy, updater, and antivirus checks.
- A bounded migration estimate that fits one explicitly approved work unit.

## Primary references

- [Electron process model](https://www.electronjs.org/docs/latest/tutorial/process-model)
- [Electron security checklist](https://www.electronjs.org/docs/latest/tutorial/security)
- [Electron session permission handlers](https://www.electronjs.org/docs/latest/api/session#sessetpermissionrequesthandlerhandler)
- [Tauri Windows prerequisites](https://v2.tauri.app/start/prerequisites/)
- [Tauri WebView2 installer modes](https://v2.tauri.app/distribute/windows-installer/)
- [Tauri sidecars](https://v2.tauri.app/develop/sidecar/)
- [Tauri capabilities](https://v2.tauri.app/security/capabilities/)
- [Tauri permissions](https://v2.tauri.app/security/permissions/)
- [Tauri local build-tool cache](https://v2.tauri.app/reference/config/#uselocaltoolsdir)
- [Open Tauri permission-handler request](https://github.com/tauri-apps/tauri/issues/14753)
- [Live2D Cubism SDK for Web](https://docs.live2d.com/en/cubism-sdk-manual/cubism-sdk-for-web/)
