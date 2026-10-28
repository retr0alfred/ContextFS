"""fsio: whole-drive realities (ADR-0011). Windows-only behaviours are skipped elsewhere."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys

import pytest

from contextfs.engine import fsio

win = pytest.mark.skipif(sys.platform != "win32", reason="Windows filesystem semantics")


def _files(root, **kw):
    stats = fsio.WalkStats()
    entries = list(fsio.walk(root, 1, stats=stats, **kw))
    return {e.rel_path: e for e in entries}, stats


def test_walk_lists_files_with_relative_paths(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "x.txt").write_text("x")
    (tmp_path / "top.md").write_text("t")
    files, stats = _files(tmp_path)
    assert set(files) == {"a/b/x.txt", "top.md"}
    assert stats.errors == []


def test_excluded_names_and_self_exclusion(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("j")
    (tmp_path / "index").mkdir()
    (tmp_path / "index" / "contextfs.db").write_text("db")
    (tmp_path / "keep.txt").write_text("k")
    files, _ = _files(tmp_path, self_exclude=[tmp_path / "index"])
    assert set(files) == {"keep.txt"}


@win
def test_junction_loop_is_not_followed(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.txt").write_text("x")
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(tmp_path / "a" / "loop"), str(tmp_path)],
        check=True,
        capture_output=True,
    )
    files, stats = _files(tmp_path)
    assert set(files) == {"a/x.txt"}
    assert any(e.kind == "reparse_skipped" for e in stats.errors)


@win
def test_paths_longer_than_260_chars(tmp_path):
    deep = fsio.long_path(tmp_path) + "\\" + "\\".join(["d" * 50] * 6)
    os.makedirs(deep)
    with open(deep + "\\long.txt", "w", encoding="utf-8") as fh:
        fh.write("long")
    files, stats = _files(tmp_path)
    (rel,) = files
    assert len(fsio.plain_path(files[rel].path)) > 260
    assert fsio.read_bytes(files[rel]) == b"long"
    assert stats.errors == []


@win
def test_exclusively_locked_file_is_reported_not_crashed(tmp_path):
    target = tmp_path / "locked.txt"
    target.write_text("secret")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = ctypes.c_void_p
    handle = k32.CreateFileW(str(target), 0x80000000, 0, None, 3, 0, None)  # share mode 0
    try:
        files, _ = _files(tmp_path)
        with pytest.raises(fsio.ReadError) as err:
            fsio.read_bytes(files["locked.txt"])
        assert err.value.kind == "locked"
    finally:
        k32.CloseHandle(ctypes.c_void_p(handle))


@win
def test_reading_does_not_block_rename_replace_saves(tmp_path):
    """Word saves by writing a temp file and renaming it over the original."""
    target = tmp_path / "doc.txt"
    target.write_text("v0")
    files, _ = _files(tmp_path)
    entry = files["doc.txt"]
    for i in range(50):
        data = fsio.read_bytes(entry) if entry.size == target.stat().st_size else b""
        tmp = tmp_path / f"~tmp{i}"
        tmp.write_text(f"v{i + 1}")
        os.replace(tmp, target)  # must never raise a sharing violation
        entry.size = target.stat().st_size
        entry.mtime_ns = target.stat().st_mtime_ns
        del data
    assert target.read_text().startswith("v")


@win
def test_offline_placeholder_is_never_opened(tmp_path):
    target = tmp_path / "cloud.pdf"
    target.write_bytes(b"%PDF-1.4")
    subprocess.run(["attrib", "+O", str(target)], check=True, capture_output=True)
    try:
        files, stats = _files(tmp_path)
        entry = files["cloud.pdf"]
        assert entry.placeholder and stats.placeholders == 1
        with pytest.raises(fsio.ReadError) as err:
            fsio.read_bytes(entry)
        assert err.value.kind == "placeholder"
    finally:
        subprocess.run(["attrib", "-O", str(target)], capture_output=True)


def test_missing_root_yields_error_not_exception(tmp_path):
    files, stats = _files(tmp_path / "unplugged-drive")
    assert files == {}
    assert stats.errors and stats.errors[0].kind == "missing"
