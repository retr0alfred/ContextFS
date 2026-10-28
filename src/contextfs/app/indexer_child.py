"""The indexer child process (ADR-0001): the only writer; reports progress as JSON lines.

Started by the GUI with ``--indexer <index_dir> <root> [<root> ...]``. It writes one JSON object
per line to stdout (``started`` / ``progress`` / ``done`` / ``error``).

**Cancellation is a file**, not stdin. The GUI creates ``<index_dir>/cancel.request``, and the
child checks for it between files, commits its batch and exits cleanly, so the next launch
resumes. Reading stdin from a background thread stalled the child on Qt-created Windows pipes
(log.md Phase 32), so the child never touches stdin.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

__all__ = ["main", "CANCEL_FILE"]

CANCEL_FILE = "cancel.request"


def main(argv: list[str]) -> int:
    """Entry point: ``argv = [index_dir, root, ...]``."""
    import logging

    from contextfs.engine import Engine, WriterLocked

    logging.getLogger("pypdf").setLevel(logging.ERROR)  # malformed-PDF chatter, not our errors
    index_dir, roots = Path(argv[0]), argv[1:]
    cancel_path = index_dir / CANCEL_FILE  # the GUI removes stale ones before launching us

    def emit(obj: dict) -> None:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    last = [0]

    def progress(rep) -> None:
        done = rep.new + rep.changed
        if done - last[0] >= 10:
            last[0] = done
            emit({"type": "progress", "seen": rep.seen, "processed": done, "unchanged": rep.unchanged})

    try:
        with Engine.open(index_dir, mode="write") as eng:
            emit({"type": "started", "roots": roots})
            rep = eng.update(roots, progress=progress, cancel=cancel_path.exists)
            emit({"type": "done", "report": rep.as_dict()})
        return 0
    except WriterLocked as exc:
        emit({"type": "error", "kind": "locked", "message": str(exc)})
        return 4
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI, never silent
        emit({"type": "error", "kind": type(exc).__name__, "message": str(exc)[:500]})
        return 1
    finally:
        cancel_path.unlink(missing_ok=True)
