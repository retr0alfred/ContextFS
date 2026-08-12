"""Background workers for the desktop GUI (Phase 27).

Every ContextFS operation that touches a model, the disk, or the index runs
here, on a Qt worker thread, never on the GUI thread. On the target hardware
(Ryzen 7 3700U, no GPU) loading the embedding model takes seconds and a full
scan takes tens of seconds; doing either on the GUI thread would freeze the
window, and Windows would paint the "not responding" ghost over it.

The design point that makes the GUI worth building at all is
:class:`RetrievalService`: it loads the models **once** and keeps them resident
for the life of the window. The CLI cannot do this - each invocation is a fresh
process that pays model load, uses it for one query, and exits. The GUI is the
only surface where the warm-query cost is what the user actually experiences.
"""

from __future__ import annotations

import time
import traceback
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

__all__ = ["Job", "JobRunner", "RetrievalService", "run_job"]


class JobSignals(QObject):
    """Signals a job emits. Separate object because QRunnable is not a QObject."""

    #: Emitted with the job's return value on success.
    finished = Signal(object)
    #: Emitted with (message, traceback) if the job raised.
    failed = Signal(str, str)
    #: Emitted with a human-readable progress line.
    progress = Signal(str)


class Job(QRunnable):
    """Run one callable off the GUI thread and report the outcome.

    The callable may accept a ``progress`` keyword; if it does, it is handed a
    function it can call to push status lines back to the window.
    """

    def __init__(self, fn, *args, **kwargs) -> None:
        """Bind the callable and its arguments."""
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = JobSignals()

    @Slot()
    def run(self) -> None:
        """Execute, converting any exception into a `failed` signal.

        A traceback reaching a Qt slot boundary is undefined behaviour and can
        take the process down, so nothing is allowed to propagate out of here.
        """
        try:
            result = self.fn(*self.args, progress=self.signals.progress.emit, **self.kwargs)
        except TypeError as exc:
            # The callable did not want a progress channel. Retry without it,
            # but only if that is genuinely why it failed - otherwise a real
            # TypeError inside the job would be silently retried and reported
            # against the wrong call.
            if "progress" not in str(exc):
                self.signals.failed.emit(str(exc), traceback.format_exc())
                return
            try:
                result = self.fn(*self.args, **self.kwargs)
            except Exception as inner:  # noqa: BLE001 - boundary, must not escape
                self.signals.failed.emit(str(inner), traceback.format_exc())
                return
        except Exception as exc:  # noqa: BLE001 - boundary, must not escape
            self.signals.failed.emit(str(exc), traceback.format_exc())
            return
        self.signals.finished.emit(result)


class JobRunner:
    """Owns the thread pool and keeps job objects alive while they run."""

    def __init__(self, max_threads: int = 1) -> None:
        """Serialise background work onto a single worker thread.

        One thread, not several, for two independent reasons:

        1. **Correctness.** The long-lived index connection is shared across
           jobs. Serialising them means no two operations ever touch it at
           once, which is half of what makes the cross-thread SQLite handle
           safe (the other half being serialized sqlite3).
        2. **Speed, on this hardware.** The target machine has 8 logical cores
           and torch is already configured to use several. Running two indexing
           or embedding jobs concurrently oversubscribes the CPU and makes both
           slower — the GUI would feel busier while getting less done.

        The cost is that a long scan delays a queued search. That is the right
        trade: the alternative is a search racing the scan that is rewriting
        the index underneath it.
        """
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(max_threads)
        self._live: list[Job] = []

    def submit(self, fn, *args, on_done=None, on_error=None, on_progress=None, **kwargs) -> Job:
        """Queue a callable and wire up its callbacks."""
        job = Job(fn, *args, **kwargs)
        if on_done is not None:
            job.signals.finished.connect(on_done)
        if on_error is not None:
            job.signals.failed.connect(on_error)
        if on_progress is not None:
            job.signals.progress.connect(on_progress)
        job.signals.finished.connect(lambda _=None, j=job: self._retire(j))
        job.signals.failed.connect(lambda *_, j=job: self._retire(j))
        self._live.append(job)
        self.pool.start(job)
        return job

    def _retire(self, job: Job) -> None:
        """Drop a finished job so it can be collected."""
        if job in self._live:
            self._live.remove(job)

    def wait(self, timeout_ms: int = 30_000) -> bool:
        """Block until queued work drains. Used on window close."""
        return self.pool.waitForDone(timeout_ms)


def run_job(fn, *args, **kwargs):
    """Call a plain function, tolerating the ``progress`` keyword.

    Lets the same callable be used directly in tests and inside a :class:`Job`.
    """
    kwargs.pop("progress", None)
    return fn(*args, **kwargs)


class RetrievalService:
    """Holds the loaded models and index for the life of the window.

    This is the whole reason a desktop GUI is more than a nicer CLI. Model load
    on this hardware is measured in seconds; the CLI pays it on every single
    invocation because each command is a separate process. Here it is paid once,
    at startup, on a worker thread, behind a visible status line - and every
    query afterwards is a warm query.

    Not thread-safe by construction, and not made so: all access is funnelled
    through the single-slot job queue in :class:`JobRunner`, because SQLite
    connections and the LanceDB handle are not safe to share across threads
    arbitrarily.
    """

    def __init__(self, config) -> None:
        """Bind to a configuration without loading anything yet."""
        self.config = config
        self.store = None
        self.hybrid = None
        self.baseline = None
        self.graph = None
        self.load_seconds = 0.0
        self.ready = False

    def load(self, progress=None) -> dict[str, Any]:
        """Open the index and load the models. Slow; call from a worker."""
        started = time.perf_counter()

        def say(message):
            if progress:
                progress(message)

        say("opening index…")
        from contextfs.store import Store

        # Opened here on a worker thread, used by later search jobs, and closed
        # on the GUI thread when the window shuts. See Store's docstring for why
        # that is safe: serialized sqlite3 plus the single-threaded job queue.
        self.store = Store(self.config.db_path, read_only=True, cross_thread=True)

        say("loading embedding model…")
        from contextfs.embed import Embedder

        embedder = Embedder(
            self.config.embeddings.model,
            device=self.config.embeddings.device,
            batch_size=self.config.embeddings.batch_size,
            expected_dimension=self.config.embeddings.dimension,
            backend=self.config.embeddings.backend,
            num_threads=self.config.embeddings.num_threads,
        )

        say("opening vector store…")
        from contextfs.embed import VectorStore

        vectors = VectorStore(self.config.vector_dir, self.config.embeddings.dimension)

        say("loading relationship graph…")
        from contextfs.graph import load_graph

        self.graph = load_graph(self.config.graph_file)

        say("building timeline index…")
        from contextfs.temporal import TimelineIndex

        timeline = TimelineIndex.from_store(self.store)

        from contextfs.retrieval import ALL_SIGNALS, HybridRetriever, SemanticBaseline

        self.hybrid = HybridRetriever(
            self.store,
            vectors,
            embedder,
            self.graph,
            self.config,
            signals=ALL_SIGNALS,
            timeline_index=timeline,
            # Feedback is on in the GUI, as it is in the CLI. It is a bounded
            # re-rank and it never reaches the evaluation harness.
            feedback=None,
        )
        self.baseline = SemanticBaseline(self.store, vectors, embedder)

        say("warming up…")
        # One throwaway query so the first *user* query is warm. Without this
        # the first search pays lazy-init costs inside torch and LanceDB and
        # looks several times slower than the system actually is.
        self.hybrid.search("warm up", top_k=1)

        self.load_seconds = time.perf_counter() - started
        self.ready = True
        return {"seconds": self.load_seconds, "files": self.store.file_count()}

    def search(self, text: str, top_k: int = 10, compare: bool = False, progress=None):
        """Run a query. Returns (hybrid_response, baseline_response|None)."""
        _ = progress
        if not self.ready:
            raise RuntimeError("index not loaded yet")
        hybrid = self.hybrid.search(text, top_k)
        flat = self.baseline.search(text, top_k) if compare else None
        return hybrid, flat

    def close(self) -> None:
        """Release the index handles."""
        if self.store is not None:
            self.store.close()
            self.store = None
        self.ready = False
