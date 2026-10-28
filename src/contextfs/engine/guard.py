"""Indexer safety guards (ADR-0001 §2, ADR-0012 §4; log.md Phase 33).

- :class:`InProgress` records, before a file is read or parsed, which file the indexer is inside.
  If the process dies there (a parser crash, a hang that the watchdog ends, a kill), the next
  update finds the record and gives that file a *strike*. Two strikes on the same unchanged file
  quarantine it: it is indexed by name and file-system times only, never opened again until it
  changes. One strike is forgiven, because a user closing the app mid-file looks the same.
- :class:`Watchdog` notices a file that takes longer than ``timeout`` seconds and calls
  ``on_hang`` (the indexer child exits, leaving the record in place). A thread cannot interrupt
  a stuck parser, so ending the process is the only safe way out.
- :func:`check_free_space`: refuse to start indexing below max(2 GB, 2x the index size) free, and
  stop cleanly (like a cancel) below 1 GB while running.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path

__all__ = [
    "InProgress",
    "Watchdog",
    "DiskSpaceLow",
    "check_free_space",
    "low_disk_while_running",
    "QUARANTINE_STRIKES",
]

QUARANTINE_STRIKES = 2
MIN_FREE_START_MB = 2048
MIN_FREE_RUN_MB = 1024


class DiskSpaceLow(RuntimeError):
    """Not enough free disk space to index safely."""


class InProgress:
    """A tiny file naming the file being indexed right now.

    It is rewritten in place and never fsynced: the OS keeps it across a process crash, which is
    the case that matters.
    """

    NAME = "inprogress.marker"

    def __init__(self, index_dir: Path) -> None:
        """Bind to ``<index_dir>/inprogress.marker``."""
        self.path = Path(index_dir) / self.NAME
        self.fd: int | None = None
        self.current: dict | None = None
        self.started = 0.0

    def previous(self) -> dict | None:
        """The record a previous run left behind (it died inside that file), or None."""
        try:
            raw = self.path.read_bytes()
        except OSError:
            return None
        try:
            return json.loads(raw) if raw.strip() else None
        except ValueError:
            return None

    def open(self) -> None:
        """Start recording (empties any previous record)."""
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0))
        os.ftruncate(self.fd, 0)

    def set(self, root_id: int, rel_path: str, size: int, mtime_ns: int) -> None:
        """Record that ``rel_path`` is about to be read and parsed."""
        self.current = {"root_id": root_id, "rel_path": rel_path, "size": size, "mtime_ns": mtime_ns}
        self.started = time.monotonic()
        if self.fd is not None:
            payload = json.dumps(self.current).encode("utf-8")
            os.lseek(self.fd, 0, os.SEEK_SET)
            os.write(self.fd, payload)
            os.ftruncate(self.fd, len(payload))

    def clear(self) -> None:
        """The file finished (successfully or with a handled error)."""
        self.current = None
        if self.fd is not None:
            os.ftruncate(self.fd, 0)

    def close(self) -> None:
        """A clean end of the update: no record remains."""
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self.path.unlink(missing_ok=True)


class Watchdog:
    """Calls ``on_hang(record)`` once if one file takes longer than ``timeout`` seconds."""

    def __init__(self, marker: InProgress, timeout: float, on_hang) -> None:
        """Watch ``marker``; poll about once a second."""
        self.marker, self.timeout, self.on_hang = marker, timeout, on_hang
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="contextfs-watchdog", daemon=True)

    def start(self) -> Watchdog:
        """Begin watching."""
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop watching."""
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(min(1.0, self.timeout / 4)):
            cur = self.marker.current
            if cur is not None and time.monotonic() - self.marker.started > self.timeout:
                self.on_hang(dict(cur))
                return


def _limit_mb(env: str, default: int) -> int:
    try:
        return int(os.environ.get(env, default))
    except ValueError:
        return default


def _index_bytes(index_dir: Path) -> int:
    return sum(f.stat().st_size for f in Path(index_dir).rglob("*") if f.is_file())


def check_free_space(index_dir: Path) -> None:
    """Raise :class:`DiskSpaceLow` if starting an update could fill the disk.

    ``CONTEXTFS_MIN_FREE_MB`` overrides the 2 GB floor (the hardening suite uses it to simulate
    a full disk without filling one).
    """
    floor = _limit_mb("CONTEXTFS_MIN_FREE_MB", MIN_FREE_START_MB) * 2**20
    need = max(floor, 2 * _index_bytes(index_dir))
    free = shutil.disk_usage(index_dir).free
    if free < need:
        raise DiskSpaceLow(
            f"only {free / 2**30:.1f} GB free on the index drive; indexing needs at least "
            f"{need / 2**30:.1f} GB. Free some space or move the index (Settings)."
        )


def low_disk_while_running(index_dir: Path) -> bool:
    """True below 1 GB free (``CONTEXTFS_MIN_FREE_RUN_MB`` overrides): stop cleanly."""
    floor = _limit_mb("CONTEXTFS_MIN_FREE_RUN_MB", MIN_FREE_RUN_MB) * 2**20
    return shutil.disk_usage(index_dir).free < floor
