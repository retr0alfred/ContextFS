"""Audit (v2-P1, log Phase 29): lexical baselines and significance on the SMOKE fixture.

This is an audit diagnostic, NOT a paper result. The 40-file / 17-query corpus is
self-authored and is being demoted to a smoke fixture (v2-P3). The question here is
narrow: on the very data the published numbers come from, (a) is full-vs-baseline
distinguishable from noise, and (b) would a plain lexical baseline - which the
paper drafts claim exists ("sparse + dense") but the code never implemented - change
the picture?

Systems (all over the same index, same 17 queries, top-10, target-based MRR):
  dense_flat     flat cosine kNN over MiniLM document vectors (published "baseline")
  dense_format   dense_flat + format-hint multiplier (published "semantic_only")
  full           published full ContextFS configuration
  bm25           Okapi BM25 (k1=1.2, b=0.75) over extracted document text
  bm25_path      BM25 over extracted text + tokenised relative path
  rrf            reciprocal-rank fusion (k=60) of bm25_path and dense_flat

Statistics: exact paired sign-flip randomisation test over all 2^n sign patterns on
per-query reciprocal-rank differences (two-sided), and a 95% percentile bootstrap CI
(10,000 resamples, seed 0) on the mean difference.

Usage:
    python scripts/audit_lexical_and_stats.py
"""

from __future__ import annotations

import itertools
import math
import random
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contextfs.config import load_config  # noqa: E402
from contextfs.datagen.corpus_spec import QUERIES  # noqa: E402
from contextfs.embed import Embedder, VectorStore  # noqa: E402
from contextfs.evaluation import reciprocal_rank  # noqa: E402
from contextfs.graph import load_graph  # noqa: E402
from contextfs.retrieval import HybridRetriever, SemanticBaseline  # noqa: E402
from contextfs.store import Store  # noqa: E402
from contextfs.temporal import TimelineIndex  # noqa: E402

TOKEN = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


class BM25:
    """Okapi BM25 over pre-tokenised documents."""

    def __init__(self, docs: dict[str, list[str]], k1: float = 1.2, b: float = 0.75) -> None:
        """Index the documents."""
        self.docs, self.k1, self.b = docs, k1, b
        self.avgdl = sum(len(t) for t in docs.values()) / max(1, len(docs))
        df = Counter(term for toks in docs.values() for term in set(toks))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = {p: Counter(toks) for p, toks in docs.items()}

    def rank(self, query: str) -> list[str]:
        """Paths with a positive score, best first."""
        q = tokens(query)
        scores = {}
        for path, tf in self.tf.items():
            dl = len(self.docs[path])
            s = 0.0
            for term in q:
                f = tf.get(term, 0)
                if f:
                    s += self.idf[term] * f * (self.k1 + 1) / (
                        f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                    )
            scores[path] = s
        return [p for p, s in sorted(scores.items(), key=lambda kv: -kv[1]) if s > 0]


def rrf(*rankings: list[str], k: int = 60) -> list[str]:
    fused: dict[str, float] = {}
    for ranking in rankings:
        for position, path in enumerate(ranking, start=1):
            fused[path] = fused.get(path, 0.0) + 1.0 / (k + position)
    return [p for p, _ in sorted(fused.items(), key=lambda kv: -kv[1])]


def sign_flip_p(diffs: list[float]) -> float:
    observed = abs(sum(diffs))
    hits = total = 0
    for signs in itertools.product((1, -1), repeat=len(diffs)):
        total += 1
        hits += abs(sum(s * d for s, d in zip(signs, diffs, strict=True))) >= observed - 1e-12
    return hits / total


def bootstrap_ci(diffs: list[float], reps: int = 10_000, seed: int = 0) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(rng.choice(diffs) for _ in range(n)) / n for _ in range(reps))
    return means[int(0.025 * reps)], means[int(0.975 * reps) - 1]


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
    paths = store.path_by_file_id()
    text = {paths[d["file_id"]]: d["text"] or "" for d in store.all_documents()}
    bm25 = BM25({p: tokens(t) for p, t in text.items()})
    bm25_path = BM25({p: tokens(t) + tokens(p.replace("_", " ")) for p, t in text.items()})

    flat = SemanticBaseline(store, vectors, emb)
    fmt = HybridRetriever(store, vectors, emb, graph, cfg, signals=("semantic",), timeline_index=timeline)
    full = HybridRetriever(store, vectors, emb, graph, cfg, timeline_index=timeline)

    runs: dict[str, list[list[str]]] = {name: [] for name in
                                         ("dense_flat", "dense_format", "full", "bm25", "bm25_path", "rrf")}
    for spec in QUERIES:
        dense_all = flat.search(spec.text, len(text)).paths
        runs["dense_flat"].append(dense_all[:10])
        runs["dense_format"].append(fmt.search(spec.text, 10).paths)
        runs["full"].append(full.search(spec.text, 10).paths)
        runs["bm25"].append(bm25.rank(spec.text)[:10])
        bp = bm25_path.rank(spec.text)
        runs["bm25_path"].append(bp[:10])
        runs["rrf"].append(rrf(bp, dense_all)[:10])

    rr = {name: [reciprocal_rank(r, set(q.targets)) for r, q in zip(rs, QUERIES, strict=True)]
          for name, rs in runs.items()}
    hit1 = {name: sum(v == 1.0 for v in vals) / len(vals) for name, vals in rr.items()}
    print(f"SMOKE FIXTURE ONLY - n={len(QUERIES)} queries, 40 files. Not a paper result.\n")
    print(f"{'system':<14} {'MRR':>7} {'hit@1':>7}")
    for name, vals in rr.items():
        print(f"{name:<14} {sum(vals) / len(vals):>7.4f} {hit1[name]:>7.4f}")

    print("\nPaired comparisons on per-query RR (exact sign-flip test, two-sided; 95% bootstrap CI)")
    for a, b in [("full", "dense_flat"), ("full", "dense_format"), ("full", "rrf"),
                 ("full", "bm25_path"), ("rrf", "dense_flat"), ("dense_format", "dense_flat")]:
        diffs = [x - y for x, y in zip(rr[a], rr[b], strict=True)]
        lo, hi = bootstrap_ci(diffs)
        wins = sum(d > 0 for d in diffs)
        losses = sum(d < 0 for d in diffs)
        print(f"  {a:>12} - {b:<12} mean {sum(diffs) / len(diffs):+.4f}  CI [{lo:+.4f}, {hi:+.4f}]"
              f"  p={sign_flip_p(diffs):.4f}  W/L/T {wins}/{losses}/{len(diffs) - wins - losses}")

    print("\nPer-query RR")
    print(f"{'qid':<5} {'kind':<9}" + "".join(f"{n:>13}" for n in rr))
    for i, q in enumerate(QUERIES):
        print(f"{q.id:<5} {q.kind:<9}" + "".join(f"{rr[n][i]:>13.3f}" for n in rr))
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
