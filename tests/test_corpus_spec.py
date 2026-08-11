"""Phase 3 tests: the synthetic benchmark's structural and adversarial invariants.

These tests guard properties the *evaluation* depends on. If any of them break,
later phases would still run and still produce numbers - the numbers would just
be measuring something other than what the paper claims. That silent-wrong
failure mode is why these are tests rather than comments.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextfs.config import load_config
from contextfs.datagen.corpus_spec import CORPUS_FILES, QUERIES, SESSIONS
from contextfs.datagen.generate import build_ground_truth, generate_corpus

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CFG = load_config(PROJECT_ROOT / "contextfs.toml")

ML = "College/Semester7/MachineLearning"
KEY_PDF = f"{ML}/Unit4_Ensemble_Methods.pdf"
TIMETABLE = f"{ML}/Exam_Timetable_Sem7.xlsx"


def flatten(content) -> str:
    """Flatten any writer payload into one searchable lowercase string."""
    if isinstance(content, str):
        return content.lower()
    parts: list[str] = []
    stack = [content]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
        elif item is not None:
            parts.append(str(item))
    return " ".join(parts).lower()


SPEC_BY_PATH = {s.path: s for s in CORPUS_FILES}


# --- size and coverage -------------------------------------------------------


def test_corpus_size_is_within_the_specified_range():
    assert 30 <= len(CORPUS_FILES) <= 60


def test_all_required_formats_are_present():
    kinds = {s.kind for s in CORPUS_FILES}
    assert {"pdf", "docx", "pptx", "xlsx", "txt", "md", "code"} <= kinds


def test_query_count_meets_the_benchmark_minimum():
    assert len(QUERIES) >= 10


def test_paths_are_unique():
    paths = [s.path for s in CORPUS_FILES]
    assert len(paths) == len(set(paths))


def test_query_ids_are_unique():
    ids = [q.id for q in QUERIES]
    assert len(ids) == len(set(ids))


# --- THE adversarial case ----------------------------------------------------


FORBIDDEN_IN_KEY_PDF = [
    "exam",
    "test",
    "revision",
    "revise",
    "timetable",
    "syllabus",
    "semester",
    "studied",
    "study",
    "deadline",
]


def test_key_pdf_never_mentions_exam_or_revision():
    """The corpus's central adversarial case must stay adversarial.

    Query q01 ("the pdf I studied before my machine learning exam") targets this
    file. If the file ever comes to contain the vocabulary of the query, a pure
    semantic baseline could find it by lexical overlap and the query would stop
    testing the hypothesis - while still appearing to pass.
    """
    text = flatten(SPEC_BY_PATH[KEY_PDF].content)
    leaked = [word for word in FORBIDDEN_IN_KEY_PDF if word in text]
    assert not leaked, f"q01's target leaked query vocabulary: {leaked}"


def test_key_pdf_is_reachable_only_through_context():
    """The bridge to the key PDF must exist, and must be external to it."""
    timetable = flatten(SPEC_BY_PATH[TIMETABLE].content)
    assert "unit4_ensemble_methods.pdf" in timetable, "timetable must name the PDF"
    assert "exam" in timetable, "timetable must supply the word the PDF lacks"

    checklist = flatten(SPEC_BY_PATH[f"{ML}/ml_revision_checklist.txt"].content)
    assert "exam" in checklist and "unit4_ensemble_methods.pdf" in checklist


def test_key_pdf_and_its_bridges_share_one_session():
    """Activity retrieval can only recover the PDF if they co-occur in a session."""
    session = SPEC_BY_PATH[KEY_PDF].session
    assert session == "ml_exam_prep"
    assert SPEC_BY_PATH[TIMETABLE].session == session
    assert SPEC_BY_PATH[f"{ML}/ml_revision_checklist.txt"].session == session


def test_q01_and_q15_share_a_target_but_differ_in_phrasing():
    """The paired-query demonstration must remain intact."""
    q01 = next(q for q in QUERIES if q.id == "q01")
    q15 = next(q for q in QUERIES if q.id == "q15")
    assert set(q01.targets) == set(q15.targets) == {KEY_PDF}
    assert q01.kind == "activity" and q01.difficulty == "hard"
    assert q15.kind == "semantic" and q15.difficulty == "easy"


# --- sessions ----------------------------------------------------------------


def test_every_declared_session_id_exists():
    declared = {s.id for s in SESSIONS}
    used = {s.session for s in CORPUS_FILES if s.session}
    assert used <= declared


def test_every_session_has_at_least_two_members():
    for session in SESSIONS:
        members = [f for f in CORPUS_FILES if f.session == session.id]
        assert len(members) >= 2, f"{session.id} has {len(members)} member(s)"


def test_negative_control_session_exists_and_is_incoherent():
    """personal_misc must be scattered in time, or it is not a negative control."""
    control = next(s for s in SESSIONS if s.kind == "none")
    members = [f for f in CORPUS_FILES if f.session == control.id]
    assert len(members) >= 4
    span_days = (max(f.modified_at for f in members) - min(f.modified_at for f in members)).days
    assert span_days > 180, f"negative control spans only {span_days} days; too clusterable"


def test_real_sessions_are_temporally_coherent():
    """Each genuine session's files must fall inside its declared window."""
    from datetime import datetime

    for session in SESSIONS:
        if session.kind == "none":
            continue
        start = datetime.fromisoformat(session.start)
        end = datetime.fromisoformat(session.end)
        for spec in (f for f in CORPUS_FILES if f.session == session.id):
            assert start <= spec.modified_at <= end.replace(hour=23, minute=59), (
                f"{spec.path} mtime {spec.mtime} outside {session.id} "
                f"window {session.start}..{session.end}"
            )


def test_some_files_belong_to_no_session():
    """Unsessioned files are required: they test that sessions are not universal."""
    assert [f for f in CORPUS_FILES if f.session is None]


# --- dates -------------------------------------------------------------------


def test_both_date_classes_are_well_represented():
    meaningful = [d for f in CORPUS_FILES for d in f.meaningful_dates]
    incidental = [d for f in CORPUS_FILES for d in f.incidental_dates]
    assert len(meaningful) >= 15, "too few meaningful dates for a stable precision estimate"
    assert len(incidental) >= 15, "too few incidental dates; recall would be trivially high"


def test_no_date_is_labelled_both_ways_within_a_file():
    for spec in CORPUS_FILES:
        meaningful = {d.date for d in spec.meaningful_dates}
        incidental = {d.date for d in spec.incidental_dates}
        assert not (meaningful & incidental), spec.path


def test_every_date_label_carries_a_justification():
    """A label without a stated reason is not defensible in a viva."""
    for spec in CORPUS_FILES:
        for label in spec.dates:
            assert label.why.strip(), f"{spec.path}: {label.date} has no justification"
            assert label.surface.strip(), f"{spec.path}: {label.date} has no surface form"


def test_date_surfaces_actually_occur_in_their_documents():
    """A labelled surface form that is not in the text would be unfindable."""
    missing = []
    for spec in CORPUS_FILES:
        text = flatten(spec.content)
        for label in spec.dates:
            if label.surface.lower() not in text:
                missing.append((spec.path, label.surface))
    assert not missing, f"labelled surfaces absent from content: {missing}"


def test_structured_context_signal_has_a_negative_control():
    """A spreadsheet full of *incidental* dates must exist.

    Without it, "dates inside tables are meaningful" would be a perfect rule on
    this corpus, and the structured-context weight would be unfalsifiable.
    """
    attendance = SPEC_BY_PATH[f"{ML}/ml_lab_attendance.xlsx"]
    assert attendance.kind == "xlsx"
    assert len(attendance.incidental_dates) >= 4
    assert not attendance.meaningful_dates


def test_a_meaningful_date_exists_outside_every_session():
    """The timeline layer must be separable from the activity layer."""
    orphans = [f for f in CORPUS_FILES if f.session is None and f.meaningful_dates]
    assert orphans, "no unsessioned file carries a meaningful date; q17 would be confounded"


# --- near-duplicates ---------------------------------------------------------


def test_near_duplicate_pairs_are_planted_and_well_formed():
    pairs = [f for f in CORPUS_FILES if f.near_duplicate_of]
    assert len(pairs) >= 2
    for spec in pairs:
        assert spec.near_duplicate_of in SPEC_BY_PATH, spec.near_duplicate_of
        assert spec.near_duplicate_of != spec.path
        original = SPEC_BY_PATH[spec.near_duplicate_of]
        assert original.kind == spec.kind, "a near-duplicate should share its format"


def test_near_duplicates_are_similar_but_not_identical():
    for spec in (f for f in CORPUS_FILES if f.near_duplicate_of):
        mine = set(flatten(spec.content).split())
        theirs = set(flatten(SPEC_BY_PATH[spec.near_duplicate_of].content).split())
        jaccard = len(mine & theirs) / len(mine | theirs)
        assert 0.4 < jaccard < 1.0, f"{spec.path}: jaccard {jaccard:.2f} vs original"


# --- queries -----------------------------------------------------------------


def test_query_targets_and_relevance_sets_point_at_real_files():
    for query in QUERIES:
        for path in query.targets:
            assert path in SPEC_BY_PATH, f"{query.id} targets unknown file {path}"
        for path in query.relevant:
            assert path in SPEC_BY_PATH, f"{query.id} marks unknown file {path} relevant"
        assert set(query.targets) <= set(query.relevant), query.id


def test_every_query_has_a_stated_rationale():
    for query in QUERIES:
        assert len(query.rationale.strip()) > 30, f"{query.id} rationale is too thin"


def test_benchmark_contains_both_easy_and_hard_queries():
    easy = [q for q in QUERIES if q.difficulty == "easy"]
    hard = [q for q in QUERIES if q.difficulty == "hard"]
    assert len(easy) >= 3, "without easy queries we cannot show context does no harm"
    assert len(hard) >= 5, "without hard queries the benchmark cannot separate the systems"


def test_all_five_query_kinds_are_exercised():
    kinds = {q.kind for q in QUERIES}
    assert kinds == {"semantic", "activity", "temporal", "entity", "hybrid"}


def test_no_query_is_answered_by_its_own_wording_alone():
    """No hard query may share a rare distinctive token with its target's path."""
    for query in (q for q in QUERIES if q.difficulty == "hard"):
        words = {w for w in query.text.lower().split() if len(w) > 6}
        for target in query.targets:
            stem = Path(target).stem.lower().replace("_", " ")
            assert not (
                words & set(stem.split())
            ), f"{query.id} leaks its answer through the filename: {target}"


# --- ground truth object -----------------------------------------------------


def test_ground_truth_is_json_serialisable_and_complete(tmp_path):
    payload = build_ground_truth(tmp_path / "corpus")
    text = json.dumps(payload)
    assert len(text) > 5000
    for key in ("schema_version", "counts", "sessions", "files", "queries", "provenance"):
        assert key in payload
    assert payload["counts"]["files"] == len(CORPUS_FILES)
    assert payload["counts"]["queries"] == len(QUERIES)


def test_ground_truth_declares_synthetic_provenance():
    """Provenance must be explicit: no real user file was ever involved."""
    payload = build_ground_truth(Path("corpus"))
    provenance = payload["provenance"].lower()
    assert "synthetic" in provenance
    assert "no file from any real user machine" in provenance


def test_committed_ground_truth_matches_the_specification():
    """The checked-in ground truth must not drift from the code that builds it."""
    gt_path = CFG.eval.ground_truth
    if not gt_path.is_file():
        pytest.skip("ground truth not generated yet; run scripts/generate_corpus.py")
    committed = json.loads(gt_path.read_text(encoding="utf-8"))
    fresh = build_ground_truth(CFG.paths.root)
    assert committed["counts"] == fresh["counts"]
    assert [q["id"] for q in committed["queries"]] == [q["id"] for q in fresh["queries"]]
    assert [f["path"] for f in committed["files"]] == [f["path"] for f in fresh["files"]]


# --- generation --------------------------------------------------------------


@pytest.mark.slow
def test_generation_produces_every_file(tmp_path):
    written = generate_corpus(tmp_path / "corpus", clean=True)
    assert len(written) == len(CORPUS_FILES)
    for path in written:
        assert path.is_file()
        assert path.stat().st_size > 0


@pytest.mark.slow
def test_generation_stamps_specified_mtimes(tmp_path):
    from datetime import datetime

    root = tmp_path / "corpus"
    generate_corpus(root, clean=True)
    for spec in CORPUS_FILES:
        actual = datetime.fromtimestamp((root / Path(spec.path)).stat().st_mtime)
        assert abs((actual - spec.modified_at).total_seconds()) < 2, spec.path
