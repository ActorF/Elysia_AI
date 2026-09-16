# Elysia Desktop

The Stage 6 desktop foundation connects a React + TypeScript interface to the
existing Python Brain through an Electron-owned child process and a strict,
versioned local protocol. The first Stage 7 Voice slice adds host-local
microphone and speaker selection, native permission status, and bounded input
and output tests without retaining audio. The bounded capture slice adds
explicit one-utterance recording: the renderer downmixes and resamples input to
16 kHz mono `s16le`, and local VAD submits only valid speech transiently for
local Faster-Whisper transcription. A bounded final transcript returns through
Electron to an editable review surface. The user can explicitly send it
through the current Chat's normal durable path or place it in the Composer,
where it replaces an empty draft or is appended after existing draft text. A
renderer-local controller binds the exact Chat and optional Project and owns
the closed `IDLE → LISTENING → TRANSCRIBING → THINKING → SPEAKING → IDLE`
lifecycle without owning audio bytes. The renderer also has persistent Chat and Project
surfaces, resilient streamed message actions, revisioned Settings, Chat
attachments, Project source storage, semantic design tokens, system/light/dark
themes, keyboard and screen-reader navigation, durable per-Chat drafts,
renderer-refresh stream recovery, and consistent loading, empty, error,
offline, and fatal states. This Voice slice still exposes final transcripts
only and requires explicit submission. After that submission, a dedicated
reply-time monitor can accept sustained user speech while the turn is thinking
or speaking. It requires WebRTC echo cancellation both as an exact constraint
and as a verified track setting; otherwise it fails closed and the reply
continues. Confirmed speech stops exact playback and managed synthesis, requests
exact Chat cancellation, advances the Voice epoch, and continues the new
capture in `LISTENING`. Automatic re-listening after a normally completed reply
and real-time partial transcripts remain future work. Ordinary
Chat replies now copy exact Brain chunks into a bounded sentence queue backed
by one managed local GPT-SoVITS worker. Correlation metadata crosses NDJSON,
while validated PCM WAV uses private fd3 framing and preload-owned Web Audio.
Preload routes every reply clip to the saved speaker selection before decoding;
a missing selected device fails that clip instead of falling back to another
speaker. React never receives the audio or private voice configuration; it
sees only closed, sanitized playback status used to present `SPEAKING` and to
settle the exact Voice turn. The independent
loopback-only Python adapter and repeated/multi-emotion smoke remain available
for diagnostics.
Electron is frozen as the production
shell. The Tauri source and toolchain were removed after the comparison; the
rationale, recorded measurements, and revisit gates are in
[`docs/decisions/0001-desktop-shell.md`](../docs/decisions/0001-desktop-shell.md).

## Development

Prerequisites:

- The repository Python virtual environment exists at `.venv`.
- Ollama is running and the model configured in the root `.env` is installed.
- Local STT additionally requires `requirements-stt.txt` and a complete model
  directory at `models/weights/faster-whisper/<model>`; neither is installed or
  downloaded automatically.
- Optional desktop reply playback and Python synthesis require a separately installed GPT-SoVITS
  runtime, checkpoints/reference audio under the ignored
  `models/weights/gpt-sovits/` tree, and an ignored
  `workspace/settings/voice-profiles.json` catalog. None is downloaded,
  committed, or packaged by this project, and none is required to run the
  text-only desktop UI.
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

To enable the default `small` speech model on CPU, install the optional runtime
from the repository root and place the model before starting Electron:

```bat
cd /d D:\Elysia_AI
.venv\Scripts\python.exe -m pip install -r requirements-stt.txt
```

The model must be a complete local Faster-Whisper directory at
`models\weights\faster-whisper\small\`. Other selectable directories are
`tiny`, `base`, `medium`, `large-v3`, and `turbo`. These weight directories are
Git-ignored and must not be committed or packaged with the application.

## Interface

- Open **Settings** or press `Ctrl+,` to manage the default Ollama model and
  origin, Memory limits, file import size, local STT model/device/language, and
  appearance. Backend values are atomically stored in
  `workspace/settings/global.json` and apply after a Backend restart;
  appearance remains in this device's renderer storage and applies immediately.
- The Voice section selects a system-default or exact microphone and speaker,
  reports Windows microphone access, and runs short local tests. Desired opaque
  device IDs are stored separately in `workspace/settings/audio-device.json`;
  device labels, permission state, availability, and test audio never enter
  Python.
- In a Chat, **Start voice** and the phone button open the Voice capture page
  and binds the Session to the exact active Chat and optional Project without
  requesting microphone access. Only **Start microphone** begins one bounded
  capture. The renderer downmixes and resamples input, and local VAD waits for
  valid speech before sending temporary 16 kHz mono `s16le` PCM to Python once.
  A successful recognition displays an editable **Final transcript**. **Send
  transcript** explicitly submits it through the normal durable Chat path;
  **Use transcript in message** or **Append transcript to message** only updates
  the existing Composer draft. Direct Voice submission preserves that draft
  and does not attach files staged in the Composer. Only after this explicit
  Voice submission may the app open a reply-time interruption monitor.
- The Voice surface presents the closed `IDLE → LISTENING → TRANSCRIBING →
  THINKING → SPEAKING → IDLE` lifecycle as ready, capture/transcription
  progress, **Elysia is thinking**, and **Elysia is speaking**. During a sent
  Voice reply it also presents **Listening for interruption** and
  **Interrupting Elysia**. Confirmed sustained speech rolls the controller to a
  new epoch and `LISTENING`; old-epoch callbacks cannot reopen or settle the new
  turn. A normal reply still returns to idle only after Chat and optional
  playback both finish, and it does not start another capture by itself.
- Settings shows Global defaults beside the active Project's inheritance and
  the active Chat's pinned model. Speech recognition selects
  `tiny` / `base` / `small` / `medium` / `large-v3` / `turbo`,
  `auto` / `cuda` / `cpu`, and `auto` / `zh` / `en`. Backend-backed changes
  clearly request a restart before they are reported as active.
- Voice and Settings translate sanitized readiness enums into recovery actions.
  Missing optional dependencies or a model, unavailable CUDA, and initialization
  failures never expose model paths, native exception text, or library details
  to React. `auto` may fall back to CPU; a real CPU transcription smoke path has
  passed, while this guide makes no claim of successful real-GPU validation.
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
  assignment, archive, and restore. Managed sentence playback is available for
  ordinary Chat replies when its ignored local runtime and Profile are valid;
  reply-time barge-in is available when verified echo cancellation starts.
  Hands-free continuation after a normal reply, Work permissions, and later
  file-processing controls remain unavailable.

## Manual bounded Voice Session and barge-in smoke test

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

Install `requirements-stt.txt`, place a complete model in the matching
`models\weights\faster-whisper\<model>\` directory, then select that model and
**CPU only** under **Settings → Speech recognition**. Save and restart the
Backend. Open a Chat, enter a short draft if you want to exercise append, choose
**Start voice**, and then choose **Start microphone**. Speak and pause for about
0.6 seconds.

Confirm that an editable **Final transcript** appears and that no request is
sent before an explicit action. First choose **Use transcript in message** or
**Append transcript to message**, and confirm that the Composer contains the
edited text without sending it. Repeat the capture, edit the result, and choose
**Send transcript**. The surface must show **Elysia is thinking**, the message
and streamed reply must appear in the same bound Chat, and any existing
Composer draft and staged attachment must remain unchanged. With
`voice.speech`, the surface must show **Elysia is speaking** and return to
**Ready to listen** only after both Chat and playback finish. Without that
capability, text must still finish and return the Session to idle.

For barge-in, send another reviewed Voice transcript and speak a sustained
phrase while the UI shows either **Elysia is thinking** or **Elysia is
speaking**. With verified WebRTC echo cancellation, the surface must show
**Listening for interruption**, then **Interrupting Elysia**, stop only that
reply's playback/TTS and Chat stream, and continue the new utterance in
`LISTENING`. Review and explicitly send the resulting Final Transcript; it is
not submitted automatically. If the browser cannot verify echo cancellation,
the monitor must release the microphone, show a safe warning, and allow the
current reply to continue. This is a manual acceptance checklist; this guide
does not claim that it has already passed across real microphone, speaker, and
room-echo combinations.

Repeat once while silent for about 10 seconds, once with **Cancel capture**,
and once with **Cancel transcription**; none may add a Chat-history Turn.
Close Voice during playback and confirm that playback for that request stops
without a late status reopening the Session. Switching Chat or Project must
also invalidate the previous Voice Session. The page shows only generic
progress, not partial recognized text. Stop immediately if Windows reports
that microphone access is denied.

## Manual local synthesis and playback smoke tests

Prepare a GPT-SoVITS runtime and assets that you have the right to use, copy
`config\voice_profiles.example.json` to the ignored
`workspace\settings\voice-profiles.json`, and replace the example values with
accurate local relative paths, reference text, and language. The adapter
accepts only loopback-IP HTTP origins (`localhost` is normalized to
`127.0.0.1` before I/O), supports WAV/AAC for this non-streaming API, and
resolves model/reference assets only under `models\weights\gpt-sovits\`.

If the Profile is marked `local-evaluation-only`, leave
`GPT_SOVITS_ALLOW_LOCAL_EVALUATION=False` until you have explicitly confirmed
its rights status and paths; then opt in locally without committing `.env`.
For desktop playback, keep the loopback API stopped: Electron's Python Backend
starts the worker itself from `models\cache\GPT-SoVITS-v2-240821`. Launch the
desktop normally, send a Chat message that produces several sentences, and
confirm that each reply sentence plays once in order while the exact text is
still persisted. Closing the window and exiting must stop the current clip and
release the Backend and managed worker; after restarting the app, a new Chat
reply must be playable again. Reloading discards the current clip; after the
replacement private playback owner registers, later replies must play again.
Text Chat remains usable throughout either lifecycle.

For the independent loopback adapter smoke, start the configured API and run
from CMD:

```bat
cd /d D:\Elysia_AI
.venv\Scripts\python.exe scripts\smoke_gpt_sovits.py --profile default --emotion neutral --emotion happy --emotion sad
```

The command synthesizes one fixed Chinese sentence twice per emotion and keeps
the audio in memory. Success output contains only readiness, format, byte
count, duration, and SHA-256 metadata; the digest can fingerprint known bytes
and is not anonymization. `service_binding_unverified` means the
loopback API is reachable but cannot attest that the catalog-declared weights
are loaded; it must not be read as model-identity verification. Stopping the
runtime must produce the stable `service_unreachable` error. See the root
[`README.en.md`](../README.en.md) for the current acceptance-runtime CMD example
and the full configuration boundary.

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
Neither output contains the GPT-SoVITS runtime, Voice Profile catalog, model
weights, or reference audio.

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
- The independent Python GPT-SoVITS adapter remains outside the desktop
  protocol: it permits only loopback-IP HTTP, ignores environment proxies,
  rejects redirects and retries, and accepts only bounded, length-declared
  identity WAV/AAC responses. Desktop reply playback instead owns one guarded
  local worker, a bounded FIFO, and an inherited fd3 binary pipe. Main and
  Preload validate the canonical WAV before Web Audio playback; that private
  byte-delivery IPC is not part of public `DesktopApi`. Renderer receives only
  request ID, Chat ID, closed `playing|played|skipped` plus sequence, or terminal
  `completed|cancelled` status. WAV bytes, clip tokens, hashes, text, paths,
  prompts, diagnostics, and native errors never enter React. A validated,
  exact Request-ID-and-Chat-ID `stopSpeechPlayback` method permits hang-up or
  barge-in after Chat text ownership has ended. On confirmed interruption the
  Renderer also cancels the exact Chat request; duplicate, late, and mismatched
  ownership cannot stop another turn. The managed
  saved output-device selection is applied before each Web Audio decode; an
  unavailable explicit sink skips that clip instead of leaking it through the
  system default speaker. The managed runtime's current partial manifest proves launch consistency, not complete
  supply-chain provenance, so desktop speech caching remains disabled.
- Settings accepts an exact non-sensitive allowlist, including the closed STT
  model/device/language enums, uses optimistic revisions
  and atomic replacement, and remains repairable after Backend initialization
  rejects a saved model or Ollama origin.
- Audio-device preferences use an independent optimistic revision and remain
  repairable while Chat generation is active or Brain initialization has
  failed. Electron owns hardware enumeration, Windows permission state, and
  immediate resource cleanup when a test, capture, or visible context ends.
  Microphone and speaker-selection permissions are limited to the trusted main
  renderer. Opening the Voice page does not request microphone access; capture
  begins only from the user's explicit control. After **Send transcript**, the
  reply-time monitor requires verified WebRTC echo cancellation and sustained
  local VAD; inability to verify the track fails closed instead of risking a
  self-interruption. Accepted PCM exists only during the correlated local
  transcription request. If an interrupted utterance must wait for the old Chat
  terminal, its bounded PCM remains in memory for no more than 10 seconds and
  is wiped on timeout, hang-up, Voice close, Chat/Project switch, or another
  privacy boundary. The final result contains bounded text and safe language
  metadata, never PCM, model paths, or native diagnostics. Neither process
  persists audio. Capture and transcription alone never create a Chat Turn;
  each Final Transcript still requires the user's explicit send confirmation.
- Native selection and drop paths remain inside the trusted preload/Electron
  boundary. Python copies validated regular files into opaque, scope-specific
  storage, and protocol responses expose only safe metadata and attachment IDs.
