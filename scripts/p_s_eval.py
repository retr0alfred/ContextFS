r"""P-S (ADR-0015 §7): full ContextFS vs the strongest baseline, paired per query (log.md Phase 35).

    python scripts/p_s_eval.py ENGINE_PER_QUERY BENCH_PER_QUERY --out DIR

- ContextFS: ``scripts/engine_eval.py``'s per-query nDCG@10 for the shipped default time source
  (``recon_v1``, expected-overlap feature, Decision 100 weights).
- Baseline: ``scripts/bench_eval.py --ce``'s ``ce_rerank`` (BM25+dense first stage, MiniLM
  cross-encoder rerank), the strongest baseline on dev in Phase 31.
- Split, fixed before the test run: memory-cue queries = cue in {temporal, activity, mixed, vague};
  content queries = cue in {semantic, entity, format}.
Reports means, the paired difference, a paired randomisation p and a persona-cluster bootstrap CI.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from contextfs.evalkit import paired_randomisation_p  # noqa: E402

SYSTEM, BASELINE = "recon_v1", "ce_rerank"
MEMORY = {"temporal", "activity", "mixed", "vague"}
CONTENT = {"semantic", "entity", "format"}


def boot(pairs: list[tuple[str, float]], n: int = 2000) -> list[float]:
    by: dict[str, list[float]] = {}
    for persona, d in pairs:
        by.setdefault(persona, []).append(d)
    keys, rng, vals = list(by), random.Random(0), []
    for _ in range(n):
        ds = [d for k in (rng.choice(keys) for _ in keys) for d in by[k]]
        vals.append(statistics.mean(ds))
    vals.sort()
    return [round(vals[int(0.025 * n)], 4), round(vals[int(0.975 * n) - 1], 4)]


def compare(rows: list[dict]) -> dict:
    diffs = [r["sys"] - r["base"] for r in rows]
    return {"n": len(rows), "contextfs": round(statistics.mean(r["sys"] for r in rows), 4),
            "baseline": round(statistics.mean(r["base"] for r in rows), 4),
            "diff": round(statistics.mean(diffs), 4), "p": round(paired_randomisation_p(diffs), 5),
            "ci95_persona": boot([(r["persona"], r["sys"] - r["base"]) for r in rows])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("engine_per_query", type=Path)
    parser.add_argument("bench_per_query", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    eng = {r["query_id"]: r for r in map(json.loads, args.engine_per_query.read_text(encoding="utf-8").splitlines())}
    bench = {r["query_id"]: r for r in map(json.loads, args.bench_per_query.read_text(encoding="utf-8").splitlines())}
    common = sorted(set(eng) & set(bench))
    rows = [{"persona": eng[q]["persona"], "cue": eng[q]["cue"], "sys": eng[q]["ndcg10"][SYSTEM],
             "base": bench[q]["metrics"][BASELINE]["ndcg10"]} for q in common]
    result = {"system": SYSTEM, "baseline": BASELINE, "unmatched_engine": len(set(eng) - set(bench)),
              "unmatched_bench": len(set(bench) - set(eng)), "all": compare(rows),
              "memory_cue": compare([r for r in rows if r["cue"] in MEMORY]),
              "content": compare([r for r in rows if r["cue"] in CONTENT])}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "p_s.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
