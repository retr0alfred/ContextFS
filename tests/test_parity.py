"""Parity (ADR-0010): the GUI, the CLI and the evaluation harness rank with the *same* engine.

1. GUI search job == CLI ``find --json`` == in-process ``Engine.search`` (same paths and scores).
2. Thread environments do not change results (the CLI under OMP/MKL = 1 vs 8; engine threads 1 vs 4).
3. The harness's offline re-scoring of a kept pool reproduces the online ranking.
4. Two independent builds of the same corpus give the same logical index.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from contextfs.engine import Engine, default_model_dir
from contextfs.engine.rank import SearchConfig

pytestmark = pytest.mark.skipif(
    not (default_model_dir() / "model_fp32.onnx").exists(), reason="ONNX model not exported"
)
AS_OF = datetime(2026, 9, 30, 21).astimezone()
REPO = Path(__file__).resolve().parents[1]
QUERIES = ["notes on BCNF decomposition", "biryani recipe", "the spreadsheet with rent", "cricket"]


def _corpus(root: Path) -> Path:
    files = {
        "College/DBMS/normalization_notes.md": "# Normalization\nBCNF and 3NF decomposition. Deadline 24 November 2025.",
        "College/DBMS/er_diagrams.md": "Entity relationship diagrams, cardinality and keys.",
        "College/OS/scheduling.txt": "Round robin and shortest job first CPU scheduling.",
        "Personal/recipe_biryani.txt": "Biryani: rice, saffron, onions, mint.",
        "Personal/budget.csv": "month,rent\nmarch,9000\napril,9000\n",
        "Personal/cricket_scores.md": "Club cricket scores from the weekend league.",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    base = tmp_path_factory.mktemp("parity")
    corpus = _corpus(base / "corpus")
    with Engine.open(base / "index", mode="write") as eng:
        eng.update([corpus])
    return base, corpus


def _ranked(resp, k: int = 10) -> list[tuple[str, float]]:
    return [(r.rel_path, round(r.score, 6)) for r in resp.results[:k]]


def _cli_find(index: Path, query: str, threads: str) -> dict:
    env = dict(os.environ, PYTHONUTF8="1", OMP_NUM_THREADS=threads, MKL_NUM_THREADS=threads,
               OPENBLAS_NUM_THREADS=threads)
    out = subprocess.run(
        [sys.executable, "-m", "contextfs", "find", query, "--index-dir", str(index), "--json",
         "--as-of", AS_OF.isoformat()],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(REPO), timeout=300, check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_cli_matches_in_process_engine_under_two_thread_environments(built):
    base, _ = built
    with Engine.open(base / "index") as eng:
        expected = {q: _ranked(eng.search(q, SearchConfig(), as_of=AS_OF)) for q in QUERIES}
        cfg_hash = eng.search(QUERIES[0], SearchConfig(), as_of=AS_OF).config_hash
    for threads in ("1", "8"):
        for q in QUERIES[:2]:
            got = _cli_find(base / "index", q, threads)
            assert got["config_hash"] == cfg_hash
            assert [(r["rel_path"], round(r["score"], 6)) for r in got["results"]] == expected[q], (threads, q)


def test_engine_thread_count_does_not_change_rankings(built):
    base, _ = built
    with Engine.open(base / "index", threads=1) as one, Engine.open(base / "index", threads=4) as four:
        for q in QUERIES:
            a, b = one.search(q, as_of=AS_OF), four.search(q, as_of=AS_OF)
            assert [r.rel_path for r in a.results] == [r.rel_path for r in b.results]
            assert np.allclose([r.score for r in a.results], [r.score for r in b.results], atol=1e-5)


def test_gui_search_job_matches_engine(built):
    pytest.importorskip("PySide6")
    from contextfs.app.gui import SearchJob

    base, _ = built
    got = {}
    for i, q in enumerate(QUERIES):
        job = SearchJob(base / "index", q, (), i, reopen=True)
        job.signals.done.connect(lambda payload, seq: got.__setitem__(seq, payload[0]))
        job.run()  # synchronously, on this thread
    SearchJob.engine.close()
    SearchJob.engine = None
    with Engine.open(base / "index") as eng:
        for i, q in enumerate(QUERIES):
            # the GUI reads relative time from "now"; these queries carry no time cue
            assert not got[i].frame["time"]
            assert _ranked(got[i]) == _ranked(eng.search(q, SearchConfig(), as_of=AS_OF)), q


def test_harness_offline_rescoring_reproduces_online_ranking(built):
    spec = importlib.util.spec_from_file_location("engine_eval", REPO / "scripts" / "engine_eval.py")
    engine_eval = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine_eval)
    base, _ = built
    weights = dict(SearchConfig().weights)
    with Engine.open(base / "index") as eng:
        for q in QUERIES:
            resp = eng.search(q, engine_eval.config_for("rules_v0", weights.items()), as_of=AS_OF)
            assert engine_eval.offline_rank(resp.pool, weights)[:10] == resp.paths[:10], q


def _logical(index: Path) -> dict:
    from contextfs.engine.vectors import VectorFiles

    with Engine.open(index) as eng:
        c = eng.conn
        path = {r[0]: r[1] for r in c.execute("SELECT file_id, rel_path FROM files")}
        sessions: dict[str, set] = {}
        for fid, sid in c.execute("SELECT file_id, session_id FROM sessions"):
            sessions.setdefault(sid, set()).add(path[fid])
        mat, _, fids = VectorFiles(index / "vectors").load(c, "doc")
        return {
            "files": sorted(tuple(r) for r in c.execute("SELECT rel_path, size, content_hash, status FROM files")),
            "chunks": sorted((path[f], o, t) for f, o, t in c.execute("SELECT file_id, ord, text FROM chunks")),
            "file_time": sorted(
                (path[r[0]], *r[1:])
                for r in c.execute("SELECT file_id, estimator, event, lo_utc, hi_utc, point_utc, confidence "
                                   "FROM file_time")),
            "sessions": sorted(sorted(s) for s in sessions.values()),
            "vectors": {path[int(f)]: row for f, row in zip(fids, mat, strict=True)},
        }


def test_two_independent_builds_give_the_same_logical_index(built, tmp_path):
    base, corpus = built
    with Engine.open(tmp_path / "index2", mode="write", threads=1) as eng:
        eng.update([corpus])
    a, b = _logical(base / "index"), _logical(tmp_path / "index2")
    for key in ("files", "chunks", "file_time", "sessions"):
        assert [tuple(x) for x in a[key]] == [tuple(x) for x in b[key]], key
    assert a["vectors"].keys() == b["vectors"].keys()
    for p in a["vectors"]:
        assert np.allclose(a["vectors"][p], b["vectors"][p], atol=1e-5), p
