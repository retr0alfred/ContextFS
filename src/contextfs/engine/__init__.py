"""ContextFS v2 engine: the one library behind the GUI, the CLI and the evaluation (ADR-0010).

Contracts are in ``docs/engine/DESIGN.md``. This module never imports Qt, Typer, the benchmark
generator or any truth file.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = ["Engine", "WriterLocked", "default_index_dir", "default_model_dir"]


def default_index_dir() -> Path:
    r"""``%LOCALAPPDATA%\\ContextFS\\index`` unless ``CONTEXTFS_INDEX_DIR`` overrides it."""
    env = os.environ.get("CONTEXTFS_INDEX_DIR")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "ContextFS" / "index"


def default_model_dir() -> Path:
    """The bundled ONNX model (frozen app), else the project's ``models/onnx`` (dev)."""
    env = os.environ.get("CONTEXTFS_MODEL_DIR")
    if env:
        return Path(env)
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen) / "models" / "minilm-l6-v2"
    return Path(__file__).resolve().parents[3] / "models" / "onnx" / "minilm-l6-v2"


class WriterLocked(RuntimeError):
    """Another process (GUI indexer or CLI) holds the index's writer lock."""


class Engine:
    """Open an index for reading or writing."""

    def __init__(self, index_dir: Path, mode: str, model_dir: Path | None, threads: int) -> None:
        """Use :meth:`open`."""
        from contextfs.engine.schema import connect, migrate

        self.index_dir = Path(index_dir)
        self.mode = mode
        self.model_dir = Path(model_dir) if model_dir else default_model_dir()
        self.threads = threads
        self._lock_fh = None
        self._encoder = None
        db = self.index_dir / "contextfs.db"
        if mode == "write":
            self.index_dir.mkdir(parents=True, exist_ok=True)
            self._acquire_writer_lock()
            self.conn = connect(db)
            migrate(self.conn)
        else:
            if not db.exists():
                raise FileNotFoundError(f"no index at {self.index_dir}; run an update first")
            self.conn = connect(db, read_only=True)
        _pin_blas_threads(threads)

    @classmethod
    def open(
        cls,
        index_dir: Path | None = None,
        mode: str = "read",
        model_dir: Path | None = None,
        threads: int = 4,
    ) -> Engine:
        """Open (and in write mode create or migrate) the index at ``index_dir``."""
        return cls(index_dir or default_index_dir(), mode, model_dir, threads)

    # -- writer lock (ADR-0012) -----------------------------------------------------------------

    def _acquire_writer_lock(self) -> None:
        path = self.index_dir / "writer.lock"
        fh = open(path, "a+b")  # noqa: SIM115 - held for the engine's lifetime
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            fh.close()
            raise WriterLocked(f"another ContextFS is updating {self.index_dir}") from exc
        self._lock_fh = fh

    def close(self) -> None:
        """Release the database and the writer lock."""
        self.conn.close()
        if self._lock_fh is not None:
            self._lock_fh.close()
            self._lock_fh = None

    def __enter__(self) -> Engine:
        """Context-manager entry."""
        return self

    def __exit__(self, *exc) -> None:
        """Context-manager exit."""
        self.close()

    # -- components --------------------------------------------------------------------------

    @property
    def encoder(self):
        """The ONNX sentence encoder, loaded on first use."""
        if self._encoder is None:
            from contextfs.engine.encoder import OnnxEncoder

            self._encoder = OnnxEncoder(self.model_dir, threads=self.threads)
        return self._encoder

    # -- API ---------------------------------------------------------------------------------

    def update(self, roots, progress=None, cancel=None, clock=None):
        """Incrementally index ``roots`` (write mode)."""
        if self.mode != "write":
            raise RuntimeError("open the engine with mode='write' to update")
        from contextfs.engine.indexer import run_update

        return run_update(self, [Path(r) for r in roots], progress, cancel, clock)

    def search(self, query: str, config=None, as_of=None):
        """Ranked, explained results for ``query`` (see ``engine.rank``)."""
        from contextfs.engine.rank import search

        return search(self, query, config, as_of)

    def time_evidence(self, file_id: int) -> dict:
        """Raw evidence and every estimator's per-event times for one file (dates panel)."""
        ev = [
            dict(r)
            for r in self.conn.execute("SELECT * FROM time_evidence WHERE file_id=?", (file_id,))
        ]
        ft = [
            dict(r)
            for r in self.conn.execute("SELECT * FROM file_time WHERE file_id=?", (file_id,))
        ]
        obs = [
            dict(r)
            for r in self.conn.execute("SELECT * FROM observations WHERE file_id=?", (file_id,))
        ]
        return {"evidence": ev, "file_time": ft, "observations": obs}

    def status(self) -> dict:
        """Counts for the status bar and the CLI."""
        q = self.conn.execute
        return {
            "files": q("SELECT COUNT(*) FROM files WHERE status='present'").fetchone()[0],
            "offline": q("SELECT COUNT(*) FROM files WHERE status='offline'").fetchone()[0],
            "metadata_only": {
                r[0]: r[1]
                for r in q(
                    "SELECT access, COUNT(*) FROM files WHERE status='present' AND access!='ok' GROUP BY access"
                )
            },
            "chunks": q("SELECT COUNT(*) FROM vectors WHERE kind='chunk' AND valid=1").fetchone()[
                0
            ],
            "sessions": q("SELECT COUNT(DISTINCT session_id) FROM sessions").fetchone()[0],
            "roots": [dict(r) for r in q("SELECT path, state FROM roots")],
            "generation": (
                q("SELECT value FROM meta WHERE key='index_generation'").fetchone() or [None]
            )[0],
        }


def _pin_blas_threads(n: int) -> None:
    """Deterministic BLAS reductions (skeptic N6): same thread count in every process."""
    try:
        from threadpoolctl import threadpool_limits

        threadpool_limits(n)
    except Exception:  # noqa: BLE001 - optional; ORT threads are pinned separately
        os.environ.setdefault("OPENBLAS_NUM_THREADS", str(n))
