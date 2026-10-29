# ContextFS v2 — status

Last updated: 2026-10-29 (end of v2-P8; v2-P9 kit exported) · Branch: `v2`

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
| P7 Final evaluation | Phase 35 | **done** | ONE test run (config `3934cb68…`, run cap 1, finished; inputs staged on C:, Decision 109). Holm over 3 primaries (Decision 108): **P-S ContextFS 0.672 vs CE rerank 0.562 nDCG@10 (+0.110, Holm p 0.00015)**; P-A1 arrival interval score +267 d, p 0.088 (Holm 0.177, n.s.); P-A2 time-cued +0.008, p 0.163 (Holm 0.177, n.s.). Content-copy arrival coverage 0.88 (rules_v0 0.07, rules+bulk 0.98); false edits 644/0 vs 1,579/1,715. Fidelity replay 162/162. README rewritten with test numbers only |
| P8 Optimise | Phase 36 | **done** | Zero tolerance (Decision 110): CPU indexing by default, GPU optional (warned on <= 8 GB). Bit-identical to Phase 7 on 20 dev personas / 758 queries (app path and final exe c8c926f5). Memory at 100k files: 1,583 MB before -> session-rebuild fix (Decision 111, output-identical) -> 1,098 MB; worst observed ~1,350 MB < 1,536 MB limit. Window redesigned (monotone light/dark). Hardening 22/22 on c8c926f5 |
| P9 Paper kit | Phase 37 | **done → STOP** | Export only (the user writes the paper): data tables, phase bullets, 3 diagrams, 2 charts, real-app example, candid summary, verified references -> `docs/paper_kit/ContextFS_paper_kit.zip` (local, git-ignored) |

## Open items needing the user
1. **Skipped folder names are ignored** (pre-existing bug found in Phase 8): names added in first run are saved but never passed
   to the indexer. **Deferred by the user to before the conference** (2026-10-29); the fix is output-identical with default settings.
2. The paper (written elsewhere from the kit). Optional for the memory-cue framing: a dev ablation of which signal serves which cue.
3. `C:\ContextFS-scratch` holds disposable copies (p7stage ~4.5 GB, p8code, p8/mem 100k corpus and indexes, harden8): say when to delete.
4. **Standing item (no phase):** clean-account check (Part B of `docs/release/CLEAN_ACCOUNT_CHECKLIST.md`) before any release or
   demo on a machine other than yours (Decision 105). Before any release: a third-party notices file.
5. Smart App Control is in Evaluation mode on this laptop; the build is unsigned. Checked by every self test.
6. Optional: real-user study (needs ethics approval; protocol in `docs/study/`).

## Machine notes
- D: (the project drive) is a bus-powered USB HDD that resets under sustained small-file writes. Test scratch lives in `C:\ContextFS-scratch` (disposable; user-approved). See log.md Decision 104.
- Memory (Decision 111): on the CPU default, 100k files peak ~1.1–1.35 GB, under the 1.5 GB limit for <= 8 GB machines; the GPU option reaches ~1.5 GB at 10k files (Settings warns).

- Run `dist\ContextFS\ContextFS.exe` from internal storage if possible: from the USB disk the window can freeze 1–3 s after the disk idles (log.md Phase 34).

## Standing reminders
- Paper-strategy docs stay local (public repo).
- Every number before Phase 29 is unverified; smoke-fixture numbers are never paper numbers.
- Headline for direction A (test): recon_v1 makes dates truthful after copies, but neither A primary is significant; even true file times leave temporal-cue Hit@1 at 0.118 on test.
