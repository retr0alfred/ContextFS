"""Phase 14-16 & 21-22 tests: decomposition, retrieval, explanations, metrics."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from contextfs.config import load_config
from contextfs.datagen.corpus_spec import QUERIES
from contextfs.embed import Embedder, VectorStore
from contextfs.evaluation import (
    ABLATIONS,
    evaluate_system,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from contextfs.graph import load_graph
from contextfs.query import QueryDecomposer
from contextfs.retrieval import Explanation, HybridRetriever, SemanticBaseline
from contextfs.store import Store
from contextfs.temporal import TimelineIndex

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CFG = load_config(PROJECT_ROOT / "contextfs.toml")
TODAY = date(2026, 8, 12)

ML = "College/Semester7/MachineLearning"
KEY_PDF = f"{ML}/Unit4_Ensemble_Methods.pdf"


# ---------------------------------------------------------------------------
# Metrics (pure functions)
# ---------------------------------------------------------------------------


def test_precision_at_k_is_bounded_by_the_relevant_set():
    """A query with two answers must be able to reach P@10 = 1.0."""
    assert precision_at_k(["a", "b", "x", "y"], {"a", "b"}, 10) == 1.0
    assert precision_at_k(["a", "x"], {"a", "b"}, 2) == 0.5


def test_recall_at_k():
    assert recall_at_k(["a", "b"], {"a", "b", "c"}, 10) == pytest.approx(2 / 3)
    assert recall_at_k([], {"a"}, 5) == 0.0
    assert recall_at_k(["a"], set(), 5) == 0.0


def test_reciprocal_rank():
    assert reciprocal_rank(["x", "a"], {"a"}) == 0.5
    assert reciprocal_rank(["a"], {"a"}) == 1.0
    assert reciprocal_rank(["x", "y"], {"a"}) == 0.0


def test_ablation_grid_covers_every_research_question():
    answered = {spec["answers"] for spec in ABLATIONS.values()}
    assert {"RQ1", "RQ2", "RQ4", "RQ5"} <= answered
    assert "baseline" in ABLATIONS and ABLATIONS["baseline"]["flat"]
    assert ABLATIONS["full"]["signals"] == ("semantic", "graph", "activity", "timeline")


def test_the_activity_and_temporal_ablations_isolate_one_layer_each():
    """RQ1 and RQ2 must differ from their common parent by exactly one signal."""
    parent = set(ABLATIONS["semantic_graph"]["signals"])
    assert set(ABLATIONS["semantic_graph_activity"]["signals"]) - parent == {"activity"}
    assert set(ABLATIONS["semantic_graph_temporal"]["signals"]) - parent == {"timeline"}


# ---------------------------------------------------------------------------
# Explanation completeness
# ---------------------------------------------------------------------------


def test_an_empty_explanation_is_not_complete():
    assert not Explanation().is_complete


def test_an_explanation_needs_a_reason_not_just_arithmetic():
    only_numbers = Explanation(signal_scores={"semantic": 0.5}, contributions={"semantic": 0.2})
    assert not only_numbers.is_complete, "numbers alone do not explain anything"

    with_reason = Explanation(
        matched_topic=["exam"],
        signal_scores={"semantic": 0.5},
        contributions={"semantic": 0.2},
    )
    assert with_reason.is_complete


def test_explanation_reasons_are_human_readable():
    explanation = Explanation(
        matched_topic=["exam", "machine"],
        topic_similarity=0.47,
        matched_session={"label": "exam prep", "size": 7},
        matched_timeline=[{"date": "2025-11-24", "surface": "24 Nov"}],
        signal_scores={"semantic": 0.5},
        contributions={"semantic": 0.2},
    )
    reasons = explanation.reasons()
    assert any("topic match" in r for r in reasons)
    assert any("work session" in r for r in reasons)
    assert any("2025-11-24" in r for r in reasons)


# ---------------------------------------------------------------------------
# Query decomposition
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def decomposer():
    return QueryDecomposer(CFG)


@pytest.mark.slow
def test_format_hints_are_detected(decomposer):
    assert decomposer.decompose("the pdf about ensembles").format_hint == (".pdf",)
    assert ".pptx" in decomposer.decompose("the slides I presented").format_hint
    assert ".xlsx" in decomposer.decompose("the spreadsheet with my exams").format_hint


@pytest.mark.slow
def test_activity_cues_are_detected(decomposer):
    assert decomposer.decompose("the pdf I studied before my exam").has_activity
    assert decomposer.decompose("everything from the hackathon weekend").has_activity
    assert not decomposer.decompose("support vector machines and kernels").has_activity


@pytest.mark.slow
def test_temporal_expressions_are_extracted_and_resolved(decomposer):
    result = decomposer.decompose("what was due in the third week of October", TODAY)
    assert result.has_temporal
    assert "third week of october" in result.date_expression.lower()
    assert result.date_range.days == 7


@pytest.mark.slow
def test_the_longest_temporal_expression_wins(decomposer):
    """'third week of October' must beat the bare 'October' inside it."""
    result = decomposer.decompose("the third week of October 2025", TODAY)
    assert result.date_range.days == 7, result.date_range.interpretation


@pytest.mark.slow
def test_a_month_consumed_as_time_is_not_also_a_topic_term(decomposer):
    result = decomposer.decompose("deadlines I had in September", TODAY)
    assert result.has_temporal
    assert "september" not in result.topic_terms


@pytest.mark.slow
def test_topic_terms_exclude_noise(decomposer):
    terms = decomposer.decompose("the file thing about machine learning").topic_terms
    assert "file" not in terms and "thing" not in terms
    assert "machine" in terms or "learning" in terms


@pytest.mark.slow
def test_decomposition_never_raises(decomposer):
    for text in ("", "   ", "!!!", "a" * 2000, "日本語"):
        assert decomposer.decompose(text) is not None


@pytest.mark.slow
def test_every_benchmark_query_decomposes_sensibly(decomposer):
    """Phase 14's stated verification, as an assertion rather than eyeballing."""
    for spec in QUERIES:
        result = decomposer.decompose(spec.text, TODAY)
        assert (
            result.topic_terms or result.entities or result.has_temporal
        ), f"{spec.id} decomposed to nothing: {spec.text!r}"
        if spec.kind == "temporal":
            assert (
                result.has_temporal or spec.id == "q17"
            ), f"{spec.id} is a temporal query but no date was extracted"
        if spec.kind == "activity":
            assert result.has_activity, f"{spec.id} is an activity query with no cue"


# ---------------------------------------------------------------------------
# Retrieval against the real index
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def systems():
    if not CFG.db_path.is_file():
        pytest.skip("no index; run `contextfs scan` first")
    store = Store(CFG.db_path, read_only=True)
    vectors = VectorStore(CFG.vector_dir, CFG.embeddings.dimension)
    graph = load_graph(CFG.graph_file)
    timeline = TimelineIndex.from_store(store)
    embedder = Embedder(
        CFG.embeddings.model,
        expected_dimension=CFG.embeddings.dimension,
        backend=CFG.embeddings.backend,
        num_threads=CFG.embeddings.num_threads,
    )
    hybrid = HybridRetriever(store, vectors, embedder, graph, CFG, timeline_index=timeline)
    baseline = SemanticBaseline(store, vectors, embedder)
    yield hybrid, baseline, store
    store.close()


@pytest.mark.slow
def test_every_result_carries_a_complete_explanation(systems):
    """Phase 16's requirement: 100% of results, not just the top one."""
    hybrid, baseline, _ = systems
    for spec in QUERIES:
        for system in (hybrid, baseline):
            response = system.search(spec.text, 10)
            for result in response.results:
                assert result.explanation.is_complete, (
                    f"{spec.id} / {system.__class__.__name__} / {result.path}: "
                    f"{result.explanation.as_dict()}"
                )


@pytest.mark.slow
def test_explanations_are_machine_readable(systems):
    hybrid, _, _ = systems
    response = hybrid.search("the pdf I studied before my machine learning exam", 5)
    for result in response.results:
        payload = result.explanation.as_dict()
        for key in (
            "matched_topic",
            "matched_entities",
            "matched_session",
            "matched_timeline",
            "graph_path",
            "signals",
            "contributions",
        ):
            assert key in payload


@pytest.mark.slow
def test_the_adversarial_query_finds_the_key_pdf(systems):
    """q01. The PDF contains no exam vocabulary; context must find it."""
    hybrid, _, _ = systems
    response = hybrid.search("the pdf I studied before my machine learning exam", 10)
    assert KEY_PDF in response.paths, response.paths


@pytest.mark.slow
def test_the_hybrid_system_beats_the_baseline_overall(systems):
    """The project's central hypothesis, as a test."""
    hybrid, baseline, _ = systems
    hybrid_result = evaluate_system(hybrid, QUERIES, name="full")
    baseline_result = evaluate_system(baseline, QUERIES, name="baseline")

    assert hybrid_result.mrr > baseline_result.mrr, (
        f"hybrid MRR {hybrid_result.mrr:.3f} did not beat " f"baseline {baseline_result.mrr:.3f}"
    )
    assert hybrid_result.mean_recall(10) > baseline_result.mean_recall(10)


@pytest.mark.slow
def test_context_layers_do_not_degrade_semantic_or_entity_queries(systems):
    """Guards the regression that adaptive weighting was introduced to fix."""
    hybrid, baseline, _ = systems
    hybrid_kinds = evaluate_system(hybrid, QUERIES).by_kind()
    baseline_kinds = evaluate_system(baseline, QUERIES).by_kind()

    for kind in ("semantic", "entity"):
        assert hybrid_kinds[kind]["mrr"] >= baseline_kinds[kind]["mrr"] - 1e-9, (
            f"{kind} queries degraded: "
            f"{baseline_kinds[kind]['mrr']} -> {hybrid_kinds[kind]['mrr']}"
        )


@pytest.mark.slow
def test_activity_and_temporal_queries_improve(systems):
    hybrid, baseline, _ = systems
    hybrid_kinds = evaluate_system(hybrid, QUERIES).by_kind()
    baseline_kinds = evaluate_system(baseline, QUERIES).by_kind()
    for kind in ("activity", "temporal"):
        assert hybrid_kinds[kind]["mrr"] > baseline_kinds[kind]["mrr"], kind


@pytest.mark.slow
def test_disabling_a_signal_changes_the_weighting(systems):
    """The mechanism Phase 22 depends on."""
    _, _, store = systems
    vectors = VectorStore(CFG.vector_dir, CFG.embeddings.dimension)
    embedder = Embedder(CFG.embeddings.model, expected_dimension=CFG.embeddings.dimension)
    graph = load_graph(CFG.graph_file)

    limited = HybridRetriever(store, vectors, embedder, graph, CFG, signals=("semantic", "graph"))
    assert set(limited.weights) == {"semantic", "graph"}
    assert sum(limited.weights.values()) == pytest.approx(1.0)


@pytest.mark.slow
def test_adaptive_weighting_drops_signals_the_query_cannot_support(systems):
    hybrid, _, _ = systems
    entity_query = hybrid.search("documents where my supervisor Dr Murari is mentioned", 5)
    weights = entity_query.results[0].explanation.signal_weights
    assert "timeline" not in weights, "timeline weighted on a query that names no time"
    assert "activity" not in weights, "activity weighted on a query with no activity cue"
    assert sum(weights.values()) == pytest.approx(1.0)


@pytest.mark.slow
def test_a_temporal_query_keeps_its_timeline_weight(systems):
    hybrid, _, _ = systems
    response = hybrid.search("what was due in the third week of October", 5)
    weights = response.results[0].explanation.signal_weights
    assert "timeline" in weights


@pytest.mark.slow
def test_the_baseline_uses_only_the_semantic_signal(systems):
    _, baseline, _ = systems
    response = baseline.search("anything", 5)
    assert response.signals == ("semantic",)
    assert response.system == "baseline"


@pytest.mark.slow
def test_results_are_ranked_by_descending_score(systems):
    hybrid, _, _ = systems
    response = hybrid.search("database normalization with BCNF", 10)
    scores = [r.score for r in response.results]
    assert scores == sorted(scores, reverse=True)
    assert [r.rank for r in response.results] == list(range(1, len(scores) + 1))


@pytest.mark.slow
def test_a_query_matching_nothing_returns_cleanly(systems):
    hybrid, _, _ = systems
    response = hybrid.search("zzzzqqq nonexistent gibberish term", 10)
    assert isinstance(response.paths, list)
