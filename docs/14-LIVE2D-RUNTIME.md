# Live2D Runtime Architecture

> Scope: local Cubism-compatible character model, Electron rendering, half-body and full-body framing, state/expression/mouth control, desktop-pet reuse, and safe fallback behavior

## Delivered behavior

Animated character mode now loads a real binary `model.moc3` instead of moving a flat portrait with CSS. The current Elysia model exposes 26 drawables and 31 bounded parameters for eyes, brows, mouth, head, body, limbs, hair, accessory movement, breath, and cheek color. The main Character Panel uses a head-to-waist half-body composition, the taller Voice call surface uses its own call-specific half-body composition, and the separate transparent always-on-top desktop pet presents the full body. All three surfaces share the same packaged model and texture.

The runtime remains a presentation layer. Chat, Voice, Knowledge, and future Work/Approval producers emit only the closed `CharacterState` values; the user setting emits only `neutral`, `happy`, or `sad`; trusted Preload emits only `closed`, `small`, `medium`, or `wide` from real Web Audio RMS. None of those producers can select model files, drawable IDs, arbitrary parameter names, or executable motion data.

## File layout

| Path | Responsibility |
|---|---|
| `data/characters/elysia-2dArt/live2d-source/` | Common-canvas source layers retained for visual review and later hand correction; never packaged |
| `desktop/public/character/live2d/elysia/model.model3.json` | Minimal fixed model manifest naming one MOC and one texture |
| `desktop/public/character/live2d/elysia/model.moc3` | Binary Cubism-compatible rig |
| `desktop/public/character/live2d/elysia/textures/atlas.png` | Packed model texture |
| `desktop/public/character/live2d/runtime/purismcore.js` | Pinned PurismCore v1.1.0 Web runtime |
| `desktop/src/character/live2d-runtime.ts` | Bounded loader, parameter driver, and native WebGL2 renderer |
| `desktop/src/character/Live2DCharacterCanvas.tsx` | React lifecycle boundary for one controller/canvas |
| `desktop/pet.html` and `desktop/src/desktop-pet-main.ts` | Separate full-body pet surface, minimal controls, and pet-specific controller lifecycle |
| `desktop/electron/live2d-assets.ts` | Exact packaged-resource URL allowlist |

Only three model URLs are fetchable through `elysia-asset://character`: the manifest, MOC, and texture. Electron resolves them under the application-owned `dist/character/live2d/elysia` directory and rejects all other hosts, methods, paths, query strings, fragments, credentials, percent escapes, dot segments, and path aliases. Development pages use Vite's same-origin public assets; packaged `file:` pages use the restricted scheme. `webSecurity`, context isolation, sandboxing, and the existing navigation/window-open denial remain enabled.

## Render and control flow

1. `CharacterArtwork` checks the effective performance mode.
2. Animated mode mounts a controller with an explicit presentation framing: `half-body` for the near-square Main Character Panel, `call-half-body` for the taller Voice surface, or `full-body` for the desktop pet. Still and OS Reduced Motion do not create a Live2D model, WebGL context, or render loop; the shared page currently loads the Core script before that renderer-level decision.
3. The loader validates the fixed manifest shape, fetch timeout, response size, MOC consistency, texture, and WebGL2 availability.
4. PurismCore creates the model. The renderer looks up only its predefined parameter IDs and clamps every value to the model's declared minimum and maximum.
5. Each animation frame combines a small deterministic idle cycle with the current semantic state, selected emotion, and trusted mouth cue, calls `model.update()`, sorts drawables by draw order, and renders the complete frame.
6. ResizeObserver updates backing resolution with a bounded device-pixel ratio. The selected framing keeps the reviewed character bounds centered and scaled for its surface instead of fitting the model's unused square canvas space.
7. Unmount, performance-mode change, initialization failure, or an unrecoverable frame/restore error cancels animation, releases model/MOC and GL resources, and returns to reviewed static artwork. A transient WebGL context loss first pauses rendering and rebuilds GPU resources after `webglcontextrestored`; only a failed rebuild enters the fatal fallback path.

The renderer intentionally uploads and redraws all model drawables each frame. The current model is small and has no drawable masks, so this avoids trusting optional dirty-flag behavior in the compatibility core while keeping the implementation deterministic and auditable. A future masked model must add and test an explicit stencil/mask pass before it can replace this asset.

## Presentation surfaces

- Main Chat uses `half-body`, whose reviewed viewport runs from the head ornament to the waist and fills the existing near-square portrait panel. Voice uses the separate `call-half-body` viewport so its taller call surface keeps the shoulders and arms without shrinking the Main portrait back into a miniature full-body figure.
- The Desktop Pet uses `full-body`. The complete character is visible inside its nominal 320×480 DIP transparent always-on-top window.
- Framing is selected by trusted application code when the controller is created. Character state, model output, and persisted user data cannot submit camera values, crop coordinates, or arbitrary framing names.
- The pet's full character surface is an Electron drag region. A small toolbar appears on hover or keyboard focus; its non-drag Chat and Hide controls respectively focus Main Chat or switch the pet to `hidden`.

## State and expression mapping

| Input | Live2D result |
|---|---|
| `idle` | Gentle breathing, blink, small head/body sway |
| `listening` | More attentive head angle and open eyes |
| `thinking` | Focused brows, slower side movement |
| `speaking` | Friendly mouth form plus real RMS mouth opening |
| `working` | Focused brow/body posture |
| `waiting_approval` | Patient, low-motion posture |
| `error` | Concerned brows and reduced motion |
| `neutral / happy / sad` | Bounded mouth, eye-smile, brow, and cheek offsets |
| `closed / small / medium / wide` | Four clamped `ParamMouthOpenY` targets |

The mouth is amplitude-driven, not phoneme-driven. It synchronizes visible opening with audio energy but does not claim viseme or A/E/I/O/U recognition.

## Fallbacks and accessibility

- While the model initializes, the reviewed state/expression/mouth atlas remains visible.
- A missing Core, blocked WASM, invalid manifest, failed fetch, unsupported WebGL2, unrecoverable context restoration, or model/texture error switches to the same static chain without disabling Chat or Voice. A successfully restored WebGL context resumes Live2D instead of flashing the fallback.
- Still mode and OS Reduced Motion use only static assets and never start a render loop.
- The desktop pet starts Live2D only when visible, the shared user preference is `animated`, and OS motion policy allows it. It keeps the original portrait and text fallback as independent recovery surfaces.
- Trusted speech sampling checks `data-character-mouth-capable="true"`, so it drives either the Live2D canvas or the legacy speaking atlas without learning model internals.

## Desktop Pet preference boundary

Electron Main owns the strict `disabled / hidden / visible` preference and native placement. If the preference file does not exist, the first-run state is `visible`; the user can then hide or disable the pet through its controls, Settings, or the tray. A preference file that exists but is corrupt, oversized, or invalid still fails closed to `disabled`. Hidden and Disabled destroy the dedicated Renderer rather than retaining an invisible WebGL page.

The pet Renderer remains separate from the main application. Renderer `localStorage` is never shared across partitions: the trusted main Renderer sends only the validated `animated / still` choice to Electron Main, which keeps a monotonic in-memory snapshot. The pet's minimal Preload can read and subscribe to that snapshot in addition to reporting ready, requesting Hide, or opening Main Chat. The pet defaults to Still until this synchronization succeeds, and its own OS Reduced Motion query remains an independent override. It cannot access the Python Backend, Chat data, microphone, filesystem, Node, arbitrary IPC, or the main `DesktopApi`.

## Distribution integrity

`scripts/check_distribution_assets.py` pins the manifest, MOC, texture, Core runtime, Core license, and all static fallback assets by exact path, byte length, and SHA-256. Package CI requires every path exactly once in the ASAR and authenticates the bytes from a complete extracted ASAR tree. Source layers remain outside the Electron Builder allowlist.

PurismCore v1.1.0 is included with its MIT license. Character-art and model authorization review remains a separate unresolved distribution matter recorded in `MODEL_LICENSE.md`; it does not change whether the application technically loads and drives the model.

## Verification

The required automated gates are:

```text
python scripts/check_python_documentation.py
python scripts/check_distribution_assets.py
python -m pytest
cd desktop
npm run docs:check
npm run typecheck
npm test
npm run make
```

The packaged audit then captures `asar list`, extracts the complete ASAR tree, and runs the distribution checker with both artifacts. A valid package must remain fully offline and must render or cleanly fall back without weakening Electron security controls.
