"""Phase 9 tests: the relationship graph and its four edge types."""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from contextfs.config import load_config
from contextfs.datagen.corpus_spec import CORPUS_FILES
from contextfs.datagen.generate import generate_corpus
from contextfs.embed import Embedder, VectorStore, embed_documents
from contextfs.entities import EntityExtractor
from contextfs.extract import extract_many
from contextfs.graph import (
    EDGE_TYPES,
    build_graph,
    graph_stats,
    load_graph,
    neighbours,
    node_id,
    save_graph,
    shortest_explained_path,
)
from contextfs.scanner import Scanner
from contextfs.store import Store

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CFG = load_config(PROJECT_ROOT / "contextfs.toml")

ML = "College/Semester7/MachineLearning"
DBMS = "College/Semester7/DBMS"
UF = "Projects/UrbanFlow"
CAP = "College/Capstone"

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """A fully indexed corpus with its relationship graph."""
    base = tmp_path_factory.mktemp("graph")
    corpus = base / "corpus"
    generate_corpus(corpus, clean=True)
    config_file = base / "contextfs.toml"
    config_file.write_text(
        f'[paths]\nroot = "{corpus.as_posix()}"\ndata_dir = "derived"\n', encoding="utf-8"
    )
    cfg = load_config(config_file)
    cfg.ensure_data_dir()
    store = Store(cfg.db_path)
    Scanner(cfg).scan(store)

    pending = store.files_needing_extraction()
    rows = {r["path"]: r for r in pending}
    batch = extract_many([(Path(r["abs_path"]), r["path"]) for r in pending], config=cfg)
    for doc in batch.documents:
        store.save_document(rows[doc.rel_path]["id"], doc, rows[doc.rel_path]["content_hash"])

    from datetime import datetime

    extractor = EntityExtractor(cfg.entities.spacy_model)
    for row in store.files_needing_entities():
        result = extractor.extract(
            row["path"], row["doc_text"] or "", reference_date=datetime.fromisoformat(row["mtime"])
        )
        spans = [
            (b["char_start"], b["char_end"]) for b in store.get_blocks(row["id"]) if b["is_tabular"]
        ]
        store.save_entities(row["id"], result, row["content_hash"], spans)
    store.reconcile_entity_categories()

    vectors = VectorStore(cfg.vector_dir, cfg.embeddings.dimension)
    embed_documents(store, vectors, Embedder(cfg.embeddings.model), cfg)

    graph, report = build_graph(store, vectors, cfg)
    ids = {row["path"]: row["id"] for row in store.all_files()}
    yield graph, report, store, ids, cfg
    store.close()


def nid(ids, path):
    """Graph node id for a corpus-relative path."""
    return node_id(ids[path])


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_every_file_becomes_a_node(built):
    graph, report, _, _, _ = built
    assert report.nodes == 40
    assert graph.number_of_nodes() == 40


def test_graph_is_a_multidigraph(built):
    graph, _, _, _, _ = built
    assert isinstance(graph, nx.MultiDiGraph)


def test_nodes_carry_the_metadata_explanations_need(built):
    graph, _, _, ids, _ = built
    data = graph.nodes[nid(ids, f"{ML}/Unit3_SVM_Notes.md")]
    assert data["path"].endswith("Unit3_SVM_Notes.md")
    assert data["ext"] == ".md"
    assert data["folder"] == ML
    assert data["mtime"]


def test_all_edges_have_a_known_type_and_a_bounded_weight(built):
    graph, _, _, _, _ = built
    for _, _, data in graph.edges(data=True):
        assert data["type"] in EDGE_TYPES
        assert 0.0 <= data["weight"] <= 1.0, data


def test_symmetric_relations_are_stored_both_ways(built):
    graph, _, _, _, _ = built
    for left, right, data in list(graph.edges(data=True)):
        if data["type"] in {"semantic", "entity", "structural", "duplicate"}:
            assert graph.has_edge(right, left), f"{data['type']} edge is not symmetric"


def test_parallel_edges_of_different_types_coexist(built):
    """The reason for MultiDiGraph: several relations between the same pair."""
    graph, _, _, ids, _ = built
    left = nid(ids, f"{DBMS}/Assignment2_Normalization_draft.docx")
    right = nid(ids, f"{DBMS}/Assignment2_Normalization_final.docx")
    kinds = {data["type"] for data in graph.get_edge_data(left, right).values()}
    assert len(kinds) >= 3, f"expected several relation types, got {kinds}"


# ---------------------------------------------------------------------------
# THE planted duplicates - the phase's stated manual check
# ---------------------------------------------------------------------------


def test_planted_near_duplicates_are_linked(built):
    """Both deliberately planted near-duplicate pairs must get duplicate edges."""
    graph, _, _, ids, _ = built
    planted = [
        (spec.path, spec.near_duplicate_of) for spec in CORPUS_FILES if spec.near_duplicate_of
    ]
    assert len(planted) == 2

    for path, original in planted:
        left, right = nid(ids, path), nid(ids, original)
        data = graph.get_edge_data(left, right) or {}
        kinds = {entry["type"] for entry in data.values()}
        assert "duplicate" in kinds, f"{path} was not linked to {original} as a duplicate"


def test_duplicate_edges_carry_their_evidence(built):
    graph, report, _, _, _ = built
    assert report.duplicate_pairs
    for _, _, score in report.duplicate_pairs:
        assert score >= CFG.graph.duplicate_threshold
    for _, _, data in graph.edges(data=True):
        if data["type"] == "duplicate":
            assert "jaccard" in data and "similarity" in data


def test_jaccard_separates_duplicates_far_better_than_cosine(built):
    """The measurement that justified switching the duplicate signal.

    If this margin ever collapses, the choice of Jaccard needs revisiting -
    so the property is asserted rather than left in a comment.
    """
    from contextfs.embed import VectorStore
    from contextfs.graph import jaccard, shingles

    _, _, store, ids, cfg = built
    vectors = VectorStore(cfg.vector_dir, cfg.embeddings.dimension)
    file_ids, _matrix = vectors.document_vectors()

    texts = {row["file_id"]: shingles(row["text"]) for row in store.all_documents()}
    planted = {
        frozenset({spec.path, spec.near_duplicate_of})
        for spec in CORPUS_FILES
        if spec.near_duplicate_of
    }

    duplicate_jaccard, other_jaccard = [], []
    paths = {file_id: path for path, file_id in ids.items()}
    for i, left in enumerate(file_ids):
        for right in file_ids[i + 1 :]:
            score = jaccard(texts.get(left, set()), texts.get(right, set()))
            if frozenset({paths[left], paths[right]}) in planted:
                duplicate_jaccard.append(score)
            else:
                other_jaccard.append(score)

    assert min(duplicate_jaccard) > max(other_jaccard) * 5, (
        f"jaccard separation collapsed: duplicates {min(duplicate_jaccard):.3f} "
        f"vs best non-duplicate {max(other_jaccard):.3f}"
    )


def test_no_spurious_duplicates(built):
    """Unrelated files must not be flagged as near-duplicates."""
    _, report, _, _, _ = built
    planted = {
        frozenset({spec.path, spec.near_duplicate_of})
        for spec in CORPUS_FILES
        if spec.near_duplicate_of
    }
    detected = {frozenset({left, right}) for left, right, _ in report.duplicate_pairs}
    assert detected <= planted, f"unexpected duplicate pairs: {detected - planted}"


# ---------------------------------------------------------------------------
# Edge types
# ---------------------------------------------------------------------------


def test_all_four_edge_types_are_produced(built):
    _, report, _, _, _ = built
    for kind in ("semantic", "entity", "structural", "duplicate"):
        assert report.by_type.get(kind, 0) > 0, f"no {kind} edges were built"


def test_structural_edges_link_files_in_one_folder(built):
    graph, _, _, ids, _ = built
    left = nid(ids, f"{UF}/app.py")
    right = nid(ids, f"{UF}/traffic_model.py")
    data = graph.get_edge_data(left, right) or {}
    structural = [entry for entry in data.values() if entry["type"] == "structural"]
    assert structural
    assert structural[0]["relation"] == "same_folder"


def test_structural_edges_do_not_link_unrelated_trees(built):
    graph, _, _, ids, _ = built
    left = nid(ids, "Personal/Misc/recipe_biryani.txt")
    right = nid(ids, f"{ML}/Unit3_SVM_Notes.md")
    data = graph.get_edge_data(left, right) or {}
    assert not [entry for entry in data.values() if entry["type"] == "structural"]


def test_entity_edges_link_the_capstone_documents(built):
    """The supervisor's name should connect the capstone files."""
    graph, _, _, ids, _ = built
    left = nid(ids, f"{CAP}/supervisor_meeting_notes.md")
    right = nid(ids, f"{CAP}/ContextFS_Proposal.docx")
    data = graph.get_edge_data(left, right) or {}
    entity_edges = [entry for entry in data.values() if entry["type"] == "entity"]
    assert entity_edges, "no entity edge between the supervisor notes and the proposal"
    assert entity_edges[0]["shared_count"] >= CFG.graph.min_shared_entities


def test_entity_edges_record_what_was_shared(built):
    """An explanation must be able to name the shared entities."""
    graph, _, _, _, _ = built
    for _, _, data in graph.edges(data=True):
        if data["type"] == "entity":
            assert data["shared"], "entity edge carries no evidence"
            assert all(":" in key for key in data["shared"])


def test_semantic_edges_respect_the_threshold(built):
    graph, _, _, _, _ = built
    for _, _, data in graph.edges(data=True):
        if data["type"] == "semantic":
            assert data["similarity"] >= CFG.graph.semantic_edge_threshold


def test_semantic_degree_is_capped(built):
    """A dense graph stops discriminating; the per-node cap must hold."""
    graph, _, _, _, _ = built
    for node in graph.nodes:
        outgoing = [
            target
            for _, target, data in graph.out_edges(node, data=True)
            if data["type"] == "semantic"
        ]
        # The cap applies per source; symmetry can add a few inbound partners.
        assert len(outgoing) <= CFG.graph.semantic_edges_per_node * 2


def test_semantically_related_ml_files_are_linked(built):
    graph, _, _, ids, _ = built
    left = nid(ids, f"{ML}/Unit3_SVM_Notes.md")
    right = nid(ids, f"{ML}/Unit4_Ensemble_Methods.pdf")
    data = graph.get_edge_data(left, right) or {}
    assert data, "the two ML lecture documents are not connected at all"


# ---------------------------------------------------------------------------
# Statistics and traversal
# ---------------------------------------------------------------------------


def test_graph_stats_report_is_complete(built):
    graph, _, _, _, _ = built
    stats = graph_stats(graph)
    for key in ("nodes", "edges", "by_type", "mean_degree", "connected_components", "density"):
        assert key in stats
    assert stats["nodes"] == 40


def test_the_graph_is_connected_enough_to_traverse(built):
    graph, report, _, _, _ = built
    stats = graph_stats(graph)
    assert stats["connected_components"] <= 3, (
        f"graph is fragmented into {stats['connected_components']} components; "
        "traversal-based retrieval would be crippled"
    )
    assert not report.isolated, f"isolated files cannot be reached by traversal: {report.isolated}"


def test_neighbours_are_ranked_by_weight(built):
    graph, _, _, ids, _ = built
    ranked = neighbours(graph, nid(ids, f"{ML}/Unit3_SVM_Notes.md"))
    assert ranked
    weights = [weight for _, _, weight in ranked]
    assert weights == sorted(weights, reverse=True)


def test_neighbours_can_be_filtered_by_edge_type(built):
    """The mechanism the Phase 22 ablation relies on."""
    graph, _, _, ids, _ = built
    node = nid(ids, f"{ML}/Unit3_SVM_Notes.md")
    only_structural = neighbours(graph, node, edge_types={"structural"})
    assert only_structural
    assert all(kind == "structural" for _, kind, _ in only_structural)


def test_explained_path_names_each_hop(built):
    """Phase 16 needs the path *and* the reason for each hop."""
    graph, _, _, ids, _ = built
    path = shortest_explained_path(
        graph,
        nid(ids, f"{ML}/Exam_Timetable_Sem7.xlsx"),
        nid(ids, f"{ML}/Unit4_Ensemble_Methods.pdf"),
    )
    assert path is not None
    for source, kind, target in path:
        assert kind in EDGE_TYPES
        assert graph.has_node(source) and graph.has_node(target)


def test_path_between_unrelated_files_can_still_be_found_or_reported(built):
    graph, _, _, ids, _ = built
    result = shortest_explained_path(
        graph,
        nid(ids, "Personal/Misc/recipe_biryani.txt"),
        nid(ids, f"{DBMS}/normalization_examples.sql"),
        edge_types={"duplicate"},
    )
    assert result is None, "restricting to duplicate edges should not connect these"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_graph_roundtrips_through_json(built, tmp_path):
    graph, _, _, _, _ = built
    path = tmp_path / "graph.json"
    save_graph(graph, path)
    assert path.is_file()

    reloaded = load_graph(path)
    assert reloaded.number_of_nodes() == graph.number_of_nodes()
    assert reloaded.number_of_edges() == graph.number_of_edges()
    assert graph_stats(reloaded)["by_type"] == graph_stats(graph)["by_type"]


def test_saved_graph_is_human_readable_json(built, tmp_path):
    """Auditability: an explainable system's index should be inspectable."""
    import json

    graph, _, _, _, _ = built
    path = tmp_path / "graph.json"
    save_graph(graph, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "nodes" in data and "links" in data
    assert any("path" in node for node in data["nodes"])


def test_loading_a_missing_graph_returns_an_empty_graph(tmp_path):
    assert load_graph(tmp_path / "nope.json").number_of_nodes() == 0


def test_building_without_vectors_still_produces_a_graph(built):
    _, _, store, _, cfg = built
    graph, report = build_graph(store, None, cfg)
    assert report.nodes == 40
    assert report.by_type.get("structural", 0) > 0
    assert report.by_type.get("semantic", 0) == 0
    assert report.by_type.get("duplicate", 0) == 0
