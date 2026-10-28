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
import statistics
import sys
import time
from datetime import datetime
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

SOURCES = ("time_off", "raw_mtime", "rules_v0", "oracle")
GRID = (0.0, 0.05, 0.1, 0.2, 0.3, 0.45, 0.6, 0.8, 1.0, 1.4)


def oracle_override(persona_dir: Path) -> tuple:
    out = []
    for line in (persona_dir / "truth" / "truth_manifest.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        for ev in ("authored", "arrived_here", "last_user_edit"):
            v = r["truth"].get(ev)
            if v:
                t = int(datetime.fromisoformat(v).astimezone().timestamp())
                out.append((r["rel_path"], ev, t, t + 1))
    return tuple(out)


def config_for(source: str, weights, oracle=()) -> SearchConfig:
    w = tuple(weights)
    if source == "time_off":
        return SearchConfig(weights=w, signals=tuple(f for f in FEATURES if f != "time"), top_k=100, keep_pool=True)
    if source == "oracle":
        return SearchConfig(weights=w, time_source="override", time_override=oracle, top_k=100, keep_pool=True)
    return SearchConfig(weights=w, time_source=source, top_k=100, keep_pool=True)


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
    parser.add_argument("--fit", action="store_true")
    parser.add_argument("--weights", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "data" / "bench_results")
    args = parser.parse_args()

    for d in args.persona_dirs:
        fam = json.loads((d / "truth" / "persona.json").read_text(encoding="utf-8"))["persona"]["family"]
        if fam == "B" and (args.fit or not args.tag.startswith("TEST-")):
            print(f"refusing: {d.name} is a test persona (no fitting; test runs must be tagged TEST-*)")
            return 2

    weights = dict(SearchConfig().weights)
    if args.weights:
        weights = json.loads(args.weights.read_text(encoding="utf-8"))["weights"]
    started = time.perf_counter()
    records = []  # (query, source, pool, online_ranked)
    for d in args.persona_dirs:
        idx = d / "index_v2"
        if not (idx / "contextfs.db").exists():
            with Engine.open(idx, mode="write") as eng:
                rep = eng.update([d / "corpus"])
                print(f"indexed {d.name}: {rep.new} files in {rep.seconds}s")
        meta = json.loads((d / "truth" / "persona.json").read_text(encoding="utf-8"))
        as_of = datetime.fromisoformat(meta["persona"]["end"]).astimezone()
        oracle = oracle_override(d)
        queries = [json.loads(x) for x in (d / "truth" / "queries_llm.jsonl").read_text(encoding="utf-8").splitlines()]
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
        per[src].append({"cue": q["cue"], **metrics(ranked, q)})
    summary = {"tag": args.tag, "personas": [d.name for d in args.persona_dirs], "weights": weights,
               "n_queries": len(per["rules_v0"]), "seconds": round(time.perf_counter() - started, 1),
               "offline_online_mismatches": mismatches, "systems": {}, "by_cue": {}, "paired": {}}
    for s, rows in per.items():
        summary["systems"][s] = {m: {"mean": round(statistics.mean(r[m] for r in rows), 4),
                                     "ci95": [round(v, 4) for v in bootstrap_ci([r[m] for r in rows])]}
                                 for m in ("ndcg10", "mrr", "hit1", "r10")}
    for cue in sorted({r["cue"] for r in per["rules_v0"]}):
        summary["by_cue"][cue] = {s: round(statistics.mean(r["ndcg10"] for r in rows if r["cue"] == cue), 4)
                                  for s, rows in per.items()}
        summary["by_cue"][cue]["n"] = sum(1 for r in per["rules_v0"] if r["cue"] == cue)
    for a, b in (("rules_v0", "raw_mtime"), ("oracle", "raw_mtime"), ("rules_v0", "time_off"), ("oracle", "rules_v0")):
        diffs = [x["ndcg10"] - y["ndcg10"] for x, y in zip(per[a], per[b], strict=True)]
        summary["paired"][f"{a} - {b}"] = {"mean": round(statistics.mean(diffs), 4),
                                           "p": round(paired_randomisation_p(diffs), 5)}
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
    print("paired:", json.dumps(summary["paired"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
