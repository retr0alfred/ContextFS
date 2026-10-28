r"""P-A1: intrinsic accuracy of per-event file times, per damage type (ADR-0006 E5, ADR-0015 §7).

    python scripts/time_eval.py data/bench/*-A-* --tag NAME [--index-root DIR] [--recompute]

Against the sealed simulation truth (read here by the *harness*; the engine never sees it), for
every estimator in the index's ``file_time`` table and every (damage type, event):

- ``n``           files whose truth has this event
- ``coverage``    share whose truth lies inside [lo, hi]; abstentions count as misses (E5)
- ``within1d``    share with |point - truth| <= 1 day; abstentions count as misses (E5)
- ``mdae_d``      median |point - truth| in days over *answered* files, and ``mdae_all_d`` with
                  abstentions imputed as infinitely wrong (so abstaining never helps)
- ``width_p50/p90_d`` interval width over answered files; ``width_all_p50_d`` with abstentions imputed
                  as the full 1990-to-now range
- ``is20_d``      median interval (Winkler) score at alpha = 0.2, days; abstention = full range
- ``coverage_ci`` 95% persona-cluster bootstrap CI for coverage (personas, not files, are resampled)

Event existence (an estimator may say an event never happened, e.g. no edit for a download) is
scored separately per (origin, event): TP/FN/FP/TN, precision, recall.

Truth times are naive wall-clock times in the generator's zone (IST, +05:30, no DST;
``data/calibration/undamaged_model_v1.md``) and are converted with that fixed offset, so results do
not depend on the evaluating machine's zone. Dev personas only: test personas are refused.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

EVENTS = ("authored", "arrived_here", "last_user_edit")
DAY = 86400.0
GENERATOR_TZ = timezone(timedelta(hours=5, minutes=30))
FULL_RANGE = (631152000, int(time.time()))  # 1990-01-01 .. now: what "no answer" is worth
ALPHA = 0.2


def to_utc(naive: str) -> int:
    return int(datetime.fromisoformat(naive).replace(tzinfo=GENERATOR_TZ).timestamp())


def truth_of(persona: Path) -> dict[str, dict]:
    out = {}
    for line in (persona / "truth" / "truth_manifest.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        out[r["rel_path"]] = {"damage": r["damage"], "origin": r["origin"], "kind": r["kind"],
                              "truth": {e: (to_utc(v) if v else None) for e, v in r["truth"].items()}}
    return out


def index_of(persona: Path, index_root: Path | None) -> Path:
    return (index_root / persona.name) if index_root else persona / "index_v2"


def recompute(index: Path) -> None:
    from contextfs.engine.indexer import recompute_times, refresh_bulk_events
    from contextfs.engine.schema import connect, migrate

    conn = connect(index / "contextfs.db")
    migrate(conn)
    refresh_bulk_events(conn)
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


def interval_score(lo: float, hi: float, y: float) -> float:
    s = hi - lo
    if y < lo:
        s += (2 / ALPHA) * (lo - y)
    elif y > hi:
        s += (2 / ALPHA) * (y - hi)
    return s


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def summarise(recs: list[dict], boot: int = 1000) -> dict:
    """recs: one per (persona, file, event) whose truth has the event; point None = abstained."""
    n = len(recs)
    if not n:
        return {}
    got = [r for r in recs if r["point"] is not None]
    err = [abs(r["point"] - r["truth"]) / DAY for r in got]
    err_all = err + [float("inf")] * (n - len(got))
    width = [(r["hi"] - r["lo"]) / DAY for r in got]
    full = (FULL_RANGE[1] - FULL_RANGE[0]) / DAY
    width_all = width + [full] * (n - len(got))
    iscore = [interval_score(r["lo"], r["hi"], r["truth"]) / DAY for r in got]
    iscore += [interval_score(*FULL_RANGE, r["truth"]) / DAY for r in recs if r["point"] is None]

    def cover(rs):
        return sum(r["point"] is not None and r["lo"] <= r["truth"] <= r["hi"] for r in rs) / len(rs)

    by_p: dict[str, list] = {}
    for r in recs:
        by_p.setdefault(r["persona"], []).append(r)
    rng = random.Random(0)
    keys = list(by_p)
    samples = []
    for _ in range(boot):
        pick = [r for k in (rng.choice(keys) for _ in keys) for r in by_p[k]]
        samples.append(cover(pick))
    samples.sort()
    return {
        "n": n,
        "answered": round(len(got) / n, 4),
        "coverage": round(cover(recs), 4),
        "coverage_ci": [round(samples[int(0.025 * boot)], 4), round(samples[int(0.975 * boot) - 1], 4)],
        "within1d": round(sum(e <= 1 for e in err) / n, 4),
        "mdae_d": round(statistics.median(err), 3) if err else None,
        "mdae_all_d": round(pct(err_all, 0.5), 3) if pct(err_all, 0.5) != float("inf") else "inf",
        "width_p50_d": round(pct(width, 0.5), 3) if width else None,
        "width_p90_d": round(pct(width, 0.9), 3) if width else None,
        "width_all_p50_d": round(pct(width_all, 0.5), 3),
        "is20_d": round(statistics.median(iscore), 3),
        "by_conf": {c: [sum(r["conf"] == c for r in got),
                        round(sum(r["lo"] <= r["truth"] <= r["hi"] for r in got if r["conf"] == c)
                              / max(1, sum(r["conf"] == c for r in got)), 3)]
                    for c in ("sure", "probably", "unknown") if any(r["conf"] == c for r in got)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("persona_dirs", type=Path, nargs="+")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--index-root", type=Path, default=None)
    parser.add_argument("--recompute", action="store_true")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "time_eval")
    args = parser.parse_args()
    if any("-B-" in d.name for d in args.persona_dirs):
        print("refusing: test personas (family B) are frozen until v2-P7")
        return 2
    per: dict[tuple, list[dict]] = {}
    exist: dict[tuple, list[int]] = {}  # (est, origin, event) -> [TP, FN, FP, TN]
    estimators: set[str] = set()
    for d in args.persona_dirs:
        idx = index_of(d, args.index_root)
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
                    cm = exist.setdefault((name, t["origin"], event), [0, 0, 0, 0])
                    cm[(0 if e else 1) if tv is not None else (2 if e else 3)] += 1
                    if tv is None:
                        continue
                    rec = {"persona": d.name, "truth": tv, "lo": e[0] if e else None, "hi": e[1] if e else None,
                           "point": e[2] if e else None, "conf": e[3] if e else None}
                    for key in ((name, t["damage"], event), (name, "ALL", event)):
                        per.setdefault(key, []).append(rec)
    summary = {"tag": args.tag, "personas": [d.name for d in args.persona_dirs], "alpha": ALPHA,
               "rows": {}, "macro": {}, "existence": {}}
    for key in sorted(per):
        summary["rows"]["|".join(key)] = summarise(per[key])
    for name in sorted(estimators):
        for event in EVENTS:
            rows = [summary["rows"][k] for k in summary["rows"]
                    if k.startswith(name + "|") and k.endswith("|" + event) and "|ALL|" not in k
                    and summary["rows"][k].get("n", 0) >= 30]
            if rows:
                summary["macro"][f"{name}|{event}"] = {
                    m: round(statistics.mean(r[m] for r in rows), 4) for m in ("coverage", "within1d", "is20_d")}
    for (name, origin, event), (tp, fn, fp, tn) in sorted(exist.items()):
        summary["existence"][f"{name}|{origin}|{event}"] = {
            "TP": tp, "FN": fn, "FP": fp, "TN": tn,
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None}
    out = args.out / args.tag
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"{'estimator':<10} {'damage':<13} {'event':<15} {'n':>5} {'cover':>6} {'ci':>13} {'<=1d':>5} "
          f"{'mdae':>8} {'w50':>7} {'w90':>7} {'is20':>8}")
    for key, s in summary["rows"].items():
        name, dmg, event = key.split("|")
        if dmg in ("git_clone",):
            continue
        print(f"{name:<10} {dmg:<13} {event:<15} {s['n']:>5} {s['coverage']:>6.2f} "
              f"{str(s['coverage_ci']):>13} {s['within1d']:>5.2f} {str(s['mdae_d']):>8} "
              f"{str(s['width_p50_d']):>7} {str(s['width_p90_d']):>7} {s['is20_d']:>8}")
    print("existence of last_user_edit (precision / recall):")
    for k, v in summary["existence"].items():
        if k.endswith("last_user_edit"):
            print(f"  {k:<40} P={v['precision']} R={v['recall']} (TP {v['TP']} FN {v['FN']} FP {v['FP']} TN {v['TN']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
