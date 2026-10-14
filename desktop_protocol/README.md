# Elysia Desktop Protocol v1

This directory is the language-neutral contract between Electron and the
existing Python Backend.

- `schema/v1.schema.json` is the machine-readable JSON Schema.
- `fixtures/v1.samples.json` contains the samples consumed by both Python and
  TypeScript contract tests.
- `contracts.py` provides Python runtime validation and message builders.
- `desktop/electron/protocol.ts` provides the matching TypeScript runtime
  parser and types.

Every newline-delimited JSON frame carries:

```json
{
  "protocol": {
    "name": "elysia.desktop",
    "version": 1
  }
}
```

Electron creates a new random session token for every Python process. The
token is passed to that child through its environment and must be echoed in
the fast typed `handshake` request before Python starts application services.
Electron verifies the negotiated version and capabilities, then sends the
separate `initialize` request with a longer timeout and typed progress before
entering the `ready` state.

Version 1 defines strict request, response, error, stream, progress,
permission, event, cancel, and permission-decision shapes. The current runtime
advertises `chat.stream`, `chat.retry`, `request.cancel`, `stream`, `progress`,
`event`, `chat.sessions`, `project.management`, `settings.management`,
`attachment.management`, `knowledge.management`, `voice.settings`,
`voice.capture`, and the optional `voice.transcription`, `voice.speech`,
`voice.speech.start`, and `voice.speech.cancel` capabilities.
Both new-turn and retry generation reuse the `chat.reply` stream.
Cancellation succeeds only before generation claims its atomic commit gate, so
a successful Stop response guarantees that the interrupted turn is not saved.
Backend permission prompts retain their stable schema but are not yet
advertised as an active capability.

`chat.mode` changes only the explicitly selected active Chat using
`{chatId, mode: "chat" | "work"}` and returns the same canonical Chat state as
other session mutations. It preserves messages, summaries, attachments, and
drafts, and refuses changes during an admitted reply or local transcription.
The UI explains the selected capabilities before the next turn. Mode-specific
prompt instructions are composed separately from the unchanged persona
resource. Chat mode denies tool admission; selecting Work only makes typed,
workspace-bound, separately approved actions eligible. Message text, Project
instructions, retrieved memory, and model output cannot select a mode or grant
tool permissions.

`work.workspace.get` and `work.workspace.bind` operate on one canonical active
Work Chat. The bind request carries a directory supplied by Electron Main's
native chooser or `null` to revoke access; the renderer has no raw path setter.
Both return `{kind: "work.workspace", chatId, workspacePath}`. Each Chat's
selection is stored beneath the private data root at `workspace/work/bindings`.
Project paths are not inherited automatically. Directory identity is saved
with the selection and rechecked before execution. Safe access pins ancestors
and reads through the verified leaf handle; traversal, network/device roots,
symlinks, Windows junctions, and hard links are rejected. Work state is durable
during data-root migration and excluded from generic cache cleanup.

`work.tools.list` returns `{kind: "work.tools", tools}` for the active Work
Chat. Every catalog entry declares its exact input and output schemas, risk,
and timeout. The catalog contains four read operations: directory listing,
bounded text reading, filename search, and metadata, plus separately approved
create, edit, move, and recoverable delete operations. There is no shell or
arbitrary-command tool. Statuses distinguish
success, partial output, denial, cancellation, and failure with fixed public
errors. Work code uses `work_tools` because the reviewed RVC runtime owns the
unrelated vendor namespace `tools`; the unused old Elysia namespace was removed.
Selected Work roots must also be separate from application code and private
application data. This prevents a file edit from modifying the very binding or
permission journal that authorizes subsequent actions.

`work.tool.run` executes an explicit `{chatId, call}` after the same canonical
mode and workspace checks. Read tools reject credential/private-key paths,
binary or unsupported text formats, and text beyond 256 KiB. Listing is bounded
to 1,000 entries; filename search visits at most 5,000 entries and publishes at
most 500 matches without reading bodies. Deadlines and cancellation are
cooperative around native local I/O. Read operations create no cache, backup,
or hidden file writes. Write tool IDs receive `permission_required` here.

`work.tool.preview` validates an explicit write action and returns its exact
parameters, fixed risk, bounded diff/impact preview, and a five-minute UTC
approval lease. Preparing a change never changes selected Workspace files.
Replacing a preview, changing mode/scope/Project/Chat, or restarting revokes
older rights. Native write primitives exclusively pin the approved file and
check its digest before creating, editing, moving, or deleting one text file.
The private transaction journal and fsynced before-image precede any mutation;
overwrites and recoverable deletes therefore retain a restoration copy.
The 20-second write budget applies to preflight and backup admission. Once
native writing starts it completes and verifies through the same handle, with
an accurate outcome and recovery intent if interrupted. Native OS calls are
not forcibly terminated to manufacture a timeout or successful cancellation.

`work.tool.confirm` carries only the current Chat ID, exact shown approval ID,
and a Boolean one-time decision. The renderer cannot substitute new parameters
while approving. Low-risk reads are explicit user actions; medium/high writes
always require preview and confirmation. No critical-risk operation is exposed.
Decline, expiry, changed bytes, changed Project/root, revoked rights, and token
replay all refuse mutation. Modified actions receive a new preview/token. The
UI offers a stronger recoverable-deletion confirmation and no permanent grant.

`work.plan.create` records a reviewed title and one to sixteen exact tool calls;
it does not start execution. `work.plan.control` starts, pauses between steps,
resumes, advances, or cancels the exact plan. `work.plan.get` returns its bounded
snapshot, including each step's input, result, approval, and physical `busy`
lease. The worker executes reads sequentially and stops before every write.
`work.plan.confirm` authorizes only that plan's displayed approval ID once.
Generic tool confirmation cannot consume plan-owned tokens. Pause preserves a
pending approval; cancellation revokes it and discards late read results. An
already admitted native write completes truthfully while cancelled capacity
remains reserved. Backend shutdown cancels admission and waits for that worker
to drain. Private versioned checkpoints restore safe unfinished work as paused;
uncertain writes require audit inspection and cannot replay. Approval tokens
never survive restart. Mode, owner, Project, and pinned Workspace identity are
rechecked for every step and before an explicit Resume.

`work.plan.revise` consumes the shown approval as a `modify` decision and
prepares a new pending plan containing the revised same-tool call and unfinished
suffix. It neither starts that plan nor approves the replacement. Audit decision
values are `approve`, `modify`, `reject`, and `timeout`; legacy records have a
null decision rather than an inferred one.

`work.plan.retry` accepts only `chatId`, `planId`, and `stepId`. It prepares a
new pending plan from an explicitly retryable failed read and the unfinished
suffix, preserving exact step IDs/calls and the original job budget. Completed
steps never replay. Only `timeout` and `io_error` reads permit up to two retries;
new writes still need their own preview and approval.

An optional strict `job` observation accompanies plan snapshots: a default
300-second total budget (trusted configuration capped at 900), 30-second read
or preview steps, and a 20-second write-admission deadline. Pause and approval
wait consume total time. Retry cannot reset that clock. Cancellation/timeout
keep physical capacity reserved until native work drains; already admitted
writes report their actual outcome. Active step, phase, attempt, remaining
seconds, stop reason, and retry hints are validated against the plan, without
exposing callbacks, filesystem handles, or any authority to change limits.

`work.audit.list` returns the active Work Chat's latest hundred entries, newest
first, within a 512 KiB payload and with explicit truncation. Read tools persist
only a private audit summary; selected Workspace files remain untouched. Reads
and writes record the original action time, Chat/Project, tool, parameter
summary, user decision, outcome, and fixed error code. File bodies and literal
search text never enter this public history: new content and search queries are
represented by sizes and digests. Each action's entry reflects its current Undo
or interrupted-recovery outcome. Audit does not grant new file access and may
be inspected after the binding is cleared.

`work.undo` carries only the exact recorded Undo ID and `confirmed: true`.
Electron consumes the displayed history permission before dispatch and checks
the response against that recorded path/destination. Python reacquires the
current Chat/Project, pinned root, expected current bytes, and verified backup;
changed files or existing destinations refuse restoration. No external edit is
overwritten. Undo waits for live plans and native callbacks to finish. Failed
or interrupted transactions disclose a relative journal directory under
`workspace/work/transactions`; a `before.bin` path and digest appear only when
that restoration copy was actually verified. Users can open the data directory
from Settings for manual recovery. No operation resumes automatically.

Work journals and before-images survive data-root migration and generic cache
cleanup. The existing legacy export bundle has its own explicit allowlist and
does not include Work journals or backups; retain the data root when preserving
these recovery records. Expanding release-wide backup formats belongs to the
later Release stage.

The desktop lockfile updates the compatible cache and source-map fixes. Its
scoped `@electron/get@3.1.0` override uses `global-agent@4.1.3` to preserve the
packaging downloader's CommonJS proxy bootstrap while removing the vulnerable
legacy formatter chain. The isolated proxy bootstrap regression exercises
that actual dependency without network traffic or global HTTP side effects in
the test process; renderer and packaged-app checks still validate the application.

`settings.get` and `settings.update` expose one exact public allowlist with
optimistic revision checks: Chat model, Ollama origin, two Memory limits,
import byte limit, transcription model/device/language, automatic Voice Call speech,
speech rate and volume percentages, and a logical Voice Profile ID. The STT fields are
closed enums: `tiny|base|small|medium|large-v3|turbo`, `auto|cuda|cpu`, and
`auto|zh|en`. Speech rate is an integer from 50 through 200, volume is an
integer from 0 through 100, and Voice Profile IDs use the bounded logical
`[a-z0-9][a-z0-9._-]{0,63}` form rather than a filesystem path. Protocol v1
still parses and round-trips the retired `captionsEnabled`,
`transcriptReviewMode`, and `automaticRelisten` fields so older settings files
can migrate without loss. Current clients do not expose those fields as
controls, and they do not govern Dictate or Voice Call behavior.

Saved changes are reported separately from active values. Model/runtime/STT
changes, speech rate, and Voice Profile selection may be listed in
`restartFields`; automatic Voice Call speech and volume are live preferences and are
never valid restart field names. Retired compatibility fields are likewise
never restart fields. API keys, tokens, passwords, base paths, environment data, arbitrary
extension fields, live microphone state, Transcript content, and current Voice
session state are rejected. Authenticated Settings reads remain available when
Brain initialization fails or STT is active so the desktop can diagnose state;
configuration mutations are rejected while a transcription is active or
physically draining.

`voice.settings.get` and `voice.settings.update` persist only the desired
opaque microphone and speaker IDs, with `null` meaning the current system
default. Their response additionally includes an exact, read-only
`transcriptionStatus`: `unavailable|available|ready`, selected model, requested
device, resolved `cuda|cpu|null`, closed compute type, and a closed reason code.
Model paths, native messages, exception objects, and arbitrary extra fields are
rejected. Electron/React maps missing-model, missing-dependency, runtime-probe,
device, CUDA, and initialization reason codes to safe recovery actions. The
methods use independent optimistic revisions and reads remain available when
Brain initialization fails, Chat generation is active, or STT is active;
device-setting mutations are rejected during STT. Device labels, Windows
permission state, live availability, and audio samples never cross this Python
protocol boundary; Electron owns those transient hardware details.

`voice.capture.complete` is an intentionally narrow bridge for one bounded
utterance. It is reachable only after the user explicitly presses Dictate or
opens Voice Call in the renderer. The renderer downmixes and resamples input to 16 kHz
mono signed 16-bit little-endian PCM, processes 20 ms / 320-sample frames, and
uses bounded local VAD so silence or short input is discarded before submission.
An accepted request carries the active Chat ID, a bounded Voice session ID,
frame-aligned speech markers, sample counts, fixed-format metadata, and strict
canonical Base64 PCM. Python validates that transient payload and returns only
the session and Chat IDs, format metadata, total and speech durations, and a
SHA-256 digest; PCM is never echoed in the response. This operation does not
persist audio, invoke the Brain, or create a Chat Turn. Speech-to-text, TTS, and
continuous voice conversation remain outside this receipt method.

`voice.transcription.start` is the separate long-running speech-to-text
request. It reuses the exact fixed-format capture fields, adds an `auto`, `zh`,
or `en` language hint, and carries each PCM payload exactly once. Python admits
the request to a fixed-size bounded worker before emitting
`voice.transcription.started` and progress. Its terminal response contains only
the original Voice session and Chat IDs, bounded final text, resolved `zh` or
`en` language, and a finite language probability. Before publication, the
complete final text always crosses the pinned OpenCC `t2s` boundary, including
Chinese text inside an `en`-dominant result; no raw Traditional transcript is
sent to Dictate or Voice Call. It never contains PCM, a model path, or native-
library diagnostics.

The active configuration maps the selected model name only to
`models/weights/faster-whisper/<model>` and requires a complete local model.
The adapter uses local-only loading and never treats a protocol or Settings
model name as permission to download weights. The optional Python runtime is
declared separately in `requirements-stt.txt`; neither dependencies nor model
weights are part of this protocol or its fixtures.

Transcription and Chat generation are mutually exclusive because the first
local implementation must not overcommit CPU/GPU model resources. Cancellation
and timeout are immediate logical terminal states. Python cannot safely kill a
thread executing native inference, so that physical worker remains occupied
until the call returns; the late result is discarded and new STT or Chat work
continues to receive a busy response during that drain. Shutdown closes
admission and suppresses callbacks that lose the shutdown race. Electron
correlates this optional capability to the originating Voice session and Chat,
then exposes only the final sanitized transcript to React. Dictate appends that
text to the current Composer draft and never sends it. Voice Call instead
submits each completed final transcript through the existing Chat request path;
after the reply and expected TTS drain, an unmuted call starts another bounded
capture. The protocol operation itself still performs no Chat side effect: the
Renderer chooses the consumer only after exact session ownership validation.
No partial recognized text crosses the wire. Voice Call is an utterance-based
loop, not real-time partial recognition or gapless streaming.

Desktop Protocol v1 advertises the optional `voice.speech` capability but
deliberately defines no public request that can synthesize arbitrary text.
Every `chat.stream` and `chat.retry` request carries the required Boolean
`speakReply`. Ordinary text Chat sends `false`, so Python does not create a
speech turn and emits no synthetic zero-work terminal. Voice Call sends `true`;
during that accepted `chat.reply` stream Python gives the speech path a copy of
each text chunk when the live automatic read-aloud preference is enabled. The
canonical Chat stream and final persisted Assistant text remain owned by the
existing Chat transaction. Natural-boundary sentences enter one bounded FIFO
queue, and cancellation, replacement, or synthesis failure cannot turn a
partial spoken copy into a committed Chat message.

`voice.speech.start` supports the explicit **Read aloud** action without
opening an arbitrary-text synthesis boundary. Its closed request contains only
the active `chatId` and one `assistantMessageId`. Python loads that Chat from
the canonical repository, requires that the ID belongs to a persisted
Assistant message, and feeds the stored content through a fresh managed turn.
The closed acknowledgement echoes `kind`, the new speech-owning `requestId`,
`chatId`, and `assistantMessageId`. Source text, paths, profiles, and synthesis
options are rejected as unknown fields.

`voice.speech.cancel` is the narrow authenticated control operation paired with
that managed path. Its request carries both the originating Chat request ID and
Chat ID so an old renderer event cannot stop a newer turn that happens to share
UI state. Its response echoes that exact ownership pair with
`kind: "voice.speech.cancel"` and a Boolean `stopped`. `true` means the matching
managed speech coordinator was still active and accepted cancellation; `false`
is an idempotent outcome when the turn already reached a terminal state, was
already cancelled, or no longer owns both identifiers. The false result is not
an error and never broadens cancellation to another turn. Generic
`request.cancel` and `shutdown` keep their existing bare `{ "stopped": true }`
result, which cannot be confused with this ownership-bearing response.

The independent loopback GPT-SoVITS `/tts` adapter and fixed-text smoke remain
separate Python-only diagnostics. `service_binding_unverified` there means only
that the external API is reachable and structurally compatible—the upstream
API cannot attest which catalog-declared weights its process loaded. Desktop
speech instead owns one guarded local worker lease and binds it to one resolved
Voice Profile selection. The partial runtime manifest proves consistency
between that parent and worker, not complete third-party provenance, so this
managed path also does not claim a supply-chain-verified model identity or
enable synthesized-audio caching.

The Voice Profile catalog remains under the Git-ignored
`workspace/settings/` tree, while the separately installed runtime,
checkpoints, and reference audio remain in ignored local runtime/model
directories. They are not protocol fixtures, repository content, or packaged
dependencies. Protocol v1 deliberately does not encode the renderer-local
`LISTENING → THINKING → SPEAKING` Voice Session state machine; React owns that
already-implemented UI lifecycle while this wire contract carries only its
bounded operations and terminal facts.

The general Python `SynthesisResult` permits at most 32 MiB of encoded audio
with complete supported container framing; it still does not promise that a
codec decoder will accept every otherwise valid payload. Desktop delivery is
narrower: the managed worker must return an exact 32 kHz mono PCM16 WAV no
larger than 8 MiB. Because one Protocol v1 NDJSON frame is capped at 16 MiB and
Base64 would expand audio further, Python sends each accepted clip through the
separate inherited binary pipe at child descriptor 3. Its fixed header carries
an opaque 256-bit clip token, uint32 sentence sequence, bounded byte length,
and SHA-256 digest. Electron Main validates that framing incrementally and
admits at most one unacknowledged frame, so JSON parsing never holds audio and
slow playback applies bounded backpressure.

The correlated `voice.speech.clip`, `voice.speech.failure`, and
`voice.speech.terminal` events are closed shapes. Clip metadata must match the
next binary frame before playback; failures expose only a stable enum; terminal
counters describe completion or cancellation. These events cannot carry source
text, Base64 audio, paths, profile/reference details, cache state, or native
diagnostics. Chat and transcription lifecycle events are closed and
request-correlated for the same reason. Electron Main pairs exact metadata with
the next fd3 frame and forwards the validated WAV plus an opaque playback
identity over private Main-to-Preload IPC; React never receives a WAV, token,
digest, model path, reference path, or exact prompt. Preload resolves the selected output device,
decodes and starts one clip, reports its closed outcome, and fail-closes on
sink, decode, lifecycle, or correlation failure. Window replacement and
shutdown release stale playback ownership without making text Chat depend on
optional speech.

`image.generate` is the single Protocol v1 image-generation entry point. Its
exact DTO contains `profileId`, `sizePresetId`, the eight public prompt
components (`appearance`, `outfit`, `pose`, `scene`, `style`, `camera`,
`lighting`, and `negativePrompt`), `seed`, and the required literal
`originalFictionalConfirmed: true`. The Backend fixes the operation to a new
original fictional character; callers cannot select a different intent,
workflow, model, path, policy, or content mode. Unknown fields are rejected,
including `contentMode`, `adultMode`, `nsfw`, and `protection`. Prompt
components are bounded Unicode text and reject control characters and lone
surrogates so Python, TypeScript, and JSON Schema interpret the DTO equally.

Image progress is also a closed public surface. `operation: "image.generate"`
always uses `total: 100` and exactly these `(message, completed)` checkpoints:
`validating/5`, `probing/15`, `submitting/25`, `sampling/35`, `verifying/90`,
and `completed/100`. Runtime model names, paths, node names, and arbitrary
diagnostic phases cannot become protocol progress text. Generic
`request.cancel` remains the cancellation operation and continues to target
the originating request ID.

`image.gallery.list`, `image.gallery.get`, `image.gallery.delete`, and
`image.gallery.open` expose only opaque `gallery_<32 lowercase hex>` artifact
IDs. List pages use `limit` 1 through 255 plus a non-negative `offset`; items
are unique and sorted newest first by the complete canonical
`YYYY-MM-DDTHH:mm:ss.ffffffZ` timestamp, then artifact ID, with `hasMore`
allowed only for a full page. Public artifact metadata is limited to ID,
creation time, bounded dimensions and byte size, SHA-256, `image/png`, profile
ID, and seed. Generation additionally returns an opaque
`imageop_<32 lowercase hex>` operation ID.

Gallery open never returns PNG bytes, Base64, a path, a prompt, or workflow
details. It returns the artifact ID plus a nonzero 256-bit `previewToken`,
length, digest, media type, and dimensions that must match the next bounded
frame on the separate private image byte channel. This keeps JSON responses
path-free while retaining exact metadata-to-bytes correlation.

`attachment.list`, `attachment.add`, and `attachment.remove` operate on one
exact Chat or Project scope. Native source paths are accepted only across the
authenticated Electron-main-to-Python boundary and are never returned in a
response, persisted in a manifest, or forwarded to the renderer. The public
state contains only an opaque ID, display basename, canonical media type,
byte size, ready status, and configured intake limits. A draft created under an
older, larger limit remains visible and removable after the limit is lowered.
`chat.stream.attachmentIds`
claims only ready items in that Chat; cancellation restores the draft, while
a successful Chat commit reconciles the blob to the persisted message. File
contents on this attachment route remain message data and are not implicitly
parsed, indexed, or promoted into a Project corpus.

`knowledge.management` is the separate Project-only document path. Its exact
methods are `knowledge.list`, `knowledge.source.add`,
`knowledge.source.replace`, `knowledge.source.reindex`,
`knowledge.source.delete`, `knowledge.project.rebuild`,
`knowledge.project.revoke`, `knowledge.recover`, and
`knowledge.source.export`. Add and replace accept native source paths only from
authenticated Electron Main; export accepts only the destination selected by
the native save dialog. Those paths never appear in responses, events, Chat
history, or the renderer API.

Knowledge list results expose at most the closed source states `ready`,
`unindexed`, `stale`, `revoked`, and `processing`, plus bounded durable
operation snapshots. Long lifecycle mutations run outside the protocol reader
and emit request-correlated progress followed by
`knowledge.operation.changed` or the terminal-only
`knowledge.operation.completed`. Cooperative `request.cancel` can stop
cancellable lifecycle work before the safe commit boundary. Recovery and
verified export do not report a fake cancellation success when they cannot be
interrupted; the public React Stop action is not enabled for either operation.

Verified export is a separate background task under the same conservative
global Knowledge lease. It does not enter the lifecycle journal or emit a fake
lifecycle checkpoint. Its terminal response is a path-free
`knowledge.export` receipt containing only the safe file name, media type, and
written byte count; the native destination never appears in a response, event,
snapshot, Chat history, or Renderer API. Electron pins the Source metadata
authenticated before the Save dialog and accepts export success only when that
receipt matches it exactly.

A Backend admits only one public lifecycle/export task at a time. The shared
Python coordinator excludes a concurrent grounded answer, while a separate
Backend admission guard rejects Project-authority writes for the same global
policy. Electron mirrors that lease before sending work so a list, second
export, lifecycle mutation, or grounded request is not queued behind the owner.
Once an export request is pending, the renderer-facing
`BackendSnapshot.activeKnowledgeOperation` preserves its exact request and
Project ownership across Renderer reloads with `cancellable: false`.
After the validated wire response releases the lease, Electron emits a
correlated, path-free `knowledge-export-settled` Renderer event; typed failures
use the existing correlated Knowledge error event. These are Electron
`BackendEvent` contracts, not additional Python wire events. Archived Projects
remain read-only.

`chat.stream` and `chat.retry` require `speakReply` and accept the optional
Boolean `useProjectKnowledge`. Omission or `false` for Project knowledge
preserves the ordinary Chat path;
`true` authorizes only the corpus derived from that Chat's canonical active
Project relationship. Assistant history may contain a closed `groundedAnswer`
object with status, bounded statement kinds, exact citation closure, safe file
metadata, excerpts, and text/table locations. Python and TypeScript reject
grounded proof on non-Assistant messages and reject paths, hashes, vectors,
prompts, native messages, unknown fields, or inconsistent operation states.

String limits are measured in Unicode code points and each UTF-8 NDJSON frame
is capped at 16,777,216 bytes, including leading and trailing JSON whitespace.
Blank Chat input has one language-independent definition: Unicode White_Space
code points plus U+FEFF. The JSON Schema records the same list so Python and
TypeScript cannot silently disagree at unusual Unicode boundaries.

The Electron process tracks every request ID and enforces monotonic stream
sequences, one empty terminal stream chunk, and a final response whose text
exactly matches the accumulated stream. The
renderer receives only the smaller API in `desktop/electron/contracts.ts` and
cannot read or modify Chat or Memory files directly.
