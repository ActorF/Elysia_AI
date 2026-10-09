# External Desktop Pet Program Integration

> Scope: the permanently static in-app character, opt-in discovery and launch
> of local Bongo Cat Mver companion programs, process ownership, failure
> recovery, and paid-asset distribution boundaries.

The filename is retained so existing documentation links remain valid. Elysia
no longer embeds or renders a dynamic desktop-pet model.

## Delivered behavior

The character panels inside Main Chat and Voice are always static. Character
state and the user-selected ten-value Voice Emotion choose only explicitly
reviewed PNG atlas cells. The closed values are `neutral`, `happy`, `sad`,
`caring`, `moved`, `playful`, `affectionate`, `teasing`, `serious`, and
`surprised`. There is no Animated/Still switch for those panels, and model
output cannot select files, atlas cells, expressions, or animation parameters.

The optional animated Desktop Pet is a separate external-program integration.
It is `disabled` by default. After the user chooses a local program-collection
folder, Settings lists only programs that Electron Main validates against the
reviewed Bongo Cat Mver build. `visible` starts only the selected program;
`hidden` preserves the folder and selection without running it; `disabled`
turns the capability off. The original program—not an Elysia rendering
surface—provides the animated character and owns its interaction behavior.

## Architecture split

| Component | Responsibility |
| --- | --- |
| `desktop/public/character/elysia-state-atlas.png` | Static closed-state artwork for the in-app character panel |
| `desktop/public/character/elysia-expression-atlas.png` | Static user-selected expression artwork |
| `desktop/public/character/elysia-portrait.png` | Reviewed static fallback artwork |
| `desktop/electron/desktop-pet-program-library.ts` | Main-private bounded discovery, structural validation, pinned hashes, and launch-time revalidation |
| `desktop/electron/desktop-pet-program-manager.ts` | Single-child launch, exact PID-tree stop, switching, and stale-event isolation |
| `desktop/electron/desktop-pet-preferences.ts` | Default-off intent, Main-private folder, stable selection, migration, and atomic persistence |
| `desktop/electron/desktop-pet-contracts.ts` | Path-free state and update contracts exposed to the main Renderer |
| `desktop/electron/main.ts` | Native folder picker, serialized reconciliation, tray actions, and shutdown ownership |
| `desktop/src/settings/SettingsView.tsx` | User-facing folder, detected-program, and mode controls |

There is no separate Elysia-owned Pet HTML entry, Pet Preload, dynamic rendering
surface, embedded model loader, custom model-asset protocol, or packaged
dynamic-character runtime in the current architecture. The external program
keeps its own window, position, rendering resources, model behavior, and UI.

## Local source and rights boundary

The project owner's purchased local collection is attributed to `@书呆儿` and
is currently stored under an ignored local directory:

```text
data/characters/爱莉希雅原版猫猫版总合集_34be1/
```

That path is not a product requirement. Settings uses a native folder picker,
and installed builds do not probe the source checkout for the paid directory.
The selected absolute path remains private to Main. React receives only a safe
folder label, stable program IDs, display names, status, and sanitized warning.

The recorded free-version page is
<https://pan.quark.cn/s/cb5d84acad8e>. Its current contents, authorization,
terms, availability, and equivalence to the purchased local collection have
not been independently verified. The link records provenance context only; it
is not a redistribution grant, security endorsement, or promise that a
download will pass this application's reviewed-build checks.

Every paid EXE, DLL, Live2D model, texture, motion, expression, physics file,
runtime, configuration, and source archive remains local to the user. None may
enter Git, GitHub, releases, installers, ASAR, unpacked builds, fixtures,
examples, screenshots, generated test output, or shared diagnostics.

## Discovery is not arbitrary execution authority

Electron Main owns discovery because a folder chosen by the user can contain
arbitrary native code. A successful directory selection therefore does not
authorize every EXE below that directory. The scanner applies bounded depth,
entry, candidate, top-level-entry, JSON-size, reference-count, reference-length,
and referenced-file-size limits. It rejects symbolic links, junction/path
escapes, absolute or parent-relative model references, URLs, control
characters, and ambiguous program identities.

A candidate must contain complete `standard`, `keyboard`, and `gamepad` model
profiles whose manifests and referenced local files remain inside both the
candidate program directory and selected collection root. Its `config.json`
must be a bounded JSON object with the required `decoration`, `standard`,
`keyboard`, `gamepad`, `network`, and `workarea` sections and a supported input
mode.

Native code is a stricter boundary. Main rejects any unreviewed top-level EXE
or DLL. The launcher, `BongoCatUI.exe`, `BongoCatMverUI.dll`, every admitted
dependency DLL, and required resource files must match pinned byte lengths and
SHA-256 identities. Hashing reads an exact size and checks file metadata again,
so a file that changes during validation fails closed.

The stable selection ID derives from the program's normalized relative
directory plus the pinned launcher identity. Mutable user configuration is not
part of that ID, so legitimate per-program settings do not silently create a
new selection. The number of available programs is always the validated scan
result; no product path assumes that a collection contains exactly six.

## Launch-time revalidation

A completed Settings scan is a snapshot, not permanent execution authority.
Immediately before every launch, Main confirms that the collection root,
program directory, launcher path, selected descriptor, and stable ID still
refer to the same canonical locations. It then repeats the complete structural
validation and every pinned executable, DLL, and resource hash.

This second gate rejects a stale selection or replacement that is already
observable when revalidation runs, and it shortens the interval between the
last validation and process creation. It does not eliminate TOCTOU: launch is
still path-based, not bound to a verified open file handle or immutable Windows
file identity, so a local actor able to modify the collection could replace a
path after revalidation and before Windows resolves the executable or one of
its dependencies. A moved, missing, modified, newly injected, or link-replaced
native component observed by revalidation prevents launch and produces a fixed
warning asking the user to rescan. Native paths and underlying diagnostics do
not cross into React.

## Configuration ownership

Each original `config.json` stays beside its program and remains owned by the
original Bongo Cat Mver application. Elysia performs only the bounded read
needed to validate its required sections and supported input mode. It never
modifies, replaces, normalizes, copies, uploads, packages, or migrates that
file, and it does not mirror the program's settings into Electron application
data.

Consequently, each appearance keeps its own keyboard, mouse, gamepad, window,
expression, audio, and other original settings when the user switches away and
later returns. The Elysia preference stores only its own mode, chosen
collection path, and selected stable program ID.

## Original-program interaction behavior

Elysia launches the selected reviewed executable with no arguments, with the
program's own directory as its working directory, and without a command shell.
The external window remains visible because it is the actual Desktop Pet.

The reviewed Bongo Cat Mver launcher embeds a `requireAdministrator` manifest,
but Elysia deliberately applies Windows `RunAsInvoker` compatibility for that
child only. The companion therefore stays under the current interactive user
token instead of receiving administrator rights, while Main retains the exact
PID tree needed for safe switching and shutdown. Windows integrity isolation
may prevent the companion from observing input sent to a separately elevated
application; Elysia does not elevate itself or the unsigned companion to bypass
that operating-system boundary.

The original program is responsible for:

- standard mouse-and-keyboard response;
- keyboard-only and gamepad input modes;
- mouse and Live2D eye tracking, including its own mirror controls;
- expression shortcuts and restoration behavior;
- model motion, expressions, physics, and rendering;
- moving, resizing, always-on-top behavior, and desktop/game display options;
- its own UI, tray behavior, frame limit, background, audio, and compatibility
  settings.

Elysia does not emulate these controls, inject input, interpret their model
parameters, or promise that a differently built third-party download behaves
like the reviewed local program.

## Process ownership and switching

`DesktopPetProgramManager` serializes all admitted transitions and owns at most
one child returned by its exact launch call. It never adopts a process that the
user started manually. Launch uses the selected executable path, an empty
argument list, its program directory as `cwd`, `shell: false`,
`detached: false`, visible Windows behavior, and ignored standard streams.

Switching from program A to B always stops and awaits A before launching B. On
Windows, the stop path invokes the system `taskkill.exe` with only
`/PID <owned-root-pid> /T`. It does not use `/IM`, enumerate processes, or
match executable names; therefore a manually started copy with the same name
is outside Elysia's authority. `/T` covers descendants created by the exact
owned root. Native termination and the higher-level stop both have deadlines.
If the old PID tree cannot be confirmed stopped, ownership is retained, the
runtime becomes failed, and B is not launched.

Process callbacks also retain object identity. An exit or error from an old
child cannot overwrite the state of a replacement. An unexpected exit of the
active child changes runtime state to `failed` with a sanitized warning; an
expected stop converges to `absent`. Startup errors, missing PIDs, stop errors,
and timeouts expose fixed messages rather than native paths or command output.

## Modes, failures, exit, and tray recovery

- `disabled` is the default and runs no external program.
- `hidden` retains the chosen folder and program but stops the Elysia-owned
  process and releases its resources.
- `visible` requires a ready scan and selected program, repeats launch-time
  validation, and then starts that program.
- A missing/disconnected directory, invalid candidate set, changed program,
  launch failure, or unexpected process exit affects only the optional Pet.
  Chat, Voice, Knowledge, local speech, and static character art continue.
- The tray offers **Launch Desktop Pet**, **Stop Desktop Pet**, or **Retry
  Desktop Pet** according to current state, plus **Disable Desktop Pet**.
  When no trusted selection exists, it directs the user to Settings.
- Hidden or visible intent keeps Elysia tray-resident when the main window is
  closed, so the Pet can be launched or stopped again. Disabling restores the
  normal last-window-close behavior.
- Application shutdown makes the manager reject new launches and performs a
  bounded stop of the exact owned process. Optional cleanup cannot block exit
  forever.

Settings mutations, startup scan, rescan, tray requests, and selection changes
share a serialized mutation queue. A selection change stops the old process
before publishing and reconciling the new selection, so Settings never pairs a
new label with the previous running program.

## Persistence and migration

Electron stores its own strict, bounded, revisioned desktop-pet preference
under `userData` with atomic replacement. The default and every invalid stored
document fail closed to `disabled`. Main keeps the absolute collection path
private; public state contains no native path or PID.

Migration is version-specific:

- A version-1 preference belongs to the removed embedded-model window. Loading
  it forces Desktop Pet to `disabled`, clears the obsolete placement, carries
  forward no external path or selection, and asks the user to choose a local
  companion-program folder.
- A version-2 preference already describes an external program. Loading it
  preserves its valid mode, collection path, and selected stable program ID,
  but discards the obsolete placement field because Bongo Cat Mver owns its own
  position. Persisted values are not launch authority: Main rescans the
  collection, resolves the saved ID against the newly validated result, and
  performs launch-time revalidation before starting the program.

## Distribution integrity

`scripts/check_distribution_assets.py` audits the Git index, reviewed Electron
Builder configuration, real unpacked package, ASAR listing, and extracted ASAR
tree. The package may contain only the reviewed static character assets and
ordinary application code. The checker rejects the named paid directory and
external desktop-pet executables, DLLs, configurations, Live2D manifests,
MOCs, textures, motions, expressions, physics, runtimes, editor files, archives,
or packaging escape mechanisms.

This engineering gate reduces accidental distribution risk; it does not prove
ownership or grant rights. The complete provenance and rights record remains in
[`MODEL_LICENSE.md`](../MODEL_LICENSE.md).

## Verification

The required automated gates are:

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

Package CI additionally captures `asar list`, extracts the complete ASAR tree,
and audits both. A valid package contains reviewed static in-app art but no
external desktop-pet program, paid Live2D asset, or dynamic-character runtime.
