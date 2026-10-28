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
