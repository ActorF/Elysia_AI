"""Verify durable scope isolation and fail-closed vector persistence."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import struct

import pytest

from attachments.domain import AttachmentScope
import documents.vector_store as vector_store_module
from documents.chunking import (
    DocumentChunkingPolicy,
    StructureAwareDocumentChunker,
)
from documents.cleaning import ConservativeDocumentCleaner
from documents.domain import (
    DOCUMENT_SCHEMA_VERSION,
    DocumentBlock,
    DocumentLoadLimits,
    DocumentSource,
    LoadedDocument,
)
from documents.embedding import (
    DOCUMENT_EMBEDDING_TEMPLATE_VERSION,
    EMBEDDED_DOCUMENT_SCHEMA_VERSION,
    EMBEDDING_SCHEMA_VERSION,
    QUERY_EMBEDDING_TEMPLATE_VERSION,
    DocumentEmbeddingService,
    EmbeddedDocument,
    EmbeddingBatch,
    EmbeddingBatchPolicy,
    EmbeddingModelIdentity,
    EmbeddingRequest,
    EmbeddingVector,
)
from documents.exceptions import (
    DocumentContentLimitError,
    DocumentCorruptError,
    DocumentValidationError,
)
from documents.vector_store import (
    VECTOR_ENCODING,
    VECTOR_STORE_SCHEMA_VERSION,
    SQLiteVectorStore,
    VectorStoreLimits,
    VectorStoreModelMismatchError,
    VectorStorePersistenceError,
    VectorStoreStaleError,
)


class _UnitVectorEmbedder:
    """Return deterministic unit vectors for persistence-only tests."""

    def __init__(self, identity: EmbeddingModelIdentity) -> None:
        self._identity = identity
        self._policy = EmbeddingBatchPolicy()

    @property
    def identity(self) -> EmbeddingModelIdentity:
        """Return the fixed identity supplied by the fixture."""

        return self._identity

    @property
    def policy(self) -> EmbeddingBatchPolicy:
        """Return the fixed v1 embedding policy."""

        return self._policy

    def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        """Return one exact ordered unit vector for every requested item."""

        vector = (1.0,) + (0.0,) * (self.identity.dimension - 1)
        return EmbeddingBatch(
            schema_version=EMBEDDING_SCHEMA_VERSION,
            identity=self.identity,
            policy=self.policy,
            purpose=request.purpose,
            vectors=tuple(
                EmbeddingVector(item_id=item.item_id, values=vector)
                for item in request.items
            ),
        )


def _identity(*, digest_character: str = "a") -> EmbeddingModelIdentity:
    """Build one small but fully versioned test embedding space."""

    return EmbeddingModelIdentity(
        provider="fixture",
        adapter_id="fixture-http",
        adapter_version="1.0.0",
        model_tag="fixture-embedding:1",
        model_digest=digest_character * 64,
        dimension=3,
        normalization="l2",
        document_template_version=DOCUMENT_EMBEDDING_TEMPLATE_VERSION,
        query_template_version=QUERY_EMBEDDING_TEMPLATE_VERSION,
    )


def _scope(kind: str, suffix: str) -> AttachmentScope:
    """Build one valid Chat or Project attachment scope."""

    prefix = "chat" if kind == "chat" else "project"
    return AttachmentScope(kind=kind, id=f"{prefix}_{suffix}")  # type: ignore[arg-type]


def _embedded_document(
    scope: AttachmentScope,
    *,
    link_id: str,
    texts: tuple[str, ...],
    identity: EmbeddingModelIdentity | None = None,
) -> EmbeddedDocument:
    """Run real cleaning/chunking/embedding domains for one test document."""

    model_identity = _identity() if identity is None else identity
    source = DocumentSource(
        scope=scope,
        link_id=link_id,
        file_id=f"file_{hashlib.sha256('|'.join(texts).encode()).hexdigest()}",
        file_name="source.txt",
        media_type="text/plain",
        size_bytes=max(1, sum(len(text) for text in texts)),
    )
    loaded = LoadedDocument(
        schema_version=DOCUMENT_SCHEMA_VERSION,
        source=source,
        document_format="text",
        loader_id="vector-test-loader",
        loader_version="1.0.0",
        blocks=tuple(
            DocumentBlock(ordinal, "code", text)
            for ordinal, text in enumerate(texts)
        ),
        limits=DocumentLoadLimits(),
    )
    chunked = StructureAwareDocumentChunker(
        policy=DocumentChunkingPolicy(max_chunk_code_points=2_000),
    ).chunk(ConservativeDocumentCleaner().clean(loaded))
    return DocumentEmbeddingService(
        _UnitVectorEmbedder(model_identity)
    ).embed_document(chunked)


def _list(
    store: SQLiteVectorStore,
    document: EmbeddedDocument,
    *,
    limit: int = 100,
    after_ordinal: int | None = None,
):
    """Read one fixture document through every mandatory identity guard."""

    return store.list_document(
        document.source.scope,
        document.source.link_id,
        expected_derivation_fingerprint=document.derivation_fingerprint,
        expected_identity=document.identity,
        limit=limit,
        after_ordinal=after_ordinal,
    )


def _mutate_database(path: Path, statement: str, values: tuple[object, ...]) -> None:
    """Apply one deliberate corruption while always releasing Windows handles."""

    connection = sqlite3.connect(path)
    try:
        connection.execute(statement, values)
        connection.commit()
    finally:
        connection.close()


def _canonical_test_json(value: object) -> str:
    """Encode one test payload exactly like the persistence boundary."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _rewrite_first_stored_chunk(
    database: Path,
    document: EmbeddedDocument,
    payload: dict[str, object],
) -> None:
    """Publish a checksum-consistent hostile first row with recomputed IDs."""

    mappings = payload.get("source_mappings")
    text = payload.get("text")
    assert isinstance(mappings, list)
    assert isinstance(text, str)
    parsed = vector_store_module._parse_chunk(
        payload,
        maximum_mappings=max(1, len(mappings)),
        maximum_text_code_points=max(1, len(text)),
    )
    chunk_id = vector_store_module._expected_chunk_id(
        parsed,
        document.derivation_fingerprint,
    )
    payload["chunk_id"] = chunk_id
    embedding_preimage = _canonical_test_json({
        "embedding_id_domain": "elysia.chunk-embedding.v1",
        "chunk_id": chunk_id,
        "derivation_fingerprint": document.derivation_fingerprint,
        "embedding_space_id": document.identity.embedding_space_id,
    }).encode("utf-8")
    embedding_id = f"embedding_{hashlib.sha256(embedding_preimage).hexdigest()}"
    chunk_json = _canonical_test_json(payload)
    _mutate_database(
        database,
        "UPDATE vector_records SET chunk_id = ?, embedding_id = ?, "
        "chunk_json = ?, chunk_checksum = ? WHERE ordinal = 0",
        (
            chunk_id,
            embedding_id,
            chunk_json,
            hashlib.sha256(chunk_json.encode("utf-8")).hexdigest(),
        ),
    )


def test_store_round_trips_complete_chunks_and_fixed_float32_bytes(
    tmp_path: Path,
) -> None:
    """Preserve citation mappings and use portable little-endian vectors."""

    identity = _identity()
    scope = _scope("chat", "roundtrip")
    document = _embedded_document(
        scope,
        link_id="attachment_roundtrip",
        texts=("Alpha", "Beta"),
        identity=identity,
    )
    database = tmp_path / "vectors.sqlite3"
    store = SQLiteVectorStore(database, identity)

    store.replace_document(scope, document)

    assert store.model_identity == identity
    assert store.limits == VectorStoreLimits()
    assert _list(store, document) == document.chunks
    assert store.get_record(
        scope,
        document.source.link_id,
        document.chunks[1].chunk_id,
        expected_derivation_fingerprint=document.derivation_fingerprint,
        expected_identity=identity,
    ) == document.chunks[1]
    connection = sqlite3.connect(database)
    try:
        vector_blob = connection.execute(
            "SELECT vector_blob FROM vector_records ORDER BY ordinal LIMIT 1"
        ).fetchone()[0]
        metadata = connection.execute(
            """
            SELECT schema_version, vector_encoding, dimensions, identity_json
            FROM vector_store_metadata
            """
        ).fetchone()
        lineage_json = connection.execute(
            "SELECT lineage_json FROM vector_documents"
        ).fetchone()[0]
    finally:
        connection.close()
    assert vector_blob == struct.pack("<3f", 1.0, 0.0, 0.0)
    assert metadata[:3] == (
        VECTOR_STORE_SCHEMA_VERSION,
        VECTOR_ENCODING,
        3,
    )
    assert json.loads(metadata[3])["embedding_space_id"] == (
        identity.embedding_space_id
    )
    lineage = json.loads(lineage_json)
    assert lineage["chunked_document"]["provenance"]["loader_id"] == (
        "vector-test-loader"
    )
    assert lineage["embedding_policy"]["truncate"] is False


def test_limits_are_snapshotted_on_input_and_property_read(
    tmp_path: Path,
) -> None:
    """Prevent frozen-dataclass bypasses from changing live store budgets."""

    configured = VectorStoreLimits(
        max_list_records=1,
        max_database_bytes=4 * 1024 * 1024,
        max_database_pages=1_024,
    )
    store = SQLiteVectorStore(
        tmp_path / "vectors.sqlite3",
        _identity(),
        limits=configured,
    )

    object.__setattr__(configured, "max_list_records", 999)
    object.__setattr__(configured, "max_database_bytes", 512)
    object.__setattr__(configured, "max_database_pages", 1)
    published = store.limits
    object.__setattr__(published, "max_list_records", 998)
    object.__setattr__(published, "max_database_bytes", 1_024)
    object.__setattr__(published, "max_database_pages", 2)

    assert store.limits.max_list_records == 1
    assert store.limits.max_database_bytes == 4 * 1024 * 1024
    assert store.limits.max_database_pages == 1_024
    assert store.limits is not published


def test_model_identity_property_returns_a_detached_snapshot(
    tmp_path: Path,
) -> None:
    """Prevent frozen-object bypasses from mutating the live store identity."""

    identity = _identity()
    store = SQLiteVectorStore(tmp_path / "vectors.sqlite3", identity)

    published = store.model_identity
    object.__setattr__(published, "model_digest", "b" * 64)

    assert store.model_identity == identity
    assert store.model_identity is not published


def test_limits_reject_subclasses_at_the_untrusted_input_boundary(
    tmp_path: Path,
) -> None:
    """Reject accessor overrides instead of treating subclasses as data."""

    class _LimitsSubclass(VectorStoreLimits):
        """Stand in for a caller-controlled limits implementation."""

    with pytest.raises(TypeError, match="limits must be VectorStoreLimits"):
        SQLiteVectorStore(
            tmp_path / "vectors.sqlite3",
            _identity(),
            limits=_LimitsSubclass(),
        )


@pytest.mark.parametrize(
    "limits",
    [
        VectorStoreLimits(max_database_bytes=512, max_database_pages=1),
        VectorStoreLimits(max_database_bytes=1_024, max_database_pages=2),
    ],
)
def test_database_limit_boundaries_accept_coherent_exact_integers(
    limits: VectorStoreLimits,
) -> None:
    """Accept byte/page ceilings that describe at least one SQLite page."""

    assert limits.max_database_pages * 512 <= limits.max_database_bytes


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_database_bytes": 511, "max_database_pages": 1},
        {"max_database_bytes": 512, "max_database_pages": 2},
        {"max_database_bytes": True, "max_database_pages": 1},
        {"max_database_bytes": 1_024, "max_database_pages": 1.5},
    ],
)
def test_database_limit_boundaries_reject_invalid_or_incoherent_values(
    overrides: dict[str, object],
) -> None:
    """Reject non-exact and physically impossible database ceilings."""

    with pytest.raises(DocumentValidationError):
        VectorStoreLimits(**overrides)  # type: ignore[arg-type]


def test_delete_document_is_idempotent_and_exactly_scope_isolated(
    tmp_path: Path,
) -> None:
    """Delete only one scope's link and report an absent repeat as false."""

    identity = _identity()
    chat_scope = _scope("chat", "isolation")
    project_scope = _scope("project", "isolation")
    chat_document = _embedded_document(
        chat_scope,
        link_id="attachment_shared",
        texts=("Shared",),
        identity=identity,
    )
    project_document = _embedded_document(
        project_scope,
        link_id="attachment_shared",
        texts=("Shared",),
        identity=identity,
    )
    store = SQLiteVectorStore(tmp_path / "vectors.sqlite3", identity)

    store.replace_document(chat_scope, chat_document)
    store.replace_document(project_scope, project_document)

    assert _list(store, chat_document) == chat_document.chunks
    assert _list(store, project_document) == project_document.chunks
    assert store.delete_document(chat_scope, "attachment_shared") is True
    assert store.delete_document(chat_scope, "attachment_shared") is False
    assert _list(store, chat_document) == ()
    assert _list(store, project_document) == project_document.chunks


def test_replace_document_removes_old_generation_and_rejects_stale_reads(
    tmp_path: Path,
) -> None:
    """Publish all-or-nothing replacement without leaving old chunk rows."""

    identity = _identity()
    scope = _scope("chat", "replace")
    old = _embedded_document(
        scope,
        link_id="attachment_replace",
        texts=("Old one", "Old two"),
        identity=identity,
    )
    new = _embedded_document(
        scope,
        link_id="attachment_replace",
        texts=("New only",),
        identity=identity,
    )
    store = SQLiteVectorStore(tmp_path / "vectors.sqlite3", identity)
    store.replace_document(scope, old)

    store.replace_document(scope, new)

    with pytest.raises(VectorStoreStaleError, match="requested derivation"):
        _list(store, old)
    assert _list(store, new) == new.chunks
    assert store.get_record(
        scope,
        new.source.link_id,
        old.chunks[0].chunk_id,
        expected_derivation_fingerprint=new.derivation_fingerprint,
        expected_identity=identity,
    ) is None


def test_list_is_bounded_and_uses_forward_ordinal_pagination(
    tmp_path: Path,
) -> None:
    """Page deterministically without materializing an unbounded scope."""

    identity = _identity()
    scope = _scope("project", "pagination")
    document = _embedded_document(
        scope,
        link_id="attachment_pagination",
        texts=("A", "B", "C"),
        identity=identity,
    )
    limits = VectorStoreLimits(max_list_records=2)
    store = SQLiteVectorStore(
        tmp_path / "vectors.sqlite3",
        identity,
        limits=limits,
    )
    store.replace_document(scope, document)

    first = _list(store, document, limit=2)
    second = _list(
        store,
        document,
        limit=2,
        after_ordinal=first[-1].ordinal,
    )

    assert first == document.chunks[:2]
    assert second == document.chunks[2:]
    with pytest.raises(DocumentContentLimitError, match="list limit"):
        _list(store, document, limit=3)


def test_delete_scope_removes_only_the_exact_scope(
    tmp_path: Path,
) -> None:
    """Cascade all vectors for one owner without crossing another scope."""

    identity = _identity()
    first_scope = _scope("project", "delete_first")
    second_scope = _scope("project", "delete_second")
    first = _embedded_document(
        first_scope,
        link_id="attachment_first",
        texts=("First",),
        identity=identity,
    )
    second = _embedded_document(
        second_scope,
        link_id="attachment_second",
        texts=("Second",),
        identity=identity,
    )
    store = SQLiteVectorStore(tmp_path / "vectors.sqlite3", identity)
    store.replace_document(first_scope, first)
    store.replace_document(second_scope, second)

    assert store.delete_scope(first_scope) == 1
    assert _list(store, first) == ()
    assert _list(store, second) == second.chunks
    assert store.delete_scope(first_scope) == 0


def test_rebuild_replaces_exact_scope_set_and_preserves_other_scopes(
    tmp_path: Path,
) -> None:
    """Replace one scope transactionally while leaving peer scopes intact."""

    identity = _identity()
    target_scope = _scope("project", "rebuild")
    peer_scope = _scope("chat", "rebuild")
    old = _embedded_document(
        target_scope,
        link_id="attachment_old",
        texts=("Old",),
        identity=identity,
    )
    replacement = _embedded_document(
        target_scope,
        link_id="attachment_new",
        texts=("Replacement",),
        identity=identity,
    )
    peer = _embedded_document(
        peer_scope,
        link_id="attachment_peer",
        texts=("Peer",),
        identity=identity,
    )
    store = SQLiteVectorStore(tmp_path / "vectors.sqlite3", identity)
    store.replace_document(target_scope, old)
    store.replace_document(peer_scope, peer)

    store.rebuild(target_scope, (replacement,))

    assert _list(store, old) == ()
    assert _list(store, replacement) == replacement.chunks
    assert _list(store, peer) == peer.chunks
    store.rebuild(target_scope, ())
    assert _list(store, replacement) == ()
    assert _list(store, peer) == peer.chunks


def test_rebuild_with_empty_iterable_clears_only_the_exact_scope(
    tmp_path: Path,
) -> None:
    """Interpret an empty rebuild as the exact empty set for one scope."""

    identity = _identity()
    target_scope = _scope("chat", "empty_rebuild")
    peer_scope = _scope("project", "empty_rebuild")
    target = _embedded_document(
        target_scope,
        link_id="attachment_shared",
        texts=("Target",),
        identity=identity,
    )
    peer = _embedded_document(
        peer_scope,
        link_id="attachment_shared",
        texts=("Peer",),
        identity=identity,
    )
    store = SQLiteVectorStore(tmp_path / "vectors.sqlite3", identity)
    store.replace_document(target_scope, target)
    store.replace_document(peer_scope, peer)

    store.rebuild(target_scope, ())

    assert _list(store, target) == ()
    assert _list(store, peer) == peer.chunks


def test_rebuild_failure_rolls_back_the_previous_scope_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the old scope intact when an insert fails after transactional delete."""

    identity = _identity()
    scope = _scope("chat", "rollback")
    old = _embedded_document(
        scope,
        link_id="attachment_old",
        texts=("Old",),
        identity=identity,
    )
    replacement = _embedded_document(
        scope,
        link_id="attachment_new",
        texts=("New",),
        identity=identity,
    )
    store = SQLiteVectorStore(tmp_path / "vectors.sqlite3", identity)
    store.replace_document(scope, old)

    def _fail_insert(
        connection: sqlite3.Connection,
        prepared: object,
    ) -> None:
        """Simulate a SQLite failure after rebuild removed old rows."""

        raise sqlite3.OperationalError("injected write failure")

    monkeypatch.setattr(
        SQLiteVectorStore,
        "_insert_prepared_document",
        staticmethod(_fail_insert),
    )
    with pytest.raises(VectorStorePersistenceError, match="rebuild failed"):
        store.rebuild(scope, (replacement,))

    assert _list(store, old) == old.chunks
    assert _list(store, replacement) == ()


def test_database_and_read_calls_require_the_exact_model_identity(
    tmp_path: Path,
) -> None:
    """Reject cross-model opens and reads even when dimensions are identical."""

    identity = _identity(digest_character="a")
    other_identity = _identity(digest_character="b")
    scope = _scope("chat", "model")
    document = _embedded_document(
        scope,
        link_id="attachment_model",
        texts=("Model",),
        identity=identity,
    )
    database = tmp_path / "vectors.sqlite3"
    store = SQLiteVectorStore(database, identity)
    store.replace_document(scope, document)

    with pytest.raises(VectorStoreModelMismatchError, match="different"):
        SQLiteVectorStore(database, other_identity)
    with pytest.raises(VectorStoreModelMismatchError, match="different"):
        store.list_document(
            scope,
            document.source.link_id,
            expected_derivation_fingerprint=document.derivation_fingerprint,
            expected_identity=other_identity,
        )


def test_unknown_schema_version_fails_closed_on_the_next_operation(
    tmp_path: Path,
) -> None:
    """Refuse a future SQLite schema instead of guessing a migration."""

    identity = _identity()
    database = tmp_path / "vectors.sqlite3"
    store = SQLiteVectorStore(database, identity)
    _mutate_database(database, "PRAGMA user_version = 99", ())

    with pytest.raises(DocumentCorruptError, match="unsupported"):
        store.delete_scope(_scope("chat", "future"))


def test_schema_creation_failure_rolls_back_every_ddl_statement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave no partial tables when initialization fails between DDL statements."""

    identity = _identity()
    database = tmp_path / "vectors.sqlite3"
    original_statements = vector_store_module._SCHEMA_STATEMENTS
    monkeypatch.setattr(
        vector_store_module,
        "_SCHEMA_STATEMENTS",
        (original_statements[0], original_statements[0]),
    )

    with pytest.raises(DocumentCorruptError, match="database is invalid"):
        SQLiteVectorStore(database, identity)

    connection = sqlite3.connect(database)
    try:
        tables = connection.execute(
            """
            SELECT name FROM sqlite_schema
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
    finally:
        connection.close()
    assert tables == []

    monkeypatch.setattr(
        vector_store_module,
        "_SCHEMA_STATEMENTS",
        original_statements,
    )
    SQLiteVectorStore(database, identity)


def test_initialization_rejects_an_empty_utf16_database(tmp_path: Path) -> None:
    """Refuse an empty database whose text byte lengths are not the v1 format."""

    database = tmp_path / "utf16-empty.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA encoding = 'UTF-16le'")
        # Creating then dropping the first table fixes SQLite's file encoding
        # while returning the catalog to the only state the Store may adopt.
        connection.execute("CREATE TABLE encoding_probe (value TEXT)")
        connection.execute("DROP TABLE encoding_probe")
        connection.commit()
        assert connection.execute("PRAGMA encoding").fetchone()[0] == (
            "UTF-16le"
        )
    finally:
        connection.close()

    with pytest.raises(DocumentCorruptError, match="encoding is unsupported"):
        SQLiteVectorStore(database, _identity())

    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT name FROM sqlite_schema"
        ).fetchall() == []
    finally:
        connection.close()


def test_hostile_same_column_schema_without_constraints_fails_closed(
    tmp_path: Path,
) -> None:
    """Reject look-alike tables that omit primary, unique, and foreign keys."""

    database = tmp_path / "vectors.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            f"""
            CREATE TABLE vector_store_metadata (
                singleton INTEGER,
                schema_version INTEGER,
                vector_encoding TEXT,
                embedding_space_id TEXT,
                dimensions INTEGER,
                identity_json TEXT,
                identity_checksum TEXT
            );
            CREATE TABLE vector_documents (
                scope_kind TEXT,
                scope_id TEXT,
                link_id TEXT,
                derivation_fingerprint TEXT,
                embedding_space_id TEXT,
                lineage_json TEXT,
                lineage_checksum TEXT,
                record_count INTEGER
            );
            CREATE TABLE vector_records (
                scope_kind TEXT,
                scope_id TEXT,
                link_id TEXT,
                ordinal INTEGER,
                chunk_id TEXT,
                embedding_id TEXT,
                chunk_json TEXT,
                chunk_checksum TEXT,
                vector_blob BLOB,
                vector_checksum TEXT
            );
            PRAGMA application_id = {0x454C5956};
            PRAGMA user_version = {VECTOR_STORE_SCHEMA_VERSION};
            """
        )
    finally:
        connection.close()

    with pytest.raises(DocumentCorruptError, match="schema is invalid"):
        SQLiteVectorStore(database, _identity())


@pytest.mark.parametrize(
    "statements",
    [
        ("CREATE VIEW foreign_view AS SELECT 1 AS value;",),
        (
            "CREATE TABLE foreign_index_table (value INTEGER);",
            "CREATE INDEX foreign_index ON foreign_index_table (value);",
        ),
        (
            "CREATE TABLE foreign_trigger_table (value INTEGER);",
            """
            CREATE TRIGGER foreign_trigger
            AFTER INSERT ON foreign_trigger_table
            BEGIN SELECT 1; END;
            """,
        ),
    ],
    ids=("view", "index", "trigger"),
)
def test_initialization_rejects_every_preexisting_schema_object(
    tmp_path: Path,
    statements: tuple[str, ...],
) -> None:
    """Treat only a catalog with no table, index, view, or trigger as empty."""

    database = tmp_path / "foreign.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.executescript("\n".join(statements))
        before = connection.execute(
            "SELECT type, name FROM sqlite_schema ORDER BY type, name"
        ).fetchall()
    finally:
        connection.close()

    with pytest.raises(DocumentCorruptError):
        SQLiteVectorStore(database, _identity())

    connection = sqlite3.connect(database)
    try:
        after = connection.execute(
            "SELECT type, name FROM sqlite_schema ORDER BY type, name"
        ).fetchall()
    finally:
        connection.close()
    assert after == before


def test_unknown_schema_trigger_fails_closed_on_the_next_operation(
    tmp_path: Path,
) -> None:
    """Reject executable schema objects outside the fixed table allowlist."""

    identity = _identity()
    database = tmp_path / "vectors.sqlite3"
    store = SQLiteVectorStore(database, identity)
    _mutate_database(
        database,
        """
        CREATE TRIGGER hostile_vector_trigger
        AFTER INSERT ON vector_documents
        BEGIN SELECT 1; END
        """,
        (),
    )

    with pytest.raises(DocumentCorruptError, match="schema is invalid"):
        store.delete_scope(_scope("project", "hostile_trigger"))


def test_orphaned_vector_row_fails_the_foreign_key_integrity_check(
    tmp_path: Path,
) -> None:
    """Reject rows orphaned by an external writer with foreign keys disabled."""

    identity = _identity()
    scope = _scope("chat", "orphan")
    document = _embedded_document(
        scope,
        link_id="attachment_orphan",
        texts=("Orphan",),
        identity=identity,
    )
    database = tmp_path / "vectors.sqlite3"
    store = SQLiteVectorStore(database, identity)
    store.replace_document(scope, document)
    _mutate_database(
        database,
        "DELETE FROM vector_documents WHERE link_id = ?",
        (document.source.link_id,),
    )

    with pytest.raises(DocumentCorruptError, match="integrity check"):
        store.delete_scope(scope)


@pytest.mark.parametrize("corruption", ["length", "checksum", "nan", "norm"])
def test_vector_blob_corruption_fails_closed(
    tmp_path: Path,
    corruption: str,
) -> None:
    """Validate fixed length, checksum, finite values, and L2 normalization."""

    identity = _identity()
    scope = _scope("chat", f"vector_{corruption}")
    document = _embedded_document(
        scope,
        link_id="attachment_vector",
        texts=("Vector",),
        identity=identity,
    )
    database = tmp_path / "vectors.sqlite3"
    store = SQLiteVectorStore(database, identity)
    store.replace_document(scope, document)
    if corruption == "length":
        blob = struct.pack("<2f", 1.0, 0.0)
        checksum = hashlib.sha256(blob).hexdigest()
    elif corruption == "checksum":
        blob = struct.pack("<3f", 1.0, 0.0, 0.0)
        checksum = "0" * 64
    elif corruption == "nan":
        blob = struct.pack("<3f", float("nan"), 0.0, 0.0)
        checksum = hashlib.sha256(blob).hexdigest()
    else:
        blob = struct.pack("<3f", 2.0, 0.0, 0.0)
        checksum = hashlib.sha256(blob).hexdigest()
    _mutate_database(
        database,
        "UPDATE vector_records SET vector_blob = ?, vector_checksum = ?",
        (blob, checksum),
    )

    with pytest.raises(DocumentCorruptError, match="vector"):
        _list(store, document)


@pytest.mark.parametrize("target", ["chunk", "lineage"])
def test_strict_json_rejects_duplicate_members_even_with_matching_checksum(
    tmp_path: Path,
    target: str,
) -> None:
    """Reject paired metadata/checksum tampering instead of using last-key wins."""

    identity = _identity()
    scope = _scope("project", f"json_{target}")
    document = _embedded_document(
        scope,
        link_id="attachment_json",
        texts=("JSON",),
        identity=identity,
    )
    database = tmp_path / "vectors.sqlite3"
    store = SQLiteVectorStore(database, identity)
    store.replace_document(scope, document)
    malformed = '{"duplicate":1,"duplicate":2}'
    checksum = hashlib.sha256(malformed.encode()).hexdigest()
    if target == "chunk":
        statement = (
            "UPDATE vector_records SET chunk_json = ?, chunk_checksum = ?"
        )
    else:
        statement = (
            "UPDATE vector_documents SET lineage_json = ?, "
            "lineage_checksum = ?"
        )
    _mutate_database(database, statement, (malformed, checksum))

    with pytest.raises(DocumentCorruptError, match="metadata|lineage|chunk"):
        _list(store, document)


def test_deep_json_is_mapped_to_corruption_even_with_matching_checksum(
    tmp_path: Path,
) -> None:
    """Map decoder recursion exhaustion without leaking an interpreter error."""

    identity = _identity()
    scope = _scope("project", "deep_json")
    document = _embedded_document(
        scope,
        link_id="attachment_deep_json",
        texts=("JSON",),
        identity=identity,
    )
    database = tmp_path / "vectors.sqlite3"
    store = SQLiteVectorStore(database, identity)
    store.replace_document(scope, document)
    malformed = '{"nested":' + "[" * 20_000 + "0" + "]" * 20_000 + "}"
    checksum = hashlib.sha256(malformed.encode()).hexdigest()
    _mutate_database(
        database,
        "UPDATE vector_documents SET lineage_json = ?, "
        "lineage_checksum = ?",
        (malformed, checksum),
    )

    with pytest.raises(DocumentCorruptError, match="metadata is invalid"):
        _list(store, document)


@pytest.mark.parametrize(
    "field_name",
    [
        "record_count",
        "total_chunk_code_points",
        "total_source_mappings",
    ],
)
def test_lineage_aggregate_tampering_fails_with_matching_checksum(
    tmp_path: Path,
    field_name: str,
) -> None:
    """Reject recomputed checksums when document-wide totals are impossible."""

    identity = _identity()
    scope = _scope("chat", f"lineage_total_{field_name}")
    document = _embedded_document(
        scope,
        link_id="attachment_lineage_total",
        texts=("Totals",),
        identity=identity,
    )
    database = tmp_path / "vectors.sqlite3"
    store = SQLiteVectorStore(database, identity)
    store.replace_document(scope, document)
    connection = sqlite3.connect(database)
    try:
        lineage_json = connection.execute(
            "SELECT lineage_json FROM vector_documents"
        ).fetchone()[0]
    finally:
        connection.close()
    lineage = json.loads(lineage_json)
    assert isinstance(lineage, dict)
    if field_name == "record_count":
        lineage[field_name] = len(document.chunks) + 1
    else:
        chunked = lineage["chunked_document"]
        assert isinstance(chunked, dict)
        limits = chunked["processing_limits"]
        assert isinstance(limits, dict)
        limit_name = (
            "max_total_chunk_code_points"
            if field_name == "total_chunk_code_points"
            else "max_total_source_mappings"
        )
        configured_limit = limits[limit_name]
        assert isinstance(configured_limit, int)
        lineage[field_name] = configured_limit + 1
    rewritten = _canonical_test_json(lineage)
    _mutate_database(
        database,
        "UPDATE vector_documents SET lineage_json = ?, "
        "lineage_checksum = ?",
        (
            rewritten,
            hashlib.sha256(rewritten.encode("utf-8")).hexdigest(),
        ),
    )

    with pytest.raises(DocumentCorruptError, match="count|totals"):
        _list(store, document)


def test_checksum_consistent_oversized_chunk_text_fails_lineage_limit(
    tmp_path: Path,
) -> None:
    """Apply the stored policy even when row IDs and checksums are recomputed."""

    identity = _identity()
    scope = _scope("chat", "oversized_chunk")
    document = _embedded_document(
        scope,
        link_id="attachment_oversized_chunk",
        texts=("Text",),
        identity=identity,
    )
    database = tmp_path / "vectors.sqlite3"
    store = SQLiteVectorStore(database, identity)
    store.replace_document(scope, document)
    connection = sqlite3.connect(database)
    try:
        chunk_json = connection.execute(
            "SELECT chunk_json FROM vector_records WHERE ordinal = 0"
        ).fetchone()[0]
    finally:
        connection.close()
    payload = json.loads(chunk_json)
    assert isinstance(payload, dict)
    payload["text"] = "x" * 2_001
    mappings = payload["source_mappings"]
    assert isinstance(mappings, list) and len(mappings) == 1
    mapping = mappings[0]
    assert isinstance(mapping, dict)
    mapping["chunk_end_code_point"] = 2_001
    span = mapping["source_span"]
    assert isinstance(span, dict)
    span["end_code_point"] = 2_001
    _rewrite_first_stored_chunk(database, document, payload)

    with pytest.raises(DocumentCorruptError, match="text.*lineage limit"):
        _list(store, document)


def test_checksum_consistent_non_pdf_page_fails_lineage_validation(
    tmp_path: Path,
) -> None:
    """Reject invented pages that disagree with the stored document format."""

    identity = _identity()
    scope = _scope("project", "invented_page")
    document = _embedded_document(
        scope,
        link_id="attachment_invented_page",
        texts=("Page",),
        identity=identity,
    )
    database = tmp_path / "vectors.sqlite3"
    store = SQLiteVectorStore(database, identity)
    store.replace_document(scope, document)
    connection = sqlite3.connect(database)
    try:
        chunk_json = connection.execute(
            "SELECT chunk_json FROM vector_records WHERE ordinal = 0"
        ).fetchone()[0]
    finally:
        connection.close()
    payload = json.loads(chunk_json)
    assert isinstance(payload, dict)
    payload["page_number"] = 1
    mappings = payload["source_mappings"]
    assert isinstance(mappings, list)
    for mapping in mappings:
        assert isinstance(mapping, dict)
        span = mapping["source_span"]
        assert isinstance(span, dict)
        span["page_number"] = 1
    _rewrite_first_stored_chunk(database, document, payload)

    with pytest.raises(DocumentCorruptError, match="page lineage"):
        _list(store, document)


def test_chunk_parser_rejects_mapping_count_before_building_mappings() -> None:
    """Apply the lineage mapping ceiling before expanding hostile entries."""

    payload: dict[str, object] = {
        "ordinal": 0,
        "chunk_id": "chunk_" + "a" * 64,
        "kind": "code",
        "text": "ab",
        "page_number": None,
        "source_mappings": [None, None],
    }

    with pytest.raises(DocumentCorruptError, match="mappings exceed"):
        vector_store_module._parse_chunk(
            payload,
            maximum_mappings=1,
            maximum_text_code_points=2_000,
        )


def test_actual_record_count_query_stops_at_one_over_the_limit(
    tmp_path: Path,
) -> None:
    """Detect surplus rows through the bounded composite-key ordinal scan."""

    identity = _identity()
    scope = _scope("chat", "surplus_row")
    document = _embedded_document(
        scope,
        link_id="attachment_surplus_row",
        texts=("One",),
        identity=identity,
    )
    database = tmp_path / "vectors.sqlite3"
    store = SQLiteVectorStore(
        database,
        identity,
        limits=VectorStoreLimits(
            max_records_per_document=1,
            max_list_records=1,
        ),
    )
    store.replace_document(scope, document)
    _mutate_database(
        database,
        """
        INSERT INTO vector_records (
            scope_kind, scope_id, link_id, ordinal, chunk_id, embedding_id,
            chunk_json, chunk_checksum, vector_blob, vector_checksum
        )
        SELECT scope_kind, scope_id, link_id, 1, ?, ?, chunk_json,
               chunk_checksum, vector_blob, vector_checksum
        FROM vector_records WHERE ordinal = 0
        """,
        ("chunk_" + "f" * 64, "embedding_" + "f" * 64),
    )

    with pytest.raises(DocumentCorruptError, match="incomplete"):
        _list(store, document, limit=1)


def test_database_file_byte_limit_is_enforced_before_sqlite_open(
    tmp_path: Path,
) -> None:
    """Reject an oversized main file before SQLite parses untrusted bytes."""

    database = tmp_path / "oversized.sqlite3"
    database.write_bytes(b"0" * 513)

    with pytest.raises(DocumentContentLimitError, match="byte limit"):
        SQLiteVectorStore(
            database,
            _identity(),
            limits=VectorStoreLimits(
                max_database_bytes=512,
                max_database_pages=1,
            ),
        )


def test_database_page_limit_is_enforced_after_schema_creation(
    tmp_path: Path,
) -> None:
    """Count allocated SQLite pages after initialization before publishing."""

    database = tmp_path / "page_limited.sqlite3"
    with pytest.raises(DocumentContentLimitError, match="storage limit"):
        SQLiteVectorStore(
            database,
            _identity(),
            limits=VectorStoreLimits(max_database_pages=1),
        )

    # The failed tiny-cap initialization must not commit a partial schema.
    SQLiteVectorStore(database, _identity())


@pytest.mark.parametrize("operation", ["replace", "rebuild"])
def test_write_page_ceiling_rolls_back_the_previous_generation(
    tmp_path: Path,
    operation: str,
) -> None:
    """Keep the old generation when a replacement reaches max_page_count."""

    identity = _identity()
    scope = _scope("chat", f"page_rollback_{operation}")
    link_id = "attachment_page_rollback"
    old = _embedded_document(
        scope,
        link_id=link_id,
        texts=("Old",),
        identity=identity,
    )
    database = tmp_path / "vectors.sqlite3"
    SQLiteVectorStore(database, identity).replace_document(scope, old)
    connection = sqlite3.connect(database)
    try:
        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
    finally:
        connection.close()
    assert type(page_count) is int
    constrained = SQLiteVectorStore(
        database,
        identity,
        limits=VectorStoreLimits(max_database_pages=page_count),
    )
    replacement = _embedded_document(
        scope,
        link_id=link_id,
        texts=tuple(
            f"{ordinal:04d}" + "x" * 1_900
            for ordinal in range(64)
        ),
        identity=identity,
    )

    with pytest.raises(DocumentContentLimitError, match="storage limit"):
        if operation == "replace":
            constrained.replace_document(scope, replacement)
        else:
            constrained.rebuild(scope, (replacement,))

    assert _list(constrained, old) == old.chunks


@pytest.mark.parametrize("operation", ["document", "scope"])
def test_delete_journal_ceiling_rolls_back_the_previous_generation(
    tmp_path: Path,
    operation: str,
) -> None:
    """Count a deletion journal before commit and retain rows on overflow."""

    identity = _identity()
    scope = _scope("project", f"journal_rollback_{operation}")
    document = _embedded_document(
        scope,
        link_id="attachment_journal_rollback",
        texts=tuple(
            f"{ordinal:04d}" + "x" * 1_900
            for ordinal in range(64)
        ),
        identity=identity,
    )
    database = tmp_path / "vectors.sqlite3"
    SQLiteVectorStore(database, identity).replace_document(scope, document)
    connection = sqlite3.connect(database)
    try:
        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
    finally:
        connection.close()
    assert type(page_count) is int
    byte_count = database.stat().st_size
    constrained = SQLiteVectorStore(
        database,
        identity,
        limits=VectorStoreLimits(
            max_database_bytes=byte_count,
            max_database_pages=page_count,
        ),
    )

    with pytest.raises(DocumentContentLimitError, match="byte limit"):
        if operation == "document":
            constrained.delete_document(scope, document.source.link_id)
        else:
            constrained.delete_scope(scope)

    assert _list(constrained, document) == document.chunks


def test_schema_cursor_reader_stops_at_one_over_the_allowlist() -> None:
    """Never expand an attacker-sized catalog before rejecting its surplus."""

    class _UnboundedCursor:
        """Model a hostile cursor that can yield rows forever."""

        def __init__(self) -> None:
            """Start with no rows observed by the bounded reader."""

            self.calls = 0

        def fetchone(self) -> tuple[str]:
            """Return another row and expose how many were requested."""

            self.calls += 1
            return ("hostile",)

    cursor = _UnboundedCursor()
    rows = SQLiteVectorStore._fetch_bounded_rows(
        cursor,  # type: ignore[arg-type]
        3,
    )

    assert len(rows) == 4
    assert cursor.calls == 4


def test_database_byte_limit_includes_known_sqlite_sidecars(
    tmp_path: Path,
) -> None:
    """Count journal/WAL sidecars rather than trusting only the main file."""

    database = tmp_path / "sidecar.sqlite3"
    identity = _identity()
    SQLiteVectorStore(database, identity)
    main_bytes = database.stat().st_size
    Path(f"{database}-wal").write_bytes(b"0" * 512)
    byte_limit = main_bytes + 511

    with pytest.raises(DocumentContentLimitError, match="byte limit"):
        SQLiteVectorStore(
            database,
            identity,
            limits=VectorStoreLimits(
                max_database_bytes=byte_limit,
                max_database_pages=byte_limit // 512,
            ),
        )


def test_database_validation_uses_one_monotonic_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map progress interruption or final expiry to one path-private error."""

    database = tmp_path / "private-validation-deadline.sqlite3"
    store = SQLiteVectorStore(database, _identity(), timeout_seconds=5)
    clock_reads = 0

    def _expired_clock() -> float:
        """Expire immediately after the validation deadline is established."""

        nonlocal clock_reads
        clock_reads += 1
        return 0.0 if clock_reads == 1 else 100.0

    monkeypatch.setattr(vector_store_module, "monotonic", _expired_clock)

    with pytest.raises(DocumentContentLimitError, match="time limit") as error:
        store.delete_scope(_scope("chat", "deadline"))

    assert str(database) not in str(error.value)


@pytest.mark.parametrize(
    ("budget_kind", "expected_message"),
    [("metadata", "metadata limit"), ("vector", "vector limit")],
)
def test_list_streams_rows_under_cumulative_document_budgets(
    tmp_path: Path,
    budget_kind: str,
    expected_message: str,
) -> None:
    """Stop a page before fetching more rows once actual bytes exceed limits."""

    identity = _identity()
    scope = _scope("project", f"stream_budget_{budget_kind}")
    document = _embedded_document(
        scope,
        link_id="attachment_stream_budget",
        texts=("First", "Second"),
        identity=identity,
    )
    database = tmp_path / "vectors.sqlite3"
    SQLiteVectorStore(database, identity).replace_document(scope, document)
    connection = sqlite3.connect(database)
    try:
        chunk_sizes = tuple(
            len(row[0].encode("utf-8"))
            for row in connection.execute(
                "SELECT chunk_json FROM vector_records ORDER BY ordinal"
            ).fetchall()
        )
    finally:
        connection.close()
    assert len(chunk_sizes) == 2
    if budget_kind == "metadata":
        limits = VectorStoreLimits(
            max_document_metadata_bytes=max(chunk_sizes),
        )
    else:
        limits = VectorStoreLimits(max_document_vector_bytes=12)
    constrained = SQLiteVectorStore(database, identity, limits=limits)

    with pytest.raises(DocumentCorruptError, match=expected_message):
        _list(constrained, document, limit=2)


def test_get_record_checks_actual_bytes_against_document_budget(
    tmp_path: Path,
) -> None:
    """Reject one oversized persisted row before expanding its JSON graph."""

    identity = _identity()
    scope = _scope("chat", "record_budget")
    document = _embedded_document(
        scope,
        link_id="attachment_record_budget",
        texts=("Record",),
        identity=identity,
    )
    database = tmp_path / "vectors.sqlite3"
    SQLiteVectorStore(database, identity).replace_document(scope, document)
    connection = sqlite3.connect(database)
    try:
        chunk_json = connection.execute(
            "SELECT chunk_json FROM vector_records"
        ).fetchone()[0]
    finally:
        connection.close()
    constrained = SQLiteVectorStore(
        database,
        identity,
        limits=VectorStoreLimits(
            max_document_metadata_bytes=len(chunk_json.encode("utf-8")) - 1,
        ),
    )

    with pytest.raises(DocumentCorruptError, match="document limit"):
        constrained.get_record(
            scope,
            document.source.link_id,
            document.chunks[0].chunk_id,
            expected_derivation_fingerprint=document.derivation_fingerprint,
            expected_identity=identity,
        )


def test_record_and_list_limits_are_enforced_before_database_mutation(
    tmp_path: Path,
) -> None:
    """Bound document and rebuild graphs without partially replacing old data."""

    identity = _identity()
    scope = _scope("chat", "limits")
    old = _embedded_document(
        scope,
        link_id="attachment_old",
        texts=("Old",),
        identity=identity,
    )
    too_many = _embedded_document(
        scope,
        link_id="attachment_many",
        texts=("One", "Two"),
        identity=identity,
    )
    limits = VectorStoreLimits(
        max_records_per_document=1,
        max_list_records=1,
        max_rebuild_documents=1,
        max_rebuild_records=1,
    )
    store = SQLiteVectorStore(
        tmp_path / "vectors.sqlite3",
        identity,
        limits=limits,
    )
    store.replace_document(scope, old)

    with pytest.raises(DocumentContentLimitError, match="record limit"):
        store.replace_document(scope, too_many)
    with pytest.raises(DocumentContentLimitError, match="document limit"):
        store.rebuild(scope, (old, old))

    assert _list(store, old, limit=1) == old.chunks


def test_scope_and_database_path_validation_do_not_echo_local_paths(
    tmp_path: Path,
) -> None:
    """Keep invalid-scope and path failures stable and path-private."""

    identity = _identity()
    secret_path = tmp_path / "private-user-name" / "vectors.sqlite3"

    with pytest.raises(DocumentValidationError) as relative_error:
        SQLiteVectorStore(Path("private-user-name/vectors.sqlite3"), identity)
    assert "private-user-name" not in str(relative_error.value)

    store = SQLiteVectorStore(secret_path, identity)
    with pytest.raises(DocumentValidationError) as scope_error:
        store.delete_scope("chat_private")  # type: ignore[arg-type]
    assert str(secret_path) not in str(scope_error.value)


def test_connection_is_closed_when_post_connect_hardening_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Release a partially configured SQLite handle before mapping its error."""

    class _FailingConnection:
        """Expose the minimum connection surface needed for this failure."""

        def __init__(self) -> None:
            """Start with an open fake handle and assignable row factory."""

            self.row_factory: object | None = None
            self.closed = False

        def execute(self, _statement: str) -> None:
            """Fail the first hardening PRAGMA after connect succeeds."""

            raise sqlite3.OperationalError("fixture PRAGMA failure")

        def close(self) -> None:
            """Record that the store released the partial handle."""

            self.closed = True

    connection = _FailingConnection()

    def _connect(*_args: object, **_kwargs: object) -> _FailingConnection:
        """Return the observable post-connect failure fixture."""

        return connection

    monkeypatch.setattr(vector_store_module.sqlite3, "connect", _connect)

    with pytest.raises(VectorStorePersistenceError, match="could not be opened"):
        SQLiteVectorStore(tmp_path / "vectors.sqlite3", _identity())
    assert connection.closed is True


@pytest.mark.parametrize(
    ("failure", "expected_type", "expected_message"),
    [
        (
            sqlite3.DatabaseError(r"malformed C:\private\vectors.sqlite3"),
            DocumentCorruptError,
            "database is invalid",
        ),
        (
            sqlite3.Error(r"runtime C:\private\vectors.sqlite3"),
            VectorStorePersistenceError,
            "could not be validated",
        ),
    ],
)
def test_revalidation_maps_sqlite_errors_and_closes_the_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: sqlite3.Error,
    expected_type: type[Exception],
    expected_message: str,
) -> None:
    """Keep post-construction corruption path-private and release its handle."""

    database = tmp_path / "vectors.sqlite3"
    store = SQLiteVectorStore(database, _identity())
    connection = sqlite3.connect(database)

    def _fail_validation(_connection: sqlite3.Connection) -> None:
        """Raise the selected low-level failure during schema revalidation."""

        raise failure

    monkeypatch.setattr(store, "_open_connection", lambda: connection)
    monkeypatch.setattr(store, "_validate_schema", _fail_validation)

    with pytest.raises(expected_type, match=expected_message) as captured:
        store.delete_scope(_scope("chat", "validation_error"))

    assert "private" not in str(captured.value).lower()
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


def test_cleanup_failures_never_replace_a_primary_document_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve the stable operation failure when rollback and close both fail."""

    class _FailingCleanupConnection:
        """Raise one primary error followed by path-bearing cleanup errors."""

        def execute(self, statement: str, *_args: object) -> object:
            """Begin successfully, then fail the scoped delete."""

            if statement == "BEGIN IMMEDIATE":
                return object()
            raise DocumentCorruptError("primary corruption")

        def rollback(self) -> None:
            """Model a rollback failure that must remain hidden."""

            raise sqlite3.OperationalError(r"rollback C:\private\vectors.db")

        def close(self) -> None:
            """Model a close failure that must not replace the primary error."""

            raise sqlite3.OperationalError(r"close C:\private\vectors.db")

    store = SQLiteVectorStore(tmp_path / "vectors.sqlite3", _identity())
    monkeypatch.setattr(
        store,
        "_open_validated_connection",
        lambda: _FailingCleanupConnection(),
    )

    with pytest.raises(DocumentCorruptError) as captured:
        store.delete_document(
            _scope("chat", "cleanup_primary"),
            "attachment_cleanup_primary",
        )

    assert str(captured.value) == "primary corruption"
    assert "private" not in str(captured.value).lower()


def test_close_failure_after_success_is_mapped_without_a_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return one fixed persistence error when successful work cannot close."""

    class _Cursor:
        """Expose the affected-row count returned by the fake delete."""

        rowcount = 1

    class _CloseFailureConnection:
        """Complete the transaction but fail while releasing its handle."""

        def execute(self, _statement: str, *_args: object) -> object:
            """Return a cursor for both begin and delete statements."""

            return _Cursor()

        def commit(self) -> None:
            """Complete the modeled transaction successfully."""

        def rollback(self) -> None:
            """Provide the unused cleanup surface for completeness."""

        def close(self) -> None:
            """Raise a path-bearing native close failure."""

            raise sqlite3.OperationalError(r"close C:\private\vectors.db")

    store = SQLiteVectorStore(tmp_path / "vectors.sqlite3", _identity())
    monkeypatch.setattr(
        store,
        "_open_validated_connection",
        lambda: _CloseFailureConnection(),
    )
    monkeypatch.setattr(
        store,
        "_commit_bounded_write",
        lambda connection: connection.commit(),
    )

    with pytest.raises(
        VectorStorePersistenceError,
        match="connection could not be closed",
    ) as captured:
        store.delete_document(
            _scope("chat", "cleanup_close"),
            "attachment_cleanup_close",
        )

    assert "private" not in str(captured.value).lower()


def test_tampered_input_vector_is_revalidated_before_replace(
    tmp_path: Path,
) -> None:
    """Treat injected frozen dataclasses as untrusted mutable object graphs."""

    identity = _identity()
    scope = _scope("chat", "tampered")
    document = _embedded_document(
        scope,
        link_id="attachment_tampered",
        texts=("Tampered",),
        identity=identity,
    )
    object.__setattr__(document.chunks[0], "vector", (2.0, 0.0, 0.0))
    store = SQLiteVectorStore(tmp_path / "vectors.sqlite3", identity)

    with pytest.raises(DocumentValidationError, match="L2-normalized|checksum"):
        store.replace_document(scope, document)
    assert store.delete_scope(scope) == 0


@pytest.mark.parametrize("target", ["embedded_lineage", "source_chunk"])
def test_frozen_input_lineage_bypass_is_rejected_before_replace(
    tmp_path: Path,
    target: str,
) -> None:
    """Deep-snapshot both chunk graphs and reject post-construction mutation."""

    identity = _identity()
    scope = _scope("chat", f"tampered_{target}")
    document = _embedded_document(
        scope,
        link_id="attachment_tampered_lineage",
        texts=("Lineage",),
        identity=identity,
    )
    if target == "embedded_lineage":
        object.__setattr__(
            document.chunks[0],
            "derivation_fingerprint",
            "b" * 64,
        )
    else:
        object.__setattr__(
            document.chunked_document.chunks[0],
            "text",
            "Changed",
        )
    store = SQLiteVectorStore(tmp_path / "vectors.sqlite3", identity)

    with pytest.raises(DocumentValidationError, match="lineage|metadata"):
        store.replace_document(scope, document)
    assert store.delete_scope(scope) == 0
