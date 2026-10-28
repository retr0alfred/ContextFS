# ADR-0006: Time evidence and per-event reconciled time (foundation for paper direction A)

- **Status:** Accepted · **Date:** 2026-10-27 · **Phase:** v2-P2
- **Debate record:** architect R2, IR R2, packaging R2, skeptic R1 #1–4, skeptic R2 N1–N5, D1;
  facilitator measurement M1 (`debate/measurements.md`)

## Context
- Direction A (Decision 92) needs per-file time evidence from several sources and a reconciled
  time that survives copies, syncs and restores.
- M1 measured three copy behaviours:
  - CopyFile-style copies reset the created time and keep the modified time;
  - robocopy keeps both;
  - content-only copies destroy both.
- The skeptic showed:
  - the v1 generator fakes only mtime/atime, so every synthetic file looks copied, and python-docx
    stamps DOCX files with 2013 (N1);
  - the proposed ground truth was circular (N2);
  - "the time" mixes different events (N3).

## Decision
1. **Schema** (owner: architect role). `time_evidence(file_id, source, kind, wall_s, utc_s,
   offset_min, tz_state ∈ {utc, offset, floating}, resolution_s, reliability, raw, captured_scan,
   input_fp)`. It records:
   - NTFS birth and modified times (UTC);
   - FAT/exFAT and DOS-zip times (floating, 2 s resolution);
   - Office `docProps/core.xml` created and modified, plus `lastModifiedBy`;
   - PDF Info/XMP create and modify times;
   - EXIF `DateTimeOriginal` (with `OffsetTimeOriginal` when present);
   - zip entry times;
   - ContextFS observations: `first_seen`, and `superseded_mtime` (mtime moved, hash unchanged).
   - Atime is captured once, before our first read, as weak evidence and **never ranked**. We
     never request `FILE_WRITE_ATTRIBUTES`.
2. **Per-event output** (skeptic D1). `file_time(file_id, event ∈ {authored, arrived_here,
   last_user_edit}, t_lo_utc, t_hi_utc, t_point, confidence ∈ {sure, probably, unknown}, method,
   evidence_used, estimator_version)`.
   - **Sessions use `last_user_edit`.**
   - Time queries match the event the query implies ("downloaded" → `arrived_here`, default
     `last_user_edit`).
   - Ground truth labels say which event they record.
3. **Estimator v0** (owner: IR role) is rule-based:
   - floating times become intervals of −14 h to +12 h;
   - in-file times survive transfers;
   - `birth > mtime` means a CopyFile copy, so the mtime survived (M1);
   - `birth ≈ mtime` inside a folder burst makes the mtime suspect;
   - robocopy is invisible, and that is harmless;
   - in-file `docProps created` is ignored when `modified` exists (templates).
   - Burst parameters are fitted on the **dev split only**. The probabilistic estimator is the
     contribution, in v2-P6.
4. **Non-circular evaluation protocol:**
   - **E1 — freeze the transfer table first.** The packaging role measures table **T** by hand: tool
     × file-system pair → effect on every time field. The tools are Explorer copy/move, robocopy,
     7-Zip, `git clone`, a OneDrive round trip, FAT32/exFAT USB, a browser download and Save As.
     T is committed with a hash **before** any estimator rule file exists, and CI enforces the
     ordering.
   - **E2 — damage by real tools.** The generator damages times only by applying T's tools for real
     on NTFS, and the real scanner captures the evidence.
   - **E3 — leakage guard.** The estimator cannot import `datagen` or read manifests. `file_time` is
     byte-identical after manifests are deleted.
   - **E4 — independent ground truth** (skeptic N2). Truth is **never an estimator input**:
     pre-transfer file-system times are sealed in a file the engine cannot read, from real donor
     files (target ≥ 300 from several donors, kept locally). The achievable CI width is computed
     before the study, and v1's 40-file copy is a smoke check only.
   - **E5 — pre-registered metrics per damage type:** interval coverage at median width, median
     absolute error in days, share within 1 day. Singleton content-only copies are their own row,
     with **no detection claim** (N4).
5. **The generator must be fixed before any number** (N1, a v2-P3 requirement). It sets birth times
   (`SetFileTime` on its own files) and writes consistent in-file metadata. A **zero-damage persona
   test** must show the copy signature on 0% of files and an estimator error of 0.

## Consequences
- The UI shows one date plus its source in words, and a "Dates for this file" panel (ADR-0013).
- Placeholders contribute file-system times only; per-source coverage is reported.

## Objections
- **IR, architect (R2):** a single interval. *Overruled* (D1); a per-event interval is the paper's
  substance.
- **Performance (R2):** a `t_modified_ns` column. *Replaced* by `last_user_edit`.
- **Architect (R2):** content-copied real files with in-file metadata as the answer. *Rejected*
  (circular, N2).

## How we will know it was wrong
The zero-damage test fails; E4 donors cannot reach a CI width that separates methods; or T shows
that the in-file times we rely on are rewritten by common tools.

## Revision 1 — after the fresh review (2026-10-27)
Supersedes conflicting clauses. Source: `review/fresh_review.md` B1, B2, M2, M11, m4.
- **The undamaged time model is frozen before the estimator** (B1): donor histograms, hashed, in the
  order T → donor statistics → rules (ADR-0015 §3).
- **Damage is a simulator plus real-tool replay** (M11): T is measured with real tools, the
  generator applies T's effects, and a 5% sample of every damage type is replayed through the real
  tools to report fidelity. This replaces "real tools wherever possible" in E2.
- **Scan history** (M2):
  - `update()` takes an injected clock;
  - `first_seen`, `superseded_mtime` and move observations go to an **append-only `observations`
    table** that is backed up with settings and **survives a rebuild**;
  - the benchmark replays scan → damage → scan;
  - history is excluded from the "incremental == clean" comparison.
- **Primaries** (B2): pre-registered P-A1 (E5 per damage type) and P-A2 (nDCG@10 on time-cued
  queries, `file_time` vs raw mtime vs oracle, with a parser + raw-mtime baseline). See ADR-0015 §7.
- **Additions to T (m4):**
  - **NTFS tunnelling**: a safe-save (write temp, delete, rename) restores the old creation time,
    so `birth > mtime` can also come from editing, not only copying;
  - FAT times converted with the *current* DST bias;
  - exFAT's stored UTC offset;
  - `Zone.Identifier` (Mark-of-the-Web ADS) as free `arrived_here` evidence for downloads, read
    only on non-placeholder files.

## Status update — 2026-10-29: the v2-P6 estimator and the arrival event (log.md Phase 34, Decisions 106–107)
- **`arrived_here` means "came into your files"** (download, import or creation). It does *not*
  mean the copy date of a migration. The truth stays as generated (damage never changes it); the
  app's label is "Came into your files".
- **T v1.1 addendum** (`data/transfer_table/T_v1_1_motw.*`): Mark-of-the-Web survival was measured,
  and content-only copies and git clone drop it. The damage simulator was corrected, and the
  correction re-applied to dev and test before any test run.
- **The probabilistic estimator of §3 is `recon_v1`** (`docs/engine/RECON_V1.md`):
  - evidence rules: in-file times, MotW, EXIF, siblings, and bulk-copy events detected from the
    root's birth times;
  - an event gets a date only on positive evidence;
  - per-method, **widen-only** split-conformal calibration fitted on dev only (`recon_params.py`,
    sha256 46c06469…);
  - the app's default time source since Decision 107.
- **E5 on dev:** see log.md Phase 34. The rules were developed on dev (in-sample); only the
  calibration is held out (leave-one-persona/profile/archetype-out).
