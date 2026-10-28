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

from contextfs.engine import fsio, timeev
from contextfs.engine.estimate import ESTIMATORS, FileEvidence, folder_context
from contextfs.engine.schema import EVENTS
from contextfs.engine.vectors import VectorFiles

__all__ = ["UpdateReport", "run_update"]

BATCH_FILES = 500
BATCH_SECONDS = 5.0
READ_EXT_FOR_EVIDENCE = {".jpg", ".jpeg", ".tif", ".tiff", ".zip"}  # read for in-file times only


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

    def as_dict(self) -> dict:
        """JSON-serialisable form."""
        return dict(self.__dict__)


def _utc_now(clock: Callable[[], datetime] | None) -> int:
    return int((clock() if clock else datetime.now(timezone.utc)).timestamp())


def run_update(engine, roots: list[Path], progress=None, cancel=None, clock=None) -> UpdateReport:
    """Index ``roots`` into ``engine``'s database. ``clock`` is injectable (ADR-0010 Rev. 1)."""
    from xxhash import xxh3_128_hexdigest

    from contextfs.embed import chunk_blocks
    from contextfs.extract import extract_file, extractor_for

    conn, rep, started = engine.conn, UpdateReport(), time.perf_counter()
    vecs = VectorFiles(engine.index_dir / "vectors")
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
    pending_chunks: list[tuple[int, int, str]] = []  # (file_id, chunk_id, text)
    touched_folders: set[tuple[int, str]] = set()
    batch_started, batch_n = time.perf_counter(), 0

    def flush():
        nonlocal batch_started, batch_n
        if pending_chunks:
            texts = [t for _, _, t in pending_chunks]
            mat = engine.encoder.encode(texts)
            start = vecs.append("chunk", mat)
            conn.executemany(
                "INSERT OR REPLACE INTO vectors(kind,item_id,file_id,segment,row,embedder,valid) "
                "VALUES ('chunk',?,?,'chunk',?,?,1)",
                [
                    (cid, fid, start + i, engine.encoder.fingerprint)
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
                    (fid, fid, dstart + i, engine.encoder.fingerprint)
                    for i, fid in enumerate(by_file)
                ],
            )
            rep.chunks_embedded += len(pending_chunks)
            pending_chunks.clear()
        conn.commit()
        batch_started, batch_n = time.perf_counter(), 0

    for root_path in roots:
        if rep.cancelled:
            break
        root_path = Path(root_path)
        row = conn.execute("SELECT root_id FROM roots WHERE path=?", (str(root_path),)).fetchone()
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
            cancel=cancel,
        ):
            if cancel and cancel():
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
            file_id = _index_file(
                engine,
                conn,
                entry,
                prev,
                now,
                tmp_dir,
                rep,
                pending_chunks,
                xxh3_128_hexdigest,
                extract_file,
                extractor_for,
                chunk_blocks,
            )
            folder = entry.rel_path.rsplit("/", 1)[0] if "/" in entry.rel_path else ""
            touched_folders.add((root_id, folder))
            batch_n += 1
            if progress:
                progress(rep)
            if batch_n >= BATCH_FILES or time.perf_counter() - batch_started > BATCH_SECONDS:
                flush()
            del file_id
        if cancel and cancel():
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

    if not rep.cancelled:
        recompute_times(conn, touched_folders)
        from contextfs.engine.sessions import rebuild_sessions

        rebuild_sessions(conn)
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
) -> int:
    ext = Path(entry.rel_path).suffix.lower()
    access, detail, content_hash, data = "ok", None, None, None
    if entry.placeholder:
        access = "placeholder"
    elif entry.encrypted:
        access = "encrypted"
    elif extractor_for(Path(entry.rel_path)) is not None or ext in READ_EXT_FOR_EVIDENCE:
        try:
            data = fsio.read_bytes(entry)
            content_hash = hash_fn(data)
        except fsio.ReadError as err:
            access, detail = err.kind, err.detail
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
        pending_chunks.append((file_id, cid, name_line))
        return
    for i, ch in enumerate(chunks[:200]):  # cap chunks per document (ADR-0001 Rev. 1)
        cid = conn.execute(
            "INSERT INTO chunks(file_id, ord, char_start, char_end, text) VALUES (?,?,?,?,?)",
            (file_id, i, ch.char_start, ch.char_end, ch.text),
        ).lastrowid
        pending_chunks.append((file_id, cid, f"{name_line}. {ch.text}" if i == 0 else ch.text))


def _tombstone(conn, file_id: int) -> None:
    conn.execute("UPDATE files SET status='deleted' WHERE file_id=?", (file_id,))
    conn.execute("UPDATE vectors SET valid=0 WHERE file_id=?", (file_id,))
    conn.execute("DELETE FROM docs_fts WHERE rowid=?", (file_id,))
    conn.execute("DELETE FROM names_tri WHERE rowid=?", (file_id,))


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
