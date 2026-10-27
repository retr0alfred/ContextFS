# Real-user re-finding study: protocol (ADR-0015 §9)

**Purpose.** An external check that the benchmark's memory-style queries look like real ones, and
a small real-data test of ContextFS against the baselines on people's own folders. Study data is
**never used for fitting** (tuning, thresholds, templates).

**Run by:** the authors (Alfred Mathew, Abu Ibrahim Mothi), supervised by Dr. Murari Devakannan
Kamalesh. **Ethics:** obtain the institution's approval, or a written exemption, **before**
recruiting.

## Participants
- 8–12 adults (students or staff) who keep a personal folder of at least 300 files on a Windows
  laptop.
- Voluntary, unpaid, free to withdraw at any time with all their data deleted.
- Consent form: `docs/study/CONSENT.md`, signed before the session.

## What runs where
Everything runs on the **participant's own laptop**, from a USB copy of the portable app (v2-P4).
- No network.
- The index lives in a temporary folder on the participant's machine, deleted at the end.
- Nothing about their files leaves the machine except the export described below, after they
  have reviewed it on screen.

## Session (about 60 minutes)
1. **Consent** (5 min).
2. **Queries before indexing** (15 min).
   - The participant names a folder and, *before anything is indexed*, writes **8–12
     descriptions of files they would like to find again**, in their own words, from memory.
   - At least 3 should mention *when* (for example "before my exam", "around Diwali") and at least
     3 should mention *what they were doing*.
   - They do not open the files while writing.
   - Typed into the study tool, timestamped.
3. **Index** (15–30 min, depending on folder size). The participant may take a break.
4. **Judging** (20 min).
   - For each query, the tool shows the **pooled top-10** from all systems (ContextFS and every
     baseline), interleaved and de-duplicated in a fixed random order.
   - **The system labels are hidden.**
   - The participant grades each result: 3 = the file I meant; 2 = a version or copy of it;
     1 = related (same task); 0 = not relevant.
   - They may also say "I misremembered which file it was". That is recorded as an outcome, not
     discarded.
5. **Export review** (5 min). The tool shows the exact export on screen. The participant may
   delete any query text before saving.

## What is exported (per participant)
- Query texts (after the participant's own review), the cue types *they* tick (time / activity /
  person / content / format), and timings.
- For every judged result: the query number, the **anonymised result id** (a salted hash), the
  grade, and which systems returned it at which rank.
- Optional: the donor time-statistics histogram (`scripts/donor_time_stats.py`), counts only.
- **Never:** file content, file names, folder names, paths, author names, exact file timestamps.

## Analysis plan (fixed before the first session)
- **Primary:** nDCG@10 of ContextFS vs the strongest baseline, over all participant queries.
  Paired randomisation test; bootstrap CI by participant (cluster bootstrap).
- **Secondary:**
  - the same split by participant-ticked cue type;
  - Hit@1;
  - the share of "misremembered" outcomes;
  - lexical overlap between query and target (to compare against the generated benchmark
    queries, ADR-0015 §6).
- With n ≈ 10 participants × 10 queries, the study is **descriptive and confirmatory in
  direction only**. It is reported with its CIs and is not claimed as powered.

## Data handling
- Exports are stored only on the authors' machines, in a git-ignored folder, and deleted after
  the paper is accepted or after 12 months, whichever is first.
- Participants receive a copy of their own export.

## Tooling status
The study tool (query capture → index → blinded pooled judging → export review) is built on the v2
engine. It is scheduled for v2-P4/P5, when the portable app exists. This protocol is fixed now so
the analysis cannot be adapted to the results.
