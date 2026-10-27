# Round 1 · UX Lead

People re-find by **time and people** more than keywords [1], and they **orienteer** in small steps from something nearby [2]. So the UI must make two things cheap: correcting how a query was read, and stepping sideways from a near-miss.

Current GUI faults (`gui/app.py`):
- A single worker thread, so a scan blocks search.
- No first-run flow.
- Raw arithmetic as the explanation.
- The parsed query is never shown.
- `FAINT` text is 2.96:1 and the timeline grey 3.19:1 on `#050505`, where AA needs 4.5:1 [3]. Input borders are 1.44:1, where 3:1 is needed [4] (ratios computed locally from `theme.py`).

## 1. Position summary

- **Search never waits.** It runs on the last committed index. Indexing shows as a determinate chip with Pause and Cancel.
- **First run is three screens**: *Places → Exclusions → What to expect*. Indexing is staged so that names and dates are searchable within minutes.
- **Show how the query was read.** Editable chips (time range, type, topic) are passed to the engine as explicit overrides.
- **Explanations: evidence first, arithmetic last.** First the real passage, date and session neighbours. Then "mostly because of…". Contribution bars and a counterfactual ("without the session signal: #6") sit behind a disclosure.
- **Cut the 3D graph and the Insights tab.** Timeline survives as a date strip above results. Sessions survive as "worked on together" inside a result.

## 2. Proposals

### 2.1 First-run flow
**Decision.**
1. **Places.** Known folders (Documents, Desktop, Downloads, OneDrive) are offered pre-ticked, plus "Add folder or drive…". Picking a drive such as `C:\` runs a cancellable metadata-only pre-walk that shows per-folder counts, sizes and what will be skipped.
2. **Exclusions.** The defaults are listed with plain reasons: "Windows system files", "Program installs", "AppData", "Developer folders (node_modules, .git, venvs)", "Caches". Each has a checkbox and an N-files count. Custom patterns and "Restore defaults" are available. Rules match **paths**, not bare names at any depth.
3. **What to expect.** First-index time as a range, index size, and index location (`%LOCALAPPDATA%`, with "Change…"). Then the promise: *Nothing leaves this computer. Your files are never changed, moved or deleted. Everything learned lives in [location]; delete it any time.*

**OneDrive.** Cloud-only files are detected from attributes alone (`RECALL_ON_DATA_ACCESS` [5]). They are indexed by name, path and dates only, **never downloaded**, and the wizard says so: "1,240 online-only files will be findable by name and date; mark them 'Always keep on this device' to include contents." There is no in-app download toggle in v2.

**Staged availability.** Searches are possible at each stage:
1. names, paths and multi-source dates;
2. text;
3. embeddings and entities;
4. sessions.

The status chip names which stage is complete.

**Alternatives.** A bare folder picker (today) sets no expectations. Silently indexing the whole profile breaks trust.
**Trade-off.** The pre-walk is slow on an HDD, so it is skippable.
**Would change my mind.** If the full pipeline reaches usable search as fast as a metadata stage, drop the staging.
**Measurement.** Time to the first name/date search on a 100k-file synthetic profile on an 8 GB CPU box. Target: under 2 minutes.

### 2.2 Every launch
**Decision.** Search runs immediately on the last committed snapshot. The status shows `Updating · 1,240 / 3,900 changed · ~2 min [Pause] [Cancel]`. The bar is determinate because the count is known, which Microsoft prefers [6].
- Results on screen are **never reordered under the user**. A "Index updated, refresh results" link appears instead.
- **Pause** holds after the current batch and survives a restart. **Cancel** keeps committed batches.
- Closing the window stops indexing. There is no tray process.

**Missing root.** A banner says "E:\ is not connected; its 12,400 files are kept and marked offline". Those results carry an *offline* tag with Open disabled. Nothing is purged until the user chooses "Remove this place".

**Engine requirement.** A reader independent of the writer. Today's single `JobRunner` thread queues searches behind scans.
**Alternatives.** Blocking search until the update ends could take minutes after a week away.
**Trade-off.** Results can be briefly stale; the chip discloses it.
**Would change my mind.** If p95 launch update is under 3 s, a blocking "Updating…" is simpler.
**Measurement.** Measured while indexing:
- keystroke-to-paint stays under 16 ms p95;
- query latency stays at or below 2× idle.

### 2.3 Search UX
**Decision.**
- **Input.** One box, focused at launch (`Ctrl+L` or `/`). **Enter submits.** There is no search-as-you-type, because memory queries are sentences and partial results flicker. Any length or script is accepted.
- **"Read as" chips** appear only for what was detected, for example `▲ 1 Mar – 15 Apr 2025 (from "before my ML exam")` · `PDF` · `topic: machine learning`.
  - Click a chip to edit it (date picker, type list); × removes it.
  - Edits re-run as `overrides` in the engine API, so the CLI gets the same through `--from/--to/--type`.
  - Vague times ("last sem", "around Diwali") always show the concrete range chosen.
- **Result rows** have four lines:
  - **name** · type · reconciled date with its source, e.g. "created 3 Jan 2024 (PDF metadata; copy date ignored)";
  - the folder, middle-elided;
  - a snippet from the matched chunk, with terms in bold and underlined;
  - a **why line** of at most three glyph-plus-word reasons: `◆ about gradient descent · ▲ dated 14 Mar ("exam") · ● worked on with Lecture7.pptx`.

  Scores are hidden.
- **Keys:**

  | Key | Action |
  |---|---|
  | ↓ | Into the result list |
  | Enter | Open the file |
  | `Ctrl+Enter` | Open the folder with the file selected (`explorer /select,`) |
  | `Ctrl+Shift+C` | Copy the path |
  | `Alt+→` | Open the details |
  | `Esc` | Back to the search box |

- **Nothing found** always says why and offers to relax: "No PDFs dated 1 Mar–15 Apr match 'gradient'. Without the date: 7 results [Show]". It also mentions hits in excluded folders. **Weak matches** get a header: "Closest matches. None is a strong fit."
- **Research mode** (View menu) restores scores, the baseline comparison and the raw explanation JSON for the paper.

**Alternatives.** The current table hides snippets and reasons. An LLM answer is not faithful.
**Would change my mind.** Chips confuse users in testing.
**Measurement.** Think-aloud study with 6–8 students re-finding 5 of their own files. Measure success, time, chip-edit rate, and whether they can say why #1 appeared.

### 2.4 Explanations
**Decision.** The detail pane has three tiers:
1. **Evidence.** Matched passages with location ("p. 4"), the date sentence ("Exam on **14 March**"), and session members with times. These are document-side and checkable.
2. **"Mostly because of…"**, one sentence naming the dominant signal.
3. **"How it was ranked" (collapsed).** Bars labelled with glyph, word and number, and a **counterfactual**: "Without the session signal this would be #6." It is computed by re-scoring the *same candidate pool* with that signal zeroed, one re-sort per signal over ≤k items. It is shown only if the rank moves by ≥2.

**Faithfulness rule (unit-tested).** Every phrase in the why line maps to a non-zero contribution field, and no prose is generated.
**Would change my mind.** If the final scorer is not additive, the counterfactual becomes "rank with the layer ablated". It is still computed, never estimated.
**Measurement.** In the study, the share of users who correctly predict what removing a chip will do.

### 2.5 Views
- **3D graph: cut from the app.** It caps at 1,200 nodes, has no keyboard or screen-reader path, and answers no re-finding question. Keep `contextfs visualise` in the CLI for paper figures.
- **Insights: cut** for v2.
- **Timeline: a date strip.** A histogram of result dates above the list, with the detected range shaded. Dragging edits the time chip.
- **Sessions: orienteering inside a result.** "Worked on together" and "Files from the same days" re-query around a near-miss [2].

### 2.6 Settings
Settings are pages:
- **Places**: per root, file count, last update and offline status, with per-root exclusions.
- **Exclusions**: the first-run editor with preview counts.
- **Performance**: Auto / GPU / CPU only (showing the detected device), and an indexing pace of Gentle or Fast.
- **Search model**: the bundled choices, with a warning that changing it re-embeds N files.
- **Index**: location with *Move…*, size, *Check & repair*, *Rebuild*, *Open log folder*.
- **Privacy**: the promise and "Delete all ContextFS data".

Changes that re-index are staged behind "Apply (re-reads ~N files)".

### 2.7 Errors and edge states
One **Issues (37)** link in the status area opens a grouped list:
- *Locked*: retried next launch.
- *Permission denied*: lists the folders, with an [Exclude] action.
- *Online-only*, *password-protected* and *too large*: name and dates only.

Nothing vanishes silently. **Corruption** shows a banner, "Index damaged. [Repair] keeps settings, re-reads files (~est.)", and search is disabled with that reason. A **second launch** focuses the existing window.

### 2.8 Accessibility
- **Contrast.** Raise `FAINT` and the timeline grey to ≥4.5:1 and input borders to ≥3:1.
- **High contrast.** When `QAccessibilityHints.contrastPreference` reports it (Qt ≥6.10; 6.12 is installed) [7], drop the custom stylesheet for the system palette, which hard-coded stylesheets otherwise override [8].
- **Light/dark.** Follow the system preference.
- **Screen readers.** Every row gets an accessible name: "Result 1 of 10, Lecture7.pdf, Documents\ML, edited 12 March, mostly topic". Bars become real widgets with text values, not HTML `div`s in a `QTextEdit`. The result count is announced. Test with Narrator and NVDA through Qt's `QAccessible` layer [9].
- **Glyphs.** `◆` and `◈` look alike at 13 px, so a glyph never appears without its word.
- **Keyboard and focus.** A visible focus ring distinct from the inverted selection. Full keyboard operation.

## 3. Positions on other questions

- **UI stack:** keep PySide6-Essentials. Cutting the 3D view removes the only pull toward WebEngine.
- **Process model:** UI plus a separate indexer process (or at least an isolated writer), so search never waits and indexer crashes cannot kill the window. A watcher that runs **only while the window is open**, shown in the status area, is consistent with "no background service".
- **Packaging:** the first run must work offline with the model bundled. GPU use is automatic and the active device is shown.
- **Scale:** staged availability needs per-stage, per-batch commits with resume.
- **Index safety:** `%LOCALAPPDATA%` by default, movable, with a single-instance lock.

## 4. Risks in this proposal

- **Stage-1 results** (name and date only) may disappoint. The chip must say "contents still being read".
- **Chips depend on structured parser output.** An open-vocabulary parser must still emit fields.
- **Counterfactuals with a multiplicative format penalty** may read oddly.
- **Custom-painted rows are silent to screen readers** unless accessible text is set explicitly.
- **The pre-walk** on a big HDD may itself take minutes.
- **Broad user exclusions** can hide the target.

## 5. Sources

1. Dumais et al., Stuff I've Seen, SIGIR 2003: https://www.microsoft.com/en-us/research/?p=145459
2. Teevan et al., orienteering, CHI 2004: https://www.microsoft.com/en-us/research/publication/perfect-search-engine-not-enough-study-orienteering-behavior-directed-search/
3. WCAG 2.2 SC 1.4.3: https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html
4. WCAG 2.2 SC 1.4.11: https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html
5. File attribute constants: https://learn.microsoft.com/en-us/windows/win32/fileio/file-attribute-constants
6. https://learn.microsoft.com/windows/win32/uxguide/progress-bars ; https://learn.microsoft.com/windows/apps/design/controls/progress-controls
7. QAccessibilityHints: https://doc.qt.io/qtforpython-6/PySide6/QtGui/QAccessibilityHints.html
8. https://www.qt.io/blog/dark-mode-on-windows-11-with-qt-6.5 ; https://lists.qt-project.org/pipermail/interest/2023-June/039127.html
9. QAccessible: https://doc.qt.io/qt-6.10/qaccessible.html
