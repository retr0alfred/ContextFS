# ContextFS

**A local-first, context-aware, time-intelligent personal file retrieval system.**

> Filesystems assume you remember *paths*. People remember *projects, deadlines,
> exams, meetings, and work sessions*. ContextFS is a contextual memory layer
> that sits above your filesystem and closes that gap — without ever touching
> your files.

Final Year CS research project · Alfred Mathew (43110050), Abu Ibrahim Mothi (43110024)
· Supervisor: Dr. Murari Devakannan Kamalesh

---

## Status

🚧 **Under active construction.** This README is provisional; the full
documentation pass (setup, architecture diagrams, usage examples, honest
strengths/weaknesses) lands in Phase 25 of the build.

Build progress is tracked phase-by-phase in **[log.md](log.md)**, which is also
where every design decision and every deviation from the original spec is
recorded with its reasoning. Usage instructions live in **[guide.md](guide.md)**.

| Phase | Status |
|---|---|
| 1 — Project scaffold & environment | ✅ complete |
| 2 — Configuration & CLI skeleton | ⏳ next |
| 3–26 | ⬜ pending |

---

## Research hypothesis

> Context-aware retrieval — combining semantic understanding, relationship
> modelling, activity modelling, and temporal intelligence — outperforms pure
> semantic (embedding-only) retrieval for **memory-based** file re-finding.

ContextFS exists to support or refute that hypothesis with measured numbers, not
to be a feature showcase. Both systems (a pure-semantic baseline and the full
hybrid) are built and evaluated side by side on the same ground-truth query set.

## Non-negotiable principles

| Principle | What it means here |
|---|---|
| **Local-first** | No cloud calls, no telemetry, no external APIs, no paid APIs. Everything runs on your machine. |
| **Read-only** | Scanned files are never modified, renamed, moved, or deleted. All derived data lives in ContextFS's own SQLite/LanceDB stores. |
| **Explainable** | Every result carries a machine-readable reason: matched topic, entities, session, timeline, and the graph path that connected it. |
| **Incremental** | Re-scanning reprocesses only what changed. |
| **Private** | No screenshots, no activity monitoring, no background surveillance. |

## Quick start

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install "torch>=2.2,<3" --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\python.exe -m pip install -e ".[dev,datagen]"
.venv\Scripts\python.exe -m spacy download en_core_web_md
.venv\Scripts\python.exe scripts\generate_corpus.py
```

The CPU-only torch index is deliberate — see Decision 2 in [log.md](log.md).

Configuration lives in [`contextfs.toml`](contextfs.toml). To point ContextFS at
your own files without committing your paths, copy it to `contextfs.local.toml`
(git-ignored) and edit that.

## License

MIT.
