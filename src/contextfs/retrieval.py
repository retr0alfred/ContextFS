"""Layer 9 - hybrid retrieval, the semantic baseline, and explanations.

Two retrieval systems live here, and they are built together on purpose. Every
claim this project makes is comparative, so the baseline is not an afterthought
bolted on at evaluation time - it shares the same index, the same embeddings and
the same query decomposition, differing *only* in which signals it is allowed to
use. That is what makes the comparison mean anything: any difference between
them is attributable to the context layers and to nothing else.

:class:`SemanticBaseline`
    Flat nearest-neighbour search over document vectors. No graph, no sessions,
    no timeline. This is what a competent conventional system does.

:class:`HybridRetriever`
    Seed selection, graph expansion, and a weighted combination of four signals:
    semantic similarity, graph connectivity, activity context, timeline context.

Scoring
-------
::

    score = w_sem · semantic + w_graph · graph + w_act · activity + w_time · timeline

Weights come from ``[retrieval]`` in the config and are validated to sum to 1.0.
When the ablation harness disables a layer, the remaining weights are
**re-normalised** (Phase 2, Decision 11) so configurations stay on a comparable
scale - otherwise a system with fewer signals would score lower purely from
arithmetic and every ablation row would be confounded.

Explanations (Phase 16)
-----------------------
Ranking and explaining are the same pass. Every component that contributes to a
score records *why* it contributed at the moment it does so, so an explanation
cannot drift from the score it explains. Every returned result carries a
complete :class:`Explanation`; a result that cannot say why it is present is a
bug, and a test asserts 100% coverage rather than spot-checking the top hit.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Explanation",
    "RetrievalResult",
    "RetrievalResponse",
    "SemanticBaseline",
    "HybridRetriever",
    "ALL_SIGNALS",
]

#: Every signal the hybrid ranker can use. The ablation harness passes subsets.
ALL_SIGNALS = ("semantic", "graph", "activity", "timeline")


@dataclass
class Explanation:
    """Why a file was returned. Required on every result, never optional."""

    matched_topic: list[str] = field(default_factory=list)
    topic_similarity: float = 0.0
    matched_entities: list[str] = field(default_factory=list)
    matched_session: dict[str, Any] | None = None
    matched_timeline: list[dict[str, Any]] = field(default_factory=list)
    graph_path: list[dict[str, str]] = field(default_factory=list)
    seed_origin: str = ""
    format_match: bool = False
    feedback_note: str = ""
    signal_scores: dict[str, float] = field(default_factory=dict)
    signal_weights: dict[str, float] = field(default_factory=dict)
    contributions: dict[str, float] = field(default_factory=dict)

    @property
    def is_complete(self) -> bool:
        """Whether this explanation actually explains anything.

        An explanation object that exists but is empty would satisfy the letter
        of "every result carries an explanation" while defeating its purpose, so
        completeness means *at least one substantive reason*, plus the scoring
        arithmetic.
        """
        has_reason = bool(
            self.matched_topic
            or self.matched_entities
            or self.matched_session
            or self.matched_timeline
            or self.graph_path
        )
        return has_reason and bool(self.signal_scores) and bool(self.contributions)

    def reasons(self) -> list[str]:
        """Human-readable reasons, strongest first."""
        out: list[str] = []
        if self.matched_topic:
            out.append(
                f"topic match ({self.topic_similarity:.2f}): " + ", ".join(self.matched_topic[:5])
            )
        if self.matched_entities:
            out.append("shares entities: " + ", ".join(self.matched_entities[:5]))
        if self.matched_session:
            session = self.matched_session
            out.append(
                f"same work session as your query: {session['label']} " f"({session['size']} files)"
            )
        if self.matched_timeline:
            dates = ", ".join(
                f"{item['date']} ({item['surface']})" for item in self.matched_timeline[:3]
            )
            out.append(f"has a meaningful date in range: {dates}")
        if self.graph_path:
            hops = " -> ".join(f"[{hop['type']}] {hop['to_label']}" for hop in self.graph_path)
            out.append(f"reached via {hops}")
        if self.format_match:
            out.append("matches the file type you asked for")
        if self.feedback_note:
            out.append(self.feedback_note)
        return out

    def as_dict(self) -> dict[str, Any]:
        """Machine-readable form, as the explainability requirement demands."""
        return {
            "matched_topic": self.matched_topic,
            "topic_similarity": round(self.topic_similarity, 4),
            "matched_entities": self.matched_entities,
            "matched_session": self.matched_session,
            "matched_timeline": self.matched_timeline,
            "graph_path": self.graph_path,
            "seed_origin": self.seed_origin,
            "format_match": self.format_match,
            "feedback": self.feedback_note,
            "signals": {k: round(v, 4) for k, v in self.signal_scores.items()},
            "weights": self.signal_weights,
            "contributions": {k: round(v, 4) for k, v in self.contributions.items()},
            "reasons": self.reasons(),
            "complete": self.is_complete,
        }


@dataclass
class RetrievalResult:
    """One ranked file, with the reasoning that put it there."""

    file_id: int
    path: str
    score: float
    rank: int = 0
    explanation: Explanation = field(default_factory=Explanation)

    def as_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "rank": self.rank,
            "file_id": self.file_id,
            "path": self.path,
            "score": round(self.score, 4),
            "explanation": self.explanation.as_dict(),
        }


@dataclass
class RetrievalResponse:
    """A complete answer: results, how the query was read, and what it cost."""

    query: str
    results: list[RetrievalResult] = field(default_factory=list)
    decomposition: Any = None
    system: str = "hybrid"
    signals: tuple[str, ...] = ALL_SIGNALS
    weights: dict[str, float] = field(default_factory=dict)
    seeds: list[str] = field(default_factory=list)
    expanded_nodes: int = 0
    latency_ms: float = 0.0

    @property
    def paths(self) -> list[str]:
        """Result paths in rank order."""
        return [result.path for result in self.results]

    def as_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "query": self.query,
            "system": self.system,
            "signals": list(self.signals),
            "weights": self.weights,
            "seeds": self.seeds,
            "expanded_nodes": self.expanded_nodes,
            "latency_ms": round(self.latency_ms, 2),
            "decomposition": self.decomposition.as_dict() if self.decomposition else None,
            "results": [result.as_dict() for result in self.results],
        }


class SemanticBaseline:
    """Flat embedding search. The comparison system, deliberately unaugmented.

    Kept honest in two ways: it uses the *same* index and the *same* embeddings
    as the hybrid system, and it is a genuinely reasonable system rather than a
    strawman - dense retrieval over document vectors is what a good conventional
    tool does. It still produces an explanation, so baseline results can be
    inspected side by side with hybrid ones.
    """

    def __init__(self, store, vectors, embedder) -> None:
        """Bind to the shared index."""
        self.store = store
        self.vectors = vectors
        self.embedder = embedder

    def search(self, query: str, top_k: int = 10) -> RetrievalResponse:
        """Return the ``top_k`` nearest documents to the query."""
        started = time.perf_counter()
        response = RetrievalResponse(query=query, system="baseline", signals=("semantic",))

        vector = self.embedder.encode_one(query)
        hits = self.vectors.search_documents(vector, limit=top_k)

        for rank, hit in enumerate(hits, start=1):
            explanation = Explanation(
                matched_topic=[query],
                topic_similarity=float(hit["score"]),
                seed_origin="semantic nearest-neighbour",
                signal_scores={"semantic": float(hit["score"])},
                signal_weights={"semantic": 1.0},
                contributions={"semantic": float(hit["score"])},
            )
            response.results.append(
                RetrievalResult(
                    file_id=int(hit["file_id"]),
                    path=hit["rel_path"],
                    score=float(hit["score"]),
                    rank=rank,
                    explanation=explanation,
                )
            )
        response.latency_ms = (time.perf_counter() - started) * 1000
        return response


class HybridRetriever:
    """Seed selection, graph expansion, and weighted multi-signal ranking."""

    def __init__(
        self,
        store,
        vectors,
        embedder,
        graph,
        config,
        *,
        signals: tuple[str, ...] = ALL_SIGNALS,
        timeline_index=None,
        feedback=None,
    ) -> None:
        """Bind to the index and choose which signals are enabled.

        Args:
            store: SQLite metadata store.
            vectors: LanceDB vector store.
            embedder: Embedder used for the query vector.
            graph: The relationship graph, or None to disable traversal.
            config: Resolved ContextFS configuration.
            signals: Subset of :data:`ALL_SIGNALS`. The ablation harness passes
                subsets; weights are re-normalised over whatever is enabled.
            timeline_index: Timeline index for temporal seeding.
            feedback: Optional feedback store (Phase 19).
        """
        self.store = store
        self.vectors = vectors
        self.embedder = embedder
        self.graph = graph
        self.config = config
        self.signals = tuple(s for s in ALL_SIGNALS if s in signals) or ("semantic",)
        self.weights = config.retrieval.normalised(set(self.signals))
        self.timeline_index = timeline_index
        self.feedback = feedback
        self._decomposer = None

    @property
    def decomposer(self):
        """The query decomposer, built on first use."""
        if self._decomposer is None:
            from contextfs.query import QueryDecomposer

            self._decomposer = QueryDecomposer(self.config, self.timeline_index)
        return self._decomposer

    # -- main entry point --------------------------------------------------

    def search(self, query: str, top_k: int | None = None, reference=None) -> RetrievalResponse:
        """Run the full pipeline: decompose, seed, expand, score, explain."""
        started = time.perf_counter()
        top_k = top_k or self.config.retrieval.top_k
        decomposition = self.decomposer.decompose(query, reference)

        response = RetrievalResponse(
            query=query,
            system="hybrid",
            signals=self.signals,
            weights=self.weights,
            decomposition=decomposition,
        )

        query_vector = self.embedder.encode_one(query)
        context = self._gather(decomposition, query_vector)
        response.seeds = context["seed_labels"]

        candidates = self._expand(context)
        response.expanded_nodes = len(candidates)

        scored = [
            self._score(file_id, origin, context, decomposition)
            for file_id, origin in candidates.items()
        ]
        scored = [result for result in scored if result.score > 0]
        self._apply_feedback(query, scored)
        scored.sort(key=lambda result: -result.score)

        for rank, result in enumerate(scored[:top_k], start=1):
            result.rank = rank
        response.results = scored[:top_k]
        response.latency_ms = (time.perf_counter() - started) * 1000
        return response

    # -- feedback (Phase 19) -----------------------------------------------

    def _apply_feedback(self, query: str, scored: list[RetrievalResult]) -> None:
        """Nudge results the user has previously picked for this same query.

        Three constraints shape this, and they matter more than the mechanism:

        1. **It is a re-rank, not a signal.** The boost is added *after* the
           weighted signal mix and is never folded into ``contributions``, so
           the four research signals keep summing to the score they earned on
           their own. An ablation asking "what does the graph contribute?"
           must not silently be answering "graph plus whatever the user
           clicked last time".
        2. **It is bounded and saturating.** ``w / (1 + |w|)`` means the first
           pick buys half the available boost and further picks buy
           progressively less, so no amount of clicking can pin a wrong file
           to rank 1 forever. With the default 0.15 cap, feedback can reorder
           near-ties but cannot overturn a clear semantic win.
        3. **It is off unless a feedback store is supplied.** The evaluation
           harness constructs the retriever without one, so the reported
           research numbers can never be inflated by feedback recorded while
           demoing the system - which would be circular.
        """
        if self.feedback is None or not scored:
            return
        weights = self.feedback.feedback_for_query(query)
        if not weights:
            return
        cap = self.config.retrieval.feedback_max_boost
        for result in scored:
            net = weights.get(result.file_id)
            if not net:
                continue
            boost = cap * (net / (1.0 + abs(net)))
            result.score = max(0.0, result.score + boost)
            verb = "confirmed" if boost > 0 else "rejected"
            result.explanation.feedback_note = (
                f"you {verb} this for this query before ({boost:+.3f} to the score)"
            )

    # -- seeding -----------------------------------------------------------

    def _gather(self, decomposition, query_vector) -> dict[str, Any]:
        """Collect seeds and the lookup tables scoring will need.

        Seeds come from up to four independent routes, and which routes fire is
        itself diagnostic - a query that seeds only semantically is one the
        baseline could also answer.
        """
        from contextfs.graph import node_id

        context: dict[str, Any] = {
            "query_vector": query_vector,
            "seed_labels": [],
            "semantic_seeds": {},
            "entity_seeds": {},
            "timeline_seeds": {},
            "session_seeds": {},
            "paths": self.store.path_by_file_id(),
            "sessions": {},
            "session_of": self.store.session_membership(),
            "doc_similarity": {},
        }

        ids, matrix = self.vectors.document_vectors()
        if len(ids):
            similarities = matrix @ query_vector
            context["doc_similarity"] = {
                file_id: float(similarities[i]) for i, file_id in enumerate(ids)
            }
            ranked = sorted(context["doc_similarity"].items(), key=lambda kv: -kv[1])
            for file_id, score in ranked[: self.config.retrieval.max_seed_nodes]:
                if score <= 0:
                    break
                context["semantic_seeds"][file_id] = score
            context["seed_labels"].extend(
                f"semantic:{context['paths'].get(fid, fid)}"
                for fid in list(context["semantic_seeds"])[:3]
            )

        if decomposition.entities:
            index = self.store.entity_index()
            wanted = {name.lower() for name in decomposition.entity_names}
            for key, file_ids in index.items():
                _, _, normalised = key.partition(":")
                if normalised.lower() in wanted or any(
                    w in normalised.lower() for w in wanted if len(w) > 3
                ):
                    for file_id in file_ids:
                        context["entity_seeds"].setdefault(file_id, set()).add(normalised)
            context["seed_labels"].extend(
                f"entity:{name}" for name in decomposition.entity_names[:3]
            )

        if decomposition.date_range and self.timeline_index is not None:
            for node in self.timeline_index.query(decomposition.date_range):
                context["timeline_seeds"].setdefault(node.file_id, []).append(
                    {
                        "date": node.day.isoformat(),
                        "surface": node.surface,
                        "score": round(node.score, 3),
                        "reason": node.reason,
                    }
                )
            context["seed_labels"].append(f"timeline:{decomposition.date_expression}")

        for row in self.store.sessions():
            context["sessions"][row["session_id"]] = {
                "session_id": row["session_id"],
                "label": row["label"],
                "kind": row["kind"],
                "size": row["size"],
                "start": row["start_at"],
                "end": row["end_at"],
            }

        # A session becomes a seed when any of its members is already a seed.
        # This is the mechanism behind q01: the timetable seeds semantically,
        # its session seeds from it, and the lecture PDF arrives as a member.
        seeded_files = (
            set(context["semantic_seeds"])
            | set(context["entity_seeds"])
            | set(context["timeline_seeds"])
        )
        for file_id in seeded_files:
            session_id = context["session_of"].get(file_id)
            if session_id:
                context["session_seeds"].setdefault(session_id, set()).add(file_id)
        context["seed_labels"].extend(
            f"session:{context['sessions'][sid]['label']}"
            for sid in list(context["session_seeds"])[:2]
            if sid in context["sessions"]
        )

        context["seed_nodes"] = {node_id(file_id) for file_id in seeded_files}
        return context

    # -- expansion ---------------------------------------------------------

    def _expand(self, context) -> dict[int, str]:
        """Walk the graph from the seeds, collecting candidate files.

        Bounded by ``max_hops`` and ``max_expanded_nodes``. Unbounded expansion
        on a well-connected graph reaches everything, at which point the graph
        stops discriminating and only the semantic term does any work.
        """
        candidates: dict[int, str] = {}
        for file_id in context["semantic_seeds"]:
            candidates[file_id] = "semantic seed"
        for file_id in context["entity_seeds"]:
            candidates.setdefault(file_id, "entity seed")
        for file_id in context["timeline_seeds"]:
            candidates.setdefault(file_id, "timeline seed")

        if "graph" not in self.signals or self.graph is None:
            return candidates

        from contextfs.graph import session_node_id

        allowed = self._allowed_edge_types()
        frontier = set(context["seed_nodes"])
        if "activity" in self.signals:
            frontier |= {session_node_id(sid) for sid in context["session_seeds"]}
        visited = set(frontier)

        for _ in range(self.config.retrieval.max_hops):
            next_frontier: set[str] = set()
            for node in frontier:
                if not self.graph.has_node(node):
                    continue
                for _, target, data in self.graph.out_edges(node, data=True):
                    if data.get("type") not in allowed or target in visited:
                        continue
                    visited.add(target)
                    next_frontier.add(target)
                    attrs = self.graph.nodes[target]
                    if attrs.get("kind") == "file":
                        candidates.setdefault(attrs["file_id"], f"graph:{data.get('type')}")
                    if len(candidates) >= self.config.retrieval.max_expanded_nodes:
                        return candidates
            frontier = next_frontier
            if not frontier:
                break
        return candidates

    def _allowed_edge_types(self) -> set[str]:
        """Edge types this configuration may traverse.

        The mapping from signals to edge types is what lets Phase 22 disable a
        layer without rebuilding the graph.
        """
        allowed = {"semantic", "entity", "structural", "duplicate"}
        if "activity" not in self.signals:
            allowed.discard("activity")
        else:
            allowed.add("activity")
        if "timeline" in self.signals:
            allowed.add("temporal")
        return allowed

    # -- scoring and explaining --------------------------------------------

    def _score(self, file_id, origin, context, decomposition) -> RetrievalResult:
        """Score one candidate and build its explanation in the same pass."""
        explanation = Explanation(seed_origin=origin)
        scores: dict[str, float] = {}

        semantic = context["doc_similarity"].get(file_id, 0.0)
        scores["semantic"] = max(0.0, semantic)
        explanation.topic_similarity = semantic
        # Populated whenever there is *any* similarity, not only above a
        # threshold. A weak topic match is still the reason a result is
        # present, and suppressing it left 23.5% of semantic-only results with
        # an empty explanation in the first evaluation run - a silent breach of
        # the "every result explains itself" requirement that the coverage
        # metric caught. Stating "0.11" is honest; stating nothing is not.
        if semantic > 0.0:
            explanation.matched_topic = (
                decomposition.topic_terms[:6] if decomposition.topic_terms else [decomposition.text]
            )

        shared = context["entity_seeds"].get(file_id, set())
        explanation.matched_entities = sorted(shared)
        scores["graph"] = self._graph_score(file_id, context, explanation, shared)

        scores["activity"] = self._activity_score(file_id, context, decomposition, explanation)
        scores["timeline"] = self._timeline_score(file_id, context, explanation)

        if decomposition.format_hint:
            path = context["paths"].get(file_id, "")
            explanation.format_match = any(
                path.lower().endswith(ext) for ext in decomposition.format_hint
            )

        weights = self._adaptive_weights(decomposition, context)
        total = 0.0
        contributions: dict[str, float] = {}
        for signal in self.signals:
            weight = weights.get(signal, 0.0)
            value = scores.get(signal, 0.0)
            contributions[signal] = weight * value
            total += weight * value

        # A format hint is a stated constraint, applied as a bounded multiplier
        # rather than a weighted signal: "slides" should promote decks, not
        # outrank the entire topic of the query.
        if decomposition.format_hint:
            total *= (
                self.config.retrieval.format_boost
                if explanation.format_match
                else self.config.retrieval.format_penalty
            )

        # Feedback is deliberately NOT applied here. It used to be, as a hook
        # inside per-file scoring, which put it upstream of the format
        # multiplier and inside the arithmetic the explanation reports - so a
        # boosted file would have shown inflated `contributions` for signals
        # that did not earn them. Phase 19 moved it to `_apply_feedback`, after
        # scoring, where it is visibly a re-rank rather than a signal.

        explanation.signal_scores = {s: scores.get(s, 0.0) for s in self.signals}
        explanation.signal_weights = weights
        explanation.contributions = contributions

        return RetrievalResult(
            file_id=file_id,
            path=context["paths"].get(file_id, str(file_id)),
            score=min(1.0, total),
            explanation=explanation,
        )

    def _adaptive_weights(self, decomposition, context) -> dict[str, float]:
        """Drop signals the query provides no evidence for, redistributing them.

        Motivated by a measured regression rather than by theory. In the first
        full ablation run, entity-style queries got **worse** with the context
        layers on (MRR 0.750 -> 0.667), and ``semantic+graph+temporal`` (0.585)
        outscored the full system (0.566). Both had one cause: a query such as
        "documents where my supervisor is mentioned" names no time and no
        activity, yet the timeline and activity weights were still applied. Any
        file that happened to sit in a session or carry a dated neighbour picked
        up score it had not earned, pushing the genuine entity match down.

        The rule is a statement about evidence, not a tuning knob: a signal
        whose *input* is absent from the query contributes noise, so it should
        not contribute weight. Timeline weight is dropped when the query names
        no date; activity weight is dropped when the query carries no activity
        cue. Remaining weights are re-normalised, for the same reason the
        ablation re-normalises - so the score stays on one scale and
        configurations remain comparable.

        The activity condition is the query's *own* cue, not "did any seed land
        in a session". Nearly every file belongs to some session, so the latter
        is true for almost every query and gates nothing - a first attempt used
        it and moved no metric at all. What licenses the activity signal is the
        user asking an activity-shaped question.

        The chosen weights are reported in every explanation, so a result never
        hides which weighting produced it.
        """
        active = set(self.signals)
        if "timeline" in active and not decomposition.has_temporal:
            active.discard("timeline")
        if "activity" in active and not decomposition.has_activity:
            active.discard("activity")
        if not active:
            active = {"semantic"}
        _ = context
        return self.config.retrieval.normalised(active)

    def _graph_score(self, file_id, context, explanation, shared_entities) -> float:
        """Connectivity between this file and the query's seed nodes."""
        if "graph" not in self.signals or self.graph is None:
            return 0.0
        from contextfs.graph import node_id

        node = node_id(file_id)
        if not self.graph.has_node(node):
            return 0.0

        if node in context["seed_nodes"]:
            explanation.graph_path = [
                {"from": "query", "type": "seed", "to": node, "to_label": "direct match"}
            ]
            return 1.0

        best = 0.0
        allowed = self._allowed_edge_types()
        for _, target, data in self.graph.out_edges(node, data=True):
            if data.get("type") not in allowed or target not in context["seed_nodes"]:
                continue
            weight = float(data.get("weight", 0.0))
            if weight > best:
                best = weight
                label = self.graph.nodes[target].get("path") or target
                explanation.graph_path = [
                    {
                        "from": node,
                        "type": data["type"],
                        "to": target,
                        "to_label": str(label),
                    }
                ]
        if shared_entities and not explanation.graph_path:
            explanation.graph_path = [
                {
                    "from": node,
                    "type": "entity",
                    "to": "query",
                    "to_label": ", ".join(sorted(shared_entities)[:3]),
                }
            ]
            best = max(best, 0.5)
        return best

    def _activity_score(self, file_id, context, decomposition, explanation) -> float:
        """How strongly this file belongs to a work session the query implies."""
        if "activity" not in self.signals:
            return 0.0
        session_id = context["session_of"].get(file_id)
        if not session_id or session_id not in context["session_seeds"]:
            return 0.0

        session = context["sessions"].get(session_id)
        if session:
            explanation.matched_session = session

        seeded_members = len(context["session_seeds"][session_id])
        size = max(1, session["size"] if session else 1)
        # A session whose members are mostly seeds is strong evidence; one
        # touched by a single seed is weak.
        coverage = min(1.0, seeded_members / size)
        base = 0.5 + 0.5 * coverage
        # An explicit activity cue in the query ("before my exam") is what makes
        # this signal appropriate at all, so it amplifies rather than gates.
        return min(1.0, base * (1.0 + 0.4 * decomposition.activity_cue))

    def _timeline_score(self, file_id, context, explanation) -> float:
        """Whether this file carries a meaningful date inside the query's range."""
        if "timeline" not in self.signals:
            return 0.0
        hits = context["timeline_seeds"].get(file_id)
        if not hits:
            return 0.0
        explanation.matched_timeline = hits
        return min(1.0, max(item["score"] for item in hits))
