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
