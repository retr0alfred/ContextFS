"""Phase 8 tests: the semantic tree and its summaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from contextfs.config import load_config
from contextfs.datagen.generate import generate_corpus
from contextfs.extract import extract_many
from contextfs.scanner import Scanner
from contextfs.store import Store
from contextfs.summarize import OllamaSummarizer, Summarizer, extractive_summary
from contextfs.tree import SemanticTree, TreeNode, build_tree

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ML = "College/Semester7/MachineLearning"


@pytest.fixture(scope="module")
def indexed(tmp_path_factory):
    """A scanned + extracted corpus (no embeddings: the tree does not need them)."""
    base = tmp_path_factory.mktemp("tree")
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
    yield cfg, store
    store.close()


@pytest.fixture(scope="module")
def built(indexed):
    cfg, store = indexed
    tree, report = build_tree(store, None, Summarizer(cfg))
    return tree, report, store


# ---------------------------------------------------------------------------
# Extractive summarisation (must work with no LLM at all)
# ---------------------------------------------------------------------------


def test_extractive_summary_selects_sentences():
    text = (
        "The UrbanFlow controller reallocates green time every cycle. "
        "It uses cheap ultrasonic sensors instead of inductive loops. "
        "The fairness floor prevents any approach from starving. "
        "Karan has the HDMI adapter. "
        "Mean wait dropped from 41.2 seconds to 28.7 seconds."
    )
    result = extractive_summary(text, max_sentences=2)
    assert result.ok
    assert result.backend == "extractive"
    assert result.sentence_count == 2


def test_extractive_summary_preserves_document_order():
    text = " ".join(f"Sentence number {i} about retrieval and indexing systems." for i in range(8))
    result = extractive_summary(text, max_sentences=3)
    numbers = [int(part) for part in result.text.split() if part.isdigit()]
    assert numbers == sorted(numbers), "summary sentences were reordered"


def test_extractive_summary_handles_short_and_empty_input():
    assert extractive_summary("").text == ""
    short = extractive_summary("Just one short line of text here.")
    assert short.ok


def test_extractive_summary_accepts_a_title():
    result = extractive_summary("Some content about databases and joins.", title="DBMS notes")
    assert result.text.startswith("DBMS notes:")


def test_summarizer_falls_back_to_extractive_without_ollama(indexed):
    cfg, _ = indexed
    summarizer = Summarizer(cfg)
    assert summarizer.backend_name == "extractive"
    assert summarizer.summarize("Some text about exams and revision schedules.").ok


def test_ollama_client_refuses_a_remote_endpoint():
    with pytest.raises(ValueError, match="no remote inference"):
        OllamaSummarizer("llama3", "https://api.example.com")


def test_ollama_client_accepts_loopback():
    client = OllamaSummarizer("llama3", "http://127.0.0.1:11434")
    assert client.endpoint == "http://127.0.0.1:11434"


# ---------------------------------------------------------------------------
# Tree structure
# ---------------------------------------------------------------------------


def test_tree_has_the_expected_node_kinds(built):
    tree, _, _ = built
    stats = tree.stats()
    assert stats["root"] == 1
    assert stats["file"] == 40
    assert stats["project"] >= 3
    assert stats["chunk"] > 40


def test_project_nodes_match_top_level_directories(built):
    tree, _, _ = built
    projects = {node.label for node in tree.nodes_of_kind("project")}
    assert projects == {"College", "Projects", "Personal", "Downloads"}


def test_folder_nodes_match_the_corpus_structure(built):
    tree, _, _ = built
    folders = {node.rel_path for node in tree.nodes_of_kind("folder")}
    assert "College/Semester7" in folders
    assert "College/Semester7/MachineLearning" in folders
    assert "Personal/Career" in folders


def test_every_file_node_is_reachable_from_root(built):
    """The phase's stated verification: no orphaned file nodes."""
    tree, _, _ = built
    reachable = tree.reachable_from_root()
    for node in tree.file_nodes():
        assert node.node_id in reachable, f"orphaned file node: {node.rel_path}"
    assert tree.orphans() == []


def test_every_node_is_reachable_from_root(built):
    tree, _, _ = built
    assert len(tree.reachable_from_root()) == len(tree.nodes)


def test_file_counts_roll_up_correctly(built):
    tree, _, _ = built
    root = tree.get(SemanticTree.ROOT_ID)
    assert root.file_count == 40
    for project in tree.nodes_of_kind("project"):
        descendant_files = [n for n in tree.descendants(project.node_id) if n.kind == "file"]
        assert project.file_count == len(descendant_files)


def test_ancestors_reach_the_root(built):
    tree, _, _ = built
    node = next(n for n in tree.file_nodes() if n.rel_path == f"{ML}/Unit3_SVM_Notes.md")
    labels = [ancestor.label for ancestor in tree.ancestors(node.node_id)]
    assert labels == ["MachineLearning", "Semester7", "College", "corpus"]


def test_path_to_root_is_human_readable(built):
    tree, _, _ = built
    node = next(n for n in tree.file_nodes() if n.rel_path == "Projects/UrbanFlow/app.py")
    assert tree.path_to_root(node.node_id) == ["app.py", "UrbanFlow", "Projects", "corpus"]


def test_chunk_nodes_hang_off_their_file(built):
    tree, _, _ = built
    node = next(n for n in tree.file_nodes() if n.rel_path == f"{ML}/Exam_Timetable_Sem7.xlsx")
    chunks = tree.children_of(node.node_id)
    assert chunks
    assert all(child.kind == "chunk" for child in chunks)
    assert all(child.file_id == node.file_id for child in chunks)


def test_depths_are_consistent(built):
    tree, _, _ = built
    for node in tree.nodes.values():
        if node.parent_id:
            assert node.depth == tree.get(node.parent_id).depth + 1, node.node_id


def test_render_produces_indented_output(built):
    tree, _, _ = built
    lines = tree.render(max_depth=2)
    assert lines[0].endswith("corpus")
    assert any("College" in line for line in lines)


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------


def test_every_non_leaf_node_has_a_summary(built):
    tree, _, _ = built
    missing = [
        node.node_id
        for node in tree.nodes.values()
        if node.kind in {"root", "project", "folder", "file"} and not node.summary.strip()
    ]
    assert not missing, f"nodes without summaries: {missing[:5]}"


def test_summary_count_matches_the_structure(built):
    """The phase's stated verification: summary count matches folder/project structure."""
    tree, report, _ = built
    stats = tree.stats()
    expected = stats["root"] + stats["project"] + stats["folder"] + stats["file"]
    assert report.summaries == expected


def test_summaries_come_from_the_extractive_backend_without_ollama(built):
    _, report, _ = built
    assert report.summary_backend == "extractive"
    assert report.llm_fallbacks == 0, "no LLM was configured, so nothing should have fallen back"


def test_folder_summaries_describe_their_contents(built):
    tree, _, _ = built
    node = next(n for n in tree.nodes_of_kind("folder") if n.rel_path == ML)
    text = node.summary.lower()
    assert any(
        term in text for term in ("ensemble", "vector", "exam", "machine", "kernel")
    ), f"unhelpful folder summary: {node.summary[:200]}"


def test_every_summary_is_bounded_in_length(built):
    """A summary longer than its source is not a summary.

    This caught a real defect: for spreadsheets, table rows have no terminal
    punctuation, so sentence-count limiting bounded nothing and one file's
    "summary" came out at 856 characters for an 833-character document.
    """
    from contextfs.summarize import MAX_SUMMARY_CHARS

    tree, _, store = built
    for node in tree.nodes.values():
        if node.kind == "chunk" or not node.summary:
            continue
        allowance = MAX_SUMMARY_CHARS + len(node.label) + 10
        assert len(node.summary) <= allowance, f"{node.node_id}: {len(node.summary)} chars"


def test_file_summaries_are_shorter_than_their_documents(built):
    tree, _, store = built
    for node in tree.file_nodes():
        document = store.get_document(node.file_id)
        if document and document["char_count"] > 900:
            assert len(node.summary) < document["char_count"], node.rel_path


def test_folder_summaries_do_not_grow_up_the_tree(built):
    """A parent summary must not be longer than the sum of its children's."""
    tree, _, _ = built
    for node in tree.nodes_of_kind("folder"):
        children = [c for c in tree.children_of(node.node_id) if c.summary]
        if len(children) > 1:
            assert len(node.summary) <= sum(len(c.summary) for c in children)


def test_building_without_a_summarizer_still_produces_structure(indexed):
    _, store = indexed
    tree, report = build_tree(store, None, None)
    assert report.summaries == 0
    assert tree.stats()["file"] == 40
    assert tree.orphans() == []


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_tree_persists_and_reloads(built):
    tree, _, store = built
    stored = store.save_tree(tree)
    assert stored == len(tree.nodes)
    assert store.tree_node_count() == len(tree.nodes)

    projects = store.tree_nodes("project")
    assert {row["label"] for row in projects} == {
        "College",
        "Projects",
        "Personal",
        "Downloads",
    }


def test_saving_twice_does_not_duplicate(built):
    tree, _, store = built
    store.save_tree(tree)
    first = store.tree_node_count()
    store.save_tree(tree)
    assert store.tree_node_count() == first


def test_stored_parent_links_are_valid(built):
    tree, _, store = built
    store.save_tree(tree)
    rows = store.tree_nodes()
    ids = {row["node_id"] for row in rows}
    for row in rows:
        if row["parent_id"]:
            assert row["parent_id"] in ids, f"dangling parent on {row['node_id']}"


# ---------------------------------------------------------------------------
# Tree primitives
# ---------------------------------------------------------------------------


def test_orphan_detection_works():
    tree = SemanticTree()
    tree.nodes["ghost"] = TreeNode(node_id="ghost", kind="file", label="ghost", parent_id="missing")
    assert "ghost" in tree.orphans()


def test_add_links_child_to_parent():
    tree = SemanticTree()
    tree.add(TreeNode(node_id="a", kind="project", label="A", parent_id=SemanticTree.ROOT_ID))
    assert tree.get(SemanticTree.ROOT_ID).children == ["a"]
    assert tree.orphans() == []
