# ContextFS v2 — status

Last updated: 2026-10-29 (end of v2-P7) · Branch: `v2`

**▶ MILESTONE M1 (2026-10-27): architecture frozen; app scope reduced (Decision 95): portable app, no installer; clean-VM testing is a 1-day stretch goal; signing off the critical path.**

| v2 phase | log.md | State | Notes |
|---|---|---|---|
| Bootstrap | Phase 29 | **done** | Portable Py 3.12.16 + uv in `.tools/`, torch 2.14.1+cu130 (dev only), models in `models/hf`, `start.bat` + `scripts/dev_bootstrap.py` |
| P1 Audit | Phase 29 | **done** | `docs/AUDIT_AND_ROADMAP.md` (local only). Directions: Decision 92 |
| P2 Architecture debate | Phase 30 | **done** | `docs/adr/` 0001–0016 + debate record + fresh review + response. Decision 94 |
| P3 Evaluation foundation | Phase 31 | **done** | Benchmark (44 personas, LLM queries), test split FROZEN (`split_v1.json` ed76950b…), harness + all baselines + ablations + faithfulness. Dev: CE-rerank 0.576 nDCG@10 > v1 0.454; v1 best MRR/Hit@1; time cues unsolved by all (direction A headroom) |
| P4 Build the app | Phase 32 | **done** | Engine (3 event times per file, pluggable estimators), GUI + indexer child, CLI, parity tests, portable `dist/ContextFS` (330 MB; frozen self test ALL PASS, no dev-env leakage). GPU gates passed → DirectML wheel, discrete adapter only (Decisions 97–100). Dev (in-sample): rules_v0 0.679 nDCG@10; temporal-cue MRR 0.263 / Hit@1 0.135, **oracle times only 0.273 / 0.135** |
| P5 Hardening | Phase 33 | **done** | 22/22 hardening cases pass on the packaged exe (`b8ae539c`; read-only, crashes/hangs/kills, damaged index, disk floor, GUI never stalls > 250 ms, 10k files: search p95 66 ms, no-op launch update 1.6 s). Real-data demo bugs + independent QA (4 High, 6 Medium) fixed with regression tests; 545 tests pass. Decisions 101–104 |
| P6 Novelty features | Phase 34 | **done** | Direction A: `recon_v1` reconciled per-event time (widen-only calibration, dev only), app default (Decision 107). Dev P-A1 content-copy: arrival coverage 0.86 at ~2.3 y median width (rules_v0 0.06), last-edit within-1d 0.74 vs 0.04, false edits 524/0 vs 1,354/1,623; most coverage gain is bulk-copy detection. P-A2 +0.012 vs raw (n.s.), ≈ rules_v0; P-A2s 49% vs 39% of oracle headroom (not distinguishable). T v1.1 MotW fix applied to dev+test (Decision 106). 22/22 hardening on the final build. B not started |
| P7 Final evaluation | Phase 35 | **done → STOP** | ONE test run (config `3934cb68…`, run cap 1, finished; inputs staged on C:, Decision 109). Holm over 3 primaries (Decision 108): **P-S ContextFS 0.672 vs CE rerank 0.562 nDCG@10 (+0.110, Holm p 0.00015)**; P-A1 arrival interval score +267 d, p 0.088 (Holm 0.177, n.s.); P-A2 time-cued +0.008, p 0.163 (Holm 0.177, n.s.). Content-copy arrival coverage 0.88 (rules_v0 0.07, rules+bulk 0.98); false edits 644/0 vs 1,579/1,715. Fidelity replay 162/162. README rewritten with test numbers only |
| P8 Optimise | Phase 36 | not started | tolerance needs user sign-off first |
| P9 Paper kit | Phase 37 | not started | |

## Open items needing the user
1. Say "continue" to start v2-P8 (optimise/slim). It needs a tolerance you sign off first (which numbers may move, by how much); any change outside it is reverted.
2. **Decide the home of the 8 GB memory-cap risk** (Decision 105): indexer peak 1.5 GB at 10k files (GPU), at the 1.5 GB job limit for ≤ 8 GB machines.
3. `C:\ContextFS-scratch\p7stage` (~4.5 GB staged copy) and `C:\ContextFS-scratch\p7` (test-run indexes) are disposable; kept so the run can be re-verified. Say when to delete them.
4. **Standing item (no phase):** clean-account check (Part B of `docs/release/CLEAN_ACCOUNT_CHECKLIST.md`) — revisit before any release or demo on a machine other than yours (Decision 105). Before any release: a third-party notices file.
5. Smart App Control is in Evaluation mode on this laptop; the build is unsigned. Checked by every self test.
6. Optional: real-user study (needs ethics approval; protocol in `docs/study/`) — the only route to external validity for direction A.

## Machine notes
- D: (the project drive) is a bus-powered USB HDD that resets under sustained small-file writes. Test scratch lives in `C:\ContextFS-scratch` (disposable; user-approved). See log.md Decision 104.
- Open risk (home not yet decided, Decision 105): indexer peak memory 1.5 GB at 10k files with the GPU (1.1 GB on CPU), at the 1.5 GB job limit for ≤ 8 GB machines; a limit kill would count as a crash strike.

- Run `dist\ContextFS\ContextFS.exe` from internal storage if possible: from the USB disk the window can freeze 1–3 s after the disk idles (log.md Phase 34).

## Standing reminders
- Paper-strategy docs stay local (public repo).
- Every number before Phase 29 is unverified; smoke-fixture numbers are never paper numbers.
- Headline for direction A (test): recon_v1 makes dates truthful after copies, but neither A primary is significant; even true file times leave temporal-cue Hit@1 at 0.118 on test.
