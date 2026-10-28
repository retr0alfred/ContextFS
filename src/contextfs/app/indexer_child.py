"""The indexer child process (ADR-0001): the only writer; reports progress as JSON lines.

Started by the GUI with ``--indexer [--gpu auto|off] <index_dir> <root> [<root> ...]``. It writes
one JSON object per line to stdout (``started`` / ``progress`` / ``done`` / ``error``); ``done``
says which device embedded the files (a probed GPU or the CPU, with the reason).

**Cancellation is a file**, not stdin. The GUI creates ``<index_dir>/cancel.request``, and the
child checks for it between files, commits its batch and exits cleanly, so the next launch
resumes. Reading stdin from a background thread stalled the child on Qt-created Windows pipes
(log.md Phase 32), so the child never touches stdin.

**Hardening (log.md Phase 33):**
- a file that takes longer than ``FILE_TIMEOUT`` seconds ends the child (exit 3); the in-progress
  record makes it a strike, and two strikes quarantine the file (``engine.guard``). The GUI
  restarts the child, so one bad file costs a restart, not the scan;
- a full disk ends it with exit 5 before anything is written;
- stderr is capped at 25 MB (ADR-0016).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

__all__ = ["main", "CANCEL_FILE", "EXIT_HANG", "EXIT_LOCKED", "EXIT_DISK", "FILE_TIMEOUT"]

CANCEL_FILE = "cancel.request"
EXIT_HANG, EXIT_LOCKED, EXIT_DISK = 3, 4, 5
FILE_TIMEOUT = float(os.environ.get("CONTEXTFS_FILE_TIMEOUT", "180"))
LOG_CAP = 25 * 2**20


class _CappedStream:
    """Pass writes through until ``cap`` characters, then drop them (with one notice)."""

    def __init__(self, stream, cap: int) -> None:
        self.stream, self.cap, self.used = stream, cap, 0

    def write(self, text: str) -> int:
        if self.stream is None or self.used >= self.cap:
            return len(text)
        self.used += len(text)
        if self.used >= self.cap:
            text += "\n[log capped at 25 MB; further messages dropped]\n"
        return self.stream.write(text)

    def flush(self) -> None:
        if self.stream is not None:
            self.stream.flush()


def _install_test_fault(spec: str) -> None:
    """Hardening-suite hook: make parsing one file hang or crash, as a broken parser would.

    ``CONTEXTFS_TEST_FAULT=hang:<name>`` or ``crash:<name>`` affects any file whose path contains
    <name>. Inert unless that variable is set.
    """
    import faulthandler
    import time

    import contextfs.extract as extract

    mode, _, needle = spec.partition(":")
    real = extract.extract_file

    def faulty(path, rel_path, *args, **kwargs):
        if needle and needle in rel_path:
            if mode == "hang":
                time.sleep(10**6)
            elif mode == "crash":
                # a real segfault: ctypes would turn a bad read into a catchable OSError instead
                faulthandler._sigsegv()
        return real(path, rel_path, *args, **kwargs)

    extract.extract_file = faulty


def main(argv: list[str]) -> int:
    """Entry point: ``argv = [--gpu auto|off,] index_dir, root, ...``."""
    import logging

    from contextfs.engine import Engine, WriterLocked
    from contextfs.engine.guard import DiskSpaceLow

    if os.environ.get("CONTEXTFS_TEST_FAULT"):
        _install_test_fault(os.environ["CONTEXTFS_TEST_FAULT"])
    sys.stderr = _CappedStream(sys.stderr, LOG_CAP)
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # malformed-PDF chatter, not our errors
    device = "cpu"
    if argv[:1] == ["--gpu"]:
        device = "auto" if argv[1] == "auto" else "cpu"
        argv = argv[2:]
    index_dir, roots = Path(argv[0]), argv[1:]
    cancel_path = index_dir / CANCEL_FILE  # the GUI removes stale ones before launching us

    def emit(obj: dict) -> None:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    def on_hang(record: dict) -> None:
        emit({"type": "error", "kind": "hang", "file": record["rel_path"],
              "message": f"{record['rel_path']} took over {FILE_TIMEOUT:.0f} s; it gets a strike"})
        os._exit(EXIT_HANG)  # a stuck parser cannot be interrupted; the record stays for the strike

    last = [0]

    def progress(rep) -> None:
        done = rep.new + rep.changed
        if done - last[0] >= 10:
            last[0] = done
            emit({"type": "progress", "seen": rep.seen, "processed": done, "unchanged": rep.unchanged})

    try:
        with Engine.open(index_dir, mode="write", device=device) as eng:
            if eng.recovered:
                emit({"type": "recovered", "message": eng.recovered})
            emit({"type": "started", "roots": roots})
            # the GUI always passes the complete Settings list, so other roots were removed
            rep = eng.update(roots, progress=progress, cancel=cancel_path.exists, prune_other_roots=True,
                             file_timeout=FILE_TIMEOUT, on_hang=on_hang)
            emit({"type": "done", "report": rep.as_dict(), "device": eng.device_note})
        return 0
    except WriterLocked as exc:
        emit({"type": "error", "kind": "locked", "message": str(exc)})
        return EXIT_LOCKED
    except DiskSpaceLow as exc:
        emit({"type": "error", "kind": "disk_low", "message": str(exc)})
        return EXIT_DISK
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI, never silent
        emit({"type": "error", "kind": type(exc).__name__, "message": str(exc)[:500]})
        return 1
    finally:
        cancel_path.unlink(missing_ok=True)
