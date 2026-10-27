# ADR-0010: One engine library; GUI, CLI and evaluation get identical results (tested)

- **Status:** Accepted · **Date:** 2026-10-27 · **Phase:** v2-P2
- **Debate record:** architect R1/R2 §1.2, IR R2 §5, packaging R2, skeptic R1 #10, R2 N6, N7

## Context
- In v1, indexing orchestration lives in `cli/main.py`, and the GUI calls the CLI's private helpers.
- Parity was a manual check.
- The user requires identical results across GUI, CLI and evaluation, proven by a test.

## Decision
1. **`contextfs.engine`** is the only public surface. It never imports Qt, Typer or `datagen`.
   ```python
   Engine.open(index_dir, mode="read" | "write")
   engine.search(query, config: SearchConfig, as_of, trace=False) -> SearchResponse
   engine.first_stage(query, config, as_of, unpooled=False) -> CandidatePool
   engine.update(roots=None, progress=None, cancel=None) -> UpdateReport
   engine.recompute_aggregates(stages=None)      # clean rebuild of global stages
   engine.status(); engine.explain(...); engine.time_evidence(file_id)
   ```
   - `SearchConfig` is frozen and hashed. It carries overrides, signal masks, feedback (off) and
     the scorer hash.
   - `as_of` is injected, so nothing reads `date.today()`.
   - Responses carry `generation`, `config_hash`, `embedder_fp` and `signals_used`.
2. **Determinism:**
   - `Engine.open` pins ORT and BLAS threads;
   - ranking sorts on scores rounded to 1e-5, then by path (skeptic N6);
   - query batch = 1;
   - feedback is off in evaluation.
3. **Paper numbers come from the frozen build:** `contextfs.exe eval --final`, which uses the shipped
   runtime, model and index type (ADR-0003). The venv-vs-frozen parity test runs in CI.
4. **Parity test:**
   - 50 queries run through the GUI `Engine`, `contextfs search --json` and the evaluation harness,
     against one index;
   - **under two different BLAS/ORT thread environments**;
   - must give equal ranked paths, `signals_used`, `doc_ref`s and contributions, with scores within
     1e-6.
   - Lexical-only answers given before the model loads are labelled and excluded.
5. **Tests own their data.** No test reads a developer's live index (the v1 `test_activity.py`
   defect). Fixtures are built in `tmp_path`.

## Consequences
The CLI and GUI become thin. `cli/main.py`'s pipeline code moves into `engine/`.

## How we will know it was wrong
The parity test fails under either thread environment, or the cross-CPU top-10 overlap (ADR-0003)
is below 95%.
