# Production Data Layout

## 1. Purpose

Elysia separates replaceable program files from private runtime data. An
application update may replace the Electron bundle and Python code, but it must
not select the install directory as a writable data location or overwrite a
Chat, Project, Memory record, Source, setting, or index.

This document defines the Stage 14 data-location contract. Backup and restore,
schema rollback, secrets, packaged Python, and installer retention policy are
separate later modules; moving the live directory is not presented as a
backup.

## 2. Owners and roots

Electron Main is the only authority that selects the Python data root. It keeps
the small `data-storage-bootstrap.json` pointer under Electron's stable
`userData` directory and passes the selected absolute path to the child as
`ELYSIA_DATA_ROOT`. Renderer code can request a native folder picker and show
the active location, but it cannot submit an arbitrary filesystem path.

Python keeps two deliberately different roots:

- `AppSettings.base_dir` is the read-only resource root containing Python code,
  worker scripts, and locally installed model/component assets.
- `AppSettings.data_layout.root` is the movable private-data root. Every
  production repository, log, and application-owned cache derives from it.

Electron's device-local window preferences and Chromium profile remain under
Electron `userData`. That stable platform state includes theme, character
performance choice, crash-safe unsent Chat drafts, Desktop Pet preferences,
notification preferences, and Chromium's own caches. Moving the Python data
root does not claim to move the Windows/Chromium profile. Ollama and optional
model runtimes are independently owned components and are never deleted by the
ordinary data cleanup action.

## 3. Versioned root layout

Each active root has a bounded `layout.json` manifest with layout version 1 and
a generated root ID. A directory without a valid manifest is never treated as
an old Elysia root that may be removed.

```text
<data-root>/
├── layout.json
├── workspace/
│   ├── settings/          global, audio-device, and voice-profile settings
│   ├── chats/             Chat index and individual sessions
│   ├── projects/          Project catalog and workspace bindings
│   ├── conversations/     legacy conversation and summary records
│   ├── memory/            profile and long-term Memory
│   ├── migrations/        migration markers and recovery backups
│   ├── recovery/          validated import quarantine and recovery journal
│   ├── attachments/       private original files and scope manifests
│   └── knowledge/         Source authority, operation journal, and SQLite index
├── cache/                 application-owned, rebuildable cache only
├── logs/                  local application diagnostics
└── audio/                 reserved temporary spill category; normally absent
```

Microphone PCM and synthesized WAV data currently travel only through bounded
memory and the private fd3 channel. Elysia does not create `audio/` during
normal initialization and does not retain recordings. The reserved category
exists so any future, explicitly approved temporary spill policy has one
bounded cleanup location instead of writing inside Chats or Sources.

Project `workspacePath` values and Knowledge export destinations point to
user-selected external locations. Moving Elysia data never rebases those
external paths.

## 4. Capacity categories

Main scans the active root without following symbolic links or Windows reparse
points. The scan is bounded by entry and byte limits and returns a one-use scan
token, measurement time, free space, total bytes, file count, blocked-entry
count, and these closed categories:

| Category | Contents | Direct cleanup |
| --- | --- | --- |
| Configuration | `workspace/settings` plus import/recovery metadata | No |
| Chats | Chats, legacy conversations, and migration state | No |
| Projects | Project catalog and external workspace bindings | No |
| Memory | Profile and long-term Memory | No |
| Sources | Attachment originals, Source authority, and lifecycle journals | No |
| Indexes | SQLite vectors and associated index artifacts | No |
| Temporary audio | Only application-owned temporary spill files | Yes |
| Cache | Only application-owned rebuildable cache | Yes |
| Logs | Local application logs | Yes |
| Other | Unknown or future entries | No |

Indexes are coupled to the Source catalog and are changed through Knowledge
lifecycle operations, not raw filesystem cleanup. `models/cache` is an
external GPT-SoVITS component despite its historical name and is not this
table's Cache category.

## 5. First migration and directory movement

On the first desktop run after this layout is introduced, an existing source
checkout's `workspace/` may be copied into the default data root. The source is
preserved so this compatibility step cannot destroy development data. Logs,
model weights, third-party runtimes, build output, and caches are not imported.

An interactive move follows a two-phase transaction:

1. Main rejects active Chat, Voice, Knowledge, restart, or persistence work.
2. A native picker selects an empty absolute destination. Root directories,
   links/reparse points, the resource root, and nested source/destination pairs
   are rejected.
3. The Backend stops, releasing JSON locks, attachment leases, logs, and
   SQLite handles.
4. Main copies into a sibling staging directory and verifies a bounded,
   deterministic SHA-256 inventory before publishing the new manifest and
   bootstrap revision.
5. Python restarts with the new absolute root and must reach `ready`.
6. Only after readiness may Main remove the old root. Removal requires the
   exact transaction, old root ID, valid manifest, no blocked or unknown
   entries, and the same SHA-256 inventory copied in step 4. Main atomically
   isolates that root and verifies it again before exact-file removal; any
   late writer or identity change retains the copy with a warning. Both the
   original and isolation candidates are journaled in the stable bootstrap
   before deletion, so any surviving recovery copy remains visible in Settings
   across a crash or application restart.
7. Any copy, verification, pointer, or Backend failure rolls the pointer back,
   restarts the old root, and removes the transaction-owned destination only
   when its identity and fingerprint remain exact. A changed, blocked, or
   unverifiable destination is retained and journaled for manual review.

The stable bootstrap makes a completed pointer switch survive application
updates. A crash may leave a verified duplicate, but never authorizes deletion
of an unmarked or unfamiliar directory.

## 6. Safe cleanup

Cleanup is deliberately narrower than deletion. Main accepts only the fixed
temporary-audio, application-cache, and log categories, together with the
current storage revision and one-use scan token. It asks for native
confirmation, stops the Backend, removes only the exact owned directories,
restarts the same data root (which recreates any required runtime directory),
and rescans.

Chats, Projects, Memory, configuration, original attachments, Sources,
Knowledge journals, indexes, migration/recovery state, model weights,
GPT-SoVITS components, reference audio, and Ollama data have no generic cleanup
capability. Their domain-specific delete, revoke, rebuild, uninstall, backup,
or restore flows remain the only valid owners. Recovery copies retained from a
move are displayed as exact paths in Settings and are never included in generic
temporary cleanup; this module deliberately requires manual review instead of
guessing that a durable duplicate is disposable. A recorded path is not erased
merely because it is missing during startup: removable and network volumes may
be temporarily offline, so only the exact transaction that just completed a
verified deletion can clear its journal candidates.

## 7. Verification boundary

Module 1 automation covers exact path derivation, invalid root rejection,
legacy copy without source deletion, manifest and bootstrap validation,
bounded category scans, symlink refusal, staged copy verification, revision
conflicts, rollback, guarded old-root retention, cleanup allowlisting, Backend
environment injection, Settings behavior while Python is unavailable, and
upgrade-safe package exclusions. It also covers cross-restart discovery of a
retained recovery root and startup rollback when a pending destination volume
has disappeared. Clean-machine NSIS upgrade/uninstall tests and
complete backup/restore acceptance remain Stage 14 Modules 3, 5, and 7.
