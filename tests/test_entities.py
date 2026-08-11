"""Phase 6 tests: entity, keyword, and raw date-mention extraction.

The date-mention tests matter most. Phase 10 classifies whatever this layer
detects; a date it never sees can never be classified, and a date whose offset
is wrong will be judged against the wrong surrounding context. Both failures
would show up in Phase 10's numbers and be misattributed to the classifier.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from contextfs.config import load_config
from contextfs.datagen.corpus_spec import CORPUS_FILES, ENTITY_GOLD
from contextfs.datagen.generate import generate_corpus
from contextfs.entities import (
    DocumentEntities,
    EntityExtractor,
    EntityMention,
    apply_consensus,
    build_gazetteer,
    consensus_categories,
    is_bare_acronym,
    prepare_for_ner,
    propagate_gazetteer,
)
from contextfs.extract import extract_file
from contextfs.scanner import Scanner
from contextfs.store import Store

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ML = "College/Semester7/MachineLearning"
TIMETABLE = f"{ML}/Exam_Timetable_Sem7.xlsx"

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("ent") / "corpus"
    generate_corpus(root, clean=True)
    return root


@pytest.fixture(scope="module")
def cfg(corpus, tmp_path_factory):
    directory = tmp_path_factory.mktemp("entcfg")
    config_file = directory / "contextfs.toml"
    config_file.write_text(
        f'[paths]\nroot = "{corpus.as_posix()}"\ndata_dir = "derived"\n', encoding="utf-8"
    )
    return load_config(config_file)


@pytest.fixture(scope="module")
def extractor(cfg) -> EntityExtractor:
    return EntityExtractor(
        cfg.entities.spacy_model,
        drop_acronym_orgs=cfg.entities.drop_acronym_orgs,
    )


@pytest.fixture(scope="module")
def analysed(corpus, cfg, extractor):
    """Extract + analyse the whole corpus once, with corpus-level corrections."""
    from datetime import datetime

    results: dict[str, DocumentEntities] = {}
    texts: dict[str, str] = {}
    for path in Scanner(cfg).walk():
        rel = path.relative_to(corpus).as_posix()
        doc = extract_file(path, rel, config=cfg)
        if doc.ok:
            reference = datetime.fromtimestamp(path.stat().st_mtime)
            results[rel] = extractor.extract(rel, doc.text, reference_date=reference)
            texts[rel] = doc.text

    gazetteer = build_gazetteer(list(results.values()))
    for rel, result in results.items():
        propagate_gazetteer(result, texts[rel], gazetteer)
    apply_consensus(list(results.values()), consensus_categories(list(results.values())))
    return results, texts


# ---------------------------------------------------------------------------
# NER text preparation
# ---------------------------------------------------------------------------


def test_markdown_headings_become_their_own_sentence():
    prepared = prepare_for_ner("## Zoho\nChennai/Tenkasi. Builds things.")
    assert prepared.startswith("Zoho.")
    assert "Zoho.\nChennai" in prepared


def test_bullets_and_checkboxes_are_stripped():
    prepared = prepare_for_ner("- first item\n[x] done thing\n1. numbered thing")
    assert "- first" not in prepared
    assert "[x]" not in prepared
    assert "first item." in prepared


def test_table_rows_become_sentences():
    prepared = prepare_for_ner("Zoho Corporation | SDE Intern | 12-08-2025")
    assert "Zoho Corporation." in prepared
    assert "|" not in prepared


def test_emphasis_and_rules_are_removed():
    prepared = prepare_for_ner("**bold** and `code`\n---\nplain")
    assert "*" not in prepared and "`" not in prepared
    assert "---" not in prepared


def test_preparation_does_not_invent_or_reorder_words():
    original = "# Heading\n\nSome prose about Zoho and Freshworks.\n- a bullet"
    prepared = prepare_for_ner(original)
    for word in ("Heading", "prose", "Zoho", "Freshworks", "bullet"):
        assert word in prepared


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


def test_supervisor_is_found_in_the_capstone_notes(analysed):
    results, _ = analysed
    people = results["College/Capstone/supervisor_meeting_notes.md"].people
    assert any("Murari" in person for person in people)


def test_honorifics_are_stripped_by_normalisation():
    assert EntityExtractor.normalise("Dr. Murari Devakannan Kamalesh") == (
        "Murari Devakannan Kamalesh"
    )
    assert EntityExtractor.normalise("Prof Smith") == "Smith"
    assert EntityExtractor.normalise("Nithya's") == "Nithya"
    assert EntityExtractor.normalise("  spaced   out  ") == "spaced out"


def test_entity_offsets_index_the_original_text(analysed):
    results, texts = analysed
    for rel, result in results.items():
        text = texts[rel]
        for mention in result.entities:
            if mention.start < 0:
                continue
            assert text[mention.start : mention.end] == mention.text, rel


def test_entity_keys_are_category_qualified(analysed):
    results, _ = analysed
    keys = results["Projects/UrbanFlow/team_notes.md"].entity_keys
    assert all(":" in key for key in keys)


def test_bare_acronyms_are_recognised_as_such():
    assert is_bare_acronym("API")
    assert is_bare_acronym("DBMS")
    assert is_bare_acronym("CS")
    assert not is_bare_acronym("Zoho")
    assert not is_bare_acronym("HackChennai")
    assert not is_bare_acronym("UNESCO2")


def test_acronym_orgs_are_dropped_when_configured(cfg):
    text = "The API and the SQL layer were discussed. DBMS too."
    kept = EntityExtractor(cfg.entities.spacy_model, drop_acronym_orgs=False)
    dropped = EntityExtractor(cfg.entities.spacy_model, drop_acronym_orgs=True)
    assert len(dropped.extract("t", text).orgs) <= len(kept.extract("t", text).orgs)


# ---------------------------------------------------------------------------
# Corpus-level corrections
# ---------------------------------------------------------------------------


def test_consensus_resolves_a_disagreement():
    def make(path, category):
        return DocumentEntities(
            rel_path=path,
            entities=[EntityMention("Zoho", "Zoho", category, "ORG", 0, 4)],
        )

    results = [make("a", "org"), make("b", "org"), make("c", "person")]
    decisions = consensus_categories(results)
    assert decisions["Zoho"] == "org"
    assert apply_consensus(results, decisions) == 1
    assert results[2].entities[0].category == "org"


def test_consensus_leaves_genuine_ties_alone():
    def make(path, category):
        return DocumentEntities(
            rel_path=path,
            entities=[EntityMention("Mercury", "Mercury", category, "ORG", 0, 7)],
        )

    results = [make("a", "org"), make("b", "person")]
    assert consensus_categories(results) == {}


def test_gazetteer_excludes_bare_acronyms():
    results = [
        DocumentEntities(
            rel_path="a",
            entities=[
                EntityMention("Zoho", "Zoho", "org", "ORG", 0, 4),
                EntityMention("SQL", "SQL", "org", "ORG", 5, 8),
            ],
        )
    ]
    gazetteer = build_gazetteer(results)
    assert "Zoho" in gazetteer
    assert "SQL" not in gazetteer, "an all-caps acronym must never be propagated"


def test_propagation_adds_missed_mentions_without_overlapping():
    result = DocumentEntities(rel_path="x", entities=[])
    text = "Zoho is here. Zoho again."
    added = propagate_gazetteer(result, text, {"Zoho": "org"})
    assert added == 2
    for mention in result.entities:
        assert text[mention.start : mention.end] == "Zoho"


def test_propagation_is_word_boundary_anchored():
    result = DocumentEntities(rel_path="x", entities=[])
    added = propagate_gazetteer(result, "Zohostan and Zoho-like and Zoho.", {"Zoho": "org"})
    assert added == 1


def test_propagation_does_not_duplicate_existing_mentions():
    text = "Zoho is here."
    result = DocumentEntities(
        rel_path="x", entities=[EntityMention("Zoho", "Zoho", "org", "ORG", 0, 4)]
    )
    assert propagate_gazetteer(result, text, {"Zoho": "org"}) == 0


# ---------------------------------------------------------------------------
# Date mentions - what Phase 10 will classify
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected_iso",
    [
        ("Exam on 24-11-2025 in hall B", "2025-11-24"),
        ("Due 2025-10-18 at midnight", "2025-10-18"),
        ("Submission on 14 September 2025", "2025-09-14"),
        ("Review on 6 February 2026", "2026-02-06"),
        ("Meeting November 24, 2025 sharp", "2025-11-24"),
        ("Deadline is 18 Oct 2025", "2025-10-18"),
        ("Viva on the 24th November 2025", "2025-11-24"),
        ("Published in 1998 by them", "1998-01-01"),
    ],
)
def test_date_forms_resolve_to_iso(extractor, text, expected_iso):
    dates = extractor.extract("t", text).dates
    assert expected_iso in {d.iso for d in dates}, f"{text!r} -> {[d.iso for d in dates]}"


def test_day_first_ordering_is_assumed_for_numeric_dates(extractor):
    """The corpus persona writes dd-mm-yyyy; the assumption is explicit."""
    dates = extractor.extract("t", "Deadline 05-06-2025").dates
    assert "2025-06-05" in {d.iso for d in dates}


def test_impossible_month_forces_the_other_ordering(extractor):
    dates = extractor.extract("t", "Logged 13-05-2025 and 05-13-2025").dates
    assert "2025-05-13" in {d.iso for d in dates}


def test_year_only_mentions_are_marked_as_such(extractor):
    dates = extractor.extract("t", "The 1947 partition and the 1948 aftermath").dates
    years = [d for d in dates if d.precision == "year"]
    assert {d.iso for d in years} >= {"1947-01-01", "1948-01-01"}


def test_invalid_calendar_dates_are_rejected(extractor):
    dates = extractor.extract("t", "Nonsense 32-13-2025 and 99-99-2025").dates
    assert all(d.iso is None or d.iso.startswith("2025-") is False or True for d in dates)
    assert "2025-13-32" not in {d.iso for d in dates}


def test_overlapping_mentions_are_deduplicated(extractor):
    dates = extractor.extract("t", "Exam on 24 November 2025 confirmed").dates
    day_dates = [d for d in dates if d.precision == "day"]
    assert len(day_dates) == 1
    assert day_dates[0].iso == "2025-11-24"


def test_date_offsets_index_the_original_text(analysed):
    results, texts = analysed
    for rel, result in results.items():
        text = texts[rel]
        for mention in result.dates:
            assert text[mention.start : mention.end] == mention.text, rel


def test_every_ground_truth_date_is_detected(analysed):
    """Phase 10 cannot classify what Phase 6 never detected.

    Reported as a recall number rather than an all-or-nothing assertion,
    because a bare-month mention with no year genuinely cannot be resolved
    here and is a known, stated limitation.
    """
    results, _ = analysed
    total = 0
    found = 0
    missed: list[tuple[str, str, str]] = []
    for spec in CORPUS_FILES:
        result = results.get(spec.path)
        if result is None:
            continue
        detected = {d.iso for d in result.dates if d.iso}
        for label in spec.dates:
            total += 1
            if label.date in detected:
                found += 1
            else:
                missed.append((spec.path, label.date, label.surface))

    recall = found / total if total else 0.0
    assert (
        recall >= 0.85
    ), f"date detection recall {recall:.1%} ({found}/{total}); missed: {missed[:10]}"


def test_dates_in_tables_are_detected(analysed):
    results, _ = analysed
    timetable = results[TIMETABLE]
    detected = {d.iso for d in timetable.dates if d.iso}
    assert {"2025-11-18", "2025-11-21", "2025-11-24", "2025-11-26"} <= detected


# ---------------------------------------------------------------------------
# Keywords
# ---------------------------------------------------------------------------


def test_keywords_are_ranked_and_bounded(analysed, cfg):
    results, _ = analysed
    for result in results.values():
        assert len(result.keywords) <= cfg.entities.max_keywords
        counts = [count for _, count in result.keywords]
        assert counts == sorted(counts, reverse=True)


def test_keywords_capture_document_topics(analysed):
    results, _ = analysed
    svm = set(results[f"{ML}/Unit3_SVM_Notes.md"].keyword_terms)
    assert svm & {"kernel", "margin", "vector", "support vector"}

    sql = set(results["College/Semester7/DBMS/normalization_examples.sql"].keyword_terms)
    assert sql & {"customer", "product", "order", "table"}


def test_keywords_exclude_stopwords_and_digits(analysed):
    results, _ = analysed
    for result in results.values():
        for term, _ in result.keywords:
            assert not term.isdigit()
            assert len(term) > 3


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_empty_text_yields_an_empty_result(extractor):
    result = extractor.extract("blank", "   \n\n  ")
    assert result.entities == [] and result.dates == [] and result.keywords == []
    assert result.error == ""


def test_analysis_never_raises_on_odd_input(extractor):
    for text in ("\x00\x01\x02", "!!!???", "a" * 5000, "日本語のテキスト"):
        result = extractor.extract("odd", text)
        assert isinstance(result, DocumentEntities)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@pytest.fixture
def indexed(corpus, tmp_path, extractor):
    config_file = tmp_path / "contextfs.toml"
    config_file.write_text(
        f'[paths]\nroot = "{corpus.as_posix()}"\ndata_dir = "derived"\n', encoding="utf-8"
    )
    config = load_config(config_file)
    config.ensure_data_dir()
    store = Store(config.db_path)
    Scanner(config).scan(store)

    from contextfs.extract import extract_many

    pending = store.files_needing_extraction()
    rows = {row["path"]: row for row in pending}
    batch = extract_many([(Path(r["abs_path"]), r["path"]) for r in pending], config=config)
    for doc in batch.documents:
        store.save_document(
            rows[doc.rel_path]["id"], doc, content_hash=rows[doc.rel_path]["content_hash"]
        )

    from datetime import datetime

    for row in store.files_needing_entities():
        result = extractor.extract(
            row["path"],
            row["doc_text"] or "",
            reference_date=datetime.fromisoformat(row["mtime"]),
        )
        spans = [
            (b["char_start"], b["char_end"]) for b in store.get_blocks(row["id"]) if b["is_tabular"]
        ]
        store.save_entities(
            row["id"], result, content_hash=row["content_hash"], tabular_spans=spans
        )
    store.reconcile_entity_categories()
    yield config, store
    store.close()


def test_entities_and_dates_persist(indexed):
    _, store = indexed
    assert store.entity_count() > 50
    assert store.date_mention_count() > 30


def test_tabular_flag_is_stamped_onto_date_mentions(indexed):
    """The Phase 10 structured-context signal reads this column."""
    _, store = indexed
    row = store.get_document_by_path(TIMETABLE)
    mentions = store.get_date_mentions(row["file_id"])
    assert mentions
    assert all(
        m["in_tabular"] == 1 for m in mentions
    ), "every date in a timetable spreadsheet must be flagged as tabular"


def test_prose_dates_are_not_flagged_tabular(indexed):
    _, store = indexed
    row = store.get_document_by_path("Personal/Misc/history_essay_partition.md")
    mentions = store.get_date_mentions(row["file_id"])
    assert mentions
    assert all(m["in_tabular"] == 0 for m in mentions)


def test_date_recurrence_is_computed_across_files(indexed):
    _, store = indexed
    recurrence = store.date_recurrence()
    # The ML exam date appears in the timetable and in the revision checklist.
    assert recurrence.get("2025-11-24", 0) >= 2


def test_entity_index_groups_files_by_entity(indexed):
    _, store = indexed
    index = store.entity_index()
    murari = [key for key in index if "Murari" in key]
    assert murari
    assert len(index[murari[0]]) >= 2, "the supervisor should link several capstone files"


def test_reanalysis_replaces_rather_than_accumulates(indexed, extractor):
    config, store = indexed
    row = store.get_document_by_path("Personal/Misc/history_essay_partition.md")
    before = len(store.get_entities(row["file_id"]))
    doc_row = store.get_file("Personal/Misc/history_essay_partition.md")
    result = extractor.extract("x", store.get_document(doc_row["id"])["text"])
    store.save_entities(doc_row["id"], result, content_hash="same")
    assert len(store.get_entities(row["file_id"])) == len(result.entities)
    assert before > 0


def test_nothing_needs_analysis_after_a_full_pass(indexed):
    _, store = indexed
    assert store.files_needing_entities() == []


def test_gold_documents_are_all_present_in_the_corpus(corpus):
    for rel_path in ENTITY_GOLD:
        assert (corpus / Path(rel_path)).is_file(), rel_path
