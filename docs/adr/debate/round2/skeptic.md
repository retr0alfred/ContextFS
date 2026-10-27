# Round 2 · Red-Team Skeptic

Severity: **B** blocker, **M** major, **m** minor. "Measured" = run today in `.venv` on synthetic data.

## 1. Round 1 attacks: status

| # | Attack | Status | Why |
|---|---|---|---|
| 1 | Reconciler unowned and circular | **Partly** | Owners and protocol E1–E5 exist, but the ground truth is circular or tiny (N2) and the generator is broken (N1). |
| 2 | `fs_rewritten` inverted | Resolved | Confirmed by M1; the flag was removed. |
| 3 | Zones and precision | Resolved | `tz_state` and `resolution_s` added. Perf's current-zone midpoint differs from IR's −14…+12 h interval; use IR's. |
| 4 | Self-polluted atime | Resolved | Never used. |
| 5 | Share-delete | Resolved | `CreateFileW` with full sharing, plus a Word test. The size caps conflict (N11). |
| 6 | Placeholders, stale entries | Resolved | Coverage reporting and re-stat through the handle. The OneDrive ProcMon check is still manual. |
| 7 | RRF pool | Resolved | Uncapped union, unpooled baselines, fusion chosen on dev. Chunk-level detail remains (N10). |
| 8 | Fitting the generator | **Partly** | Blind queries, but nobody says who writes them. The student is not blind. |
| 9 | Explanations | Mostly | Frozen absent value, signed contributions, Δrank gate. Chip removal ≠ counterfactual (N12). |
| 10 | Parity | **Partly** | Fixed by design, but broken by BLAS non-determinism (N6) and the CPU instruction set (N7). |
| 11 | Process and GPU crash | Resolved | Consensus topology; sticky GPU flag. |
| 12 | Storage conflicts | Resolved | Exact f32 vectors. The 8 GB ceiling is open (N9). |
| 13 | WAL and disk | Resolved | Guards specified. The estimates disagree (N15). |
| 14 | Watcher | Resolved | Cut. |
| 15 | Multilingual and throughput | **Partly** | The embedder gate exists, but throughput is still estimated: 5–8 h on 4 cores. |
| 16 | Minor | Resolved | — |
| 17 | Scope | **Partly** | The cuts are real, but the MVP union is still large (§4). |

## 2. New attacks

**N1. The benchmark generator would invalidate contribution A as it stands.** [IR, Arch; future P3 owner · **B**]

- `datagen/writers.py:43` sets only atime and mtime (`os.utime`). Birth time is the generation time, so **`birth > mtime` (the copy signature) fires on every synthetic file**.
- Measured: python-docx defaults to `created = modified = 2013-12-23`, and reportlab stamps PDFs with the real time. Under IR rule 2, every DOCX would be dated 2013.
- **Resolution:** set birth time with `SetFileTime` on the generator's own files, write consistent in-file metadata, and add a zero-damage persona test (§5.1).

**N2. The "independent" ground truth is circular or tiny.** [IR, Arch, UX · **B**]
- The old-laptop truth is the `datagen` spec: 40 synthetic files, **one** of them damaged.
- Arch's "content-copied real files with in-file metadata as the answer" scores the estimator against its own input.
- 50 participant labels give CIs too wide to separate methods.
- **Resolution:** truth is never an estimator input. Seal the pre-transfer FS times in a file the engine cannot read, target 300 or more real files from several donors, and compute the achievable CI width first.

**N3. The target time is undefined.** [IR, Arch, Perf, UX · **B**]
- `file_time` is one interval, but rule 2 mixes EXIF capture, PDF creation, docProps *modified* and zip times. Perf sorts on `t_modified_ns`, which no schema has.
- A lecturer's PDF downloaded last week was authored in 2019. "Restoring" 2019 makes "the PDF I downloaded before my exam" *worse*: users remember arrival and use, not authorship.
- **Resolution:** one interval per event (`authored`, `arrived_here`, `last_user_edit`). Sessions use `last_user_edit`, and the ground truth labels which event it records.

**N4. Single content-only copies are invisible.** [IR · **M**]
- M1 shows that `shutil.copy` leaves no trace in the FS times.
- Rule 3 needs a burst of N or more files, so single downloads and attachments (the commonest way a student receives a file) are trusted as-is.
- Resolution: report results per damage type with singletons as their own row, and do not claim to detect them.

**N5. The session gap G = 240 h was tuned on the smoke set and makes "exact blocks" moot.** [Perf, Arch · **M**]
- `contextfs.toml:220` sets G = 240 h (the code default is 72 h). Ten-day silences are rare on a whole drive, so the 2,000-file cap's cuts *become* the algorithm.
- **Resolution:** fit G on dev, report the forced-cut share, and limit sessions to extracted documents.

**N6. Exact search is not bit-reproducible across BLAS thread counts.** [IR, Arch, Perf · **M**]
- Measured: `X @ q` with OpenBLAS default threads vs `OPENBLAS_NUM_THREADS=1` is **not bitwise equal**. The maximum difference was 4.8e-7 at 10⁵ vectors and 5.4e-7 at 3×10⁵.
- That is inside IR's 1e-6 tolerance, but near-ties can still swap ranks or P0 membership. The GUI and the CLI can easily differ in BLAS threading.
- The latency claims are verified: p50 6.3 ms at 10⁵ and 19.2 ms at 3×10⁵.
- **Resolution:** pin BLAS threads in `Engine.open`, sort on scores rounded to 1e-5 then by path, and run the parity test under different thread environments.

**N7. "The shipped artifact" ≠ the user's numbers.** [IR, Pkg · m]
ORT documents that U8S8 int8 saturates on AVX2 and on AVX-512 without VNNI, but not on VNNI CPUs [3]. State the evaluation CPU, and report top-10 overlap on the AVX2 3700U.

**N8. Pinned versions released yesterday.** [Pkg, IR · m]
- `onnxruntime` 1.31.1 was uploaded on 2026-10-26 [1], and `symspellpy` 6.10.1 was released 2026-10-27 [2].
- Resolution: pin a release at least two weeks old and re-pin deliberately.

**N9. A whole drive on an 8 GB machine has no defined behaviour.** [Perf, Arch, IR · **M**]
- f32 chunk vectors top out near 3×10⁵ (Perf); 10⁶ chunks is 1.46 GB, and a non-resident memmap rereads disk on every query. Every role defers ANN, yet the BRIEF promises whole drives.
- **Resolution:** decide now, because it changes the dense baseline: a document-level first stage (≈190 MB) with chunk re-scoring, or a documented 10⁵ limit.

**N10. Dense P0 at chunk level.** [IR · m] 200 chunks are fewer than 200 documents. Retrieve until 200 distinct documents.

**N11. The size caps conflict and leave a textbook hole.** [Pkg, Perf · **M**]
The caps are 64 MB (Pkg), 100 MB (Perf) and none (Arch). Scanned textbooks often exceed them and become metadata-only. Pick one cap; above it, stream-copy to temp under share-delete.

**N12. Removing a chip is not the counterfactual.** [UX, IR · m]
P1 includes timeline hits, so removing the time chip changes the pool, while the counterfactual keeps the pool fixed. Label chip removal as a "re-search" and test the two separately.

**N13. Deleting a mapped segment fails on Windows.** [Arch, Perf · m] The UI's memmaps stay open until garbage collection, so compaction must wait for the UI to acknowledge it has released them. Also, 500-file commits create about 250 segments; use one growing tail segment.

**N14. The VM plan.** [Pkg · m] VirtualBox 7.2.22 is confirmed [5]. The Enterprise evaluation needs a Microsoft-account sign-in and shuts down hourly after 90 days, and a snapshot does not reset that clock [4]. VM timings under VBS must not become Perf numbers.

**N15. Disk estimates.** [Pkg · m] Pkg's 60–90 MB per 10⁴ files assumes int8 vectors, which no role ships.

**N16. BRIEF deviations.** [All · m] The GPU, the GPU toggle and model choice are all deferred or cut. Record that as approved. The first real GPU workload must still be flagged to the user.

**N17. Freezing spaCy is still a single point of failure.** [Pkg, IR · **M**] IR defers the NER decision to P8, but the freeze risk lands in P4 week 1. Spike it first. If it fails, drop NER from the app.

## 3. Decisions the facilitator must make

| # | Decision | Positions | Recommendation |
|---|---|---|---|
| D1 | Event semantics of `file_time` | single interval (IR, Arch) vs per event (skeptic) | **Per event** (N3); this is the paper. |
| D2 | Scale promise on 8 GB | whole drive (BRIEF) vs ≤10⁵ claims (Perf) | **Document-level first stage + chunk re-score**, decided before P3 freezes the baselines. |
| D3 | Default embedder | granite if the gate passes (IR) | **MiniLM int8 default for MVP**: it matches v1, halves index time, and multilinguality is not a paper claim. Granite is an evaluation row. |
| D4 | Incremental sessions | dirty-range sweep (Perf MVP, first to cut) vs block memoisation (Arch) | Recompute the block partition fully (a sort), then re-cluster only blocks whose hash changed. Drop dirty-range logic. |
| D5 | Content size cap | 64 / 100 MB / none | 100 MB in memory; above that, a temp stream copy. |
| D6 | Near-duplicate edges | MVP (Arch) vs P1 input (IR) | Cut from MVP. |
| D7 | Resumable scans | cut third (Arch) vs "resume next launch" (UX) | Keep; per-batch commits already provide it. |
| D8 | NER in the app | keep until ablation in P8 (IR) | Decided by the week-1 freeze spike. |
| D9 | GPU and model choice | everyone LATER/CUT | Accept, recorded as a deviation. |
| D10 | Which G | 240 (config) / 72 (code) | Fit on dev; report forced cuts. |

## 4. Minimum viable scope for v2-P4

1. **Indexer child.** Writer lock, Job Object, per-batch commits, resume. Placeholders metadata-only. Share-delete opens. `\\?\` walker. No junction descent. Offline roots.
2. **SQLite catalog.** `time_evidence` (zone-aware) and estimator v0 per event. FTS5 BM25 with a name/path trigram table.
3. **Exact f32 vectors.** One pinned ORT CPU wheel, MiniLM int8 default, BLAS and ORT threads pinned.
4. **Queries.** Union P0, a linear scorer (≤12 features, dev-fitted), regex and relational time plus Settings periods.
5. **Sessions.** Exact per gap block, capped, on `last_user_edit`.
6. **GUI.** First run, search never waits, progress and Cancel, rows with the Δrank-gated why-line, the dates panel, × on chips, the Issues list, Research mode.
7. **Packaging.** PyInstaller one-dir, Inno per-user, offline `selftest`, unsigned with SHA-256.

**Out:** SymSpell, P1 expansion and the counterfactual UI (each added only if the schedule allows), duplicates, DirectML, the date strip, chip editing, high contrast, a study larger than 5 people.

## 5. Tests required before any paper number

1. **Generator audit (N1).** On a zero-damage persona: the copy signature fires on 0%, estimator error is 0, and in-file times equal the simulated times.
2. **Leakage.** An AST check that the estimator does not import `datagen`. `file_time` is byte-identical with manifests deleted.
3. **Freezes.** T is frozen with a hash before the estimator rules are written. Split lock: `fit()` rejects test seeds. A test-run counter is logged with the SHA and config hash. The pre-registered primary metric is committed.
4. **Ground-truth independence (N2).** No truth label is derivable from any `time_evidence` row.
5. **Parity.** GUI vs CLI vs frozen `eval`, under two different BLAS/ORT thread environments. Ranked paths, `signals_used`, `doc_ref`s and cᵢ must be equal.
6. **Export parity.** ONNX vs sentence-transformers reference: cosine ≥ 0.9999 on 500 texts, `1_Pooling` reproduced.
7. **Pool tests.**
   - Each channel ranked inside P0 equals its unpooled top-200 *documents*.
   - Pool recall is reported per query kind.
   - Offline re-scoring equals online.
8. **Explanations.** Claim verification is 100%. Σcᵢ = s(x) − s(a). Every why-line reason has Δrank ≥ 1, or is labelled as a gate or a negative.
9. **Regression anchor.** The new engine configured as v1 reproduces `dense_flat` 0.503 on the smoke fixture, or the difference is explained.
10. **Incremental == clean** on scripted edit sequences.
11. **Cross-CPU check (N7).** Top-10 overlap between the dev machine and an AVX2 laptop.

## Sources

1. https://pypi.org/pypi/onnxruntime/1.31.1/json
2. https://pypi.org/project/symspellpy/
3. https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html
4. https://www.microsoft.com/evalcenter/evaluate-windows-11-enterprise
5. https://www.virtualbox.org/wiki/Downloads

Local: `writers.py:43`, `contextfs.toml:220`, `verify_corpus.py`, M1, numpy probe.
