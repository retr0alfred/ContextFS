"""Phase 10 tests: meaningful vs. incidental date classification.

The accuracy number is produced by ``scripts/date_eval.py``. These tests protect
the *model*: that each signal behaves as documented, that the neutral-at-0.5
design holds, that the score is reproducible from its explanation, and that the
specific adversarial cases the corpus was built around come out right.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from contextfs.config import load_config
from contextfs.datagen.corpus_spec import CORPUS_FILES
from contextfs.store import Store
from contextfs.temporal import DateClassifier
from contextfs.temporal.classify import NEUTRAL, PAST_RECORD_KEYWORDS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CFG = load_config(PROJECT_ROOT / "contextfs.toml")

ML = "College/Semester7/MachineLearning"
UF = "Projects/UrbanFlow"
CAP = "College/Capstone"


@pytest.fixture(scope="module")
def classifier() -> DateClassifier:
    return DateClassifier(CFG)


def base_kwargs(**overrides):
    """A neutral classification request that individual tests perturb."""
    kwargs = {
        "file_id": 1,
        "rel_path": "some/file.md",
        "text": "nothing of interest here 2025-11-24 and nothing after it either",
        "iso_date": "2025-11-24",
        "surface": "2025-11-24",
        "char_start": 25,
        "char_end": 35,
        "precision": "day",
        "in_tabular": False,
        "mtime": "2025-11-20T10:00:00",
        "file_count": 1,
    }
    kwargs.update(overrides)
    return kwargs


# ---------------------------------------------------------------------------
# The neutral-at-0.5 design
# ---------------------------------------------------------------------------


def test_absent_signals_are_neutral_not_negative(classifier):
    """The design decision the whole model rests on."""
    verdict = classifier.classify(**base_kwargs())
    assert verdict.signals.keyword == NEUTRAL
    assert verdict.signals.structured == NEUTRAL
    assert verdict.signals.crossfile == NEUTRAL


def test_a_prose_deadline_is_not_punished_for_lacking_a_table(classifier):
    """The concrete false negative that motivated neutral-at-0.5.

    A deadline written in a sentence must clear the threshold. If the
    structured-context signal returned 0 for prose, this would score 0.534
    against a 0.55 threshold and be lost.
    """
    verdict = classifier.classify(
        **base_kwargs(
            text="Last date for submission: 31 December 2025. No extensions.",
            iso_date="2025-12-31",
            surface="31 December 2025",
            char_start=26,
            char_end=42,
            mtime="2025-12-11T15:30:00",
            in_tabular=False,
            file_count=1,
        )
    )
    assert verdict.is_meaningful, verdict.explain()


# ---------------------------------------------------------------------------
# S1 - keyword proximity
# ---------------------------------------------------------------------------


def test_commitment_vocabulary_raises_the_keyword_signal(classifier):
    score, evidence = classifier.keyword_signal(
        "The submission deadline is 24-11-2025 sharp", 27, 37
    )
    assert score > 0.8
    assert any(kind == "meaningful" for _, kind, _, _ in evidence)


def test_incidental_vocabulary_lowers_the_keyword_signal(classifier):
    score, evidence = classifier.keyword_signal("She was born on 11 April 2003", 16, 29)
    assert score < 0.2
    assert any(kind == "incidental" for _, kind, _, _ in evidence)


def test_past_record_vocabulary_lowers_the_keyword_signal(classifier):
    score, _ = classifier.keyword_signal(
        "Attendance | 11-08-2025 | Linear regression | Absent", 13, 23
    )
    assert score < NEUTRAL


def test_keyword_evidence_decays_with_distance(classifier):
    near_text = "deadline 24-11-2025"
    near, _ = classifier.keyword_signal(near_text, 9, 19)

    filler = " ".join(["word"] * 10)
    far_text = f"deadline {filler} 24-11-2025"
    position = far_text.index("24-11-2025")
    far, _ = classifier.keyword_signal(far_text, position, position + 10)

    assert near > far


def test_the_best_evidence_wins_not_the_sum(classifier):
    """A table repeating a status word must not out-vote one 'deadline'."""
    repeated = "status status status status status deadline 24-11-2025"
    score, _ = classifier.keyword_signal(repeated, len(repeated) - 10, len(repeated))
    assert score > NEUTRAL


def test_present_is_not_treated_as_a_past_record_word():
    """Removing it fixed both false negatives in the first evaluation run.

    In an attendance sheet "Present" is a status; in meeting notes
    "Present: Alfred, Abu" introduces the attendee list. Too polysemous to use.
    """
    assert "present" not in PAST_RECORD_KEYWORDS
    assert "attendance" in PAST_RECORD_KEYWORDS
    assert "absent" in PAST_RECORD_KEYWORDS


def test_column_headers_supply_context_a_window_cannot_see(classifier):
    """A 'Timestamp' column governs rows far below it."""
    row = "Readings\nTimestamp | Approach | Count\n" + "x " * 40 + "13-09-2025 | north | 34"
    position = row.index("13-09-2025")
    without, _ = classifier.keyword_signal(row, position, position + 10)
    with_header, evidence = classifier.keyword_signal(
        row, position, position + 10, header="Readings Timestamp | Approach | Count"
    )
    assert with_header < without
    assert any("column header" in word for word, _, _, _ in evidence)


def test_section_headings_supply_context_a_window_cannot_see(classifier):
    """A '# Supervisor meetings' heading identifies the dates beneath it."""
    body = "16 January 2026, 11:00, staff room. " + "x " * 40
    without, _ = classifier.keyword_signal(body, 0, 15)
    with_heading, _ = classifier.keyword_signal(
        body, 0, 15, header="Supervisor meetings - Dr. Murari Devakannan Kamalesh"
    )
    assert with_heading > without


# ---------------------------------------------------------------------------
# S2 - structured context
# ---------------------------------------------------------------------------


def test_tabular_context_is_evidence_for_but_prose_is_neutral(classifier):
    tabular, _ = classifier.structured_signal(True)
    prose, _ = classifier.structured_signal(False)
    assert tabular == 1.0
    assert prose == NEUTRAL


def test_structured_context_alone_cannot_make_a_date_meaningful(classifier):
    """The attendance spreadsheet is the corpus's control for exactly this."""
    verdict = classifier.classify(
        **base_kwargs(
            text="Attendance\nDate | Experiment | Status | Marks\n11-08-2025 | "
            "Linear regression | Absent | 0",
            iso_date="2025-08-11",
            surface="11-08-2025",
            char_start=44,
            char_end=54,
            in_tabular=True,
            mtime="2025-11-11T09:15:00",
            header="Attendance Date | Experiment | Status | Marks",
        )
    )
    assert not verdict.is_meaningful, verdict.explain()


# ---------------------------------------------------------------------------
# S3 - metadata consistency
# ---------------------------------------------------------------------------


def test_a_date_just_after_the_mtime_is_highly_consistent(classifier):
    score, _ = classifier.metadata_signal("2025-11-24", "2025-11-20T10:00:00")
    assert score > 0.9


def test_a_long_past_date_is_inconsistent(classifier):
    score, _ = classifier.metadata_signal("1947-08-15", "2025-07-14T16:20:00")
    assert score < 0.01


def test_the_signal_is_asymmetric_past_decays_faster(classifier):
    """People write about deadlines before they fall due."""
    future, _ = classifier.metadata_signal("2025-12-20", "2025-11-20T10:00:00")
    past, _ = classifier.metadata_signal("2025-10-21", "2025-11-20T10:00:00")
    assert future > past


def test_an_unparseable_timestamp_is_neutral(classifier):
    score, note = classifier.metadata_signal("2025-11-24", "not-a-date")
    assert score == NEUTRAL
    assert "unavailable" in note


# ---------------------------------------------------------------------------
# S4 - cross-file recurrence
# ---------------------------------------------------------------------------


def test_recurrence_rises_with_file_count_and_saturates(classifier):
    one, _ = classifier.crossfile_signal(1)
    two, _ = classifier.crossfile_signal(2)
    many, _ = classifier.crossfile_signal(20)
    assert one == NEUTRAL
    assert two > one
    assert many == pytest.approx(1.0)
    assert classifier.crossfile_signal(4)[0] == pytest.approx(1.0)


def test_a_single_occurrence_is_neutral_not_zero(classifier):
    """A one-off deadline must still be able to clear the threshold."""
    assert classifier.crossfile_signal(1)[0] == NEUTRAL


# ---------------------------------------------------------------------------
# The precision gate
# ---------------------------------------------------------------------------


def test_a_bare_year_is_penalised(classifier):
    day = classifier.classify(**base_kwargs(precision="day"))
    year = classifier.classify(**base_kwargs(precision="year"))
    assert year.score < day.score
    assert year.precision_penalty == CFG.temporal.year_only_penalty
    assert day.precision_penalty == 1.0


def test_publication_years_are_incidental(classifier):
    verdict = classifier.classify(
        **base_kwargs(
            text="[9] Brin and Page. The Anatomy of a Large-Scale Search Engine. 1998.",
            iso_date="1998-01-01",
            surface="1998",
            char_start=62,
            char_end=66,
            precision="year",
            mtime="2026-01-30T13:45:00",
        )
    )
    assert not verdict.is_meaningful


# ---------------------------------------------------------------------------
# Combination and explainability
# ---------------------------------------------------------------------------


def test_the_score_is_reproducible_from_its_explanation(classifier):
    """A viva-defensible model: the arithmetic must be checkable by hand."""
    verdict = classifier.classify(**base_kwargs(in_tabular=True, file_count=3))
    explanation = verdict.explain()
    recomputed = sum(explanation["contributions"].values()) * explanation["precision_penalty"]
    assert recomputed == pytest.approx(verdict.score, abs=1e-3)


def test_weights_sum_to_one_so_the_score_is_a_probability_scale(classifier):
    assert sum(classifier.weights.values()) == pytest.approx(1.0)


def test_the_score_is_bounded(classifier):
    for kwargs in (
        base_kwargs(text="deadline due submission 2025-11-24", in_tabular=True, file_count=10),
        base_kwargs(text="born published founded 2025-11-24", precision="year"),
    ):
        verdict = classifier.classify(**kwargs)
        assert 0.0 <= verdict.score <= 1.0


def test_every_verdict_carries_complete_evidence(classifier):
    explanation = classifier.classify(**base_kwargs()).explain()
    for key in ("signals", "weights", "contributions", "weighted_sum", "evidence", "verdict"):
        assert key in explanation
    assert set(explanation["signals"]) == set(classifier.weights)


def test_reason_is_human_readable(classifier):
    reason = classifier.classify(
        **base_kwargs(text="The exam deadline is 2025-11-24", char_start=21, char_end=31)
    ).reason()
    assert reason.startswith("0.") or reason.startswith("1.")
    assert "+" in reason


def test_threshold_comes_from_configuration_not_code(classifier):
    """The threshold must be a config value, and moving it must move verdicts."""
    assert classifier.threshold == CFG.temporal.timeline_node_threshold

    request = base_kwargs()
    score = classifier.classify(**request).score

    permissive = DateClassifier(
        load_config(
            PROJECT_ROOT / "contextfs.toml",
            overrides={"temporal": {"timeline_node_threshold": max(0.01, score - 0.1)}},
        )
    )
    strict = DateClassifier(
        load_config(
            PROJECT_ROOT / "contextfs.toml",
            overrides={"temporal": {"timeline_node_threshold": min(0.99, score + 0.1)}},
        )
    )
    assert permissive.classify(**request).is_meaningful
    assert not strict.classify(**request).is_meaningful


def test_metadata_proximity_alone_can_carry_a_date_over_the_threshold(classifier):
    """A documented property of the model, asserted so it stays deliberate.

    A date four days after a document's own timestamp, with no keyword, no
    table and no recurrence, scores 0.574 - just over the 0.55 threshold. That
    is defensible (writing about something that happens next week is exactly
    what a commitment looks like) but it means S3 is not merely a tie-breaker.
    Recorded here so the behaviour is a decision rather than an accident.
    """
    verdict = classifier.classify(**base_kwargs())
    assert verdict.signals.keyword == NEUTRAL
    assert verdict.signals.metadata > 0.85
    assert 0.55 <= verdict.score < 0.62


def test_collapse_keeps_the_best_mention_per_file_and_date(classifier):
    weak = classifier.classify(**base_kwargs())
    strong = classifier.classify(
        **base_kwargs(text="deadline 2025-11-24", char_start=9, char_end=19)
    )
    collapsed = classifier.collapse([weak, strong])
    assert len(collapsed) == 1
    assert collapsed[0].score == strong.score


# ---------------------------------------------------------------------------
# End-to-end against the real index (requires `contextfs scan` to have run)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def indexed_verdicts(classifier):
    if not CFG.db_path.is_file():
        pytest.skip("no index; run `contextfs scan` first")
    store = Store(CFG.db_path, read_only=True)
    verdicts = classifier.collapse(classifier.classify_store(store))
    yield {(v.rel_path, v.iso_date): v for v in verdicts}
    store.close()


@pytest.mark.slow
def test_the_exam_date_is_meaningful(indexed_verdicts):
    verdict = indexed_verdicts[(f"{ML}/Exam_Timetable_Sem7.xlsx", "2025-11-24")]
    assert verdict.is_meaningful
    assert verdict.score > 0.8


@pytest.mark.slow
def test_attendance_records_are_incidental(indexed_verdicts):
    """The corpus's control against 'dates in tables are meaningful'."""
    attendance = [
        v for (path, _), v in indexed_verdicts.items() if path == f"{ML}/ml_lab_attendance.xlsx"
    ]
    assert attendance
    assert not any(v.is_meaningful for v in attendance), [v.reason() for v in attendance]


@pytest.mark.slow
def test_historical_dates_are_incidental(indexed_verdicts):
    historical = [
        v
        for (path, _), v in indexed_verdicts.items()
        if path == "Personal/Misc/history_essay_partition.md"
    ]
    assert historical
    assert not any(v.is_meaningful for v in historical)


@pytest.mark.slow
def test_birthdays_are_incidental(indexed_verdicts):
    birthdays = [
        v for (path, _), v in indexed_verdicts.items() if path == "Personal/Misc/birthday_list.txt"
    ]
    assert birthdays
    assert not any(v.is_meaningful for v in birthdays)


@pytest.mark.slow
def test_the_unsessioned_scholarship_deadline_is_meaningful(indexed_verdicts):
    """Isolates the timeline layer from the activity layer (query q17)."""
    verdict = indexed_verdicts[("Downloads/scholarship_form_notes.txt", "2025-12-31")]
    assert verdict.is_meaningful


@pytest.mark.slow
def test_classification_beats_naive_extraction_on_the_corpus(indexed_verdicts):
    """RQ3, asserted as a property rather than only reported in a script."""
    labels = {(spec.path, label.date): label.kind for spec in CORPUS_FILES for label in spec.dates}
    tp = fp = fn = 0
    for key, truth in labels.items():
        verdict = indexed_verdicts.get(key)
        predicted = bool(verdict and verdict.is_meaningful)
        if truth == "meaningful" and predicted:
            tp += 1
        elif truth == "incidental" and predicted:
            fp += 1
        elif truth == "meaningful":
            fn += 1

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    naive_precision = len([k for k, v in labels.items() if v == "meaningful"]) / len(labels)
    naive_f1 = 2 * naive_precision / (naive_precision + 1)

    assert f1 > naive_f1 + 0.2, f"F1 {f1:.3f} vs naive {naive_f1:.3f}"
    assert precision > 0.85 and recall > 0.85


@pytest.mark.slow
def test_classified_dates_persist(indexed_verdicts):
    store = Store(CFG.db_path, read_only=True)
    try:
        counts = store.date_counts()
        assert counts["total"] > 0
        assert counts["meaningful"] > 0
        assert counts["incidental"] > 0
        rows = store.meaningful_dates()
        assert rows
        assert all(row["explanation"].startswith("{") for row in rows)
    finally:
        store.close()
