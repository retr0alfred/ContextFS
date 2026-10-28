# recon_v1 — reconciled per-event file time (direction A, v2-P6)

Status: design, 2026-10-29. Fits only on the **dev** split; the test split stays frozen until v2-P7.

## Problem (measured on dev, `data/time_eval/dev-time-baseline`)
- **Content-only copies reset both NTFS times.** For those files, raw mtime and rules_v0 put
  arrival and last edit at the migration date:
  - coverage 0.04–0.06 at **zero width**, i.e. confidently wrong;
  - median error 209–258 days.
- **Events that never happened get dates.** rules_v0 dates a "last edit" for **2,412** files nobody
  edited (downloads, camera imports); raw mtime for 3,908. These create false time matches and
  false "last edited …" explanations.
- **Camera imports get the wrong arrival point** (the capture time), although the interval covers
  the truth.

## Evidence available to the engine (never the truth)
- NTFS created `b` and modified `m`.
- In-file times: OOXML created/modified, PDF created/modified, EXIF original, zip entries.
- The Mark-of-the-Web zone.
- The path.
- Sibling files.
- The root's distribution of birth times.

## Design
1. **Bulk-copy detection (new, global).**
   - Sort the root's NTFS birth times. A window of 2 h holding ≥ max(50, 15% of the root's files)
     is a *bulk-copy event* [Bs, Be].
   - On dev, damaged personas show 133–221 births in their densest 2 h window (at the true
     migration + ≤ 1 h); undamaged personas show ≤ 17.
   - Global context is recomputed when the set of events changes. At the end of a completed scan,
     every folder is recomputed then, keeping incremental == clean.
2. **Per-file signatures.**
   - `copy` = b > m + 2 s: CopyFile family; birth reset, mtime kept (T v1).
   - `reset` = b ∈ a bulk event and m ≤ Be + 60 s: content-only copy; both times reset.
   - `reset_then_edited` = b ∈ a bulk event and m > Be + 60 s: an edit after the copy, so m is a
     real edit.
3. **Origin**, from evidence only:
   - `downloaded` when a MotW zone is present;
   - `received` when EXIF original is present and m is within the floating-zone window of it, or the
     file is `reset`;
   - `external` (someone else's document) when in-file modified − created ≤ 10 min and there is no
     MotW;
   - otherwise `own` (made by the user).
4. **Event existence.** Only `own` files get a `last_user_edit` (an edit evidenced by m, or by
   in-file modified > created + 10 min). Downloads, imports and external documents get none: no row,
   so ranking cannot match a non-event, and explanations cannot claim one.
5. **Anchors per event** (each path through these rules is a named *method*):
   - **authored:**
     - earliest in-file created;
     - else min(b, m) unless `reset`;
     - else an interval [floor, Bs] (floor = earliest sibling-evidence time in the folder, else
       1990).
   - **arrived_here:**
     - own → = authored;
     - downloaded: b (no damage), m (`copy`: the download stamp is kept), or [authored, Bs]
       narrowed by the download-lag quantiles (`reset`);
     - received: b (no damage), else [capture, min(Bs, capture + lag quantile)].
   - **last_user_edit (own only):**
     - m unless `reset`;
     - `reset`: in-file modified if present, else [authored, Bs].
6. **Calibration** (the only fitted part; dev only):
   - For every method, the residuals (truth − point) on dev give asymmetric quantiles that widen the
     interval to a **target coverage of 80%** (split-conformal by method).
   - Lag distributions (capture→import, authored→download) are dev quantiles.
   - Confidence label: sure if the method's dev within-1-day rate ≥ 0.9, probably if ≥ 0.5, else
     unknown.
   - Reported dev numbers use **leave-one-persona-out** fits. The shipped parameters (fit on all 20
     dev personas) are frozen, with a hash, before the test run.
7. **Ranking use of uncertainty.**
   - The time feature becomes the *expected* soft-overlap of the cue over the estimate's interval
     (uniform), times the confidence weight.
   - For point estimates (raw mtime, oracle) this equals today's feature, so P-A2's comparison of
     time sources stays like-for-like.
8. **Explanations** say "between X and Y (estimated)" for wide intervals, a date for narrow ones,
   and nothing for events that did not happen.

## Benchmark fidelity correction (T v1.1)
- The damage simulator keeps the MotW mark through every behaviour, but T v1.1 measured that
  content-only copies and git clone drop it.
- Evaluation therefore uses the **T v1.1 evidence view**: MotW evidence is removed for files whose
  damage behaviour drops it.
- This applies identically to every estimator and to dev and test. It was decided before any test
  run, on a measurement.
- The uncorrected view is reported as a robustness row.

## What must not happen
- No truth, manifest or `benchgen` import in the engine; parameters are aggregate quantiles only.
- No fitting on test personas.
- No claim beyond the simulator: the clue→event relations are partly simulator properties (for
  example, camera files' mtime equals EXIF capture exactly). The paper states this. External
  validity rests on the real-user study (ADR-0015 §9), not on this benchmark.

## As built (2026-10-29; log.md Phase 34). Differences from the draft above, after two independent reviews
- **No origin labels.**
  - The draft's `external` rule (in-file Δ ≤ 10 min) read a generator constant, and was dropped as a
    rule.
  - A reset file with no download mark or EXIF gets "came into your files" = its creation (variant
    A). This was selected on dev by LOPO macro interval score: A 230.8, B ("between creation and the
    copy") 314.6, C (A with a separate method) 310.4. The criterion was not committed before the
    runs, so this is a dev model selection, not a pre-registered one.
- **Soft existence.**
  - An edit is dated only on positive evidence: an mtime later than the download (downloads), later
    than both capture and import (photos), or any mtime of the user's own files.
  - Never-re-saved documents moved by a content copy get a weak (`unknown`) edit, not none.
  - The photo rule was changed after the import-time-mtime perturbation (605 → 0 false edits); that
    perturbation is therefore no longer an independent test.
- **Precedence:** a copy signature (b > m + 2 s) beats bulk-event membership.
- **Bulk-copy events:**
  - gap clustering (30 min) with size ≥ 30 and density ≥ 100× the root's birth rate (dev-fitted);
  - stored per root, and recomputed (whole root re-estimated) only at the end of a completed scan.
- **Calibration** is per method and widen-only (α = 0.2). Pooling within a method still widens some
  undamaged files (undamaged arrival median width 21 d): a stated cost.
- **Points** come from evidence anchors, never from fitted lags.
- **Ranking:** expected soft overlap over the interval (equal to the old feature for points);
  explanations give a range for wide intervals.
- **Limitations to state:**
  - authored ≈ 1.00 because in-file "created" equals truth by construction;
  - documents whose in-file "modified" does not track edits lose last-edit recovery after a content
    copy (0.92 → 0.22 in the perturbation suite);
  - `camera_copied`'s fitted widening (+21 d) mirrors the generator's 1–20 d import lag;
  - one migration date in dev and test (a dev stress set covers other geometries).
