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
`attachment.management`, `voice.settings`, `voice.capture`, and the optional
`voice.transcription` capability.
Both new-turn and retry generation reuse the `chat.reply` stream.
Cancellation succeeds only before generation claims its atomic commit gate, so
a successful Stop response guarantees that the interrupted turn is not saved.
Backend permission prompts retain their stable schema but are not yet
advertised as an active capability.

`settings.get` and `settings.update` expose one exact eight-field public
allowlist with optimistic revision checks: Chat model, Ollama origin, two
Memory limits, import byte limit, transcription model, transcription device,
and transcription language. The STT fields are closed enums:
`tiny|base|small|medium|large-v3|turbo`, `auto|cuda|cpu`, and `auto|zh|en`.
Saved changes are reported separately from active values and take effect after
Backend restart. API keys, tokens, passwords, base paths, environment data,
and arbitrary extension fields are rejected. Authenticated Settings reads
remain available when Brain initialization fails or STT is active so the
desktop can diagnose state; configuration mutations are rejected while a
transcription is active or physically draining.

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
utterance. It is reachable only after the user explicitly starts microphone
capture in the renderer. The renderer downmixes and resamples input to 16 kHz
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
`en` language, and a finite language probability. It never contains PCM, a
model path, or native-library diagnostics.

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
admission and suppresses callbacks that lose the shutdown race. Electron now
correlates this optional capability to the originating Voice session and Chat,
then exposes only the final sanitized transcript to React. React lets the user
edit it and explicitly place or append it into the current Composer draft; it
does not send a message automatically. No partial recognized text crosses the
wire in this slice. Real-time partial transcripts remain part of future
continuous Voice rather than this bounded final-result contract.

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
contents are stored locally but are not read, parsed, or indexed in this
protocol milestone.

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
