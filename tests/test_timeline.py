"""Phase 11 tests: date-range resolution and the timeline interval index."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from contextfs.config import load_config
from contextfs.store import Store
from contextfs.temporal import (
    DateRange,
    RangeResolutionError,
    TimelineIndex,
    TimelineNode,
    resolve_best,
    resolve_range,
    resolve_range_candidates,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CFG = load_config(PROJECT_ROOT / "contextfs.toml")

#: Fixed "today" so relative expressions are deterministic in tests.
TODAY = date(2026, 8, 12)


def rng(expression: str) -> DateRange:
    return resolve_range(expression, TODAY)


# ---------------------------------------------------------------------------
# Explicit forms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expression,start,end",
    [
        ("2025-11-24", date(2025, 11, 24), date(2025, 11, 24)),
        ("24-11-2025", date(2025, 11, 24), date(2025, 11, 24)),
        ("24 November 2025", date(2025, 11, 24), date(2025, 11, 24)),
        ("2025-11-24 to 2025-12-01", date(2025, 11, 24), date(2025, 12, 1)),
        ("November 2025", date(2025, 11, 1), date(2025, 11, 30)),
        ("2025", date(2025, 1, 1), date(2025, 12, 31)),
        ("Q1 2026", date(2026, 1, 1), date(2026, 3, 31)),
        ("Q4 2025", date(2025, 10, 1), date(2025, 12, 31)),
        ("March 2026 to May 2026", date(2026, 3, 1), date(2026, 5, 31)),
    ],
)
def test_explicit_ranges_resolve(expression, start, end):
    resolved = rng(expression)
    assert (resolved.start, resolved.end) == (start, end), resolved.interpretation


def test_february_length_is_respected():
    assert rng("February 2024").end == date(2024, 2, 29)
    assert rng("February 2025").end == date(2025, 2, 28)


def test_a_reversed_span_is_normalised():
    resolved = rng("2025-12-01 to 2025-11-24")
    assert resolved.start < resolved.end


def test_case_and_whitespace_are_insensitive():
    assert rng("  NOVEMBER   2025 ").start == date(2025, 11, 1)


def test_dashes_and_words_both_work_as_span_separators():
    assert rng("March 2026 - May 2026").end == date(2026, 5, 31)
    assert rng("March 2026 until May 2026").end == date(2026, 5, 31)


# ---------------------------------------------------------------------------
# Ordinal weeks - query q02
# ---------------------------------------------------------------------------


def test_third_week_of_october_is_the_15th_to_the_21st():
    """The benchmark's q02 phrasing. Counted from the 1st, as people mean it."""
    resolved = resolve_range("third week of October 2025", TODAY)
    assert (resolved.start, resolved.end) == (date(2025, 10, 15), date(2025, 10, 21))


def test_first_and_last_week_resolve():
    assert resolve_range("first week of October 2025", TODAY).start == date(2025, 10, 1)
    last = resolve_range("last week of October 2025", TODAY)
    assert last.end == date(2025, 10, 31)
    assert last.days == 7


def test_a_week_that_does_not_exist_is_an_error():
    with pytest.raises(RangeResolutionError, match="no week"):
        resolve_range("fifth week of February 2025", TODAY)


# ---------------------------------------------------------------------------
# Relative forms
# ---------------------------------------------------------------------------


def test_relative_ranges_are_anchored_to_the_reference_date():
    assert rng("today").start == TODAY
    assert rng("yesterday").start == date(2026, 8, 11)
    assert rng("this month").start == date(2026, 8, 1)
    assert rng("last month").start == date(2026, 7, 1)
    assert rng("next month").start == date(2026, 9, 1)
    assert rng("last year").start == date(2025, 1, 1)


def test_week_ranges_are_seven_days_and_start_on_monday():
    this_week = rng("this week")
    assert this_week.days == 7
    assert this_week.start.weekday() == 0
    assert rng("last week").end == this_week.start - __import__("datetime").timedelta(days=1)


def test_month_arithmetic_wraps_the_year():
    assert resolve_range("next month", date(2025, 12, 15)).start == date(2026, 1, 1)
    assert resolve_range("last month", date(2025, 1, 15)).start == date(2024, 12, 1)


# ---------------------------------------------------------------------------
# Year inference for bare months
# ---------------------------------------------------------------------------


def test_a_bare_month_picks_the_nearest_year_by_default():
    """In August 2026, "September" is nearer as 2026 than 2025."""
    assert rng("September").start.year == 2026
    assert resolve_range("September", date(2026, 2, 1)).start.year == 2025


def test_candidates_are_generated_for_ambiguous_months():
    candidates = resolve_range_candidates("September", TODAY)
    assert len(candidates) > 1
    assert {candidate.start.year for candidate in candidates} >= {2025, 2026}


def test_no_candidates_are_generated_when_the_year_is_explicit():
    assert len(resolve_range_candidates("September 2025", TODAY)) == 1
    assert len(resolve_range_candidates("2025-09-14", TODAY)) == 1
    assert len(resolve_range_candidates("last month", TODAY)) == 1


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_unparseable_input_raises_with_guidance():
    with pytest.raises(RangeResolutionError, match="Try:"):
        rng("sometime around the hackathon")


def test_empty_input_raises():
    with pytest.raises(RangeResolutionError):
        rng("   ")


def test_an_impossible_date_is_not_silently_accepted():
    with pytest.raises(RangeResolutionError):
        rng("32-13-2025")


# ---------------------------------------------------------------------------
# The interval index
# ---------------------------------------------------------------------------


def make_index() -> TimelineIndex:
    return TimelineIndex.build(
        [
            TimelineNode(1, "a.md", date(2025, 9, 14), "14 Sep", 0.9, "+deadline"),
            TimelineNode(2, "b.md", date(2025, 10, 18), "18 Oct", 0.8, "+due"),
            TimelineNode(2, "b.md", date(2025, 10, 21), "21 Oct", 0.7, "+due"),
            TimelineNode(3, "c.md", date(2025, 11, 24), "24 Nov", 0.95, "+exam"),
        ]
    )


def test_query_returns_only_nodes_inside_the_range():
    index = make_index()
    hits = index.query(rng("October 2025"))
    assert [node.day.isoformat() for node in hits] == ["2025-10-18", "2025-10-21"]


def test_range_boundaries_are_inclusive():
    index = make_index()
    assert index.query(resolve_range("2025-09-14", TODAY))
    assert index.query(resolve_range("2025-09-14 to 2025-09-14", TODAY))


def test_results_are_chronological_then_by_score():
    index = TimelineIndex.build(
        [
            TimelineNode(1, "low.md", date(2025, 9, 14), "x", 0.6),
            TimelineNode(2, "high.md", date(2025, 9, 14), "x", 0.9),
            TimelineNode(3, "later.md", date(2025, 9, 20), "x", 0.99),
        ]
    )
    hits = index.query(rng("September 2025"))
    assert [node.rel_path for node in hits] == ["high.md", "low.md", "later.md"]


def test_an_empty_range_returns_nothing():
    assert make_index().query(rng("March 2020")) == []


def test_an_empty_index_is_safe():
    empty = TimelineIndex.build([])
    assert empty.query(rng("2025")) == []
    assert empty.span() is None


def test_files_in_range_groups_by_file():
    grouped = make_index().files_in_range(rng("October 2025"))
    assert set(grouped) == {2}
    assert len(grouped[2]) == 2


def test_span_and_stats_describe_the_index():
    index = make_index()
    assert index.span() == (date(2025, 9, 14), date(2025, 11, 24))
    stats = index.stats()
    assert stats["timeline_nodes"] == 4
    assert stats["distinct_files"] == 3
    assert stats["earliest"] == "2025-09-14"


# ---------------------------------------------------------------------------
# Index-aware disambiguation
# ---------------------------------------------------------------------------


def test_resolve_best_prefers_the_year_that_has_files():
    """The fix for confidently-empty answers on backward-looking queries."""
    index = make_index()
    chosen = resolve_best("September", index, TODAY)
    assert chosen.start.year == 2025, chosen.interpretation
    assert "where your files are" in chosen.interpretation


def test_resolve_best_explains_when_it_overrides_the_default():
    chosen = resolve_best("October", make_index(), TODAY)
    assert chosen.start.year == 2025
    assert "chosen over" in chosen.interpretation


def test_resolve_best_leaves_unambiguous_expressions_alone():
    chosen = resolve_best("September 2026", make_index(), TODAY)
    assert chosen.start.year == 2026
    assert "chosen over" not in chosen.interpretation


def test_resolve_best_falls_back_when_no_year_has_data():
    chosen = resolve_best("March", make_index(), TODAY)
    assert chosen.start.year == TODAY.year or chosen.start.year == TODAY.year + 1


def test_resolve_best_without_an_index_is_plain_resolution():
    assert resolve_best("September", None, TODAY).start.year == 2026


# ---------------------------------------------------------------------------
# Against the real index
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_index():
    if not CFG.db_path.is_file():
        pytest.skip("no index; run `contextfs scan` first")
    store = Store(CFG.db_path, read_only=True)
    yield TimelineIndex.from_store(store)
    store.close()


@pytest.mark.slow
def test_the_index_contains_only_meaningful_dates(live_index):
    assert live_index.nodes
    store = Store(CFG.db_path, read_only=True)
    try:
        assert len(live_index.nodes) == len(store.meaningful_dates())
        total = store.date_counts()["total"]
        assert len(live_index.nodes) < total, "the timeline should exclude incidental dates"
    finally:
        store.close()


@pytest.mark.slow
def test_q02_third_week_of_october_finds_the_dbms_assignment(live_index):
    chosen = resolve_best("third week of October", live_index, TODAY)
    paths = {node.rel_path for node in live_index.query(chosen)}
    assert any("DBMS" in path for path in paths), paths


@pytest.mark.slow
def test_q07_september_finds_the_hackathon_deadline(live_index):
    chosen = resolve_best("September", live_index, TODAY)
    assert chosen.start.year == 2025
    paths = {node.rel_path for node in live_index.query(chosen)}
    assert "Projects/UrbanFlow/submission_checklist.txt" in paths, paths


@pytest.mark.slow
def test_the_exam_date_is_retrievable_by_range(live_index):
    hits = live_index.query(resolve_range("November 2025", TODAY))
    paths = {node.rel_path for node in hits}
    assert "College/Semester7/MachineLearning/Exam_Timetable_Sem7.xlsx" in paths


@pytest.mark.slow
def test_historical_dates_are_absent_from_the_timeline(live_index):
    """1947 must not be a timeline node - that is Phase 10 doing its job."""
    assert live_index.query(resolve_range("1947", TODAY)) == []


@pytest.mark.slow
def test_query_latency_is_measured_not_estimated(live_index):
    measurement = live_index.benchmark(resolve_range("2025", TODAY), repeats=50)
    assert measurement["repeats"] == 50
    assert measurement["median_ms"] >= 0.0
    assert measurement["nodes"] == len(live_index.nodes)
