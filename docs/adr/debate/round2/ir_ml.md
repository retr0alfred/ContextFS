# Round 2 · IR/ML Researcher

**Local measurements, 2026-10-27, `.venv` Python with `-I`:**
- FTS5 trigram (SQLite 3.53.1): `MATCH 'gradeint'` returns **0 rows** (V9 confirmed).
- OSA distance from "gradeint" to "gradient" is **1**.
- Exact numpy top-200 over 384-d f32 vectors: **10.5 ms** at 1.25×10⁵ (192 MB) and **80 ms** at 10⁶ (1.5 GB). Upcasting from f16 took 107 / 942 ms. This agrees with Perf's Round 2 figures.

## 1. Revised proposal

### 1.1 Retrieval core

**First stage.** FTS5 BM25 over chunks (`unicode61 remove_diacritics 2`) plus a name/path table, with trigram **for substrings only**. Dense is exact f32 brute force over chunks (the Perf/Arch store); a document takes its best chunk's score, and that chunk is the evidence. **Typos:** SymSpell (OSA ≤ 2) over the index's own `fts5vocab` vocabulary, adding OR-terms and keeping the original.

**P0 = union of BM25 top-200 and dense top-200 (≤ 400). No RRF and no cap.**
- Ranking P0 by one channel's own score reproduces that channel's unpooled top-200 exactly, so the single-channel baselines stay true baselines. A test asserts this.
- Baselines are also reported unpooled via `first_stage(unpooled=True)`. This is cheap because both channels are exact.

**Fusion is chosen on dev** from {dense-only, RRF k∈{10, 60}, z-score linear α}. Decision 91's smoke result (RRF 0.428 vs dense 0.503) is why nothing is assumed.

**P1 = P0 ∪ session-mates and duplicates of the top-20 ∪ timeline hits** (≤ +200). It is reported as its own row. Pool recall is reported per query kind for both P0 and P1.

**Scorer.** Linear over ≤ 12 features: BM25, dense, name, format, time overlap (core and band), session, link, and `q_has_time × overlap`. Weights are fitted by coordinate ascent on dev nDCG@10 and stored as hashed JSON. **LambdaMART is cut**: Decision 92 treats fusion as engineering.

### 1.2 Models

- **ORT build.** One pinned `onnxruntime` 1.31.1 wheel with the CPU EP, used by the venv, the frozen build and eval (Pkg R2).
- **Embedder gate, P3 week 1** (needs downloads, so not run now). granite-97m `model_quint8_avx2.onnx` (12 layers, as flagged) vs MiniLM-L6 int8 ONNX, measured as chunks/s on 4 threads over 2,000 real chunks plus dev nDCG@10. **Granite ships only at ≥ 100 chunks/s and nDCG ≥ MiniLM's**; otherwise the claim is "English; multilingual best-effort". Export parity: `1_Pooling`, cosine ≥ 0.9999 on 500 texts.
- **spaCy:** off the query path; index-time NER is cut in P8 (−141 MB) if the `-ner` ablation gains < 0.01 dev nDCG.
- **Rerankers:** none in the app; MiniLM-L6 CE is eval-only; gte cut (`trust_remote_code`, no ONNX, over budget).

### 1.3 Query understanding

NFKC with a 2,000-character cap; SymSpell; an editable format lexicon giving soft P(kind). **Time:** the existing regex resolver plus relational operators ("before/after/around X", "N weeks before X") and **user-defined periods** in Settings ("sem 5 = Jul–Nov 2025"). Corpus-event anchors, festival tables and `dateparser` are **LATER (Decision 92 B)**; V17 is right that they are unverified. Every stage is guarded and degrades rather than failing.

### 1.4 Reconciled-time estimator (mine; Arch owns schema and capture)

**Input:** Arch R2 `time_evidence`. **Output:** `file_time` = `[t_lo, t_hi]`, `t_point`, `method`, confidence ∈ {sure, probably, unknown}, `evidence_used` (rendered by UX's dates panel).

**v0 rules.** Each constant cites a row of the frozen table T.
1. **Convert each row to a UTC interval.** `floating` becomes wall −14 h…+12 h, with the machine's zone as the point. Add `resolution_s` widening. atime is ignored.
2. **In-file times survive file-system transfers** (M1). Sources: EXIF original, PDF dates, `docProps` *modified*, maximum zip entry time. `docProps` *created* is used only if *modified* is missing, because templates inherit it. Discard values before 1990, after `first_seen` + 1 day, or at the DOS epoch.
3. **Copy signatures.** `birth > mtime` = CopyFile-family copy, so **mtime survived** (M1). `birth ≈ mtime` inside a folder burst (≥ N files within W s) = content-only, clone or download copy, so **mtime is suspect**. robocopy is invisible and harmless. `superseded_mtime` with the same hash: the earlier time wins.
4. **Combine.** Trusted mtime consistent with in-file times → `sure`; suspect mtime with an in-file time → `probably`; suspect with neither → `unknown`, interval [earliest bound, burst]. Sessions skip intervals wider than G (as Arch and Perf).

N and W are fitted on dev only. A probabilistic estimator with cohort reasoning is **contribution A (v2-P6)**, and v0 is its baseline.

**Non-circular evaluation protocol.**
- **E1. Freeze T first.** Pkg hand-measures it by extending M1: tool × file-system pair → effect on birth, mtime and each in-file field, plus precision and zone. It is committed with a hash. CI asserts that the first commit touching `estimator/rules*` comes after T's commit.
- **E2. Real tools, not simulated stamps.** The generator damages times only by sampling compositions of T rows, applied by **running the actual tool** on NTFS wherever possible. The real scanner then captures the evidence.
- **E3. Leakage.** An AST check that `estimator` does not import `datagen` or manifest paths. `file_time` must be byte-identical after manifests are deleted. `time_evidence` is the only input.
- **E4. Ground truth the generator did not damage**, never used to design rules: the old-laptop copy (a real transfer with known original times), held-out real files pushed through T's tools by hand, and about 50 participant-dated files (UX).
- **E5. Pre-registered metrics per damage type:** interval coverage at median width, median absolute error in days, and share within 1 day. Compared against raw mtime, raw birth, min(all) and in-file-only; downstream, temporal-query nDCG with the estimator vs raw mtime.

### 1.5 Explanations

- **Contribution.** cᵢ = s(x) − s(x with xᵢ := aᵢ). For a linear scorer this is exactly wᵢ(xᵢ − aᵢ), and s(x) = s(a) + Σcᵢ.
- **Absent values `a` are frozen in the scorer JSON before any test run.** Evidence features are absent at 0. Similarity features (BM25, dense) are absent at the **query-pool median**, so an uncentred dense score cannot dominate "mostly because of".
- **Display:** signed, with negatives as "despite …"; only |cᵢ| ≥ 0.1·max; every reason has a `doc_ref` (a chunk span or an evidence row). Expansion candidates show their pool-entry path, and removing it counts as "drops out". The time chip shows **core + band**.
- **Metrics:** counterfactual rank drop (Comprehensiveness@10), uncited mass, claim verification. Sufficiency is cut.

## 2. Response to the skeptic

| # | Verdict and action |
|---|---|
| V9 | **Accept.** Trigram for substrings; typos via corpus SymSpell. |
| V17 | **Accept.** Moved to LATER. |
| 1 | **Accept.** §1.4. |
| 3 | **Accept.** Floating times become intervals; IST and DST fixtures added in E2. |
| 4 | **Accept.** atime is not used. |
| 7 | **Accept.** Union pool, unpooled baselines, fusion chosen on dev, pool recall per kind. |
| 8 | **Partially accept.** See detail below. |
| 9 | **Accept.** Reference point, signed contributions, gate semantics, core+band chip (§1.5). |
| 10 | **Accept** (§5). |
| 15 | **Accept.** Embedder gate, conditional multilingual claim, English CE eval-only, gte cut. |
| 17 | **Accept** (§6). |

**Attack 8.** LTR is cut (≤ 12 features). Test queries are written **blind** (persona and target descriptions, no template lexicon), and UX's real queries are a second test set. Pre-registered primary metric: blind-query nDCG@10, full vs best baseline. Corpus statistics are per persona. **Rejected:** that template hold-out is worthless; it blocks reuse of exact phrasing, so it stays alongside.

## 3. Ten questions

1. **Reconciler.** I own the estimator, Arch owns capture, and Pkg measures T. Ground truth: the old-laptop copy, the held-out real-transfer set, participant-dated files.
2. **What eval runs.** The shipped artifact, via frozen `contextfs.exe eval`: `onnxruntime` 1.31.1 CPU EP, model pinned by SHA-256, `intra_op=4`/`inter_op=1` sequential, exact f32, feedback off, `as_of` injected.
3. **Topology.** UI and query in one process (CPU encoder, batch = 1), plus one indexer child for all bulk work and any GPU work.
4. **Vector store.** Exact f32 `.npy` (10.5 / 80 ms, above); ~3×10⁵-chunk ceiling on 8 GB; ANN beyond is LATER, as its own row.
5–8. **P0:** §1.1. **Contribution/absent:** §1.5. **Zone-less/2 s times:** Arch's schema, made intervals by rule 1. **Opens:** Arch/Pkg `CreateFileW` with full sharing.
9. **Disk per 10⁴ files (estimate).** About 20k chunks, giving roughly 31 MB of vectors, 50 MB of FTS, and 15 MB of metadata and evidence: about 100 MB. Measured in P7.
10. **Cut order if behind:** SymSpell → P1 expansion → counterfactual UI → granite (use MiniLM). **Never cut:** estimator v0 with E1–E5, the parity test, the test lock.

## 4. Contradictions resolved

| Issue | Resolution |
|---|---|
| ORT `-directml` 1.24.4 vs 1.31.1 | 1.31.1 CPU everywhere; a later DML switch moves app and eval together, and DML-built indexes get their own row. |
| `fs_rewritten` (Arch) vs `time_unknown` (Perf) | Both replaced by estimator interval width > G. |
| Who owns T | Pkg measures it. I own its format, the freeze rule and E1–E5. |
| UX "non-zero contribution" | Replaced by signed, reference-based cᵢ; UX's counterfactual reuses the same re-scoring. |
| Perf int8 gate (−1 nDCG) | Only picks the shipped file; that file is what gets evaluated. |
| spaCy 141 MB | Off the query path; index-time NER decided by ablation. |

## 5. Guaranteeing identical results

**Pinned:** the ORT wheel, CPU EP, model hash, thread counts, query batch = 1, exact search over the **same index files**, the scorer hash, the `SearchConfig` hash (including overrides), `as_of`, feedback off, and ties broken by (score desc, path asc).

**Parity test.** 50 queries through the GUI `Engine`, the frozen `contextfs search --json` and the harness, all against one index. Ranked paths, `signals_used`, `doc_ref`s and cᵢ must be equal, and scores within 1e-6.

**Accepted differences, each tested separately:** two independent builds may differ by batch float noise (test: vector cosine ≥ 0.9999 and identical top-10 on ≥ 99% of queries). Lexical-only results before the model loads are labelled and excluded, and the harness asserts that all signals ran. venv vs frozen build must match exactly (Pkg's CI).

## 6. Scope

| Element | Tag |
|---|---|
| BM25 + exact dense, union P0, unpooled baselines, dev fusion; blind queries, pre-registration, split lock; embedder gate, MiniLM CE baseline | MVP P3 |
| Linear scorer (dev-fitted, ≤ 12 features) | MVP P3/P4 |
| Estimator v0, T freeze, E1–E5 | MVP P3 (protocol), P4 (app) |
| Signed cᵢ, `doc_ref`, claim verification, rank drop; SymSpell; relational time, Settings periods; P1 expansion (first to cut) | MVP P4 |
| Probabilistic estimator (A) | LATER P6 |
| Event anchors, festivals, `dateparser` (B) | LATER P6, if A is solid |
| Prototype format detection, spaCy cut, ANN | LATER P8 |
| LLM reranker baseline | LATER P7, optional |
| LambdaMART, gte, GLiNER, shipped LLM, sufficiency, atime | CUT |

## Sources

- https://www.sqlite.org/fts5.html
- https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2
- https://huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base
- https://pypi.org/project/onnxruntime/
- https://pypi.org/project/symspellpy/
- Local: scratchpad script; M1; log.md D91–92.
