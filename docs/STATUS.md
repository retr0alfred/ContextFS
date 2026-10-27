# ContextFS v2 — status

Last updated: 2026-10-27 (end of v2-P2) · Branch: `v2`

| v2 phase | log.md | State | Notes |
|---|---|---|---|
| Bootstrap | Phase 29 | **done** | Portable Py 3.12.16 + uv in `.tools/`, torch 2.14.1+cu130 (dev only), models in `models/hf`, `start.bat` + `scripts/dev_bootstrap.py` |
| P1 Audit | Phase 29 | **done** | `docs/AUDIT_AND_ROADMAP.md` (local only). Directions: Decision 92 |
| P2 Architecture debate | Phase 30 | **done → STOP** | `docs/adr/` 0001–0016 + debate record + fresh review + response. Decision 94 |
| P3 Evaluation foundation | Phase 31 | next | Paper path first (ADR-0014 Rev. 1 plan): T → donor histograms → generator fix → personas/queries → freeze → harness/baselines |
| P4 Build the app | Phase 32 | not started | Engine refactor, fsio, indexer child, GUI MVP, packaging/GPU/signing spikes |
| P5 Hardening | Phase 33 | not started | must pass on packaged, signed `.exe` |
| P6 Novelty features | Phase 34 | not started | A primary (probabilistic estimator); B only if time allows |
| P7 Final evaluation | Phase 35 | not started | 3 pre-registered primaries (ADR-0015 §7) |
| P8 Optimise | Phase 36 | not started | tolerance needs user sign-off first |
| P9 Paper kit | Phase 37 | not started | |

## Open items needing the user
1. **Code signing** (ADR-0004 Rev. 1): SignPath Foundation / Certum open-source cert / institution cert — or approve a "Smart App Control must be off" deviation.
2. **GPU deviation** (ADR-0003): acknowledge that if DirectML gates fail in P4, the app ships CPU-only.
3. **First real GPU workload** is the P3 local-LLM query generation — will be flagged before it runs (eyes on screen).
4. Downloads for clean-machine tests (VirtualBox, Windows 11 eval ISO) are yours when P4 arrives.

## Standing reminders
- Paper-strategy docs stay local (public repo).
- Every number before Phase 29 is unverified; smoke-fixture numbers are never paper numbers.
