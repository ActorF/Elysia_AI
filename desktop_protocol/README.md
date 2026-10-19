# Elysia Desktop Protocol v1

The language-neutral contract between Electron Main and the Python Backend.

| Definition | Source |
| --- | --- |
| JSON Schema | [schema/v1.schema.json](schema/v1.schema.json) |
| Shared Python/TypeScript samples | [fixtures/v1.samples.json](fixtures/v1.samples.json) |
| Python validation and builders | [contracts.py](contracts.py) |
| TypeScript parser and types | [protocol.ts](../desktop/electron/protocol.ts) |
| Smaller renderer API | [contracts.ts](../desktop/electron/contracts.ts) |

## Transport and ownership

Every UTF-8 NDJSON frame carries `"protocol": {"name": "elysia.desktop", "version": 1}`.
The frame limit is 16,777,216 bytes, including surrounding JSON whitespace.
String limits count Unicode code points. Blank Chat input means Unicode
White_Space plus U+FEFF; the schema records the same list for both languages.

Electron passes a fresh random session token to each child through its
environment. A fast typed `handshake` authenticates the token and negotiates
version/capabilities before services start; a separate `initialize` uses a
longer timeout and typed progress before `ready`.

Requests, responses, errors, streams, progress, permissions, events, cancellation,
and permission decisions have strict shapes. Use the negotiated capability
set; optional STT and speech depend on local readiness. Backend permission
prompts retain their schema but are not advertised as an active capability.

Both new turns and retries use `chat.reply`. Electron tracks request IDs,
monotonic sequences, one empty terminal chunk, and a final response matching
the accumulated text exactly. Cancellation succeeds only before the atomic
commit gate: successful Stop means the interrupted turn was not saved.
React cannot read or modify Chat or Memory files directly.

## Chat and Work

`chat.mode` accepts `{chatId, mode: "chat" | "work"}` for one active Chat,
preserves its content, and refuses changes during a reply or transcription.
Persona resources remain unchanged. Chat denies tools; Work makes only typed,
workspace-bound actions eligible. Prompts, memory, Project instructions, and
model output cannot grant a mode or permission.

### Workspace and tools

`work.workspace.get` / `work.workspace.bind` return
`{kind: "work.workspace", chatId, workspacePath}`. Binding accepts only Main's
native directory selection or `null` to revoke; there is no renderer path
setter or automatic Project inheritance. Bindings persist under
`workspace/work/bindings`.

Pinned directory identity is rechecked before execution. Safe access pins
ancestors and reads through the verified leaf handle; traversal, network/device
roots, symlinks, junctions, and hard links are rejected. Selected roots must be
separate from application code and private data to prevent edits to the
authority records that authorize later actions.

`work.tools.list` publishes exact schemas, risk, and timeout for four reads
(list, text read, filename search, metadata) and separately approved create,
edit, move, and recoverable delete. There is no shell or arbitrary-command
tool. The `work_tools` namespace avoids the RVC vendor's unrelated `tools`.

`work.tool.run` accepts `{chatId, call}` after canonical mode/root checks.
Reads reject credentials/private keys, unsupported or binary text, and content
over 256 KiB. Listing stops at 1,000 entries; filename search visits at most
5,000 entries and returns 500 matches without reading bodies. Deadlines and
cancellation are cooperative around native I/O. Reads write no Workspace
files, caches, or backups; write IDs return `permission_required`.

### Approval and recovery

`work.tool.preview` returns exact parameters, fixed risk, bounded impact/diff,
and a five-minute UTC approval lease without modifying files.
`work.tool.confirm` accepts only Chat ID, displayed approval ID, and a Boolean
one-time decision; it cannot replace parameters. Medium/high writes always
need confirmation; no critical-risk operation or permanent grant is exposed.

Changing preview, mode, owner, Project, root, or process revokes approval.
Decline, expiry, replay, or changed bytes refuse mutation. Writes exclusively
pin the approved file and digest, then persist a transaction journal and fsynced
before-image before mutation. The 20-second budget covers admission/backup;
once native writing starts, it completes and verifies through the same handle.
A timeout cannot manufacture successful cancellation of an OS write.

`work.audit.list` returns the current Work Chat's latest 100 entries, newest
first, within 512 KiB and with explicit truncation. Entries retain original
time, owner, tool, summarized parameters, decision, outcome, and fixed errors.
Bodies and search literals are replaced by sizes/digests. Undo/recovery updates
the same action's outcome; history remains inspectable after unbinding and
grants no new access.

`work.undo` accepts only the recorded Undo ID and `confirmed: true`.
Electron consumes the displayed permission and validates the recorded paths.
Python rechecks owner, pinned root, expected current bytes, and verified backup.
Changed files or occupied destinations refuse restoration. Undo waits for live
plans/native callbacks; no external edit is overwritten.

Interrupted transactions disclose a relative directory under
`workspace/work/transactions`; `before.bin` and its digest appear only when
verified. No recovery runs automatically. Journals/backups survive data-root
migration and generic cleanup, but are excluded from the legacy export bundle;
retain the data root to preserve them.

### Plans and jobs

| Method | Contract |
| --- | --- |
| `work.plan.create` | Records a title and 1–16 exact calls without executing. |
| `work.plan.control` | Starts, pauses between steps, resumes, advances, or cancels the exact plan. |
| `work.plan.get` | Returns bounded step inputs/results/approvals and physical `busy` ownership. |
| `work.plan.confirm` | Consumes only that plan's displayed approval once; generic confirmation cannot use it. |
| `work.plan.revise` | Consumes the shown approval as `modify`; prepares a pending same-tool revision and unfinished suffix without starting or approving it. |
| `work.plan.retry` | Accepts only Chat/plan/step IDs; prepares a pending retryable failed read and unfinished suffix without replaying completed steps. |

Reads execute sequentially and stop before each write. Pause preserves pending
approval; cancel revokes it and discards late read results. An admitted write
reports its actual outcome and retains capacity until drained. Shutdown closes
admission and waits for the worker. Checkpoints restore safe unfinished work
as paused; uncertain writes require audit, and approval tokens never survive
restart. Every step and Resume rechecks mode, owner, Project, and root identity.

Audit decisions are `approve|modify|reject|timeout`; legacy decisions are null.
Only `timeout` / `io_error` reads allow up to two retries. Original step IDs,
calls, and job budget remain unchanged; new writes need new approval.

Optional strict `job` snapshots expose a default 300-second total budget
(trusted configuration capped at 900), 30-second read/preview steps, and
20-second write admission. Pause and approval waits consume total time; retry
cannot reset it. Phase, attempt, remaining time, stop reason, and retry hints
must agree with the plan and expose no handles or authority to change limits.

## Settings

`settings.get` / `settings.update` use an exact non-sensitive allowlist and
optimistic revisions: Chat model, Ollama origin, Memory limits, import limit,
STT selection, automatic Voice speech, rate, volume, logical Profile ID, and
the closed Voice Emotion selection.

| Value | Bound |
| --- | --- |
| STT model | `tiny`, `base`, `small`, `medium`, `large-v3`, `turbo` |
| STT device / language | Device: `auto`, `cuda`, `cpu`; language: `auto`, `zh`, `en` |
| Speech rate / volume | Integer 50–200 / 0–100 |
| Profile ID | `[a-z0-9][a-z0-9._-]{0,63}`; never a path |

Saved values are distinct from active values. Model/runtime/STT, speech rate,
Profile, and Emotion changes may require restart. Automatic Voice speech and
volume are live and never valid `restartFields`. Retired `captionsEnabled`,
`transcriptReviewMode`, and `automaticRelisten` round-trip for migration only;
they neither control the UI nor require restart.

Keys, tokens, passwords, paths, environment data, arbitrary fields, microphone
state, transcripts, and live sessions are rejected. Reads remain available
after Brain initialization failure or during STT; mutations refuse active or
physically draining transcription.

`voice.settings.get` / `voice.settings.update` use independent revisions and
persist only opaque microphone/speaker IDs (`null` means system default).
Read-only `transcriptionStatus` contains `unavailable|available|ready`, selected
model, requested/resolved device, closed compute type, and reason code.
Reads work during Chat/STT or Brain failure; device mutations refuse STT.
Device labels, permissions, live availability, and samples stay in Electron.

## Voice

### Capture and transcription

`voice.capture.complete` acknowledges one bounded utterance after explicit
Dictate or Voice Call activation. Capture uses 16 kHz mono `s16le`, 20 ms /
320-sample frames, and local VAD. Requests carry exact Chat/session IDs,
frame-aligned markers, counts, fixed metadata, and canonical Base64 PCM.
The receipt returns only ownership, format, durations, and SHA-256.
It neither persists audio nor creates a Chat turn.

`voice.transcription.start` carries that PCM exactly once plus
`auto|zh|en`, enters a bounded worker, and emits correlated started/progress
events. The terminal response contains ownership, bounded final text, resolved
`zh|en`, and finite language probability. Pinned OpenCC `t2s` processes the
complete result, including Chinese inside English-dominant text. Partial
transcripts, PCM, model paths, and native diagnostics never appear in results.

Models resolve only to complete local
`models/weights/faster-whisper/<model>` directories; no implicit downloads.
Dependencies are separate in `requirements-stt.txt`, outside protocol fixtures.

STT and Chat generation are mutually exclusive to bound model resource use.
Cancellation/timeout are logically immediate, but native inference cannot be
safely killed: the physical slot remains busy until it returns and its late
result is discarded. Shutdown closes admission and suppresses losing callbacks.

After exact session validation, Dictate appends final text without sending;
Voice Call submits it through the normal Chat path and captures again after
the reply and expected speech drain. This is a renderer-owned utterance loop,
not partial recognition or gapless streaming. The protocol operation itself
has no Chat side effect.

### Speech and playback

`chat.stream` / `chat.retry` require Boolean `speakReply`. Text Chat sends
false and creates no speech turn or zero-work terminal. Voice Call sends true;
when automatic speech is enabled, exact reply chunks feed a bounded sentence
FIFO. Chat owns the canonical stream and commit; cancelled/failed speech
cannot commit a partial message.

`voice.speech.start` accepts only active `chatId` and persisted
`assistantMessageId`. Python loads canonical Assistant content; arbitrary text,
paths, profiles, and synthesis options are rejected. Its acknowledgement
echoes kind, new speech-owning request ID, Chat ID, and Assistant message ID.

`voice.speech.cancel` binds the originating request ID and Chat ID.
Its response echoes that pair, `kind: "voice.speech.cancel"`, and `stopped`.
False is idempotent when that owner already ended; it never cancels another
turn. Generic `request.cancel` / `shutdown` retain their bare
`{"stopped": true}` result.

The managed worker binds one guarded lease to a resolved local Profile.
Runtime/checkpoints/reference audio and the ignored
`workspace/settings/voice-profiles.json` catalog are not packaged fixtures.
The separate loopback `/tts` smoke is Python-only;
`service_binding_unverified` confirms compatibility, not loaded-weight identity.
The managed partial manifest proves parent/worker consistency, not complete
third-party provenance; synthesized-audio caching remains disabled.

General Python `SynthesisResult` allows up to 32 MiB of framed audio without
guaranteeing codec decode. Desktop accepts only 32 kHz mono PCM16 WAV up to
8 MiB. To avoid Base64 expansion inside NDJSON, clips use inherited fd3 with
a 256-bit token, uint32 sequence, bounded length, and SHA-256 header.
Main validates incrementally and admits one unacknowledged frame for backpressure.

Closed `voice.speech.clip|failure|terminal` events carry matching metadata,
stable errors, and terminal counters, never text, audio, paths, Profile details,
or diagnostics. Main pairs the next frame with exact metadata and sends WAV
privately to preload. React receives sanitized status, not bytes/tokens/digests.
Preload validates device/decode/lifecycle/correlation and reports a closed
outcome. Window replacement and shutdown retire stale playback without making
text Chat depend on speech.

## Images

`image.generate` accepts only Profile ID, size preset, seed, and eight bounded
prompt components: `appearance`, `outfit`, `pose`, `scene`, `style`, `camera`,
`lighting`, `negativePrompt`. The current product branch requires literal
`characterId: "elysia"` plus `fictionalAdultConfirmed: true`; the compatible
original-character branch instead requires `originalFictionalConfirmed: true`.
Control characters, lone surrogates, and unknown fields (including
`contentMode`, `adultMode`, `nsfw`, `protection`) are rejected. Explicit adult
text inside a prompt component uses the same request path and is not filtered;
minor, real-person, multiple-subject, unreviewed asset, workflow, and model-path
requests remain outside the contract.

Progress always uses `operation: "image.generate"`, `total: 100`, and fixed
checkpoints: `validating/5`, `probing/15`, `submitting/25`, `sampling/35`,
`verifying/90`, `completed/100`. Cancel targets the original request ID.

Gallery list/get/delete/open use `gallery_<32 lowercase hex>` IDs; generation
also returns `imageop_<32 lowercase hex>`. Lists accept limits 1–255 and
nonnegative offsets, sort unique items newest first by canonical
`YYYY-MM-DDTHH:mm:ss.ffffffZ` then ID, and allow `hasMore` only on full pages.
Metadata is limited to ID, creation time, bounded dimensions/bytes, SHA-256,
`image/png`, Profile ID, and seed.

Open returns ID, nonzero 256-bit preview token, length, digest, media type,
and dimensions matching the next private image-channel frame. Paths, prompts,
workflows, PNG bytes, and Base64 never enter the JSON result.

## Attachments and Knowledge

`attachment.list|add|remove` act on one exact Chat/Project scope. Main supplies
native source paths privately; they never return in metadata or enter manifests.
Public state contains opaque ID, basename, media type, bytes, ready status, and
intake limits. Older oversized drafts remain visible/removable after a limit
reduction. `chat.stream.attachmentIds` claims ready items in that Chat only:
cancel restores drafts; commit reconciles blobs to messages. Attachments are
not automatically parsed, indexed, or promoted into a Project corpus.

Knowledge methods are `knowledge.list`, `knowledge.source.add|replace|reindex|delete|export`,
`knowledge.project.rebuild|revoke`, and `knowledge.recover`.
Add/replace accept Main-selected sources; export accepts only the native Save
destination. Paths never enter public events, history, or the renderer API.

List exposes closed source states `ready|unindexed|stale|revoked|processing`
and bounded durable operation snapshots. Long mutations run outside the
reader and emit correlated progress plus `knowledge.operation.changed` or
terminal-only `knowledge.operation.completed`. Cooperative cancellation stops
before safe commit. Recovery and export expose no fake cancellation or React Stop.

Export is a separate background task under the global Knowledge lease, without
a lifecycle journal/checkpoint. Its `knowledge.export` receipt contains only
safe filename, media type, and written bytes, matched against metadata pinned
before the Save dialog. Private cleanup intent persists; destination paths do not.

One Backend lifecycle/export task owns the global lease, excluding grounded
answers and Project-authority writes. Electron mirrors admission so competing
lists, exports, mutations, and grounded requests are rejected rather than queued.
`BackendSnapshot.activeKnowledgeOperation` preserves exact export request and
Project ownership across reload with `cancellable: false`. After validated
settlement releases the lease, Main emits `knowledge-export-settled` or the
correlated error. These are Electron events, not new Python wire events.
Archived Projects are read-only.

`chat.stream` / `chat.retry` accept optional Boolean `useProjectKnowledge`.
Omitted/false preserves ordinary Chat; true authorizes only the corpus derived
from the canonical active Project relationship. Only Assistant history may
contain closed `groundedAnswer` proof: status, bounded statement kinds, exact
citation closure, safe metadata, excerpts, and text/table locations.
Unknown fields, paths, hashes, vectors, prompts, native diagnostics, and
inconsistent states are rejected.
