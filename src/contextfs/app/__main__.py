"""ContextFS desktop app entry point (also the PyInstaller entry).

- ``ContextFS.exe``                 → the GUI
- ``ContextFS.exe --indexer ...``   → the indexer child (started by the GUI)
- ``ContextFS.exe --selftest [--gpu]`` → headless offline self test (Decision 95); ``--gpu`` also
  runs the GPU parity probe
- ``ContextFS-console.exe --find <index> [--as-of ISO] (--queries FILE | <query>)`` and
  ``--status <index>``: JSON for support and for the hardening suite, through the packaged build

Every mode runs with network use blocked (a Python audit hook): the app is local-first, and this
makes that a property of the process rather than a promise (log.md Phase 33).
"""

from __future__ import annotations

import json
import sys
import time


def block_network() -> None:
    """Fail any socket connect/bind/DNS lookup made from Python code in this process."""

    def hook(event, args):
        if event in ("socket.connect", "socket.getaddrinfo", "socket.bind"):
            raise RuntimeError(f"network use blocked: ContextFS is local-only ({event})")

    sys.addaudithook(hook)


def _find(args: list[str]) -> int:
    from datetime import datetime
    from pathlib import Path

    from contextfs.engine import Engine

    index, rest = Path(args[0]), args[1:]
    as_of, queries = None, []
    while rest:
        if rest[0] == "--as-of":
            as_of, rest = datetime.fromisoformat(rest[1]).astimezone(), rest[2:]
        elif rest[0] == "--queries":
            queries += [q for q in Path(rest[1]).read_text(encoding="utf-8").splitlines() if q.strip()]
            rest = rest[2:]
        else:
            queries.append(rest[0])
            rest = rest[1:]
    out = []
    with Engine.open(index) as eng:
        eng.search("warm up", as_of=as_of)  # load the model once, outside the timings
        for q in queries:
            t = time.perf_counter()
            resp = eng.search(q, as_of=as_of)
            out.append({"query": q, "ms": round((time.perf_counter() - t) * 1000, 2), "notes": resp.notes,
                        "results": [{"rel_path": r.rel_path, "score": round(r.score, 6), "reasons": r.reasons}
                                    for r in resp.results]})
    sys.stdout.write(json.dumps({"queries": out}, ensure_ascii=False) + "\n")
    return 0


def _status(args: list[str]) -> int:
    from pathlib import Path

    from contextfs.engine import Engine

    with Engine.open(Path(args[0])) as eng:
        st = eng.status()
        st["integrity"] = eng.conn.execute("PRAGMA integrity_check").fetchone()[0]
    sys.stdout.write(json.dumps(st, default=str) + "\n")
    return 0


def main() -> int:
    """Dispatch on the command line."""
    block_network()
    args = sys.argv[1:]
    if args[:1] == ["--indexer"]:
        from contextfs.app.indexer_child import main as child

        return child(args[1:])
    if args[:1] == ["--selftest"]:
        from contextfs.app.selftest import run_selftest

        return run_selftest(gpu="--gpu" in args)
    if args[:1] == ["--find"]:
        return _find(args[1:])
    if args[:1] == ["--status"]:
        return _status(args[1:])
    from contextfs.app.gui import run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
