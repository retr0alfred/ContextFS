# ADR-0002: UI stack — keep PySide6-Essentials; cut the 3D graph, Insights and the semantic tree from the app

- **Status:** Accepted · **Date:** 2026-10-27 · **Phase:** v2-P2
- **Debate record:** `debate/round1/ux.md`, `packaging.md`, `performance.md`; `round2/ux.md`

## Context
v1 ships a PySide6-Essentials GUI (209 MB installed) with Search, Insights and Index tabs. The 3D
graph is a standalone three.js HTML export. `PySide6` import costs 2.18 s on the dev machine
(measured, performance R2). Requirements: a normal Windows app, fast to start, small enough to
bundle, accessible.

## Options considered
| Option | For | Against |
|---|---|---|
| **PySide6-Essentials (keep)** | Already written; native widgets; LGPL; PyInstaller hooks are mature; no Chromium | ~209 MB; ~2 s import |
| Tauri/Electron + Python sidecar | Web UI flexibility | Second runtime and language; IPC for every query; user ruled out "webapp" (Decision 82) |
| Tkinter | Free, small | Weak widgets for results and explanations; poor accessibility |
| Dear PyGui / Flet | GPU UI / quick | Non-native; immature freezing story; weak screen-reader support |

## Decision
1. **Keep PySide6-Essentials.** The window must appear within 2 s warm and 4 s cold. The UI process
   never imports torch, spaCy, LanceDB, NetworkX or scipy (enforced by an import-guard test).
2. **App contents (MVP):**
   - first-run flow;
   - a single search box with result rows (file, snippet, signed "why" line, dates);
   - detail pane: evidence, the "Dates for this file" panel, a counterfactual on demand;
   - progress chip, Settings, Issues list.
3. **Cut from the app:** the 3D graph (it stays as a CLI export, LATER), the Insights tab, and the
   semantic tree. Timeline and sessions are shown inside results ("worked on together", the date
   strip is LATER), not as separate views.
4. **Accessibility:**
   - fix the measured contrast failures (`FAINT` 2.96:1 and the timeline grey 3.19:1, both below
     WCAG 4.5:1);
   - an accessible name on every result row;
   - full keyboard flow.
   - Windows high-contrast support is LATER.

## Consequences
The installed size is dominated by Qt plus ORT plus models (ADR-0004). The research "views" leave the
product; the paper uses evaluation figures instead.

## Objections
- **UX (R1):** cut 3D and Insights. *Accepted.*
- **Architect:** keep the 3D view for the viva. *Kept as a CLI export only.*
- Nobody argued for leaving Qt.

## How we will know it was wrong
Cold start > 4 s on the 8 GB VM after the import-guard work (M-S1), or freezing PySide6 fails parity
twice.

## Revision 1 — after the fresh review (2026-10-27)
Source: `review/fresh_review.md` m1. Targets corrected to what is achievable given the measured
2.18 s PySide6 import:
- window ≤ 3 s warm and ≤ 5 s cold;
- word search ready ≤ 3.5 s;
- meaning search ≤ 6 s warm.
Phase 7 measures them; optimisation (v2-P8) may tighten them.
