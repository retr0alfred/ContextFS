# Round 2 · UX Lead

Changes since Round 1:
- I accept the skeptic's cuts: no watcher, and one indexer child process.
- Explanations now use a signed reference point, and the "why" line is gated on whether removing a reason changes the rank.
- Time chips show the scorer's soft interval.
- File dates are shown with their source and confidence, following M1.
- Scope is cut to fit (§5).

## 1. Revised proposal

### 1.1 Before the models load
I adopt Perf's milestones: window ≤2 s, word search ready ≤2.5 s, meaning search ready ≤4 s.
- The search box works from the first paint, and the status line reads `Word search ready · meaning search loading…`.
- A query sent before meaning search is ready waits up to 3 s behind a spinner.
- After 3 s, word-match results appear under the header *"Word-match results. Meaning search will refine these."*
- Those results are replaced **only if the user has not touched the list** (no selection, no scroll). Otherwise a "Refine with meaning search" link appears. This keeps my rule that results are never reordered under the user.
- Every response shows its `signals_used`. The why-line never cites a signal that did not run.

### 1.2 Every launch
**Search** reads the last committed snapshot and never waits for indexing.

**Updates.** There is no watcher. A scan runs at launch, and **F5** rescans on demand. Progress shows as a determinate chip, `Updating · 1,240 / 3,900 · ~2 min [Cancel]`. Cancel keeps every committed batch, and the next launch resumes.

**Status messages**, never silent:

| Situation | What the user sees |
|---|---|
| Root missing (drive unplugged, share offline) | Its files are kept and marked *offline*, with Open disabled. Nothing is purged without "Remove place". |
| The CLI holds the writer lock | "Another ContextFS (command line) is updating the index. Search works." |
| The indexer child crashes (including GPU device removal) | "Indexing stopped unexpectedly. [Restart] [Restart on CPU only]". The CPU choice is sticky. |
| Free space is below 2× the estimated index size | A warning. |
| `SQLITE_FULL` | Indexing pauses with a banner. |

### 1.3 How a file's "when" is shown (M1)
M1 measured three copy patterns:
- the created time is reset (Copy-Item, cmd, `copy2`, Expand-Archive);
- both times are preserved and the copy is invisible (robocopy);
- both times are destroyed (`shutil.copy`).

A result row therefore shows **one date plus its source in words**:

| Engine evidence | Row | Detail adds |
|---|---|---|
| Consistent FS times | `Modified 14 Mar 2025` | — |
| created > modified | `Modified 14 Mar 2025` | "Copied to this PC 27 Oct 2026; 'created' is the copy, not authorship." |
| FS rewritten, and an earlier date inside the file | `Saved 14 Mar 2025 (inside the document)` | "File date 27 Oct 2026 looks like a copy: 312 files share it." |
| FS rewritten, nothing earlier | `27 Oct 2026 · may be a copy date` | The cohort or burst reason |
| Zone unknown or 2 s precision | Date only, no clock time | "Time zone not recorded" |
| Online-only file | FS date only | "Dates inside the file unavailable (online-only)" |

**"Dates for this file" panel (MVP).** It lists every stored source (FS times, `docProps`, PDF, EXIF, zip). Each source gets a verdict: *used*, *ignored (reset by copy)*, or *unknown zone*. The reconciler's confidence appears as a word: *sure*, *probably*, or *unknown*. The UI never invents a date; it only renders the reconciler's output and reasons.

### 1.4 Chips mapped to IR's `QueryFrame`
| Field | Chip | MVP edit → override |
|---|---|---|
| Time interval (core + decay, query span) | `▲ 1 Mar–15 Apr 2025, fading ±2 wk (from "before my ML exam")` | × → `time=REMOVED` (time features set to absent) |
| Anchor | Tooltip: "ML exam → Exam_Timetable_Sem7.xlsx, 14 Mar" | — |
| P(kind) | `PDF` if P ≥ 0.6, else `type: unsure (PDF/slides)` | ×, or pick a kind → `kind=REMOVED` or `kind=pdf` |
| Corpus entity | `person: Dr. Rao` | × → entity features set to absent |
| Typo expansion | `also searched: gradient (for "gradeint")` | × → expansion off |
| Failed stage | "Couldn't interpret 'around Pongal'" | — |

- Overrides are part of the frozen `SearchConfig`, so the CLI (`--no-time`, `--kind`) and the parity test cover them.
- Removing a chip is faithful by construction, because it is exactly the absent-value re-score.
- A chip never shows a crisp range the scorer does not use.
- Date-picker editing is LATER.

### 1.5 Explanations (faithful by definition)
Definitions:
- `contribution(g) = s(x) − s(x with group g at its frozen absent value)`. It is signed.
- **Counterfactual(g)** = this file's rank after re-scoring **the same pool** with g absent.

The detail pane shows, in order:
1. **How it was found.** This is the pool-entry gate, which is not an additive term: "Word match on 'gradient'", "Meaning match (p. 4)", or "Brought in: worked on together with Lecture7.pptx".
2. **Evidence.** The cited `doc_ref` spans and the dates panel. No reason may simply echo the query.
3. **Why it ranks here.** The why-line names only groups whose removal costs **at least one rank**. If none does: *"Ranked mostly on wording; no single piece of evidence decides it."*
4. **Counted against**, shown when the magnitude exceeds ε: "Not a PDF (−0.12)".
5. **Details (collapsed).**
   - Signed bars with glyph, word and number.
   - "Without session evidence: #6 (now #1)", or "would not have been considered" for files that entered the pool through expansion.
   - Tooltip: "same candidates; first stage not re-run".

**Test.** Every shown reason must meet three checks:
- its Δrank ≥1, or it is labelled as a gate or a negative;
- its claim re-verifies against the index;
- the uncited share of the score is shown in Research mode.

### 1.6 Topology and the rest
- **Process.** UI and the query path run in one process (CPU ONNX encoder). One indexer child does extraction, embedding and any GPU work.
- **Feedback buttons:** cut, for parity.
- **Research mode** shows the `SearchConfig` hash, `signals_used` and the uncited share, so demo numbers match paper rows.
- **Open.** Uses `os.startfile` on a normal path. For long `\\?\` paths it falls back to opening the parent folder, with a notice.
- **Accessibility (MVP):** contrast token fixes, the system palette under high contrast, accessible names on rows and the detail pane, and a Narrator smoke test.

## 2. Responses to the skeptic's attacks on UX

- **9 (explanations):** accepted in full. Contributions are signed against a frozen absent value, the why-line is Δrank-gated, negatives and the pool gate are shown, and chips show the decay band.
- **11 (process):** resolved (§1.6). Round 1's "isolated writer" ambiguity is withdrawn.
- **14 (watcher):** accepted. My Round 1 watcher is withdrawn in favour of F5.
- **16 (minor):** the CLI-lock message and the `\\?\` open fallback are added. A second launch focuses the window, and the file lock is authoritative.
- **17 (scope):** NVDA work and the larger study shrink to a Narrator smoke test and a **5-person, 45-minute think-aloud**. I keep the study because it supplies ground truth not produced by the generator (§3 Q1, attack 8).
- **10f (parity):** results are labelled with `signals_used`, and the parity assertion includes it.

## 3. Answers to the shared questions

1. **Reconciler.** The IR/ML role owns the algorithm; Arch owns the schema. For ground truth, each study participant dates 10 of their own files against calendar or exam dates, giving about 50 real labels. Add the old-laptop copy and M1's transfer table.
2. **Evaluation.** Evaluate the shipped artifact: the same ORT package, quantised file and CPU EP. Research mode shows the config hash.
3. **Topology.** UI + query in one process, plus one indexer child. GPU runs only in the child.
4. **Vector store.** Defer to Perf/Arch. My requirement is query p95 ≤300 ms on the 8 GB target, at 10⁵ and 10⁶ chunks. Brute force is fine if it meets that.
5. **P0.** Defer to IR. The UI shows each result's entry route.
6. **Contribution and soft time.** See §1.4–1.5.
7. **Zones and precision.** Stored as the skeptic proposes. The UI shows date only for zone-unknown or coarse sources.
8. **File opens.** These belong to the engine (share-delete, frozen atime). I add one release test: Word saves a large file while it is being indexed, and the save must succeed.
9. **Disk.** Perf supplies the per-10⁴-files figure. The UI shows the estimate and handles low space (§1.2).
10. **Four weeks behind.** Cut, in this order: chip editing, the date strip, "same days", the high-contrast switch, then Issues per-item actions. **Never cut:** search-never-waits, the evidence tier, the dates panel, or the privacy promise.

## 4. Contradictions resolved

| Conflict | Resolution |
|---|---|
| Arch's pipe host vs Perf's one process | Perf's model |
| Perf's "upgrade in place" vs UX's no-reorder rule | Auto-upgrade only while the list is untouched |
| Arch's `feedback=True` | Feedback cut |
| Arch's watcher | No watcher; F5 |
| IR's "largest contribution" vs the skeptic | Δrank-gated why-line plus signed details |
| Arch sessions on `t_modified` | Sessions must use the reconciled time the UI shows, or "worked on together" contradicts the dates panel |

## 5. Scope

| UI element | Status |
|---|---|
| Single-instance focus; first run (Places, editable exclusions, expectations, privacy promise); OneDrive notice | MVP |
| Per-folder pre-walk estimates | LATER (MVP: live counts) |
| Progress chip, Cancel, resume next launch, F5, offline/lock/crash/disk messages | MVP |
| Persistent Pause | LATER |
| Watcher | CUT |
| Search box and keyboard flow; word-match labelling; 4-line rows with a Δrank-gated why-line | MVP |
| Chips with × removal | MVP |
| Chip editing | LATER |
| Detail pane (entry route, evidence, counted-against, bars, counterfactual); dates panel; "worked on together" | MVP |
| "Same days" view; date strip | LATER |
| Nothing-found with relaxations; Research mode | MVP |
| Feedback buttons; Insights tab; 3D graph in the app (CLI export kept) | CUT |
| Settings: places, exclusions, repair = rebuild, delete all data | MVP |
| Settings: move index; GPU toggle | LATER |
| Model choice | CUT |
| Issues list with grouped counts | MVP |
| Per-item fixes | LATER |
| Contrast fixes, accessible names, Narrator test, 5-person think-aloud | MVP |
| High-contrast palette | MVP, first to drop |
| Light theme; NVDA pass | LATER |

## 6. Remaining risks

- The Δrank gate may often say "ranked mostly on wording". That would be a research finding, not a UI bug.
- Auto-upgrading results can still surprise a user who reads without touching the list.
- 50 date labels are a sanity check, not a benchmark.
- If the reconciler outputs only a timestamp with no reasons, the dates panel degrades to listing raw sources.

**Sources:** WCAG 1.4.3 and 1.4.11 (https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html, https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html) and Qt `QAccessibilityHints` (https://doc.qt.io/qtforpython-6/PySide6/QtGui/QAccessibilityHints.html), as in Round 1. Everything else comes from the debate files and M1.
