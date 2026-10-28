"""Engine: indexing, incremental updates, deletions, offline roots, locks, search robustness."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

from contextfs.engine import Engine, default_model_dir
from contextfs.engine.indexer import UpdateReport

pytestmark = pytest.mark.skipif(
    not (default_model_dir() / "model_fp32.onnx").exists(), reason="ONNX model not exported"
)
AS_OF = datetime(2026, 9, 30, 21).astimezone()


def _corpus(root: Path) -> Path:
    (root / "College" / "DBMS").mkdir(parents=True)
    (root / "College" / "DBMS" / "normalization_notes.md").write_text(
        "# Normalization\nBCNF and third normal form. Deadline: 24 November 2025.\n",
        encoding="utf-8",
    )
    (root / "Personal").mkdir()
    (root / "Personal" / "recipe_biryani.txt").write_text(
        "Biryani: rice, saffron, onions.", encoding="utf-8"
    )
    (root / "Personal" / "budget.csv").write_text("month,rent\nmarch,9000\n", encoding="utf-8")
    return root


@pytest.fixture()
def indexed(tmp_path):
    corpus = _corpus(tmp_path / "corpus")
    eng = Engine.open(tmp_path / "index", mode="write")
    rep = eng.update([corpus])
    yield eng, corpus, rep
    eng.close()


def test_first_update_indexes_everything(indexed):
    eng, _, rep = indexed
    assert isinstance(rep, UpdateReport)
    assert rep.new == 3 and rep.errors == 0
    assert eng.status()["files"] == 3


def test_second_update_is_a_no_op(indexed):
    eng, corpus, _ = indexed
    rep = eng.update([corpus])
    assert (rep.new, rep.changed, rep.unchanged, rep.deleted) == (0, 0, 3, 0)


def test_change_add_delete_are_detected(indexed):
    eng, corpus, _ = indexed
    (corpus / "Personal" / "recipe_biryani.txt").write_text(
        "Biryani v2 with mint", encoding="utf-8"
    )
    (corpus / "Personal" / "new_note.md").write_text("fresh note about cricket", encoding="utf-8")
    (corpus / "Personal" / "budget.csv").unlink()
    rep = eng.update([corpus])
    assert (rep.new, rep.changed, rep.deleted) == (1, 1, 1)
    paths = eng.search("cricket", as_of=AS_OF).paths
    assert paths[0] == "Personal/new_note.md"
    assert "Personal/budget.csv" not in eng.search("rent march budget", as_of=AS_OF).paths


def test_missing_root_goes_offline_and_is_not_purged(indexed, tmp_path):
    eng, corpus, _ = indexed
    moved = tmp_path / "unplugged"
    os.rename(corpus, moved)
    rep = eng.update([corpus])
    assert rep.offline_roots == [str(corpus)]
    assert eng.status()["offline"] == 3 and eng.status()["files"] == 0
    os.rename(moved, corpus)
    rep = eng.update([corpus])
    assert eng.status()["files"] == 3 and rep.deleted == 0


def test_cancel_mid_walk_keeps_unreached_files_and_resumes(indexed):
    eng, corpus, _ = indexed
    (corpus / "Personal" / "recipe_biryani.txt").write_text("changed", encoding="utf-8")
    calls = [0]

    def cancel_after_first_check() -> bool:
        calls[0] += 1
        return calls[0] > 1

    rep = eng.update([corpus], cancel=cancel_after_first_check)
    assert rep.cancelled and rep.deleted == 0
    assert eng.status()["files"] == 3
    rep = eng.update([corpus])
    assert not rep.cancelled and rep.changed == 1 and rep.deleted == 0


def _undated(eng) -> int:
    return eng.conn.execute(
        "SELECT COUNT(*) FROM files f WHERE status='present' "
        "AND NOT EXISTS (SELECT 1 FROM file_time t WHERE t.file_id=f.file_id)"
    ).fetchone()[0]


def test_a_cancelled_first_scan_still_dates_every_committed_file(tmp_path):
    # Real-data demo (log.md Phase 33): every scan of a 20 GB folder was cancelled by closing the
    # app, and per-event dates were only computed by completed scans, so no file had a date.
    corpus = _corpus(tmp_path / "corpus")
    calls = [0]

    def cancel_after_two() -> bool:
        calls[0] += 1
        return calls[0] > 3

    with Engine.open(tmp_path / "index", mode="write") as eng:
        rep = eng.update([corpus], cancel=cancel_after_two)
        assert rep.cancelled and 0 < eng.status()["files"] < 3
        assert _undated(eng) == 0


def test_an_index_without_dates_heals_on_the_next_update(indexed):
    eng, corpus, _ = indexed
    eng.conn.execute("DELETE FROM file_time")
    eng.conn.commit()
    rep = eng.update([corpus])
    assert rep.healed_folders > 0 and _undated(eng) == 0


def test_sessions_wait_for_a_completed_scan_then_rebuild(indexed):
    eng, corpus, _ = indexed
    eng.conn.execute("DELETE FROM sessions")
    eng.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('sessions_stale', '1')")
    eng.conn.commit()
    eng.update([corpus], cancel=lambda: True)
    assert eng.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    eng.update([corpus])
    assert eng.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] > 0


def test_a_folder_removed_from_settings_leaves_search_and_comes_back(indexed, tmp_path):
    # Real-data demo: a test folder removed in Settings still showed up in results.
    eng, corpus, _ = indexed
    other = tmp_path / "other"
    other.mkdir()
    (other / "cricket.md").write_text("club cricket scores", encoding="utf-8")
    eng.update([corpus, other], prune_other_roots=True)
    assert "cricket.md" in eng.search("cricket scores", as_of=AS_OF).paths
    rep = eng.update([corpus], prune_other_roots=True)
    assert rep.removed_roots == [str(other)]
    assert "cricket.md" not in eng.search("cricket scores", as_of=AS_OF).paths
    assert eng.status()["files"] == 3
    # same folder written differently (separators, case) is the same root, restored instantly
    rep = eng.update([corpus, str(other).upper().replace("\\", "/")], prune_other_roots=True)
    assert rep.new == 0 and "cricket.md" in eng.search("cricket scores", as_of=AS_OF).paths


def test_explicit_roots_without_prune_leave_other_roots_alone(indexed, tmp_path):
    eng, corpus, _ = indexed
    other = tmp_path / "other"
    other.mkdir()
    (other / "cricket.md").write_text("club cricket scores", encoding="utf-8")
    eng.update([other])
    assert eng.status()["files"] == 4


def test_every_file_gets_per_event_times_from_every_estimator(indexed):
    eng, _, _ = indexed
    rows = eng.conn.execute(
        "SELECT estimator, event, COUNT(*) FROM file_time GROUP BY estimator, event"
    ).fetchall()
    got = {(r[0], r[1]): r[2] for r in rows}
    for est in ("raw_mtime", "rules_v0"):
        for ev in ("authored", "arrived_here", "last_user_edit"):
            assert got.get((est, ev)) == 3


def test_second_writer_is_refused_but_readers_work(indexed, tmp_path):
    from contextfs.engine import WriterLocked

    with pytest.raises(WriterLocked):
        Engine.open(tmp_path / "index", mode="write")
    with Engine.open(tmp_path / "index") as reader:
        assert (
            reader.search("biryani saffron", as_of=AS_OF).paths[0] == "Personal/recipe_biryani.txt"
        )


@pytest.mark.parametrize(
    "query", ["", "   ", "x" * 5000, "நன்றி मेरा", "???!!", "SELECT * FROM files; --", '"unbalanced']
)
def test_any_query_returns_results_or_a_clear_note(indexed, query):
    eng, _, _ = indexed
    resp = eng.search(query, as_of=AS_OF)
    assert resp.results or resp.notes
    for r in resp.results:
        assert r.reasons, "every result must explain itself"


def test_explanations_cite_only_what_the_file_contains(indexed):
    eng, _, _ = indexed
    resp = eng.search("notes about BCNF normalization", as_of=AS_OF)
    top = resp.results[0]
    assert top.rel_path.endswith("normalization_notes.md")
    for term in top.doc_refs.get("bm25", {}).get("terms_in_evidence", []):
        assert term in Path(top.path).read_text(encoding="utf-8").lower()


def test_engine_never_imports_ui_cli_or_benchmark_code():
    import subprocess

    code = (
        "import sys; import contextfs.engine, contextfs.engine.rank, contextfs.engine.indexer; "
        "bad=[m for m in sys.modules if m.startswith(('PySide6','typer','contextfs.benchgen','contextfs.cli'))]; "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
