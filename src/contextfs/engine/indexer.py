"""``Engine.update()``: incremental indexing of the configured roots (ADR-0001, 0005, 0006, 0011).

Per changed file: walk entry → share-delete read → hash → temp copy → extract (v1 extractors) →
time evidence → chunks → FTS → embeddings. Unchanged files (same size and mtime) are only
touched. Batches commit every ``BATCH_FILES`` files or ``BATCH_SECONDS`` seconds, so a crash or a
cancel loses at most one batch and the next update resumes. Then the global stages are rebuilt:
per-event ``file_time`` for every estimator, and sessions.

Safety rules:
- A missing root is marked offline; its files are kept and never tombstoned.
- A file is tombstoned only if its directory was listed successfully in this update.
- Placeholders and EFS-encrypted files are indexed as metadata only.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from contextfs.engine import fsio, guard, timeev
from contextfs.engine.estimate import ESTIMATORS, FileEvidence, folder_context
from contextfs.engine.schema import EVENTS
from contextfs.engine.vectors import VectorFiles

__all__ = ["UpdateReport", "run_update"]

BATCH_FILES = 500
BATCH_SECONDS = 5.0
READ_EXT_FOR_EVIDENCE = {".jpg", ".jpeg", ".tif", ".tiff", ".zip"}  # read for in-file times only
#: Zip-based documents are checked before parsing: a small file can unpack to gigabytes.
ZIP_FORMATS = {".docx", ".pptx", ".xlsx", ".odt", ".ods", ".odp", ".epub", ".zip"}
ZIP_EXPANDED_CAP = 512 * 2**20
FORCED_DETAIL = {
    "quarantined": "made the indexer crash or hang twice; not opened again until it changes",
    "parse_error": "reading or parsing failed; indexed by name and dates only (see errors)",
}


@dataclass
class UpdateReport:
    """What an update did."""

    seen: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    deleted: int = 0
    offline_roots: list[str] = field(default_factory=list)
    metadata_only: dict[str, int] = field(default_factory=dict)
    errors: int = 0
    chunks_embedded: int = 0
    seconds: float = 0.0
    cancelled: bool = False
    healed_folders: int = 0  # folders whose files had no per-event dates (see run_update)
    removed_roots: list[str] = field(default_factory=list)  # left the user's list (prune)
    low_disk: bool = False  # stopped cleanly below the free-space floor (engine.guard)
    struck: str | None = None  # the previous run died inside this file (engine.guard)
    quarantined: int = 0  # files indexed by name only after crashing/hanging the indexer twice
    parse_errors: int = 0  # files whose reading or parsing raised; indexed by name only
    repaired_vectors: int = 0  # files re-queued because their vectors were missing on disk

    def as_dict(self) -> dict:
        """JSON-serialisable form."""
        return dict(self.__dict__)


def _utc_now(clock: Callable[[], datetime] | None) -> int:
    return int((clock() if clock else datetime.now(timezone.utc)).timestamp())


def run_update(
    engine,
    roots: list[Path],
    progress=None,
    cancel=None,
    clock=None,
    prune_other_roots=False,
    file_timeout: float | None = None,
    on_hang=None,
) -> UpdateReport:
    """Index ``roots`` into ``engine``'s database. ``clock`` is injectable (ADR-0010 Rev. 1).

    Safety (engine.guard): refuses to start when the disk is nearly full; records the file in
    progress so a crash or hang becomes a strike against it; with ``file_timeout`` and
    ``on_hang``, a watchdog reports a file stuck for longer than ``file_timeout`` seconds.
    """
    guard.check_free_space(engine.index_dir)
    rep = UpdateReport()
    marker = guard.InProgress(engine.index_dir)
    crashed = marker.previous()
    marker.open()
    dog = guard.Watchdog(marker, file_timeout, on_hang).start() if file_timeout and on_hang else None
    try:
        return _run_update(engine, roots, progress, cancel, clock, prune_other_roots, rep, marker, crashed)
    finally:
        if dog is not None:
            dog.stop()
        marker.close()


def _run_update(engine, roots, progress, cancel, clock, prune_other_roots, rep, marker, crashed):
    from xxhash import xxh3_128_hexdigest

    from contextfs.embed import chunk_blocks
    from contextfs.extract import extract_file, extractor_for

    conn, started = engine.conn, time.perf_counter()
    vecs = VectorFiles(engine.index_dir / "vectors")

    def stop() -> bool:
        return rep.low_disk or bool(cancel and cancel())

    tmp_dir = engine.index_dir / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    for stale in tmp_dir.iterdir():  # sweep leftovers from a crash
        stale.unlink(missing_ok=True)
    now = _utc_now(clock)
    scan_id = conn.execute(
        "INSERT INTO scans(started_at, state) VALUES (?, 'running')",
        (datetime.fromtimestamp(now, timezone.utc).isoformat(),),
    ).lastrowid
    conn.commit()
    if crashed:
        _strike(conn, crashed, now)
        rep.struck = crashed.get("rel_path")
    rep.repaired_vectors = _repair_vectors(conn, vecs)
    pending_chunks: list[tuple[int, int, str]] = []  # (file_id, chunk_id, text)
    # Folders whose per-event dates must be recomputed; drained at every batch commit, so a cancel
    # or a kill never leaves committed files without dates (log.md Phase 33, real-data demo).
    touched_folders: set[tuple[int, str]] = set()
    batch_started, batch_n = time.perf_counter(), 0
    if prune_other_roots:  # roots no longer in the user's list leave search (data kept)
        wanted = {_root_key(r) for r in roots}
        for row in conn.execute("SELECT root_id, path FROM roots WHERE state!='removed'").fetchall():
            if _root_key(row[1]) not in wanted:
                conn.execute("UPDATE roots SET state='removed' WHERE root_id=?", (row[0],))
                rep.removed_roots.append(row[1])
        conn.commit()
    # Self-heal: indexes written before that fix (or killed mid-batch) may hold files with no dates.
    healed = _folders_missing_times(conn)
    if healed:
        recompute_times(conn, healed)
        _set_meta(conn, "sessions_stale", "1")
        rep.healed_folders = len(healed)

    def flush():
        nonlocal batch_started, batch_n
        if pending_chunks:
            texts = [t for _, _, t in pending_chunks]
            mat = engine.bulk_encoder.encode(texts)
            start = vecs.append("chunk", mat)
            conn.executemany(
                "INSERT OR REPLACE INTO vectors(kind,item_id,file_id,segment,row,embedder,valid) "
                "VALUES ('chunk',?,?,'chunk',?,?,1)",
                [
                    (cid, fid, start + i, engine.bulk_encoder.fingerprint)
                    for i, (fid, cid, _) in enumerate(pending_chunks)
                ],
            )
            by_file: dict[int, list[int]] = {}
            for i, (fid, _, _) in enumerate(pending_chunks):
                by_file.setdefault(fid, []).append(i)
            docs = np.stack([mat[ix].mean(axis=0) for ix in by_file.values()])
            docs /= np.clip(np.linalg.norm(docs, axis=1, keepdims=True), 1e-12, None)
            dstart = vecs.append("doc", docs)
            conn.executemany(
                "INSERT OR REPLACE INTO vectors(kind,item_id,file_id,segment,row,embedder,valid) "
                "VALUES ('doc',?,?,'doc',?,?,1)",
                [
                    (fid, fid, dstart + i, engine.bulk_encoder.fingerprint)
                    for i, fid in enumerate(by_file)
                ],
            )
            rep.chunks_embedded += len(pending_chunks)
            pending_chunks.clear()
        if touched_folders:
            recompute_times(conn, touched_folders)
            touched_folders.clear()
            _set_meta(conn, "sessions_stale", "1")
        conn.commit()
        if guard.low_disk_while_running(engine.index_dir):
            rep.low_disk = True  # stop like a cancel: everything so far is committed
        batch_started, batch_n = time.perf_counter(), 0

    for root_path in roots:
        if rep.cancelled:
            break
        root_path = Path(root_path)
        row = next(
            (r for r in conn.execute("SELECT root_id, path FROM roots") if _root_key(r[1]) == _root_key(root_path)),
            None,
        )
        root_id = (
            row["root_id"]
            if row
            else conn.execute(
                "INSERT INTO roots(path, added_at) VALUES (?, ?)",
                (str(root_path), datetime.fromtimestamp(now, timezone.utc).isoformat()),
            ).lastrowid
        )
        if not root_path.exists():
            conn.execute("UPDATE roots SET state='offline' WHERE root_id=?", (root_id,))
            conn.execute(
                "UPDATE files SET status='offline' WHERE root_id=? AND status='present'", (root_id,)
            )
            rep.offline_roots.append(str(root_path))
            conn.commit()
            continue
        conn.execute("UPDATE roots SET state='online' WHERE root_id=?", (root_id,))
        conn.execute(
            "UPDATE files SET status='present' WHERE root_id=? AND status='offline'", (root_id,)
        )
        known = {
            r["rel_path"]: r
            for r in conn.execute(
                "SELECT file_id, rel_path, size, mtime_ns, content_hash, status FROM files WHERE root_id=?",
                (root_id,),
            )
        }
        stats = fsio.WalkStats()
        seen: set[str] = set()
        for entry in fsio.walk(
            root_path,
            root_id,
            stats=stats,
            self_exclude=[engine.index_dir],
            anchored=fsio.default_anchored_exclusions(),
            cancel=stop,
        ):
            if stop():
                rep.cancelled = True
                break
            rep.seen += 1
            seen.add(entry.rel_path)
            prev = known.get(entry.rel_path)
            if (
                prev
                and prev["status"] == "present"
                and prev["size"] == entry.size
                and prev["mtime_ns"] == entry.mtime_ns
            ):
                rep.unchanged += 1
                continue
            args = (engine, conn, entry, prev, now, tmp_dir, rep, pending_chunks,
                    xxh3_128_hexdigest, extract_file, extractor_for, chunk_blocks)
            forced = "quarantined" if _is_quarantined(conn, root_id, entry) else None
            rep.quarantined += forced is not None
            marker.set(root_id, entry.rel_path, entry.size, entry.mtime_ns)
            snapshot = (rep.new, rep.changed, dict(rep.metadata_only), len(pending_chunks))
            if not conn.in_transaction:  # keep the batch one transaction: a bare SAVEPOINT would
                conn.execute("BEGIN")  # open one that RELEASE commits, before the batch's vectors
            conn.execute("SAVEPOINT one_file")
            try:
                file_id = _index_file(*args, force_access=forced)
            except Exception as exc:  # noqa: BLE001 - one bad file must never stop a scan
                conn.execute("ROLLBACK TO one_file")
                rep.new, rep.changed, rep.metadata_only = snapshot[0], snapshot[1], snapshot[2]
                del pending_chunks[snapshot[3]:]
                rep.parse_errors += 1
                conn.execute(
                    "INSERT INTO errors(scan_at, path, kind, detail) VALUES (?,?,?,?)",
                    (str(now), entry.rel_path, "parse_error", f"{type(exc).__name__}: {exc}"[:300]),
                )
                file_id = _index_file(*args, force_access="parse_error")
            conn.execute("RELEASE one_file")
            marker.clear()
            folder = entry.rel_path.rsplit("/", 1)[0] if "/" in entry.rel_path else ""
            touched_folders.add((root_id, folder))
            batch_n += 1
            if progress:
                progress(rep)
            if batch_n >= BATCH_FILES or time.perf_counter() - batch_started > BATCH_SECONDS:
                flush()
            del file_id
        if stop():
            # the walk also stops on cancel; without this the tombstone pass below would
            # treat every file it never reached as deleted
            rep.cancelled = True
        for err in stats.errors:
            conn.execute(
                "INSERT INTO errors(scan_at, path, kind, detail) VALUES (?,?,?,?)",
                (str(now), err.path, err.kind, err.detail),
            )
        rep.errors += len(stats.errors)
        if not rep.cancelled:
            for rel, prev in known.items():
                parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
                if rel not in seen and prev["status"] == "present" and parent in stats.listed_dirs:
                    _tombstone(conn, prev["file_id"])
                    touched_folders.add((root_id, parent))
                    rep.deleted += 1
        flush()

    # Sessions are global and slower (5 s for 5k files), so they wait for a completed scan; the
    # stale flag carries the need across cancelled scans.
    if not rep.cancelled and _get_meta(conn, "sessions_stale", "1") == "1":
        from contextfs.engine.sessions import rebuild_sessions

        rebuild_sessions(conn)
        _set_meta(conn, "sessions_stale", "0")
    rep.seconds = round(time.perf_counter() - started, 2)
    conn.execute(
        "UPDATE scans SET finished_at=?, state=?, stats_json=? WHERE scan_id=?",
        (
            datetime.now(timezone.utc).isoformat(),
            "cancelled" if rep.cancelled else "done",
            json.dumps(rep.as_dict()),
            scan_id,
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('index_generation', ?)", (str(scan_id),)
    )
    conn.commit()
    return rep


def _index_file(
    engine,
    conn,
    entry,
    prev,
    now,
    tmp_dir,
    rep,
    pending_chunks,
    hash_fn,
    extract_file,
    extractor_for,
    chunk_blocks,
    force_access: str | None = None,
) -> int:
    ext = Path(entry.rel_path).suffix.lower()
    access, detail, content_hash, data = "ok", None, None, None
    if force_access:
        access, detail = force_access, FORCED_DETAIL[force_access]
    elif entry.placeholder:
        access = "placeholder"
    elif entry.encrypted:
        access = "encrypted"
    elif extractor_for(Path(entry.rel_path)) is not None or ext in READ_EXT_FOR_EVIDENCE:
        try:
            data = fsio.read_bytes(entry)
            content_hash = hash_fn(data)
        except fsio.ReadError as err:
            access, detail = err.kind, err.detail
        if data is not None and ext in ZIP_FORMATS:
            expanded = _zip_expanded_bytes(data)
            if expanded > ZIP_EXPANDED_CAP:
                access, detail, data = "too_large", f"expands to {expanded // 2**20} MB when unpacked", None
    else:
        access = "unsupported"
    if access != "ok":
        rep.metadata_only[access] = rep.metadata_only.get(access, 0) + 1

    values = (
        entry.root_id,
        entry.rel_path,
        ext,
        entry.size,
        entry.mtime_ns,
        entry.birth_ns,
        entry.atime_ns,
        entry.attributes,
        entry.file_id,
        content_hash,
        access,
        detail,
        str(now),
    )
    if prev is None:
        file_id = conn.execute(
            "INSERT INTO files(root_id, rel_path, ext, size, mtime_ns, birth_ns, atime_ns, attributes, ntfs_id,"
            " content_hash, status, access, detail, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,'present',?,?,?)",
            values,
        ).lastrowid
        conn.execute(
            "INSERT INTO observations(file_id, kind, at_utc) VALUES (?, 'first_seen', ?)",
            (file_id, now),
        )
        rep.new += 1
    else:
        file_id = prev["file_id"]
        if content_hash and content_hash == prev["content_hash"]:
            conn.execute(
                "INSERT INTO observations(file_id, kind, at_utc, detail) VALUES (?, 'superseded_mtime', ?, ?)",
                (file_id, now, str(prev["mtime_ns"])),
            )
        conn.execute(
            "UPDATE files SET root_id=?, rel_path=?, ext=?, size=?, mtime_ns=?, birth_ns=?, atime_ns=?, attributes=?,"
            " ntfs_id=?, content_hash=?, status='present', access=?, detail=?, last_seen=? WHERE file_id=?",
            (*values, file_id),
        )
        rep.changed += 1

    copy_path = None
    try:
        if data is not None:
            copy_path = tmp_dir / f"{file_id}{ext}"
            copy_path.write_bytes(data)
        evidence = timeev.capture(entry, copy_path)
        conn.execute("DELETE FROM time_evidence WHERE file_id=?", (file_id,))
        conn.executemany(
            "INSERT INTO time_evidence(file_id, source, kind, utc_s, wall_s, tz_state, resolution_s, reliability,"
            " raw, captured_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    file_id,
                    e["source"],
                    e["kind"],
                    e["utc_s"],
                    e["wall_s"],
                    e["tz_state"],
                    e["resolution_s"],
                    e["reliability"],
                    e["raw"],
                    now,
                )
                for e in evidence
            ],
        )
        unchanged_content = (
            prev is not None and content_hash and content_hash == prev["content_hash"]
        )
        if (
            copy_path is not None
            and extractor_for(Path(entry.rel_path)) is not None
            and not unchanged_content
        ):
            doc = extract_file(copy_path, entry.rel_path)
            _store_document(
                conn, file_id, entry.rel_path, doc, content_hash, pending_chunks, chunk_blocks
            )
        elif not unchanged_content:
            _store_document(
                conn, file_id, entry.rel_path, None, content_hash, pending_chunks, chunk_blocks
            )
    finally:
        if copy_path is not None:
            copy_path.unlink(missing_ok=True)
    return file_id


def _name_words(rel_path: str) -> str:
    import re

    return " ".join(re.split(r"[\\/_\-.\s()]+", rel_path))


def embedding_text(rel_path: str, ord_: int, text: str, name_only: bool) -> str:
    """The text a chunk is embedded from (stored chunk text is kept unprefixed for snippets).

    A name-only chunk embeds the file's name words; the first content chunk is prefixed with
    them so dense search can match a file by its name and its opening together.
    """
    name_line = _name_words(rel_path)
    if name_only:
        return name_line
    return f"{name_line}. {text}" if ord_ == 0 else text


def _store_document(
    conn, file_id, rel_path, doc, content_hash, pending_chunks, chunk_blocks
) -> None:
    conn.execute("UPDATE vectors SET valid=0 WHERE file_id=?", (file_id,))
    conn.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
    conn.execute("DELETE FROM docs_fts WHERE rowid=?", (file_id,))
    conn.execute("DELETE FROM names_tri WHERE rowid=?", (file_id,))
    text = doc.text if doc is not None and doc.ok else ""
    conn.execute(
        "INSERT OR REPLACE INTO docs(file_id, title, text, chars, ok, extractor, error, input_hash) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            file_id,
            Path(rel_path).stem,
            text,
            len(text),
            int(bool(doc and doc.ok)),
            doc.extractor if doc else None,
            doc.error if doc else None,
            content_hash,
        ),
    )
    conn.execute(
        "INSERT INTO docs_fts(rowid, name, body) VALUES (?,?,?)",
        (file_id, _name_words(rel_path), text),
    )
    conn.execute("INSERT INTO names_tri(rowid, path) VALUES (?,?)", (file_id, rel_path))
    # Even a file with no text gets one "name" chunk, so dense search can find it by its name.
    chunks = (
        chunk_blocks(file_id, rel_path, doc.blocks)
        if doc is not None and doc.ok and doc.blocks
        else []
    )
    name_line = _name_words(rel_path)
    if not chunks:
        cid = conn.execute(
            "INSERT INTO chunks(file_id, ord, char_start, char_end, text) VALUES (?,?,?,?,?)",
            (file_id, 0, 0, 0, name_line),
        ).lastrowid
        pending_chunks.append((file_id, cid, embedding_text(rel_path, 0, name_line, name_only=True)))
        return
    for i, ch in enumerate(chunks[:200]):  # cap chunks per document (ADR-0001 Rev. 1)
        cid = conn.execute(
            "INSERT INTO chunks(file_id, ord, char_start, char_end, text) VALUES (?,?,?,?,?)",
            (file_id, i, ch.char_start, ch.char_end, ch.text),
        ).lastrowid
        pending_chunks.append((file_id, cid, embedding_text(rel_path, i, ch.text, name_only=False)))


def _tombstone(conn, file_id: int) -> None:
    conn.execute("UPDATE files SET status='deleted' WHERE file_id=?", (file_id,))
    conn.execute("UPDATE vectors SET valid=0 WHERE file_id=?", (file_id,))
    conn.execute("DELETE FROM docs_fts WHERE rowid=?", (file_id,))
    conn.execute("DELETE FROM names_tri WHERE rowid=?", (file_id,))


def _strike(conn, rec: dict, now: int) -> None:
    """The previous run died inside ``rec``'s file: count a strike (reset if the file changed)."""
    row = conn.execute(
        "SELECT size, mtime_ns, count FROM strikes WHERE root_id=? AND rel_path=?",
        (rec.get("root_id"), rec.get("rel_path")),
    ).fetchone()
    same = row is not None and (row[0], row[1]) == (rec.get("size"), rec.get("mtime_ns"))
    conn.execute(
        "INSERT OR REPLACE INTO strikes(root_id, rel_path, size, mtime_ns, count, last_at) VALUES (?,?,?,?,?,?)",
        (rec.get("root_id"), rec.get("rel_path"), rec.get("size"), rec.get("mtime_ns"),
         (row[2] + 1) if same else 1, now),
    )
    conn.commit()


def _is_quarantined(conn, root_id: int, entry) -> bool:
    row = conn.execute(
        "SELECT size, mtime_ns, count FROM strikes WHERE root_id=? AND rel_path=?", (root_id, entry.rel_path)
    ).fetchone()
    return bool(row) and row[2] >= guard.QUARANTINE_STRIKES and (row[0], row[1]) == (entry.size, entry.mtime_ns)


def _zip_expanded_bytes(data: bytes) -> int:
    """Total unpacked size of a zip-based document (0 if it is not a readable zip)."""
    import io
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return sum(i.file_size for i in zf.infolist())
    except (zipfile.BadZipFile, ValueError, OSError):
        return 0


def _repair_vectors(conn, vecs: VectorFiles) -> int:
    """Re-queue files whose vectors are missing; returns how many (ADR-0012 §3).

    Covers manifest rows past the end of a truncated or lost vector file, and chunks that never
    got a valid vector.
    """
    bad: set[int] = set()
    for kind in ("chunk", "doc"):
        total = vecs.rows(kind)
        rows = conn.execute(
            "SELECT file_id FROM vectors WHERE kind=? AND valid=1 AND row>=?", (kind, total)
        ).fetchall()
        if rows:
            conn.execute("UPDATE vectors SET valid=0 WHERE kind=? AND valid=1 AND row>=?", (kind, total))
            bad |= {r[0] for r in rows}
    # chunks without a valid vector (any older inconsistency) are re-embedded the same way
    bad |= {r[0] for r in conn.execute(
        "SELECT DISTINCT c.file_id FROM chunks c JOIN files f ON f.file_id=c.file_id WHERE f.status='present' "
        "AND NOT EXISTS (SELECT 1 FROM vectors v WHERE v.kind='chunk' AND v.item_id=c.chunk_id AND v.valid=1)"
    )}
    if bad:
        # size -1 never matches the walk, so the next scan re-reads and re-embeds these files
        # and no stored hash, so an unchanged file is still re-extracted and re-embedded
        conn.executemany("UPDATE files SET size=-1, content_hash=NULL WHERE file_id=?", [(f,) for f in bad])
    conn.commit()
    return len(bad)


def _root_key(path) -> str:
    """Compare roots the way Windows does: separators and case do not matter."""
    import os

    return os.path.normcase(os.path.normpath(str(path)))


def _get_meta(conn, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def _set_meta(conn, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))


def _folders_missing_times(conn) -> set[tuple[int, str]]:
    """Folders holding a present or offline file that has no ``file_time`` rows."""
    rows = conn.execute(
        "SELECT f.root_id, f.rel_path FROM files f WHERE f.status IN ('present','offline') "
        "AND NOT EXISTS (SELECT 1 FROM file_time t WHERE t.file_id=f.file_id)"
    )
    return {(r[0], r[1].rsplit("/", 1)[0] if "/" in r[1] else "") for r in rows}


def recompute_times(conn, folders: set[tuple[int, str]]) -> None:
    """Re-estimate per-event times for every file in the touched folders, for every estimator."""
    for root_id, folder in folders:
        like = f"{folder}/%" if folder else "%"
        files = conn.execute(
            "SELECT file_id, rel_path FROM files WHERE root_id=? AND status IN ('present','offline') AND rel_path LIKE ?",
            (root_id, like),
        ).fetchall()
        files = [
            f
            for f in files
            if (f["rel_path"].rsplit("/", 1)[0] if "/" in f["rel_path"] else "") == folder
        ]
        if not files:
            continue
        ids = [f["file_id"] for f in files]
        marks = ",".join("?" * len(ids))
        ev_rows: dict[int, dict[str, dict]] = {i: {} for i in ids}
        for r in conn.execute(f"SELECT * FROM time_evidence WHERE file_id IN ({marks})", ids):
            ev_rows[r["file_id"]][f"{r['source']}:{r['kind']}"] = dict(r)
        first_seen = {
            r["file_id"]: r["at"]
            for r in conn.execute(
                f"SELECT file_id, MIN(at_utc) AS at FROM observations WHERE kind='first_seen' AND file_id IN ({marks})"
                " GROUP BY file_id",
                ids,
            )
        }
        births = {
            i: ev_rows[i]["ntfs:created"]["utc_s"] for i in ids if "ntfs:created" in ev_rows[i]
        }
        ctx = folder_context(births)
        conn.execute(f"DELETE FROM file_time WHERE file_id IN ({marks})", ids)
        rows = []
        for i in ids:
            ev = FileEvidence(i, ev_rows[i], first_seen.get(i))
            for est in ESTIMATORS.values():
                for event, t in est.estimate(ev, ctx).items():
                    assert event in EVENTS
                    rows.append((i, est.name, event, *t.to_row()))
        conn.executemany(
            "INSERT INTO file_time(file_id, estimator, event, lo_utc, hi_utc, point_utc, confidence, method,"
            " evidence_json) VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
    conn.commit()
