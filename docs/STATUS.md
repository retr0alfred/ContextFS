# ContextFS v2 — status

Last updated: 2026-10-28 (end of v2-P3) · Branch: `v2`

**▶ MILESTONE M1 (2026-10-27): architecture frozen; app scope reduced (Decision 95): portable app, no installer; clean-VM testing is a 1-day stretch goal; signing off the critical path.**

| v2 phase | log.md | State | Notes |
|---|---|---|---|
| Bootstrap | Phase 29 | **done** | Portable Py 3.12.16 + uv in `.tools/`, torch 2.14.1+cu130 (dev only), models in `models/hf`, `start.bat` + `scripts/dev_bootstrap.py` |
| P1 Audit | Phase 29 | **done** | `docs/AUDIT_AND_ROADMAP.md` (local only). Directions: Decision 92 |
| P2 Architecture debate | Phase 30 | **done** | `docs/adr/` 0001–0016 + debate record + fresh review + response. Decision 94 |
| P3 Evaluation foundation | Phase 31 | **done → STOP** | Benchmark (44 personas, LLM queries), test split FROZEN (`split_v1.json` ed76950b…), harness + all baselines + ablations + faithfulness. Dev: CE-rerank 0.576 nDCG@10 > v1 0.454; v1 best MRR/Hit@1; time cues unsolved by all (direction A headroom) |
| P4 Build the app | Phase 32 | not started | Engine refactor, fsio, indexer child, GUI MVP, **portable** build, GPU spike; light clean-account check |
| P5 Hardening | Phase 33 | not started | must pass on the packaged portable `.exe` |
| P6 Novelty features | Phase 34 | not started | A primary (probabilistic estimator); B only if time allows |
| P7 Final evaluation | Phase 35 | not started | 3 pre-registered primaries (ADR-0015 §7) |
| P8 Optimise | Phase 36 | not started | tolerance needs user sign-off first |
| P9 Paper kit | Phase 37 | not started | |

## Open items needing the user
1. Say "continue" to start v2-P4 (build the app).
2. SignPath: submit after the first CI-built release (P4). Needs a LICENSE file (your and your co-author's call).
3. Smart App Control is in Evaluation mode: an open risk, checked before every demo build.
4. Optional: donor statistics from another source; user-only transfer-table rows; real-user study recruitment (needs ethics approval; protocol in `docs/study/`).

## Standing reminders
- Paper-strategy docs stay local (public repo).
- Every number before Phase 29 is unverified; smoke-fixture numbers are never paper numbers.
