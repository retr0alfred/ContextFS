# ADR-0014: v2 scope, cut order, and the tests required before any paper number

- **Status:** Accepted · **Date:** 2026-10-27 · **Phase:** v2-P2
- **Debate record:** every role's R2 scope table; skeptic R2 §4–5

## MVP (v2-P3 harness + v2-P4 app)
- **Indexing and safety:**
  - indexer child (Job Object, writer lock, per-batch commits, resume);
  - `fsio` walker and reads (ADR-0011);
  - SQLite + exact f32 vectors (ADR-0005).
- **Time and sessions:**
  - time-evidence capture and per-event estimator v0 (ADR-0006);
  - T frozen first;
  - generator fixed and zero-damage test passing;
  - gap-block sessions (ADR-0007).
- **Retrieval:**
  - FTS5 + exact dense, union P0, linear dev-fitted scorer (ADR-0008);
  - regex + relational time + Settings periods;
  - MiniLM int8 ONNX.
- **Explanations:** signed contributions, `doc_ref`, claim verification (ADR-0009).
- **Engine:** API, determinism, parity test (ADR-0010).
- **GUI:** first run, never-wait search, progress/cancel, rows, dates panel, chips with ×, Issues,
  Research mode (ADR-0013).
- **Packaging:** PyInstaller one-dir, Inno per-user, offline `selftest`, unsigned with SHA-256,
  VirtualBox + old-laptop tests (ADR-0004).
- **GPU spike:** DirectML gates (ADR-0003); ship CPU-only if they fail, as a deviation the user
  approves.

## LATER
| Phase | Items |
|---|---|
| v2-P5 | oplock back-off; volume-GUID roots; "Move index" |
| v2-P6 | probabilistic estimator (A); event anchors and festival time (B, only if A is solid) |
| v2-P7 | local-LLM reranker baseline (optional) |
| v2-P8 | ANN, sparse session linkage, watcher, NER cut by ablation, granite as default if it passes |
| Later | date strip, editable chips, high contrast, 3D CLI export, near-duplicates / version lineage, SignPath |

## CUT
LambdaMART; gte reranker; GLiNER; any shipped LLM; LanceDB; NetworkX/JSON graph; semantic tree;
USN journal; CUDA/`onnxruntime-gpu` app build; auto-update; MSIX; feedback re-rank in the UI;
Insights tab.

## Cut order if four or more weeks behind
1. SymSpell
2. P1 expansion
3. counterfactual UI (keep the metric)
4. DirectML spike (ship CPU, with user approval)
5. dirty-block incremental sessions (fall back to a full block rebuild)
6. multi-root (one root)

**Never cut:**
- time-evidence capture and estimator v0 with E1–E5;
- exact search;
- the parity test;
- the test-split lock;
- claim verification.

## Tests required before any paper number (skeptic R2 §5)
1. **Generator zero-damage audit:** copy signature on 0% of files, estimator error 0.
2. **Leakage:** the estimator does not import `datagen`, and `file_time` is unchanged after
   manifests are deleted.
3. **Freezes:** T hash before the rules; split lock (`fit()` rejects test seeds); a logged test-run
   counter; pre-registered primary metric.
4. **Ground-truth independence:** no truth label is derivable from `time_evidence`.
5. **Parity:** GUI = CLI = frozen eval, under two thread environments.
6. **Export parity:** ONNX vs sentence-transformers cosine ≥ 0.9999.
7. **Pool tests:** each channel inside P0 equals its unpooled top-200 documents; pool recall per
   kind; offline re-score = online.
8. **Explanations:** claim verification 100%; Σcᵢ identity; Δrank gate.
9. **Regression anchor:** v1 configuration → `dense_flat` 0.503 on the smoke fixture (or explained).
10. **Incremental == clean** on scripted edit sequences.
11. **Cross-CPU top-10 overlap** with an AVX2 laptop.

## Revision 1 — after the fresh review (2026-10-27): the plan, and a smaller MVP
Supersedes "MVP" and "Cut order". Source: `review/fresh_review.md` M11, B2, m5.

**The paper path is decoupled from the app and runs first.**

| Weeks from v2-P3 start | Paper path | App path |
|---|---|---|
| 1–2 | T measured and frozen; donor histograms; generator fix + zero-damage test | — |
| 2–4 | Generator personas, queries (LLM, user-approved GPU run), dev/test freeze, power calc | Engine refactor (ADR-0010), fsio walker (ADR-0011) |
| 4–6 | Harness, baselines (BM25 tuned, dense MaxP, fusion, CE rerank), estimator v0 | Indexer child, storage, time capture |
| 6–8 | Dev tuning, E1–E5 on dev, study tooling | GUI MVP, packaging spike, GPU spike, signing |
| 8–10 | (v2-P5 hardening on the packaged build) | |
| 10–12 | v2-P6 contribution A on dev → v2-P7 single test run | |

**MVP cut order if behind** (first to go):
1. vector compaction (keep appending; compact LATER)
2. SymSpell
3. relational time operators and Settings periods
4. P1 expansion
5. counterfactual UI (keep the metric)
6. DirectML spike (ship CPU-only, with user approval)
7. dirty-block incremental sessions (rebuild blocks)
8. multi-root

**Never cut:**
- time-evidence capture and estimator v0 with E1–E5;
- exact search;
- the parity tests;
- the split lock;
- claim verification;
- signing (or the approved SAC deviation).

**Test 4 reworded (B2):** "no sealed truth value flows into `time_evidence`", enforced by a
provenance taint test. Sealed truth files are opened only by the scorer, never by the engine.

Additional tests:
- the logical-index-hash parity test (ADR-0010 Rev. 1);
- the on-device GPU probe (ADR-0003 Rev. 1);
- simulator-fidelity replay (ADR-0015 §4);
- the licence gate (ADR-0016).

## Revision 2 — user decision before Phase 3 (2026-10-27, log.md Decision 95)
Supersedes conflicting items in the MVP list and Revision 1.

**Removed from the MVP:**
- the per-user installer (ADR-0004 Rev. 2);
- VirtualBox and clean-VM testing (now a stretch goal, time-boxed to 1 day, after P7);
- signing as a blocker.

**Never cut:** "signing (or the approved SAC deviation)" is replaced by "the SAC state is checked
before every demo build".

**The plan shrinks on the app path**, freeing about 1 week for the research path (benchmark,
direction A, evaluation).
