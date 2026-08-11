"""Phase 5 tests: content extraction across every corpus format.

Two things are being protected here beyond "does it produce text".

1. **Structure survives.** Spreadsheet and table content must arrive tagged
   ``is_tabular``, because Phase 10's structured-context signal reads that flag.
   If extraction quietly flattened everything to prose, Phase 10 would still
   run and would still produce a precision number - just a meaningless one.
2. **Date surface forms survive.** The ground truth labels dates by the literal
   string they appear as. If extraction mangles ``24-11-2025``, every date in
   Phase 10 becomes unfindable and the failure looks like a classifier problem.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from contextfs.config import load_config
from contextfs.datagen.corpus_spec import CORPUS_FILES
from contextfs.datagen.generate import generate_corpus
from contextfs.extract import (
    EXTRACTORS,
    ExtractionReport,
    extract_file,
    extract_many,
    extractor_for,
    supported_extensions,
)
from contextfs.extract.base import ExtractedBlock, ExtractedDocument, truncate_to
from contextfs.scanner import Scanner
from contextfs.store import Store

PROJECT_ROOT = Path(__file__).resolve().parents[1]

ML = "College/Semester7/MachineLearning"
TIMETABLE = f"{ML}/Exam_Timetable_Sem7.xlsx"
KEY_PDF = f"{ML}/Unit4_Ensemble_Methods.pdf"


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> Path:
    """A generated corpus shared by this module."""
    root = tmp_path_factory.mktemp("extract") / "corpus"
    generate_corpus(root, clean=True)
    return root


@pytest.fixture(scope="module")
def cfg(corpus, tmp_path_factory):
    """Config pointing at the shared corpus."""
    directory = tmp_path_factory.mktemp("extractcfg")
    config_file = directory / "contextfs.toml"
    config_file.write_text(
        f'[paths]\nroot = "{corpus.as_posix()}"\ndata_dir = "derived"\n', encoding="utf-8"
    )
    return load_config(config_file)


@pytest.fixture(scope="module")
def report(corpus, cfg) -> ExtractionReport:
    """One extraction pass over the whole corpus, reused by many tests."""
    items = [
        (p, p.relative_to(corpus).as_posix()) for p in sorted(corpus.rglob("*")) if p.is_file()
    ]
    return extract_many(items, config=cfg)


def doc_for(report: ExtractionReport, rel_path: str) -> ExtractedDocument:
    """Look up one document in a report."""
    return next(d for d in report.documents if d.rel_path == rel_path)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_every_corpus_format_has_an_extractor():
    corpus_extensions = {Path(spec.path).suffix.lower() for spec in CORPUS_FILES}
    missing = corpus_extensions - set(EXTRACTORS)
    assert not missing, f"corpus contains formats with no extractor: {missing}"


def test_spec_required_formats_are_supported():
    supported = set(supported_extensions())
    required = {".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md", ".py"}
    assert required <= supported


def test_unsupported_extension_is_reported_not_raised(tmp_path):
    odd = tmp_path / "thing.xyz"
    odd.write_bytes(b"binary junk")
    doc = extract_file(odd)
    assert doc.ok is False
    assert doc.extractor == "unsupported"
    assert "no extractor registered" in doc.error


def test_extractor_for_is_case_insensitive():
    assert extractor_for(Path("A.PDF")) is not None
    assert extractor_for(Path("a.MD")) is not None


# ---------------------------------------------------------------------------
# Corpus-wide outcome
# ---------------------------------------------------------------------------


def test_every_corpus_file_extracts_successfully(report):
    assert report.total == 40
    assert report.success_rate == 1.0, [(d.rel_path, d.error) for d in report.failed]


def test_no_document_extracts_to_nothing(report):
    assert report.empty == []


def test_every_document_has_at_least_one_block(report):
    for doc in report.succeeded:
        assert doc.block_count >= 1, doc.rel_path
        assert doc.char_count > 0, doc.rel_path


def test_success_rate_is_reported_per_extension(report):
    for ext, (ok, total) in report.by_extension().items():
        assert ok == total, f"{ext}: only {ok}/{total} extracted"


# ---------------------------------------------------------------------------
# Structure preservation - the part Phase 10 depends on
# ---------------------------------------------------------------------------


def test_every_spreadsheet_is_marked_tabular(report):
    xlsx = [d for d in report.succeeded if d.ext == ".xlsx"]
    assert len(xlsx) == 6
    for doc in xlsx:
        assert doc.has_tabular_content, f"{doc.rel_path} lost its tabular structure"


def test_markdown_pipe_tables_are_marked_tabular(report):
    svm = doc_for(report, f"{ML}/Unit3_SVM_Notes.md")
    assert svm.has_tabular_content, "the kernel table in the SVM notes was not detected"


def test_prose_documents_are_not_marked_tabular(report):
    for path in (
        KEY_PDF,
        "Personal/Misc/recipe_biryani.txt",
        "Personal/Misc/book_notes_sapiens.md",
    ):
        assert not doc_for(report, path).has_tabular_content, path


def test_tabular_spans_locate_table_regions(report):
    timetable = doc_for(report, TIMETABLE)
    spans = timetable.tabular_spans()
    assert spans
    text = timetable.text
    for start, end in spans:
        assert 0 <= start < end <= len(text)
    # The exam date sits inside a tabular span.
    position = text.find("24-11-2025")
    assert position >= 0, "the exam date did not survive extraction"
    assert any(start <= position < end for start, end in spans)


def test_block_at_maps_offsets_back_to_blocks(report):
    timetable = doc_for(report, TIMETABLE)
    position = timetable.text.find("24-11-2025")
    block = timetable.block_at(position)
    assert block is not None
    assert block.is_tabular
    assert block.kind == "sheet"


def test_headings_are_identified_in_markdown_and_slides(report):
    survey = doc_for(report, "College/Capstone/literature_survey.md")
    assert any(b.is_heading for b in survey.blocks)

    deck = doc_for(report, "Projects/UrbanFlow/pitch_deck.pptx")
    assert any(b.is_heading for b in deck.blocks)


def test_pdf_blocks_are_pages(report):
    pdf = doc_for(report, KEY_PDF)
    assert pdf.meta["page_count"] >= 4
    assert all(b.kind == "page" for b in pdf.blocks)
    assert [b.label for b in pdf.blocks][:2] == ["page 1", "page 2"]


def test_pptx_blocks_are_slides(report):
    deck = doc_for(report, "Projects/UrbanFlow/pitch_deck.pptx")
    assert deck.meta["slide_count"] == 5
    assert all(b.kind in {"slide", "notes"} for b in deck.blocks)


def test_xlsx_blocks_are_sheets_with_names(report):
    timetable = doc_for(report, TIMETABLE)
    assert timetable.meta["sheet_names"] == ["Semester 7 Timetable", "Notes"]
    assert [b.kind for b in timetable.blocks] == ["sheet", "sheet"]


def test_code_carries_its_language(report):
    code = doc_for(report, "Projects/UrbanFlow/traffic_model.py")
    assert code.extractor == "code"
    assert code.meta["language"] == "Python"
    assert code.meta["line_count"] > 10

    sql = doc_for(report, "College/Semester7/DBMS/normalization_examples.sql")
    assert sql.meta["language"] == "SQL"


# ---------------------------------------------------------------------------
# Content fidelity - the part the ground truth depends on
# ---------------------------------------------------------------------------


def test_labelled_date_surfaces_survive_extraction(report):
    """Every ground-truth date surface must be findable in the extracted text.

    This is the bridge between the benchmark and the system. If a surface form
    is mangled by extraction, Phase 10 cannot possibly detect it, and the
    resulting recall failure would be misattributed to the classifier.
    """
    missing: list[tuple[str, str]] = []
    for spec in CORPUS_FILES:
        if not spec.dates:
            continue
        text = doc_for(report, spec.path).text
        for label in spec.dates:
            if label.surface not in text:
                missing.append((spec.path, label.surface))
    assert not missing, f"date surfaces lost in extraction: {missing}"


def test_the_adversarial_case_survives_extraction(report):
    """The key PDF must still contain no exam vocabulary after extraction."""
    text = doc_for(report, KEY_PDF).text.lower()
    for word in ("exam", "revision", "timetable", "syllabus", "studied"):
        assert word not in text, f"{word!r} appeared in the extracted key PDF"
    assert "bagging" in text and "boosting" in text


def test_the_timetable_still_names_the_pdf_after_extraction(report):
    """The only bridge between query vocabulary and the target must survive."""
    text = doc_for(report, TIMETABLE).text
    assert "Unit4_Ensemble_Methods.pdf" in text
    assert "Machine Learning" in text


def test_docx_paragraphs_and_headings_both_survive(report):
    proposal = doc_for(report, "College/Capstone/ContextFS_Proposal.docx")
    assert "Dr. Murari Devakannan Kamalesh" in proposal.text
    assert any(b.is_heading for b in proposal.blocks)


def test_entity_names_survive_across_formats(report):
    assert "Zoho" in doc_for(report, "Personal/Career/cover_letter_zoho.docx").text
    assert "Zoho" in doc_for(report, "Personal/Career/application_tracker.xlsx").text
    assert "Nithya" in doc_for(report, "Projects/UrbanFlow/team_notes.md").text


# ---------------------------------------------------------------------------
# Error handling: capture, never throw
# ---------------------------------------------------------------------------


def test_a_corrupt_pdf_is_reported_not_raised(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\nthis is not actually a pdf\n")
    doc = extract_file(broken)
    assert doc.ok is False
    assert doc.error
    assert doc.extractor == "pdf"


def test_a_corrupt_docx_is_reported_not_raised(tmp_path):
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"PK\x03\x04 not a real docx")
    doc = extract_file(broken)
    assert doc.ok is False
    assert "could not open" in doc.error


def test_a_corrupt_xlsx_is_reported_not_raised(tmp_path):
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"PK\x03\x04 nope")
    doc = extract_file(broken)
    assert doc.ok is False


def test_a_missing_file_is_reported_not_raised(tmp_path):
    doc = extract_file(tmp_path / "ghost.txt")
    assert doc.ok is False
    assert "does not exist" in doc.error


def test_an_empty_text_file_is_flagged_not_dropped(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    doc = extract_file(empty)
    assert doc.ok is True
    assert doc.is_empty
    assert doc.warnings


def test_a_batch_survives_one_bad_file(tmp_path):
    good = tmp_path / "good.txt"
    good.write_text("readable content here", encoding="utf-8")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")

    batch = extract_many([(good, "good.txt"), (bad, "bad.pdf")])
    assert batch.total == 2
    assert len(batch.succeeded) == 1
    assert len(batch.failed) == 1
    assert batch.success_rate == 0.5


def test_non_utf8_text_is_decoded_rather_than_failing(tmp_path):
    latin = tmp_path / "legacy.txt"
    latin.write_bytes("Meeting with José on 5 June — deadline\n".encode("cp1252"))
    doc = extract_file(latin)
    assert doc.ok
    assert "Jos" in doc.text
    assert doc.meta["encoding"] in {"cp1252", "latin-1"}


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


def test_truncation_respects_block_boundaries():
    blocks = [
        ExtractedBlock(index=i, kind="page", label=f"p{i}", text="x" * 400) for i in range(10)
    ]
    kept, truncated = truncate_to(blocks, 1000)
    assert truncated
    assert sum(b.char_count for b in kept) <= 1000
    assert len(kept) == 2


def test_zero_limit_means_unlimited():
    blocks = [ExtractedBlock(index=0, kind="page", label="p", text="x" * 10_000)]
    kept, truncated = truncate_to(blocks, 0)
    assert kept == blocks
    assert truncated is False


def test_extract_file_applies_the_configured_limit(corpus):
    target = corpus / "College" / "Semester7" / "MachineLearning" / "Unit4_Ensemble_Methods.pdf"
    doc = extract_file(target, "key.pdf", max_chars=500)
    assert doc.truncated
    assert doc.char_count <= 500
    assert any("truncated" in w for w in doc.warnings)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@pytest.fixture
def indexed(corpus, tmp_path):
    """A scanned and extracted index in an isolated data directory."""
    config_file = tmp_path / "contextfs.toml"
    config_file.write_text(
        f'[paths]\nroot = "{corpus.as_posix()}"\ndata_dir = "derived"\n', encoding="utf-8"
    )
    config = load_config(config_file)
    config.ensure_data_dir()
    store = Store(config.db_path)
    Scanner(config).scan(store)

    pending = store.files_needing_extraction()
    items = [(Path(row["abs_path"]), row["path"]) for row in pending]
    batch = extract_many(items, config=config)
    rows = {row["path"]: row for row in pending}
    for doc in batch.documents:
        store.save_document(
            rows[doc.rel_path]["id"], doc, content_hash=rows[doc.rel_path]["content_hash"]
        )
    yield config, store, batch
    store.close()


def test_documents_persist_and_reload(indexed):
    _, store, _ = indexed
    assert store.document_count() == 40
    row = store.get_document_by_path(TIMETABLE)
    assert row is not None
    assert row["ok"] == 1
    assert row["has_tabular"] == 1
    assert "24-11-2025" in row["text"]


def test_blocks_persist_with_offsets_that_index_the_stored_text(indexed):
    _, store, _ = indexed
    row = store.get_document_by_path(TIMETABLE)
    blocks = store.get_blocks(row["file_id"])
    assert blocks
    text = row["text"]
    for block in blocks:
        assert text[block["char_start"] : block["char_end"]] == block["text"]


def test_nothing_needs_extraction_after_a_full_pass(indexed):
    _, store, _ = indexed
    assert store.files_needing_extraction() == []


def test_only_changed_files_are_re_extracted(indexed, corpus):
    config, store, _ = indexed
    target = corpus / "Personal" / "Misc" / "recipe_biryani.txt"
    original = target.read_bytes()
    try:
        target.write_text("completely different content now", encoding="utf-8")
        Scanner(config).scan(store)
        pending = store.files_needing_extraction()
        assert [row["path"] for row in pending] == ["Personal/Misc/recipe_biryani.txt"]
    finally:
        target.write_bytes(original)
        Scanner(config).scan(store)


def test_re_extraction_replaces_blocks_rather_than_appending(indexed, corpus):
    config, store, _ = indexed
    row = store.get_document_by_path("Personal/Misc/recipe_biryani.txt")
    file_id = row["file_id"]
    before = len(store.get_blocks(file_id))
    assert before > 1

    target = corpus / "Personal" / "Misc" / "recipe_biryani.txt"
    original = target.read_bytes()
    try:
        target.write_text("one line only", encoding="utf-8")
        doc = extract_file(target, "Personal/Misc/recipe_biryani.txt", config=config)
        store.save_document(file_id, doc, content_hash="newhash")
        assert len(store.get_blocks(file_id)) == 1, "stale blocks survived re-extraction"
    finally:
        target.write_bytes(original)


def test_extraction_does_not_modify_the_corpus(corpus, cfg):
    import hashlib

    def fingerprint():
        return {
            p.relative_to(corpus).as_posix(): (
                hashlib.sha256(p.read_bytes()).hexdigest(),
                p.stat().st_mtime_ns,
            )
            for p in sorted(corpus.rglob("*"))
            if p.is_file()
        }

    before = fingerprint()
    items = [
        (p, p.relative_to(corpus).as_posix()) for p in sorted(corpus.rglob("*")) if p.is_file()
    ]
    extract_many(items, config=cfg)
    assert fingerprint() == before, "extraction altered a scanned file"
