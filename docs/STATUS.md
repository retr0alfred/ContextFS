# ContextFS v2 — status

Last updated: 2026-10-29 (end of v2-P5) · Branch: `v2`

**▶ MILESTONE M1 (2026-10-27): architecture frozen; app scope reduced (Decision 95): portable app, no installer; clean-VM testing is a 1-day stretch goal; signing off the critical path.**

| v2 phase | log.md | State | Notes |
|---|---|---|---|
| Bootstrap | Phase 29 | **done** | Portable Py 3.12.16 + uv in `.tools/`, torch 2.14.1+cu130 (dev only), models in `models/hf`, `start.bat` + `scripts/dev_bootstrap.py` |
| P1 Audit | Phase 29 | **done** | `docs/AUDIT_AND_ROADMAP.md` (local only). Directions: Decision 92 |
| P2 Architecture debate | Phase 30 | **done** | `docs/adr/` 0001–0016 + debate record + fresh review + response. Decision 94 |
| P3 Evaluation foundation | Phase 31 | **done** | Benchmark (44 personas, LLM queries), test split FROZEN (`split_v1.json` ed76950b…), harness + all baselines + ablations + faithfulness. Dev: CE-rerank 0.576 nDCG@10 > v1 0.454; v1 best MRR/Hit@1; time cues unsolved by all (direction A headroom) |
| P4 Build the app | Phase 32 | **done** | Engine (3 event times per file, pluggable estimators), GUI + indexer child, CLI, parity tests, portable `dist/ContextFS` (330 MB; frozen self test ALL PASS, no dev-env leakage). GPU gates passed → DirectML wheel, discrete adapter only (Decisions 97–100). Dev (in-sample): rules_v0 0.679 nDCG@10; temporal-cue MRR 0.263 / Hit@1 0.135, **oracle times only 0.273 / 0.135** |
| P5 Hardening | Phase 33 | **done → STOP** | 22/22 hardening cases pass on the packaged exe (`b8ae539c`; read-only, crashes/hangs/kills, damaged index, disk floor, GUI never stalls > 250 ms, 10k files: search p95 66 ms, no-op launch update 1.6 s). Real-data demo bugs + independent QA (4 High, 6 Medium) fixed with regression tests; 545 tests pass. Decisions 101–104 |
| P6 Novelty features | Phase 34 | not started | A primary (probabilistic estimator); B only if time allows |
| P7 Final evaluation | Phase 35 | not started | 3 pre-registered primaries (ADR-0015 §7) |
| P8 Optimise | Phase 36 | not started | tolerance needs user sign-off first |
| P9 Paper kit | Phase 37 | not started | |

## Open items needing the user
1. Say "continue" to start v2-P6 (novelty: direction A, the probabilistic/reconciled time estimator).
2. **LICENSE** — first build exists now; `pyproject.toml` says MIT but there is no LICENSE file (your and your co-author's call). Needed before SignPath and any release.
3. Clean-account check (Part B of `docs/release/CLEAN_ACCOUNT_CHECKLIST.md`) — deferred by you; Part A demo done.
4. Smart App Control is in Evaluation mode on this laptop; the build is unsigned. Checked by every self test.
5. Optional: donor statistics from another source; user-only transfer-table rows; real-user study recruitment (needs ethics approval; protocol in `docs/study/`).

## Machine notes
- D: (the project drive) is a bus-powered USB HDD that resets under sustained small-file writes. Test scratch lives in `C:\ContextFS-scratch` (disposable; user-approved). See log.md Decision 104.
- Open P8 risk: indexer peak memory 1.5 GB at 10k files with the GPU (1.1 GB on CPU) — at the 1.5 GB job limit for ≤ 8 GB machines.

## Standing reminders
- Paper-strategy docs stay local (public repo).
- Every number before Phase 29 is unverified; smoke-fixture numbers are never paper numbers.
- Headline for direction A: time cues defeat every system; even true file times leave temporal-cue Hit@1 at 0.135 on dev.
