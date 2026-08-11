"""Layer 4 - chunking and embedding generation.

Chunking strategy (justified, per the phase requirement)
--------------------------------------------------------
Chunks are built **from extraction blocks, not from raw character offsets**.
Blocks already correspond to the document's own structure - a page, a
spreadsheet sheet, a slide, a Markdown section - so a block-aware chunker
produces semantically coherent units for free, where a fixed-width character
window would routinely split a timetable row in half or merge the end of one
slide with the start of the next.

The algorithm:

1. Walk blocks in document order, accumulating them into a chunk while the
   running token count stays under ``chunk_size_tokens``.
2. A block that would overflow the current chunk starts a new one.
3. A block that alone exceeds the limit is split internally on line boundaries,
   with ``chunk_overlap_tokens`` of trailing context carried into the next part.
4. A chunk inherits ``is_tabular`` from its constituent blocks, so structure
   survives into the vector store.

**Size = 256 tokens, overlap = 48 tokens (~19%).** Justification:

* ``all-MiniLM-L6-v2`` has a hard 256-token input limit. Anything longer is
  silently truncated by the model, so a larger chunk size would be a lie - the
  tail would be embedded as if it did not exist. This is the binding constraint,
  and it is why the number is 256 rather than a tuned value.
* Overlap exists so a fact spanning a chunk boundary is retrievable from either
  side. 48 tokens is roughly two sentences: enough to carry a deadline and its
  surrounding keywords together, which is exactly the span Phase 10 cares about.
* Personal documents in this corpus are small (median a few hundred tokens), so
  most files produce one to five chunks. The cost of overlap at this scale is
  negligible; on a larger corpus it would be the first parameter to revisit.

Document vectors are the **mean of a document's normalised chunk vectors**,
re-normalised. This is cheaper than embedding the full text a second time and,
unlike embedding truncated full text, it actually sees the whole document.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np

__all__ = [
    "Chunk",
    "chunk_blocks",
    "Embedder",
    "EmbeddingReport",
    "VectorStore",
    "load_embedding_model",
]


@dataclass
class Chunk:
    """One embeddable unit of a document."""

    file_id: int
    rel_path: str
    index: int
    text: str
    char_start: int
    char_end: int
    block_kinds: tuple[str, ...] = ()
    is_tabular: bool = False
    token_estimate: int = 0

    @property
    def chunk_id(self) -> str:
        """Stable identifier, unique within an index."""
        return f"{self.file_id}:{self.index}"


def _estimate_tokens(text: str) -> int:
    """Cheap token estimate used for chunk packing.

    Deliberately an estimate rather than a real tokenizer call: packing runs
    over every block of every document and calling the HuggingFace tokenizer
    per candidate would dominate chunking time on this CPU. The ratio 0.75
    words-per-token is the usual English approximation; the model truncates
    anything that slips over anyway, and overlap absorbs the error.
    """
    return max(1, int(len(text.split()) / 0.75))


def chunk_blocks(
    file_id: int,
    rel_path: str,
    blocks: Iterable[Any],
    *,
    chunk_size_tokens: int = 256,
    chunk_overlap_tokens: int = 48,
) -> list[Chunk]:
    """Pack extraction blocks into chunks, respecting document structure.

    Args:
        file_id: ``files.id`` these chunks belong to.
        rel_path: Corpus-relative path, carried for explanations.
        blocks: Objects with ``text``, ``kind``, ``is_tabular``, ``char_start``
            and ``char_end`` - either :class:`ExtractedBlock` or SQLite rows.
        chunk_size_tokens: Target maximum tokens per chunk.
        chunk_overlap_tokens: Tokens of trailing context repeated across a split.

    Returns:
        Chunks in document order.
    """
    chunks: list[Chunk] = []
    pending: list[tuple[str, str, bool, int, int]] = []
    pending_tokens = 0

    def flush() -> None:
        nonlocal pending, pending_tokens
        if not pending:
            return
        text = "\n\n".join(item[0] for item in pending)
        chunks.append(
            Chunk(
                file_id=file_id,
                rel_path=rel_path,
                index=len(chunks),
                text=text,
                char_start=pending[0][3],
                char_end=pending[-1][4],
                block_kinds=tuple(dict.fromkeys(item[1] for item in pending)),
                is_tabular=any(item[2] for item in pending),
                token_estimate=pending_tokens,
            )
        )
        pending = []
        pending_tokens = 0

    for block in blocks:
        text = _field(block, "text") or ""
        if not text.strip():
            continue
        kind = _field(block, "kind") or "section"
        tabular = bool(_field(block, "is_tabular"))
        start = int(_field(block, "char_start") or 0)
        end = int(_field(block, "char_end") or (start + len(text)))
        tokens = _estimate_tokens(text)

        if tokens > chunk_size_tokens:
            flush()
            for part_text, part_tokens in _split_long(
                text, chunk_size_tokens, chunk_overlap_tokens
            ):
                chunks.append(
                    Chunk(
                        file_id=file_id,
                        rel_path=rel_path,
                        index=len(chunks),
                        text=part_text,
                        char_start=start,
                        char_end=end,
                        block_kinds=(kind,),
                        is_tabular=tabular,
                        token_estimate=part_tokens,
                    )
                )
            continue

        if pending and pending_tokens + tokens > chunk_size_tokens:
            flush()
        pending.append((text, kind, tabular, start, end))
        pending_tokens += tokens

    flush()
    return chunks


def _field(obj: Any, name: str) -> Any:
    """Read a field from either a dataclass/object or a SQLite row."""
    if hasattr(obj, name):
        return getattr(obj, name)
    try:
        return obj[name]
    except (KeyError, IndexError, TypeError):
        return None


def _split_long(text: str, size: int, overlap: int) -> list[tuple[str, int]]:
    """Split an oversized block on line boundaries, carrying overlap forward.

    Line boundaries rather than sentence boundaries because the oversized blocks
    in practice are spreadsheet sheets and code files, where a line *is* the
    semantic unit and sentence splitting is meaningless.
    """
    lines = text.splitlines() or [text]
    parts: list[tuple[str, int]] = []
    current: list[str] = []
    current_tokens = 0

    for line in lines:
        tokens = _estimate_tokens(line)
        if current and current_tokens + tokens > size:
            parts.append(("\n".join(current), current_tokens))
            carry: list[str] = []
            carried = 0
            for previous in reversed(current):
                previous_tokens = _estimate_tokens(previous)
                if carried + previous_tokens > overlap:
                    break
                carry.insert(0, previous)
                carried += previous_tokens
            current = carry
            current_tokens = carried
        current.append(line)
        current_tokens += tokens

    if current:
        parts.append(("\n".join(current), current_tokens))
    return parts


def configure_torch_threads(threads: int = 0) -> int:
    """Set PyTorch's intra-op thread count and return the value used.

    PyTorch defaults to the number of *physical* cores (4 on the target CPU),
    leaving the other four logical cores idle. Measured on this machine,
    raising it to 8 improved embedding throughput from 19.5 to 34.5 chunks/s -
    a 77% gain for one line of configuration.

    Args:
        threads: Threads to use, or 0 to use every logical processor.

    Returns:
        The thread count actually applied.
    """
    import torch

    count = threads if threads > 0 else (os.cpu_count() or 1)
    torch.set_num_threads(count)
    return count


@lru_cache(maxsize=2)
def load_embedding_model(model_name: str, device: str = "cpu"):
    """Load and cache a sentence-transformers model (the reference backend).

    Cached because loading MiniLM costs ~5.5 s on this CPU and an index build
    would otherwise pay it repeatedly.
    """
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name, device=device)


class ModelNotCachedError(RuntimeError):
    """Raised when a model is absent locally and downloading was not permitted."""


@contextmanager
def _offline_hub():
    """Force HuggingFace libraries to use only locally cached files.

    Two reasons, in order of importance.

    **Local-first.** ContextFS promises that indexing your files makes no
    network calls. Left to their defaults, ``transformers`` and ``huggingface_hub``
    contact the Hub on every ``from_pretrained`` to check for a newer revision.
    That is a silent outbound request on every index build, which the project's
    stated principles forbid regardless of how harmless it is.

    **Speed.** Measured on this machine, those round-trips dominated model
    loading: 12.2 s with them, versus a fraction of that from cache.
    """
    keys = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY")
    previous = {key: os.environ.get(key) for key in keys}
    for key in keys:
        os.environ[key] = "1"
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@lru_cache(maxsize=2)
def load_transformers_model(model_name: str, device: str = "cpu", allow_download: bool = False):
    """Load a tokenizer and encoder via ``transformers`` directly.

    The cache is consulted offline first. Only if the model is genuinely absent,
    and only if ``allow_download`` is set, is a network fetch attempted - and
    that is a deliberate, announced, one-time setup step rather than something
    that happens quietly during indexing.

    Raises:
        ModelNotCachedError: If the model is not cached and downloading was
            not permitted, with the command needed to fetch it.
    """
    import torch

    def _load():
        from transformers import AutoModel, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModel.from_pretrained(model_name)
        model.eval()
        model.to(device)
        return tokenizer, model

    try:
        with _offline_hub():
            tokenizer, model = _load()
    except Exception as exc:  # noqa: BLE001 - HF raises several unrelated types
        if not allow_download:
            raise ModelNotCachedError(
                f"Embedding model {model_name!r} is not in the local cache, and "
                "ContextFS does not download during indexing (local-first).\n"
                f"Fetch it once with:  contextfs fetch-models"
            ) from exc
        tokenizer, model = _load()

    torch.set_grad_enabled(False)
    return tokenizer, model


def download_models(model_name: str, spacy_model: str | None = None) -> list[str]:
    """Fetch the models ContextFS needs into the local cache.

    The only function in ContextFS that is permitted to touch the network, and
    it exists solely so that indexing never has to.

    Returns:
        Human-readable lines describing what was fetched.
    """
    from transformers import AutoModel, AutoTokenizer

    messages = []
    AutoTokenizer.from_pretrained(model_name)
    AutoModel.from_pretrained(model_name)
    messages.append(f"embedding model cached: {model_name}")

    if spacy_model:
        try:
            from contextfs.entities import load_spacy

            load_spacy(spacy_model)
            messages.append(f"spaCy model present: {spacy_model}")
        except RuntimeError as exc:
            messages.append(str(exc))
    return messages


def mean_pool(last_hidden_state, attention_mask):
    """Attention-masked mean pooling.

    This is exactly the pooling ``sentence-transformers`` applies for
    ``all-MiniLM-L6-v2``: sum the token embeddings weighted by the attention
    mask, then divide by the number of real tokens. Padding must be excluded or
    short texts in a batch are dragged toward the padding embedding, which is a
    silent, batch-size-dependent corruption of the vectors.
    """
    import torch

    mask = attention_mask.unsqueeze(-1).expand(last_hidden_state.size()).float()
    summed = torch.sum(last_hidden_state * mask, dim=1)
    counts = torch.clamp(mask.sum(dim=1), min=1e-9)
    return summed / counts


@dataclass
class EmbeddingReport:
    """Aggregate outcome of an embedding pass."""

    files: int = 0
    chunks: int = 0
    tokens: int = 0
    duration_ms: float = 0.0
    errors: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """Flat printable summary."""
        return {
            "files": self.files,
            "chunks": self.chunks,
            "tokens_estimated": self.tokens,
            "duration_ms": round(self.duration_ms, 2),
            "chunks_per_second": (
                round(self.chunks / (self.duration_ms / 1000), 1) if self.duration_ms else 0.0
            ),
            "errors": len(self.errors),
        }


class Embedder:
    """Turns text into L2-normalised vectors using a local model.

    Two backends produce **identical** vectors (verified by
    ``tests/test_embed.py`` to within 1e-5 cosine):

    ``transformers`` (default)
        Loads the same weights through ``transformers.AutoModel`` and applies
        attention-masked mean pooling plus L2 normalisation - which is precisely
        what ``sentence-transformers`` does for ``all-MiniLM-L6-v2``.

    ``sentence-transformers`` (reference)
        The library named in the project's fixed stack. Kept as the correctness
        oracle and selectable at any time.

    **Why the default is not the reference.** Measured on the target machine:

        import torch                    4003 ms
        import transformers             4090 ms   (i.e. ~90 ms on top of torch)
        import sentence_transformers   15741 ms   (i.e. ~11.6 s of its own)

    ``sentence-transformers`` eagerly imports its full module zoo at package
    import time. On a Ryzen 7 3700U that is ~11.6 s of pure start-up cost added
    to every indexing run that has any embedding work to do - against roughly
    2-3 s of actual encoding for this corpus. The library is still a declared
    dependency and still the reference implementation; only the hot path avoids
    paying its import.
    """

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        *,
        device: str = "cpu",
        batch_size: int = 16,
        expected_dimension: int | None = None,
        backend: str = "transformers",
        num_threads: int = 0,
        allow_download: bool = False,
    ) -> None:
        """Configure the embedder. The model is loaded lazily on first use."""
        if backend not in {"transformers", "sentence-transformers"}:
            raise ValueError(
                f"unknown embedding backend {backend!r}; "
                "expected 'transformers' or 'sentence-transformers'"
            )
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        self.expected_dimension = expected_dimension
        self.backend = backend
        self.num_threads = num_threads
        self.allow_download = allow_download
        self._model = None
        self._tokenizer = None
        self._threads_applied = False

    def _ensure_loaded(self) -> None:
        if not self._threads_applied:
            configure_torch_threads(self.num_threads)
            self._threads_applied = True
        if self._model is not None:
            return
        if self.backend == "sentence-transformers":
            with _offline_hub():
                self._model = load_embedding_model(self.model_name, self.device)
        else:
            self._tokenizer, self._model = load_transformers_model(
                self.model_name, self.device, self.allow_download
            )

    @property
    def model(self):
        """The loaded encoder."""
        self._ensure_loaded()
        return self._model

    @property
    def dimension(self) -> int:
        """Output dimensionality of the loaded model."""
        self._ensure_loaded()
        if self.backend == "sentence-transformers":
            return int(self._model.get_sentence_embedding_dimension())
        return int(self._model.config.hidden_size)

    @property
    def max_input_tokens(self) -> int:
        """The model's hard input limit, beyond which text is truncated."""
        self._ensure_loaded()
        if self.backend == "sentence-transformers":
            return int(self._model.get_max_seq_length() or 256)
        return int(min(self._tokenizer.model_max_length, 512))

    def verify_dimension(self) -> None:
        """Fail loudly if the model's dimension disagrees with the config.

        A silent mismatch would produce a vector store that cannot be queried,
        or worse, one that can be queried with meaningless results.
        """
        if self.expected_dimension and self.dimension != self.expected_dimension:
            raise ValueError(
                f"Model {self.model_name!r} produces {self.dimension}-dimensional vectors "
                f"but the configuration declares {self.expected_dimension}. "
                "Update [embeddings].dimension, or reset the index before changing models."
            )

    def encode(self, texts: list[str], *, show_progress: bool = False) -> np.ndarray:
        """Embed a list of texts into L2-normalised row vectors.

        Normalisation is done at encode time so that cosine similarity is a
        plain dot product everywhere downstream - in LanceDB search, in graph
        edge weighting, and in the retrieval scorer. Doing it once here removes
        an entire class of "did we normalise this one?" bugs.
        """
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        self._ensure_loaded()

        if self.backend == "sentence-transformers":
            vectors = self._model.encode(
                texts,
                batch_size=self.batch_size,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=show_progress,
            )
            return np.asarray(vectors, dtype=np.float32)

        import torch

        outputs: list[np.ndarray] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            encoded = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self.max_input_tokens,
                return_tensors="pt",
            ).to(self.device)
            with torch.no_grad():
                hidden = self._model(**encoded).last_hidden_state
            pooled = mean_pool(hidden, encoded["attention_mask"])
            pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
            outputs.append(pooled.cpu().numpy())
        return np.vstack(outputs).astype(np.float32)

    def encode_one(self, text: str) -> np.ndarray:
        """Embed a single string (used for queries)."""
        return self.encode([text])[0]

    @staticmethod
    def pool(vectors: np.ndarray) -> np.ndarray:
        """Mean-pool chunk vectors into a document vector, re-normalised.

        Cheaper than embedding the full document text again, and strictly more
        faithful: embedding full text would silently truncate anything past the
        model's 256-token limit, whereas pooling has seen every chunk.
        """
        if len(vectors) == 0:
            return np.zeros(0, dtype=np.float32)
        pooled = vectors.mean(axis=0)
        norm = np.linalg.norm(pooled)
        return (pooled / norm if norm else pooled).astype(np.float32)


class VectorStore:
    """LanceDB-backed storage for chunk and document vectors.

    Two tables are kept:

    * ``chunks``    - one row per chunk, used for fine-grained retrieval and
      for locating *where* in a document a match occurred.
    * ``documents`` - one pooled vector per file, used by the pure-semantic
      baseline and as the seed-selection surface for hybrid retrieval.

    Both are needed. Chunk-level search alone conflates "this document is
    relevant" with "this paragraph is relevant", which distorts Precision@K when
    one long document contributes several near-duplicate hits.
    """

    CHUNKS = "chunks"
    DOCUMENTS = "documents"

    def __init__(self, path, dimension: int) -> None:
        """Open (creating if absent) a LanceDB database at ``path``."""
        import lancedb

        self.path = path
        self.dimension = dimension
        self.db = lancedb.connect(str(path))

    # -- schema ------------------------------------------------------------

    def _chunk_schema(self):
        import pyarrow as pa

        return pa.schema(
            [
                pa.field("chunk_id", pa.string()),
                pa.field("file_id", pa.int64()),
                pa.field("rel_path", pa.string()),
                pa.field("chunk_index", pa.int64()),
                pa.field("text", pa.string()),
                pa.field("char_start", pa.int64()),
                pa.field("char_end", pa.int64()),
                pa.field("is_tabular", pa.bool_()),
                pa.field("block_kinds", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), self.dimension)),
            ]
        )

    def _document_schema(self):
        import pyarrow as pa

        return pa.schema(
            [
                pa.field("file_id", pa.int64()),
                pa.field("rel_path", pa.string()),
                pa.field("chunk_count", pa.int64()),
                pa.field("char_count", pa.int64()),
                pa.field("vector", pa.list_(pa.float32(), self.dimension)),
            ]
        )

    def _table_names(self) -> list[str]:
        """List existing table names.

        ``table_names()`` is deprecated in favour of ``list_tables()``, but the
        replacement returns a different shape in the pinned LanceDB version, so
        the deprecated call is used deliberately and the warning is filtered in
        ``pyproject.toml`` rather than silently tolerated.
        """
        return list(self.db.table_names())

    def _table(self, name: str):
        """Open a table, creating it empty if it does not yet exist."""
        if name in self._table_names():
            return self.db.open_table(name)
        schema = self._chunk_schema() if name == self.CHUNKS else self._document_schema()
        return self.db.create_table(name, schema=schema)

    # -- writing -----------------------------------------------------------

    def delete_files(self, file_ids: Iterable[int]) -> None:
        """Remove all vectors belonging to the given files.

        Called before re-embedding a changed file, and when a file is deleted.
        Without it, a shrinking document would leave orphaned chunk vectors that
        remain searchable - returning text that no longer exists on disk.
        """
        ids = list(file_ids)
        if not ids:
            return
        predicate = f"file_id IN ({','.join(str(int(i)) for i in ids)})"
        for name in (self.CHUNKS, self.DOCUMENTS):
            if name in self._table_names():
                self._table(name).delete(predicate)

    def add_chunks(self, chunks: list[Chunk], vectors: np.ndarray) -> None:
        """Store chunk vectors."""
        if not chunks:
            return
        rows = [
            {
                "chunk_id": chunk.chunk_id,
                "file_id": chunk.file_id,
                "rel_path": chunk.rel_path,
                "chunk_index": chunk.index,
                "text": chunk.text,
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
                "is_tabular": chunk.is_tabular,
                "block_kinds": ",".join(chunk.block_kinds),
                "vector": vectors[i].tolist(),
            }
            for i, chunk in enumerate(chunks)
        ]
        self._table(self.CHUNKS).add(rows)

    def add_document(
        self, file_id: int, rel_path: str, vector: np.ndarray, chunk_count: int, char_count: int
    ) -> None:
        """Store one pooled document vector."""
        self._table(self.DOCUMENTS).add(
            [
                {
                    "file_id": file_id,
                    "rel_path": rel_path,
                    "chunk_count": chunk_count,
                    "char_count": char_count,
                    "vector": vector.tolist(),
                }
            ]
        )

    # -- reading -----------------------------------------------------------

    def search_documents(self, vector: np.ndarray, limit: int = 10) -> list[dict[str, Any]]:
        """Nearest documents to a query vector, by cosine distance."""
        return self._search(self.DOCUMENTS, vector, limit)

    def search_chunks(self, vector: np.ndarray, limit: int = 20) -> list[dict[str, Any]]:
        """Nearest chunks to a query vector, by cosine distance."""
        return self._search(self.CHUNKS, vector, limit)

    def _search(self, name: str, vector: np.ndarray, limit: int) -> list[dict[str, Any]]:
        if name not in self._table_names():
            return []
        table = self._table(name)
        if table.count_rows() == 0:
            return []
        results = table.search(vector.tolist()).metric("cosine").limit(limit).to_list()
        for row in results:
            # LanceDB returns cosine *distance*; convert to similarity so every
            # score in the system points the same way (higher is better).
            row["score"] = 1.0 - float(row.get("_distance", 1.0))
            row.pop("vector", None)
        return results

    def document_vectors(self) -> tuple[list[int], np.ndarray]:
        """Return ``(file_ids, matrix)`` of every stored document vector.

        Used by Phase 9 to build semantic edges: an all-pairs similarity over a
        small matrix is far cheaper than one nearest-neighbour query per file.
        """
        if self.DOCUMENTS not in self._table_names():
            return [], np.zeros((0, self.dimension), dtype=np.float32)
        rows = self._table(self.DOCUMENTS).to_arrow().to_pylist()
        if not rows:
            return [], np.zeros((0, self.dimension), dtype=np.float32)
        rows.sort(key=lambda r: r["file_id"])
        ids = [int(r["file_id"]) for r in rows]
        matrix = np.asarray([r["vector"] for r in rows], dtype=np.float32)
        return ids, matrix

    def counts(self) -> dict[str, int]:
        """Row counts for both tables."""
        out = {}
        for name in (self.CHUNKS, self.DOCUMENTS):
            out[name] = self._table(name).count_rows() if name in self._table_names() else 0
        return out

    def indexed_file_ids(self) -> set[int]:
        """File ids that currently have a document vector."""
        ids, _ = self.document_vectors()
        return set(ids)


def embed_documents(
    store,
    vectors: VectorStore,
    embedder: Embedder,
    config,
    *,
    file_ids: Iterable[int] | None = None,
) -> EmbeddingReport:
    """Chunk and embed documents whose vectors are missing or stale.

    Args:
        store: The SQLite metadata store.
        vectors: The LanceDB vector store.
        embedder: Configured embedder.
        config: Resolved ContextFS configuration.
        file_ids: Restrict to these files; default is everything that needs it.

    Returns:
        An :class:`EmbeddingReport`.
    """
    started = time.perf_counter()
    report = EmbeddingReport()

    pending = (
        [store.get_file_by_id(i) for i in file_ids]
        if file_ids is not None
        else store.files_needing_embedding()
    )
    pending = [row for row in pending if row is not None]
    if not pending:
        report.duration_ms = (time.perf_counter() - started) * 1000
        return report

    embedder.verify_dimension()
    vectors.delete_files([row["id"] for row in pending])

    for row in pending:
        document = store.get_document(row["id"])
        if document is None or not document["ok"]:
            continue
        blocks = store.get_blocks(row["id"])
        chunks = chunk_blocks(
            row["id"],
            row["path"],
            blocks,
            chunk_size_tokens=config.embeddings.chunk_size_tokens,
            chunk_overlap_tokens=config.embeddings.chunk_overlap_tokens,
        )
        if not chunks:
            continue
        try:
            matrix = embedder.encode([chunk.text for chunk in chunks])
        except Exception as exc:  # noqa: BLE001 - one bad document must not stop the build
            report.errors.append((row["path"], f"{type(exc).__name__}: {exc}"))
            continue

        vectors.add_chunks(chunks, matrix)
        vectors.add_document(
            row["id"],
            row["path"],
            Embedder.pool(matrix),
            chunk_count=len(chunks),
            char_count=document["char_count"],
        )
        store.mark_embedded(row["id"], row["content_hash"], len(chunks))

        report.files += 1
        report.chunks += len(chunks)
        report.tokens += sum(chunk.token_estimate for chunk in chunks)

    report.duration_ms = (time.perf_counter() - started) * 1000
    return report
