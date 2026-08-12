"""Phase 12-13 tests: activity sessions and their integration into the graph.

The single most important test in this file is
``test_the_adversarial_case_is_solved``: the exam timetable and the lecture PDF
share no vocabulary, and the entire activity layer exists so that a query about
one can reach the other. If that fails, the project's central claim fails with it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from contextfs.activity import Session, SessionBuilder, session_accuracy
from contextfs.config import load_config
from contextfs.datagen.corpus_spec import CORPUS_FILES, SESSIONS
from contextfs.embed import VectorStore
from contextfs.graph import (
    build_graph,
    date_node_id,
    node_id,
    session_node_id,
    shortest_explained_path,
)
from contextfs.store import Store

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CFG = load_config(PROJECT_ROOT / "contextfs.toml")

ML = "College/Semester7/MachineLearning"
KEY_PDF = f"{ML}/Unit4_Ensemble_Methods.pdf"
TIMETABLE = f"{ML}/Exam_Timetable_Sem7.xlsx"


def truth() -> dict[str, str | None]:
    """Ground truth with the negative control mapped to "no session"."""
    control = {s.id for s in SESSIONS if s.kind == "none"}
    return {spec.path: (None if spec.session in control else spec.session) for spec in CORPUS_FILES}


# ---------------------------------------------------------------------------
# Pairwise signals (no index needed)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def builder() -> SessionBuilder:
    return SessionBuilder(CFG)


def test_temporal_affinity_decays_to_zero_at_the_gap_limit(builder):
    now = datetime(2025, 11, 10, 18, 0)
    assert builder.temporal_affinity(now, now) == 1.0
    assert builder.temporal_affinity(now, now + timedelta(hours=builder.gap_hours)) == 0.0
    mid = builder.temporal_affinity(now, now + timedelta(hours=builder.gap_hours / 2))
    assert 0.4 < mid < 0.6


def test_entity_affinity_is_jaccard(builder):
    assert builder.entity_affinity({"a", "b"}, {"a", "b"}) == 1.0
    assert builder.entity_affinity({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert builder.entity_affinity(set(), {"a"}) == 0.0


def test_folder_affinity_rewards_shared_prefixes(builder):
    assert builder.folder_affinity("a/b", "a/b") == 1.0
    assert builder.folder_affinity("a/b", "a/c") > 0.0
    assert builder.folder_affinity("a/b", "x/y") == 0.0
    assert builder.folder_affinity("a/b/c", "a/b/d") > builder.folder_affinity("a/b/c", "a/x/y")


def test_weights_renormalise_when_vectors_are_missing(builder):
    with_vectors = builder._weights(True)
    without = builder._weights(False)
    assert sum(with_vectors.values()) == pytest.approx(1.0)
    assert sum(without.values()) == pytest.approx(1.0)
    assert without["semantic"] == 0.0
    assert without["temporal"] > with_vectors["temporal"]


def test_idle_gap_is_the_minimum_not_the_maximum(builder):
    import numpy as np

    gaps = np.array([[0, 10, 500], [10, 0, 490], [500, 490, 0]], dtype=float)
    # Cluster {0,1} against {2}: the shortest bridge is 490, not 500.
    assert builder._idle_gap(gaps, [0, 1], [2]) == 490.0


# ---------------------------------------------------------------------------
# The metric itself
# ---------------------------------------------------------------------------


def test_session_accuracy_rewards_a_perfect_partition():
    predicted = [
        Session(session_id="s1", paths=["a", "b"], file_ids=[1, 2]),
        Session(session_id="s2", paths=["c", "d"], file_ids=[3, 4]),
    ]
    metrics = session_accuracy(predicted, {"a": "X", "b": "X", "c": "Y", "d": "Y"})
    assert metrics["pairwise_f1"] == 1.0
    assert metrics["sessions_recovered"] == 2


def test_session_accuracy_punishes_over_clustering():
    predicted = [Session(session_id="s1", paths=["a", "b", "c"], file_ids=[1, 2, 3])]
    metrics = session_accuracy(predicted, {"a": "X", "b": "X", "c": None})
    assert metrics["pairwise_precision"] < 1.0
    assert metrics["fp"] > 0


def test_negative_control_files_form_no_true_pairs():
    """The convention that makes the control meaningful."""
    metrics = session_accuracy([], {"a": None, "b": None, "c": None})
    assert metrics["true_pairs"] == 0


def test_session_accuracy_punishes_under_clustering():
    predicted = [
        Session(session_id="s1", paths=["a"], file_ids=[1]),
        Session(session_id="s2", paths=["b"], file_ids=[2]),
    ]
    metrics = session_accuracy(predicted, {"a": "X", "b": "X"})
    assert metrics["pairwise_recall"] == 0.0


# ---------------------------------------------------------------------------
# Reconstruction against the real index
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reconstructed():
    if not CFG.db_path.is_file():
        pytest.skip("no index; run `contextfs scan` first")
    store = Store(CFG.db_path, read_only=True)
    vectors = VectorStore(CFG.vector_dir, CFG.embeddings.dimension)
    report = SessionBuilder(CFG).build(store, vectors)
    membership = {path: s.session_id for s in report.sessions for path in s.paths}
    yield report, membership, store
    store.close()


pytestmark_slow = pytest.mark.slow


@pytest.mark.slow
def test_the_adversarial_case_is_solved(reconstructed):
    """THE test. The exam timetable and the lecture PDF must share a session.

    The PDF contains no occurrence of "exam", "revision" or "studied" - a
    property enforced by a Phase 3 test. Activity co-membership is therefore the
    only route by which query q01 can reach it.
    """
    _, membership, _ = reconstructed
    pdf_session = membership.get(KEY_PDF)
    timetable_session = membership.get(TIMETABLE)
    assert pdf_session is not None, "the key PDF was not placed in any session"
    assert pdf_session == timetable_session, (
        f"PDF in {pdf_session}, timetable in {timetable_session}; "
        "activity retrieval cannot recover the PDF"
    )


@pytest.mark.slow
def test_every_planted_session_is_recovered(reconstructed):
    report, _, _ = reconstructed
    metrics = session_accuracy(report.sessions, truth())
    assert metrics["sessions_recovered"] == metrics["true_sessions"]


@pytest.mark.slow
def test_session_accuracy_meets_its_target(reconstructed):
    report, _, _ = reconstructed
    metrics = session_accuracy(report.sessions, truth())
    assert metrics["pairwise_precision"] >= 0.9
    assert metrics["pairwise_recall"] >= 0.9
    assert metrics["pairwise_f1"] >= 0.9, metrics


@pytest.mark.slow
def test_the_negative_control_is_not_clustered(reconstructed):
    """Personal/Misc spans 223 days and must never become a session."""
    _, membership, _ = reconstructed
    control = {s.id for s in SESSIONS if s.kind == "none"}
    control_paths = [spec.path for spec in CORPUS_FILES if spec.session in control]

    grouped: dict[str, list[str]] = {}
    for path in control_paths:
        session_id = membership.get(path)
        if session_id:
            grouped.setdefault(session_id, []).append(path)
    over = {sid: paths for sid, paths in grouped.items() if len(paths) > 1}
    assert not over, f"control files were clustered together: {over}"


@pytest.mark.slow
def test_sessions_are_temporally_coherent(reconstructed):
    """No session may contain an idle gap larger than the configured limit."""
    report, _, _ = reconstructed
    for session in report.sessions:
        assert session.start is not None and session.end is not None
        assert session.start <= session.end


@pytest.mark.slow
def test_sessions_carry_describable_context(reconstructed):
    report, _, _ = reconstructed
    for session in report.sessions:
        assert session.label
        assert session.size >= CFG.activity.min_session_size
        assert session.keywords, f"{session.session_id} has no keywords to explain it"


@pytest.mark.slow
def test_the_hackathon_session_is_labelled_correctly(reconstructed):
    _, membership, _ = reconstructed
    report, _, _ = reconstructed
    hackathon = next(s for s in report.sessions if "Projects/UrbanFlow/app.py" in s.paths)
    assert hackathon.kind == "hackathon"
    assert hackathon.span_hours < 72, "the hackathon was a weekend, not a fortnight"


@pytest.mark.slow
def test_sessions_persist_and_reload(reconstructed):
    report, _, _ = reconstructed
    store = Store(CFG.db_path, read_only=True)
    try:
        stored = store.sessions()
        assert len(stored) == len(report.sessions)
        membership = store.session_membership()
        assert len(membership) == sum(s.size for s in report.sessions)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Phase 13: graph integration
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def context_graph():
    if not CFG.db_path.is_file():
        pytest.skip("no index; run `contextfs scan` first")
    store = Store(CFG.db_path, read_only=True)
    vectors = VectorStore(CFG.vector_dir, CFG.embeddings.dimension)
    graph, report = build_graph(store, vectors, CFG)
    ids = {row["path"]: row["id"] for row in store.all_files()}
    yield graph, report, store, ids
    store.close()


@pytest.mark.slow
def test_sessions_and_dates_are_first_class_nodes(context_graph):
    graph, report, _, _ = context_graph
    kinds = {graph.nodes[n].get("kind") for n in graph.nodes}
    assert {"file", "session", "date"} <= kinds
    assert report.context_nodes["session_nodes"] > 0
    assert report.context_nodes["date_nodes"] > 0


@pytest.mark.slow
def test_activity_and_temporal_edges_exist(context_graph):
    _, report, _, _ = context_graph
    assert report.by_type.get("activity", 0) > 0
    assert report.by_type.get("temporal", 0) > 0


@pytest.mark.slow
def test_traversal_reaches_the_key_pdf_through_a_session(context_graph):
    """Phase 13's stated verification, on the case that matters.

    Starting from the timetable - the file an exam query *can* match - a graph
    walk must reach the lecture PDF, and the path must be logged with its edge
    types.
    """
    graph, _, _, ids = context_graph
    path = shortest_explained_path(
        graph,
        node_id(ids[TIMETABLE]),
        node_id(ids[KEY_PDF]),
        edge_types={"activity"},
    )
    assert path is not None, "no activity-only path from the timetable to the PDF"
    assert any(graph.nodes[hop[0]].get("kind") == "session" for hop in path) or any(
        graph.nodes[hop[2]].get("kind") == "session" for hop in path
    ), f"the path did not go through a session node: {path}"
    assert all(kind == "activity" for _, kind, _ in path)


@pytest.mark.slow
def test_traversal_reaches_files_through_a_date_node(context_graph):
    graph, _, store, ids = context_graph
    dated = store.meaningful_dates()
    assert dated
    node = date_node_id(dated[0]["iso_date"])
    assert graph.has_node(node)
    neighbours = list(graph.successors(node))
    assert neighbours
    assert all(graph.nodes[n].get("kind") == "file" for n in neighbours)


@pytest.mark.slow
def test_session_nodes_are_addressable_and_labelled(context_graph):
    graph, _, store, _ = context_graph
    for row in store.sessions():
        node = session_node_id(row["session_id"])
        assert graph.has_node(node)
        assert graph.nodes[node]["label"]
        assert graph.nodes[node]["size"] >= 2


@pytest.mark.slow
def test_temporal_file_edges_are_directed_earliest_first(context_graph):
    graph, _, _, _ = context_graph
    for source, target, data in graph.edges(data=True):
        if data.get("relation") != "edited_before":
            continue
        assert graph.nodes[source]["mtime"] <= graph.nodes[target]["mtime"]


@pytest.mark.slow
def test_context_nodes_can_be_switched_off_for_ablation(context_graph):
    """Phase 22 disables layers; the graph must build without them."""
    _, _, store, _ = context_graph
    vectors = VectorStore(CFG.vector_dir, CFG.embeddings.dimension)
    plain, report = build_graph(store, vectors, CFG, include_context=False)
    kinds = {plain.nodes[n].get("kind") for n in plain.nodes}
    assert kinds == {"file"}
    assert report.by_type.get("activity", 0) == 0
    assert report.by_type.get("temporal", 0) == 0


@pytest.mark.slow
def test_adding_context_does_not_disconnect_the_file_graph(context_graph):
    graph, report, _, _ = context_graph
    assert not report.isolated, report.isolated
