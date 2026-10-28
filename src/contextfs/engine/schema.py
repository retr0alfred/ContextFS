"""SQLite schema of the v2 index, and its migrations (ADR-0005, ADR-0012).

One SQLite database is the single commit point. Vector bytes live in ``vectors/`` and are
registered here. Migrations are ordered, run one transaction each, and are recorded in
``PRAGMA user_version``.

The time model follows docs/engine/DESIGN.md §2:
- raw clues go in ``time_evidence``;
- the append-only scan history goes in ``observations``;
- interpreted per-event times go in ``file_time``, with one row per (file, estimator, event).
"""

from __future__ import annotations

import sqlite3

__all__ = ["MIGRATIONS", "SCHEMA_VERSION", "connect", "migrate", "EVENTS"]

#: The three time events of a file (DESIGN.md §2.1). Order is significant for display only.
EVENTS = ("authored", "arrived_here", "last_user_edit")

MIGRATIONS: list[list[str]] = [
    [
        """CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)""",
        """CREATE TABLE roots (
            root_id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, volume INTEGER,
            fs_type TEXT, state TEXT NOT NULL DEFAULT 'online', added_at TEXT)""",
        """CREATE TABLE files (
            file_id INTEGER PRIMARY KEY, root_id INTEGER NOT NULL REFERENCES roots(root_id),
            rel_path TEXT NOT NULL, ext TEXT, size INTEGER, mtime_ns INTEGER, birth_ns INTEGER,
            atime_ns INTEGER, attributes INTEGER, ntfs_id INTEGER, content_hash TEXT,
            status TEXT NOT NULL DEFAULT 'present',     -- present | deleted | offline
            access TEXT NOT NULL DEFAULT 'ok',          -- ok | placeholder | locked | denied | too_large | encrypted | changed | unsupported | failed
            detail TEXT, last_seen TEXT, UNIQUE(root_id, rel_path))""",
        "CREATE INDEX idx_files_hash ON files(content_hash)",
        """CREATE TABLE observations (
            id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL, kind TEXT NOT NULL,
            at_utc INTEGER NOT NULL, detail TEXT)""",
        "CREATE INDEX idx_obs_file ON observations(file_id)",
        """CREATE TABLE time_evidence (
            file_id INTEGER NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL,
            utc_s INTEGER, wall_s INTEGER, tz_state TEXT NOT NULL CHECK(tz_state IN ('utc','offset','floating')),
            resolution_s INTEGER NOT NULL DEFAULT 1, reliability TEXT NOT NULL DEFAULT 'normal',
            raw TEXT, captured_at INTEGER, PRIMARY KEY(file_id, source, kind))""",
        """CREATE TABLE file_time (
            file_id INTEGER NOT NULL, estimator TEXT NOT NULL, event TEXT NOT NULL
                CHECK(event IN ('authored','arrived_here','last_user_edit')),
            lo_utc INTEGER NOT NULL, hi_utc INTEGER NOT NULL, point_utc INTEGER NOT NULL,
            confidence TEXT NOT NULL CHECK(confidence IN ('sure','probably','unknown')),
            method TEXT NOT NULL, evidence_json TEXT NOT NULL,
            PRIMARY KEY(file_id, estimator, event))""",
        "CREATE INDEX idx_ft_point ON file_time(estimator, event, point_utc)",
        """CREATE TABLE docs (
            file_id INTEGER PRIMARY KEY, title TEXT, text TEXT, chars INTEGER, ok INTEGER,
            extractor TEXT, error TEXT, input_hash TEXT)""",
        """CREATE TABLE chunks (
            chunk_id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL, ord INTEGER NOT NULL,
            char_start INTEGER, char_end INTEGER, text TEXT NOT NULL)""",
        "CREATE INDEX idx_chunks_file ON chunks(file_id)",
        # Document-level lexical index: filename words + extracted text (ADR-0008 Rev. 1).
        "CREATE VIRTUAL TABLE docs_fts USING fts5(name, body, tokenize='unicode61 remove_diacritics 2')",
        # Substring search over names/paths only (trigram is for substrings, not typos).
        "CREATE VIRTUAL TABLE names_tri USING fts5(path, tokenize='trigram')",
        """CREATE TABLE vectors (
            kind TEXT NOT NULL, item_id INTEGER NOT NULL, file_id INTEGER NOT NULL,
            segment TEXT NOT NULL, row INTEGER NOT NULL, embedder TEXT NOT NULL, valid INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(kind, item_id))""",
        "CREATE INDEX idx_vectors_file ON vectors(file_id)",
        """CREATE TABLE sessions (
            file_id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, block_id TEXT NOT NULL)""",
        "CREATE INDEX idx_sessions_sid ON sessions(session_id)",
        """CREATE TABLE errors (
            id INTEGER PRIMARY KEY, scan_at TEXT, path TEXT, kind TEXT, detail TEXT)""",
        """CREATE TABLE scans (
            scan_id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, state TEXT, stats_json TEXT)""",
    ],
]
SCHEMA_VERSION = len(MIGRATIONS)


def connect(path, read_only: bool = False) -> sqlite3.Connection:
    """Open the index database with the project's pragmas."""
    if read_only:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    else:
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA journal_size_limit=67108864")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Apply outstanding migrations, one transaction each. Returns the new version."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"index schema {current} is newer than this ContextFS ({SCHEMA_VERSION})"
        )
    for version in range(current, SCHEMA_VERSION):
        with conn:
            for stmt in MIGRATIONS[version]:
                conn.execute(stmt)
            conn.execute(f"PRAGMA user_version = {version + 1}")
    return SCHEMA_VERSION
