# File Metadata and Storage

Chat Attachments and Project Sources share immutable originals, versioned metadata, and exact owner scopes. The `attachments/` package owns storage; `AttachmentService` exposes the `AttachmentRepository` contract to the Desktop Backend and document loaders.

Parsing and knowledge features are implemented downstream. Start with [Document Loaders](./05-DOCUMENT-LOADERS.md), or see [Knowledge UI](./12-KNOWLEDGE-UI-TESTING.md) for the complete desktop path.

## Trust boundary

```text
Native file selection → AttachmentService → JsonAttachmentStore
                                              ├─ manifest.json
                                              └─ immutable original

Trusted loader → exact Scope + opaque File ID → verified byte snapshot
Renderer       → attachment ID + display metadata + status + limits
```

Native paths exist only in trusted Electron/Python import code. The Renderer receives no source/storage path, File ID, content digest, internal manifest state, or native diagnostics. A File ID alone grants no access: reads also require the exact owner Scope.

The boundary covers untrusted selections, crashes, accidental corruption, and a second cooperating Backend. Storage relies on the current Windows user's ACLs and an exclusive Backend lease. Descriptor pinning and link checks are defense in depth; they do not isolate data from a malicious process already running as that user and replacing directories concurrently. Stronger isolation would require restricted-identity, handle-relative access or installer-owned storage.

## Data model

Manifest schema and metadata-record schema are versioned separately: the current values are `2` and `1`.

| Value | Contract |
| --- | --- |
| `OriginalFileMetadata` | `file_<sha256>` identity, lowercase digest, safe display basename, normalized MIME, positive size, origin, aware UTC import time |
| `FileOwnership` | Opaque `attachment_<id>`, exact Scope, role, link-specific display name/MIME and import time |
| `DerivedFileRelation` | Same-Scope original and distinct derived IDs, derivation kind, producer version and UTC creation time |
| `FileCatalogSnapshot` | Originals and ownerships from one manifest read, plus an order-independent fingerprint |

Origins are `local_import`, `legacy_migration`, or `generated`. Chat ownership requires `chat_attachment`; Project ownership requires `project_source`. Derived parents must exist in the same Scope; derived IDs are unique, self-relations are invalid, and removing the final ownership removes its relations. This package stores relation metadata, not derived bytes.

Equal bytes have equal File IDs, but deduplication is Scope-local. An active duplicate selection reuses its link; reimporting a committed Chat file creates a new link to the same original. Another Chat or Project stores its own copy. This avoids cross-owner equality disclosure and keeps authorization and deletion independent.

## Storage layout

```text
workspace/attachments/
├── .backend.lock
├── chat/chat_<id>/
│   ├── manifest.json
│   └── originals/file_<sha256>.blob
└── project/project_<id>/
    ├── manifest.json
    └── originals/file_<sha256>.blob
```

The workspace is resolved by the [application data layout](./13-PRODUCTION-DATA-LAYOUT.md). `ready`, `claimed`, and `committed` are ownership states; they do not create lifecycle-specific blob copies. Legacy `drafts/` and `committed/` directories are handled during migration.

A v2 manifest has exactly `schema_version`, `scope`, `items`, and `derived`. Each item contains:

```text
record_schema_version, attachment_id, file_id, file_name, media_type,
size_bytes, sha256, origin, imported_at, link_file_name, link_media_type,
linked_at, role, status
```

Original metadata is repeated per ownership and must agree for every shared File ID; `link_*` values may differ. Every load checks exact field sets, duplicate keys, canonical IDs, role/Scope matching, counts, original consistency, and derived parents. Unknown versions fail closed without guessing their meaning or deleting bytes.

## Import and verified reads

An import validates Scope and count limits, then requires an absolute non-UNC source outside attachment storage. It rejects unsafe names, unsupported extensions, empty/oversized or non-regular files, symlinks, hard links, reparse points, and redirected parents.

The store pins the source identity, streams into a Scope-local temporary file while hashing and polling cancellation, flushes and `fsync`s, then rechecks identity, size, and modification time. It publishes or reuses the immutable original and atomically replaces the manifest **last**.

Cancellation succeeds only before manifest commit. Earlier cancellation or an ordinary failure removes temporary files and newly unreferenced originals, leaving the previous manifest authoritative. An in-process lock and exclusive `.backend.lock` lease serialize writers.

`open_verified_file(scope, file_id)` proves ownership, rejects unsafe storage entries, opens a pinned descriptor, and verifies complete size/digest/identity under an absolute stored-file ceiling. It yields a bounded spooled snapshot while holding the repository lock. Small snapshots remain in memory; larger ones use an anonymous temporary file. Lowering the new-import limit therefore does not hide valid older originals or require unbounded RAM.

`DocumentLoaderService` builds its own bounded immutable bytes and closes the verified-read context before calling a format adapter. Parsers receive neither storage paths nor live attachment handles. Tampering and cross-Scope access return stable storage/not-found errors.

## Migration and recovery

For a strict v1 manifest, migration verifies each legacy blob, derives its content ID, records `legacy_migration`, and uses the legacy manifest modification time as the available import timestamp. It moves originals into the new layout, re-verifies them, and publishes v2 before removing legacy copies. Attachment IDs, display metadata, status, and bytes survive. Completed moves are restored on ordinary failure; an interrupted migration can verify and reuse an already moved target.

Startup holds the process lease while removing recognized upload/manifest temporary files, releasing restart-invalid claims, and validating existing scopes. Ambiguous `.pending` deletion tombstones are restored; committed `.tmp` tombstones are purged. Unknown hidden entries and redirected structures fail closed.

After canonical Chat/Project repositories load, owner reconciliation removes absent owners. Chat reference reconciliation commits referenced links, removes unreferenced committed links, verifies retained originals, and reclaims orphans. Project scopes reconcile with an empty message-reference set, preserving ready Project Sources while reclaiming crash leftovers.

## Deletion and rollback

Removing one ready ownership preserves shared originals. Removing the final link commits metadata and derived-relation removal before reclaiming bytes: a crash may leave garbage, but cannot leave a live record pointing to deleted content.

Canonical owner deletion uses a tombstone transaction:

1. Rename all participating scopes to exact `.pending` tombstones.
2. Invoke the canonical Chat/Project deletion callback.
3. Restore every scope if the callback fails.
4. On success, rename to `.tmp` and purge; startup finishes interrupted cleanup.

Project cascade deletion passes the Project and linked Chat scopes to one `delete_scopes_with()` call, so repository rollback cannot leave partial file deletion.

Project knowledge deletion also removes vectors and authorization through [Knowledge Lifecycle](./11-KNOWLEDGE-LIFECYCLE.md). Storage itself does not parse documents, index content, or authorize grounded answers.

## Verification

The focused suite covers metadata invariants, verified reads, deduplication, migration, cancellation, filesystem redirection, tombstone recovery, and canonical-owner reconciliation. Run from the repository root:

```powershell
python -m pytest tests/test_attachment_file_domain.py tests/test_attachment_application_service.py tests/test_file_metadata_store.py tests/test_attachment_service.py tests/test_desktop_backend.py -q
```

Repository-wide documentation checks are listed in [AGENTS.md](../AGENTS.md).
