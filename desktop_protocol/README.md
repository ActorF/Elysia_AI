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
`event`, `chat.sessions`, `project.management`, and `settings.management`.
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
