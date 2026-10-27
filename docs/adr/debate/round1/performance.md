# Round 1 — Performance Engineer

## 1. Position summary

- **Light startup path.** The window and filename/time search need only SQLite; the ONNX query encoder loads in the background. The UI process never imports torch, spaCy, NetworkX or LanceDB.
- **Enumeration doubles as change detection.** `os.scandir` (measured: 1.5k–16.6k files/s cold, 54k–236k warm). Files are hashed only while being read for extraction, and placeholders are never opened.
- **No O(n²) work, no per-query table loads.** Sessions use sparse segmented average linkage with dirty-segment updates. Edges come from ANN kNN and are stored in SQLite. Queries touch only top-k rows.
- **CPU first.** The GPU is used only for bulk embedding. Queries are always encoded on CPU.
- **Progressive first index.** Name/path/time search works in about 1 min. Content is indexed newest-first, taking about 1.5–2 h on CPU for 500k files.

## 2. Proposals

### 2.1 Startup

**Decision.** There are three startup milestones. Times below are for a warm OS file cache; bracketed times are cold, just after a reboot.

| Milestone | Loads | Target |
|---|---|---|
| Window shown | Python, PySide6, SQLite (read-only) | ≤2.0 s [≤4 s] |
| Lexical-ready | SQLite FTS5 (names, paths, text) | ≤2.5 s |
| Semantic-ready | onnxruntime + model; vector index opened as an mmap view | ≤4 s [≤8 s] |

Measured here, importing PySide6 takes 2.18 s. log.md records torch at 3.3–4 s, sentence-transformers at 11.6 s and LanceDB at 2.6 s. torch therefore leaves the runtime; the onnxruntime CPU wheel is 14.9 MB ([PyPI](https://pypi.org/project/onnxruntime/#files)). A query typed before semantic-ready waits up to 3 s. After that it gets labelled lexical results, which upgrade in place.

**Search never waits for the launch update.** It reads the last committed snapshot: SQLite WAL plus a versioned vector-index file. The indexer commits every 500 files or 5 s behind a progress bar.

**Alternatives:** a splash screen until everything loads (15–20 s); lazy-importing torch (≥4 s, 2+ GB bundle). **Trade-off:** every model needs an ONNX export and a parity test. **Would change my mind:** a frozen PySide6 build taking ≥4 s to show the window on a warm cache. **Measurement:** 10 warm and 10 post-reboot runs per milestone, on the dev laptop and an 8 GB VM.

### 2.2 Whole-drive scan

**Enumeration measured today.** The probe walked each tree iteratively with `os.scandir` and called `DirEntry.stat(follow_symlinks=False)` on every file. It hashed nothing.

| Tree | Files | Cold | Warm |
|---|---|---|---|
| `C:\Users\Alfred` (NVMe) | 63,362 | 3.8 s (16.6k/s) | 1.2 s (54k/s) |
| `D:\` (USB HDD) | 188,417 | **127.9 s (1.5k/s)** | 0.8 s (236k/s) |

On Windows, `DirEntry.stat()` needs no extra syscall except for symlinks ([PEP 471](https://peps.python.org/pep-0471/)). `FIND_FIRST_EX_LARGE_FETCH` mainly helps high-latency media ([Old New Thing](https://devblogs.microsoft.com/oldnewthing/?p=2843)). Reading the MFT, as Everything does (about 1 min per 1M files), needs admin rights or a service ([voidtools](https://www.voidtools.com/faq/)). Microsoft says change-journal operations need admin ([MS](https://learn.microsoft.com/en-us/windows/win32/FileIO/change-journal-operations)). A third party describes an unprivileged read-only mode that may not return file names ([Meziantou](https://www.meziantou.net/reading-usn-journal-with-dotnet.htm)). **Decision:** use scandir enumeration. USN reading is an optional later accelerator that falls back to a scan.

**Change detection.**
Compare `(volume serial, path, size, mtime_ns, attributes)` from the directory entry, without reading the file. The content hash is computed during the extraction read and used only to detect duplicates and moves. Files with `RECALL_ON_DATA_ACCESS` (0x400000) or `RECALL_ON_OPEN` (0x40000) set, whose reads fetch content "from a remote store" ([MS](https://learn.microsoft.com/en-us/windows/win32/fileio/file-attribute-constants)), are indexed metadata-only and never opened.

**First full index: 500k files, 8 cores, no GPU (estimates for Phase 7 to replace).**

Assumptions: 125k text docs, at most 64 chunks per file, about 1M chunks in total. MiniLM ran at 34.5–54 chunks/s with torch on the older 3700U (log.md). On the Ryzen 7 250 that suggests about 150/s with fp32 torch and 300–450/s with ONNX int8; quantized ONNX or OpenVINO is SBERT's fastest CPU option ([SBERT](https://sbert.net/docs/sentence_transformer/usage/efficiency.html)). spaCy processes about 10k words/s per process ([spaCy](https://spacy.io/usage/facts-figures)).

| Stage | CPU wall time | User sees |
|---|---|---|
| T0 enumerate + metadata rows | 0.5 min NVMe / 6 min USB HDD | name/path/time search over everything |
| T1 extraction + FTS5 (125k docs) | 30–60 min, probably limited by pypdf | BM25 hits, newest first |
| T2 NER (NER-only pipe, ≤2k words per doc, 3 workers) | 25–45 min, overlapped with T1 | entity matches |
| T3 embedding of 1M chunks, ONNX int8 | 40–60 min (2 h with torch fp32; about 5 min on GPU) | semantic matches |
| T4 sessions + kNN edges per finished segment | minutes | session and graph explanations |

**About 1.5–2 h on CPU, pipelined**, in reverse-mtime order, so the last year is usable in about 15 min. Workers run at BELOW_NORMAL priority and use `cores − 2` while the user is active. On HDDs, files are read in directory order because each open costs a seek. **Would change my mind:** unprivileged USN reads that return names. These would cut a 10⁶-file HDD relaunch to seconds.

### 2.3 Memory budgets (500k files / 125k docs / 1M chunks)

| Component | 16 GB | 8 GB |
|---|---|---|
| UI + Python + Qt | 250 MB | 250 MB |
| Query encoder (ONNX, UI process) | 150 MB | 150 MB |
| Vector index (usearch mmap view; i8 chunks, f16 docs) | ≤600 MB working set | ≤300 MB |
| SQLite `cache_size` + `mmap_size` | 128 + 256 MB | 32 + 128 MB |
| Graph (SQLite edges, fan-out ≤400) | ≤30 MB | ≤30 MB |
| Indexer embedder (batch 32, length-bucketed) | 300 MB | 250 MB |
| NER workers (~350 MB each; count set by free RAM) | 4 workers | 1–2 workers |
| Session builder (bounded by segment size) | ≤200 MB | ≤150 MB |
| **Steady state while searching** | **≤1.2 GB** | **≤0.8 GB** |
| **Peak during first index** | **≤3.5 GB** | **≤1.8 GB** |

The timeline tree and the entity index move into indexed SQLite tables. Python objects held per file do not fit these budgets.

### 2.4 Global stages at scale

**Sessions: sparse segmented average linkage.** G = `session_gap_hours`, and t = each file's reconciled time.

1. Sort by t. **Cut exactly** at every gap > G: the gate forbids a session from crossing one. Segments above 20k files are force-cut at their largest gap.
2. **Generate candidates per file, K ≈ 24:**
   - the 8 nearest files in time order;
   - the ANN top-8 within |Δt| ≤ G;
   - up to 4 files in the same folder within G.

   Affinity, including entity Jaccard, is computed only on candidate pairs. Edges are stored as int32/int32/float32 arrays at 12 B each.
3. **Cluster inside each segment.** Run average linkage over the candidate graph using a lazy max-heap over pair sums, merging the smaller cluster's neighbour list into the larger. Every candidate pair satisfies |Δt| ≤ G, so **any linked pair already passes the gate**. Cost is O(E log E) per segment, and memory is bounded by segment size × K.
4. **Incremental:** re-cluster only dirty segments (those holding a changed file's old or new t), usually just the newest. Session IDs stay stable through max-Jaccard matching.
5. **Reconcile:** re-merge clusters near forced cuts as super-nodes. Do a full rebuild at idle after >10% churn or a model change.
6. **Burst guard:** ≥200 files from one folder within 2 s (an unzip or install) become one bulk event.

**Validation:** non-candidate pairs count as 0, which makes merging conservative. On corpora of up to 5k files, sparse must reach pairwise F1 ≥ 0.95 against exact v1. The log-log time slope over 10⁴–10⁶ files must be ≤ 1.2.

**Semantic edges:** each doc keeps its ANN top-8 above 0.55. An incremental update costs Δ queries, plus a reverse repair whenever a new score beats a neighbour's 8th edge.

**Duplicates:** ANN pairs with cosine ≥ 0.9, confirmed by shingle Jaccard.

**Entity and structural edges are not stored.** Popular entities and big folders produce O(m²) pairs. They are derived at query time from indexed postings, with a document-frequency cap.

**Graph:** a SQLite `edges(src, dst, type, w)` table indexed on src. NetworkX is used only for export.

### 2.5 Vector index

**Decision:** [usearch](https://github.com/unum-cloud/usearch) HNSW (M = 16) behind an engine interface:
- chunk vectors in i8, document vectors in f16, both opened as a memory-mapped `view`;
- the top 200 re-scored exactly from memory-mapped f16 vectors;
- exact numpy brute force at 50k vectors or fewer, and in evaluation mode.

Each chunk costs about 512 B (384 + 2M·4), or roughly 0.5 GB per million.

**Alternatives:**
- LanceDB IVF_PQ or IVF_HNSW_SQ is disk-based and low-RAM ([LanceDB](https://docs.lancedb.com/indexing/vector-index)), but adds 148 MB and a 2.6 s import.
- faiss-cpu ships Windows avx2 wheels ([PyPI](https://pypi.org/project/faiss-cpu/)) but is heavier.
- hnswlib has no memory-mapped view.

**Would change my mind:** LanceDB meeting the §2.6 targets with appends, since it also brings FTS.
**Measurement:** recall@100 against exact search, p95 latency and RSS at 10⁵ and 10⁶ chunks.

### 2.6 Per-query cost

Replace v1's whole-table loads on every query (`document_vectors()`, `path_by_file_id()`, `session_membership()`, `entity_index()`) with:
- ANN top-200 plus FTS5 top-200;
- SQL `IN (candidates)` lookups;
- SQL 2-hop expansion capped at 400 nodes.

**Targets at 10⁶ files on CPU:**
- p50 ≤ 150 ms and p95 ≤ 400 ms;
- p95 ≤ 900 ms with a cross-encoder reranking the top 50;
- at most 1.5× growth from 10⁴ to 10⁶ files.

### 2.7 GPU vs CPU

- **When the GPU is used:** only for embedding queues of 2,000 chunks or more (first index, model change). CUDA init measured 6.7 s on this machine (log.md).
- **Batch sizes:** GPU fp16 batch 256 under a 6 GB VRAM cap, halved on OOM. CPU batch 32, bucketed by length.
- **Queries:** always encoded on CPU.
- **Precision:** vectors carry an `embedder_fingerprint`. int8 or fp16 must match fp32 to within mean cosine ≥ 0.99 and an nDCG@10 drop of ≤ 1 point, otherwise ONNX fp32 is used.
- **GPU provider:** CUDA EP, or DirectML (in sustained engineering, superseded by WinML, [ORT](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)). Packaging decides which.

### 2.8 Incremental-update targets (NVMe, warm cache)

| n | Δ = 0 (enumeration only) | Δ = 10 docs, end-to-end | Δ = 1,000 docs |
|---|---|---|---|
| 10⁴ | ≤0.5 s | ≤5 s | ≤4 min |
| 10⁵ | ≤2 s (cold ≤8 s) | ≤6 s | ≤4 min |
| 10⁶ | ≤20 s (cold ≤75 s) | ≤10 s + enumeration | ≤5 min |

Except for enumeration, every stage costs O(Δ·K) plus one dirty segment.

### 2.9 Phase 7 must report

1. Startup milestones, warm and cold. 2. Query p50/p95/p99 per stage at n = 10³–10⁶ (synthetic vectors allowed at 10⁶). 3. Steady and peak RSS per process (psutil). 4. Throughput: enumeration (NVMe/HDD, cold/warm), extraction docs/s by type, NER words/s, chunks/s per backend × batch size, and first-index wall time. 5. Incremental time for Δ ∈ {0, 1, 10, 100, 1000} × n. 6. Sessions: exact-vs-sparse F1 and time/memory slopes. 7. ANN recall@k. 8. On-disk size per component. 9. GPU/CPU crossover, including initialisation. 10. Longest UI event-loop stall during indexing (≤100 ms).

## 3. Positions on other questions

- **UI:** keep PySide6 if a frozen build shows its window in ≤2 s.
- **Processes:** UI and query engine in one process. The indexer is a child process that runs only while the app is open, which sidesteps the GIL, isolates crashes and lets cancel kill it. A `ReadDirectoryChangesW` watcher while the app is open is fine.
- **Packaging:** the CPU build has no torch; GPU support is an add-on.
- **Safety:** lock file; vector-index files are versioned and swapped atomically.

## 4. Risks in this proposal

- Throughput figures are extrapolated and may be off by 2–3×.
- Sparse sessions may split clusters that v1 merges; the F1 gate catches this.
- usearch's mmap view on Windows needs a spike.
- If int8 fails the parity gate, CPU embedding time doubles.
- A cold HDD relaunch at 10⁵+ files still spends minutes enumerating.
- pypdf may dominate the first index.
- The 64-chunk cap truncates long PDFs. Truncation must be recorded and shown in explanations.

## 5. Sources

- https://peps.python.org/pep-0471/
- https://devblogs.microsoft.com/oldnewthing/?p=2843
- https://www.voidtools.com/faq/
- https://learn.microsoft.com/en-us/windows/win32/FileIO/change-journal-operations
- https://www.meziantou.net/reading-usn-journal-with-dotnet.htm
- https://learn.microsoft.com/en-us/windows/win32/fileio/file-attribute-constants
- https://pypi.org/project/onnxruntime/#files
- https://sbert.net/docs/sentence_transformer/usage/efficiency.html
- https://spacy.io/usage/facts-figures
- https://github.com/unum-cloud/usearch
- https://docs.lancedb.com/indexing/vector-index
- https://pypi.org/project/faiss-cpu/
- https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html
- Local: read-only scandir probe and import timings run 2026-10-27; log.md Decisions 41/62 and the CUDA-init measurement.
