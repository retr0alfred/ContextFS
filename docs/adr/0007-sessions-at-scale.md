# ADR-0007: Session reconstruction at scale — exact clustering inside time-gap blocks

- **Status:** Accepted · **Date:** 2026-10-27 · **Phase:** v2-P2
- **Debate record:** performance R1/R2, architect R1/R2, skeptic R2 (N5, D4, D10)

## Context
v1's average-linkage clustering is O(n³) time with three dense n×n float32 matrices: about 40 GB
each at n = 10⁵. Its idle-gap gate (two clusters may merge only if some pair is within G hours) means
that files separated by a gap greater than G can **never** share a session. G = 240 h was chosen on
the 40-file smoke corpus (code default 72 h).

## Decision
1. **Time base:** `file_time[last_user_edit].t_point` (ADR-0006). Files whose interval is wider than G
   are excluded from sessions. Sessions cover **extracted documents only**, not metadata-only files.
2. **Blocks:** sort by time and cut wherever the consecutive gap exceeds G. The gate makes blocks
   **exactly independent**, so clustering per block equals v1's global result. Blocks larger than
   **B_max = 2,000** are cut deterministically at their largest internal gaps (ties broken by
   `file_id`), and the forced-cut share is reported.
3. **Within a block:** v1's exact semantics (average linkage, gate, affinity weights, threshold),
   vectorised:
   - k×k affinity sums, updated on merge;
   - k×k min-gap, using an elementwise min on merge;
   - a cached best partner per row;
   - entity Jaccard as a sparse incidence product.
4. **Incremental** (skeptic D4):
   - always recompute the block **partition**, which is a sort (O(n log n));
   - memoise each block by `hash(sorted(file_id, t_point, feature_fp))`;
   - re-cluster only blocks whose hash changed;
   - carry session IDs over by max-Jaccard matching.
   - No dirty-range logic.
   - **A property test asserts that incremental equals a clean rebuild** on scripted edit sequences.
5. **G and the link threshold are fitted on the v2-P3 dev split** (D10). The smoke-fixture values
   are not carried over.
6. Sparse linkage for B_max > 2,000 is LATER (v2-P8), gated on pairwise F1 ≥ 0.95 against the exact
   method.

## Consequences
- Cost estimate (to be measured as M-SE1): about 0.1–0.5 s and 48 MB per 2,000-file block; a full
  rebuild at 5×10⁵ files takes minutes, not hours.
- If forced cuts dominate on real drives, the cap is effectively the algorithm. This is reported,
  not hidden.

## Objections
- **Performance (R1):** sparse linkage with a 20k cap. *Conceded* to exact linkage with a 2k cap for
  the MVP.
- **Skeptic (N5):** G = 240 makes the cap the algorithm. *Accepted*: fit G on dev, report forced cuts.

## How we will know it was wrong
Forced cuts exceed 10% of blocks on the dev personas; incremental ≠ clean on any scripted sequence;
or session pairwise F1 on dev falls far below the v1 smoke result with no explanation.
