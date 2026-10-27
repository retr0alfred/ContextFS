# ADR-0015: The benchmark (direction D), the splits, and the real-user study

- **Status:** Accepted · **Date:** 2026-10-27 · **Phase:** v2-P2 (written after the fresh review, B1, M10, M11, §3.4–3.5)
- **Debate record:** IR R2 §1.4, §2 (#8); skeptic R2 N1, N2, §5; `review/fresh_review.md` B1, B2, M10, M11

## Context
Six decisions are "fitted on dev":
- G (ADR-0007);
- burst parameters (ADR-0006);
- fusion choice;
- scorer weights;
- BM25 k1/b (ADR-0008 Rev. 1).

Nothing defined dev. The same author writes both the generator's model of *undamaged* time behaviour
and the estimator rules that detect deviations from it. That is circular unless the undamaged model
is calibrated externally and frozen first.

## Decision
1. **Unit of generation:** a *persona* (student, working professional, researcher, …).
   - Each persona has a seeded life timeline: projects, deadlines, meetings, exams, trips.
   - From it the generator produces files in mixed formats with realistic names and folders, plus
     noise: duplicates, `report (1).docx`, downloads clutter, irrelevant files, scanned PDFs.
   - Scale is 10²–10⁴+ files per persona.
2. **Splits:** by **persona and template family**. Content templates, folder schemes and query
   phrasings used in test personas are disjoint from dev.
   - **The test split is frozen** (a hash in git) when it is first generated. `fit()` rejects test
     seeds, and test runs are counted and logged (cap: 3, all reported).
   - The 40-file v1 corpus moves to `tests/fixtures/smoke/` and is never a paper source.
3. **The undamaged time model is calibrated externally and frozen before any estimator rule
   exists** (fixes B1 circularity).
   - Inputs are aggregate statistics from **donor folders**: distributions of birth−mtime,
     in-file vs file-system time offsets, folder burst sizes, by format.
   - They are collected by a local script that outputs **only histograms**, never paths, names or
     content. Donors are the student, the co-author and volunteers; consent is per the study
     protocol below.
   - The histograms are committed with a hash. CI enforces the order: T hash, then the donor-
     statistics hash, then the first estimator rule.
4. **Damage** (fixes M11, keeping skeptic E2):
   - T is measured by hand with real tools (ADR-0006 E1).
   - The generator applies T's measured effects through `SetFileTime` and in-file metadata edits on
     its **own** files (a simulator).
   - A **5% sample of every damage type is replayed through the real tools** (Explorer copy,
     robocopy, 7-Zip, a USB FAT/exFAT round trip, `git clone`; OneDrive only by hand) to validate
     simulator fidelity. Fidelity is reported.
   - Damage is applied scan → damage → scan, so scan-history evidence is realistic (fixes M2).
5. **Relevance:** graded 0–3 from the simulation (target = 3; same-session or same-project
   companions = 2 or 1; else 0). This is done per query **before** any system runs.
6. **Queries** (fixes M10):
   - per persona, across cue types (temporal, activity, entity, semantic, format, mixed, vague);
   - including paraphrases, typos and messy phrasing.
   - The query-writing LLM sees only a **degraded memory view** of the target: coarse time ("around
     mid-March", "before the exam"), a paraphrased topic, the kind of file, and **no filename,
     exact date or rare terms**.
   - Query-to-target lexical overlap is reported against the participant queries (§9). A large gap
     flags leakage.
   - The LLM is a local model of a family not used anywhere in the retriever. Its name, version and
     prompt are logged.
   - **This is the project's first real GPU workload, and the user must be asked first.**
7. **Pre-registered primaries** (fixes B2), committed before the first test run:
   - **P-A1 (intrinsic, contribution A):** E5 metrics per damage type (ADR-0006).
   - **P-A2 (extrinsic, contribution A):** nDCG@10 on time-cued test queries, with everything else
     frozen, for `file_time` vs raw mtime vs oracle simulation time. Includes a **parser + raw-mtime
     overlap** baseline, so query-parser gains are not credited to A.
   - **P-S (system):** blind-query nDCG@10, full ContextFS vs the strongest baseline (BM25+dense+CE
     rerank), split by memory-cue vs content queries.
   - Holm correction across the primaries. Paired randomisation tests and bootstrap CIs.
8. **Power:** before generating the test split, estimate the per-query variance on dev, then size
   the test set for a detectable ΔnDCG@10 of 0.03 at α = 0.05 and power 0.8. Report the computation.
9. **Real-user study** (protocol and tooling in v2-P3; the user runs it):
   - 8–12 participants, consent form, and the institution's ethics approval where required;
   - participants write memory queries about their own folders **before** indexing;
   - everything runs on their own machine;
   - collected: queries, graded judgements of the shown results, timings, and the time-evidence
     histograms of §3; **never** file content, names or paths;
   - an on-device review screen shows exactly what will be exported before anything leaves;
   - study data is **never used for fitting**.
10. **Generator ↔ engine contract** (review §3.4): the generator never calls the engine or any
    baseline to choose targets, distractors or queries; an import-lint check enforces this. The
    benchmark is released as **generator + seeds**, not files, because a zip destroys birth times.

## Consequences
The paper path (generator, E1–E5, harness, baselines) is decoupled from the app and can run first
(see the plan in ADR-0014 Rev. 1).

## How we will know it was wrong
- Simulator fidelity on the 5% replay sample is below 95% agreement per field.
- Participant queries differ sharply in lexical overlap from generated ones.
- The power computation demands an infeasible test size.
