"""Phases 21-22 - the evaluation harness and ablation configurations.

One module, because an ablation *is* the harness run over several signal
configurations; separating them would duplicate the metric code and invite the
two to drift.

Metrics
-------
Precision@K, Recall@K, MRR, and query latency, computed for every configuration
over the same ground-truth query set.

* **Precision@K** uses the *relevant* set (a superset of the targets), because a
  query like "everything from the hackathon weekend" has eight correct answers
  and scoring only the two named targets would punish a system for being right.
* **Recall@K** likewise.
* **MRR** uses the *targets*, because reciprocal rank asks "how far down is the
  thing they actually wanted".

Metrics are additionally broken down by query ``kind`` (semantic / activity /
temporal / entity / hybrid). The aggregate number answers "is the full system
better"; the breakdown answers "*which layer* is doing the work", which is the
question RQ1-RQ4 actually pose. A system that improved aggregate MRR while
degrading activity queries would be a failure disguised as a success, and only
the breakdown shows it.

Honesty
-------
Nothing here estimates. Every number is produced by running both systems over
the corpus. The query set has 17 members, so per-kind cells hold 2-4 queries and
those breakdowns are **directional, not significant**. :func:`format_report`
prints that caveat next to the numbers rather than in a footnote.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ABLATIONS",
    "QueryOutcome",
    "EvalResult",
    "evaluate_system",
    "run_ablations",
    "format_report",
    "precision_at_k",
    "recall_at_k",
    "reciprocal_rank",
]

#: The ablation grid. Each entry maps a configuration name to the signal subset
#: it may use, and to the research question that row answers.
#:
#: ``baseline`` is not a signal subset - it is the separate flat-search system,
#: which is the honest comparison point because "hybrid with only the semantic
#: signal" still pays for seed selection and graph expansion.
ABLATIONS: dict[str, dict[str, Any]] = {
    "baseline": {
        "signals": ("semantic",),
        "flat": True,
        "answers": "control",
        "description": "Pure semantic retrieval. Flat nearest-neighbour, no context layers.",
    },
    "semantic_only": {
        "signals": ("semantic",),
        "flat": False,
        "answers": "control",
        "description": "Hybrid machinery, semantic signal only. Isolates pipeline overhead.",
    },
    "semantic_graph": {
        "signals": ("semantic", "graph"),
        "flat": False,
        "answers": "RQ4",
        "description": "Adds the relationship graph. RQ4: does graph-enhanced beat semantic?",
    },
    "semantic_graph_temporal": {
        "signals": ("semantic", "graph", "timeline"),
        "flat": False,
        "answers": "RQ2",
        "description": "Adds timeline, WITHOUT activity. RQ2: does temporal intelligence help?",
    },
    "semantic_graph_activity": {
        "signals": ("semantic", "graph", "activity"),
        "flat": False,
        "answers": "RQ1",
        "description": "Adds activity, WITHOUT timeline. RQ1: does activity-aware help?",
    },
    "full": {
        "signals": ("semantic", "graph", "activity", "timeline"),
        "flat": False,
        "answers": "RQ5",
        "description": "All four layers. RQ5: does contextual retrieval solve memory queries?",
    },
}


def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Fraction of the top-k results that are relevant."""
    if k <= 0:
        return 0.0
    top = retrieved[:k]
    if not top:
        return 0.0
    # Denominator is min(k, |relevant|): a query with two correct answers cannot
    # achieve P@10 = 1.0, and penalising it for that would measure the query,
    # not the system.
    denominator = min(k, len(relevant)) or 1
    return sum(1 for path in top if path in relevant) / denominator


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Fraction of relevant documents found in the top-k."""
    if not relevant:
        return 0.0
    return sum(1 for path in retrieved[:k] if path in relevant) / len(relevant)


def reciprocal_rank(retrieved: list[str], targets: set[str]) -> float:
    """1/rank of the first target, or 0.0 if no target was retrieved."""
    for position, path in enumerate(retrieved, start=1):
        if path in targets:
            return 1.0 / position
    return 0.0


@dataclass
class QueryOutcome:
    """Per-query result, kept so failures can be inspected individually."""

    query_id: str
    text: str
    kind: str
    difficulty: str
    retrieved: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    relevant: list[str] = field(default_factory=list)
    precision: dict[int, float] = field(default_factory=dict)
    recall: dict[int, float] = field(default_factory=dict)
    rr: float = 0.0
    latency_ms: float = 0.0
    target_rank: int | None = None
    explanations_complete: bool = True

    def as_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "query_id": self.query_id,
            "text": self.text,
            "kind": self.kind,
            "difficulty": self.difficulty,
            "target_rank": self.target_rank,
            "rr": round(self.rr, 4),
            "precision": {str(k): round(v, 4) for k, v in self.precision.items()},
            "recall": {str(k): round(v, 4) for k, v in self.recall.items()},
            "latency_ms": round(self.latency_ms, 2),
            "retrieved": self.retrieved,
            "targets": self.targets,
            "explanations_complete": self.explanations_complete,
        }


@dataclass
class EvalResult:
    """Aggregate metrics for one system configuration."""

    name: str
    signals: tuple[str, ...] = ()
    answers: str = ""
    outcomes: list[QueryOutcome] = field(default_factory=list)
    k_values: tuple[int, ...] = (1, 3, 5, 10)

    @property
    def mrr(self) -> float:
        """Mean reciprocal rank over all queries."""
        return statistics.mean([o.rr for o in self.outcomes]) if self.outcomes else 0.0

    def mean_precision(self, k: int) -> float:
        """Mean Precision@k."""
        return (
            statistics.mean([o.precision.get(k, 0.0) for o in self.outcomes])
            if self.outcomes
            else 0.0
        )

    def mean_recall(self, k: int) -> float:
        """Mean Recall@k."""
        return (
            statistics.mean([o.recall.get(k, 0.0) for o in self.outcomes]) if self.outcomes else 0.0
        )

    @property
    def median_latency_ms(self) -> float:
        """Median query latency."""
        return statistics.median([o.latency_ms for o in self.outcomes]) if self.outcomes else 0.0

    @property
    def hit_at_1(self) -> float:
        """Fraction of queries whose first result is a target."""
        if not self.outcomes:
            return 0.0
        return sum(1 for o in self.outcomes if o.target_rank == 1) / len(self.outcomes)

    @property
    def explanation_coverage(self) -> float:
        """Fraction of queries where every returned result explained itself."""
        if not self.outcomes:
            return 0.0
        return sum(1 for o in self.outcomes if o.explanations_complete) / len(self.outcomes)

    def by_kind(self) -> dict[str, dict[str, float]]:
        """Metrics broken down by query kind - the RQ-relevant view."""
        groups: dict[str, list[QueryOutcome]] = {}
        for outcome in self.outcomes:
            groups.setdefault(outcome.kind, []).append(outcome)
        return {
            kind: {
                "n": len(items),
                "mrr": round(statistics.mean([o.rr for o in items]), 4),
                "p@5": round(statistics.mean([o.precision.get(5, 0.0) for o in items]), 4),
                "r@10": round(statistics.mean([o.recall.get(10, 0.0) for o in items]), 4),
            }
            for kind, items in sorted(groups.items())
        }

    def by_difficulty(self) -> dict[str, dict[str, float]]:
        """Metrics broken down by easy/hard."""
        groups: dict[str, list[QueryOutcome]] = {}
        for outcome in self.outcomes:
            groups.setdefault(outcome.difficulty, []).append(outcome)
        return {
            level: {
                "n": len(items),
                "mrr": round(statistics.mean([o.rr for o in items]), 4),
            }
            for level, items in sorted(groups.items())
        }

    def summary(self) -> dict[str, Any]:
        """Flat, JSON-serialisable summary."""
        return {
            "name": self.name,
            "signals": list(self.signals),
            "answers": self.answers,
            "queries": len(self.outcomes),
            "mrr": round(self.mrr, 4),
            "hit_at_1": round(self.hit_at_1, 4),
            **{f"p@{k}": round(self.mean_precision(k), 4) for k in self.k_values},
            **{f"r@{k}": round(self.mean_recall(k), 4) for k in self.k_values},
            "median_latency_ms": round(self.median_latency_ms, 2),
            "explanation_coverage": round(self.explanation_coverage, 4),
            "by_kind": self.by_kind(),
            "by_difficulty": self.by_difficulty(),
        }


def evaluate_system(
    system, queries, k_values=(1, 3, 5, 10), name="system", answers=""
) -> EvalResult:
    """Run a retrieval system over the ground-truth query set.

    Args:
        system: Anything with ``.search(text, top_k)``.
        queries: Ground-truth query specifications.
        k_values: Cut-offs for Precision@K / Recall@K.
        name: Configuration name for the results table.
        answers: Which research question this configuration addresses.

    Returns:
        An :class:`EvalResult`.
    """
    top_k = max(k_values)
    result = EvalResult(name=name, answers=answers, k_values=tuple(k_values))

    for spec in queries:
        started = time.perf_counter()
        response = system.search(spec.text, top_k)
        elapsed = (time.perf_counter() - started) * 1000

        retrieved = response.paths
        targets = set(spec.targets)
        relevant = set(spec.relevant)

        target_rank = next(
            (i for i, path in enumerate(retrieved, start=1) if path in targets), None
        )
        complete = all(r.explanation.is_complete for r in response.results)

        result.signals = response.signals
        result.outcomes.append(
            QueryOutcome(
                query_id=spec.id,
                text=spec.text,
                kind=spec.kind,
                difficulty=spec.difficulty,
                retrieved=retrieved,
                targets=sorted(targets),
                relevant=sorted(relevant),
                precision={k: precision_at_k(retrieved, relevant, k) for k in k_values},
                recall={k: recall_at_k(retrieved, relevant, k) for k in k_values},
                rr=reciprocal_rank(retrieved, targets),
                latency_ms=elapsed,
                target_rank=target_rank,
                explanations_complete=complete,
            )
        )
    return result


def run_ablations(
    build_system, queries, k_values=(1, 3, 5, 10), only=None
) -> dict[str, EvalResult]:
    """Run every ablation configuration.

    Args:
        build_system: ``(name, spec) -> system``, called once per configuration.
        queries: Ground-truth query specifications.
        k_values: Metric cut-offs.
        only: Restrict to these configuration names.

    Returns:
        ``{configuration_name: EvalResult}``.
    """
    results: dict[str, EvalResult] = {}
    for name, spec in ABLATIONS.items():
        if only and name not in only:
            continue
        system = build_system(name, spec)
        results[name] = evaluate_system(
            system, queries, k_values, name=name, answers=spec["answers"]
        )
    return results


def format_report(results: dict[str, EvalResult], k_values=(1, 3, 5, 10)) -> str:
    """Render the comparison table and a plain-language summary."""
    lines: list[str] = []
    add = lines.append

    add("=" * 100)
    add("RETRIEVAL EVALUATION - all configurations, same index, same query set")
    add("=" * 100)

    header = (
        f"{'configuration':<26} {'RQ':<8} {'MRR':>7} {'hit@1':>7} "
        + " ".join(f"{'P@' + str(k):>7}" for k in k_values)
        + " "
        + " ".join(f"{'R@' + str(k):>7}" for k in k_values)
        + f" {'ms':>8}"
    )
    add(header)
    add("-" * len(header))

    for name, result in results.items():
        add(
            f"{name:<26} {result.answers:<8} {result.mrr:>7.3f} {result.hit_at_1:>7.3f} "
            + " ".join(f"{result.mean_precision(k):>7.3f}" for k in k_values)
            + " "
            + " ".join(f"{result.mean_recall(k):>7.3f}" for k in k_values)
            + f" {result.median_latency_ms:>8.1f}"
        )

    # -- per-kind breakdown ------------------------------------------------
    add("")
    add("MRR BY QUERY KIND  (this is the RQ-relevant view)")
    kinds = sorted({kind for r in results.values() for kind in r.by_kind()})
    add(f"{'configuration':<26} " + " ".join(f"{kind:>11}" for kind in kinds))
    add("-" * (26 + 12 * len(kinds)))
    for name, result in results.items():
        breakdown = result.by_kind()
        add(
            f"{name:<26} "
            + " ".join(f"{breakdown.get(kind, {}).get('mrr', 0.0):>11.3f}" for kind in kinds)
        )
    counts = next(iter(results.values())).by_kind() if results else {}
    add(
        f"{'(queries per kind)':<26} "
        + " ".join(f"{counts.get(kind, {}).get('n', 0):>11}" for kind in kinds)
    )

    # -- deltas against the baseline ---------------------------------------
    if "baseline" in results and "full" in results:
        base, full = results["baseline"], results["full"]
        add("")
        add("FULL SYSTEM vs BASELINE")
        add(f"  MRR      {base.mrr:.3f} -> {full.mrr:.3f}   ({full.mrr - base.mrr:+.3f})")
        add(
            f"  hit@1    {base.hit_at_1:.3f} -> {full.hit_at_1:.3f}   "
            f"({full.hit_at_1 - base.hit_at_1:+.3f})"
        )
        for k in k_values:
            add(
                f"  R@{k:<6} {base.mean_recall(k):.3f} -> {full.mean_recall(k):.3f}   "
                f"({full.mean_recall(k) - base.mean_recall(k):+.3f})"
            )
        add("")
        add("  by query kind (MRR):")
        base_kinds, full_kinds = base.by_kind(), full.by_kind()
        for kind in kinds:
            b = base_kinds.get(kind, {}).get("mrr", 0.0)
            f = full_kinds.get(kind, {}).get("mrr", 0.0)
            marker = "  <-- " + ("improved" if f > b else "degraded" if f < b else "unchanged")
            add(f"    {kind:<12} {b:.3f} -> {f:.3f}  ({f - b:+.3f}){marker}")

    add("")
    add("EXPLANATION COVERAGE (Phase 16 requirement: 100% of results, not just top-1)")
    for name, result in results.items():
        add(f"  {name:<26} {result.explanation_coverage:.1%}")

    add("")
    add("-" * 100)
    n = len(next(iter(results.values())).outcomes) if results else 0
    add(f"Sample size: {n} queries over 40 files.")
    add("Per-kind cells hold 2-4 queries each. Those breakdowns are DIRECTIONAL,")
    add("not statistically significant. No significance test is reported because")
    add("none would be honest at this n. See log.md, Phase 21.")
    return "\n".join(lines)
