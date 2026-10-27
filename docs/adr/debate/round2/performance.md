# Round 2 · Performance Engineer (revised)

**[M]** = measured on the dev machine on 2026-10-27. **[E]** = an estimate that Phase 7 must replace.

**What changed:**
- **Conceded:** usearch, stored semantic edges, sparse linkage, the watcher, the 20k block cap and the throughput figures derived from MiniLM.
- **Held:** a light startup path, scandir-based change detection, reconciled time for sessions, no per-query table loads, and query encoding on CPU only.

## 1. Revised proposal

### 1.1 New measurement: exact vector search [M]

Setup: numpy/OpenBLAS, 384-d vectors, one query, top-200 via `argpartition`.

| Vectors | RAM | p50 | p95 |
|---|---|---|---|
| 10⁵ f32 | 146 MiB | **7.4 ms** | 9.1 ms |
| 10⁵ f32, 20 seeds batched | — | 21.3 ms | 22.2 ms |
| 10⁶ f32 | 1,465 MiB | **84 ms** | 89 ms |
| 10⁶ f16, converted to f32 in blocks | 732 MiB | 868 ms | 875 ms |

f16 is about 10× slower (numpy has no f16 BLAS), so **vectors stay f32**. Round 1 enumeration figures stand.

### 1.2 Vectors (MVP)

**Exact f32 brute force over chunk vectors**, stored in append-only memory-mapped `.npy` segments.
- SQLite owns validity: a row → `(chunk_id, embed_fp, valid)` map.
- Writers append and fsync the segment first, then commit to SQLite.
- A reader maps only the row count recorded in its snapshot.
- Deletes are tombstones; `reconcile()` compacts.

There is no LanceDB and no ANN, so the app, CLI and evaluation return identical results at every scale. The ceiling is about 3×10⁵ resident vectors (≈440 MB) on an 8 GB machine and 10⁶ on 16 GB. Above that, ANN is LATER.

### 1.3 Graph (MVP)

Only duplicate edges are stored.
- **Semantic neighbours** of about 20 seeds: one batched exact pass, 21 ms at 10⁵ [M].
- **Entity neighbours:** an inverted index with a document-frequency cap.
- **Folder neighbours:** a path-prefix index.

### 1.4 Sessions (MVP)

**Time base.** Sessions sort on `t_session = file_time.t_modified_ns`, the reconciler's estimate of the last real edit. They never sort on raw mtime.

The reconciler marks a file `time_unknown` when birth ≈ mtime ≈ a transfer cohort and no embedded time exists. Those files are excluded. `created > modified` alone is *not* an exclusion reason, because M1 shows that mtime survives exactly those copies.

**Blocks.** A block is a maximal run of files whose consecutive gaps are ≤ G. The idle-gap gate makes blocks independent, so clustering per block is exact.
- **Cap:** blocks larger than **B_max = 2,000** are cut at their largest gaps, with ties broken by `file_id`. The cut is deterministic.
- **Within a block:** v1's exact semantics (average linkage, gate, weights, threshold), vectorised:
  - a k×k affinity-sum matrix whose rows are added on merge;
  - a k×k min-gap matrix whose rows take an elementwise min on merge. This is exact, because the idle gap is a min over pairs;
  - a cached best-partner per row;
  - entity Jaccard computed as a sparse incidence product.
- **Cost [E]:** about 0.1–0.5 s and 48 MB per 2,000-file block. A full rebuild at 500k files takes about 2–4 min.

**When one file's reconciled time changes from t_old to t_new:**
1. Dirty range = the stored block containing t_old ∪ every block whose span ± G contains t_new.
2. Re-sweep that range in time order, using an index on `t_session`. Removing t_old can split a block; adding t_new can bridge two blocks.
3. Memoise each block by `hash(sorted (file_id, t_session, feature_fp))`. Re-cluster only the blocks whose hash changed.
4. Carry session IDs over by max-Jaccard matching.

A folder-level re-reconcile just produces more dirty blocks. A property test asserts that incremental == clean.

**Zones.** G = 240 h, so a zone error of up to 14 h matters only for gaps within ±14 h of G. Unknown zones use the current zone at the interval midpoint. IST and DST fixtures cover this.

### 1.5 Corrected throughput [E]

granite-97m is ModernBERT: 12 layers, hidden 384, GeGLU with intermediate 1536, and local attention over 128 tokens ([config](https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2/raw/main/config.json)). That is about **2.5–3× MiniLM-L6 per token**; the 180k vocabulary is a lookup and adds no compute. My Round 1 MiniLM figure was itself unmeasured.

The revised granite int8 estimate is 25k–45k tokens/s on 8 cores, or 100–180 chunks/s at 256 tokens. On 4 cores, halve it.

| Corpus | Chunks | 8-core embedding | End to end | 4-core |
|---|---|---|---|---|
| Benchmark persona, 10⁴ text docs | ~8×10⁴ | 8–14 min | 20–40 min | ×2 |
| Dev profile, 10⁵ files | ~2×10⁵ | 20–35 min | ~1 h | ×2 |
| Whole drive, 5×10⁵ files | ~10⁶ | **1.6–2.8 h** | **2.5–4 h** | **5–8 h** |

Name, path and time search is ready in ≤1 min (NVMe) or ≤6 min (USB HDD). BM25 results arrive first, then dense results, newest first.

Below 40 chunks/s on 4 cores (M-E1), IR's fallback applies: a potion first pass, then granite back-fill.

### 1.6 Startup [E]

| Milestone | Target |
|---|---|
| Window shown | ≤2 s warm, ≤4 s cold (PySide6 import alone: 2.18 s [M]) |
| Lexical search ready | ≤2.5 s |
| Dense search ready | ≤5 s warm |

The UI process never imports torch, spaCy, LanceDB, NetworkX or scipy.

### 1.7 Memory at 10⁵ files / 2×10⁵ chunks [E]

| Component | 8 GB | 16 GB |
|---|---|---|
| UI + Qt + Python | 250 MB | 250 MB |
| Query encoder (ORT CPU, granite int8) | 250 MB | 250 MB |
| Chunk vectors, resident | 300 MB | 300 MB |
| SQLite cache + mmap | 160 MB | 384 MB |
| Indexer (encoder, NER, extraction workers) | 1 NER + 1 extraction worker | 2 + 2 |
| **Steady, searching** | **≤0.95 GB** | **≤1.2 GB** |
| **Peak, indexing** | **≤2.0 GB** | **≤2.8 GB** |


### 1.8 Per-query cost and incremental targets [E]

**Query pipeline:**
- BM25 top-200 ∪ exact dense top-200 forms P0: uncapped, ≤ 400 candidates;
- SQL `IN (…)` lookups;
- one batched neighbour pass;
- a short read transaction per query.

**Query latency at 10⁵ files:** p50 ≤ 100 ms, p95 ≤ 250 ms, before any reranker.

**Incremental updates (NVMe, warm):**

| n | Δ = 0 | Δ = 10 docs | Δ = 1,000 docs |
|---|---|---|---|
| 10⁴ | ≤ 0.5 s | ≤ 8 s | ≤ 12 min |
| 10⁵ | ≤ 2 s (≤ 8 s cold) | ≤ 10 s | ≤ 12 min |

Apart from enumeration, every stage costs O(Δ) plus the dirty blocks.

## 2. Responses to attacks on me

- **#3 Time zones.** Accepted: naive-local time + `tz_known` + `precision_s`. Effect on sessions: §1.4.
- **#5 File opens.** Accepted.
  - Open with `CreateFileW` and `FILE_SHARE_READ|WRITE|DELETE`.
  - Read the file into a buffer (≤ 100 MB cap), then **close the handle before parsing**; xxh3 runs over the same buffer.
  - Take size and mtime from the handle. Re-stat after extraction and requeue on any change.
  - Test against a live Word save.
- **#6.** Extracted files are re-stat'ed through their handle. Stale metadata-only entries are fixed by Refresh, and coverage is reported (M-C1).
- **#10 Parity.**
  - (a) Dissolved: exact search everywhere.
  - (b) Withdrawn: evaluation uses the *shipped* int8 file. The int8-vs-fp32 gap is a reported model property, not a tolerance.
  - (c) One pinned wheel.
  - (d) The MVP index is built on the CPU EP. A DirectML index is never the evaluated one.
  - (f) The parity test compares `signals_used`.
- **#11 Process / GPU crash.** The UI process runs only the CPU EP, which cannot TDR. DirectML (LATER) runs only in the indexer child. If the child dies mid-DML, it sets a sticky `gpu_disabled` flag.
- **#13 WAL and disk.**
  - `wal_checkpoint(PASSIVE)` after each commit; `TRUNCATE` at idle and on exit.
  - If the WAL exceeds 256 MB, force a checkpoint.
  - Free space must be ≥ max(2 GB, 3× the batch estimate) before each batch.
  - `SQLITE_FULL` pauses indexing and shows a banner.
  - `VACUUM INTO` runs only with ≥ 2.2× the DB size free.
- **#12, #14, #15, #17.** Conceded or resolved (§1.5, §4, §5).

## 3. Answers to the skeptic's questions

1. **Reconciler owner and ground truth.** IR owns the algorithm and the architect owns the plumbing. My constraint: cost O(evidence) per file plus O(folder) per dirty folder. Ground truth the generator did not produce:
   - M1;
   - a transfer table recorded by hand *before* coding (Explorer, robocopy, 7-Zip, `git clone`, OneDrive, exFAT, downloads);
   - the old-laptop copy.
2. **What evaluation runs.** The shipped build's ORT wheel, `model_quint8_avx2.onnx`, the CPU EP with fixed threads, exact search, feedback off, `now` injected.
3. **Topology.** UI process (Qt + in-process `Engine` for queries, CPU EP) plus one indexer child (`--engine`, Job Object, sole writer). The child runs 1–2 recycled extraction workers with per-file timeouts.
4. **Vector store.** Exact f32 memmap: 7.4 ms at 10⁵ and 84 ms at 10⁶ [M].
5. **P0 and baselines.** P0 = BM25 top-200 ∪ dense top-200, uncapped. Unpooled baselines run exactly over the full corpus (7 ms).
6. IR's definition applies. Exact re-scoring of ≤400 candidates costs under 5 ms [E].
7. Timestamps are stored as intervals, and sessions use the midpoint.
8. **File opens and atime.** Share-delete, buffer, close. Atime is taken from enumeration *before* our read, at first sight only. `SetFileTime` needs a `FILE_WRITE_ATTRIBUTES` handle ([MS](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfiletime)), so I prefer dropping atime as evidence.
9. **Disk per 10⁴ text docs [E]:** ~340 MB (text 100, FTS5 50–100, f32 vectors 125, catalog/evidence 20). Metadata-only files cost ~1–2 MB per 10⁴.
10. **If four weeks behind, cut in order:** GPU; claims beyond 10⁵; incremental sessions (rebuild all blocks in ~2 min); NER parallelism; multi-root.

## 4. Contradictions resolved

| Topic | Positions | Resolution |
|---|---|---|
| Process model | Arch: engine host over a pipe. Pkg/UX/Skeptic: UI + indexer child. | **I hold UI + indexer child.** Queries run through the same in-process `Engine` as the CLI and evaluation, with no RPC. I adopt Arch's Job Object, writer lock and memoisation. Arch's UI-stall concern is met because parsers run in the child. |
| Vector store | Arch: LanceDB. Me in R1: usearch. | **Both conceded.** Exact memmap behind Arch's `VectorIndex` interface. lance leaves the freeze. |
| Graph | Arch: query-time view | **Conceded.** |
| Session time base | Arch: `t_modified` | **I hold reconciled time.** Arch's exclusion rule is corrected per M1 and skeptic #2. |
| Block cap | 2,000 vs 20,000 | **Conceded: 2,000**, exact. Sparse linkage → LATER. |
| Watcher | Arch/IR/UX: while open | **Cut.** |
| Timeline | Arch: in-memory tree | SQLite range query. |
| ORT wheel | Pkg: `-directml` 1.24.4. IR: 1.31.1. | One pinned wheel for app and evaluation. MVP: `onnxruntime`. Move to `-directml` in P8 only if it gives ≥ 2× with parity. |

## 5. Scope

| Element | Scope |
|---|---|
| Light UI imports; ORT CPU granite int8; one pinned wheel | MVP |
| scandir change detection; placeholders metadata-only; share-delete buffered reads | MVP |
| Staged availability, newest-first, BELOW_NORMAL priority | MVP |
| Exact f32 memmap vectors; graph as a view (only duplicates stored) | MVP |
| Exact vectorised block sessions, B_max 2,000 | MVP |
| Dirty-block incremental sessions | MVP (first to cut) |
| No per-query table loads; WAL and disk guards; int8 parity measurement | MVP |
| ANN | LATER, if > 3×10⁵ chunks on 8 GB or p95 is missed |
| Sparse linkage; DirectML; RAM-adaptive workers; watcher | LATER |
| USN journal; CUDA build; stored semantic kNN edges; 10⁶ end-to-end claims | CUT |

## 6. Phase 7 must report

| ID | Measurement |
|---|---|
| M-S1 | Time to window, lexical-ready and dense-ready: 10 warm and 10 post-reboot runs each, on the dev machine and an 8 GB VM |
| M-SC1 | Enumeration files/s, cold and warm, on NVMe and USB HDD, at 10⁴ and 10⁵ files |
| M-E1 | Embedding tokens/s and chunks/s: granite int8 and fp32 and MiniLM int8, × batch 8/32/64, × 4 or 8 threads |
| M-E2 | int8 vs fp32: mean cosine and nDCG@10 |
| M-X1 | Extraction docs/s and MB/s per type; timeouts |
| M-N1 | NER words/s per worker |
| M-F1 | First-index wall time per stage at 10³, 10⁴ and 10⁵ files; time to first name search and first dense search |
| M-Q1 | Query p50/p95/p99 per stage at 10³–10⁵ files, plus synthetic 10⁶ |
| M-V1 | Exact-search latency and resident RAM at 10⁵, 3×10⁵ and 10⁶ vectors |
| M-R1 | Steady and peak RSS per process (psutil, 100 ms), searching and indexing, on 8 GB and 16 GB |
| M-I1 | Update time for Δ ∈ {0, 1, 10, 100, 1000} × n |
| M-I2 | Incremental == clean pass rate |
| M-SE1 | Session rebuild time and per-block time vs block size; forced-cut count |
| M-U1 | Keystroke-to-paint p95 and longest event-loop stall during indexing |
| M-W1 | Peak WAL size |
| M-W2 | Disk per 10⁴ files, by component |
| M-C1 | Evidence coverage per time source; placeholder share |

## Sources

- https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2/raw/main/config.json
- https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfiletime
- Round 1 sources (PEP 471, MS file attributes, voidtools, change-journal operations, ORT PyPI, SBERT efficiency).
- Local probes: numpy brute-force benchmark and scandir enumeration, both read-only on synthetic data or metadata.
