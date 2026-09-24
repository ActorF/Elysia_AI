# File Metadata and Storage Architecture

> Implementation baseline: 2026-09-23
>
> Scope: Stage 8 Module 1, local original-file metadata and storage
>
> Boundary: storage only; document parsing and retrieval begin in later modules

## 1. Outcome

The attachment subsystem now has one versioned, path-private contract for Chat Attachments and Project Sources. It can:

- assign a content-stable File ID and SHA-256 digest;
- retain safe display name, media type, byte size, origin, UTC import time, Scope, and ownership role;
- store one immutable original per content value inside an exact owner Scope;
- preserve multiple ownership links to that original;
- record an explicit Original-to-Derived relationship for later loaders;
- migrate the previous attachment manifest without losing bytes or attachment IDs;
- open an original only by validated Scope and opaque File ID, with integrity verification;
- roll back cancellation and ordinary import failures without publishing partial metadata; and
- coordinate one Chat deletion or a Project-plus-linked-Chats deletion through a recoverable storage transaction.

The canonical implementation lives in `attachments/`. `desktop_backend.py` constructs an `AttachmentService` over `JsonAttachmentStore`; callers depend on the `AttachmentRepository` protocol instead of reaching into storage paths.

## 2. Trust boundary

The file path exists only while trusted Electron/Python code imports a native selection:

```text
native file selection
    │ absolute source path, trusted boundary only
    ▼
AttachmentService.stage_files(...)
    ▼
AttachmentRepository
    ▼
JsonAttachmentStore
    ├── versioned manifest
    └── immutable original blob

trusted loader
    │ AttachmentScope + opaque file_id
    ▼
AttachmentService.open_verified_file(...)
    └── verified bounded file stream; no storage path returned

Renderer
    └── attachment_id, display name, media type, size, ready status, limits
```

The Renderer does not receive the source path, storage path, File ID, SHA-256 digest, import origin, internal timestamp, manifest state, or native exception details. A File ID alone is not a read capability: the same request must also carry the exact validated owner Scope.

The boundary treats Renderer/native-path input, crashes, accidental workspace corruption, and a second cooperating Backend as untrusted. The attachment tree itself is private application data protected by the current Windows user's ACLs and the Backend process lease. A malicious process already running as that same user and concurrently replacing validated directories with junctions is outside this module's threat model; closing that stronger boundary requires installer-owned read-only storage or Windows handle-relative traversal/deletion under a restricted identity. Descriptor pinning and repeated reparse/hard-link checks remain defense in depth, but are not presented as isolation from a fully compromised user account.

## 3. Domain model

Manifest schema version and metadata-record schema version are separate on purpose. The current manifest is version 2; the public metadata values begin at record schema version 1.

### 3.1 Original file

`OriginalFileMetadata` describes immutable content without retaining a path.

| Field | Contract |
| --- | --- |
| `schema_version` | Exact metadata schema version; currently `1` |
| `file_id` | `file_` followed by the canonical lowercase SHA-256 digest |
| `sha256` | 64 lowercase hexadecimal characters |
| `file_name` | Safe cross-platform display basename, never a path |
| `media_type` | Normalized type inferred by trusted storage code from the admitted extension |
| `size_bytes` | Positive byte count admitted by the configured limit in effect when imported |
| `origin` | Closed value: `local_import`, `legacy_migration`, or `generated` |
| `imported_at` | Timezone-aware UTC timestamp |

The File ID is content-stable: equal bytes produce the same ID. It is still Backend-private because exposing a content digest would reveal equality information that the UI does not need.

### 3.2 Ownership link

`FileOwnership` binds an original to one exact owner namespace.

| Scope | Required role | Meaning |
| --- | --- | --- |
| `chat_<id>` | `chat_attachment` | A file attached to one Chat/message lifecycle |
| `project_<id>` | `project_source` | A source retained by one Project |

The link has its own opaque `attachment_<id>`, safe display name, media type, Scope, role, and import time. This separation matters because a committed file may be selected again under another name: both links can point to one original while preserving their own user-visible metadata.

### 3.3 Derived relation

`DerivedFileRelation` records:

- the owning Scope;
- the original File ID;
- a distinct derived File ID;
- a stable lowercase derivation kind;
- the producer version; and
- an aware UTC creation time.

The original must exist in that same Scope. Derived IDs are unique within the manifest, self-relations are rejected, and removing the final link to an original removes its relations.

Module 1 stores relation metadata only. It does not create, accept, or read derived bytes; the bounded derived-data writer belongs to a later document-processing module.

## 4. Physical layout and Manifest v2

Storage remains below the local workspace:

```text
workspace/attachments/
├── .backend.lock
├── chat/
│   └── chat_<id>/
│       ├── manifest.json
│       └── originals/
│           └── file_<sha256>.blob
└── project/
    └── project_<id>/
        ├── manifest.json
        └── originals/
            └── file_<sha256>.blob
```

Legacy `drafts/` and `committed/` directories can be encountered during migration or cleanup, but Manifest v2 does not use lifecycle-specific blob copies. Ready, claimed, and committed are ownership-link states in the manifest; the immutable original stays in `originals/`.

A Manifest v2 document has exactly four root fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Exact value `2` |
| `scope` | Exact `kind` and `id` for the containing namespace |
| `items` | Ownership links with repeated integrity metadata and lifecycle status |
| `derived` | Path-free Original-to-Derived relation records |

Each item contains exactly:

```text
record_schema_version, attachment_id, file_id, file_name, media_type,
size_bytes, sha256, origin, imported_at, link_file_name, link_media_type,
linked_at, role, status
```

The compact persisted item intentionally repeats immutable original metadata beside link-specific display metadata. Every item sharing a File ID must repeat the same canonical original values, while `link_*` values may differ. `list_file_records()` collapses items by File ID, while `list_file_ownerships()` returns every link. Strict field sets, duplicate-key rejection, canonical IDs, role matching, count limits, Scope matching, canonical-original consistency, and same-Scope derived parents are validated on every load. Unknown future manifest versions fail closed rather than guessing their meaning or deleting their bytes.

## 5. Why content addressing is Scope-local

Deduplication occurs inside one exact Chat or Project Scope, not across the whole application.

- Equal active selections in one Scope do not create duplicate active links.
- Reimporting equal bytes after a Chat link has committed creates a new ownership link but reuses the one original blob.
- Equal bytes imported into different Scopes are stored independently.

The last rule deliberately trades some disk efficiency for isolation. A global blob pool could turn content equality into a cross-Project side channel and would couple deletion and authorization across unrelated owners. Scope-local content-addressed storage keeps read authorization, reconciliation, and deletion decidable from one owner namespace.

## 6. Import, cancellation, and atomic publication

An import batch follows this order:

1. Validate the owner Scope and configured file-count limit.
2. Require an absolute non-UNC source outside attachment storage.
3. Reject unsafe basenames, unsupported extensions, empty/oversized files, symbolic links, hard links, reparse points, redirected parent components, and non-regular files.
4. Pin the approved source device/inode, open without following links where supported, and compare the opened descriptor to that identity.
5. Stream into a Scope-local temporary file while enforcing the byte limit, polling cancellation, and calculating SHA-256.
6. Flush and `fsync` the temporary file, then confirm source identity, size, and modification time did not change during the copy.
7. Reuse or atomically publish the content-addressed original.
8. Poll cancellation once more and atomically replace the manifest last.

Cancellation is accepted only before the manifest commit. If cancellation or an ordinary error occurs first, temporary files and newly published-but-unreferenced originals are removed and the old manifest remains authoritative. This makes the outcome unambiguous: either the full batch appears, or none of its ownership links do.

An in-process lock serializes operations, and the store holds one exclusive process lease in `.backend.lock`. A second Backend cannot concurrently mutate the same attachment root.

## 7. Verified Backend reads

Trusted future loaders call `open_verified_file(scope, file_id)` instead of receiving a path.

The repository:

1. validates the opaque File ID;
2. proves that the manifest in the supplied Scope owns that File ID;
3. rejects symlink, reparse, special, or externally hard-linked storage entries;
4. opens the blob through a pinned descriptor and detects check-to-open identity changes;
5. re-hashes and re-counts the complete original under an absolute stored-file ceiling;
6. compares size, digest, descriptor identity, file size, and modification time; and
7. copies verified bytes into a bounded spooled snapshot and yields that snapshot while the repository lock remains held.

Small snapshots stay in memory and larger ones spill into an anonymous temporary file, so lowering the new-import setting cannot hide a valid older original or force multi-gigabyte RAM allocation. Tampered or cross-Scope reads fail with stable storage/not-found errors. No caller can convert this API into an arbitrary filesystem reader.

## 8. Migration and startup recovery

The store eagerly examines existing Scopes while holding its process lease.

### Manifest v1 migration

For a strict v1 manifest, the store:

- validates every legacy record and blob;
- derives the new File ID from the stored SHA-256;
- uses `legacy_migration` as origin;
- uses the legacy manifest modification time as the honest available import timestamp;
- moves verified `drafts/` or `committed/` bytes into `originals/file_<sha256>.blob`;
- re-verifies the moved target before committing metadata;
- canonicalizes equal-content Original metadata while retaining each legacy ownership link's display name, type, ID, and lifecycle status;
- publishes Manifest v2 only after the originals are ready; and
- removes legacy copies only after the v2 manifest commits.

Attachment IDs, display metadata, lifecycle status, and bytes are preserved. A crash after moving a blob is restartable because migration verifies and reuses an already-published target. An ordinary exception restores completed moves before returning.

### General recovery

Startup also:

- removes recognized temporary upload/manifest entries;
- releases claims that cannot survive a Backend restart;
- restores ambiguous `.pending` deletion tombstones to favor data preservation;
- purges `.tmp` tombstones whose owner deletion already committed; and
- fails closed on unknown hidden entries or redirected storage structures.

After Chat and Project repositories load, owner reconciliation deletes only Scopes whose canonical owner is absent. Per-Chat reference reconciliation then promotes referenced links to committed, removes unreferenced committed links, verifies retained originals, and removes orphan originals and derived relations. Every surviving Project Scope also reconciles with an empty message-reference set so crash-left originals and legacy leftovers are reclaimed while ready Project Sources remain intact.

## 9. Deletion and rollback

Removing a ready ownership link preserves its original while another link in the same Scope still references that content. Removing the final link commits the ownership/Derived metadata removal first, then reclaims the now-orphan original. A crash can therefore leave recoverable garbage, never a live manifest record whose only bytes were deleted.

Canonical owner deletion uses a tombstone transaction:

1. rename every participating Scope to an exact `.pending` tombstone;
2. invoke the canonical Chat/Project deletion callback;
3. restore all hidden Scopes if that callback fails;
4. after success, rename each tombstone to `.tmp` and purge it; and
5. let startup cleanup finish a purge interrupted by process termination.

A Project cascade passes the Project Scope and every linked Chat Scope to one `delete_scopes_with()` call. Hiding all namespaces before the canonical callback prevents a partial file deletion when the Project/Chat repository transaction rolls back.

## 10. Application interfaces

`AttachmentRepository` is the persistence contract. It contains both the established Chat attachment lifecycle and the new file-oriented operations:

- list safe attachment state;
- stage, remove, claim, release, and commit ownership links;
- reconcile references and canonical owners;
- list originals, ownership links, and derived relations;
- register a same-Scope derived relation;
- open one verified original by Scope and File ID;
- delete one or several owner Scopes through rollback callbacks; and
- close process-owned resources.

`AttachmentService` is the application boundary used by the Desktop Backend. It canonicalizes iterable input, validates exact ownership for derived relationships and verified reads, and provides owner-aware helpers for Chat and Project deletion. Tests can inject a structural Repository fake without depending on the filesystem implementation.

## 11. Verification map

| Test file | Evidence |
| --- | --- |
| `tests/test_attachment_file_domain.py` | Versioning, content-derived IDs, path-free fields, UTC time, safe names, origin vocabulary, ownership roles, and derived invariants |
| `tests/test_attachment_application_service.py` | Repository delegation, verified-read selectors, same-Scope relations, close ownership, and single/multi-owner delete boundaries |
| `tests/test_file_metadata_store.py` | Manifest v2 persistence, v1 migration, deduplication, cancellation cleanup, derived cascade, verified reads, integrity failure, multi-Scope rollback, and unknown-version fail-closed behavior |
| `tests/test_attachment_service.py` | Existing lifecycle, process lock, startup recovery, filesystem redirects, hard links, source replacement, tombstone grammar, and atomic failure behavior |
| `tests/test_desktop_backend.py` | Service integration plus canonical owner/reference reconciliation during Backend initialization |

Run the focused checks from Command Prompt:

```bat
cd /d D:\Elysia_AI
.venv\Scripts\python.exe -m pytest tests\test_attachment_file_domain.py tests\test_attachment_application_service.py tests\test_file_metadata_store.py tests\test_attachment_service.py tests\test_desktop_backend.py -q
.venv\Scripts\python.exe scripts\check_python_documentation.py
cd desktop
npm run docs:check
```

## 12. Explicit non-goals and next work

This baseline does not claim any of the following:

- file-signature or format-level parsing;
- TXT, Markdown, PDF, DOCX, CSV, or source-code loaders;
- encrypted/corrupt-document handling beyond safe opaque storage;
- normalized text or preview-byte generation;
- cleaning, structural chunking, offsets, pages, or tables;
- embeddings, a vector store, reranking, or retrieval;
- Prompt injection of file contents;
- grounded answers, citation rendering, or RAG UI; or
- a global deduplicated blob pool.

The next step is Stage 8 Module 2: trusted, bounded Document Loaders that consume `open_verified_file()` and produce explicit versioned outputs without weakening the Scope or Renderer privacy boundary.
