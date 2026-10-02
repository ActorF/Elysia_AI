# Live2D Runtime Architecture

> Scope: static in-app character presentation, external local Live2D discovery,
> Desktop Pet rendering, selection persistence, and distribution boundaries

## Delivered behavior

The character panel inside the main Elysia window is always static. Character
state and the user-selected `neutral / happy / sad` expression choose only the
reviewed PNG atlases. There is no Animated/Still choice for that panel, and it
never creates a Live2D model, WebGL context, or animation loop.

The separate transparent Desktop Pet is the only animated character surface.
It is disabled by default. Settings first lets the user choose an external
local directory, then lists the compatible variants discovered there and
stores one stable selection. The Pet reads that model in place only while it
is visible; Elysia AI does not persistently import, duplicate, or cache any
model, texture, expression, motion, physics, or source archive into the
repository, application data, build output, or package. Bounded model bytes
exist only in memory while validating, serving, or rendering the visible Pet.

## Asset and source boundary

The project owner's current purchased local collection happens to be stored at:

```text
data/characters/爱莉希雅原版猫猫版总合集_34be1/
```

The collection is attributed to `@书呆儿`. Its recorded public download page
is <https://pan.quark.cn/s/cb5d84acad8e>. The link is recorded for provenance,
not as a warranty: its current content, authorization, terms, and availability
have not been independently verified. Runtime discovery therefore reports the
models actually present in the directory chosen by the user rather than
requiring this repository-relative location or assuming a fixed count. The
owner's current local copy resolves to six appearance groups, but that is an
observed scan result rather than a product constant.

This directory is local-only. It must never enter Git, GitHub, releases,
installers, ASAR, unpacked builds, examples, fixtures, screenshots, or generated
test output. `scripts/check_distribution_assets.py` enforces both the named
source-directory exclusion and a global rule that rejects Cubism model and
editor suffixes anywhere in Git, build trees, ASAR listings, or extracted
archives, even if a copied model was moved or its parent directory renamed.
Only the exact reviewed model-independent compatibility runtime and license
paths may be packaged below `character/live2d`.

The former project-generated `desktop/public/character/live2d/elysia` model,
face master, 21-layer authoring stack, build wrappers, and face-alignment gate
have been removed. They are not a fallback and must not be recreated by the
desktop build.

## Runtime layout

| Path | Responsibility |
| --- | --- |
| `desktop/public/character/elysia-state-atlas.png` | Static closed-state artwork for the main character panel |
| `desktop/public/character/elysia-expression-atlas.png` | Static user-selected expression artwork |
| `desktop/public/character/elysia-portrait.png` | Safe static fallback when no reviewed atlas cell is available |
| `desktop/public/character/live2d/runtime/purismcore.js` | Packaged model-independent Cubism compatibility runtime |
| `desktop/public/character/live2d/runtime/LICENSE-PurismCore.txt` | Required MIT notice for the compatibility runtime |
| `desktop/src/character/live2d-runtime.ts` | Bounded model loader, controller, and WebGL2 renderer |
| `desktop/pet.html` and `desktop/src/desktop-pet-main.ts` | Separate transparent Pet surface |
| `desktop/electron/desktop-pet-model-library.ts` | Bounded external-directory scanner and manifest/resource validation |
| `desktop/electron/live2d-assets.ts` | Generation-scoped opaque asset registry and custom-protocol boundary |
| `desktop/electron/desktop-pet-preferences.ts` | Default-off mode, private library location, selected model, and placement persistence |

## Discovery and selection

Electron Main owns filesystem discovery because sandboxed Renderers cannot
traverse local directories. Discovery starts from the configured collection
root, accepts only recognized `.model3.json` entry points, and returns bounded
display metadata rather than unrestricted paths. A compatible manifest may
refer only to regular files inside both its approved model directory and the
selected library root; absolute paths, traversal, symlinks, junction escapes,
URLs, credentials, query strings, fragments, and unbounded files fail closed.

Settings displays the scan result in a closed selector. The persisted value is
a stable identifier derived by trusted code, not a user-submitted filesystem
path. If the saved model disappears, becomes invalid, or is no longer within
the approved root, the application clears its effective selection and reports
that the Desktop Pet needs a compatible local model. It does not silently pick
a different costume.

The number six describes the owner's current local collection. Bongo-style
standard, keyboard, and gamepad manifests are grouped by appearance, with the
standard profile preferred, so duplicate control profiles are not presented as
different costumes. The UI still uses the actual validated scan result, so a
partial download or later local change is represented accurately. Labels are
presentation metadata only and cannot alter entry-point or asset resolution.

## Presentation split

- Main Chat, Voice, and other in-app state cards always render reviewed static
  artwork. Semantic state may change the chosen static cell, but model output
  cannot choose files, animation names, or Live2D parameters.
- The Desktop Pet renders the selected external model as a full-body animated
  character in its transparent always-on-top window. It has no static/dynamic
  preference: choosing to show the Pet means choosing its dynamic renderer.
- Pet visibility controls resource ownership. Hidden and Disabled states tear
  down the model, animation frame, and WebGL resources rather than retaining an
  invisible external model.
- Failure to discover or load a Pet model affects only the Pet. Chat, Voice,
  Knowledge, local speech playback, and the main static character panel remain
  usable.

## Security and privacy

The external collection remains under the user's control. The application
reads only files reachable from the selected, validated manifest and does not
upload them. Renderer code receives model bytes through the narrow trusted
asset boundary rather than `file:` access or Node filesystem APIs. Existing
context isolation, sandboxing, navigation denial, and window-open denial remain
enabled.

Motion, expression, pose, physics, display-info, and user-data references are
validated strictly as bounded local data during discovery. They cannot
introduce JavaScript, HTML, remote URLs, arbitrary local paths, or IPC calls.
The Pet renderer fetches the selected manifest, MOC, textures, and optional
`physics3` document. Physics metadata, identifiers, counts, ranges, particles,
inputs, and outputs are bounded before a fixed-step pendulum evaluator may
write model parameters. Motion, expression, pose, display-info, and user-data
documents are allowlisted and validated by Main but are not executed by the
current renderer. Invalid optional references therefore reject a candidate
without broadening the filesystem boundary.

PNG compressed-byte limits alone do not bound decoded memory. Before calling a
browser image decoder, the runtime validates each PNG signature and IHDR,
enforces 4096-pixel dimensions, a 16-million-pixel per-image limit, and the
same aggregate decoded-pixel budget across all textures. It then verifies the
decoder's actual dimensions against the admitted header.

After selection, Electron Main creates a generation-scoped registry containing
only the verified resources for that model. The sandboxed Pet receives one
opaque `elysia-pet-asset:` manifest URL through its five-method Preload
(`ready`, `failed`, `hide`, `openMainChat`, and `getModelBootstrap`). The
closed `failed` signal carries no error text or path; it lets Main immediately
destroy a renderer that cannot initialize or later loses its runtime. Changing
the model rotates the registry generation. Asset responses are materialized to
their exact admitted length and recheck that generation after native reads, so
even a request already in flight cannot continue streaming old model bytes.

## Distribution integrity

`scripts/check_distribution_assets.py` pins the PurismCore build, its MIT
license, the application icon formats, and reviewed static fallback assets by
path, byte length, and SHA-256.
Package CI requires each expected entry exactly once in ASAR and authenticates
the bytes extracted from the complete archive. The same checker rejects:

- the `爱莉希雅原版猫猫版总合集_34be1` component anywhere in Git or build output;
- every Cubism model/editor suffix regardless of its packaged name or path,
  plus any unexpected file below the exact model-independent Runtime paths;
- every PNG, ICO, WebP, or other reviewed visual format outside the exact
  source/package path allowlists, so an isolated paid texture remains forbidden
  even after its model filename and parent directory are changed;
- Electron Builder hooks or copy mechanisms that could bypass the file
  allowlist; and
- existing model-weight, audio, archive, runtime-cache, log, workspace, and
  private-configuration exclusions.

PurismCore v1.1.0 is distributed with its MIT notice. That software license
does not license an externally selected MOC, textures, motions, the Elysia
character, or any other third-party art. The separate provenance and rights
record remains in `MODEL_LICENSE.md`.

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
and audits both. A valid build contains the compatibility runtime and reviewed
static art, but no Elysia Live2D model assets.
