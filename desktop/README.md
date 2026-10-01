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
capture in `LISTENING`. An opt-in automatic-relisten policy can begin the next
bounded capture only after the exact Chat turn and any expected speech playback
finish normally. Its visible Session control can disable that behavior
immediately, and every resulting transcript still waits for manual review;
failures, cancellation, mute, hang-up, and context changes never silently
reopen the microphone. Real-time partial transcripts remain future work. Ordinary
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
primary lifecycle, and Backend failure into the Character Panel and call page.
The contract adds no IPC and never selects an animation file. Separate closed
registries map those states and the user-controlled `neutral / happy / sad`
emotion setting to reviewed atlas cells; the same active emotion selects the
local TTS reference and the static expression after a Backend restart. The
model cannot submit an emotion, path, cell, or animation command. During real
Web Audio playback, trusted Preload samples RMS at no more than 20 Hz and
quantizes it into `closed / small / medium / wide` mouth cues. Raw waveform
samples and continuous envelopes stay outside React. Animated mode consumes
those cues; Still and OS Reduced Motion never start mouth animation. Assets
fall back in the order speech → expression → state → portrait → accessible
text. This amplitude visualization is neither phoneme-level lip sync nor
Live2D. `waiting_approval` still has no producer until a real Work/Approval
workflow exists.
Stage 13 also provides an optional static Desktop Pet that is disabled by
default. Electron Main owns its strict `disabled / hidden / visible`
preference, private display placement, and at most one transparent,
always-on-top native window. `hidden` destroys the dedicated Renderer instead
of merely making it invisible. The pet reuses the reviewed
`elysia-portrait.png`, can be dragged or temporarily made click-through from
the tray, and opens the ordinary main Chat when clicked. Its separate
sandboxed entry receives only `ready`, `hide`, and `openMainChat` through a
minimal Preload; it has no Backend, network, filesystem, Node, audio, or main
Renderer capability. This is a bounded static 2D surface, not Live2D.
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
  appearance, including the optional Desktop Pet. Backend values are atomically stored in
  `workspace/settings/global.json`; each control identifies whether a saved
  value is live or waits for a Backend restart. Appearance remains in this
  device's renderer storage and applies immediately. If Voice contains a
  non-empty Final transcript, `Ctrl+,` asks before discarding it.
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
- The Voice header shows the bound model and a live Session timer. Separate
  lifecycle and microphone-status regions announce ready, opening, listening,
  speech detected, transcription, **Elysia is thinking**, **Elysia is
  speaking**, interruption, cancellation, and safe failure states. Assistant
  captions are visible only when enabled and can be hidden or shown from the
  Session without changing their saved global default.
- The optional Character Panel and Voice portrait consume the same semantic
  Character State contract. Closed registries map each state and the active
  `neutral / happy / sad` user setting to reviewed cells; no model output can
  select an emotion, path, cell, or animation name. During real speech output,
  trusted Preload applies the active gain, samples Web Audio RMS no faster than
  20 Hz, and publishes only `closed / small / medium / wide` visual cues. The
  static emotion uses the same active value as the TTS reference. Settings
  persists Animated / Still on this device; Still and OS Reduced Motion disable
  mouth animation. Visual failures follow speech → expression → state →
  portrait → accessible text without affecting Chat or Voice. The surface
  does not load Live2D, claim phoneme-level lip sync, write Chat state, or
  create a new Backend capability.
- Desktop Pet settings are separate from Backend settings and default to
  **Off** (`disabled`). **Hidden** keeps the opt-in while destroying the pet
  Renderer and native window; **Visible** creates one transparent, frameless,
  always-on-top 320×480 DIP nominal window. Its drag handle moves the window,
  its close control selects Hidden, and clicking the portrait reveals and
  focuses the main Chat. The tray can show or hide it, temporarily enable
  mouse click-through, reset its position, disable it, or retry a failed
  renderer; Settings also exposes an explicit retry for the failed state.
  Click-through is not persisted. Hidden keeps the process and tray resident
  after the main window closes, while Off restores the normal Windows/Linux
  last-window exit behavior.
- Main stores Desktop Pet intent in a strict revisioned JSON document under
  Electron `userData`; Settings, tray, pet controls, reset, and drag saves use
  one mutation queue, while native display ID and DIP coordinates never enter
  either Renderer. Position restoration clamps the window to current work
  areas across negative-coordinate and mixed-scale displays without applying
  Electron's `scaleFactor` twice. Display topology/metric changes reclamp the
  window. A missing or invalid preference fails closed to `disabled`, while a
  load/crash failure becomes a sanitized recoverable `failed` runtime state
  without affecting Chat, Voice, or the Python Backend. A missing Preload or
  Renderer that never reports ready is destroyed after a 10-second deadline;
  shutdown drains admitted writes before its final position snapshot, with the
  complete optional persistence sequence bounded to two seconds.
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
  reply, and unmuting never opens the microphone by itself. **Hang up** or
  `Escape` releases the Voice Session's capture, transcription, monitoring, and
  exact managed playback, while an already-running Chat reply may continue on
  the Chat page. **Audio settings** follows the same cleanup boundary before it
  opens the device controls. Leaving through any of these paths asks first when
  a non-empty Final transcript would be lost.
- The visible **Auto-continue** control mirrors the saved automatic-relisten
  default when Voice opens and can be turned off during the Session. When it is
  on, a normal reply returns to capture only after both the exact Chat request
  and any expected speech finish successfully. Cancellation, failure,
  interruption, mute, hang-up, or a Chat/Project change invalidates the pending
  continuation. The next Final transcript is still editable and is never sent
  automatically.
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
- Use the paperclip or drag and drop to stage files for the exact active Chat.
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

The eight Voice behavior values are stored with the other global Backend
settings. **Live after Save** means that a newly admitted operation observes the
saved value without restarting the Backend; it does not mean that Save can
rewrite work already in flight. Exactly five settings are live and three
require a restart:

| Settings control (`global.json` field) | Default | Valid value | Apply boundary | Contract |
| --- | --- | --- | --- | --- |
| **Read replies aloud** (`autoReadAloud`) | On | On / Off | Live after Save | Off preserves text replies but does not start new managed speech. |
| **Speech rate (%)** (`speechRatePercent`) | 100 | 50–200 | **Backend restart required** | The active synthesis rate remains unchanged until restart. |
| **Speech volume (%)** (`speechVolumePercent`) | 100 | 0–100 | Live after Save | Trusted preload applies the active value to each admitted clip; 0 silences playback without disabling synthesis. |
| **Voice profile** (`voiceProfileId`) | `default` | Configured logical Profile ID | **Backend restart required** | The active Profile remains unchanged until restart. |
| **Voice emotion** (`voiceEmotion`) | `neutral` | `neutral` / `happy` / `sad` | **Backend restart required** | The same active closed value selects the local TTS reference and reviewed static expression; model output cannot override it. |
| **Call captions** (`captionsEnabled`) | Show | Show / Hide | Live after Save | Supplies the default for newly opened Voice Sessions; the Session control remains available. |
| **Transcript review** (`transcriptReviewMode`) | `manual` | `manual` only | Live invariant | The disabled selector documents the enforced policy: recognition never sends without explicit review. |
| **Continue listening after replies** (`automaticRelisten`) | Off | On / Off | Live after Save | A clean Voice reply may open one new bounded capture, but its transcript still requires manual review. |

## Manual Voice UI and real-device acceptance checklist

On 2026-09-22, the project owner explicitly waived this manual device matrix as
a requirement for closing the Voice UI and Settings delivery. The rows below
remain marked pending as an honest record that the human observations were not
performed; they are optional future validation and must not be represented as
passed. The production-chain and automated evidence recorded later in this
section is the accepted engineering gate for this delivery.

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
**Start voice**, and use the rows below as the test script.

These rows state expected behavior, not completed results. Every result is
deliberately **Pending — manual run required** until a tester observes it on the
target Windows microphone, speaker, room, and installed local models. Stop
immediately if Windows reports that microphone access is denied.

| Exercise | Expected result | Result |
| --- | --- | --- |
| Open Voice without starting capture. | The exact Chat/Project and model are bound, the Session timer advances, primary status is ready, microphone status is off, and no permission prompt appears merely from opening Voice. | Pending — manual run required |
| Choose **Start microphone**, speak, and pause for about 0.6 seconds. | Status progresses through opening, listening/speech detected, and transcription. One editable **Final transcript** appears; no Chat request is sent and no partial recognition text is exposed. | Pending — manual run required |
| Edit the result, then choose **Use transcript in message** or **Append transcript to message**. | The Composer is updated without sending. Append preserves existing draft text. Repeat the capture and choose **Send transcript**: the message uses the normal durable path in the bound Chat while the pre-existing Composer draft and staged files remain unchanged. | Pending — manual run required |
| Observe a reviewed, submitted turn with captions shown and then hidden. | Primary status progresses through thinking and, when speech is expected, speaking; microphone status is announced separately. Assistant caption text follows its visible toggle. The Session settles only after the Chat terminal and expected speech terminal both arrive. | Pending — manual run required |
| Press **Mute** during a normal capture and again during reply-time/held interruption capture. | The microphone closes, admitted or held PCM is discarded, monitoring is disarmed, and no transcript or Chat Turn is created from discarded audio. A running text reply is not cancelled. Unmute does not reopen capture by itself. | Pending — manual run required |
| Turn **Auto-continue** on and complete a reviewed Voice turn normally. | After the exact Chat and any expected speech finish, one new bounded capture starts. The button remains visible; turning it off stops an automatically owned capture. Its transcript still waits for manual review. | Pending — manual run required |
| Repeat with Auto-continue on, then mute, cancel, interrupt, hang up, change Chat/Project, or cause Chat/speech failure before clean completion. | No stale callback or pending continuation reopens the microphone. | Pending — manual run required |
| Leave a non-empty Final transcript and try `Ctrl+,`, **Audio settings**, **Hang up**, and `Escape`. | Each route warns before data loss. Cancel keeps the review text and Session; confirming performs the requested cleanup/navigation without sending the transcript. | Pending — manual run required |
| Open **Audio settings** from Voice, then run the microphone and speaker tests. | Voice capture, transcription, monitoring, and exact playback are released before the device page opens. The chosen opaque device remains selected after Save; cancelling a device picker is a no-op. | Pending — manual run required |
| Submit a reviewed turn and speak a sustained phrase while thinking or speaking on hardware whose track reports echo cancellation enabled. | The UI shows interruption listening and then interruption, stops only that exact Chat stream and speech owner, rolls to a new listening epoch, and rejects late callbacks from the old epoch. The new Final transcript is not sent automatically. | Pending — manual run required |
| Repeat where WebRTC echo cancellation cannot be verified. | Monitoring fails closed, releases its microphone, shows a safe warning, and permits the current reply to continue. | Pending — manual run required |
| Repeat interruption attempts in quiet, speaker-echo, and headset conditions, recording trial count, false triggers, and observed interruption delay. | Verified AEC must not let Elysia's own playback trigger interruption; sustained user speech should interrupt without cancelling another turn. Record the measurements instead of replacing them with an automated simulation. | Pending — manual run required |
| Stay silent for about 10 seconds, then separately try **Cancel capture** and **Cancel transcription**. | Each path returns to a safe terminal state and creates no Chat-history Turn. | Pending — manual run required |
| Close Voice during managed playback, then switch Chat or Project in a separate run. | Exact playback and Voice-owned audio stop; the old Session cannot be reopened by late events. A text reply already in progress may continue in its Chat. | Pending — manual run required |
| Keep one Voice Session open for a recorded duration and complete several capture, reply, mute, and interruption cycles before hanging up. | Resource use remains bounded, the Windows microphone indicator turns off after hang-up, no background capture or playback remains, and opening a fresh Session still works. | Pending — manual run required |
| Enable Windows Narrator or another screen reader and exercise capture, transcription, thinking/speaking, mute, cancellation, one recoverable error, and hang-up. | Controls have understandable names and focus order; primary lifecycle and microphone status are announced separately without contradictory or repeated status floods. | Pending — manual run required |
| Save each of the five live settings, exercising a newly admitted operation after every Save. | Read-aloud, per-clip volume, caption default, enforced manual review, and automatic relisten reflect the saved value without a Backend restart. Volume 0 is silent while the text reply still completes. | Pending — manual run required |
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
if exist "%TEMP%\elysia-portrait.png" del /f /q "%TEMP%\elysia-portrait.png"
if exist "%TEMP%\elysia-state-atlas.png" del /f /q "%TEMP%\elysia-state-atlas.png"
if exist "%TEMP%\elysia-expression-atlas.png" del /f /q "%TEMP%\elysia-expression-atlas.png"
if exist "%TEMP%\elysia-speech-atlas.png" del /f /q "%TEMP%\elysia-speech-atlas.png"
pushd "%TEMP%"
call "D:\Elysia_AI\desktop\node_modules\.bin\asar.cmd" extract-file "D:\Elysia_AI\desktop\out\win-unpacked\resources\app.asar" "dist\character\elysia-portrait.png"
call "D:\Elysia_AI\desktop\node_modules\.bin\asar.cmd" extract-file "D:\Elysia_AI\desktop\out\win-unpacked\resources\app.asar" "dist\character\elysia-state-atlas.png"
call "D:\Elysia_AI\desktop\node_modules\.bin\asar.cmd" extract-file "D:\Elysia_AI\desktop\out\win-unpacked\resources\app.asar" "dist\character\elysia-expression-atlas.png"
call "D:\Elysia_AI\desktop\node_modules\.bin\asar.cmd" extract-file "D:\Elysia_AI\desktop\out\win-unpacked\resources\app.asar" "dist\character\elysia-speech-atlas.png"
popd
cd /d D:\Elysia_AI
.venv\Scripts\python.exe scripts\check_distribution_assets.py --unpacked-tree desktop\out\win-unpacked --asar-listing "%TEMP%\elysia-asar-listing.txt" --extracted-asar-portrait "%TEMP%\elysia-portrait.png" --extracted-asar-character-atlas "%TEMP%\elysia-state-atlas.png" --extracted-asar-expression-atlas "%TEMP%\elysia-expression-atlas.png" --extracted-asar-speech-atlas "%TEMP%\elysia-speech-atlas.png"
del /f /q "%TEMP%\elysia-asar-listing.txt" "%TEMP%\elysia-portrait.png" "%TEMP%\elysia-state-atlas.png" "%TEMP%\elysia-expression-atlas.png" "%TEMP%\elysia-speech-atlas.png"
```

`npm run package` creates an unpacked desktop build in `desktop\out`.
On Windows, `npm run make` additionally creates an unsigned NSIS installer.
The final audit scans the actual package tree and its ASAR listing, then verifies
the bytes extracted from the required portrait, state, expression, and speech
atlas paths. Neither
accepted output contains the GPT-SoVITS runtime, Voice Profile catalog, model
weights, or reference audio.

The application PNG and Windows ICO are derived from the official *Honkai
Impact 3rd* Elysia signet at the project owner's express direction for this
unofficial, non-commercial fan project. They are third-party assets, are not
covered by any source-code license, and do not imply HoYoverse / miHoYo
endorsement. See the root `MODEL_LICENSE.md` before publishing a build.

The packaged portrait fallback plus state, expression, and speech atlases are
reviewed generated fan artwork, not source-code-licensed assets. The
distribution audit pins all four by exact path, byte length, SHA-256, ASAR
cardinality, and extracted bytes. The state atlas is a static 4×2 RGB sheet:
only its first seven cells participate in the closed Character State contract,
and the eighth success cell is not a new runtime state. The expression atlas
admits only the user-controlled `neutral / happy / sad` mapping. Runtime speech
uses only the first four cells of the facial atlas's first band as
`closed / small / medium / wide` amplitude cues; it does not interpret the
remaining review cells as detected phonemes.
The Desktop Pet reuses the already reviewed and pinned portrait; it introduces
no additional character image or implied license. The larger Desktop Pet pose
review sheet remains repository review material and is not loaded by this
static window.

`npm run docs:check` enforces file-purpose comments plus public class,
function, class-method, and exported interface-method documentation. The
semantic why/how requirements remain part of review under the root
`AGENTS.md` policy.

`npm test` runs both the shared protocol contract suite and Electron renderer
UI tests, including Knowledge method/event races, export ownership across
Renderer reload and Project switches, trusted receipt settlement, Project
isolation, archived read-only behavior, explicit grounded intent, and citation
accessibility. The contract suite also runs `character-state.test.mjs`,
`character-presentation.test.mjs`, `speech-mouth.test.mjs`, and
`desktop-pet-lifecycle.test.mjs`, `desktop-pet-preferences.test.mjs`,
`presence-native-notification.test.mjs`, and
`presence-notification-preferences.test.mjs`.
Desktop Pet coverage verifies ready/shutdown deadlines, the shared cross-entry
mutation queue, Hidden tray residency and Disabled exit, programmatic-position
suppression, the strict update schema, default-off and corrupt-file behavior,
revision CAS, atomic replace failure, Main-private placement, resource bounds,
and mixed-scale, negative-coordinate, removed-display, and Windows
unsigned-hash display-ID DIP clamping. Renderer source-policy
tests prove that the main and pet HTML entries cannot borrow each other's IPC
authority. `desktop-pet-preload.test.cjs` loads the production dedicated
Preload in isolation and proves that only its frozen three-method API and fixed
channels exist. Presence notification contract coverage verifies the exact
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
recovery, explicit failed-state retry, the request to return to main Chat,
current-Chat/Project
scoping, Voice projection, Backend failure,
closed state/emotion/speech atlas cues, performance preference/Reduced Motion,
and the speech → expression → state → portrait → accessible-text fallback
chain.
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
- The Desktop Pet is a second, exact renderer entry with a separate sandboxed
  Preload. Its frozen API contains only `ready`, `hide`, and `openMainChat`;
  Electron validates its exact top frame and window owner for every call. It
  cannot obtain the main `DesktopApi`, Backend state, raw IPC, network,
  filesystem, Node, microphone, speaker-selection, or arbitrary navigation
  capability.
- Desktop Pet persistence is Main-only: the exact JSON schema is capped at
  16 KiB, mode changes use optimistic revisions and same-directory atomic
  replacement, and native placement is excluded from public state. Invalid
  storage defaults to Off. The nominal 320×480 DIP window is clamped to each
  current display work area and the general geometry contract caps it at
  420×560 DIP; Hidden and Disabled destroy the renderer rather than retaining
  an invisible page. These are resource bounds, not a promise of a fixed RAM
  measurement.
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
  bounded drain so they cannot consume the Desktop Pet's ordered final-position
  save window before process exit. The only
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
  leaking it through the system default speaker. For Animated speaking artwork,
  the same trusted graph samples the actual post-selection playback at no more
  than 20 Hz and reduces RMS to a four-value mouth cue; raw samples, continuous
  levels, and arbitrary animation selectors never cross into React. Still and
  Reduced Motion keep the cue closed. The managed runtime's current partial manifest proves launch consistency, not complete
  supply-chain provenance, so desktop speech caching remains disabled.
- Settings accepts an exact non-sensitive allowlist, including the closed STT
  model/device/language enums and eight Voice behavior fields, uses optimistic
  revisions and atomic replacement, and remains repairable after Backend
  initialization rejects a saved model or Ollama origin. Read-aloud, volume,
  captions, the manual-review invariant, and automatic relisten are adopted
  between admitted operations after Save; synthesis rate, Voice Profile, and
  the closed `neutral / happy / sad` Voice Emotion keep their prior active
  values until a Backend restart. The active emotion controls both the TTS
  reference choice and reviewed static expression; model output has no field
  that can select arbitrary character animation.
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
  Automatic relisten is a visible, disableable Session policy, not an
  auto-submit mode: it admits a new capture only after a clean exact-turn
  completion, and mute, failure, cancellation, hang-up, or context replacement
  invalidates its ownership before the microphone can reopen.
- Native selection and drop paths remain inside the trusted preload/Electron
  boundary. Python copies validated regular files into opaque, scope-specific
  storage, and protocol responses expose only safe metadata and attachment IDs.
