# Response to the fresh review (2026-10-27)

Every finding in `fresh_review.md`, and where it was addressed. "Rev. 1" means the ADR's appended
Revision 1 section, which supersedes conflicting clauses above it.

| Finding | Severity | Resolution | Where |
|---|---|---|---|
| B1 Benchmark has no ADR; circular undamaged-time model | Blocker | **Fixed.** New ADR: splits by persona and template family; undamaged model calibrated from donor histograms and hashed before any estimator rule (CI-enforced order T → donor stats → rules); graded relevance; power calculation; generator never calls the engine | ADR-0015; ADR-0006 Rev. 1 |
| B2 Primary metric measures fusion, not A; test 4 self-contradictory | Blocker | **Fixed.** Three pre-registered primaries (P-A1 intrinsic, P-A2 extrinsic with parser+raw-mtime baseline, P-S system), Holm-corrected; test 4 reworded as a provenance taint test | ADR-0015 §7; ADR-0006, 0008, 0014 Rev. 1 |
| B3 Unsigned build blocked by Smart App Control on clean installs | Blocker | **Accepted; needs the user.** Signing moves into the MVP; options (SignPath / Certum / institution) involve cost and identity, so raised at the Phase 2 stop. Fallback: SAC-off deviation the user approves | ADR-0004 Rev. 1 |
| M1 Never descending reparse points skips OneDrive | Major | **Fixed.** Decide by reparse tag: skip mount-point/symlink/unknown, descend `CLOUD*`; fixture + ProcMon test | ADR-0011 Rev. 1 |
| M2 Scan-history evidence uses wall clock; lost on rebuild | Major | **Fixed.** Clock injected into `update()`; append-only `observations` table survives rebuilds; benchmark replays scan→damage→scan; excluded from incremental==clean | ADR-0006, 0010, 0012 Rev. 1; ADR-0015 §4 |
| M3 Dirty shutdown is the common case; recovery blocks search | Major | **Fixed.** Clean stop on close (Cancel, 3 s, then kill); `quick_check` in the child while search continues; migrate a copy and swap | ADR-0001, 0012 Rev. 1 |
| M4 GPU gates vs int8 default; one-device validation | Major | **Fixed.** One precision per index (fp32 for GPU-built indexes, gated at matched precision); on-device 200-text probe on first GPU use with CPU fallback | ADR-0003 Rev. 1 |
| M5 Handicapped baselines; no frozen build for baselines | Major | **Fixed.** Tunable document-level BM25 (FTS5 for candidates, k1/b re-scored from `fts5vocab`); dense MaxP for all systems; equal tuning budget reported; eval bundle from the same lockfile | ADR-0003, 0005, 0008 Rev. 1 |
| M6 Index-build parity untested | Major | **Fixed.** Logical index hash across CLI/GUI and 1/2 workers; ties within 1e-6 broken on root-relative path | ADR-0010 Rev. 1 |
| M7 No 8 GB / no-GPU budget | Major | **Fixed.** Job Object memory limits, background mode, threads = cores−2, low-battery pause, chunk cap, user folders first | ADR-0001 Rev. 1 |
| M8 Default index location contradicts the rule | Major | **Fixed.** Hard self-exclusion; refuse only inside non-excluded scanned subtrees, network shares, OneDrive | ADR-0005 Rev. 1 |
| M9 "Faithful" overclaims | Major | **Fixed.** Renamed "evidence-grounded and counterfactually checked"; feature and citation are the same object (MaxP chunk, `evidence_used`); span-neutralisation check LATER | ADR-0009 Rev. 1 |
| M10 Query generation can leak | Major | **Fixed.** LLM sees a degraded memory view only; lexical-overlap check against participant queries; study defined | ADR-0015 §6, §9 |
| M11 Feasibility | Major | **Fixed.** Dated 12-week plan; paper path decoupled and first; T as a simulator validated by 5% real-tool replay; cut order reordered | ADR-0014 Rev. 1; ADR-0015 §4 |
| m1 Startup targets below measured import; "never reorders" contradiction | Minor | Fixed | ADR-0002, 0013 Rev. 1 |
| m2 Move inheritance without a hash | Minor | Fixed | ADR-0011 Rev. 1 |
| m3 Unmap acknowledgement covers only the UI | Minor | Fixed (pending-delete list for all readers) | ADR-0005 Rev. 1 |
| m4 FAT DST, exFAT offset, NTFS tunnelling, Zone.Identifier | Minor | Fixed (added to T and to evidence) | ADR-0006 Rev. 1 |
| m5 "0.503 or explained" unfalsifiable; MRR vs nDCG; corrections, run cap | Minor | Fixed (±0.0005 or blocker; Holm; test-run cap 3) | ADR-0008 Rev. 1; ADR-0015 §2, §7 |
| m6 Session partition comparison | Minor | Fixed (up to relabelling) | ADR-0010 Rev. 1 (logical hash) |
| §3.1 Logging and diagnostics | Missing | Added | ADR-0016 §1 |
| §3.2 Privacy of the index | Missing | Added (owner ACL, not-content-indexed, no OneDrive, EFS metadata-only, BitLocker warning, temp cleanup) | ADR-0016 §2 |
| §3.3 Licences | Missing | Added (notices, CI licence gate, MS MARCO cross-encoder eval-only) | ADR-0016 §3 |
| §3.4 Generator ↔ engine contract | Missing | Added | ADR-0015 §10 |
| §3.5 Real-user study | Missing | Added | ADR-0015 §9 |
| §3.6 Upgrades in the field | Missing | Added | ADR-0016 §4 |
| §3.7 Non-English content | Missing | Stated | ADR-0016 §5 |
