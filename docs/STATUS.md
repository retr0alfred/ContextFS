# ContextFS v2 — status

Last updated: 2026-10-27 (v2-P2 in progress) · Branch: `v2`

| v2 phase | log.md | State | Notes |
|---|---|---|---|
| Bootstrap | Phase 29 | **done** | Portable Py 3.12.16 + uv in `.tools/`, torch 2.14.1+cu130, models in `models/hf`, `start.bat` + `scripts/dev_bootstrap.py`. From-zero run and failure paths not yet exercised |
| P1 Audit | Phase 29 | **done** | `docs/AUDIT_AND_ROADMAP.md` (local only, git-ignored). Directions chosen: Decision 92 |
| P2 Architecture debate | Phase 30 | **in progress** | ADRs in `docs/adr/` |
| P3 Evaluation foundation | Phase 31 | not started | = direction D. Test split frozen once created; 40-file corpus → smoke fixture only |
| P4 Build the app | Phase 32 | not started | |
| P5 Hardening | Phase 33 | not started | must pass on packaged `.exe` |
| P6 Novelty features | Phase 34 | unblocked | A (temporal provenance) primary; B only if time allows after A |
| P7 Final evaluation | Phase 35 | not started | |
| P8 Optimise | Phase 36 | not started | tolerance needs user sign-off first |
| P9 Paper kit | Phase 37 | not started | |

## Key facts (smoke fixture only — never paper numbers)
- Tests: 477/477 isolated; 6 fail if run concurrently with a scan (tests read live index — open defect).
- `dense_flat` 0.503 / `dense_format` 0.518 / full 0.632 MRR; full vs `dense_format` p = 0.094.

## Standing reminders
- **Flag the first real GPU workload** (model load / embeddings / training) so the user can verify the device live.
- Paper-strategy docs stay local (public repo).
