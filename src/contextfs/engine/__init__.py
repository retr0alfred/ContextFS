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

    def __init__(
        self, index_dir: Path, mode: str, model_dir: Path | None, threads: int, device: str = "cpu"
    ) -> None:
        """Use :meth:`open`."""
        from contextfs.engine.schema import connect

        self.index_dir = Path(index_dir)
        self.mode = mode
        self.model_dir = Path(model_dir) if model_dir else default_model_dir()
        self.threads = threads
        self.device = device  # "cpu" | "auto" (GPU for bulk indexing if it passes the probe)
        self.device_note = ""  # set when indexing first embeds: which device, and why
        self._lock_fh = None
        self._encoder = None
        self._bulk_encoder = None
        db = self.index_dir / "contextfs.db"
        self.recovered = ""  # why a damaged index was set aside and rebuilt (empty if it was not)
        if mode == "write":
            self.index_dir.mkdir(parents=True, exist_ok=True)
            self._acquire_writer_lock()
            self.recovered = self._open_writable(db)
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
        device: str = "cpu",
    ) -> Engine:
        """Open (and in write mode create or migrate) the index at ``index_dir``.

        ``device="auto"`` lets *indexing* use a discrete GPU via DirectML, behind the on-device
        probe (``engine.gpu``). Queries always run on the CPU EP.
        """
        return cls(index_dir or default_index_dir(), mode, model_dir, threads, device)

    # -- corruption recovery (ADR-0012 §3) -----------------------------------------------------

    def _open_writable(self, db: Path) -> str:
        """Open and migrate for writing. A damaged database is set aside and a new one started.

        Checked: the open itself, the migrations, and ``PRAGMA quick_check`` on every writable open.
        ADR-0012 ran it only after a dirty shutdown, but a damaged page behind an intact header and a
        clean last scan would then go unnoticed; it costs about 1 s for a 286 MB index and runs in
        the indexer child while search continues (log.md Phase 33). A database written by a *newer*
        ContextFS raises instead: it is not damaged, and rebuilding would destroy it.
        """
        import sqlite3

        from contextfs.engine.schema import connect, migrate

        conn = None
        try:
            conn = connect(db)
            migrate(conn)
            verdict = conn.execute("PRAGMA quick_check").fetchone()[0]
            if verdict != "ok":
                raise sqlite3.DatabaseError(f"quick_check: {verdict}")
            self.conn = conn
            return ""
        except sqlite3.OperationalError:
            # locked, busy, disk I/O, disk full: a state, not damage. Rebuilding here would throw a
            # healthy index away (QA M1); report it and let the next update try again.
            if conn is not None:
                conn.close()
            raise
        except sqlite3.DatabaseError as exc:
            if conn is not None:
                conn.close()
            reason = f"{type(exc).__name__}: {exc}"[:200]
            self._set_aside_damaged()
            self.conn = connect(db)
            migrate(self.conn)
            return reason

    def _set_aside_damaged(self) -> None:
        """Move the damaged database and vectors to ``damaged-<time>/`` (only the latest is kept)."""
        import shutil
        import time

        for old in self.index_dir.glob("damaged-*"):
            shutil.rmtree(old, ignore_errors=True)
        dest = self.index_dir / time.strftime("damaged-%Y%m%d-%H%M%S")
        dest.mkdir()
        for name in ("contextfs.db", "contextfs.db-wal", "contextfs.db-shm", "vectors"):
            src = self.index_dir / name
            if src.exists():
                shutil.move(str(src), str(dest / name))

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

    @property
    def bulk_encoder(self):
        """The encoder for indexing: a probed GPU when ``device="auto"`` allows it, else the CPU."""
        if self._bulk_encoder is None:
            self._bulk_encoder = self.encoder
            self.device_note = "CPU (GPU off in Settings)"
            if self.device == "auto":
                from contextfs.engine.gpu import gpu_encoder

                gpu, self.device_note = gpu_encoder(self.model_dir, self.encoder, self.threads)
                if gpu is not None:
                    self._bulk_encoder = gpu
        return self._bulk_encoder

    # -- API ---------------------------------------------------------------------------------

    def update(
        self,
        roots,
        progress=None,
        cancel=None,
        clock=None,
        prune_other_roots=False,
        file_timeout=None,
        on_hang=None,
    ):
        """Incrementally index ``roots`` (write mode).

        ``prune_other_roots=True`` means ``roots`` is the user's complete list (the app's Settings):
        any other indexed root is marked *removed* and drops out of search. Its data is kept, so
        adding the folder back is instant.
        """
        if self.mode != "write":
            raise RuntimeError("open the engine with mode='write' to update")
        from contextfs.engine.indexer import run_update

        return run_update(
            self, [Path(r) for r in roots], progress, cancel, clock, prune_other_roots, file_timeout, on_hang
        )

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
            "files": q(
                "SELECT COUNT(*) FROM files f JOIN roots r USING(root_id) "
                "WHERE f.status='present' AND r.state!='removed'"
            ).fetchone()[0],
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
