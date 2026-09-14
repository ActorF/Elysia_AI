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

`settings.get` and `settings.update` expose one exact five-field public
allowlist with optimistic revision checks. API keys, tokens, passwords, base
paths, environment data, and arbitrary extension fields are rejected. The
authenticated Settings methods remain available when Brain initialization
fails so the desktop can repair an invalid saved model or Ollama origin.

`voice.settings.get` and `voice.settings.update` persist only the desired
opaque microphone and speaker IDs, with `null` meaning the current system
default. They use independent optimistic revisions and remain available when
Brain initialization fails or Chat generation is active. Device labels,
Windows permission state, live availability, and audio samples never cross
this Python protocol boundary; Electron owns those transient hardware details.

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

Transcription and Chat generation are mutually exclusive because the first
local implementation must not overcommit CPU/GPU model resources. Cancellation
and timeout are immediate logical terminal states. Python cannot safely kill a
thread executing native inference, so that physical worker remains occupied
until the call returns; the late result is discarded and new STT or Chat work
continues to receive a busy response during that drain. Shutdown closes
admission and suppresses callbacks that lose the shutdown race. This protocol
surface is implemented in Python, while the current Renderer still uses only
the receipt method and does not yet expose editable transcripts.

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
