"""Size the test split from dev per-query variance (ADR-0015 §8).

For each (system, reference) pair, uses the SD of paired per-query nDCG@10 differences on a dev
run and computes the number of test queries needed to detect a mean difference ``--delta`` at
two-sided ``--alpha`` with ``--power`` (normal approximation, paired):

    n = ((z_{1-α/2} + z_{power}) · σ_d / Δ)²

Usage:
    python scripts/power_calc.py data/bench_results/dev-smoke-template-v1/per_query.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from statistics import NormalDist


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("per_query", type=Path)
    parser.add_argument("--delta", type=float, default=0.03)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--power", type=float, default=0.8)
    parser.add_argument("--reference", default="v1_full")
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.per_query.read_text(encoding="utf-8").splitlines()]
    systems = list(rows[0]["metrics"])
    z = NormalDist().inv_cdf(1 - args.alpha / 2) + NormalDist().inv_cdf(args.power)
    print(f"dev queries: {len(rows)}; detect ΔnDCG@10 = {args.delta} at α={args.alpha}, power={args.power}")
    print(f"{'pair':<28} {'σ_d':>7} {'n needed':>9}")
    worst = 0
    for s in systems:
        if s == args.reference:
            continue
        diffs = [r["metrics"][args.reference]["ndcg10"] - r["metrics"][s]["ndcg10"] for r in rows]
        sd = statistics.stdev(diffs)
        n = math.ceil((z * sd / args.delta) ** 2)
        worst = max(worst, n)
        print(f"{args.reference + ' vs ' + s:<28} {sd:>7.3f} {n:>9}")
    print(f"\nlargest requirement: {worst} test queries (before Holm correction across primaries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
