# Production Data Layout

Application files can be replaced during an update; private Chats, Projects,
Memory, Sources, settings and indexes must survive. Electron Main selects the
writable data root and passes it to Python as `ELYSIA_DATA_ROOT`.

## Roots and ownership

Main stores the `data-storage-bootstrap.json` pointer under Electron's stable
`userData` directory. Renderer can request a native folder picker, but cannot
submit an arbitrary path.

| Root | Purpose |
| --- | --- |
| `AppSettings.base_dir` | Read-only Python resources, workers and locally installed components |
| `AppSettings.data_layout.root` | Movable private data, logs and application-owned caches |
| Electron `userData` | Bootstrap pointer, Chromium profile and device-local preferences, including unsent Chat drafts |
| User-selected external folders | Project workspaces, exports and independently owned model runtimes |

Moving the Python root does not move Chromium's profile, rebase external
workspace/export paths, or relocate Ollama and optional model components. A
move is not a backup; backup, restore and installer retention have separate
acceptance requirements.

## Directory layout

A valid `layout.json` records layout version 1 and a generated root ID. An
unmarked directory is never eligible for old-root deletion.

```text
<data-root>/
├── layout.json
├── workspace/
│   ├── settings/          global, audio-device and voice-profile settings
│   ├── chats/             Chat index and sessions
│   ├── projects/          catalog and workspace bindings
│   ├── conversations/     legacy conversations and summaries
│   ├── memory/            profile and long-term Memory
│   ├── migrations/        markers and recovery backups
│   ├── recovery/          import quarantine and recovery journal
│   ├── attachments/       originals and scope manifests
│   ├── knowledge/         Sources, operation journal and SQLite index
│   ├── work/              workflow checkpoints and desktop review copies
│   ├── vision/history/    explicitly saved Vision images
│   └── gallery/           generated-image store; desktop integration pending
├── cache/                 rebuildable application cache
├── logs/                  local diagnostics
└── audio/song-covers/     temporary Song Cover jobs and results
```

Microphone PCM and synthesized speech stay in bounded memory and the private
fd3 channel. Song Cover uses the temporary audio directory on disk; its
unexported results follow the [Song Cover lifecycle](15-SONG-COVER.md).

## Capacity and cleanup

Main scans without following symbolic links or Windows reparse points. Entry
and byte limits bound the scan, which returns a one-use token, timestamp,
free space, totals, file counts and blocked-entry counts.

| Category | Contents | Generic cleanup |
| --- | --- | --- |
| Configuration | Settings and import/recovery metadata | No |
| Chats | Chats, legacy conversations and migration state | No |
| Projects | Catalog and external workspace bindings | No |
| Memory | Profile and long-term Memory | No |
| Sources | Originals, authority and lifecycle journals | No |
| Indexes | SQLite vectors and related artifacts | No |
| Temporary audio | Application-owned temporary audio jobs | Yes |
| Cache | Rebuildable application cache | Yes |
| Logs | Local diagnostics | Yes |
| Other | Unclassified entries, including Vision history | No |

Cleanup requires the current storage revision, a one-use scan token and native
confirmation. Main stops the Backend, removes only the selected owned
audio/cache/log directories, restarts the same root and rescans. Active jobs
must finish or release their cleanup ownership first.

Domain-specific operations own deletion, revocation, rebuilding and uninstall
for durable records and models. Indexes remain coupled to the Source catalog.
`models/cache` contains external components such as GPT-SoVITS; its name does
not place it in the application Cache category.

## Migration and movement

The first compatible desktop run may copy an existing checkout's `workspace/`
into the default root. It preserves the source and excludes logs, model
weights, third-party runtimes, build output and caches.

An interactive move is a two-phase transaction:

1. Reject active Chat, Voice, Knowledge, restart or persistence work.
2. Select an empty absolute destination through a native picker. Reject root
   directories, links/reparse points, the resource root and nested pairs.
3. Stop the Backend to release locks, attachment leases, logs and SQLite.
4. Copy into sibling staging and verify a bounded, deterministic SHA-256
   inventory before publishing the manifest and bootstrap revision.
5. Restart Python at the new root and wait for `ready`.
6. Remove the old root only if its transaction, root ID, manifest and inventory
   still match, with no blocked or unknown entries. Atomically isolate and
   recheck it before exact-file removal. A late writer or changed identity
   causes retention with a warning.
7. On copy, verification, pointer or Backend failure, restore the pointer and
   restart the old root. Remove the transaction-owned destination only if its
   identity and fingerprint remain exact; otherwise retain it for review.

The stable bootstrap journals both original and isolation candidates before
deletion, so surviving copies remain visible in Settings after a crash. Missing
paths remain recorded because removable or network volumes may be offline.
Only the transaction that verifies deletion can clear its candidates. Recovery
copies never enter generic temporary cleanup.

## Verification

Storage tests cover path derivation, root/manifest validation, legacy copy,
bounded scans, links, staged verification, revision conflicts, rollback,
retained recovery copies, cleanup allowlists, Backend environment injection
and Settings access while Python is unavailable. Startup recovery also checks
a pending destination that has disappeared.

Clean-machine installer upgrade/uninstall and complete backup/restore
acceptance remain separate checks. See the [documentation index](README.md)
for related storage and feature contracts.
