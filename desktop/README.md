# Elysia Desktop

The Stage 6 desktop foundation connects a React + TypeScript interface to the
existing Python Brain through an Electron-owned child process and a strict,
versioned local protocol. The first Stage 7 Voice slice adds host-local
microphone and speaker selection, native permission status, and bounded input
and output tests without retaining audio. The current Composer exposes
**Dictate** for one explicit utterance: the renderer downmixes and resamples
input to 16 kHz mono `s16le`, local VAD submits only valid speech transiently
for local Faster-Whisper transcription, and the final text is appended to the
current message draft without sending it. **Voice Call** is a separate,
utterance-based conversation loop. Opening it starts listening immediately;
each complete utterance is locally transcribed and automatically submitted
through the current Chat's normal durable path. A renderer-local controller
binds the exact Chat and optional Project and owns
the closed `IDLE → LISTENING → TRANSCRIBING → THINKING → SPEAKING → IDLE`
lifecycle without owning audio bytes. The renderer also has persistent Chat and Project
surfaces, resilient streamed message actions, revisioned Settings, Chat
attachments, Project source storage, semantic design tokens, system/light/dark
themes, keyboard and screen-reader navigation, durable per-Chat drafts,
renderer-refresh stream recovery, and consistent loading, empty, error,
offline, and fatal states. The speech-recognition boundary still exposes final
transcripts only; it does not stream partial recognition. After Voice Call
automatically submits an utterance, a dedicated
reply-time monitor can accept sustained user speech while the turn is thinking
or speaking. It requires WebRTC echo cancellation both as an exact constraint
and as a verified track setting; otherwise it fails closed and the reply
continues. Confirmed speech stops exact playback and managed synthesis, requests
exact Chat cancellation, advances the Voice epoch, and continues the new
  capture in `LISTENING`. After the exact Chat turn settles, an unmuted call
  begins the next utterance capture; a safely terminal Chat, STT, microphone,
  or playback failure uses a fresh owner rather than reviving the failed turn.
  A failed automatic send is restored to the Composer draft before listening
  resumes whenever local draft storage is available. Mute pauses listening and
  Close ends the call; context changes prevent stale ownership from reopening
  the microphone. This is
not gapless audio streaming, and real-time partial transcripts remain future
work. Ordinary
Chat replies now copy exact Brain chunks into a bounded sentence queue backed
by one managed local GPT-SoVITS worker. Correlation metadata crosses NDJSON,
while validated PCM WAV uses private fd3 framing and preload-owned Web Audio.
For every admitted reply clip, the trusted preload reads and validates the
active speech volume, routes to the saved speaker selection, and applies a Web
Audio gain before playback. A missing selected device fails that clip instead
of falling back to another speaker, while zero volume intentionally silences
playback without disabling synthesis. React never receives the audio or private voice configuration; it
sees only closed, sanitized playback status used to present `SPEAKING` and to
settle the exact Voice turn. The independent
loopback-only Python adapter and repeated/multi-emotion smoke remain available
for diagnostics.
The Stage 8 knowledge slice now connects Project Sources to the same desktop
chain. The Project surface can add, replace, reindex, rebuild, revoke, and
delete through durable lifecycle operations with bounded cancellation and
explicit recovery. Verified-original export is a separate, non-journaled task:
it shares the global Knowledge lease, persists private cleanup intent, and does
not expose a Renderer Stop action. A Project Chat uses those sources only when
**Use Project Sources** is explicitly enabled for that turn or retry. The opt-in
is renderer-memory state bound to the exact Chat/Project pair; reload, moving
the Chat, or archiving the Project resets it. Python derives the exact corpus from the canonical Chat-to-Project
relationship, persists structured proof with the Assistant message, and React
shows statement kinds plus path-free citation details. No workspace directory
is scanned or imported automatically.
The Stage 13 Character State API is renderer-local and closed over `idle`,
`listening`, `thinking`, `speaking`, `working`, `waiting_approval`, and `error`.
It projects current-Chat generation, current-Project Knowledge activity, Voice's
primary lifecycle, and Backend failure into the Character Panel and call dialog.
The contract adds no IPC and never selects a model path or arbitrary animation.
Main Chat renders reviewed static half-body artwork, while Voice Call renders a
centered circular avatar; both are selected by that state and the user-controlled
`neutral / happy / sad` emotion. They do not
load Cubism, create a WebGL context, expose an Animated/Still preference, or
derive mouth cues from Web Audio RMS. The same active emotion selects the local
TTS reference after a Backend restart, while artwork failure falls back through
expression → state → portrait → accessible text. `waiting_approval` still has
no producer until a real Work/Approval workflow exists.
Stage 13 separately provides an optional external Desktop Pet program. It is
Off (`disabled`) on first run. Settings lets the user choose a local folder,
scans for complete trusted companion-program installations, and lets the user
select one detected program before **Visible** can be enabled. The purchased
reference pack is credited to `@书呆儿`; it is never copied into the repository,
uploaded, or packaged. Settings links to the purported free pack at
<https://pan.quark.cn/s/cb5d84acad8e>, but this project has not verified that
link's current contents or terms.
The primary **Choose desktop-pet program folder…** control accepts an
already-owned paid or free pack through the native picker; downloading the free
pack is optional. The paid files stay in that folder, which remains Git-ignored,
and the absolute selected path remains private to Electron Main.
Electron Main owns the strict `disabled / hidden / visible` preference and at
most one child program that Elysia itself launched. **Visible** starts the
selected executable in its own directory, allowing the original program to
provide its mouse, keyboard, eye-tracking, expression, position, and appearance
behavior. **Hidden** stops that owned process while retaining both Elysia's
selection and the companion program's own settings. Switching selections waits
for the old owned process to exit before starting the new one. Elysia never
copies or modifies program binaries, model files, or `config.json`, and never
terminates another copy merely because it has the same executable name.
Stage 13 Presence and Notifications is also Main-owned and fully off by
default. Settings can independently enable fixed-copy reply-ready notices and
select a neutral Daily / Weekly reminder, then disable both with one action.
Main creates silent operating-system notifications only from reviewed local
copy; the Renderer cannot supply titles, bodies, links, sounds, urgency, or
arbitrary schedules. Reminder timers run only while Elysia is already open,
stay quiet while the main window is still visible or Chat, Voice, an undrained
managed reply speech turn, or Knowledge is busy, and consume a suppressed interval instead of
catching up later. There is
no background task, startup entry, cloud push, engagement streak, or behavior
tracking.
Electron is frozen as the production
shell. The Tauri source and toolchain were removed after the comparison; the
rationale, recorded measurements, and revisit gates are in
[`docs/decisions/0001-desktop-shell.md`](../docs/decisions/0001-desktop-shell.md).

## Development

Prerequisites:

- The repository Python virtual environment exists at `.venv`.
- Ollama is running and the model configured in the root `.env` is installed.
  Project Source indexing additionally requires the fixed
  `qwen3-embedding:0.6b` artifact; neither model is downloaded automatically.
- Local STT additionally requires `requirements-stt.txt` and a complete model
  directory at `models/weights/faster-whisper/<model>`; neither is installed or
  downloaded automatically.
- Optional desktop reply playback and Python synthesis require a separately installed GPT-SoVITS
  runtime, checkpoints/reference audio under the ignored
  `models/weights/gpt-sovits/` tree, and an ignored
  `workspace/settings/voice-profiles.json` catalog below the active data root. None is downloaded,
  committed, or packaged by this project, and none is required to run the
  text-only desktop UI.
- Run all npm commands from the `desktop` directory.

Start the complete development desktop once from one CMD window:

```bat
cd /d D:\Elysia_AI\desktop
npm run dev
```

This command compiles Electron Main/Preload, starts Vite, and launches Electron
after the Renderer is ready. Electron then starts
`D:\Elysia_AI\.venv\Scripts\python.exe`, runs `desktop_backend.py`, and stops
that child process when the app quits.

`npm run dev:renderer` starts only the Vite browser preview. It does not inject
the Electron Preload and therefore cannot connect Python, microphone features,
or the Desktop Pet. Use the Electron window opened by `npm run dev` for the
complete application.

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
  appearance, including the optional Desktop Pet. Backend values are atomically stored in
  `workspace/settings/global.json` below the active data root; each control identifies whether a saved
  value is live or waits for a Backend restart. Theme remains in this device's
  Renderer storage and applies immediately; Desktop Pet intent instead uses
  Main-owned revisioned storage under Electron `userData`. If Voice contains a
  non-empty Final transcript, `Ctrl+,` asks before discarding it.
- **Data & storage** remains available even when Python is stopped. Electron
  Main shows the active versioned data root and a bounded category scan, uses a
  native picker for an empty move destination, verifies a staged copy before
  restarting Python there, and rolls back to the old root if readiness fails.
  Generic cleanup is limited to application-owned temporary audio, Cache, and
  Logs; Chats, Projects, Memory, Settings, Sources, indexes, models, and
  external Ollama data have no raw delete action. Old or failed-move copies that
  cannot pass exact deletion checks are journaled and shown across restarts as
  Recovery Copies for manual review. See
  [`docs/13-PRODUCTION-DATA-LAYOUT.md`](../docs/13-PRODUCTION-DATA-LAYOUT.md).
- The Voice section selects a system-default or exact microphone and speaker,
  reports Windows microphone access, and runs short local tests. Desired opaque
  device IDs are stored separately in active-root `workspace/settings/audio-device.json`;
  device labels, permission state, availability, and test audio never enter
  Python.
- In a Chat, the left microphone is **Dictate**. Pressing it begins one bounded
  capture; the renderer downmixes and resamples input, local VAD waits for valid
  speech, and one temporary 16 kHz mono `s16le` payload is sent to Python. A
  successful final transcript is appended to the current Composer draft and is
  never sent automatically.
- The phone button is **Voice Call**. It opens a rectangular modal bound to the
  exact active Chat and optional Project and starts listening immediately. Each
  completed utterance is transcribed locally and automatically submitted through
  the normal durable Chat path. Voice submission preserves the existing Composer
  draft and does not attach files staged there. During the reply, the app may use
  the guarded interruption monitor; after the Chat turn settles, an unmuted call
  resumes listening for the next utterance. Recoverable microphone and STT
  failures retry with a fresh owner, and failed automatic sends preserve the
  transcript in the Composer draft before listening resumes when storage allows.
- Voice Call announces ready, opening, listening, speech detected,
  transcription, **Elysia is thinking**, **Elysia is speaking**, interruption,
  cancellation, and safe failure states. A live timer and centered circular
  Elysia avatar are followed by exactly two circular icon controls:
  **Mute/Unmute** and **Close voice**. Their names appear through hover tooltips
  and accessible labels rather than permanent visible text.
- The optional Character Panel and Voice portrait consume the same semantic
  Character State contract. Closed registries map each state and the active
  `neutral / happy / sad` user setting to reviewed cells; no model output can
  select an emotion, path, cell, or animation name. The same active emotion
  value also selects the TTS reference. Both in-app surfaces are permanently
  static: they mount no Live2D model, WebGL context, render loop, Animated/Still
  control, audio analyser, or mouth-cue path. Failures follow expression → state
  → portrait → accessible text without affecting Chat or Voice. These surfaces
  do not write Chat state or create a new Backend capability.
- Desktop Pet settings are separate from Backend settings. A missing preference
  file is first-run state and defaults to **Off** (`disabled`). The user must
  choose a folder containing supported desktop-pet programs, select one
  detected program, and explicitly choose **Visible**. **Visible** launches the
  selected local program. **Hidden** stops the process Elysia launched while
  preserving its selection and the program's own settings. **Off** leaves the
  feature disabled until the user opts in again. Position, scale, input mode,
  eye tracking, expressions, and other pet behavior remain owned by the
  companion program and its own interface.
- The reviewed Bongo Cat Mver launcher declares administrator elevation, but
  Elysia runs that exact pinned child with Windows `RunAsInvoker`. The unsigned
  companion receives no administrator rights, and Main retains the owned PID
  tree required to stop or switch it safely. Input sent to a separately
  elevated application remains subject to normal Windows integrity isolation.
- Main stores Desktop Pet intent in a strict version-3 revisioned JSON document
  under Electron `userData`; executable paths remain private to Main. Settings
  receives only a folder name, sanitized program summaries, scan status, and
  the selected opaque ID. A bounded scan accepts only the reviewed program
  layout and pinned executable/DLL identities with complete Standard, Keyboard,
  and Gamepad model profiles. External program and model files stay in place
  and are never copied, modified, committed, uploaded, or included in an
  application package.
  Immediately before every launch, Main repeats the link, canonical-path,
  boundary, required-structure, size, and full pinned-hash checks. This second
  validation rejects changes already present when it runs instead of trusting
  stale discovery results, and shortens but does not eliminate the remaining
  check-to-use interval. Launch remains path-based rather than bound to a
  verified file handle or immutable file identity. A missing, corrupt,
  oversized, invalid, or legacy
  preference without a selected program fails closed to `disabled`; missing,
  changed, or unsupported folders become sanitized recoverable warnings without
  affecting Chat, Voice, or the Python Backend. The process manager serializes
  starts, switches, stops, and shutdown, targets only the exact child PID Elysia
  owns, and bounds native termination. Shutdown also drains already admitted
  preference writes without writing the external program's `config.json`.
- **Presence & notifications** is a separate immediate Settings section owned
  by Electron Main. **Reply completion notifications** and **Neutral presence
  reminders** both default to Off; reminder frequency accepts only Off, Daily,
  or Weekly, and **Turn all off** replaces both choices in one revision-checked
  update. These settings neither depend on Python readiness nor require a
  Backend restart.
- Reply completion observes only a validated terminal `chat-complete` for a
  user-started Chat or Retry, requires the main window to be unattended, and is
  suppressed while Voice, an undrained managed speech turn, or Knowledge work is active.
  The native notification is silent and uses exactly `Elysia` / `Your local
  reply is ready.` It never includes reply text, prompts, Chat or Project names,
  filenames, Memory, or model output. Cancellation, errors, streaming chunks,
  transcription, and Knowledge progress do not create notices.
- Neutral reminders use a complete 24-hour or seven-day interval anchored when
  the preference is enabled or changed. Delivery requires Elysia to be running,
  the main window to be absent, hidden, or minimized, the Backend to be ready,
  and Chat, Voice, managed speech (including synthesis gaps and final playback
  drain), and Knowledge work to be idle. A due
  cycle suppressed by a still-visible window or busy activity is recorded as
  handled instead of appearing after the window is later hidden or the activity
  becomes idle; no reminder task runs while Elysia is closed. Their exact silent
  copy is `Elysia` / `Open
  Elysia whenever you are ready.` Clicking either notice only reveals and
  focuses the ordinary main window.
- **Mute** immediately ends and discards a live capture or held interruption
  PCM and disarms reply monitoring; it does not cancel an already-running text
  reply. **Unmute** restores the listening mode appropriate to the current call
  phase. **Close voice** or `Escape` releases the Voice Call's capture,
  transcription, monitoring, and exact managed playback, while an
  already-running Chat reply may continue on the Chat page.
- Voice Call has no manual-review, caption, audio-settings, or Auto-continue
  controls. While unmuted it returns to capture only after both the exact Chat
  request and any expected speech finish successfully. Cancellation, failure,
  Close, or a Chat/Project change invalidates the pending continuation. Every
  new capture still ends at one Final Transcript boundary before automatic Chat
  submission; this is an utterance-based loop, not partial or gapless streaming.
- Settings shows Global defaults beside the active Project's inheritance and
  the active Chat's pinned model. Speech recognition selects
  `tiny` / `base` / `small` / `medium` / `large-v3` / `turbo`,
  `auto` / `cuda` / `cpu`, and `auto` / `zh` / `en`. `cpu` is the default so
  co-resident Ollama and GPT-SoVITS retain bounded GPU headroom; measured hosts
  may explicitly opt in to `auto` or `cuda`. Backend-backed changes clearly
  request a restart before they are reported as active.
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
- Use the small plus beside the model selector, or drag and drop onto the composer, to stage files for the exact active Chat.
  Chat attachments remain message-scoped and are not implicitly promoted into
  a Project corpus. Selection cancellation is a no-op, failed sends keep the
  Chat draft, and removing a file never affects another Chat or Project.
- Use **Project Sources** to add supported local documents through the native
  picker. The production Python pipeline verifies, parses, cleans, chunks,
  embeds, and indexes those files, then publishes a complete Project catalog
  last. The surface exposes source health, durable progress, cancellation,
  recovery, export, and archived read-only state without returning native
  paths to React.
- After a native export destination is accepted and the Python request starts,
  Electron owns that export across Renderer reloads and Project switches. Its
  Backend snapshot keeps every Project Source and Project-authority mutation
  disabled until the global Knowledge lease is released; **Stop current** stays
  disabled because export is not cooperatively cancellable from React. Success
  is published only after the path-free receipt matches the authenticated
  Source name, media type, and byte size. The destination path remains inside
  Electron Main and Python.
- In a Chat assigned to that Project, turn on **Use Project Sources** before
  Send or Retry to request a grounded answer. This per-window choice resets on
  reload and whenever the Chat/Project authority changes. The result labels direct source
  facts, model summaries, and inferences and exposes expandable citation
  details. It does not open a source preview or jump into a PDF/DOCX page,
  block, or cell.
- Navigation becomes a modal drawer at narrow CSS widths, including high
  Windows display or Electron zoom levels. The Composer remains in normal
  layout flow so attachments, alerts, and multiline input cannot cover the
  final message. The compact character panel is also modal, traps focus, and
  has its own close control.
- Projects support persisted metadata, instructions, workspace binding, Chat
  assignment, archive, and restore. Managed sentence playback is available for
  ordinary Chat replies when its ignored local runtime and Profile are valid;
  reply-time barge-in is available when verified echo cancellation starts.
  Work permissions, automatic workspace scanning, and background source import
  remain unavailable.

### Global Voice behavior settings

Settings exposes five effective Voice behavior values. **Live after Save**
means that a newly admitted operation observes the saved value without
restarting the Backend; it does not mean that Save can rewrite work already in
flight. Two settings are live and three require a restart:

| Settings control (`global.json` field) | Default | Valid value | Apply boundary | Contract |
| --- | --- | --- | --- | --- |
| **Read replies aloud** (`autoReadAloud`) | On | On / Off | Live after Save | Off preserves text replies but does not start new managed speech. |
| **Speech rate (%)** (`speechRatePercent`) | 100 | 50–200 | **Backend restart required** | The active synthesis rate remains unchanged until restart. |
| **Speech volume (%)** (`speechVolumePercent`) | 100 | 0–100 | Live after Save | Trusted preload applies the active value to each admitted clip; 0 silences playback without disabling synthesis. |
| **Voice profile** (`voiceProfileId`) | `default` | Configured logical Profile ID | **Backend restart required** | The active Profile remains unchanged until restart. |
| **Voice emotion** (`voiceEmotion`) | `neutral` | `neutral` / `happy` / `sad` | **Backend restart required** | The same active closed value selects the local TTS reference and reviewed static expression; model output cannot override it. |

Older settings documents and Protocol v1 shapes can still contain
`captionsEnabled`, `transcriptReviewMode`, and `automaticRelisten`. They are
retained only for lossless migration and wire compatibility; the current UI
does not expose them, and they do not change Dictate or Voice Call behavior.

## Manual Voice UI and real-device acceptance checklist

On 2026-09-22, the project owner explicitly waived this manual device matrix as
a requirement for closing the Voice UI and Settings delivery. The rows below
remain marked pending as an honest record that the human observations were not
performed; they are optional future validation and must not be represented as
passed. The production-chain and automated evidence recorded later in this
section is the accepted engineering gate for this delivery.

Use Command Prompt, not PowerShell, and start the complete desktop once:

```bat
cd /d D:\Elysia_AI\desktop
npm run dev
```

The command compiles Electron, starts Vite, launches the Electron window, and
owns both child lifecycles. Do not use the Renderer-only `npm run dev:renderer`
preview for this acceptance path because it has no Python, microphone, or
Desktop Pet integration.

Install `requirements-stt.txt`, place a complete model in the matching
`models\weights\faster-whisper\<model>\` directory, then select that model and
**CPU only** under **Settings → Speech recognition**. Save and restart the
Backend. Open a Chat, enter a short draft if you want to exercise append, choose
**Dictate** or **Voice Call**, and use the rows below as the test script.

These rows state expected behavior, not completed results. Every result is
deliberately **Pending — manual run required** until a tester observes it on the
target Windows microphone, speaker, room, and installed local models. Stop
immediately if Windows reports that microphone access is denied.

| Exercise | Expected result | Result |
| --- | --- | --- |
| Press **Dictate**, speak, and pause. | Status progresses through local capture and transcription; the final text is appended to the existing Composer draft, no Chat request is sent, and no partial recognition text is exposed. | Pending — manual run required |
| Open **Voice Call**. | The rectangular modal binds the exact Chat/Project, its timer advances, and capture begins immediately. A centered circular Elysia avatar appears above exactly two icon-only controls whose hover/accessibility names are **Mute/Unmute** and **Close voice**. | Pending — manual run required |
| Speak one complete utterance in Voice Call and pause. | The status progresses through listening, speech detected, transcription, and thinking. The Final Transcript is automatically submitted through the normal durable Chat path while the pre-existing Composer draft and staged files remain unchanged. No partial recognition text or transcript-review UI appears. | Pending — manual run required |
| Let one Voice reply and any expected playback finish. | The Session settles both Chat and speech ownership, then an unmuted call automatically begins one new utterance capture. | Pending — manual run required |
| Press **Mute** during normal capture and again during reply-time/held interruption capture, then press **Unmute**. | Mute closes the microphone, discards admitted or held PCM, and disarms monitoring without cancelling a running text reply. Unmute resumes the listening mode appropriate to the current phase. | Pending — manual run required |
| Interrupt, change Chat/Project, or cause a Chat/speech failure before clean completion. | No stale callback or pending continuation reopens the microphone. An open, unmuted call may instead start one freshly owned capture after the failed turn settles; a failed outgoing transcript is restored to the Composer draft when storage permits. | Pending — manual run required |
| Speak a sustained phrase while thinking or speaking on hardware whose track reports echo cancellation enabled. | The UI shows interruption listening and then interruption, stops only that exact Chat stream and speech owner, rolls to a new listening epoch, and rejects late callbacks from the old epoch. The completed new utterance is automatically submitted through normal Chat. | Pending — manual run required |
| Repeat where WebRTC echo cancellation cannot be verified. | Monitoring fails closed, releases its microphone, shows a safe warning, and permits the current reply to continue. | Pending — manual run required |
| Repeat interruption attempts in quiet, speaker-echo, and headset conditions, recording trial count, false triggers, and observed interruption delay. | Verified AEC must not let Elysia's own playback trigger interruption; sustained user speech should interrupt without cancelling another turn. Record the measurements instead of replacing them with an automated simulation. | Pending — manual run required |
| Stay silent in Voice Call, then choose **Mute** and **Close voice** in separate runs. | Silence does not create an empty Chat turn. Each control releases its owned capture without retaining audio. | Pending — manual run required |
| Close Voice Call during managed playback, then switch Chat or Project in a separate run. | Exact playback and Voice-owned audio stop; the old Session cannot be reopened by late events. A text reply already in progress may continue in its Chat. | Pending — manual run required |
| Keep one Voice Call open for a recorded duration and complete several capture, reply, mute, and interruption cycles before closing. | Resource use remains bounded, the Windows microphone indicator turns off after Close, no background capture or playback remains, and opening a fresh call still works. | Pending — manual run required |
| Enable Windows Narrator or another screen reader and exercise Dictate, capture, transcription, thinking/speaking, Mute/Unmute, one recoverable error, and Close. | Controls have understandable accessible names and focus order; primary lifecycle and microphone status are announced without contradictory or repeated status floods. | Pending — manual run required |
| Save each of the two live Voice settings, exercising a newly admitted operation after every Save. | Read-aloud and per-clip volume reflect the saved value without a Backend restart. Volume 0 is silent while the text reply still completes. | Pending — manual run required |
| Save a different speech rate, Voice Profile, and Voice Emotion without restarting, then restart the Backend. | All three controls report restart-required; active behavior stays at the old values before restart. After a successful restart, the closed emotion changes both the TTS reference choice and reviewed static expression. | Pending — manual run required |

For every real-device run, record the following fields together with the table
results. A generic “passed” without this environment information is not enough
to close the hardware acceptance gate.

| Acceptance record field | Recorded value |
| --- | --- |
| Date and tested Git commit | Pending — manual run required |
| Windows version | Pending — manual run required |
| Microphone and connection type | Pending — manual run required |
| Speaker/headset and connection type | Pending — manual run required |
| Room and echo condition | Pending — manual run required |
| Ollama model, STT model/device, Voice Profile, and Voice Emotion | Pending — manual run required |
| Session duration and completed Voice-turn count | Pending — manual run required |
| Interruption trials, successful interruptions, false triggers, and observed delay | Pending — manual run required |
| Screen reader and result | Pending — manual run required |

### 2026-09-22 pre-acceptance engineering evidence

The following local checks exercise real hardware and production components,
but do **not** replace the pending human observations in the tables above:

- Windows 11 Pro `10.0.26200.9457` exposed `Microphone (HECATE G1500 BAR)`
  as the default capture endpoint and `Speakers (Realtek(R) Audio)` as the
  default multimedia output. A two-second DirectShow capture reached 44.1 kHz
  stereo input with a `-30.3 dB` mean and `-10.9 dB` peak; audio was sent to a
  null sink and was not saved. Earlier production Electron captures of 2.240,
  1.480, and 1.780 seconds also completed local Chinese transcription.
- A production Electron diagnostic completed the real
  Renderer → Electron → Python Backend → Ollama → managed GPT-SoVITS → private
  fd3 → trusted preload Web Audio route. Main received the private `played`
  settlement after Renderer reported `playing` then `played`, the speech turn
  ended `completed`, Backend remained `ready`, and the `voice.speech`
  capability remained present. This proves successful decode and natural Web
  Audio completion; it does not claim that a person confirmed loudness or
  subjective audio quality in the room.
- The managed `elysia-v2` neutral CUDA worker and `qwen3.5:9b` were exercised
  concurrently. The voice lease became ready in 38.53 seconds, the Ollama
  request completed by 55.81 seconds, and one in-memory 254,764-byte canonical
  WAV completed by 58.73 seconds. No diagnostic audio was persisted.
- Historical failures were traced to model replies containing standalone
  decorative fragments such as `♪` and `✨`. The canonical Chat text remains
  untouched, while the speech-only segmenter now skips a candidate unless it
  contains a Unicode letter or number. Text such as `爱莉希雅♪` and `123！`
  remains intact; focused regression tests cover both retained and skipped
  forms.
- A post-fix production regression made the local model return exactly
  `语音分段回归测试成功。\n♪`. The real application emitted and played only
  speech sequence `0` for the Chinese sentence (162,604 bytes), emitted no
  second symbol-only sequence, settled playback as `played`, and finished
  `playing → played → terminal(completed)` while Backend stayed ready with
  `voice.speech` available. The diagnostic Chat and temporary harness were
  removed after the bounded run.

Room/headset echo trials, human-observed interruption latency and false-trigger
counts, an extended Voice Session, subjective speaker output, and Narrator were
not run. They remain optional future observations after the project owner
waived them as a delivery-closing gate; this engineering evidence must not be
rewritten as though those human checks passed.

### 2026-09-23 performance, cleanup, and distribution acceptance

The final three-component benchmark overlapped `qwen3.5:9b` with the managed
GPT-SoVITS worker on an RTX 4070 SUPER and then ran CPU Faster-Whisper while
both GPU models remained resident. Global GPU use peaked at 9,824 of 12,282
MiB. Three-cycle p50 values were 0.260 seconds to Ollama's first token, 1.291
seconds for 4.06 seconds of synthesized audio, and 1.193 seconds for CPU STT.

Automated soak coverage drains 256 transcription cycles, 256 completed or
cancelled speech turns, and 200 Renderer Voice turns to zero owned jobs,
credits, callbacks, buffers, and active IDs. This is lifecycle evidence, not a
claim that the pending multi-hour human/device row above was performed.

The complete method, per-cycle measurements, Ollama residency explanation,
rights decision, package audit, limitations, and revisit triggers are in
[`docs/03-VOICE-PERFORMANCE-SAFETY-RIGHTS.md`](../docs/03-VOICE-PERFORMANCE-SAFETY-RIGHTS.md).

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
desktop normally with **Read replies aloud** on, send a Chat message that
produces several sentences, and confirm that each reply sentence plays once in
order while the exact text is still persisted. Save several volume values and
admit a new clip after each Save: the trusted preload must apply the current
percentage to that clip, including silence at 0, without exposing its WAV bytes
to React or disabling synthesis. Closing the window and exiting must stop the
current clip and release the Backend and managed worker; after restarting the
app, a new Chat reply must be playable again. Reloading discards the current
clip; after the replacement private playback owner registers, later replies
must play again. Text Chat remains usable throughout either lifecycle. These
are manual expectations and do not assert that a physical speaker run has
already passed.

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
npx --no-install asar list out\win-unpacked\resources\app.asar > "%TEMP%\elysia-asar-listing.txt"
if exist "%TEMP%\elysia-asar-extracted" rmdir /s /q "%TEMP%\elysia-asar-extracted"
call "D:\Elysia_AI\desktop\node_modules\.bin\asar.cmd" extract "D:\Elysia_AI\desktop\out\win-unpacked\resources\app.asar" "%TEMP%\elysia-asar-extracted"
cd /d D:\Elysia_AI
.venv\Scripts\python.exe scripts\check_distribution_assets.py --unpacked-tree desktop\out\win-unpacked --asar-listing "%TEMP%\elysia-asar-listing.txt" --extracted-asar-tree "%TEMP%\elysia-asar-extracted"
del /f /q "%TEMP%\elysia-asar-listing.txt"
rmdir /s /q "%TEMP%\elysia-asar-extracted"
```

`npm run package` creates an unpacked desktop build in `desktop\out`.
On Windows, `npm run make` additionally creates an unsigned NSIS installer.
The final audit scans the actual package tree, its ASAR listing, and the complete
extracted archive. It verifies the required portrait, state, expression, and
speech atlases and application icons, and applies the global external-model and
desktop-pet-program exclusion rules to every extracted path. Exact visual-asset path
allowlists cover Git, Unpacked, and ASAR boundaries, so a renamed standalone
texture cannot bypass the model-suffix checks. Neither
accepted output contains the GPT-SoVITS runtime, Voice Profile catalog, model
weights, or reference audio.

The application PNG and Windows ICO are derived from the official *Honkai
Impact 3rd* Elysia signet at the project owner's express direction for this
unofficial, non-commercial fan project. They are third-party assets, are not
covered by any source-code license, and do not imply HoYoverse / miHoYo
endorsement. See the root `MODEL_LICENSE.md` before publishing a build.

The packaged portrait plus state, expression, and speech atlases are reviewed
generated fan artwork, not source-code-licensed assets. The distribution audit
pins all four by exact path, byte length, SHA-256, ASAR cardinality, and
extracted bytes. The state atlas is a static 4×2 RGB sheet: only its first seven
cells participate in the closed Character State contract, and the eighth
success cell is not a new runtime state. The expression atlas admits only the
user-controlled `neutral / happy / sad` mapping. The speech atlas remains an
authenticated review asset, but the current renderer does not load it or derive
visual mouth cues from playback.
Main Chat and Voice use only the packaged static artwork. The optional Desktop
Pet instead launches one user-selected external companion program in place.
No external program or model pack is part of the repository or package, and
the application does not fall back to a bundled animated character.

`npm run docs:check` enforces file-purpose comments plus public class,
function, class-method, and exported interface-method documentation. The
semantic why/how requirements remain part of review under the root
`AGENTS.md` policy.

`npm test` runs both the shared protocol contract suite and Electron renderer
UI tests, including Knowledge method/event races, export ownership across
Renderer reload and Project switches, trusted receipt settlement, Project
isolation, archived read-only behavior, explicit grounded intent, and citation
accessibility. The contract suite also runs `character-state.test.mjs`,
`character-presentation.test.mjs`, `desktop-pet-lifecycle.test.mjs`,
`desktop-pet-program-library.test.mjs`,
`desktop-pet-program-manager.test.mjs`, `desktop-pet-preferences.test.mjs`,
`presence-native-notification.test.mjs`, and
`presence-notification-preferences.test.mjs`.
Desktop Pet coverage verifies the strict update schema, first-run-disabled and
corrupt-file fail-closed behavior, migration, program selection, bounded
directory scanning, pinned executable and DLL identities, required input
profiles, path/link rejection, revision CAS, atomic replace failure, and
launch-time rejection when a previously scanned trusted file is replaced before
revalidation.
Process manager tests verify single-child ownership, ordered switches, safe
PID-specific termination, startup/shutdown deadlines, failure recovery, and
preservation of the external program's own configuration.
Presence notification
contract coverage verifies the exact
three-field update, fully-off defaults, strict 16 KiB persistence, fail-closed
invalid storage, revision conflicts, no-op and frequency re-anchoring behavior,
atomic replacement failure, private handled-cycle state, clock rollback and
long-timer bounds, opt-in delivery, unattended-window checks, Voice and Backend
busy suppression, and shutdown behavior. Native-slot tests separately verify
completion priority, Windows timeout retention/removal, late callback isolation,
replacement, explicit close, and sanitized delivery failure. The UI suite
verifies revisioned
notification controls, **Turn all off**, failure recovery, and sanitized
unsupported runtime alongside revisioned Desktop Pet Settings, failure/reload
recovery, external-folder selection and rescan, detected-program selection,
and explicit failed-state retry,
current-Chat/Project
scoping, Voice projection, Backend failure,
closed state/emotion artwork, an always-static in-app character surface, and
the expression → state → portrait → accessible-text fallback chain.
`npm run test:ui` can be used independently while working on layout.
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
- Desktop Pet execution remains Main-only. The Renderer can choose only an
  opaque detected-program ID and a closed operating mode; it cannot submit an
  executable path, launch arguments, process ID, shell command, or arbitrary
  configuration.
- Main bounds directory traversal, rejects links and path escape, verifies the
  reviewed launcher and every loadable top-level DLL by exact size and SHA-256,
  and requires complete Standard, Keyboard, and Gamepad model profiles before a
  local installation becomes selectable. It repeats the full structural and
  cryptographic validation immediately before each launch so replacements that
  are already present at revalidation are rejected and the path-based launch
  window is reduced. This defense does not eliminate TOCTOU: process creation
  is not bound to the verified file handle or an immutable Windows file
  identity, so a local writer could still replace a path in the remaining
  interval. Program paths never enter the Renderer or Python protocol.
- Desktop Pet persistence is Main-only: the exact version-3 JSON schema is
  capped at 16 KiB, mode/model changes use optimistic revisions and
  same-directory atomic replacement. Native program paths are excluded from
  public state; the opaque selected program ID is
  deliberately exposed with sanitized summaries so Settings can render and
  update the closed selection.
  Missing or invalid storage defaults to Off; Visible is rejected until a
  validated program is selected. Hidden and Disabled stop only the exact child
  process Elysia launched. Selection changes are serialized so the old child
  exits before the new one starts; failed or timed-out stops retain ownership
  and block replacement instead of leaving two programs running. The child runs
  in its original directory and owns its existing `config.json`; Elysia neither
  rewrites that file nor substitutes an application-managed copy.
- Presence notification persistence is also Main-only and capped at 16 KiB.
  Missing or invalid storage fails closed to reply notifications Off and
  reminder frequency Off; exact-schema updates use optimistic revisions,
  same-path serialization, and same-directory atomic replacement. The last
  handled reminder timestamp remains private to Main and records cadence, not
  engagement.
- Native notification content is fixed in Main, marked `silent`, and limited to
  one fixed-ID/group global notice. Main retains a Windows timed-out handle so
  the next notice, **Turn all off**, or shutdown can remove its Action Center
  entry; replaced handles are detached so late click/failed callbacks are inert.
  React can select only a boolean and the closed `off / daily / weekly` cadence;
  it cannot submit notification copy, a native
  action, link, sound, urgency, or schedule. Renderer notification permission
  remains denied, and native failures publish only sanitized
  `unsupported / failed` state without interrupting Chat, Voice, Work, or the
  Python Backend.
- Presence reminders use an unreferenced in-process timer and register no
  background task or startup entry. Main suppresses them while the window is
  visible or Voice/Backend/an undrained managed speech turn is active, records a
  suppressed due cycle as handled, and closes outstanding notifications during
  shutdown. Admitted preference and cadence-anchor writes use a parallel
  bounded drain so they cannot consume the Desktop Pet's ordered process-stop
  deadline during shutdown. The only
  Renderer activity signal is a trusted boolean indicating whether the visible
  Voice Session is open; it suppresses both optional notification kinds and
  carries no transcript, audio, prompt, or model data.
- Electron validates the exact renderer origin and top frame before handling
  any desktop IPC.
- Electron admits only one application instance, and Python holds an exclusive
  attachment-store lock, so another Backend cannot release or reuse in-flight
  claims.
- Each Python process must complete a version and capability handshake using a
  fresh local session token before Electron marks it connected.
- Python and TypeScript validate the same samples in
  `desktop_protocol/fixtures/v1.samples.json`.
- Knowledge methods use exact Project/source identifiers and closed DTOs.
  Native add/replace/export paths exist only between Electron Main and Python;
  source state, operation events, grounded history, and citations cannot carry
  paths, content hashes, vectors, prompts, or native diagnostic messages. The
  global Knowledge lease serializes lifecycle mutation, verified export, and
  grounded-answer ownership. Electron snapshots an in-flight lifecycle/export
  request for Renderer recovery, treats export as non-cancellable, and emits a
  path-free `knowledge-export-settled` event only after its safe receipt matches
  the Source metadata authenticated before the native Save dialog.
- Project Source generations are authorized only after the complete catalog is
  published. Replace/delete/revoke tombstone authorization before cleanup, and
  the shared production operation lease covers lifecycle work, grounded
  answers, and verified export. A stale or partial catalog fails closed instead
  of becoming an empty corpus.
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
  saved output-device selection and the current validated 0–100 speech-volume
  percentage are applied inside trusted preload for every admitted clip. A Web
  Audio gain node enforces the per-clip volume; zero remains an intentional
  silent playback, and an unavailable explicit sink skips that clip instead of
  leaking it through the system default speaker. The graph does not sample RMS
  for character animation and publishes no mouth cue to React; Main Chat and
  Voice artwork stays static while audio plays. The managed runtime's current
  partial manifest proves launch consistency, not complete supply-chain
  provenance, so desktop speech caching remains disabled.
- Settings accepts an exact non-sensitive allowlist, including the closed STT
  model/device/language enums and Voice behavior fields, uses optimistic
  revisions and atomic replacement, and remains repairable after Backend
  initialization rejects a saved model or Ollama origin. Read-aloud and volume
  are adopted between admitted operations after Save; synthesis rate, Voice
  Profile, and the closed `neutral / happy / sad` Voice Emotion keep their prior
  active values until a Backend restart. Retired caption, transcript-review,
  and automatic-relisten fields are compatibility-only and do not control the
  current UI. The active emotion controls both the TTS reference choice and
  reviewed static expression; model output has no field that can select
  arbitrary character animation.
- Audio-device preferences use an independent optimistic revision and remain
  repairable while Chat generation is active or Brain initialization has
  failed. Electron owns hardware enumeration, Windows permission state, and
  immediate resource cleanup when a test, capture, or visible context ends.
  Microphone and speaker-selection permissions are limited to the trusted main
  renderer. **Dictate** explicitly starts one draft-only capture, while opening
  **Voice Call** explicitly starts its utterance-based listening loop. After a
  Voice Call utterance is automatically submitted, the reply-time monitor
  requires verified WebRTC echo cancellation and sustained
  local VAD; inability to verify the track fails closed instead of risking a
  self-interruption. Accepted PCM exists only during the correlated local
  transcription request. If an interrupted utterance must wait for the old Chat
  terminal, its bounded PCM remains in memory for no more than 10 seconds and
  is wiped on timeout, hang-up, Voice close, Chat/Project switch, or another
  privacy boundary. The final result contains bounded text and safe language
  metadata, never PCM, model paths, or native diagnostics. Neither process
  persists audio. Dictate capture and transcription alone never create a Chat
  Turn; Voice Call deliberately submits a completed Final Transcript through
  the normal Chat path. An unmuted call admits the next capture only after a
  clean exact-turn completion, and Mute, failure, cancellation, Close, or
  context replacement invalidates stale ownership before the microphone can
  reopen.
- Native selection and drop paths remain inside the trusted preload/Electron
  boundary. Python copies validated regular files into opaque, scope-specific
  storage, and protocol responses expose only safe metadata and attachment IDs.
