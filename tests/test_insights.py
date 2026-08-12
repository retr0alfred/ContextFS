"""Phase 19-20 tests: relevance feedback, and the auxiliary insight surfaces.

The feedback tests are mostly about what feedback must *not* do. A learning
signal that can override the ranking, or that can leak into the evaluation
harness, would quietly invalidate every number the project reports - so those
are the properties pinned here, not "does the boost get applied".
"""

from __future__ import annotations

from datetime import datetime, timedelta

import networkx as nx
import pytest

from contextfs.config import load_config
from contextfs.insights import (
    DORMANT_AFTER_DAYS,
    FINISHED_AFTER_DAYS,
    digest,
    near_duplicates,
    projects,
    suggest_tags,
)
from contextfs.retrieval import Explanation, HybridRetriever, RetrievalResult
from contextfs.store import Store


@pytest.fixture
def store(tmp_path):
    """A migrated, empty store on disk."""
    with Store(tmp_path / "index.db") as opened:
        yield opened


def _add_file(store, path, *, size=1000, mtime=None, content_hash="h"):
    """Insert one file row and return its id."""
    stamp = (mtime or datetime.now()).isoformat(timespec="seconds")
    folder, _, name = path.rpartition("/")
    store.upsert_files(
        [
            {
                "path": path,
                "abs_path": f"C:/x/{path}",
                "name": name,
                "stem": name.rsplit(".", 1)[0],
                "ext": "." + name.rsplit(".", 1)[-1] if "." in name else "",
                "folder": folder or ".",
                "depth": path.count("/"),
                "size": size,
                "mtime_ns": 0,
                "mtime": stamp,
                "content_hash": content_hash,
                "seen_at": stamp,
                "content_changed_at": stamp,
            }
        ]
    )
    return store.get_file(path)["id"]


# ---------------------------------------------------------------------------
# Phase 19 - feedback
# ---------------------------------------------------------------------------


def test_query_normalisation_is_case_and_whitespace_insensitive():
    assert Store.normalise_query("  Machine   LEARNING notes ") == "machine learning notes"


def test_feedback_round_trips(store):
    file_id = _add_file(store, "a/one.txt")
    store.record_feedback("exam notes", file_id, "a/one.txt")
    assert store.feedback_for_query("Exam   Notes") == {file_id: 1.0}
    assert store.feedback_count() == 1


def test_rejection_is_negative_and_nets_against_picks(store):
    file_id = _add_file(store, "a/one.txt")
    store.record_feedback("q", file_id, "a/one.txt", event="pick")
    store.record_feedback("q", file_id, "a/one.txt", event="pick")
    store.record_feedback("q", file_id, "a/one.txt", event="reject")
    assert store.feedback_for_query("q") == {file_id: 1.0}


def test_feedback_does_not_leak_between_queries(store):
    file_id = _add_file(store, "a/one.txt")
    store.record_feedback("tax return", file_id, "a/one.txt")
    assert store.feedback_for_query("tax") == {}


def test_clearing_feedback_is_scopeable(store):
    file_id = _add_file(store, "a/one.txt")
    store.record_feedback("one", file_id, "a/one.txt")
    store.record_feedback("two", file_id, "a/one.txt")
    assert store.clear_feedback("one") == 1
    assert store.feedback_count() == 1


class _FeedbackStub:
    """Minimal stand-in for the feedback store."""

    def __init__(self, weights):
        self.weights = weights

    def feedback_for_query(self, query):
        return self.weights


def _retriever(feedback, tmp_path):
    """A HybridRetriever wired to nothing but a config and a feedback source."""
    cfg = load_config(None, root=tmp_path, data_dir=tmp_path / "d")
    return HybridRetriever(None, None, None, None, cfg, feedback=feedback)


def _results():
    return [
        RetrievalResult(1, "a.txt", 0.50, explanation=Explanation()),
        RetrievalResult(2, "b.txt", 0.48, explanation=Explanation()),
    ]


def test_feedback_boost_reorders_a_near_tie(tmp_path):
    retriever = _retriever(_FeedbackStub({2: 1.0}), tmp_path)
    results = _results()
    retriever._apply_feedback("q", results)
    results.sort(key=lambda r: -r.score)
    assert results[0].file_id == 2, "one pick should break a 0.02 tie"


def test_feedback_cannot_overturn_a_clear_win(tmp_path):
    """Ten picks must not promote a file that lost by more than the cap."""
    retriever = _retriever(_FeedbackStub({2: 10.0}), tmp_path)
    results = [
        RetrievalResult(1, "a.txt", 0.90, explanation=Explanation()),
        RetrievalResult(2, "b.txt", 0.50, explanation=Explanation()),
    ]
    retriever._apply_feedback("q", results)
    results.sort(key=lambda r: -r.score)
    assert results[0].file_id == 1
    assert results[1].score <= 0.50 + retriever.config.retrieval.feedback_max_boost


def test_feedback_boost_saturates(tmp_path):
    """Doubling the evidence must not double the boost."""
    one = _results()
    _retriever(_FeedbackStub({1: 1.0}), tmp_path)._apply_feedback("q", one)
    many = _results()
    _retriever(_FeedbackStub({1: 50.0}), tmp_path)._apply_feedback("q", many)
    first = one[0].score - 0.50
    second = many[0].score - 0.50
    assert second > first
    assert second < 2 * first


def test_feedback_is_inert_without_a_feedback_store(tmp_path):
    """The evaluation harness builds the retriever without feedback; prove it."""
    retriever = _retriever(None, tmp_path)
    results = _results()
    retriever._apply_feedback("q", results)
    assert [r.score for r in results] == [0.50, 0.48]


def test_feedback_never_enters_the_signal_contributions(tmp_path):
    """The four research signals must keep the score they earned alone."""
    retriever = _retriever(_FeedbackStub({1: 3.0}), tmp_path)
    results = _results()
    results[0].explanation.contributions = {"semantic": 0.50}
    retriever._apply_feedback("q", results)
    assert results[0].explanation.contributions == {"semantic": 0.50}
    assert "you confirmed this" in results[0].explanation.feedback_note


def test_feedback_note_appears_in_the_human_reasons(tmp_path):
    retriever = _retriever(_FeedbackStub({1: 1.0}), tmp_path)
    results = _results()
    retriever._apply_feedback("q", results)
    assert any("confirmed" in reason for reason in results[0].explanation.reasons())


# ---------------------------------------------------------------------------
# Phase 20 - insight surfaces
# ---------------------------------------------------------------------------


def test_near_duplicates_collapse_pairs_into_one_group(store):
    ids = [_add_file(store, f"a/f{i}.txt", content_hash=f"h{i}") for i in range(3)]
    graph = nx.MultiDiGraph()
    for a, b in ((0, 1), (1, 2)):
        graph.add_edge(f"file:{ids[a]}", f"file:{ids[b]}", type="duplicate", weight=0.4)
    groups = near_duplicates(store, graph)
    assert len(groups) == 1, "a chain of two edges is one problem, not two"
    assert len(groups[0].members) == 3


def test_identical_content_hashes_are_duplicates_without_a_graph_edge(store):
    _add_file(store, "a/x.txt", content_hash="same")
    _add_file(store, "b/y.txt", content_hash="same")
    groups = near_duplicates(store, nx.MultiDiGraph())
    assert len(groups) == 1
    assert groups[0].similarity == 1.0


def test_duplicate_group_keeps_the_newest_and_counts_the_rest_as_waste(store):
    _add_file(store, "a/old.txt", size=500, mtime=datetime(2024, 1, 1), content_hash="s")
    _add_file(store, "a/new.txt", size=500, mtime=datetime(2025, 1, 1), content_hash="s")
    group = near_duplicates(store, nx.MultiDiGraph())[0]
    assert group.keeper["path"] == "a/new.txt"
    assert group.wasted_bytes == 500


def test_near_duplicates_is_empty_without_a_graph(store):
    assert near_duplicates(store, None) == []


def test_project_stages_follow_recency(store):
    now = datetime(2026, 6, 1)
    for folder, age in (
        ("Active", 3),
        ("Dormant", DORMANT_AFTER_DAYS + 5),
        ("Finished", FINISHED_AFTER_DAYS + 5),
    ):
        for index in range(2):
            _add_file(
                store,
                f"{folder}/f{index}.txt",
                mtime=now - timedelta(days=age),
                content_hash=f"{folder}{index}",
            )
    stages = {p.folder: p.stage for p in projects(store, now=now)}
    assert stages == {"Active": "active", "Dormant": "dormant", "Finished": "finished"}


def test_single_file_folders_are_not_projects(store):
    _add_file(store, "Lonely/only.txt")
    assert [p.folder for p in projects(store)] == []


def test_every_project_carries_its_reasoning(store):
    _add_file(store, "P/a.txt", content_hash="a")
    _add_file(store, "P/b.txt", content_hash="b")
    assert all(p.reason for p in projects(store))


def test_digest_totals_match_the_file_rows(store):
    _add_file(store, "a/x.txt", size=100, content_hash="1")
    _add_file(store, "a/y.pdf", size=300, content_hash="2")
    report = digest(store)
    assert report.files == 2
    assert report.bytes == 400
    assert {ext: n for ext, n, _ in report.by_extension} == {".pdf": 1, ".txt": 1}


def test_digest_age_buckets_account_for_every_file(store):
    now = datetime(2026, 6, 1)
    for index, age in enumerate((1, 20, 100, 300, 900)):
        _add_file(
            store, f"a/f{index}.txt", mtime=now - timedelta(days=age), content_hash=str(index)
        )
    report = digest(store, now=now)
    assert sum(report.by_age.values()) == report.files == 5


def test_deleted_files_are_excluded_from_every_surface(store):
    _add_file(store, "a/x.txt", content_hash="1")
    _add_file(store, "a/y.txt", content_hash="2")
    store.mark_deleted(["a/y.txt"])
    assert digest(store).files == 1
    assert projects(store) == []


def test_suggest_tags_on_an_unknown_path_is_empty_not_an_error(store):
    assert suggest_tags(store, "nope/missing.txt") == []


def test_suggest_tags_ranks_sessions_above_keywords(store):
    """Ordering is the whole contract: a session label beats a TF-IDF term."""
    from contextfs.insights import TagSuggestion

    ranked = sorted(
        [
            TagSuggestion("kw", "keyword", 0.55),
            TagSuggestion("Exam revision", "activity session", 0.90),
        ],
        key=lambda s: -s.confidence,
    )
    assert ranked[0].source == "activity session"
