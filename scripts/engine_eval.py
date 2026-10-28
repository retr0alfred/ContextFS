"""Evaluate the v2 engine on benchmark personas, and fit its scorer on the dev split.

For each persona: index with ``contextfs.engine`` into ``<persona>/index_v2`` (once), then run
every query under each time source:

- ``time_off``:   time feature masked (content only)
- ``raw_mtime``:  time from the file system's modified time (direction A's baseline)
- ``rules_v0``:   time from the rule estimator (frozen T v1 + calibration)
- ``oracle``:     time from the sealed simulation truth, supplied by this harness as an override
                  (upper bound; the engine never opens truth files)

This is the P-A2 design (ADR-0015 §7) run on dev. ``--fit`` fits the linear scorer weights by
coordinate ascent on nDCG@10 over cached candidate pools (offline re-scoring, verified equal to
online) and writes ``data/scorer/weights_<tag>.json``. It refuses any test-family persona.

Usage:
    python scripts/engine_eval.py data/bench/*-A-* --tag dev-engine-v1 [--fit] [--weights FILE]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import numpy as np  # noqa: E402

from contextfs.engine import Engine  # noqa: E402
from contextfs.engine.rank import FEATURES, SearchConfig  # noqa: E402
from contextfs.evalkit import (  # noqa: E402
    bootstrap_ci,
    hit_at_1,
    ndcg_at_k,
    paired_randomisation_p,
    recall_at_k,
    reciprocal_rank,
)

SOURCES = ("time_off", "raw_mtime", "rules_v0", "recon_v1", "oracle")
#: The generator writes naive wall-clock times in IST (no DST); a fixed zone keeps results independent
#: of the evaluating machine's zone (review 2, finding 8; matches scripts/time_eval.py).
GENERATOR_TZ = timezone(timedelta(hours=5, minutes=30))
#: P-A2 / P-A2s (Decision 106, pre-declared before any P-A2 number): a query is time-cued when its
#: generator cue is one whose templates always carry a time slot.
TIME_CUED = {"temporal", "mixed"}
#: P-A2s subgroup: the target's file-system times were reset by damage (sealed damage label).
RESET_DAMAGE = {"content_copy", "git_clone"}
TIME_FEATURE = "expected"
GRID = (0.0, 0.05, 0.1, 0.2, 0.3, 0.45, 0.6, 0.8, 1.0, 1.4)


def oracle_override(persona_dir: Path) -> tuple:
    out = []
    for line in (persona_dir / "truth" / "truth_manifest.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        for ev in ("authored", "arrived_here", "last_user_edit"):
            v = r["truth"].get(ev)
            if v:
                t = int(datetime.fromisoformat(v).replace(tzinfo=GENERATOR_TZ).timestamp())
                out.append((r["rel_path"], ev, t, t + 1))
    return tuple(out)


def config_for(source: str, weights, oracle=()) -> SearchConfig:
    w, tf = tuple(weights), TIME_FEATURE
    if source == "time_off":
        return SearchConfig(weights=w, signals=tuple(f for f in FEATURES if f != "time"), top_k=100,
                            keep_pool=True, time_feature=tf)
    if source == "oracle":
        return SearchConfig(weights=w, time_source="override", time_override=oracle, top_k=100, keep_pool=True,
                            time_feature=tf)
    return SearchConfig(weights=w, time_source=source, top_k=100, keep_pool=True, time_feature=tf)


def power_n(diffs: list[float], alpha: float = 0.05, power: float = 0.8) -> dict:
    """Queries needed to detect the pre-registered 0.03 and the observed mean paired difference.

    Normal approximation, as scripts/power_calc.py (ADR-0015 §8).
    """
    from statistics import NormalDist

    sd = statistics.stdev(diffs)
    z = NormalDist().inv_cdf(1 - alpha / 2) + NormalDist().inv_cdf(power)
    mean = abs(statistics.mean(diffs))
    return {"sd": round(sd, 4), "n_for_0.03": int((z * sd / 0.03) ** 2) + 1,
            "n_for_observed": int((z * sd / mean) ** 2) + 1 if mean > 0 else None}


def damage_of(persona_dir: Path) -> dict[str, str]:
    return {r["rel_path"]: r["damage"] for r in map(
        json.loads, (persona_dir / "truth" / "truth_manifest.jsonl").read_text(encoding="utf-8").splitlines())}


def cluster_boot(rows_a: list[dict], rows_b: list[dict], rows_o: list[dict] | None = None, n: int = 2000):
    """Persona-cluster bootstrap 95% CI of mean(a - b), or of the share of oracle headroom recovered.

    With rows_o the statistic is (mean a - mean b) / (mean o - mean b). Rows are aligned by query.
    """
    import random

    by: dict[str, list[int]] = {}
    for i, r in enumerate(rows_a):
        by.setdefault(r["persona"], []).append(i)
    keys, rng, vals = list(by), random.Random(0), []
    for _ in range(n):
        idx = [i for k in (rng.choice(keys) for _ in keys) for i in by[k]]
        da = statistics.mean(rows_a[i]["ndcg10"] for i in idx) - statistics.mean(rows_b[i]["ndcg10"] for i in idx)
        if rows_o is None:
            vals.append(da)
        else:
            do = statistics.mean(rows_o[i]["ndcg10"] for i in idx) - statistics.mean(rows_b[i]["ndcg10"] for i in idx)
            if abs(do) > 1e-9:
                vals.append(da / do)
    vals.sort()
    dropped = n - len(vals)  # resamples with no oracle headroom cannot give a share
    if rows_o is not None and dropped:
        print(f"  cluster_boot: {dropped}/{n} resamples dropped (zero headroom)")
    return [round(vals[int(0.025 * len(vals))], 4), round(vals[int(0.975 * len(vals)) - 1], 4)] if vals else None


def offline_rank(pool: dict, weights: dict) -> list[str]:
    """Re-score a cached pool exactly as rank.search does."""
    paths = pool["paths"]
    score = np.zeros(len(paths))
    for f in pool["used"]:
        score += weights.get(f, 0.0) * (np.asarray(pool["x"][f]) - pool["absent"][f])
    order = sorted(range(len(paths)), key=lambda i: (-round(float(score[i]), 6), paths[i]))
    return [paths[i] for i in order][:100]


def metrics(ranked: list[str], q: dict) -> dict:
    return {"ndcg10": ndcg_at_k(ranked, q["qrels"], 10), "mrr": reciprocal_rank(ranked, q["target"]),
            "hit1": hit_at_1(ranked, q["target"]), "r10": recall_at_k(ranked, q["target"], 10)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("persona_dirs", type=Path, nargs="+")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--time-feature", choices=("expected", "overlap"), default="expected")
    parser.add_argument("--index-root", type=Path, default=None,
                        help="build/read indexes at <root>/<persona> instead of <persona>/index_v2")
    parser.add_argument("--fit", action="store_true")
    parser.add_argument("--weights", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "data" / "bench_results")
    args = parser.parse_args()

    for d in args.persona_dirs:
        fam = json.loads((d / "truth" / "persona.json").read_text(encoding="utf-8"))["persona"]["family"]
        if fam == "B" and (args.fit or not args.tag.startswith("TEST-")):
            print(f"refusing: {d.name} is a test persona (no fitting; test runs must be tagged TEST-*)")
            return 2

    global TIME_FEATURE
    TIME_FEATURE = args.time_feature
    weights = dict(SearchConfig().weights)
    if args.weights:
        weights = json.loads(args.weights.read_text(encoding="utf-8"))["weights"]
    started = time.perf_counter()
    records = []  # (query, source, pool, online_ranked)
    recon_fp: dict[str, str] = {}  # which calibrated recon_v1 rows each persona's run used
    for d in args.persona_dirs:
        idx = (args.index_root / d.name) if args.index_root else d / "index_v2"
        if not (idx / "contextfs.db").exists():
            with Engine.open(idx, mode="write") as eng:
                rep = eng.update([d / "corpus"])
                print(f"indexed {d.name}: {rep.new} files in {rep.seconds}s")
        meta = json.loads((d / "truth" / "persona.json").read_text(encoding="utf-8"))
        as_of = datetime.fromisoformat(meta["persona"]["end"]).replace(tzinfo=GENERATOR_TZ)
        oracle = oracle_override(d)
        dmg = damage_of(d)
        with sqlite3.connect(f"file:{(idx / 'contextfs.db').as_posix()}?mode=ro", uri=True) as c:
            rows = c.execute("SELECT f.rel_path, t.event, t.lo_utc, t.hi_utc, t.point_utc, t.confidence FROM file_time t "
                             "JOIN files f ON f.file_id=t.file_id WHERE t.estimator='recon_v1' ORDER BY 1, 2").fetchall()
        recon_fp[d.name] = hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]
        queries = [json.loads(x) for x in (d / "truth" / "queries_llm.jsonl").read_text(encoding="utf-8").splitlines()]
        for q in queries:
            q["persona"], q["target_damage"] = d.name, dmg.get(q["target"], "none")
        with Engine.open(idx) as eng:
            for q in queries:
                for src in SOURCES:
                    resp = eng.search(q["text"], config_for(src, weights.items(), oracle), as_of=as_of)
                    records.append((q, src, resp.pool, resp.paths))

    # Offline re-scoring must equal online search (ADR-0014 test 7).
    mismatches = sum(1 for _, _, pool, online in records if pool and offline_rank(pool, weights)[:10] != online[:10])
    print(f"offline vs online top-10 mismatches: {mismatches}/{len(records)}")

    def evaluate(w: dict, source: str) -> list[float]:
        out = []
        for q, src, pool, _ in records:
            if src == source:
                out.append(ndcg_at_k(offline_rank(pool, w) if pool else [], q["qrels"], 10))
        return out

    if args.fit:
        best = dict(weights)
        base = statistics.mean(evaluate(best, "rules_v0"))
        print(f"fit start nDCG@10 (rules_v0) {base:.4f}")
        for sweep in range(3):
            for f in FEATURES:
                for v in GRID:
                    trial = dict(best) | {f: v}
                    score = statistics.mean(evaluate(trial, "rules_v0"))
                    if score > base + 1e-6:
                        base, best = score, trial
            print(f"  sweep {sweep + 1}: nDCG@10 {base:.4f}  weights {best}")
        weights = best
        payload = {"weights": best, "fitted_on": [d.name for d in args.persona_dirs], "metric": "nDCG@10",
                   "time_source": "rules_v0", "grid": GRID, "dev_ndcg10": round(base, 4)}
        text = json.dumps(payload, indent=2, sort_keys=True)
        out = PROJECT_ROOT / "data" / "scorer" / f"weights_{args.tag}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {out} sha256 {hashlib.sha256((text + chr(10)).encode()).hexdigest()[:16]}")

    # Report every source with the (possibly fitted) weights, re-scored offline on the same pools.
    per = {s: [] for s in SOURCES}
    for q, src, pool, _ in records:
        ranked = offline_rank(pool, weights) if pool else []
        per[src].append({"cue": q["cue"], "persona": q["persona"], "target_damage": q["target_damage"],
                         **metrics(ranked, q)})
    summary = {"tag": args.tag, "personas": [d.name for d in args.persona_dirs], "weights": weights,
               "n_queries": len(per["rules_v0"]), "seconds": round(time.perf_counter() - started, 1),
               "offline_online_mismatches": mismatches, "systems": {}, "by_cue": {}, "by_cue_target": {},
               "paired": {}, "time_feature": TIME_FEATURE, "p_a2": {}, "p_a2s": {},
               "recon_v1_rows_sha256_16": recon_fp}
    for s, rows in per.items():
        summary["systems"][s] = {m: {"mean": round(statistics.mean(r[m] for r in rows), 4),
                                     "ci95": [round(v, 4) for v in bootstrap_ci([r[m] for r in rows])]}
                                 for m in ("ndcg10", "mrr", "hit1", "r10")}
    for cue in sorted({r["cue"] for r in per["rules_v0"]}):
        summary["by_cue"][cue] = {s: round(statistics.mean(r["ndcg10"] for r in rows if r["cue"] == cue), 4)
                                  for s, rows in per.items()}
        summary["by_cue"][cue]["n"] = sum(1 for r in per["rules_v0"] if r["cue"] == cue)
        # exact-target metrics per cue: "time cues defeat every system" is about these (Phase 31)
        summary["by_cue_target"][cue] = {
            s: {m: round(statistics.mean(r[m] for r in rows if r["cue"] == cue), 4) for m in ("mrr", "hit1")}
            for s, rows in per.items()}
    for a, b in (("rules_v0", "raw_mtime"), ("oracle", "raw_mtime"), ("rules_v0", "time_off"), ("oracle", "rules_v0")):
        diffs = [x["ndcg10"] - y["ndcg10"] for x, y in zip(per[a], per[b], strict=True)]
        summary["paired"][f"{a} - {b}"] = {"mean": round(statistics.mean(diffs), 4),
                                           "p": round(paired_randomisation_p(diffs), 5)}
    # P-A2 (pre-registered): nDCG@10 on time-cued queries, time sources compared, all else frozen.
    cued = {s: [r for r in rows if r["cue"] in TIME_CUED] for s, rows in per.items()}
    summary["p_a2"]["n"] = len(cued["raw_mtime"])
    for s in SOURCES:
        summary["p_a2"][s] = round(statistics.mean(r["ndcg10"] for r in cued[s]), 4)
    for a, b in (("recon_v1", "raw_mtime"), ("recon_v1", "rules_v0"), ("rules_v0", "raw_mtime"), ("oracle", "raw_mtime")):
        diffs = [x["ndcg10"] - y["ndcg10"] for x, y in zip(cued[a], cued[b], strict=True)]
        summary["p_a2"][f"{a} - {b}"] = {"mean": round(statistics.mean(diffs), 4),
                                         "p": round(paired_randomisation_p(diffs), 5),
                                         "ci95_persona": cluster_boot(cued[a], cued[b]),
                                         "power": power_n(diffs)}
    # P-A2s (pre-registered, Decision 106): time-cued queries whose target's fs times were reset;
    # share of the oracle's headroom over raw mtime recovered.
    sub = {s: [r for r in rows if r["target_damage"] in RESET_DAMAGE] for s, rows in cued.items()}
    summary["p_a2s"]["n"] = len(sub["raw_mtime"])
    if sub["raw_mtime"]:
        raw = statistics.mean(r["ndcg10"] for r in sub["raw_mtime"])
        orc = statistics.mean(r["ndcg10"] for r in sub["oracle"])
        summary["p_a2s"]["raw_mtime"], summary["p_a2s"]["oracle"] = round(raw, 4), round(orc, 4)
        for s in ("rules_v0", "recon_v1"):
            est = statistics.mean(r["ndcg10"] for r in sub[s])
            summary["p_a2s"][s] = {"ndcg10": round(est, 4),
                                   "share_of_headroom": round((est - raw) / (orc - raw), 4) if orc != raw else None,
                                   "ci95_persona": cluster_boot(sub[s], sub["raw_mtime"], sub["oracle"])}
    run = args.out / args.tag
    run.mkdir(parents=True, exist_ok=True)
    (run / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("n_queries", "weights", "offline_online_mismatches")}, indent=1))
    for s, m in summary["systems"].items():
        print(f"{s:<10} nDCG@10 {m['ndcg10']['mean']:.3f} {m['ndcg10']['ci95']}  MRR {m['mrr']['mean']:.3f}  "
              f"Hit@1 {m['hit1']['mean']:.3f}  R@10 {m['r10']['mean']:.3f}")
    print("by cue (nDCG@10):")
    for cue, row in summary["by_cue"].items():
        print(f"  {cue:<9} n={row['n']:<4} " + "  ".join(f"{s}={row[s]:.3f}" for s in SOURCES))
    print("by cue (MRR / Hit@1):")
    for cue, row in summary["by_cue_target"].items():
        print(f"  {cue:<9} " + "  ".join(f"{s}={row[s]['mrr']:.3f}/{row[s]['hit1']:.3f}" for s in SOURCES))
    print("paired:", json.dumps(summary["paired"]))
    print("P-A2 (time-cued):", json.dumps(summary["p_a2"]))
    print("P-A2s (time-cued, target reset):", json.dumps(summary["p_a2s"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
