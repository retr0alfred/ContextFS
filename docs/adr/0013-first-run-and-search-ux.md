# ADR-0013: First-run and search UX — never wait, show how the query was read, show true dates

- **Status:** Accepted · **Date:** 2026-10-27 · **Phase:** v2-P2
- **Debate record:** UX R1/R2; performance R2 §1.6; skeptic R2 N12, §4

## Decision
1. **First run (three screens):**
   - **Places:** one or more folders, or a whole drive, plus OneDrive detection.
   - **Exclusions:** the defaults, editable, with file counts.
   - **What to expect:** estimated time and disk use, the privacy promise (local only, read-only,
     no network), and that cloud-only files are indexed by name and dates and never downloaded.
   - Choices are saved and never asked again; Settings can add, remove or change places at any time.
   - Indexing is staged: names and dates are searchable within about a minute, content and meaning
     follow, newest first.
2. **Every launch:**
   - the window appears within 2 s;
   - word search is ready within 2.5 s, meaning search within about 5 s warm;
   - a query sent before meaning search is ready waits up to 3 s, then shows word-match results
     labelled as such. They are upgraded only if the user has not touched the list;
   - the launch update runs in the indexer child with a determinate chip: `Updating · n / N · ~t
     [Cancel]`;
   - **search never waits** and never reorders results on screen;
   - an offline root is shown as offline, never purged without "Remove place".
3. **Results:**
   - each row shows file, snippet, one date with its source in words, the gated why-line
     (ADR-0009), and Open file / Open folder;
   - the detail pane shows evidence spans, the **"Dates for this file"** panel (every source, with
     verdicts *used*, *ignored (reset by copy)* or *unknown zone*, and confidence *sure*,
     *probably* or *unknown*), and a counterfactual on demand.
4. **"Read as" chips:**
   - time interval (core plus fade), type, entity, typo expansion, or "couldn't interpret X";
   - × removes a chip, and that is a **re-search** (ADR-0009 §5);
   - chip values map 1:1 to `SearchConfig` overrides, so the CLI and the parity test cover them;
   - date-picker editing is LATER.
5. **Issues list:** locked, denied, too large, encrypted, cloud-only, quarantined. Each with a
   reason and "Retry".
6. **Research mode** (hidden toggle): shows signals_used, contributions and the config hash. Used
   for the viva and the study.
7. **Cut or LATER:**
   - **cut:** feedback buttons, the 3D view in the app, Insights, the watcher;
   - **LATER:** the date strip, editable chips, high-contrast theme, clarifying questions.

## How we will know it was wrong
Time-to-window or a keystroke stall misses the targets (M-S1, M-U1), or study participants cannot
explain why a result appeared after reading its row.

## Revision 1 — after the fresh review (2026-10-27)
Source: `review/fresh_review.md` m1.
- Startup targets follow ADR-0002 Rev. 1.
- **Wording fix.** "Never reorders" means results are never reordered **once the user has
  interacted** with the list (selection or scroll). A list nobody has touched may be upgraded from
  word-match to full results, and is labelled so.
