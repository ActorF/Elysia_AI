# External Desktop Pet

Elysia's in-app character is static. The optional animated Desktop Pet launches
an independently owned, reviewed Bongo Cat Mver program. The historical
filename is retained for existing links.

## Use and modes

In Settings, choose a local companion-program collection and select a
validated program. The default is `disabled`.

| Mode | Behavior |
| --- | --- |
| `disabled` | Capability off; no external program runs |
| `hidden` | Retain folder and selection, stop the owned program |
| `visible` | Revalidate and launch the selected program |

The tray offers Launch, Stop, Retry or Disable according to state. Without a
trusted selection it directs the user to Settings. Hidden or visible intent
keeps Elysia in the tray after the main window closes; disabling restores the
normal last-window-close behavior. Pet failures leave Chat, Voice, Knowledge
and static artwork available.

The expanded main-window character panel selects fixed, reviewed cells from
`elysia-ui-illustrations.png` by application state. Voice Emotion remains
metadata there; it does not replace the illustration sheet. The independent
Voice Call window uses the fixed, dimmed `voice-call-background.webp` image.
Neither surface renders a dynamic model, and model output cannot choose files,
atlas cells, expressions or animation parameters.

## Ownership

| Component | Responsibility |
| --- | --- |
| `desktop/public/character/` | Reviewed static illustrations, atlases and fallback portrait |
| `desktop/public/voice/voice-call-background.webp` | Fixed artwork for the independent Voice Call window |
| `desktop-pet-program-library.ts` | Bounded discovery, structural validation and pinned hashes |
| `desktop-pet-program-manager.ts` | One owned child, switching and exact PID-tree termination |
| `desktop-pet-window-controller.ts` | Hidden startup, readiness and initial window placement |
| `desktop-pet-preferences.ts` | Private folder, selection and atomic preferences |
| Electron Main | Native picker, serialized reconciliation, tray and shutdown |

The TypeScript modules above are in `desktop/electron/`. Public state contains
safe labels, stable IDs, status and fixed warnings; native paths and PIDs stay
in Main. Elysia has no embedded model loader, Pet HTML/Preload entry, custom
asset protocol or dynamic-character rendering runtime.

The external program owns its keyboard/mouse/gamepad behavior, tracking,
expressions, rendering, audio, tray and UI. Its `config.json` stays beside the
program. Elysia reads only the fields needed for validation and never rewrites,
copies or migrates that configuration. Each appearance therefore keeps its
original settings. Elysia stores only mode, collection path and program ID.

## Validation and launch

Choosing a directory does not authorize every executable inside it. Main
bounds scan depth, entries, candidates, JSON and referenced-file sizes. It
rejects links, junction/path escapes, absolute or parent-relative model
references, URLs, control characters and ambiguous identities.

A candidate needs complete `standard`, `keyboard` and `gamepad` model profiles,
with all referenced files inside both the candidate and collection roots. Its
bounded `config.json` needs `decoration`, `standard`, `keyboard`, `gamepad`,
`network` and `workarea` sections and a supported input mode.

The launcher, `BongoCatUI.exe`, `BongoCatMverUI.dll`, admitted dependency DLLs
and required resources must match pinned lengths and SHA-256 identities.
Unreviewed top-level EXEs/DLLs fail validation. Hash reads also check metadata
again to detect files changing during the read. Selection IDs combine the
normalized relative directory and pinned launcher identity; mutable settings
do not change the ID. Program count comes from the validated scan.

Before every launch, Main resolves the same canonical collection, program and
launcher, confirms the descriptor/ID, and repeats structure and hash checks.
Observed replacement or disappearance fails with a rescan warning. Launch is
still path-based: a local actor who can modify the collection may replace a
path between verification and Windows loading it. Revalidation narrows this
TOCTOU interval; it does not eliminate it.

## Native window and process lifecycle

Launch uses no arguments or shell, the program directory as `cwd`, ignored
standard streams, and `detached: false`, `windowsHide: true`. The Windows
helper is ready first and keeps the original 1500-by-1500 loading window off
screen until a normal character frame is ready.

The pinned program ignores a simple hidden-start request and exposes no
loading-complete property. The helper therefore reads its initialization guard
and rendering clocks, accounting for the loaded image base. It rehashes the
relevant executable/SFML binaries before reading those addresses and never
writes process memory. After resizing, two normal presentations—or two normal
event-loop clock advances for uncapped rendering—confirm readiness before
showing without activation. New binary hashes require revalidating this
contract. Failure never exposes the unprepared oversized window.

Initial placement is beside Elysia's lower-right corner, clamped to the current
monitor's work area; tray-only launch uses the display corner. DIP-to-physical
pixel conversion handles mixed DPI and negative coordinates. This is one-time
placement: the user can move and resize the pet afterward.

The reviewed launcher requests administrator rights, so Main applies
`RunAsInvoker` to that child only. It remains under the interactive user token;
Windows may prevent it from observing input in elevated applications.

`DesktopPetProgramManager` serializes transitions and owns at most the exact
child returned by its launch call. It never adopts manually started copies.
Switching stops and awaits A before launching B. Windows termination uses only
`taskkill.exe /PID <owned-root-pid> /T`, with deadlines; it never matches process
names. If termination cannot be confirmed, ownership remains, state becomes
failed and B is not launched.

Callbacks keep child object identity so old exit/error events cannot overwrite
a replacement. Unexpected exits become `failed`; expected stops become
`absent`. Window preparation is bound to the selected child's SFML window and
is cancelled on exit or shutdown. Application shutdown rejects new launches
and attempts a bounded stop, so optional cleanup cannot block exit forever.
Settings, startup/rescan, tray and selection changes share one mutation queue;
the old program stops before a new selection label is published.

## Preferences and migration

Preferences under Electron `userData` are strict, bounded, revisioned and
atomically replaced. Invalid data defaults to `disabled`.

- Version 1 belonged to the removed embedded model window. Migration disables
  the Pet, clears placement and requires a new local program selection.
- Version 2 already describes an external program. Migration keeps valid mode,
  path and ID, discards obsolete placement, then rescans and revalidates before
  launch. Stored preferences are not execution authority.

## Assets and distribution

The owner's purchased collection is attributed to `@书呆儿` and remains in the
ignored local directory `data/characters/爱莉希雅原版猫猫版总合集_34be1/`.
Installed builds use the folder picker and never probe that checkout path.
The recorded [free-version page](https://pan.quark.cn/s/cb5d84acad8e) is
provenance context only: its contents, terms and equivalence to the reviewed
collection have not been verified.

Paid programs, DLLs, models, textures, motions, expressions, physics,
configurations and archives stay local. They must not enter Git, releases,
installers, fixtures, screenshots or shared diagnostics. The full rights record
is [MODEL_LICENSE.md](../MODEL_LICENSE.md).

`scripts/check_distribution_assets.py` audits the Git index, Builder
configuration, unpacked package, ASAR listing and extracted ASAR tree. Only
reviewed static art and ordinary application code may ship. This gate reduces
accidental distribution; it does not grant rights.

## Verification

```text
python scripts/check_python_documentation.py
python scripts/check_distribution_assets.py
python -m pytest
cd desktop
npm run docs:check
npm run lint
npm run typecheck
npm test
npm run make
```

Package CI also captures and extracts the complete ASAR for distribution audit.
