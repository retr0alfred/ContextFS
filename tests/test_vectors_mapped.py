"""The indexer's mapped, per-item vector read gives exactly what ``load`` gives (log.md Phase 36)."""

from __future__ import annotations

import numpy as np
import pytest

from contextfs.engine import Engine, default_model_dir
from contextfs.engine.vectors import VectorFiles

pytestmark = pytest.mark.skipif(
    not (default_model_dir() / "model_fp32.onnx").exists(), reason="ONNX model not exported"
)


def _same(index) -> None:
    with Engine.open(index) as eng:
        vf = VectorFiles(index / "vectors")
        mat, items, _ = vf.load(eng.conn, "doc")
        expected = {int(i): mat[k] for k, i in enumerate(items)}  # what rebuild_sessions used to build
        rows, total = vf.item_rows(eng.conn, "doc")
        data = vf.mapped("doc", total)
        got = {i: np.array(data[r]) for i, r in rows.items()}
        del data
    assert got.keys() == expected.keys() and len(got) > 0
    for i, v in expected.items():
        assert got[i].tobytes() == v.tobytes()  # bit for bit


def test_mapped_rows_equal_load_including_after_edits(tmp_path):
    corpus = tmp_path / "c"
    for i in range(12):
        p = corpus / f"f{i % 3}" / f"note_{i}.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"note {i} about topic {i % 4} and more words", encoding="utf-8")
    index = tmp_path / "index"
    with Engine.open(index, mode="write") as eng:
        eng.update([corpus])
    _same(index)
    (corpus / "f0" / "note_0.txt").write_text("rewritten: completely different text", encoding="utf-8")
    (corpus / "f1" / "note_1.txt").unlink()
    with Engine.open(index, mode="write") as eng:
        eng.update([corpus])  # replaced and invalidated rows: the last valid row must win
    _same(index)
