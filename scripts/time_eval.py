r"""P-A1: intrinsic accuracy of per-event file times, per damage type (ADR-0006 E5, ADR-0015 §7).

    python scripts/time_eval.py data/bench/*-A-* --tag dev-time-v1 [--recompute]

For every estimator in the index's ``file_time`` table and every (damage type, event) it reports,
against the sealed simulation truth (read here by the *harness*; the engine never sees it):

- ``n``         files whose truth has this event;
- ``answered``  share with an estimate (abstentions are reported, never dropped);
- ``coverage``  share whose truth lies inside [lo, hi] (pre-registered E5);
- ``width_d``   median interval width, days (coverage must be read *at* this width);
- ``mae_d``     median absolute error of the point estimate, days;
- ``within1d``  share with |point - truth| <= 1 day;
- ``spurious``  estimates for an event the file never had (e.g. an edit of a never-edited download).

Dev personas only: test personas (family B) are refused. ``--recompute`` re-runs every estimator
over the existing ``index_v2`` (no re-indexing; ``file_time`` only).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

EVENTS = ("authored", "arrived_here", "last_user_edit")
DAY = 86400.0


def truth_of(persona: Path) -> dict[str, dict]:
    out = {}
    for line in (persona / "truth" / "truth_manifest.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        out[r["rel_path"]] = {
            "damage": r["damage"],
            "origin": r["origin"],
            "kind": r["kind"],
            "truth": {e: (int(datetime.fromisoformat(v).astimezone().timestamp()) if v else None)
                      for e, v in r["truth"].items()},
        }
    return out


def recompute(index: Path) -> None:
    from contextfs.engine.indexer import recompute_times
    from contextfs.engine.schema import connect, migrate

    conn = connect(index / "contextfs.db")
    migrate(conn)
    folders = {(r[0], r[1].rsplit("/", 1)[0] if "/" in r[1] else "")
               for r in conn.execute("SELECT root_id, rel_path FROM files WHERE status='present'")}
    recompute_times(conn, folders)
    conn.close()


def rows_of(index: Path) -> list[tuple]:
    c = sqlite3.connect(f"file:{(index / 'contextfs.db').as_posix()}?mode=ro", uri=True)
    rows = c.execute(
        "SELECT f.rel_path, t.estimator, t.event, t.lo_utc, t.hi_utc, t.point_utc, t.confidence "
        "FROM file_time t JOIN files f ON f.file_id=t.file_id WHERE f.status='present'"
    ).fetchall()
    c.close()
    return rows


def summarise(records: list[dict]) -> dict:
    """records: one per (file, event) with truth not None: est fields may be None (abstained)."""
    n = len(records)
    got = [r for r in records if r["point"] is not None]
    if not n:
        return {}
    err = [abs(r["point"] - r["truth"]) / DAY for r in got]
    return {
        "n": n,
        "answered": round(len(got) / n, 4),
        "coverage": round(sum(r["lo"] <= r["truth"] <= r["hi"] for r in got) / n, 4),
        "width_d": round(statistics.median((r["hi"] - r["lo"]) / DAY for r in got), 3) if got else None,
        "mae_d": round(statistics.median(err), 3) if err else None,
        "within1d": round(sum(e <= 1 for e in err) / n, 4),
        "by_conf": {c: round(sum(r["lo"] <= r["truth"] <= r["hi"] for r in got if r["conf"] == c)
                             / max(1, sum(r["conf"] == c for r in got)), 3)
                    for c in ("sure", "probably", "unknown") if any(r["conf"] == c for r in got)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("persona_dirs", type=Path, nargs="+")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--recompute", action="store_true")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "time_eval")
    args = parser.parse_args()
    if any("-B-" in d.name for d in args.persona_dirs):
        print("refusing: test personas (family B) are frozen until v2-P7")
        return 2
    per: dict[tuple, list[dict]] = {}
    spurious: dict[tuple, int] = {}
    estimators: set[str] = set()
    for d in args.persona_dirs:
        idx = d / "index_v2"
        if args.recompute:
            recompute(idx)
        truth = truth_of(d)
        est: dict[tuple, tuple] = {}
        for rel, name, event, lo, hi, point, conf in rows_of(idx):
            est[(rel, name, event)] = (lo, hi, point, conf)
            estimators.add(name)
        for rel, t in truth.items():
            for name in estimators:
                for event in EVENTS:
                    e = est.get((rel, name, event))
                    tv = t["truth"].get(event)
                    if tv is None:
                        if e is not None:
                            spurious[(name, t["damage"], event)] = spurious.get((name, t["damage"], event), 0) + 1
                        continue
                    rec = {"truth": tv, "lo": e[0] if e else None, "hi": e[1] if e else None,
                           "point": e[2] if e else None, "conf": e[3] if e else None}
                    for key in ((name, t["damage"], event), (name, "ALL", event)):
                        per.setdefault(key, []).append(rec)
    summary = {"tag": args.tag, "personas": [d.name for d in args.persona_dirs], "rows": {}}
    for key in sorted(per):
        s = summarise(per[key])
        s["spurious"] = spurious.get(key, 0) if key[1] != "ALL" else sum(
            v for k, v in spurious.items() if k[0] == key[0] and k[2] == key[2])
        summary["rows"]["|".join(key)] = s
    out = args.out / args.tag
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"{'estimator':<10} {'damage':<13} {'event':<15} {'n':>5} {'ans':>5} {'cover':>6} {'width_d':>8} "
          f"{'mae_d':>8} {'<=1d':>5} {'spur':>5}")
    for key, s in summary["rows"].items():
        name, dmg, event = key.split("|")
        print(f"{name:<10} {dmg:<13} {event:<15} {s['n']:>5} {s['answered']:>5.2f} {s['coverage']:>6.2f} "
              f"{s['width_d'] if s['width_d'] is not None else '-':>8} {s['mae_d'] if s['mae_d'] is not None else '-':>8} "
              f"{s['within1d']:>5.2f} {s['spurious']:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
