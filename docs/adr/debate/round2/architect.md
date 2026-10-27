# Round 2 — Systems Architect (revised)

**What changed from Round 1:**
- **Dropped:** the RPC engine host, LanceDB, the watcher and the `fs_rewritten` rule.
- **Changed:** sessions now run on reconciled time.
- **Added:** shared-delete opens, zone-aware time evidence, and a disk and WAL budget.

## 1. Revised proposal

### 1.1 Topology: two processes

| Process | Owns | Never does |
|---|---|---|
| **App** (UI + query) | Qt; `Engine` in read mode; CPU ONNX query encoder; per-query read transactions; feedback writes (`busy_timeout`) | GPU work, extraction, bulk writes |
| **Indexer child** (`--indexer`, Job Object `KILL_ON_JOB_CLOSE` [1]) | the only writer: walk, extract, NER, embed, aggregates | outlive the window |

The pipe carries control only; data flows through SQLite, and the app reloads the vector manifest when `meta.index_generation` changes. The indexer records `in_progress(file_id)` before each file; if it dies, the app restarts it and quarantines that file after 2 attempts. **No watcher in v2**: launch scan plus **Refresh**, so "no background service" holds unqualified.

### 1.2 Engine API (one library, three callers)

```python
Engine.open(index_dir, mode="read"|"write")
engine.search(query, config: SearchConfig, as_of, trace=False) -> SearchResponse
engine.first_stage(query, config, as_of, unpooled=False) -> CandidatePool
engine.update(roots=None, progress=None, cancel=None) -> UpdateReport
engine.recompute_aggregates(stages=None)        # clean rebuild of global stages
engine.status() / explain(...) / time_evidence(file_id)
```

`SearchConfig` is IR's (frozen, hashed, feedback off); responses carry `generation`, `config_hash`, `embedder_fp` and `signals_used`. Paper numbers come from `contextfs.exe eval --final` in the **frozen build**.

### 1.3 Pipeline and storage

**Per-file stages** are memoised in `stage_state(file_id, stage, input_fp, stage_version, status, attempts)`:

`walk` (dir-entry stat) → shared open + extract (content hash computed during the read) → `time_capture` → `chunk` → {`fts`, `embed`, `entities`, `date_mentions`, `dup_check`}

**Global stages** are pure functions of SQLite state:

| Stage | MVP incremental rule |
|---|---|
| entity categories | SQL vote recompute; re-tag only flipped entities |
| date classification | changed files, plus files whose dates crossed recurrence saturation |
| reconciled time | IR's estimator, per dirty folder |
| sessions | gap blocks over **reconciled** time (blocks are exactly independent); re-cluster changed-fingerprint blocks; cap 2,000 |
| duplicates | changed doc's vector against all docs (cos ≥ 0.70) → shingle Jaccard → `dup_edges` |

**Graph.** Only `dup_edges` is stored. Neighbours of the ~10 seeds come from one batched exact matmul plus SQL lookups for entity, folder, session and timeline links.

**Vectors: exact brute force over fp32 append-only segments** (`seg-N.f32` + `.ids`, mmapped).
- Segments are fsync'd before registration in SQLite `vector_segments`, which stays the commit point. Deletes are masked; idle compaction swaps the manifest and deletes old files once unmapped.
- **Measured here** (numpy, random 384-d, warm RAM, top-200):
  - 10⁵ chunks: **7.0 ms**, 146 MB
  - 5×10⁵ chunks: **39 ms**, 732 MB
  - 10⁶ chunks: about 80 ms and 1.46 GB, extrapolated
  - fp16 storage with blockwise upcast: 424 ms at 5×10⁵, rejected
- **Consequence:** eval and the app run the same exact search.

**Time-evidence schema.** I own the schema and its capture; IR owns the estimator.

```sql
time_evidence(file_id, source, kind,
  wall_s INTEGER,            -- local wall-clock seconds, no zone
  utc_s INTEGER NULL, offset_min INTEGER NULL,
  tz_state TEXT CHECK(tz_state IN('utc','offset','floating')),
  resolution_s INTEGER,      -- 1 | 2 (FAT, DOS zip) | 86400 (date only)
  reliability TEXT, raw TEXT, captured_scan INTEGER, input_fp TEXT,
  PRIMARY KEY(file_id, source, kind))
file_time(file_id PK, t_lo_utc_s, t_hi_utc_s, t_point_utc_s, method,
  evidence_used_json, estimator_version)   -- written only by IR's estimator
evidence_coverage   -- view: share of files per source and kind
```

How each source is recorded:

| Source | Stored as |
|---|---|
| NTFS times | `utc` |
| DOS zip entries, FAT times | `floating`, `resolution_s=2`; `utc` when the zip extended-timestamp field is present |
| EXIF | `OffsetTimeOriginal` when present, else `floating` |
| PDF dates | with their offset when present |
| ContextFS observations | `first_seen`; `superseded_mtime` (mtime moved, hash unchanged) |
| `accessed` | taken from enumeration before our first read, once, marked `low` |

**File opens.** `CreateFileW(GENERIC_READ, FILE_SHARE_READ|WRITE|DELETE, OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN)`, then `msvcrt.open_osfhandle`. Word's rename-replace save keeps working. A file that changes during the read is requeued after a re-stat.

**Scan.** Packaging's walker; a persisted per-directory queue; commits every 500 files or 5 s. Only a successfully listed directory may tombstone its children. A new path matching a same-update tombstone's `(size, hash)` inherits its `file_id`, keeping `first_seen`.

**Safety.**
- **Writer lock.** `msvcrt.locking` on `writer.lock`. If the CLI holds it, the GUI says so and retries.
- **Second launch.** `QLocalServer` focuses the existing window.
- **Migrations.** One transaction per migration. A `VACUUM INTO` backup is taken only if free space exceeds 1.2× the database.
- **WAL.** Readers never hold a transaction past one query. Checkpoint `PASSIVE` per batch, `TRUNCATE` above 256 MB, and set `journal_size_limit` [2].
- **Disk.** Pause indexing below 2 GB free plus the batch estimate; on `SQLITE_FULL`, pause with a banner.
- **Dirty shutdown.** Run `quick_check`; if it fails, rebuild, keeping settings and feedback.

## 2. Response to the skeptic

| # | Verdict |
|---|---|
| 1 | **Accept** the facilitator split. Leakage test: `contextfs.engine` must not import `datagen`, and output must not change when manifests are deleted. `file_time` is written only via `Estimator.estimate(evidence, folder_ctx)`. |
| 2 | **Accept** (M1: `created > modified` means mtime *survived*). Flag removed; session exclusion now depends on the estimator's interval width exceeding G. |
| 3 | **Accept.** No fabricated UTC (see the schema). IST and DST fixtures are added to the capture tests. |
| 4 | **Partially accept.** I reject `SetFileTime(…0xFFFFFFFF)`: it needs `FILE_WRITE_ATTRIBUTES` [3], which is write access to user files. Instead, atime is captured once from enumeration before our read and marked `low`. |
| 5 | **Accept.** Shared-delete opens, plus a manual test while Word saves. |
| 6 | **Accept.** Placeholder coverage is reported via `evidence_coverage`; entries touched in the last 24 h are re-statted through a handle. |
| 10 | **Accept.** Exact search everywhere; feedback off; `signals_used` asserted; one pinned ORT wheel for venv, frozen build and eval; `embedder_fp` includes the EP; DirectML LATER, as its own result row. |
| 11 | **Accept.** One topology. No GPU inference in the UI process; a GPU crash later sets a sticky `gpu_disabled` flag. |
| 12 | Resolved in §4. |
| 13 | **Accept.** See §1.3 and Q9. |
| 14 | **Accept.** No watcher. |
| 16 | **Accept.** File lock, visible CLI-lock state, hash-based moves, strip `\\?\` before `os.startfile`. |
| 17 | **Accept.** See §5. |

## 3. Answers to the ten questions

1. **Reconciler ownership and ground truth.** IR owns the estimator. Ground truth the generator did not produce:
   - the M1 transfer table, extended to Explorer, 7-Zip, `git clone` and OneDrive;
   - the old-laptop copy;
   - real files content-copied so their fs times are destroyed, with their in-file metadata kept as the answer.

   The generator may damage times only per that frozen table.
2. **Evaluation setup.** The frozen build's `eval`: pinned `onnxruntime` CPU wheel, the shipped quantised model, CPU execution provider, exact search.
3. **Topology.** App (UI + query, CPU) plus an indexer child.
4. **Vector store.** fp32 segments with brute-force search; figures in §1.3.
5. **P0.** IR decides. I support an uncapped union of at most 400. `first_stage(unpooled=True)` gives exact full-corpus BM25 and dense baselines at no extra cost.
6. **Contribution and absent.** contribution_i = s(x) − s(x with feature i set to absent_i). The absent values are frozen in the scorer JSON before any results are seen. Contributions are shown signed, and chips show both the core interval and the decay band.
7. **Zone-less and 2 s timestamps.** Stored as `wall_s` with `floating` and `resolution_s`, then compared as intervals.
8. **File opening.** Shared read/write/delete. Placeholders are never opened. atime is not frozen.
9. **Disk per 10⁴ files** (estimate; P7 measures). Assumes 25% text documents, 8 chunks each, about 1 KB of text per chunk:

   | Item | Size |
   |---|---|
   | vectors | 34 MB |
   | chunk text | 20 MB |
   | FTS5 | 15–25 MB |
   | entities and dates | ~10 MB |
   | catalog, evidence, state | ~10 MB |
   | **total** | **≈0.1 GB (±2×)**, about 10 GB per 10⁶ files |
10. **If four weeks behind, cut in this order:**
    1. near-duplicates
    2. removable-root handling
    3. resumable scans (restart the scan instead)
    4. block-cap tuning

    Never cut: evidence capture, exact search, the parity test.

## 4. Contradictions resolved

| Topic | Resolution |
|---|---|
| Process model (Perf, Packaging, UX) | **Concede:** UI + query in one process, plus an indexer child. ONNX makes queries light, so an RPC host buys nothing. |
| Vector store (Perf: usearch) | **MVP: brute force**, as measured. usearch LATER, behind `VectorIndex`, if chunks exceed ~10⁶ or 8 GB RAM fails. |
| Graph (Perf: stored ANN kNN edges) | **Hold:** exact neighbours computed live are faithful and trivially incremental. I would store kNN only if p95 expansion exceeds 150 ms. **Agree** that entity and structural links stay unstored. |
| Session time base | **Concede:** reconciled time. |
| Block cap (Perf: 20k, sparse linkage) | **MVP:** v1's exact linkage, vectorised, capped at 2,000. Sparse linkage goes to P8 behind Perf's F1 ≥ 0.95 gate. |
| Commit cadence | **Concede** Perf's 500 files / 5 s. |
| Single instance (Packaging) | **Adopt** `msvcrt.locking` as the real guard. |
| Search signature (IR) | **Adopt** `SearchConfig`, `as_of` and `trace`. |
| Watcher (IR, UX, Perf allow it) | **Hold "none"**, per skeptic attack 14 and scope. |
| Tree and 3D view (UX) | **Agree:** tree CUT; 3D view stays CLI-only, LATER. |

## 5. Scope

| MVP (P4) | LATER (P6 / P8) | CUT |
|---|---|---|
| App + indexer child, Job Object, control pipe | Watcher (P8) | RPC engine host |
| Exact fp32 vector segments | usearch ANN (P8) | Extractor pool |
| `dup_edges`, live neighbours | Sparse session linkage (P8) | LanceDB |
| Exact sessions per gap block, cap 2,000, reconciled time | Estimator novelty (P6, IR) | NetworkX/JSON graph |
| Time-evidence schema and capture (fs, Office, PDF, EXIF, zip, first_seen) | Delta-maintained aggregates (P8) | Semantic tree |
| Estimator v0 (IR) | Hypothesis equivalence test | |
| `stage_state` memoisation, aggregate recompute | Network-root tuning, index-move UI | |
| Shared opens; placeholders metadata-only | DirectML + sticky crash flag (P8) | |
| Resumable per-directory scan; offline roots by volume serial | Feedback re-rank | |
| Hash-based move inheritance | 3D view (CLI) | |
| Parity test (GUI, CLI, frozen eval); scripted incremental == clean test | | |
| Writer lock, migrations, disk and WAL guards, rebuild on `quick_check` failure | | |

## 6. Sources

1. https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information
2. https://sqlite.org/wal.html ; https://sqlite.org/pragma.html#pragma_journal_size_limit
3. https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfiletime

Local measurements: the numpy brute-force runs (2026-10-27, dev machine); facilitator M1 (`measurements.md`).
