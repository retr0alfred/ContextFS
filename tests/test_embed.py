"""Phase 7 tests: chunking, embedding, and the LanceDB vector store."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from contextfs.config import load_config
from contextfs.datagen.generate import generate_corpus
from contextfs.embed import (
    Chunk,
    Embedder,
    VectorStore,
    chunk_blocks,
    embed_documents,
)
from contextfs.extract import extract_many
from contextfs.scanner import Scanner
from contextfs.store import Store

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CFG = load_config(PROJECT_ROOT / "contextfs.toml")
MODEL = CFG.embeddings.model
DIM = CFG.embeddings.dimension

ML = "College/Semester7/MachineLearning"


class FakeBlock:
    """Minimal stand-in for an extraction block."""

    def __init__(self, text, kind="paragraph", is_tabular=False, start=0, end=None):  # noqa: D107
        self.text = text
        self.kind = kind
        self.is_tabular = is_tabular
        self.char_start = start
        self.char_end = end if end is not None else start + len(text)


# ---------------------------------------------------------------------------
# Chunking (no model needed - fast)
# ---------------------------------------------------------------------------


def test_small_blocks_are_packed_into_one_chunk():
    blocks = [FakeBlock("short paragraph here", start=i * 30) for i in range(3)]
    chunks = chunk_blocks(1, "a.md", blocks, chunk_size_tokens=256, chunk_overlap_tokens=48)
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert "short paragraph here" in chunks[0].text


def test_blocks_are_split_when_they_exceed_the_budget():
    blocks = [FakeBlock(" ".join(["word"] * 100), start=i * 600) for i in range(6)]
    chunks = chunk_blocks(1, "a.md", blocks, chunk_size_tokens=256, chunk_overlap_tokens=48)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.token_estimate <= 300  # packing bound plus one block


def test_an_oversized_single_block_is_split_internally():
    huge = "\n".join(f"line {i} with several words in it" for i in range(300))
    chunks = chunk_blocks(
        1, "big.txt", [FakeBlock(huge)], chunk_size_tokens=128, chunk_overlap_tokens=24
    )
    assert len(chunks) > 2
    assert all(chunk.text.strip() for chunk in chunks)


def test_overlap_repeats_content_across_a_split():
    lines = "\n".join(f"unique line number {i}" for i in range(120))
    chunks = chunk_blocks(
        1, "big.txt", [FakeBlock(lines)], chunk_size_tokens=64, chunk_overlap_tokens=24
    )
    assert len(chunks) >= 2
    first_lines = set(chunks[0].text.splitlines())
    second_lines = set(chunks[1].text.splitlines())
    assert first_lines & second_lines, "no overlap carried across the split"


def test_tabular_flag_propagates_into_chunks():
    blocks = [FakeBlock("Date | Subject\n24-11-2025 | ML", kind="sheet", is_tabular=True)]
    chunks = chunk_blocks(1, "t.xlsx", blocks)
    assert chunks[0].is_tabular
    assert "sheet" in chunks[0].block_kinds


def test_empty_blocks_are_skipped():
    chunks = chunk_blocks(1, "a.md", [FakeBlock("   "), FakeBlock("")])
    assert chunks == []


def test_chunk_indices_are_contiguous():
    blocks = [FakeBlock(" ".join(["w"] * 200), start=i * 900) for i in range(5)]
    chunks = chunk_blocks(1, "a.md", blocks, chunk_size_tokens=100, chunk_overlap_tokens=20)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert len({c.chunk_id for c in chunks}) == len(chunks)


def test_chunking_accepts_sqlite_rows(tmp_path):
    """chunk_blocks must work on stored rows, not just dataclasses."""
    corpus = tmp_path / "corpus"
    generate_corpus(corpus, clean=True)
    config_file = tmp_path / "contextfs.toml"
    config_file.write_text(
        f'[paths]\nroot = "{corpus.as_posix()}"\ndata_dir = "d"\n', encoding="utf-8"
    )
    cfg = load_config(config_file)
    cfg.ensure_data_dir()
    with Store(cfg.db_path) as store:
        Scanner(cfg).scan(store)
        pending = store.files_needing_extraction()
        rows = {r["path"]: r for r in pending}
        batch = extract_many([(Path(r["abs_path"]), r["path"]) for r in pending], config=cfg)
        for doc in batch.documents:
            store.save_document(rows[doc.rel_path]["id"], doc, rows[doc.rel_path]["content_hash"])

        row = store.get_file(f"{ML}/Unit4_Ensemble_Methods.pdf")
        chunks = chunk_blocks(row["id"], row["path"], store.get_blocks(row["id"]))
        assert chunks
        assert all(chunk.text for chunk in chunks)


# ---------------------------------------------------------------------------
# Embedding (model required - marked slow)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def embedder() -> Embedder:
    return Embedder(MODEL, batch_size=8, expected_dimension=DIM)


@pytest.mark.slow
def test_dimension_matches_the_model_specification(embedder):
    assert embedder.dimension == DIM == 384
    embedder.verify_dimension()


@pytest.mark.slow
def test_dimension_mismatch_is_refused():
    wrong = Embedder(MODEL, expected_dimension=768)
    with pytest.raises(ValueError, match="768"):
        wrong.verify_dimension()


@pytest.mark.slow
def test_vectors_are_l2_normalised(embedder):
    vectors = embedder.encode(["hello world", "a completely different sentence"])
    assert vectors.shape == (2, DIM)
    norms = np.linalg.norm(vectors, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


@pytest.mark.slow
def test_encoding_is_deterministic(embedder):
    a = embedder.encode(["support vector machines and the kernel trick"])
    b = embedder.encode(["support vector machines and the kernel trick"])
    assert np.allclose(a, b, atol=1e-6)


@pytest.mark.slow
def test_empty_input_returns_an_empty_matrix(embedder):
    assert embedder.encode([]).shape == (0, DIM)


@pytest.mark.slow
def test_batching_does_not_change_vectors(embedder):
    texts = [f"sentence number {i} about retrieval systems" for i in range(9)]
    small = Embedder(MODEL, batch_size=2).encode(texts)
    large = Embedder(MODEL, batch_size=16).encode(texts)
    assert np.allclose(small, large, atol=1e-4), "padding leaked into pooled vectors"


@pytest.mark.slow
def test_the_two_backends_produce_the_same_vectors():
    """The fast default must be numerically equivalent to the reference.

    This is the test that licenses Decision 40: the ``transformers`` backend is
    only a legitimate substitute for ``sentence-transformers`` if it produces
    the same embeddings, not merely similar ones.
    """
    texts = [
        "the pdf I studied before my machine learning exam",
        "Date | Subject | Hall\n24-11-2025 | Machine Learning | Block B",
        "def allocate(counts):\n    return {k: v / sum(counts.values()) for k, v in counts.items()}",
    ]
    fast = Embedder(MODEL, backend="transformers", batch_size=4).encode(texts)
    reference = Embedder(MODEL, backend="sentence-transformers", batch_size=4).encode(texts)

    cosines = (fast * reference).sum(axis=1)
    assert np.all(cosines > 1 - 1e-4), f"backends disagree; cosines={cosines}"


@pytest.mark.slow
def test_pooling_produces_a_unit_vector(embedder):
    vectors = embedder.encode(["first chunk text", "second chunk text", "third chunk"])
    pooled = Embedder.pool(vectors)
    assert pooled.shape == (DIM,)
    assert np.isclose(np.linalg.norm(pooled), 1.0, atol=1e-5)


@pytest.mark.slow
def test_pooling_an_empty_matrix_is_safe():
    assert Embedder.pool(np.zeros((0, DIM), dtype=np.float32)).shape == (0,)


@pytest.mark.slow
def test_semantically_similar_text_scores_higher_than_unrelated(embedder):
    query = embedder.encode_one("how do bagging and boosting differ")
    related = embedder.encode_one(
        "Bagging draws bootstrap samples and averages; boosting fits learners sequentially."
    )
    unrelated = embedder.encode_one("Soak the basmati rice for thirty minutes, no more.")
    assert float(query @ related) > float(query @ unrelated) + 0.15


@pytest.mark.slow
def test_unknown_backend_is_refused():
    with pytest.raises(ValueError, match="unknown embedding backend"):
        Embedder(MODEL, backend="openai")


# ---------------------------------------------------------------------------
# Vector store
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_vector_store_roundtrip(tmp_path, embedder):
    store = VectorStore(tmp_path / "v.lance", DIM)
    chunks = [
        Chunk(1, "a.md", 0, "ensembles and boosting", 0, 20),
        Chunk(2, "b.md", 0, "biryani recipe with ghee", 0, 24),
    ]
    vectors = embedder.encode([c.text for c in chunks])
    store.add_chunks(chunks, vectors)
    for i, chunk in enumerate(chunks):
        store.add_document(chunk.file_id, chunk.rel_path, vectors[i], 1, len(chunk.text))

    assert store.counts() == {"chunks": 2, "documents": 2}

    hits = store.search_documents(embedder.encode_one("how does boosting work"), limit=2)
    assert hits[0]["rel_path"] == "a.md"
    assert hits[0]["score"] > hits[1]["score"]


@pytest.mark.slow
def test_search_on_an_empty_store_returns_nothing(tmp_path, embedder):
    store = VectorStore(tmp_path / "empty.lance", DIM)
    assert store.search_documents(embedder.encode_one("anything")) == []
    assert store.search_chunks(embedder.encode_one("anything")) == []


@pytest.mark.slow
def test_deleting_a_file_removes_all_its_vectors(tmp_path, embedder):
    store = VectorStore(tmp_path / "v.lance", DIM)
    chunks = [Chunk(7, "gone.md", i, f"chunk {i}", 0, 10) for i in range(3)]
    vectors = embedder.encode([c.text for c in chunks])
    store.add_chunks(chunks, vectors)
    store.add_document(7, "gone.md", Embedder.pool(vectors), 3, 30)
    assert store.counts()["chunks"] == 3

    store.delete_files([7])
    assert store.counts() == {"chunks": 0, "documents": 0}


@pytest.mark.slow
def test_document_vectors_returns_an_aligned_matrix(tmp_path, embedder):
    store = VectorStore(tmp_path / "v.lance", DIM)
    for file_id, text in ((3, "alpha"), (1, "beta"), (2, "gamma")):
        store.add_document(file_id, f"{file_id}.md", embedder.encode_one(text), 1, 5)
    ids, matrix = store.document_vectors()
    assert ids == [1, 2, 3], "ids must be sorted so callers can index the matrix safely"
    assert matrix.shape == (3, DIM)


# ---------------------------------------------------------------------------
# End-to-end over the corpus
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def indexed(tmp_path_factory):
    """A fully scanned, extracted, and embedded corpus."""
    base = tmp_path_factory.mktemp("emb")
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

    vectors = VectorStore(cfg.vector_dir, DIM)
    embedder = Embedder(MODEL, batch_size=cfg.embeddings.batch_size, expected_dimension=DIM)
    report = embed_documents(store, vectors, embedder, cfg)
    yield cfg, store, vectors, embedder, report
    store.close()


@pytest.mark.slow
def test_every_document_is_embedded(indexed):
    _, store, vectors, _, report = indexed
    assert report.files == 40
    assert report.chunks >= 40
    assert vectors.counts()["documents"] == 40
    assert store.embedded_count() == 40


@pytest.mark.slow
def test_nothing_needs_embedding_after_a_full_pass(indexed):
    _, store, _, _, _ = indexed
    assert store.files_needing_embedding() == []


@pytest.mark.slow
def test_nearest_neighbours_of_the_svm_notes_are_sane(indexed):
    """Phase 7 verification: known-similar documents must be near each other."""
    _, _, vectors, embedder, _ = indexed
    query = embedder.encode_one("support vector machines, kernels and the margin")
    hits = vectors.search_documents(query, limit=3)
    paths = [hit["rel_path"] for hit in hits]
    assert paths[0] == f"{ML}/Unit3_SVM_Notes.md", paths


@pytest.mark.slow
def test_near_duplicate_documents_are_nearest_neighbours(indexed):
    """The planted near-duplicate pair must be mutually closest.

    Phase 9 builds duplicate edges from exactly this similarity, so if it does
    not hold here, duplicate detection cannot work later.
    """
    _, store, vectors, _, _ = indexed
    ids, matrix = vectors.document_vectors()
    paths = store.path_by_file_id()
    index = {paths[file_id]: position for position, file_id in enumerate(ids)}

    original = index[f"{ML}/Unit4_Ensemble_Methods.pdf"]
    annotated = index[f"{ML}/Unit4_Ensemble_Methods_annotated.pdf"]

    similarities = matrix @ matrix[original]
    similarities[original] = -1.0
    assert int(np.argmax(similarities)) == annotated
    assert similarities[annotated] > 0.9


@pytest.mark.slow
def test_the_recipe_is_the_odd_one_out(indexed):
    """A topical sanity check with an obvious right answer."""
    _, store, vectors, _, _ = indexed
    ids, matrix = vectors.document_vectors()
    paths = store.path_by_file_id()
    index = {paths[file_id]: position for position, file_id in enumerate(ids)}

    recipe = matrix[index["Personal/Misc/recipe_biryani.txt"]]
    svm = matrix[index[f"{ML}/Unit3_SVM_Notes.md"]]
    ensembles = matrix[index[f"{ML}/Unit4_Ensemble_Methods.pdf"]]
    assert float(svm @ ensembles) > float(svm @ recipe)


@pytest.mark.slow
def test_reembedding_a_changed_file_replaces_its_vectors(indexed):
    cfg, store, vectors, embedder, _ = indexed
    row = store.get_file("Personal/Misc/movie_watchlist.txt")
    before = vectors.counts()["chunks"]

    store.mark_embedded(row["id"], "stale-hash", 0)
    assert [r["path"] for r in store.files_needing_embedding()] == [
        "Personal/Misc/movie_watchlist.txt"
    ]
    report = embed_documents(store, vectors, embedder, cfg)
    assert report.files == 1
    assert vectors.counts()["chunks"] == before, "re-embedding duplicated chunks"


@pytest.mark.slow
def test_tabular_chunks_are_marked_in_the_vector_store(indexed):
    _, _, vectors, embedder, _ = indexed
    hits = vectors.search_chunks(embedder.encode_one("exam timetable dates and halls"), limit=10)
    tabular = [hit for hit in hits if hit["is_tabular"]]
    assert tabular, "no tabular chunk surfaced for a timetable query"
