# Elysia Desktop

React + TypeScript, hosted by Electron with a local Python Backend.
This guide covers development, verification, and native boundaries.
For product setup, start with the [project README](../README.md).

## Run locally

Prepare the repository's `.venv` and a running Ollama instance with the
configured Chat model. A root `.env` is optional. In a new **PowerShell**
window, use the actual checkout path; this example assumes `D:\Elysia_AI`:

```powershell
Set-Location -LiteralPath 'D:\Elysia_AI\desktop'
npm.cmd ci
npm.cmd run dev
```

In **CMD**, replace the first line with `cd /d "D:\Elysia_AI\desktop"`.
Run `npm.cmd ci` only for initial setup or after dependency updates; later
launches need just the directory change and `npm.cmd run dev`.
Using `npm.cmd` also avoids PowerShell's `npm.ps1` execution-policy errors.

The command builds Main/Preload, starts Vite, opens Electron, and owns
`.venv\Scripts\python.exe desktop_backend.py` until exit.
`npm run dev:renderer` is a browser-only preview; Backend, microphone,
and Desktop Pet integration require the Electron window.

Optional features use separately prepared local assets:

| Capability | Requirements |
| --- | --- |
| Project Sources | Pinned Ollama `qwen3-embedding:0.6b`; see [embeddings](../docs/07-LOCAL-EMBEDDINGS-VECTOR-STORE.md). |
| Speech recognition | `requirements-stt.txt` and a complete `models/weights/faster-whisper/<model>/` directory. |
| Speech playback | GPT-SoVITS runtime, checkpoints/reference audio under `models/weights/gpt-sovits/`, and active-root `workspace/settings/voice-profiles.json`. |
| Song Cover | Private CUDA/WSL runtimes in the [Song Cover guide](../docs/15-SONG-COVER.md); unavailable in the current installer. |
| Desktop Pet | A supported external program selected in Settings; see [character presentation](../docs/14-LIVE2D-RUNTIME.md). |

These assets are not downloaded automatically or bundled. Text Chat does not
require them. For speech recognition, run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-stt.txt
```

Place the complete model in `models/weights/faster-whisper/small/`, select it
in Settings, and restart the Backend. Supported names are `tiny`, `base`,
`small`, `medium`, `large-v3`, and `turbo`. CPU is the default to leave GPU
room for Ollama and GPT-SoVITS. Model directories stay Git-ignored.

## Interface essentials

| Action | Behavior |
| --- | --- |
| `Ctrl+,` / `Ctrl+K` / `Ctrl+B` | Settings / Chat search / toggle navigation. |
| Enter / Shift+Enter | Send / new line; IME composition never sends. |
| Work permissions | Beside the model: conversation only, read-only, ask before actions (default), or allow supported desktop actions. File access stays inside the selected Workspace. |
| Deep thinking | Enables the local model's thinking mode for this Work reply. The collapsible activity log shows actual stages and tool results; retrying an answer never repeats actions. |
| Dictate | Listens continuously, transcribes bounded segments in order, and appends final text to the draft without sending. Stop closes the microphone and drains pending transcription. |
| Voice Call | Opens a separate window and automatically sends each completed utterance to the current Chat. Mute pauses capture; End call or Escape releases its audio resources. |
| Read aloud | Speaks one persisted Assistant reply. Ordinary text Chat is silent by default. |
| Use Project Sources | Opts the current turn or retry into the Chat's canonical Project corpus; reload or a context change resets the choice. |

Chat attachments remain message data; they are not automatically indexed.
Unsent text is saved per Chat, and renderer refresh reconnects to requests
still owned by Electron. Settings labels live and restart-required changes.

### Voice settings

| Setting | Default / range | Applies |
| --- | --- | --- |
| Speak Voice Call replies | On / On or Off | Next admitted operation |
| Speech volume | 100 / 0–100% | Each admitted clip; 0 silences playback without stopping synthesis |
| Speech rate | 100 / 50–200% | After Backend restart |
| Voice profile | `default` / configured logical ID | After Backend restart |
| Voice emotion | `neutral` / ten reviewed values | After Backend restart; selects the TTS reference |

Emotions are `neutral`, `happy`, `sad`, `caring`, `moved`, `playful`,
`affectionate`, `teasing`, `serious`, and `surprised`.
The character panel selects static art by application state; Voice Call uses
a fixed background. Neither view changes its artwork with Voice Emotion.
Retired `captionsEnabled`, `transcriptReviewMode`, and `automaticRelisten`
fields remain migration-compatible but do not control the current UI.

## Verify

From the repository root:

```powershell
python scripts/check_python_documentation.py
```

From `desktop`:

```powershell
npm run docs:check
npm run lint
npm run typecheck
npm test
npm run build
npm audit --audit-level=high
```

`npm test` includes contract and UI tests. Use `npm run test:contract` or
`npm run test:ui` for a focused run. UI tests use a sandboxed test preload
excluded from the application. Documentation checks enforce structure;
explanations still require review under [AGENTS.md](../AGENTS.md).

### Device checks

Real-device observations remain **pending**, following the owner's 2026-09-22
waiver as a delivery gate. Automated checks do not establish subjective audio
quality, room/headset echo behavior, interruption latency, extended hardware
stability, or screen-reader quality.

For a new run, record the date/commit, Windows version, microphone, speaker,
room conditions, model/device/profile/emotion, session length, turn count,
interruption attempts/false triggers/delay, and screen reader. Check:

- Dictate through silence and speech longer than 30 seconds; Stop drains in
  order. Navigation or device failure discards pending audio without losing
  accepted text or editing another Chat.
- Voice Call preserves the draft and staged attachments, sends one final
  transcript per utterance, and listens again after the exact turn settles.
- Mute, End call, context changes, and failures release the correct audio
  owner; late callbacks cannot reopen a retired call.
- Sustained speech interrupts only with verified echo cancellation. Record
  quiet-room, speaker-echo, and headset results; unavailable AEC leaves the
  current reply running.
- Read aloud, volume 0, live/restart-required settings, keyboard navigation,
  and screen-reader announcements behave as described above.

Performance and lifecycle-soak evidence, with their limits, are in the
[Voice acceptance record](../docs/03-VOICE-PERFORMANCE-SAFETY-RIGHTS.md).

### Local synthesis

Copy `config/voice_profiles.example.json` to ignored active-root
`workspace/settings/voice-profiles.json` and configure authorized local assets.
The catalog requires Schema v2: replace every asset's placeholder `path`,
`bytes`, and lowercase 64-character `sha256` with its actual values. Use `/`
in relative asset paths, including on Windows; weights and reference audio
must remain under `models/weights/gpt-sovits/`. Legacy string-only paths are
not accepted. Fill in the exact reference text and language for each emotion.

For a `local-evaluation-only` profile, keep
`GPT_SOVITS_ALLOW_LOCAL_EVALUATION=False` until its rights and paths have been
explicitly reviewed, then opt in with `GPT_SOVITS_ALLOW_LOCAL_EVALUATION=True`
in the root `.env` and restart the Backend. Local opt-in does not grant
redistribution rights.

Desktop playback owns its GPT-SoVITS worker under
`models/cache/GPT-SoVITS-v2-240821/`; keep the independent loopback API stopped
while testing desktop playback. Verify ordered clips, per-clip volume, reload
recovery, and cleanup on exit.

The independent API smoke accepts loopback-IP HTTP, WAV/AAC, and assets below
`models/weights/gpt-sovits/`. The recorded local acceptance runtime can be
started in a separate CMD window, then stopped with Ctrl+C:

```bat
cd /d D:\Elysia_AI\models\cache\GPT-SoVITS-v2-240821
runtime\python.exe -X utf8 api_v2.py -c GPT_SoVITS\configs\tts_infer_elysia.yaml -a 127.0.0.1 -p 9880
```

That runtime and YAML are local examples, not repository content. Use a runtime
and Profile whose paths and rights you have reviewed. From the repository root:

```powershell
.\.venv\Scripts\python.exe scripts/smoke_gpt_sovits.py --profile default --emotion neutral --emotion happy --emotion sad --emotion caring --emotion moved --emotion playful --emotion affectionate --emotion teasing --emotion serious --emotion surprised
```

The smoke synthesizes a fixed sentence twice per emotion in memory.
`service_binding_unverified` confirms API compatibility, not loaded-model
identity; stopping the API must produce `service_unreachable`.

## Package and audit

From `desktop`, `npm run package` creates `out/win-unpacked`; `npm run make`
also builds an unsigned NSIS installer. The shell package does not bundle
Python, Ollama, or private model runtimes. For development-only use, set
`ELYSIA_PROJECT_ROOT` and `ELYSIA_PYTHON` to the checkout and interpreter.

The scoped `@electron/get@3.1.0` → `global-agent@4.1.3` override preserves
the downloader's CommonJS proxy bootstrap while removing the legacy formatter
chain; `tests/packaging-proxy.test.mjs` checks that compatibility offline.

Audit the actual package and extracted ASAR before distribution:

```powershell
npm run package
$auditRoot = Join-Path ([IO.Path]::GetTempPath()) ("elysia-package-audit-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $auditRoot | Out-Null
$auditListing = Join-Path $auditRoot "asar-listing.txt"
$auditExtracted = Join-Path $auditRoot "extracted"
npx --no-install asar list .\out\win-unpacked\resources\app.asar | Set-Content -Encoding utf8 $auditListing
npx --no-install asar extract .\out\win-unpacked\resources\app.asar $auditExtracted
..\.venv\Scripts\python.exe ..\scripts\check_distribution_assets.py --unpacked-tree .\out\win-unpacked --asar-listing $auditListing --extracted-asar-tree $auditExtracted
```

The audit checks Git, package paths, and reviewed artwork bytes. Models, voice
catalogs, reference audio, and external Desktop Pet programs remain excluded.
Read [MODEL_LICENSE.md](../MODEL_LICENSE.md) before publishing; official signet
icons and generated fan artwork are outside any source-code license.
The audit folder is disposable after review.

For a visible-window benchmark, use PowerShell 7.4+ from `desktop`:

```powershell
pwsh -NoProfile -File .\benchmarks\measure-shell.ps1 `
  -Executable .\out\win-unpacked\Elysia.exe -Runs 10
```

This does not measure Backend readiness or first Chat. See the
[Electron decision](../docs/decisions/0001-desktop-shell.md) for measurements
and migration gates.

## Native boundaries

- React has no direct Node.js, Python, Chat, or Memory access. Main validates
  origin and top frame; the sandboxed preload exposes only
  [contracts.ts](electron/contracts.ts). Each Python child uses a fresh
  authenticated handshake and the shared [protocol](../desktop_protocol/README.md).
- Project Sources require explicit intake and turn-level consent. A global
  lease protects lifecycle work, grounded answers, and verified export.
  Export survives renderer reload, is not cancellable from React, and reports
  success only after receipt validation; native paths remain private.
- Dictation PCM is transient and bounded. Transcription returns final text,
  normalized with OpenCC `t2s`. Reply-time interruption requires verified AEC;
  held interruption audio expires within 10 seconds.
- Speech bytes use private fd3 framing and trusted preload playback. React
  receives sanitized status only. Exact request/Chat ownership prevents stale
  cancellation; a missing selected speaker fails the clip. Runtime manifests
  prove launch consistency, not full provenance; speech caching stays off.
- Desktop Pet is off by default. Main revalidates pinned program identities,
  owns only its child, and preserves external configuration. Path-based launch
  retains a check-to-use interval. The reviewed launcher uses `RunAsInvoker`
  without administrator rights.
- Notifications are off by default, silent, and use fixed Main-owned copy.
  Reminders run only while Elysia is open and unattended; busy intervals are
  consumed without catch-up. There is no background task or startup entry.
- Settings uses closed schemas, optimistic revisions, and atomic replacement.
  [Data storage](../docs/13-PRODUCTION-DATA-LAYOUT.md) verifies migrations and
  retains recovery copies; generic cleanup does not delete user content.

Further contracts: [Work](../docs/17-WORKFLOW-AND-DESKTOP-AGENT.md) ·
[Vision](../docs/18-VISION-AND-SCREEN.md) ·
[Internet](../docs/19-INTERNET-AND-SERVICES.md) ·
[Song Cover](../docs/15-SONG-COVER.md).
