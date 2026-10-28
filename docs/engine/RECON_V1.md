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
