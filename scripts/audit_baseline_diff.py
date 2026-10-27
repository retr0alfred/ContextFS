"""Audit (v2-P1, log Phase 29): trace the 0.503 'baseline' vs 0.518 'semantic_only' gap.

Diffs the flat SemanticBaseline against HybridRetriever(signals=("semantic",)) query by
query, then removes each suspected methodological difference one at a time:

  A. format multiplier (format_boost / format_penalty)  -> neutralised by setting both to 1.0
  B. candidate pool (entity + timeline seeds are added even when only "semantic" is on)
  C. similarity source (LanceDB cosine search vs in-memory matrix @ query_vector)

Usage:
    python scripts/audit_baseline_diff.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contextfs.config import load_config  # noqa: E402
from contextfs.datagen.corpus_spec import QUERIES  # noqa: E402
from contextfs.embed import Embedder, VectorStore  # noqa: E402
from contextfs.evaluation import evaluate_system  # noqa: E402
from contextfs.graph import load_graph  # noqa: E402
from contextfs.retrieval import HybridRetriever, SemanticBaseline  # noqa: E402
from contextfs.store import Store  # noqa: E402
from contextfs.temporal import TimelineIndex  # noqa: E402


class _SemanticSeedsOnly(HybridRetriever):
    """semantic_only with the candidate pool restricted to the semantic top-k seeds."""

    def _expand(self, context):  # noqa: D102
        return dict.fromkeys(context["semantic_seeds"], "semantic seed")


def main() -> int:
    cfg = load_config(PROJECT_ROOT / "contextfs.toml")
    store = Store(cfg.db_path, read_only=True)
    vectors = VectorStore(cfg.vector_dir, cfg.embeddings.dimension)
    graph = load_graph(cfg.graph_file)
    timeline = TimelineIndex.from_store(store)
    emb = Embedder(
        cfg.embeddings.model,
        device=cfg.embeddings.device,
        batch_size=cfg.embeddings.batch_size,
        expected_dimension=cfg.embeddings.dimension,
        backend=cfg.embeddings.backend,
        num_threads=cfg.embeddings.num_threads,
    )
    neutral = load_config(
        PROJECT_ROOT / "contextfs.toml",
        overrides={"retrieval": {"format_boost": 1.0, "format_penalty": 1.0}},
    )

    systems = {
        "baseline (flat, LanceDB search)": SemanticBaseline(store, vectors, emb),
        "semantic_only (as published)": HybridRetriever(
            store, vectors, emb, graph, cfg, signals=("semantic",), timeline_index=timeline
        ),
        "semantic_only, format x1.0": HybridRetriever(
            store, vectors, emb, graph, neutral, signals=("semantic",), timeline_index=timeline
        ),
        "semantic_only, semantic seeds only": _SemanticSeedsOnly(
            store, vectors, emb, graph, cfg, signals=("semantic",), timeline_index=timeline
        ),
        "semantic_only, format x1.0 + seeds only": _SemanticSeedsOnly(
            store, vectors, emb, graph, neutral, signals=("semantic",), timeline_index=timeline
        ),
    }
    results = {name: evaluate_system(s, QUERIES, (1, 3, 5, 10), name=name) for name, s in systems.items()}
    print(f"{'system':<42} {'MRR':>7} {'hit@1':>7} {'R@10':>7}")
    for name, r in results.items():
        print(f"{name:<42} {r.mrr:>7.4f} {r.hit_at_1:>7.4f} {r.mean_recall(10):>7.4f}")

    base = results["baseline (flat, LanceDB search)"]
    pub = results["semantic_only (as published)"]
    print("\nPer-query differences, baseline vs semantic_only (as published):")
    for b, s in zip(base.outcomes, pub.outcomes, strict=True):
        if b.retrieved != s.retrieved:
            print(f"  {b.query_id} rr {b.rr:.3f} -> {s.rr:.3f}  {b.text!r}")
            print(f"     base: {[p.rsplit('/', 1)[-1] for p in b.retrieved[:5]]}")
            print(f"     semo: {[p.rsplit('/', 1)[-1] for p in s.retrieved[:5]]}")

    # C: is LanceDB's ranking identical to the in-memory dot-product ranking?
    ids, matrix = vectors.document_vectors()
    mismatches = 0
    for spec in QUERIES:
        q = emb.encode_one(spec.text)
        lance = [int(h["file_id"]) for h in vectors.search_documents(q, limit=10)]
        sims = matrix @ q
        mem = [ids[i] for i in sims.argsort()[::-1][:10]]
        mismatches += lance != mem
    print(f"\nC. LanceDB top-10 vs in-memory top-10 ranking mismatches: {mismatches}/{len(QUERIES)}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
