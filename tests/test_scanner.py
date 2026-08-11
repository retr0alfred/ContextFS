"""Phase 4 tests: file discovery, change classification, and the read-only guarantee.

The read-only tests are the important ones. ContextFS's foundational promise is
that it never modifies a scanned file; a promise that is only checked by reading
the code is not a promise, so it is checked by fingerprinting the corpus before
and after every operation.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import pytest

from contextfs.config import load_config
from contextfs.datagen.generate import generate_corpus
from contextfs.scanner import Scanner, hash_file
from contextfs.store import SCHEMA_VERSION, Store

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> Path:
    """A freshly generated synthetic corpus, shared across this module."""
    root = tmp_path_factory.mktemp("scan") / "corpus"
    generate_corpus(root, clean=True)
    return root


@pytest.fixture
def cfg(corpus, tmp_path):
    """A config pointing at the shared corpus with a per-test data directory."""
    config_file = tmp_path / "contextfs.toml"
    config_file.write_text(
        f'[paths]\nroot = "{corpus.as_posix()}"\ndata_dir = "derived"\n'
        '[scan]\nignore_dirs = [".git", "__pycache__"]\n'
        'ignore_globs = ["*.tmp", "~$*"]\n',
        encoding="utf-8",
    )
    return load_config(config_file)


@pytest.fixture
def store(cfg):
    """An open store for the test's own data directory."""
    cfg.ensure_data_dir()
    with Store(cfg.db_path) as handle:
        yield handle


def fingerprint_tree(root: Path) -> dict[str, tuple[str, int, int]]:
    """Map every file under ``root`` to ``(sha256, size, mtime_ns)``.

    SHA-256 is used here rather than the scanner's xxhash: the test must not
    share a hash implementation with the code it is auditing, or a bug in that
    implementation would be invisible to the audit.
    """
    out: dict[str, tuple[str, int, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            out[path.relative_to(root).as_posix()] = (digest, stat.st_size, stat.st_mtime_ns)
    return out


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def test_store_creates_and_migrates(tmp_path):
    with Store(tmp_path / "d" / "contextfs.db") as store:
        assert store.schema_version == SCHEMA_VERSION
        assert store.path.is_file()


def test_migrations_are_idempotent(tmp_path):
    db = tmp_path / "contextfs.db"
    with Store(db) as store:
        first = store.migrate()
    with Store(db) as store:
        assert store.migrate() == first == SCHEMA_VERSION


def test_meta_roundtrip(tmp_path):
    with Store(tmp_path / "contextfs.db") as store:
        assert store.get_meta("nothing") is None
        store.set_meta("k", "v1")
        store.set_meta("k", "v2")
        assert store.get_meta("k") == "v2"


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def test_hash_is_stable_and_content_sensitive(tmp_path):
    a = tmp_path / "a.txt"
    a.write_bytes(b"hello world")
    first, size = hash_file(a)
    assert size == 11
    assert hash_file(a)[0] == first

    b = tmp_path / "b.txt"
    b.write_bytes(b"hello worlD")
    assert hash_file(b)[0] != first


def test_hash_handles_a_file_larger_than_one_chunk(tmp_path):
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (3 * 1024 * 1024))
    digest, size = hash_file(big)
    assert size == 3 * 1024 * 1024
    assert len(digest) == 32  # 128-bit hex


# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------


def test_walk_finds_every_corpus_file(cfg, corpus):
    found = {p.relative_to(corpus).as_posix() for p in Scanner(cfg).walk()}
    on_disk = {p.relative_to(corpus).as_posix() for p in corpus.rglob("*") if p.is_file()}
    assert found == on_disk
    assert len(found) == 40


def test_ignored_directories_are_never_descended(cfg, corpus):
    junk = corpus / "__pycache__"
    junk.mkdir()
    (junk / "cached.pyc").write_bytes(b"junk")
    try:
        found = {p.name for p in Scanner(cfg).walk()}
        assert "cached.pyc" not in found
    finally:
        (junk / "cached.pyc").unlink()
        junk.rmdir()


def test_ignored_globs_are_skipped(cfg, corpus):
    temp = corpus / "scratch.tmp"
    lock = corpus / "~$Report.docx"
    temp.write_text("junk", encoding="utf-8")
    lock.write_text("junk", encoding="utf-8")
    try:
        found = {p.name for p in Scanner(cfg).walk()}
        assert "scratch.tmp" not in found
        assert "~$Report.docx" not in found
    finally:
        temp.unlink()
        lock.unlink()


def test_walk_on_a_missing_root_yields_nothing(tmp_path):
    config_file = tmp_path / "contextfs.toml"
    config_file.write_text('[paths]\nroot = "does_not_exist"\n', encoding="utf-8")
    assert list(Scanner(load_config(config_file)).walk()) == []


def test_records_carry_useful_structure(cfg, store):
    result = Scanner(cfg).scan(store)
    by_path = {r.path: r for r in result.new}
    record = by_path["College/Semester7/MachineLearning/Unit3_SVM_Notes.md"]
    assert record.ext == ".md"
    assert record.stem == "Unit3_SVM_Notes"
    assert record.folder == "College/Semester7/MachineLearning"
    assert record.depth == 3
    assert record.size > 0
    assert record.content_hash


# ---------------------------------------------------------------------------
# Classification: new / modified / unchanged / deleted
# ---------------------------------------------------------------------------


def test_first_scan_classifies_everything_as_new(cfg, store):
    result = Scanner(cfg).scan(store)
    assert len(result.new) == 40
    assert result.modified == []
    assert result.unchanged == []
    assert result.deleted == []
    assert result.files_hashed == 40


def test_second_scan_classifies_everything_as_unchanged(cfg, store):
    scanner = Scanner(cfg)
    scanner.scan(store)
    result = scanner.scan(store)
    assert len(result.unchanged) == 40
    assert result.new == []
    assert result.modified == []
    assert result.deleted == []


def test_unchanged_files_are_not_hashed_on_a_second_scan(cfg, store):
    """The whole point of the cheap tier: no I/O for files that did not move."""
    scanner = Scanner(cfg)
    scanner.scan(store)
    result = scanner.scan(store)
    assert result.files_hashed == 0
    assert result.bytes_hashed == 0


def test_modified_file_is_detected(cfg, store, corpus):
    scanner = Scanner(cfg)
    scanner.scan(store)

    target = corpus / "Personal" / "Misc" / "recipe_biryani.txt"
    original = target.read_text(encoding="utf-8")
    original_stat = target.stat()
    try:
        time.sleep(0.01)
        target.write_text(original + "\nPS: use more ghee.\n", encoding="utf-8")
        result = scanner.scan(store)
        assert [r.path for r in result.modified] == ["Personal/Misc/recipe_biryani.txt"]
        assert len(result.unchanged) == 39
        assert result.files_hashed == 1
    finally:
        target.write_text(original, encoding="utf-8")
        os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))


def test_new_file_is_detected(cfg, store, corpus):
    scanner = Scanner(cfg)
    scanner.scan(store)

    added = corpus / "Personal" / "Misc" / "new_note.txt"
    added.write_text("something new", encoding="utf-8")
    try:
        result = scanner.scan(store)
        assert [r.path for r in result.new] == ["Personal/Misc/new_note.txt"]
        assert len(result.unchanged) == 40
    finally:
        added.unlink()


def test_deleted_file_is_detected_and_tombstoned(cfg, store, corpus):
    scanner = Scanner(cfg)
    scanner.scan(store)

    target = corpus / "Downloads" / "wifi_setup_instructions.txt"
    content = target.read_bytes()
    stat = target.stat()
    target.unlink()
    try:
        result = scanner.scan(store)
        assert result.deleted == ["Downloads/wifi_setup_instructions.txt"]
        assert len(result.unchanged) == 39

        row = store.get_file("Downloads/wifi_setup_instructions.txt")
        assert row is not None, "deleted files must be tombstoned, not dropped"
        assert row["status"] == "deleted"
        assert store.file_count() == 39
        assert store.file_count(include_deleted=True) == 40
    finally:
        target.write_bytes(content)
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def test_restored_file_is_revived_not_duplicated(cfg, store, corpus):
    scanner = Scanner(cfg)
    scanner.scan(store)
    target = corpus / "Downloads" / "wifi_setup_instructions.txt"
    content = target.read_bytes()
    stat = target.stat()

    target.unlink()
    scanner.scan(store)
    target.write_bytes(content)
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    result = scanner.scan(store)

    assert store.file_count() == 40
    assert store.file_count(include_deleted=True) == 40
    row = store.get_file("Downloads/wifi_setup_instructions.txt")
    assert row["status"] == "present"
    assert result.deleted == []


def test_touched_but_unchanged_file_is_not_reported_as_modified(cfg, store, corpus):
    """A timestamp bump with identical bytes must not trigger a reindex."""
    scanner = Scanner(cfg)
    scanner.scan(store)

    target = corpus / "Personal" / "Misc" / "birthday_list.txt"
    stat = target.stat()
    try:
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        result = scanner.scan(store)
        assert result.modified == [], "content-identical file was reported as modified"
        assert len(result.unchanged) == 40
        assert result.files_hashed == 1, "the file should be hashed once to prove equality"
    finally:
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def test_full_rescan_marks_everything_modified(cfg, store):
    scanner = Scanner(cfg)
    scanner.scan(store)
    result = scanner.scan(store, full=True)
    assert len(result.modified) == 40
    assert result.unchanged == []
    assert result.files_hashed == 40


def test_rehash_reads_every_file_but_still_reports_unchanged(cfg, store):
    scanner = Scanner(cfg)
    scanner.scan(store)
    result = scanner.scan(store, rehash=True)
    assert result.files_hashed == 40
    assert len(result.unchanged) == 40
    assert result.modified == []


def test_dry_run_writes_nothing(cfg, store, corpus):
    scanner = Scanner(cfg)
    result = scanner.scan(store, dry_run=True)
    assert len(result.new) == 40
    assert store.file_count() == 0, "a dry run must not populate the index"

    real = scanner.scan(store)
    assert len(real.new) == 40


# ---------------------------------------------------------------------------
# THE read-only guarantee
# ---------------------------------------------------------------------------


def test_scanning_does_not_modify_any_file(cfg, store, corpus):
    """The foundational promise, checked byte-for-byte with an independent hash."""
    before = fingerprint_tree(corpus)
    assert len(before) == 40

    scanner = Scanner(cfg)
    scanner.scan(store)
    scanner.scan(store)
    scanner.scan(store, full=True)
    scanner.scan(store, rehash=True)
    scanner.scan(store, dry_run=True)

    after = fingerprint_tree(corpus)
    assert after == before, "scanning altered content, size, or mtime of a scanned file"


def test_scanning_creates_no_files_under_the_root(cfg, store, corpus):
    before = set(corpus.rglob("*"))
    Scanner(cfg).scan(store)
    assert set(corpus.rglob("*")) == before


def test_all_derived_data_lives_under_the_data_dir(cfg, store, corpus):
    Scanner(cfg).scan(store)
    assert cfg.db_path.is_file()
    assert cfg.paths.data_dir in cfg.db_path.parents
    assert cfg.paths.data_dir not in corpus.parents
    assert not (corpus / ".contextfs").exists()


# ---------------------------------------------------------------------------
# Persistence and scan history
# ---------------------------------------------------------------------------


def test_scan_run_is_recorded_with_real_statistics(cfg, store):
    Scanner(cfg).scan(store)
    run = store.last_scan()
    assert run is not None
    assert run["files_seen"] == 40
    assert run["count_new"] == 40
    assert run["duration_ms"] > 0
    assert run["finished_at"]


def test_dry_runs_do_not_become_the_last_scan(cfg, store):
    scanner = Scanner(cfg)
    scanner.scan(store)
    scanner.scan(store, dry_run=True)
    assert store.last_scan()["dry_run"] == 0


def test_extension_counts_match_the_corpus(cfg, store):
    Scanner(cfg).scan(store)
    counts = store.counts_by_extension()
    assert counts[".md"] == 10
    assert counts[".xlsx"] == 6
    assert counts[".pdf"] == 4
    assert counts[".docx"] == 5
    assert counts[".pptx"] == 2
    assert sum(counts.values()) == 40


def test_content_changed_at_tracks_content_not_observation(cfg, store, corpus):
    scanner = Scanner(cfg)
    scanner.scan(store)
    first = store.get_file("Personal/Misc/recipe_biryani.txt")["content_changed_at"]

    scanner.scan(store)
    assert store.get_file("Personal/Misc/recipe_biryani.txt")["content_changed_at"] == first

    target = corpus / "Personal" / "Misc" / "recipe_biryani.txt"
    original = target.read_bytes()
    stat = target.stat()
    try:
        time.sleep(1.05)  # utc_now() has one-second resolution
        target.write_text("different", encoding="utf-8")
        scanner.scan(store)
        assert store.get_file("Personal/Misc/recipe_biryani.txt")["content_changed_at"] != first
    finally:
        target.write_bytes(original)
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def test_errors_are_recorded_rather_than_raised(cfg, store, tmp_path):
    """A missing root is reported, not thrown."""
    config_file = tmp_path / "contextfs.toml"
    config_file.write_text('[paths]\nroot = "nope"\ndata_dir = "d"\n', encoding="utf-8")
    bad = load_config(config_file)
    result = Scanner(bad).scan(store)
    assert result.errors
    assert result.seen == 0


def test_oversized_files_are_inventoried_but_not_hashed(corpus, tmp_path):
    config_file = tmp_path / "contextfs.toml"
    config_file.write_text(
        f'[paths]\nroot = "{corpus.as_posix()}"\ndata_dir = "d"\n'
        "[scan]\nmax_file_size_mb = 0.004\n",
        encoding="utf-8",
    )
    cfg_small = load_config(config_file)
    cfg_small.ensure_data_dir()
    with Store(cfg_small.db_path) as small_store:
        result = Scanner(cfg_small).scan(small_store)
        assert result.skipped_too_large > 0
        assert result.seen == 40, "oversized files must still be inventoried"
        assert result.files_hashed < 40


def test_touched_fraction_is_the_incrementality_metric(cfg, store, corpus):
    scanner = Scanner(cfg)
    first = scanner.scan(store)
    assert first.touched_fraction == 1.0

    second = scanner.scan(store)
    assert second.touched_fraction == 0.0

    target = corpus / "Personal" / "Misc" / "movie_watchlist.txt"
    original = target.read_bytes()
    stat = target.stat()
    try:
        target.write_text("changed", encoding="utf-8")
        third = scanner.scan(store)
        assert third.touched_fraction == pytest.approx(1 / 40)
    finally:
        target.write_bytes(original)
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
