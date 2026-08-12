"""Run the full retrieval evaluation and ablation study.

Produces the comparison table for Phases 21 and 22, plus per-query detail and
machine-readable CSV/JSON output.

Usage:
    python scripts/evaluate.py
    python scripts/evaluate.py --queries          # per-query detail
    python scripts/evaluate.py --only baseline,full
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contextfs.config import load_config  # noqa: E402
from contextfs.datagen.corpus_spec import QUERIES  # noqa: E402
from contextfs.embed import Embedder, VectorStore  # noqa: E402
from contextfs.evaluation import (  # noqa: E402
    ABLATIONS,
    format_report,
    run_ablations,
)
from contextfs.graph import load_graph  # noqa: E402
from contextfs.retrieval import HybridRetriever, SemanticBaseline  # noqa: E402
from contextfs.store import Store  # noqa: E402
from contextfs.temporal import TimelineIndex  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--queries", action="store_true", help="print per-query detail")
    parser.add_argument("--only", type=str, default="", help="comma-separated configurations")
    parser.add_argument("--out", type=Path, default=None, help="output directory")
    args = parser.parse_args()

    cfg = load_config(args.config or (PROJECT_ROOT / "contextfs.toml"))
    if not cfg.db_path.is_file():
        print(f"no index at {cfg.db_path}; run `contextfs scan` first")
        return 1

    only = {name.strip() for name in args.only.split(",") if name.strip()} or None
    out_dir = args.out or cfg.eval.results_dir

    store = Store(cfg.db_path, read_only=True)
    vectors = VectorStore(cfg.vector_dir, cfg.embeddings.dimension)
    graph = load_graph(cfg.graph_file)
    timeline = TimelineIndex.from_store(store)
    embedder = Embedder(
        cfg.embeddings.model,
        device=cfg.embeddings.device,
        batch_size=cfg.embeddings.batch_size,
        expected_dimension=cfg.embeddings.dimension,
        backend=cfg.embeddings.backend,
        num_threads=cfg.embeddings.num_threads,
    )

    # Warm the models BEFORE timing anything. Cold-start import and model load
    # cost ~10 s on this machine (log.md, Phase 7) and would otherwise be
    # charged entirely to whichever query happened to run first, making the
    # latency column meaningless.
    warm_started = time.perf_counter()
    embedder.encode_one("warm up the encoder")
    warm = HybridRetriever(store, vectors, embedder, graph, cfg, timeline_index=timeline)
    warm.decomposer.decompose("warm up the decomposer")
    warmup_ms = (time.perf_counter() - warm_started) * 1000
    print(f"model warm-up: {warmup_ms:.0f} ms (excluded from query latency)\n")

    def build(name, spec):
        if spec["flat"]:
            return SemanticBaseline(store, vectors, embedder)
        return HybridRetriever(
            store,
            vectors,
            embedder,
            graph,
            cfg,
            signals=spec["signals"],
            timeline_index=timeline,
        )

    results = run_ablations(build, QUERIES, tuple(cfg.eval.k_values), only)
    print(format_report(results, tuple(cfg.eval.k_values)))

    print("\n" + "=" * 100)
    print("ABLATION -> RESEARCH QUESTION MAPPING")
    print("=" * 100)
    for name, spec in ABLATIONS.items():
        if only and name not in only:
            continue
        print(f"  {name:<26} {spec['answers']:<8} {spec['description']}")

    if args.queries:
        print("\n" + "=" * 100)
        print("PER-QUERY DETAIL (baseline vs full)")
        print("=" * 100)
        base = results.get("baseline")
        full = results.get("full")
        if base and full:
            for b, f in zip(base.outcomes, full.outcomes, strict=False):
                arrow = "  " if f.rr == b.rr else (" +" if f.rr > b.rr else " -")
                print(
                    f"\n{arrow} {b.query_id} [{b.kind}/{b.difficulty}]  "
                    f"RR {b.rr:.3f} -> {f.rr:.3f}   rank {b.target_rank} -> {f.target_rank}"
                )
                print(f"    {b.text!r}")
                print(f"    want: {b.targets}")
                print(f"    base: {b.retrieved[:3]}")
                print(f"    full: {f.retrieved[:3]}")

    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {name: result.summary() for name, result in results.items()}
    (out_dir / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    with open(out_dir / "results.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        k_values = tuple(cfg.eval.k_values)
        writer.writerow(
            ["configuration", "answers", "queries", "mrr", "hit@1"]
            + [f"p@{k}" for k in k_values]
            + [f"r@{k}" for k in k_values]
            + ["median_latency_ms", "explanation_coverage"]
        )
        for name, result in results.items():
            writer.writerow(
                [
                    name,
                    result.answers,
                    len(result.outcomes),
                    round(result.mrr, 4),
                    round(result.hit_at_1, 4),
                ]
                + [round(result.mean_precision(k), 4) for k in k_values]
                + [round(result.mean_recall(k), 4) for k in k_values]
                + [round(result.median_latency_ms, 2), round(result.explanation_coverage, 4)]
            )

    with open(out_dir / "per_query.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "configuration",
                "query_id",
                "kind",
                "difficulty",
                "rr",
                "target_rank",
                "p@5",
                "r@10",
                "latency_ms",
                "text",
            ]
        )
        for name, result in results.items():
            for outcome in result.outcomes:
                writer.writerow(
                    [
                        name,
                        outcome.query_id,
                        outcome.kind,
                        outcome.difficulty,
                        round(outcome.rr, 4),
                        outcome.target_rank,
                        round(outcome.precision.get(5, 0.0), 4),
                        round(outcome.recall.get(10, 0.0), 4),
                        round(outcome.latency_ms, 2),
                        outcome.text,
                    ]
                )

    print(f"\nwrote {out_dir / 'results.json'}")
    print(f"wrote {out_dir / 'results.csv'}")
    print(f"wrote {out_dir / 'per_query.csv'}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
