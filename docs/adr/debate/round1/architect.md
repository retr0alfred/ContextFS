# Round 1 — Systems Architect

## 1. Position summary

- **Two processes, one engine.** The UI process (Qt, no torch) talks over a local named pipe to an engine host process (models, SQLite writer, indexer). The engine host runs the same `contextfs.engine` library that the CLI and the eval harness import in-process. Nothing runs once the window is closed.
- **SQLite is the single commit point.** Catalog, time evidence, stage state, FTS5 chunks, sessions and duplicate edges all live in one WAL database. Vectors are kept separately, keyed by `(file_id, embed_fp)`, and count as valid only when SQLite says they are current.
- **The graph becomes a query-time view.** NetworkX/JSON is removed. Neighbours are computed live for the ~10 seeds of each query.
- **Incremental = memoised clean rebuild.** Every derived stage is a pure function of fingerprinted inputs. Sessions split *exactly* at idle gaps longer than G. A property test asserts incremental == clean.
- **Scans are resumable, and their verdicts are local.** The walk is stat-only and persisted per directory. A file is tombstoned only when its own directory was listed successfully.

## 2. Proposals

### 2.1 Process model

**Decision.** The UI spawns `ContextFS.exe --engine` inside a Job Object with `KILL_ON_JOB_CLOSE`, so the engine cannot outlive it [1]. Extraction runs in 1–2 recycled subprocesses with per-file timeouts. IPC uses stdlib `multiprocessing.connection` over `AF_PIPE`.

Engine threads: `rpc`, `query` (read connection), `indexer` (sole writer), `watcher`, and `model-exec`. `model-exec` is one priority queue for all inference: queries go first, and index batches are capped at about 300 ms.

**Watcher, only while the window is open.** `ReadDirectoryChangesW` events are *hints* that mark directories dirty. They are never stored. On buffer overflow or `ERROR_NOTIFY_ENUM_DIR`, the root is re-enumerated [2]. There is no watcher on network roots (64 KB buffer cap) [2]. The USN journal is rejected because it needs administrator rights [3].

This fits "no background service" read as: *nothing outlives the window, no autostart, no activity record beyond index state.* Observed-change times are kept at scan-batch granularity only.

- **Alternatives.** (a) A single process, as in v1. Parsers that hold the GIL make the UI stutter, and a parser segfault kills the window. (b) A tray service, which violates the principle.
- **Trade-offs.** About 100–150 MB of duplicated baseline, and an IPC schema to version.
- **Would change my mind.** A single process with UI stalls under 100 ms during indexing, and zero crashes on fuzzed documents.
- **Measurement.** p99 event-loop latency during a 10⁴-file index, and crash count over 1,000 malformed PDF/DOCX files.

### 2.2 Launch, step by step

1. **Paint the window.** If `\\.\pipe\ContextFS-<user>` answers, send "activate" and exit; otherwise start a `QLocalServer`.
2. **Read settings** from `%LOCALAPPDATA%\ContextFS\settings.json`. If missing, run first-run.
3. **Spawn the engine.** It takes an exclusive `index.lock`. If a CLI update holds it, the engine opens read-only.
4. **Check the schema.**
   - Older: `VACUUM INTO backups/pre-vN.db` [4], then migrate in one transaction per version and restore on failure.
   - Newer than this build: open read-only.
   - `clean_shutdown=0`: run `PRAGMA quick_check` at idle [5].
5. **Search is available at once in lexical mode** (FTS5 + filename + time). Dense signals join once the models load. Each response lists `signals_used`.
6. **Resume or start the update.** An interrupted scan resumes; otherwise the launch update (§2.5) starts. Search never waits for it: it reads the last committed WAL snapshot [6]. Files that changed since indexing are flagged `stale` in their explanation.
7. **Exit.** Finish the current batch, run `wal_checkpoint(TRUNCATE)`, set `clean_shutdown=1` and release the lock.

### 2.3 Engine API (`src/contextfs/engine/api.py`; `EngineClient` implements the same Protocol)

```python
class Engine:
    @classmethod
    def open(cls, index_dir: Path, *, mode: Literal["write","read"]="write") -> "Engine"
    def add_root(self, path: Path, *, exclusions: Sequence[str] = ()) -> Root
    def remove_root(self, root_id: int) -> None
    def update(self, *, roots=None, progress: ProgressSink|None=None, cancel: CancelToken|None=None) -> UpdateReport
    def reconcile(self, stages: Sequence[str]|None=None, *, cancel=None) -> ReconcileReport
    def status(self) -> IndexStatus  # generation, pending per stage, roots online, quarantined
    def search(self, query: str, *, k: int=10, now: datetime, signals: SignalSet=ALL,
               filters: Filters|None=None, feedback: bool=True) -> SearchResponse
    def first_stage(self, query: str, *, n: int, channels: Sequence[str]) -> CandidatePool
    def features(self, query: str, pool: CandidatePool, *, now: datetime) -> FeatureTable
    def explain(self, query: str, file_id: int, *, now: datetime) -> Explanation
    def time_evidence(self, file_id: int) -> TimeEvidence
    def neighbours(self, file_id: int, kinds: Sequence[str]) -> list[Edge]
    def record_feedback(self, query: str, file_id: int, event: str) -> None
```

Every input that affects ranking is an explicit argument, `now` included. Index-bound settings are stored in `meta` with a hash. The CLI becomes thin Typer over this API. v1 shows why: GUI and CLI run stages in **different orders** (`gui/app.py` vs `cli/main.py`), and only the CLI applies feedback.

**Parity test** (`tests/test_surface_parity.py`). 50 queries through `Engine`, `EngineClient` and `contextfs search --json`, with `now`, device and threads pinned. Ranked paths and explanations must be equal, and scores within 1e-6. Ties break on path, never on id.

### 2.4 Indexing pipeline

Per-file stages are memoised on `(file_id, stage) → (input_fp, stage_version)`. `stage_version` combines the code version, the model id and a hash of the relevant config:

`walk` (stat) → `identify` (xxh3 of local files; move detection) → `fs_time` → `extract` (subprocess: blocks **plus** Office/PDF/EXIF/zip timestamps) → `chunk` → {`embed`, `fts`, `entities`, `date_mentions`, `minhash`}

| Aggregate stage | Global input | Incremental rule |
|---|---|---|
| entity categories | votes | delta-maintain `entity_votes`; re-categorise only entities whose majority flipped |
| date classification | recurrence (saturates at 4) | delta-maintain counts; reclassify files whose dates crossed a saturation bucket |
| time reconciliation | per-folder burst stats | recompute dirty folders |
| sessions | time order | blocks = maximal runs of files with consecutive gaps ≤ G over `t_modified` (below) |
| duplicates | MinHash LSH buckets | per-file bucket update |
| timeline | classified dates | interval tree built in memory at open |
| tree | — | removed from the pipeline (retrieval never used it) |

**Session blocks.** v1's idle-gap gate forbids merging across a larger gap, so blocks are *exactly* independent. Each is memoised by `hash(sorted (path, feature_fp))`. Two real-drive rules:
- **Chaining.** Blocks over `B_max` (initially 2,000 files) are cut deterministically at their largest internal gaps, and each cut is logged.
- **Bulk copies.** Files with `fs_rewritten=1` and no embedded time are excluded from sessions, because their time is unknown.

A clean rebuild uses the same blocking, so the two still agree.

**`reconcile()`** recomputes aggregates into shadow tables, diffs and swaps. It runs on a stage-version change, every 20 updates, or on demand.

**Test** (`tests/test_incremental_equivalence.py`). Hypothesis edit sequences (add, modify, delete, move, case-rename). After each step, a canonical dump must equal a clean build.

**Poison files.** Quarantined after 2 crashes or timeouts, retried when the fingerprint changes.

### 2.5 Scans, multi-root, safety

**Walk.** BFS over a persisted `dir_queue` with `os.scandir`. On Windows, `DirEntry.stat()` needs no extra syscall except for reparse points, and Python ≥3.12 exposes `st_birthtime` [10].

- **Reparse points** are skipped by default.
- **Cloud-only files.** Files marked `RECALL_ON_DATA_ACCESS`, `RECALL_ON_OPEN` or `OFFLINE` are never opened; they get metadata only [11].
- **Commits** go per directory, or every ~1,000 entries or 1 s, so a crash or sleep resumes from the queue.
- **Denied directories** mark their children `unreachable`, not deleted.
- **Unplugged volumes** make their root `offline`. A root is matched by volume serial if its drive letter changes.
- **Files that change during extraction** are re-queued.
- **No more `touch_seen`.** v1 rewrote a row for every file on every scan (O(corpus)); that is removed.

**Moves.** A new path whose `(size, hash)` matches a same-scan tombstone inherits its `file_id`, so nothing is re-embedded.

**Multi-root.** Nested roots are merged; removing a root purges its rows and vectors.

**Index location.** `%LOCALAPPDATA%` by default; configurable. Paths inside a root or on a network share are refused (WAL needs same-host shared memory [6]).

**Corruption.** A failed `quick_check` sets the database aside and triggers a rebuild of the derived data. Feedback is kept in `feedback.jsonl`.

### 2.6 Storage layout

`%LOCALAPPDATA%\ContextFS\index\` holds `catalog.db` (WAL; FTS5 verified in the bundled SQLite 3.53.1), `vectors/` (LanceDB behind a `VectorIndex` interface), `index.lock`, `backups/` and `logs/`.

```sql
roots(root_id PK, path, volume_serial, kind, state, exclusions_json)
dirs(dir_id PK, root_id, rel_path, parent_id, list_state, last_listed_scan)
files(file_id PK, root_id, dir_id, rel_path, rel_path_fold, size, mtime_ns, birth_ns, attrs,
      content_hash, hash_state /*hashed|placeholder|too_large|locked*/,
      status /*present|deleted|unreachable|offline*/, UNIQUE(root_id, rel_path_fold))
stage_state(file_id, stage, input_fp, stage_version, status, attempts, error, PRIMARY KEY(file_id, stage))
chunks(chunk_id PK, file_id, ord, char_start, char_end, text)  -- + FTS5 external-content table
time_evidence(file_id, source /*fs|office_core|pdf_info|pdf_xmp|exif|zip_entries|observed*/,
      kind /*created|modified|accessed|taken|first_seen|superseded_mtime*/,
      t_utc_ns, tz, precision, raw, input_fp, PRIMARY KEY(file_id, source, kind))
file_time(file_id PK, t_created_ns, t_modified_ns, created_src, modified_src, confidence,
      fs_rewritten, rewrite_reasons_json, reconciler_version)
```

**Time evidence v1 discards.** When the mtime moves but the hash does not, v1 throws the old mtime away. It is now stored as `superseded_mtime`. ContextFS's own `first_seen` is also stored as evidence, because no sync or restore can rewrite it. `chunks` holds text once; v1's duplicate copy in `documents.text` goes.

**Cross-store atomicity.**
- **Writes:** vectors first, then the SQLite `stage_state` commit.
- **Queries:** results are filtered against present, current files.
- **Deletes:** a SQLite tombstone first, then a batched vector purge.
- **Cleanup:** `reconcile()` drops orphan vectors.

**Graph as a view.** NetworkX was measured at about 54 GB for 10⁸ edges [7]; at 10⁶ files (~10⁷ edges) that is several GB. Instead, 2-hop expansion uses ANN neighbours, a df-capped entity inverted index, folder prefixes, sessions and the timeline. Only duplicate edges are stored.

**Vectors.** LanceDB stays. Rows not yet indexed are brute-forced until `optimize()` folds them in, and IVF_PQ, IVF_RQ and IVF_HNSW_SQ are available [8]. sqlite-vec would give atomicity, but it is self-described as pre-v1 [9].

- **Alternatives.** Persisted CSR graph; one DB per root.
- **Would change my mind.** p95 live-expansion latency above 150 ms at 10⁶ files would justify a persisted kNN table.
- **Measurement.** Expansion latency and index size at 10⁴, 10⁵ and 10⁶ synthetic files.

## 3. Positions on other questions

- **UI:** keep PySide6-Essentials; the process split removes the startup objection.
- **Packaging:** one-dir build, one exe with an `--engine` mode, CPU by default.
- **Memory:** one resident model set; document vectors on disk behind ANN, not loaded per query as in v1.
- **IR:** every layer consumes `first_stage` + `features`, so baselines share pools.

## 4. Risks in my own proposal

- **IPC failures** (version mismatch, hung host): heartbeat plus auto-restart.
- **Block cuts are approximate.** Measure recall on very active corpora.
- **Live semantic expansion depends on ANN recall.**
- **The equivalence test may be flaky** if float paths vary with thread count.
- **Unverified:** that an Explorer copy sets birth time after mtime. Measure before trusting `fs_rewritten`.
- **Burst detection may misflag** real bulk saves (camera imports).

## 5. Sources

1. https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information
2. https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-readdirectorychangesw
3. https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew ; https://learn.microsoft.com/en-us/windows/win32/fileio/walking-a-buffer-of-change-journal-records
4. https://sqlite.org/lang_vacuum.html#vacuuminto
5. https://sqlite.org/pragma.html#pragma_quick_check
6. https://sqlite.org/wal.html
7. https://arxiv.org/pdf/1606.07550
8. https://docs.lancedb.com/indexing/vector-index ; https://docs.lancedb.com/resource/faq
9. https://github.com/asg017/sqlite-vec
10. https://docs.python.org/3/library/os.html
11. https://learn.microsoft.com/en-us/windows/win32/fileio/file-attribute-constants
