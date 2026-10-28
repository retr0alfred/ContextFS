"""Exact f32 vectors in append-only files, registered in SQLite (ADR-0005).

- ``vectors/chunk.f32``: one row per chunk (MaxP scoring, evidence chunk).
- ``vectors/doc.f32``: one row per document (pooled; used only to *generate* dense candidates).

Writers append and fsync the file, *then* insert the ``vectors`` rows in the same SQLite
transaction as the rest of the batch, so SQLite remains the single commit point. A crash between
the append and the commit leaves unreferenced tail rows, which readers ignore. Deletes set
``valid = 0``. Compaction is on the cut list, so the files only grow in the MVP.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

__all__ = ["VectorFiles", "DIM"]

DIM = 384


class VectorFiles:
    """Append-only float32 matrices plus SQLite bookkeeping."""

    def __init__(self, root: Path) -> None:
        """Bind to ``<index>/vectors``."""
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, kind: str) -> Path:
        return self.root / f"{kind}.f32"

    def rows(self, kind: str) -> int:
        """Rows physically present in the file (committed or not)."""
        p = self._path(kind)
        return p.stat().st_size // (4 * DIM) if p.exists() else 0

    def append(self, kind: str, matrix: np.ndarray) -> int:
        """Append rows; returns the first row number. Fsyncs before returning."""
        matrix = np.ascontiguousarray(matrix, dtype=np.float32)
        assert matrix.ndim == 2 and matrix.shape[1] == DIM
        p = self._path(kind)
        start = self.rows(kind)
        with open(p, "ab") as fh:
            # Trim a torn tail left by a crash mid-write, so rows stay aligned.
            if fh.tell() % (4 * DIM):
                fh.truncate(start * 4 * DIM)
            fh.write(matrix.tobytes())
            fh.flush()
            os.fsync(fh.fileno())
        return start

    def load(self, conn, kind: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(matrix, item_ids, file_ids) for the *valid, committed* rows of ``kind``."""
        rows = conn.execute(
            "SELECT item_id, file_id, row FROM vectors WHERE kind=? AND valid=1 ORDER BY row",
            (kind,),
        ).fetchall()
        if not rows:
            return np.zeros((0, DIM), np.float32), np.zeros(0, np.int64), np.zeros(0, np.int64)
        total = self.rows(kind)
        data = np.fromfile(self._path(kind), dtype=np.float32, count=total * DIM).reshape(
            total, DIM
        )
        rows = [r for r in rows if r["row"] < total]  # a truncated/lost file: those rows are gone
        if not rows:
            return np.zeros((0, DIM), np.float32), np.zeros(0, np.int64), np.zeros(0, np.int64)
        idx = np.asarray([r["row"] for r in rows], dtype=np.int64)
        return (
            data[idx],
            np.asarray([r["item_id"] for r in rows], dtype=np.int64),
            np.asarray([r["file_id"] for r in rows], dtype=np.int64),
        )
