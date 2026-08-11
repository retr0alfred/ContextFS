"""SQLite metadata store for ContextFS.

Everything ContextFS derives about a file lives here or in LanceDB - never in
the file itself, and never anywhere outside ``paths.data_dir``.

Schema evolution
----------------
The schema grows across build phases. Rather than a single frozen DDL, this
module keeps an ordered :data:`MIGRATIONS` list and records the applied version
in ``PRAGMA user_version``. Opening an older database upgrades it in place, so
an index built in Phase 4 is still readable in Phase 20 without a reindex -
which is the same property the "incremental, never a full rebuild" constraint
demands of the retrieval layers.

Concurrency
-----------
WAL journaling is enabled so a long-running GUI (Phase 27) can read while a
scan writes, without either blocking the other on this single-user workload.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "Store",
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "utc_now",
]


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string.

    All timestamps ContextFS *generates* are UTC. Timestamps it *reads from the
    filesystem* stay in local time, because activity sessions model a human
    working day and converting those to UTC would smear a late-night session
    across two calendar dates.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------
# Each entry is a list of statements applied in order. Append only; never edit
# a released migration, or existing databases will diverge from fresh ones.

MIGRATIONS: list[list[str]] = [
    # -- v1: Phase 4, file inventory and scan history -----------------------
    [
        """
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS files (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            path          TEXT    NOT NULL UNIQUE,
            abs_path      TEXT    NOT NULL,
            name          TEXT    NOT NULL,
            stem          TEXT    NOT NULL,
            ext           TEXT    NOT NULL,
            folder        TEXT    NOT NULL,
            depth         INTEGER NOT NULL,
            size          INTEGER NOT NULL,
            mtime_ns      INTEGER NOT NULL,
            mtime         TEXT    NOT NULL,
            content_hash  TEXT,
            status        TEXT    NOT NULL DEFAULT 'present',
            first_seen    TEXT    NOT NULL,
            last_seen     TEXT    NOT NULL,
            content_changed_at TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_files_status ON files(status)",
        "CREATE INDEX IF NOT EXISTS idx_files_folder ON files(folder)",
        "CREATE INDEX IF NOT EXISTS idx_files_ext    ON files(ext)",
        "CREATE INDEX IF NOT EXISTS idx_files_hash   ON files(content_hash)",
        "CREATE INDEX IF NOT EXISTS idx_files_mtime  ON files(mtime_ns)",
        """
        CREATE TABLE IF NOT EXISTS scan_runs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            root          TEXT    NOT NULL,
            started_at    TEXT    NOT NULL,
            finished_at   TEXT,
            duration_ms   REAL,
            full_rescan   INTEGER NOT NULL DEFAULT 0,
            dry_run       INTEGER NOT NULL DEFAULT 0,
            files_seen    INTEGER NOT NULL DEFAULT 0,
            count_new     INTEGER NOT NULL DEFAULT 0,
            count_modified INTEGER NOT NULL DEFAULT 0,
            count_unchanged INTEGER NOT NULL DEFAULT 0,
            count_deleted INTEGER NOT NULL DEFAULT 0,
            bytes_hashed  INTEGER NOT NULL DEFAULT 0,
            files_hashed  INTEGER NOT NULL DEFAULT 0,
            skipped_ignored INTEGER NOT NULL DEFAULT 0,
            skipped_too_large INTEGER NOT NULL DEFAULT 0,
            errors        INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS scan_errors (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id  INTEGER NOT NULL,
            path     TEXT    NOT NULL,
            stage    TEXT    NOT NULL,
            message  TEXT    NOT NULL,
            at       TEXT    NOT NULL
        )
        """,
    ],
    # -- v2: Phase 5, extracted content ------------------------------------
    [
        """
        CREATE TABLE IF NOT EXISTS documents (
            file_id       INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
            path          TEXT    NOT NULL,
            extractor     TEXT    NOT NULL,
            ok            INTEGER NOT NULL,
            error         TEXT    NOT NULL DEFAULT '',
            warnings      TEXT    NOT NULL DEFAULT '[]',
            meta          TEXT    NOT NULL DEFAULT '{}',
            text          TEXT    NOT NULL DEFAULT '',
            char_count    INTEGER NOT NULL DEFAULT 0,
            word_count    INTEGER NOT NULL DEFAULT 0,
            block_count   INTEGER NOT NULL DEFAULT 0,
            has_tabular   INTEGER NOT NULL DEFAULT 0,
            truncated     INTEGER NOT NULL DEFAULT 0,
            content_hash  TEXT,
            extracted_at  TEXT    NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_documents_ok   ON documents(ok)",
        "CREATE INDEX IF NOT EXISTS idx_documents_path ON documents(path)",
        """
        CREATE TABLE IF NOT EXISTS document_blocks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            block_index INTEGER NOT NULL,
            kind        TEXT    NOT NULL,
            label       TEXT    NOT NULL DEFAULT '',
            text        TEXT    NOT NULL,
            is_tabular  INTEGER NOT NULL DEFAULT 0,
            is_heading  INTEGER NOT NULL DEFAULT 0,
            row_count   INTEGER NOT NULL DEFAULT 0,
            char_start  INTEGER NOT NULL DEFAULT 0,
            char_end    INTEGER NOT NULL DEFAULT 0
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_blocks_file ON document_blocks(file_id)",
        "CREATE INDEX IF NOT EXISTS idx_blocks_tab  ON document_blocks(is_tabular)",
    ],
    # -- v3: Phase 6, entities, keywords, raw date mentions ----------------
    [
        """
        CREATE TABLE IF NOT EXISTS entities (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            text        TEXT    NOT NULL,
            normalised  TEXT    NOT NULL,
            category    TEXT    NOT NULL,
            spacy_label TEXT    NOT NULL DEFAULT '',
            char_start  INTEGER NOT NULL DEFAULT 0,
            char_end    INTEGER NOT NULL DEFAULT 0
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_entities_file ON entities(file_id)",
        "CREATE INDEX IF NOT EXISTS idx_entities_norm ON entities(normalised)",
        "CREATE INDEX IF NOT EXISTS idx_entities_cat  ON entities(category)",
        """
        CREATE TABLE IF NOT EXISTS keywords (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id  INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            term     TEXT    NOT NULL,
            count    INTEGER NOT NULL DEFAULT 1,
            rank     INTEGER NOT NULL DEFAULT 0
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_keywords_file ON keywords(file_id)",
        "CREATE INDEX IF NOT EXISTS idx_keywords_term ON keywords(term)",
        """
        CREATE TABLE IF NOT EXISTS date_mentions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            text        TEXT    NOT NULL,
            iso_date    TEXT,
            precision   TEXT    NOT NULL DEFAULT 'day',
            source      TEXT    NOT NULL DEFAULT '',
            char_start  INTEGER NOT NULL DEFAULT 0,
            char_end    INTEGER NOT NULL DEFAULT 0,
            in_tabular  INTEGER NOT NULL DEFAULT 0,
            year_inferred INTEGER NOT NULL DEFAULT 0
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_dates_file ON date_mentions(file_id)",
        "CREATE INDEX IF NOT EXISTS idx_dates_iso  ON date_mentions(iso_date)",
        """
        CREATE TABLE IF NOT EXISTS entity_runs (
            file_id      INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
            content_hash TEXT,
            error        TEXT NOT NULL DEFAULT '',
            duration_ms  REAL NOT NULL DEFAULT 0,
            run_at       TEXT NOT NULL
        )
        """,
    ],
]

#: The schema version this build of ContextFS writes.
SCHEMA_VERSION = len(MIGRATIONS)


class Store:
    """A connection to the ContextFS SQLite metadata database.

    Usable as a context manager::

        with Store(cfg.db_path) as store:
            store.upsert_file(record)
    """

    def __init__(self, path: Path | str, *, read_only: bool = False) -> None:
        """Open (and if necessary create and migrate) the database at ``path``.

        Args:
            path: Database file. Parent directories are created.
            read_only: Open without applying migrations. Used by ``stats`` and
                the GUI so that merely inspecting an index cannot alter it.
        """
        self.in_memory = str(path) == ":memory:"
        self.path = Path(path)
        self.read_only = read_only
        if not self.in_memory:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(":memory:" if self.in_memory else str(self.path))
        self.conn.row_factory = sqlite3.Row
        self._configure()
        if not read_only or self.in_memory:
            self.migrate()

    @classmethod
    def ephemeral(cls) -> Store:
        """Open a throwaway in-memory store.

        Used by ``scan --dry-run`` when no index exists yet, so that asking
        "what would happen?" does not itself create an index.
        """
        return cls(":memory:")

    def _configure(self) -> None:
        """Apply pragmas chosen for a single-user local index."""
        cursor = self.conn.cursor()
        # WAL: a reader (GUI) and a writer (scan) can run concurrently.
        if not self.in_memory:
            cursor.execute("PRAGMA journal_mode = WAL")
        # NORMAL rather than FULL: this is a rebuildable derived index, not a
        # ledger. Losing the last transaction to a power cut costs one rescan.
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute("PRAGMA foreign_keys = ON")
        # 8 MB page cache; the target machine has 14 GB but the whole point is
        # to stay small enough that ContextFS is never the reason it swaps.
        cursor.execute("PRAGMA cache_size = -8000")
        cursor.execute("PRAGMA temp_store = MEMORY")
        cursor.close()

    # -- lifecycle ---------------------------------------------------------

    def migrate(self) -> int:
        """Apply any outstanding migrations. Returns the resulting version."""
        cursor = self.conn.cursor()
        current = cursor.execute("PRAGMA user_version").fetchone()[0]
        for version in range(current, len(MIGRATIONS)):
            for statement in MIGRATIONS[version]:
                cursor.execute(statement)
            cursor.execute(f"PRAGMA user_version = {version + 1}")
        self.conn.commit()
        cursor.close()
        return self.schema_version

    @property
    def schema_version(self) -> int:
        """The schema version currently stored in the database file."""
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def close(self) -> None:
        """Commit and close the connection."""
        try:
            self.conn.commit()
        finally:
            self.conn.close()

    def __enter__(self) -> Store:
        """Enter a context manager."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close on context exit."""
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        """Run a batch of statements in one transaction.

        Batching matters on the target hardware: committing per file turns a
        40-file scan into 40 fsyncs on a laptop SATA SSD.
        """
        cursor = self.conn.cursor()
        try:
            yield cursor
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    # -- meta --------------------------------------------------------------

    def set_meta(self, key: str, value: Any) -> None:
        """Store a scalar under ``key``."""
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        self.conn.commit()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        """Read a scalar previously stored with :meth:`set_meta`."""
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    # -- files -------------------------------------------------------------

    def known_files(self) -> dict[str, sqlite3.Row]:
        """Return every non-deleted file row, keyed by relative path."""
        rows = self.conn.execute("SELECT * FROM files WHERE status = 'present'").fetchall()
        return {row["path"]: row for row in rows}

    def all_files(self, include_deleted: bool = False) -> list[sqlite3.Row]:
        """Return file rows, optionally including tombstoned ones."""
        sql = "SELECT * FROM files"
        if not include_deleted:
            sql += " WHERE status = 'present'"
        return self.conn.execute(sql + " ORDER BY path").fetchall()

    def get_file(self, path: str) -> sqlite3.Row | None:
        """Look up one file row by its corpus-relative path."""
        return self.conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()

    def get_file_by_id(self, file_id: int) -> sqlite3.Row | None:
        """Look up one file row by primary key."""
        return self.conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()

    def upsert_files(self, records: Iterable[dict[str, Any]]) -> None:
        """Insert or update file rows in a single transaction.

        ``first_seen`` is preserved across updates; ``content_changed_at`` is
        advanced only when the content hash actually differs, so it records
        genuine content change rather than mere re-observation.
        """
        with self.transaction() as cursor:
            cursor.executemany(
                """
                INSERT INTO files (
                    path, abs_path, name, stem, ext, folder, depth,
                    size, mtime_ns, mtime, content_hash, status,
                    first_seen, last_seen, content_changed_at
                ) VALUES (
                    :path, :abs_path, :name, :stem, :ext, :folder, :depth,
                    :size, :mtime_ns, :mtime, :content_hash, 'present',
                    :seen_at, :seen_at, :content_changed_at
                )
                ON CONFLICT(path) DO UPDATE SET
                    abs_path     = excluded.abs_path,
                    size         = excluded.size,
                    mtime_ns     = excluded.mtime_ns,
                    mtime        = excluded.mtime,
                    content_hash = COALESCE(excluded.content_hash, files.content_hash),
                    status       = 'present',
                    last_seen    = excluded.last_seen,
                    content_changed_at = CASE
                        WHEN excluded.content_hash IS NOT NULL
                         AND files.content_hash IS NOT NULL
                         AND excluded.content_hash <> files.content_hash
                        THEN excluded.last_seen
                        ELSE files.content_changed_at
                    END
                """,
                list(records),
            )

    def touch_seen(self, paths: Iterable[str], when: str | None = None) -> int:
        """Record that unchanged files were observed, without rewriting them.

        An unchanged file needs exactly one column updated. Running the full
        upsert for it would rewrite every column and every index entry on every
        scan, making incremental scan cost proportional to *corpus* size rather
        than to *change* size - which is the metric Phase 18 reports.

        Returns:
            The number of rows touched.
        """
        when = when or utc_now()
        paths = list(paths)
        if not paths:
            return 0
        with self.transaction() as cursor:
            # Chunked to stay under SQLite's default 999-variable limit.
            for start in range(0, len(paths), 500):
                chunk = paths[start : start + 500]
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"UPDATE files SET last_seen = ? WHERE path IN ({placeholders})",
                    [when, *chunk],
                )
        return len(paths)

    def mark_deleted(self, paths: Iterable[str], when: str | None = None) -> int:
        """Tombstone files that vanished from the filesystem.

        Rows are *tombstoned rather than removed* so that incremental updates
        in later phases can find and unwind the derived data (embeddings, graph
        edges, timeline nodes) that belonged to the file. Hard-deleting here
        would orphan all of it.

        Returns:
            The number of rows tombstoned.
        """
        when = when or utc_now()
        paths = list(paths)
        if not paths:
            return 0
        with self.transaction() as cursor:
            cursor.executemany(
                "UPDATE files SET status = 'deleted', last_seen = ? WHERE path = ?",
                [(when, path) for path in paths],
            )
        return len(paths)

    def purge_deleted(self) -> int:
        """Permanently remove tombstoned rows. Returns the number removed."""
        with self.transaction() as cursor:
            cursor.execute("DELETE FROM files WHERE status = 'deleted'")
            return cursor.rowcount

    def file_count(self, include_deleted: bool = False) -> int:
        """Count indexed files."""
        sql = "SELECT COUNT(*) FROM files"
        if not include_deleted:
            sql += " WHERE status = 'present'"
        return self.conn.execute(sql).fetchone()[0]

    def counts_by_extension(self) -> dict[str, int]:
        """Return ``{extension: count}`` over present files."""
        rows = self.conn.execute(
            "SELECT ext, COUNT(*) AS n FROM files WHERE status='present' "
            "GROUP BY ext ORDER BY n DESC"
        ).fetchall()
        return {row["ext"]: row["n"] for row in rows}

    # -- documents (Phase 5) ----------------------------------------------

    def save_document(self, file_id: int, doc: Any, content_hash: str | None = None) -> None:
        """Persist one :class:`~contextfs.extract.base.ExtractedDocument`.

        The document's blocks are replaced wholesale rather than merged: a
        re-extraction of a changed file must not leave stale blocks from the
        previous version behind, which would corrupt every downstream layer
        that reads block offsets.

        Args:
            file_id: ``files.id`` of the source file.
            doc: The extracted document.
            content_hash: The file's content hash at extraction time, so a later
                scan can tell whether the stored extraction is still current.
        """
        import json

        now = utc_now()
        with self.transaction() as cursor:
            cursor.execute("DELETE FROM document_blocks WHERE file_id = ?", (file_id,))
            cursor.execute(
                """
                INSERT INTO documents (
                    file_id, path, extractor, ok, error, warnings, meta, text,
                    char_count, word_count, block_count, has_tabular, truncated,
                    content_hash, extracted_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(file_id) DO UPDATE SET
                    path=excluded.path, extractor=excluded.extractor, ok=excluded.ok,
                    error=excluded.error, warnings=excluded.warnings, meta=excluded.meta,
                    text=excluded.text, char_count=excluded.char_count,
                    word_count=excluded.word_count, block_count=excluded.block_count,
                    has_tabular=excluded.has_tabular, truncated=excluded.truncated,
                    content_hash=excluded.content_hash, extracted_at=excluded.extracted_at
                """,
                (
                    file_id,
                    doc.rel_path,
                    doc.extractor,
                    int(doc.ok),
                    doc.error,
                    json.dumps(doc.warnings),
                    json.dumps(doc.meta, default=str),
                    doc.text,
                    doc.char_count,
                    doc.word_count,
                    doc.block_count,
                    int(doc.has_tabular_content),
                    int(doc.truncated),
                    content_hash,
                    now,
                ),
            )

            offset = 0
            rows = []
            for block in doc.blocks:
                start = offset
                end = start + len(block.text)
                rows.append(
                    (
                        file_id,
                        block.index,
                        block.kind,
                        block.label,
                        block.text,
                        int(block.is_tabular),
                        int(block.is_heading),
                        block.row_count,
                        start,
                        end,
                    )
                )
                offset = end + 2  # matches ExtractedDocument.text's "\n\n" join
            if rows:
                cursor.executemany(
                    "INSERT INTO document_blocks (file_id, block_index, kind, label, text, "
                    "is_tabular, is_heading, row_count, char_start, char_end) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    rows,
                )

    def get_document(self, file_id: int) -> sqlite3.Row | None:
        """Return the stored extraction for a file."""
        return self.conn.execute("SELECT * FROM documents WHERE file_id = ?", (file_id,)).fetchone()

    def get_document_by_path(self, path: str) -> sqlite3.Row | None:
        """Return the stored extraction for a corpus-relative path."""
        return self.conn.execute("SELECT * FROM documents WHERE path = ?", (path,)).fetchone()

    def get_blocks(self, file_id: int) -> list[sqlite3.Row]:
        """Return a file's blocks in document order."""
        return self.conn.execute(
            "SELECT * FROM document_blocks WHERE file_id = ? ORDER BY block_index", (file_id,)
        ).fetchall()

    def all_documents(self, ok_only: bool = True) -> list[sqlite3.Row]:
        """Return every stored extraction, joined to its still-present file."""
        sql = (
            "SELECT d.* FROM documents d JOIN files f ON f.id = d.file_id "
            "WHERE f.status = 'present'"
        )
        if ok_only:
            sql += " AND d.ok = 1"
        return self.conn.execute(sql + " ORDER BY d.path").fetchall()

    def document_count(self, ok_only: bool = True) -> int:
        """Count stored extractions."""
        sql = "SELECT COUNT(*) FROM documents"
        if ok_only:
            sql += " WHERE ok = 1"
        return self.conn.execute(sql).fetchone()[0]

    def delete_documents(self, file_ids: Iterable[int]) -> int:
        """Remove extractions (and their blocks) for the given files."""
        ids = list(file_ids)
        if not ids:
            return 0
        with self.transaction() as cursor:
            marks = ",".join("?" * len(ids))
            cursor.execute(f"DELETE FROM document_blocks WHERE file_id IN ({marks})", ids)
            cursor.execute(f"DELETE FROM documents WHERE file_id IN ({marks})", ids)
            return cursor.rowcount

    def files_needing_extraction(self) -> list[sqlite3.Row]:
        """Return present files with no extraction, or a stale one.

        Staleness is decided by comparing the file's current content hash with
        the hash recorded at extraction time - so a file whose *content* changed
        is re-extracted while a file that was merely re-observed is not.
        """
        return self.conn.execute(
            """
            SELECT f.* FROM files f
            LEFT JOIN documents d ON d.file_id = f.id
            WHERE f.status = 'present'
              AND (d.file_id IS NULL OR d.content_hash IS NOT f.content_hash)
            ORDER BY f.path
            """
        ).fetchall()

    # -- entities (Phase 6) ------------------------------------------------

    def save_entities(
        self, file_id: int, result: Any, content_hash: str | None = None, tabular_spans=None
    ) -> None:
        """Persist entities, keywords, and raw date mentions for one file.

        Existing rows for the file are deleted first, so re-running Layer 3 on a
        changed document replaces its analysis rather than accumulating
        duplicates across scans.

        Args:
            file_id: ``files.id`` of the source document.
            result: A :class:`~contextfs.entities.DocumentEntities`.
            content_hash: Hash at analysis time, for staleness detection.
            tabular_spans: ``[(start, end), ...]`` character ranges of tabular
                content, from :meth:`ExtractedDocument.tabular_spans`. Each date
                mention is stamped with whether it falls inside one - computed
                here, once, so the Phase 10 classifier never re-parses source
                files to answer a question extraction already knew.
        """
        spans = list(tabular_spans or [])
        with self.transaction() as cursor:
            for table in ("entities", "keywords", "date_mentions"):
                cursor.execute(f"DELETE FROM {table} WHERE file_id = ?", (file_id,))

            if result.entities:
                cursor.executemany(
                    "INSERT INTO entities (file_id, text, normalised, category, spacy_label, "
                    "char_start, char_end) VALUES (?,?,?,?,?,?,?)",
                    [
                        (file_id, m.text, m.normalised, m.category, m.spacy_label, m.start, m.end)
                        for m in result.entities
                    ],
                )
            if result.keywords:
                cursor.executemany(
                    "INSERT INTO keywords (file_id, term, count, rank) VALUES (?,?,?,?)",
                    [
                        (file_id, term, count, rank)
                        for rank, (term, count) in enumerate(result.keywords)
                    ],
                )
            if result.dates:
                cursor.executemany(
                    "INSERT INTO date_mentions (file_id, text, iso_date, precision, source, "
                    "char_start, char_end, in_tabular, year_inferred) VALUES (?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            file_id,
                            d.text,
                            d.iso,
                            d.precision,
                            d.source,
                            d.start,
                            d.end,
                            int(any(s <= d.start < e for s, e in spans)),
                            int(d.year_inferred),
                        )
                        for d in result.dates
                    ],
                )
            cursor.execute(
                "INSERT INTO entity_runs (file_id, content_hash, error, duration_ms, run_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(file_id) DO UPDATE SET "
                "content_hash=excluded.content_hash, error=excluded.error, "
                "duration_ms=excluded.duration_ms, run_at=excluded.run_at",
                (file_id, content_hash, result.error, result.duration_ms, utc_now()),
            )

    def get_entities(self, file_id: int, category: str | None = None) -> list[sqlite3.Row]:
        """Return a file's entity mentions, optionally filtered by category."""
        if category:
            return self.conn.execute(
                "SELECT * FROM entities WHERE file_id = ? AND category = ? ORDER BY char_start",
                (file_id, category),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM entities WHERE file_id = ? ORDER BY char_start", (file_id,)
        ).fetchall()

    def get_keywords(self, file_id: int) -> list[sqlite3.Row]:
        """Return a file's keywords in rank order."""
        return self.conn.execute(
            "SELECT * FROM keywords WHERE file_id = ? ORDER BY rank", (file_id,)
        ).fetchall()

    def get_date_mentions(self, file_id: int | None = None) -> list[sqlite3.Row]:
        """Return date mentions for one file, or for the whole corpus."""
        if file_id is None:
            return self.conn.execute(
                "SELECT dm.*, f.path FROM date_mentions dm JOIN files f ON f.id = dm.file_id "
                "WHERE f.status = 'present' ORDER BY f.path, dm.char_start"
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM date_mentions WHERE file_id = ? ORDER BY char_start", (file_id,)
        ).fetchall()

    def entity_category_votes(self) -> dict[str, dict[str, int]]:
        """Return ``{normalised: {category: distinct_file_count}}``.

        The input to :meth:`reconcile_entity_categories`.
        """
        rows = self.conn.execute(
            "SELECT e.normalised, e.category, COUNT(DISTINCT e.file_id) AS n "
            "FROM entities e JOIN files f ON f.id = e.file_id "
            "WHERE f.status = 'present' GROUP BY e.normalised, e.category"
        ).fetchall()
        votes: dict[str, dict[str, int]] = {}
        for row in rows:
            votes.setdefault(row["normalised"], {})[row["category"]] = row["n"]
        return votes

    def reconcile_entity_categories(self) -> int:
        """Resolve category disagreements for the same entity across the corpus.

        A statistical NER model types an entity from local sentence context, so
        the *same* string can be typed differently in different documents.
        Measured on this corpus: "Zoho" was typed ``org`` in the application
        tracker and ``person`` in the company-research notes, purely because the
        latter is written as Markdown headings.

        Resolution rule: for each normalised entity string, the category
        supported by the **strictly greatest number of distinct files** wins and
        is applied everywhere. A tie changes nothing - a coin-flip would be
        worse than an inconsistency, because it is unpredictable.

        This is a corpus-level correction that stays compatible with incremental
        indexing: it reads the votes already stored, so re-analysing one file
        immediately benefits from what the rest of the corpus knows.

        Returns:
            The number of mention rows whose category was changed.
        """
        changed = 0
        with self.transaction() as cursor:
            for normalised, tally in self.entity_category_votes().items():
                if len(tally) < 2:
                    continue
                ranked = sorted(tally.items(), key=lambda kv: -kv[1])
                if ranked[0][1] == ranked[1][1]:
                    continue  # genuine tie: leave the evidence alone
                winner = ranked[0][0]
                cursor.execute(
                    "UPDATE entities SET category = ? WHERE normalised = ? AND category <> ?",
                    (winner, normalised, winner),
                )
                changed += cursor.rowcount
        return changed

    def entity_index(self) -> dict[str, set[int]]:
        """Return ``{entity_key: {file_id, ...}}`` across the corpus.

        This is the input to entity-overlap edges in Phase 9. Built with one
        query rather than per-file lookups because the graph builder needs the
        whole picture at once.
        """
        rows = self.conn.execute(
            "SELECT e.category, e.normalised, e.file_id FROM entities e "
            "JOIN files f ON f.id = e.file_id WHERE f.status = 'present'"
        ).fetchall()
        index: dict[str, set[int]] = {}
        for row in rows:
            key = f"{row['category']}:{row['normalised']}"
            index.setdefault(key, set()).add(row["file_id"])
        return index

    def date_recurrence(self) -> dict[str, int]:
        """Return ``{iso_date: number_of_distinct_files}``.

        This is the cross-file recurrence signal for Phase 10, computed once
        over the corpus rather than per document.
        """
        rows = self.conn.execute(
            "SELECT dm.iso_date, COUNT(DISTINCT dm.file_id) AS n FROM date_mentions dm "
            "JOIN files f ON f.id = dm.file_id "
            "WHERE f.status = 'present' AND dm.iso_date IS NOT NULL "
            "GROUP BY dm.iso_date"
        ).fetchall()
        return {row["iso_date"]: row["n"] for row in rows}

    def files_needing_entities(self) -> list[sqlite3.Row]:
        """Return files whose entity analysis is missing or stale."""
        return self.conn.execute(
            """
            SELECT f.*, d.text AS doc_text FROM files f
            JOIN documents d ON d.file_id = f.id AND d.ok = 1
            LEFT JOIN entity_runs er ON er.file_id = f.id
            WHERE f.status = 'present'
              AND (er.file_id IS NULL OR er.content_hash IS NOT f.content_hash)
            ORDER BY f.path
            """
        ).fetchall()

    def entity_count(self) -> int:
        """Total entity mentions across present files."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM entities e JOIN files f ON f.id = e.file_id "
            "WHERE f.status = 'present'"
        ).fetchone()[0]

    def date_mention_count(self) -> int:
        """Total raw date mentions across present files."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM date_mentions dm JOIN files f ON f.id = dm.file_id "
            "WHERE f.status = 'present'"
        ).fetchone()[0]

    # -- scan runs ---------------------------------------------------------

    def start_scan(self, root: str, *, full: bool, dry_run: bool) -> int:
        """Open a scan-run record and return its id."""
        cursor = self.conn.execute(
            "INSERT INTO scan_runs (root, started_at, full_rescan, dry_run) VALUES (?, ?, ?, ?)",
            (root, utc_now(), int(full), int(dry_run)),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def finish_scan(self, scan_id: int, stats: dict[str, Any]) -> None:
        """Close a scan-run record with its measured statistics."""
        self.conn.execute(
            """
            UPDATE scan_runs SET
                finished_at = :finished_at, duration_ms = :duration_ms,
                files_seen = :files_seen, count_new = :count_new,
                count_modified = :count_modified, count_unchanged = :count_unchanged,
                count_deleted = :count_deleted, bytes_hashed = :bytes_hashed,
                files_hashed = :files_hashed, skipped_ignored = :skipped_ignored,
                skipped_too_large = :skipped_too_large, errors = :errors
            WHERE id = :scan_id
            """,
            {**stats, "scan_id": scan_id, "finished_at": utc_now()},
        )
        self.conn.commit()

    def last_scan(self) -> sqlite3.Row | None:
        """Return the most recent completed scan run."""
        return self.conn.execute(
            "SELECT * FROM scan_runs WHERE finished_at IS NOT NULL "
            "AND dry_run = 0 ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def scan_history(self, limit: int = 10) -> list[sqlite3.Row]:
        """Return recent scan runs, newest first."""
        return self.conn.execute(
            "SELECT * FROM scan_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def record_errors(self, scan_id: int, errors: Iterable[tuple[str, str, str]]) -> None:
        """Persist ``(path, stage, message)`` failures from a scan.

        Errors are recorded rather than raised so that one unreadable file
        cannot abort an otherwise successful index build - but they are never
        silently discarded, which is the failure mode this table exists to
        prevent.
        """
        errors = list(errors)
        if not errors:
            return
        now = utc_now()
        with self.transaction() as cursor:
            cursor.executemany(
                "INSERT INTO scan_errors (scan_id, path, stage, message, at) VALUES (?,?,?,?,?)",
                [(scan_id, path, stage, message, now) for path, stage, message in errors],
            )

    def errors_for_scan(self, scan_id: int) -> list[sqlite3.Row]:
        """Return recorded errors for one scan run."""
        return self.conn.execute(
            "SELECT * FROM scan_errors WHERE scan_id = ? ORDER BY id", (scan_id,)
        ).fetchall()
