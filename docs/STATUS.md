# ContextFS v2 — status

Last updated: 2026-10-27 (v2-P3 in progress) · Branch: `v2`

**▶ MILESTONE M1 (2026-10-27): architecture frozen; app scope reduced (Decision 95): portable app, no installer; clean-VM testing is a 1-day stretch goal; signing off the critical path.**

| v2 phase | log.md | State | Notes |
|---|---|---|---|
| Bootstrap | Phase 29 | **done** | Portable Py 3.12.16 + uv in `.tools/`, torch 2.14.1+cu130 (dev only), models in `models/hf`, `start.bat` + `scripts/dev_bootstrap.py` |
| P1 Audit | Phase 29 | **done** | `docs/AUDIT_AND_ROADMAP.md` (local only). Directions: Decision 92 |
| P2 Architecture debate | Phase 30 | **done** | `docs/adr/` 0001–0016 + debate record + fresh review + response. Decision 94 |
| P3 Evaluation foundation | Phase 31 | **in progress** | Paper path first (ADR-0014 Rev. 1 plan): T → donor histograms → generator fix → personas/queries → freeze → harness/baselines |
| P4 Build the app | Phase 32 | not started | Engine refactor, fsio, indexer child, GUI MVP, **portable** build, GPU spike; light clean-account check |
| P5 Hardening | Phase 33 | not started | must pass on the packaged portable `.exe` |
| P6 Novelty features | Phase 34 | not started | A primary (probabilistic estimator); B only if time allows |
| P7 Final evaluation | Phase 35 | not started | 3 pre-registered primaries (ADR-0015 §7) |
| P8 Optimise | Phase 36 | not started | tolerance needs user sign-off first |
| P9 Paper kit | Phase 37 | not started | |

## Open items needing the user
1. **SignPath:** submit `docs/release/signpath_application.md` yourself after the first CI-built release (P4). Blocked now by reCAPTCHA and eligibility. Prerequisites: a LICENSE file (MIT is declared but no file exists; your and your co-author's call), a code-signing policy page, GitHub 2FA.
2. **Smart App Control is in Evaluation mode on this laptop**: it may switch to On by itself and block an unsigned build. This is an open risk; the SAC state is checked before every demo build.
3. **First real GPU workload** is the P3 local-LLM query generation. It will be flagged before it runs (eyes on screen).
4. Light clean-account check (P4): you create a second standard account or use the old laptop, then launch the copied folder.

## Standing reminders
- Paper-strategy docs stay local (public repo).
- Every number before Phase 29 is unverified; smoke-fixture numbers are never paper numbers.
