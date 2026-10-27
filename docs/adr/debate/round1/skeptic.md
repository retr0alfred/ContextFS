# Round 1 · Red-Team Skeptic

Severity: **B** = blocker, **M** = major, **m** = minor.

## 0. External claims

| # | Claim (role) | Verdict |
|---|---|---|
| V1 | PyInstaller 6.22, 2026-08-08 (Pkg) | Verified [1] |
| V2 | Inno Setup 7.1 (Pkg) | Verified. "Free for non-commercial use" is loose: the licence allows commercial use, and commercial users are only *asked* to pay [2]. |
| V3 | `onnxruntime-directml` latest is 1.24.4 (Pkg) | Verified [3] |
| V4 | DirectML in "sustained engineering" | Verified. Perf's "superseded" overstates it: DirectML is still supported, but new features go to WinML [4]. |
| V5 | WinML "downloads EPs from the Store" (Pkg) | Partly wrong. EPs come via Windows Update, and CPU and DirectML work everywhere. The real cost is the Windows App SDK [5]. |
| V6 | granite-97m-r2: Apache-2.0, 97M, 384-d, 32k context, 52 languages, MMTEB-R 60.3 (IR) | Verified (publisher figures). It ships `model_quint8_avx2.onnx`. It is **ModernBERT with 12 layers** (MiniLM-L6 has 6) and a 180k vocabulary. The 97m model is not Matryoshka [6][7]. |
| V7 | ms-marco-MiniLM-L6-v2: 22.7M, Apache-2.0, English | Verified [8] |
| V8 | gte-multilingual-reranker-base: 306M, Apache-2.0 | Verified, but it needs **`trust_remote_code`** and its page lists no ONNX export [9]. |
| V9 | FTS5 trigram gives "substring **and typo** matching" (IR) | **Wrong.** It does substring matching only, and terms under 3 characters match nothing [10]. Local test on SQLite 3.53.1: `MATCH 'gradeint'` against `gradient_descent_notes.pdf` returns 0 rows. |
| V10 | usearch has an mmap `view` (Perf) | Verified. Whether a view can be appended to is unverifiable (probably: `load`, add, rewrite the file) [11]. |
| V11 | `os.walk(followlinks=False)` descends junctions (Pkg) | Verified at local `Lib/os.py:432`. |
| V12 | Cloud attribute bits | The constants are verified [12]. "Never hydrates for any provider" is unverifiable without ProcMon. |
| V13 | USN journal needs admin | Verified for `FSCTL_READ_USN_JOURNAL` [13]. The unprivileged variant is unverifiable. |
| V14 | No Windows Sandbox on Home | Verified [14] |
| V15 | Artifact Signing for individuals is US/Canada only | Verified [15], so it is unavailable to this student. SignPath requires an already-released app, a verifiable CI build and MFA, and approves at its discretion [16]. |
| V16 | WAL does not work on network filesystems (Arch) | Verified. The same page warns of **checkpoint starvation**: with a reader always open, the WAL grows without bound [17]. |
| V17 | dateparser p99 < 30 ms; `holidays` covers Diwali (IR) | Unverifiable: no benchmark exists, and the docs do not list Diwali [18][19]. |

## 1. Attacks

**1. Nobody owns the reconciler, the paper's primary contribution.** [All · **B**]
- Decision 92 makes temporal provenance (A) primary. Arch writes a schema, IR consumes a "reconciled time", and Perf clusters on it. Nobody specifies the algorithm, its ground truth or its validation.
- The same author will write both the mtime-damage injector and the reconciler. The reconciler will learn the injector. That is circular.
- **Resolve:**
  - Name an owner.
  - *Before* writing the reconciler, freeze an empirical table of transfer signatures from real transfers: Explorer copy/move, robocopy, the Windows and 7-Zip extractors, `git clone`, an OneDrive round trip, FAT32/exFAT USB, downloads, and Save As. The generator may damage times only from that table.
  - Validate on the old-laptop copy, where `verify_corpus` already caught a rewritten mtime.
  - Leakage test: the reconciler must not import `datagen`, and its output must not change when the manifest is deleted.

**2. The `fs_rewritten` logic is inverted.** [Arch · **M**]
- An Explorer copy keeps mtime and resets birth time. So birth > mtime means the mtime *survived*, and excluding those files from sessions discards good data. After a laptop migration that is nearly the whole corpus.
- The destructive cases (clone, `cp`, downloads) give birth ≈ mtime ≈ transfer time, which only cohort and burst evidence can reveal.

**3. Time zones and precision are ignored.** [Arch, IR, Perf · **M**]
- EXIF `DateTimeOriginal` has no offset unless `OffsetTimeOriginal` is present.
- Zip and FAT times are local, with 2 s resolution. PDF dates often lack an offset.
- `docProps` `created` is inherited through Save As and templates.
- A `t_utc_ns` for any of these is a guess. IST's 5.5 h offset is larger than a plausible session gap G.
- **Resolve:** store naive-local time with `tz_known`, treat unknown zones as intervals, and add IST and DST fixtures.

**4. "Accessed" evidence is unreliable, and ContextFS itself pollutes it.** [Arch, IR · **M**]
- NTFS last-access updating is system-managed: off on volumes over 128 GB, on for small and removable ones [20].
- Where it is on, every extraction read rewrites the atime the index records.
- **Resolve:** read atime only from enumeration, freeze it with `SetFileTime(…, 0xFFFFFFFF)` on the read handle [21], or drop the feature.

**5. The indexer can break the user's saves.** [Pkg, Arch, Perf · **M**]
- Python's `open()` does not share delete [22]. While pypdf holds a big PDF, Word's rename-replace save fails with a sharing violation. That breaks "read-only on user files" in effect.
- **Resolve:** open through `CreateFileW` with `FILE_SHARE_READ|WRITE|DELETE`, and test it while Word is saving.

**6. Placeholders lose exactly the evidence the paper needs; directory metadata can be stale.** [Arch, Pkg, Perf · **M**]
- Office, PDF, EXIF and zip times all require opening the file, so cloud-only files (the ones sync rewrites) get file-system times only.
- `FindFirstFile`/`DirEntry` size and mtime are stale for files open for writing and for secondary hard links [23]. No-open change detection will miss these.
- **Resolve:** report per-source evidence coverage, model placeholders in the benchmark, and re-stat suspect entries through a handle.

**7. RRF k=60 as the pool builder ignores the log.** [IR · **M**]
- Decision 91: RRF scored MRR 0.428 against 0.503 for dense-only, and "fusion must be chosen on the dev split".
- P0 is capped at 300 by RRF rank, and the BM25-only and dense-only baselines then rank inside P0. They become pool-restricted hybrids, not the baselines the BRIEF lists.
- **Resolve:** report each baseline unpooled (exact, full corpus) as well as pooled. Make P0 the uncapped union (≤ 400). Choose fusion on dev, and report pool recall per query kind.

**8. Learned fusion will fit the generator.** [IR · **M**]
- Templates write both the documents and the queries ("ML exam"), so time and anchor features become trivially predictive. Holding out templates does not help when the lexicon is shared.
- Transductive corpus statistics (entity votes, the SymSpell vocabulary, anchors) must never be pooled across splits.
- **Resolve:** have test queries written blind to the templates, use UX's think-aloud queries as a *real* test set, and pre-register the primary metric.

**9. The explanations are not yet faithful.** [IR, UX · **M**]
- A linear `w·x` has no reference point, so "mostly because of…" will name whichever uncentred feature is largest (dense or BM25).
- UX's test ("non-zero contribution") is too weak, and negative contributions are hidden.
- Pool entry is a gate, not an additive term. LambdaMART's `pred_contrib` (TreeSHAP) has different semantics.
- The time chip shows a crisp range while the scorer uses a soft interval.
- **Resolve:** define contribution = s(x) − s(x with the feature at its frozen "absent" value), show it signed, and have the chip show the core and the decay band.

**10. "One engine, identical results" is false as proposed.** [Arch, IR, Perf, Pkg · **B**]
- (a) Perf uses exact search "in evaluation mode" and HNSW above 50k vectors, so the app and the paper diverge at whole-drive scale.
- (b) The int8 gate allows −1 nDCG point.
- (c) Pkg ships `onnxruntime-directml` 1.24.4, while IR and Perf cite `onnxruntime` 1.31.1: different kernels.
- (d) Vectors are built on DirectML, but queries are encoded on CPU.
- (e) Arch's default `feedback=True` makes GUI results depend on history.
- (f) Results are lexical-only until the model loads.
- (g) CUDA torch is reserved for dev and eval.
- **Resolve:** evaluate the shipped artifact (same ORT package, quantised file and CPU EP). Report exact vs ANN separately, turn feedback off, and include `signals_used` in the parity assertion.

**11. The process model is contradictory, and GPU crashes are not exceptions.** [Arch, Perf, Pkg, UX · **M**]
- Arch runs queries in an engine host reached over a pipe. Perf puts the query encoder in the UI process. Pkg and UX describe UI plus an indexer child.
- A DirectML device removal or TDR kills the process natively; "fall back on failure" catches nothing.
- **Resolve:** run inference outside the UI process, and keep a sticky "GPU crashed, use CPU" flag.

**12. Storage conflicts.** [Arch vs Perf · **M**]

| Question | Arch | Perf |
|---|---|---|
| Vector store | LanceDB | usearch |
| Graph | no stored graph | SQLite `edges` |
| Session time base | `t_modified` (the BRIEF asks for reconciled time) | reconciled time |
| Block cap | 2,000 | 20,000 |

- usearch likely forces the writer to rewrite about 0.5 GB per 5 s commit. LanceDB at 1 s commits piles up fragments.
- **Resolve:** first measure exact brute force over document vectors (125k × 384 fp32 ≈ 190 MB) with chunk re-scoring of candidates. If p95 holds, drop ANN; attack 10a goes too.

**13. WAL and disk are unbudgeted.** [Arch, Perf · **M**]
- A reader left open through a 2 h first index starves checkpoints (V16).
- There is no index-size estimate and no `SQLITE_FULL` handling.
- `VACUUM INTO` doubles disk use on a nearly full 256 GB C:.
- **Resolve:** short reads, forced checkpoints, free-space checks.

**14. A whole-drive watcher will thrash.** [Arch, Pkg · **M**]
- `ReadDirectoryChangesW` on `C:\` sees AppData and cache churn, so overflows are routine.
- Arch re-enumerates the whole root on overflow (~128 s cold on the HDD, per Perf), and watches removable roots, whose open handles block safe ejection.
- **Resolve:** no watcher in v2; a launch scan plus Refresh.

**15. A multilingual pitch built on English parts, and throughput estimated for the wrong model.** [IR, Perf, Pkg · **M**]
- spaCy `en_core_web_md` produces no entities for Devanagari text, and the default reranker is English-only.
- Perf's 300–450 chunks/s figure is for MiniLM. Granite has twice the layers, so T3 alone could take 1.5–2.5 h.
- The quint8 dynamic-quant model may not accelerate on DirectML, and a separate fp16 file means two vector spaces.
- The gte reranker breaks Pkg's "ONNX-exportable, ≤ 150 MB" budget.
- **Resolve:** benchmark granite vs MiniLM first.

**16. Minor.** [Pkg, Arch, UX · m]
- Signing: no route is realistic (V15). Rehearse an unsigned build on the actual viva machine.
- The rationale for rejecting WinML is wrong (V5).
- A `Local\` mutex is per logon session, so only the file lock protects a shared `index_dir`.
- When the CLI holds the writer lock, the GUI silently skips its update.
- `DirEntry` reports `st_ino` = 0 on Windows.
- `\\?\` paths may fail in `os.startfile`.
- The benchmark must produce `first_seen` through the real scan path.

**17. Scope will sink the schedule.** [All · **B**]
- The proposals together add up to two-process RPC, extractor pools, Hypothesis equivalence, HNSW, sparse linkage, three ONNX ports, DirectML, SymSpell, anchors, LambdaMART, ERASER, SignPath, NVDA work and a user study, *plus* the benchmark and the reconciler.
- Single points of failure:
  - the benchmark generator, which gates fitting, reconciler validation and every paper number;
  - ONNX tokenizer and pooling parity (`1_Pooling`);
  - freezing spaCy and lance.
- Sessions and the graph get the most engineering, yet the log's prior-art check (Connections, SOSP 2005) says they are not novel.
- **Cut list:**
  - no watcher;
  - UI and queries in one process, plus one indexer child;
  - brute-force vectors;
  - no stored graph;
  - v1's exact sessions per gap block, with Arch's cap;
  - a linear scorer only;
  - DirectML as a stretch goal behind CPU parity.

## 2. Questions every role must answer in Round 2

1. Who owns the reconciler, and what ground truth exists that the generator did not produce?
2. What exactly does evaluation run (ORT package and version, model file, EP, exact or ANN)? Is it the shipped artifact?
3. Which single process topology, and where does inference live?
4. Which single vector store, and what does brute force measure at 10⁵ and 10⁶ chunks?
5. How is P0 built, and how are unpooled baselines reported?
6. What exactly do "contribution" and "absent" mean, and how do chips stay faithful to soft time?
7. How are timestamps without a zone, or with 2 s precision, stored and compared?
8. How does the indexer open files (share-delete, frozen atime)?
9. What is the on-disk budget per 10⁴ files?
10. What is cut first if the project is four weeks behind?

## Sources

[1] pyinstaller.org/en/stable/CHANGES.html · [2] groups.google.com/g/innosetup/c/j6yZATewzKQ ; packages.nuget.org/packages/Tools.InnoSetup/7.1.0/License · [3] pypi.org/pypi/onnxruntime-directml/json · [4] onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html · [5] learn.microsoft.com/windows/ai/new-windows-ml/overview · [6] huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2 · [7] huggingface.co/api/models/ibm-granite/granite-embedding-97m-multilingual-r2 (and its raw/main/config.json) · [8] huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2 · [9] huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base · [10] sqlite.org/fts5.html · [11] github.com/unum-cloud/usearch · [12] learn.microsoft.com/en-us/windows/win32/fileio/file-attribute-constants · [13] learn.microsoft.com/en-us/windows/win32/FileIO/change-journal-operations · [14] learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/ · [15] learn.microsoft.com/azure/artifact-signing/quickstart · [16] signpath.org/terms · [17] sqlite.org/wal.html · [18] dateparser.readthedocs.io/en/stable/_modules/dateparser/search.html · [19] python-holidays.readthedocs.io/en/latest/_autosummary/holidays.countries.india.html · [20] winaero.com/disable-ntfs-last-access-time-updates-in-windows-10 ; github.com/MicrosoftDocs/windowsserverdocs/blob/main/WindowsServerDocs/administration/windows-commands/fsutil-behavior.md · [21] learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfiletime · [22] bugs.python.org/msg164514 ; github.com/golang/go/issues/32088 · [23] devblogs.microsoft.com/oldnewthing/20111226-00/?p=8813
