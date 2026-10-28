"""Indexer safety (Phase 33 hardening).

Quarantine, per-file isolation, zip bombs, disk floor, corruption recovery, vector repair,
migrations, and the hang watchdog.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
import zipfile

import pytest

from contextfs.engine import Engine, default_model_dir, guard
from contextfs.engine.schema import MIGRATIONS, SCHEMA_VERSION, migrate

pytestmark = pytest.mark.skipif(
    not (default_model_dir() / "model_fp32.onnx").exists(), reason="ONNX model not exported"
)


def _corpus(root):
    (root / "a").mkdir(parents=True)
    (root / "a" / "notes.md").write_text("normalization BCNF notes", encoding="utf-8")
    (root / "a" / "recipe.txt").write_text("biryani with saffron", encoding="utf-8")
    (root / "b").mkdir()
    (root / "b" / "budget.csv").write_text("rent,9000", encoding="utf-8")
    return root


def _access(eng, rel):
    return eng.conn.execute("SELECT access FROM files WHERE rel_path=?", (rel,)).fetchone()[0]


def test_a_file_that_kills_the_indexer_twice_is_quarantined(tmp_path):
    corpus = _corpus(tmp_path / "c")
    idx = tmp_path / "idx"
    st = (corpus / "a" / "recipe.txt").stat()
    record = {"root_id": 1, "rel_path": "a/recipe.txt", "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    with Engine.open(idx, mode="write") as eng:
        eng.update([corpus])  # creates root 1
        eng.conn.execute("UPDATE files SET size=-1 WHERE rel_path='a/recipe.txt'")  # force a re-read
        eng.conn.commit()
    for strike in (1, 2):
        (idx / guard.InProgress.NAME).write_text(json.dumps(record), encoding="utf-8")  # "died inside it"
        with Engine.open(idx, mode="write") as eng:
            rep = eng.update([corpus])
            assert rep.struck == "a/recipe.txt"
            if strike == 1:  # one strike is forgiven (a user closing the app looks the same)
                assert _access(eng, "a/recipe.txt") == "ok"
                eng.conn.execute("UPDATE files SET size=-1 WHERE rel_path='a/recipe.txt'")
                eng.conn.commit()
            else:
                assert rep.quarantined == 1 and _access(eng, "a/recipe.txt") == "quarantined"
                assert "recipe.txt" in eng.search("recipe").paths[0]  # still findable by name
                assert "saffron" not in json.dumps([r.reasons for r in eng.search("saffron").results])
    assert not (idx / guard.InProgress.NAME).exists()  # a clean run leaves no record


def test_a_parser_exception_degrades_one_file_not_the_scan(tmp_path, monkeypatch):
    import contextfs.extract as extract

    real = extract.extract_file

    def explode(path, rel_path, *a, **k):
        if rel_path.endswith("notes.md"):
            raise RecursionError("malformed document")
        return real(path, rel_path, *a, **k)

    monkeypatch.setattr(extract, "extract_file", explode)
    with Engine.open(tmp_path / "idx", mode="write") as eng:
        rep = eng.update([_corpus(tmp_path / "c")])
        assert rep.parse_errors == 1 and rep.new == 3
        assert _access(eng, "a/notes.md") == "parse_error"
        assert eng.conn.execute("SELECT COUNT(*) FROM errors WHERE kind='parse_error'").fetchone()[0] == 1
        assert eng.search("biryani saffron").paths[0] == "a/recipe.txt"


def test_a_zip_bomb_document_is_not_unpacked(tmp_path):
    corpus = _corpus(tmp_path / "c")
    with zipfile.ZipFile(corpus / "a" / "bomb.docx", "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", b"0" * (600 * 2**20))  # ~600 MB unpacked, ~0.6 MB packed
    with Engine.open(tmp_path / "idx", mode="write") as eng:
        rep = eng.update([corpus])
        assert rep.new == 4 and _access(eng, "a/bomb.docx") == "too_large"


def test_low_disk_refuses_to_start_and_leaves_the_index_untouched(tmp_path, monkeypatch):
    corpus = _corpus(tmp_path / "c")
    with Engine.open(tmp_path / "idx", mode="write") as eng:
        eng.update([corpus])
        monkeypatch.setenv("CONTEXTFS_MIN_FREE_MB", str(10**9))
        with pytest.raises(guard.DiskSpaceLow):
            eng.update([corpus])
        assert eng.status()["files"] == 3


def test_low_disk_while_running_stops_cleanly(tmp_path, monkeypatch):
    import contextfs.engine.indexer as indexer

    monkeypatch.setattr(indexer, "BATCH_FILES", 1)  # a batch boundary after every file
    monkeypatch.setenv("CONTEXTFS_MIN_FREE_RUN_MB", str(10**9))
    with Engine.open(tmp_path / "idx", mode="write") as eng:
        rep = eng.update([_corpus(tmp_path / "c")])
        assert rep.low_disk and rep.cancelled and rep.seen < 3 and rep.deleted == 0


def test_a_damaged_database_is_set_aside_and_rebuilt(tmp_path):
    corpus = _corpus(tmp_path / "c")
    idx = tmp_path / "idx"
    with Engine.open(idx, mode="write") as eng:
        eng.update([corpus])
    (idx / "contextfs.db").write_bytes(b"this is not a database" * 1000)
    for side in ("contextfs.db-wal", "contextfs.db-shm"):
        (idx / side).unlink(missing_ok=True)
    with Engine.open(idx, mode="write") as eng:
        assert eng.recovered
        rep = eng.update([corpus])
        assert rep.new == 3
    assert len(list(idx.glob("damaged-*"))) == 1
    with Engine.open(idx) as eng:
        assert eng.search("biryani").paths[0] == "a/recipe.txt"


def test_truncated_vectors_never_crash_search_and_are_re_embedded(tmp_path):
    corpus = _corpus(tmp_path / "c")
    idx = tmp_path / "idx"
    with Engine.open(idx, mode="write") as eng:
        eng.update([corpus])
    for kind in ("chunk", "doc"):
        f = idx / "vectors" / f"{kind}.f32"
        f.write_bytes(f.read_bytes()[: 384 * 4])  # keep one row
    with Engine.open(idx) as eng:
        eng.search("biryani")  # must not raise
    with Engine.open(idx, mode="write") as eng:
        rep = eng.update([corpus])
        assert rep.repaired_vectors >= 2 and rep.changed >= 2
    with Engine.open(idx) as eng:
        assert eng.search("biryani saffron").paths[0] == "a/recipe.txt"


def test_migration_from_schema_1_is_atomic_and_complete(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    for stmt in MIGRATIONS[0]:
        conn.execute(stmt)
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    assert migrate(conn) == SCHEMA_VERSION
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.execute("SELECT * FROM strikes").fetchall()


def test_a_failing_migration_rolls_back_entirely(tmp_path, monkeypatch):
    import contextfs.engine.schema as schema

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    for stmt in MIGRATIONS[0]:
        conn.execute(stmt)
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    monkeypatch.setattr(schema, "MIGRATIONS", [*MIGRATIONS[:1], ["CREATE TABLE half (x)", "THIS IS NOT SQL"]])
    monkeypatch.setattr(schema, "SCHEMA_VERSION", 2)
    with pytest.raises(sqlite3.OperationalError):
        schema.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert not conn.execute("SELECT name FROM sqlite_master WHERE name='half'").fetchall()


def test_a_newer_index_is_refused_not_rebuilt(tmp_path):
    idx = tmp_path / "idx"
    with Engine.open(idx, mode="write") as eng:
        eng.update([_corpus(tmp_path / "c")])
        eng.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
        eng.conn.commit()
    with pytest.raises(RuntimeError, match="newer"):
        Engine.open(idx, mode="write")
    assert not list(idx.glob("damaged-*"))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows share modes")
def test_a_file_locked_during_one_scan_is_read_on_the_next(tmp_path):
    # Hardening suite C04: the locked file kept its size and mtime, so it was never retried.
    import ctypes
    from ctypes import wintypes

    corpus = _corpus(tmp_path / "c")
    target = corpus / "a" / "notes.md"
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    handle = k32.CreateFileW(str(target), 0x80000000, 0, None, 3, 0x80, None)  # no sharing at all
    with Engine.open(tmp_path / "idx", mode="write") as eng:
        try:
            eng.update([corpus])
            assert _access(eng, "a/notes.md") == "locked"
        finally:
            k32.CloseHandle(wintypes.HANDLE(handle))
        eng.update([corpus])
        assert _access(eng, "a/notes.md") == "ok"
        assert eng.search("normalization BCNF").paths[0] == "a/notes.md"


def test_the_watchdog_reports_a_stuck_file(tmp_path):
    marker = guard.InProgress(tmp_path)
    marker.open()
    hits = []
    dog = guard.Watchdog(marker, 0.3, hits.append).start()
    marker.set(1, "slow.pdf", 10, 20)
    time.sleep(1.5)
    dog.stop()
    marker.close()
    assert hits and hits[0]["rel_path"] == "slow.pdf"


# -- Independent QA findings (log.md Phase 33) ----------------------------------------------------


def test_qa_h1_a_renamed_or_deleted_folder_leaves_no_ghost_results(tmp_path):
    corpus = _corpus(tmp_path / "c")
    with Engine.open(tmp_path / "idx", mode="write") as eng:
        eng.update([corpus])
        (corpus / "a").rename(corpus / "renamed")
        rep = eng.update([corpus])
        paths = eng.search("biryani saffron").paths
        assert rep.deleted == 2 and "a/recipe.txt" not in paths and "renamed/recipe.txt" in paths
        import shutil

        shutil.rmtree(corpus / "renamed")
        eng.update([corpus])
        assert not [p for p in eng.search("biryani saffron").paths if "recipe" in p]


def test_qa_h2_a_1601_dated_file_does_not_stop_the_scan(tmp_path):
    import os

    corpus = _corpus(tmp_path / "c")
    old = corpus / "a" / "ancient.txt"
    old.write_text("ancient parchment", encoding="utf-8")
    zero = -11644473600 * 10**9 + 10**9  # one second after the Windows file-time epoch
    os.utime(old, ns=(zero, zero))
    with Engine.open(tmp_path / "idx", mode="write") as eng:
        rep = eng.update([corpus])
        assert rep.new == 4 and rep.errors == 0
        assert eng.update([corpus]).unchanged == 4  # the clamped time compares equal next scan
        assert eng.search("ancient parchment").paths[0] == "a/ancient.txt"


def test_qa_h3_dates_before_1970_format_safely():
    from contextfs.engine.timeev import local_datetime

    for utc in (-1, -86400 * 365 * 50, -11644473600, -(10**13), 10**13):
        assert local_datetime(utc).year >= 1  # never raises


def test_qa_h4_a_users_stop_is_not_a_strike(tmp_path):
    corpus = _corpus(tmp_path / "c")
    idx = tmp_path / "idx"
    st = (corpus / "a" / "recipe.txt").stat()
    record = {"root_id": 1, "rel_path": "a/recipe.txt", "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    with Engine.open(idx, mode="write") as eng:
        eng.update([corpus])
    for _ in range(3):  # three closes in the same file
        (idx / guard.InProgress.NAME).write_text(json.dumps(record), encoding="utf-8")
        guard.note_user_stop(idx)
        with Engine.open(idx, mode="write") as eng:
            assert eng.update([corpus]).struck is None
    with Engine.open(idx) as eng:
        assert eng.conn.execute("SELECT COUNT(*) FROM strikes").fetchone()[0] == 0


def test_qa_m1_a_locked_database_is_not_treated_as_damaged(tmp_path, monkeypatch):
    import contextfs.engine.schema as schema

    idx = tmp_path / "idx"
    with Engine.open(idx, mode="write") as eng:
        eng.update([_corpus(tmp_path / "c")])

    def busy(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(schema, "connect", busy)
    with pytest.raises(sqlite3.OperationalError):
        Engine.open(idx, mode="write")
    monkeypatch.undo()
    assert not list(idx.glob("damaged-*"))
    with Engine.open(idx) as eng:
        assert eng.status()["files"] == 3  # the healthy index is untouched


@pytest.mark.skipif(sys.platform != "win32", reason="Windows share modes")
def test_qa_m3_a_file_that_stays_locked_is_not_re_embedded(tmp_path):
    import ctypes
    from ctypes import wintypes

    corpus = _corpus(tmp_path / "c")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    handle = k32.CreateFileW(str(corpus / "a" / "notes.md"), 0x80000000, 0, None, 3, 0x80, None)
    try:
        with Engine.open(tmp_path / "idx", mode="write") as eng:
            eng.update([corpus])
            size = (tmp_path / "idx" / "vectors" / "chunk.f32").stat().st_size
            for _ in range(3):
                rep = eng.update([corpus])
                assert rep.changed == 0 and rep.unchanged == 3
            assert (tmp_path / "idx" / "vectors" / "chunk.f32").stat().st_size == size
    finally:
        k32.CloseHandle(wintypes.HANDLE(handle))


def test_qa_m4_an_unknown_date_never_claims_a_match():
    from contextfs.engine.rank import _reason

    ref = {"event": "authored", "confidence": "unknown", "lo_utc": 631152000, "hi_utc": 1793000000,
           "point_utc": 1793000000, "cue": "last year", "cue_lo": 1735689600, "cue_hi": 1767225599}
    text = _reason("time", None, ref, None)
    assert "unknown" in text and "matches" not in text and "not ruled out" in text


def test_qa_m6_a_root_inside_another_root_is_not_indexed_twice(tmp_path):
    corpus = _corpus(tmp_path / "c")
    with Engine.open(tmp_path / "idx", mode="write") as eng:
        rep = eng.update([corpus, corpus / "a"])
        assert rep.nested_roots == [str(corpus / "a")] and rep.new == 3
        assert eng.search("biryani saffron").paths.count("a/recipe.txt") == 1
