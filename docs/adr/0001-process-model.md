# ADR-0001: Process model — UI+query process, one indexer child, no watcher

- **Status:** Accepted · **Date:** 2026-10-27 · **Phase:** v2-P2 (log.md Phase 30)
- **Debate record:** `debate/round1/*`, `debate/round2/*` (all roles), skeptic attacks 11, 14

## Context
The app must stay responsive while every launch updates the index incrementally (BRIEF §3). v1 ran
everything on one Qt worker thread, so searches queued behind scans. v1's principle is "no
background service". Query-time work is light once inference moves to ONNX Runtime on CPU
(ADR-0003). Indexing is heavy: parsing, NER, embedding, and (later) GPU work.

## Options considered
| Option | For | Against |
|---|---|---|
| A. Single process, worker threads (v1) | Simplest | Parsers and native crashes kill the window; GIL contention stalls the UI during indexing |
| B. UI process + RPC engine-host process (architect R1) | Full isolation | An RPC surface for every query; two copies of state; more to freeze and test |
| **C. UI process runs queries in-process; one indexer child is the sole writer** | Queries call the same `Engine` the CLI and eval call (parity); parser or GPU crashes kill only the child | Two processes to supervise |
| Watcher while app open | Freshness | Event storms on whole drives; more failure modes; skeptic attack 14; scope |

## Decision
1. **App process** (`ContextFS.exe`): Qt UI plus `Engine` in read mode. Runs queries on the CPU
   (query batch = 1). Never does extraction, bulk writes or GPU work.
2. **Indexer child** (`ContextFS.exe --indexer`): the **only writer**. Placed in a Windows Job Object
   with `KILL_ON_JOB_CLOSE`, so it can never outlive the window. Runs 1–2 recycled extraction
   workers with per-file timeouts. A file in progress is recorded first, and a file that kills the
   child twice is quarantined.
3. The control pipe carries only commands and progress; **data flows through SQLite**. The app
   picks up new data when `meta.index_generation` changes.
4. **No file watcher.** A scan runs at launch, plus **Refresh (F5)**. "No background service" holds
   unqualified: nothing runs when the window is closed.
5. **Search never waits for the update.** It reads the last committed snapshot (ADR-0013).

## Consequences
- GUI, CLI and eval call one in-process `Engine` (ADR-0010).
- A parser segfault or GPU device-removal crash ends the child only. The UI offers "Restart" or
  "Restart on CPU only", and the CPU choice is sticky.
- Changes made while the app is open appear only after F5 or the next launch. This is accepted.

## Objections
- **Architect (R1):** wanted an RPC engine host for isolation. *Conceded in R2*: ONNX makes queries
  light, and parsers already run in the child.
- **IR, UX, Architect (R1):** wanted a watcher while open. *Overruled* on scope and failure modes
  (skeptic attack 14). Revisit in v2-P8 only.

## How we will know it was wrong
Keystroke-to-paint p95 > 100 ms or an event-loop stall > 250 ms during indexing (Phase 7 M-U1). Or
users report that stale results after in-session edits matter.
