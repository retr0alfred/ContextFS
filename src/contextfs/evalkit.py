"""Evaluation kit for the v2 benchmark: graded metrics, statistics, lexical scoring, fusion.

Used by ``scripts/bench_eval.py``. Nothing here reads truth files except through the arguments it
is given, and nothing here is imported by the engine.

Metrics:
- nDCG@k with graded gains (2^rel − 1);
- MRR on the target (relevance 3);
- Hit@1;
- Recall@k on the target.

Statistics:
- percentile bootstrap CIs (seeded);
- paired sign-flip randomisation tests (exact for n ≤ 20, else 20,000 Monte Carlo draws, seeded);
- Holm correction.
"""

from __future__ import annotations

import itertools
import math
import random
import re
from collections import Counter

__all__ = [
    "ndcg_at_k",
    "reciprocal_rank",
    "hit_at_1",
    "recall_at_k",
    "bootstrap_ci",
    "paired_randomisation_p",
    "holm",
    "tokenize",
    "BM25",
    "rrf",
    "zscore_fuse",
]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def ndcg_at_k(ranked: list[str], qrels: dict[str, int], k: int = 10) -> float:
    """Normalised DCG with graded gains 2^rel − 1."""
    dcg = sum((2 ** qrels.get(doc, 0) - 1) / math.log2(i + 2) for i, doc in enumerate(ranked[:k]))
    ideal = sorted(qrels.values(), reverse=True)[:k]
    idcg = sum((2**rel - 1) / math.log2(i + 2) for i, rel in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0


def reciprocal_rank(ranked: list[str], target: str) -> float:
    """1/rank of the target, or 0 if absent."""
    for i, doc in enumerate(ranked, start=1):
        if doc == target:
            return 1.0 / i
    return 0.0


def hit_at_1(ranked: list[str], target: str) -> float:
    """1 if the target is ranked first."""
    return 1.0 if ranked[:1] == [target] else 0.0


def recall_at_k(ranked: list[str], target: str, k: int = 10) -> float:
    """1 if the target is within the top k."""
    return 1.0 if target in ranked[:k] else 0.0


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def bootstrap_ci(values: list[float], reps: int = 10_000, seed: int = 0, alpha: float = 0.05):
    """Percentile bootstrap CI of the mean."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choice(values) for _ in range(n)) / n for _ in range(reps))
    return means[int(alpha / 2 * reps)], means[int((1 - alpha / 2) * reps) - 1]


def paired_randomisation_p(diffs: list[float], draws: int = 20_000, seed: int = 0) -> float:
    """Two-sided paired sign-flip test on per-query differences."""
    n = len(diffs)
    if n == 0:
        return 1.0
    observed = abs(sum(diffs))
    if n <= 20:
        hits = sum(
            abs(sum(s * d for s, d in zip(signs, diffs, strict=True))) >= observed - 1e-12
            for signs in itertools.product((1, -1), repeat=n)
        )
        return hits / 2**n
    rng = random.Random(seed)
    hits = sum(
        abs(sum(d if rng.random() < 0.5 else -d for d in diffs)) >= observed - 1e-12
        for _ in range(draws)
    )
    return (hits + 1) / (draws + 1)


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm–Bonferroni adjusted p-values."""
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    out, running = {}, 0.0
    for i, (name, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        out[name] = running
    return out


# ---------------------------------------------------------------------------
# Lexical scoring and fusion
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lower-cased alphanumeric tokens."""
    return _TOKEN.findall(text.lower())


class BM25:
    """Okapi BM25 over pre-tokenised documents, with tunable k1 and b (ADR-0008 Rev. 1)."""

    def __init__(self, docs: dict[str, list[str]], k1: float = 1.2, b: float = 0.75) -> None:
        """Index ``{doc_id: tokens}``."""
        self.k1, self.b = k1, b
        self.len = {d: len(t) for d, t in docs.items()}
        self.avgdl = sum(self.len.values()) / max(1, len(docs))
        self.tf = {d: Counter(t) for d, t in docs.items()}
        df = Counter(term for t in docs.values() for term in set(t))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.postings: dict[str, list[str]] = {}
        for d, t in docs.items():
            for term in set(t):
                self.postings.setdefault(term, []).append(d)

    def scores(self, query: str) -> dict[str, float]:
        """BM25 score for every document sharing at least one query term."""
        out: dict[str, float] = {}
        for term in set(tokenize(query)):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for d in self.postings[term]:
                f = self.tf[d][term]
                norm = f + self.k1 * (1 - self.b + self.b * self.len[d] / self.avgdl)
                out[d] = out.get(d, 0.0) + idf * f * (self.k1 + 1) / norm
        return out


def rrf(rankings: list[list[str]], k: int = 60) -> list[str]:
    """Reciprocal-rank fusion."""
    fused: dict[str, float] = {}
    for ranking in rankings:
        for i, d in enumerate(ranking, start=1):
            fused[d] = fused.get(d, 0.0) + 1.0 / (k + i)
    return [d for d, _ in sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))]


def zscore_fuse(score_maps: list[dict[str, float]], weights: list[float]) -> list[str]:
    """Weighted sum of per-query z-normalised scores; a missing score counts as the minimum."""
    fused: dict[str, float] = {}
    docs = set().union(*[set(s) for s in score_maps]) if score_maps else set()
    for scores, w in zip(score_maps, weights, strict=True):
        if not scores:
            continue
        vals = list(scores.values())
        mu = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals)) or 1.0
        floor = (min(vals) - mu) / sd
        for d in docs:
            z = (scores[d] - mu) / sd if d in scores else floor
            fused[d] = fused.get(d, 0.0) + w * z
    return [d for d, _ in sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))]
