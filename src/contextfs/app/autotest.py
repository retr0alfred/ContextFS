"""Self-driving GUI for the hardening suite (``CONTEXTFS_SELFDRIVE=<config.json>``; inert otherwise).

The packaged window drives itself: while its indexer child runs, it types a query every
``query_every_s`` seconds and measures
- event-loop stalls with a 20 ms heartbeat (ADR-0001: a stall over 250 ms during indexing fails);
- search latency from the request to the painted result list;
then writes a JSON report to ``out`` and closes once the update (including automatic restarts)
has finished, or after ``max_seconds``.

Config: ``{"queries": [...], "out": "report.json", "query_every_s": 1.5, "max_seconds": 900}``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

__all__ = ["attach"]


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return round(s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))], 1)


def attach(win, cfg_path: str) -> None:
    """Start driving ``win`` (a MainWindow) according to the config file."""
    cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    st = {"last": time.perf_counter(), "t0": time.perf_counter(), "gaps": [], "pending": {}, "search_ms": [],
          "counts": [], "qi": 0, "child_seen": False, "idle_ticks": 0, "statuses": []}

    def beat() -> None:
        now = time.perf_counter()
        if now - st["t0"] > 2.0:  # ignore start-up (model load, first paint)
            st["gaps"].append((now - st["last"]) * 1000)
        st["last"] = now

    heartbeat = QTimer(win)
    heartbeat.setInterval(20)
    heartbeat.timeout.connect(beat)
    heartbeat.start()

    shown = win.show_results

    def timed_show(payload, seq) -> None:
        if seq in st["pending"]:
            shown(payload, seq)  # paint first, then stop the clock
            st["search_ms"].append((time.perf_counter() - st["pending"].pop(seq)) * 1000)
            st["counts"].append(len(payload[0].results))
            return
        shown(payload, seq)

    win.show_results = timed_show  # SearchJob connects to the attribute at search time

    def finish(timed_out: bool) -> None:
        gaps = st["gaps"]
        report = {
            "seconds": round(time.perf_counter() - st["t0"], 1),
            "timed_out": timed_out,
            "heartbeats": len(gaps),
            "stall_max_ms": round(max(gaps), 1) if gaps else None,
            "stalls_over_250ms": sum(1 for g in gaps if g > 250),
            "gap_p99_ms": _pct(gaps, 0.99),
            "searches": len(st["search_ms"]),
            "search_p50_ms": _pct(st["search_ms"], 0.5),
            "search_p95_ms": _pct(st["search_ms"], 0.95),
            "search_max_ms": round(max(st["search_ms"]), 1) if st["search_ms"] else None,
            "result_counts": st["counts"],
            "child_seen": st["child_seen"],
            "restarts": win.restarts_total,
            "last_child_exit": win.last_child_exit[0] if getattr(win, "last_child_exit", None) else None,
            "statuses": st["statuses"][-12:],
        }
        Path(cfg["out"]).write_text(json.dumps(report, indent=2), encoding="utf-8")
        win.close()
        QApplication.quit()

    def drive() -> None:
        msg = win.status.currentMessage()
        if msg and (not st["statuses"] or st["statuses"][-1] != msg):
            st["statuses"].append(msg)
        if win.proc is not None:
            st["child_seen"], st["idle_ticks"] = True, 0
        elif getattr(win, "last_child_exit", None) is not None:
            st["child_seen"] = True  # a fast update can finish before the first tick
            st["idle_ticks"] += 1
        else:
            st["idle_ticks"] += 1 if st["child_seen"] else 0
        queries = cfg.get("queries", [])
        if queries:
            win.box.setText(queries[st["qi"] % len(queries)])
            st["qi"] += 1
            win.search()
            st["pending"][win.seq] = time.perf_counter()
        elapsed = time.perf_counter() - st["t0"]
        # done: a child ran, none has run for 3 ticks (restarts start within 1 s), searches answered
        if st["child_seen"] and st["idle_ticks"] >= 3 and len(st["pending"]) <= 1:
            driver.stop()
            QTimer.singleShot(1500, lambda: finish(False))
        elif elapsed > cfg.get("max_seconds", 900):
            driver.stop()
            finish(True)

    driver = QTimer(win)
    driver.setInterval(int(cfg.get("query_every_s", 1.5) * 1000))
    driver.timeout.connect(drive)
    driver.start()
