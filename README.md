# ContextFS

**Local-first search over your own files, by what you remember about them.**

> Filesystems assume you remember *paths*. People remember *what a file was about, what kind of
> file it was, roughly when it came in or when they last worked on it*. ContextFS indexes your
> folders without ever modifying them and lets you search by any of that, with every result
> explained.

Final-year B.E. CSE research project · Alfred Mathew (43110050), Abu Ibrahim Mothi (43110024)
· Supervisor: Dr. Murari Devakannan Kamalesh

📝 [Build log and every design decision](log.md) · 📊 [Status](docs/STATUS.md) ·
🏗 [Architecture decisions](docs/adr/) · ⏱ [How file times are reconstructed](docs/engine/RECON_V1.md)

---

## Results

Every number in this section comes from **one run on a frozen, held-out test split** (log.md
Phase 35). The run configuration was hashed and committed before it executed, the run counter
allows exactly one test run, and nothing was re-run or adjusted afterwards. Configuration sha256
`3934cb68…` (`data/p7/frozen_config.json`); outputs in [`data/p7/test/`](data/p7/test/).

**The benchmark** is procedural: simulated people (students, researchers, professionals) with
years of files, realistic file times, and realistic damage from moving files between drives
(copies that reset creation and modification times, lost download marks). Queries are written the
way people remember files. All fitting used the 20 development personas; the **test split has 24
different personas, 5,715 files and 916 queries**. A replay of the simulated damage through the
real tools (Copy-Item, shutil, robocopy, git clone) agreed on every field for 162 of 162 sampled
files.

### Pre-registered primary results (Holm-corrected across all three)

| Primary | Result | p | Holm p |
|---|---|---|---|
| **P-S** System: ContextFS vs the strongest baseline (BM25 + dense + cross-encoder rerank), nDCG@10, all queries | **0.672 vs 0.562 (+0.110)**, persona CI [0.094, 0.126] | 0.00005 | **0.00015** |
| **P-A1** Time accuracy: arrival-date interval score per persona, raw mtime − ContextFS's estimator | +267 days in ContextFS's favour, CI [12, 568] | 0.088 | 0.177 |
| **P-A2** Ranking on time-cued queries: ContextFS's estimator − raw mtime, nDCG@10 | +0.008, CI [−0.002, 0.023] | 0.163 | 0.177 |

**The system result holds. Neither time-accuracy primary is significant.**

### Search quality (test split, 916 queries)

| System | nDCG@10 | MRR | Hit@1 |
|---|---|---|---|
| Path + modified-time heuristic | 0.310 | 0.173 | 0.095 |
| Dense embeddings (MaxP) | 0.375 | 0.256 | 0.142 |
| BM25 | 0.478 | 0.292 | 0.163 |
| BM25 + dense (RRF) | 0.464 | 0.285 | 0.148 |
| ContextFS v1 (the earlier version of this project) | 0.461 | 0.354 | 0.235 |
| BM25 + dense + cross-encoder rerank | 0.562 | 0.320 | 0.175 |
| **ContextFS v2** | **0.672** | **0.431** | **0.274** |

| Query kind | n | ContextFS | Cross-encoder baseline | Δ |
|---|---|---|---|---|
| Memory cues (time, activity, mixed, vague) | 531 | 0.695 | 0.545 | **+0.150** |
| Content cues (topic, entity, format) | 385 | 0.640 | 0.585 | **+0.054** |

The gain comes from the engine as a whole (content, names, format and time combined), **not**
from the time estimator: with time switched off ContextFS scores 0.634, and choosing between raw
mtime (0.666), rule-based times (0.672), the reconstructed times (0.672) or the *true* times
(0.676) moves it by less than 0.01.

### Dating files after they were copied (test split)

Copying a folder to a new drive resets file creation and often modification times, so "when did
this come into my files?" gets confidently wrong answers. For **content-copied files**
(n = 1,427):

| Estimator | Arrival: interval contains the truth | Arrival: within 1 day | Last edit: within 1 day (n = 302) |
|---|---|---|---|
| Raw modified time | 0.00 | 0.001 | 0.06 |
| Rule-based (rules_v0) | 0.07 (zero-width, i.e. confidently wrong) | 0.001 | 0.06 |
| Rules + bulk-copy detection | 0.98 (median width 526 days) | 0.001 | 0.70 |
| **ContextFS (recon_v1)** | **0.88** (median width 913 days) | **0.18** | **0.70** |

It also stops inventing edits that never happened (`last edited` dates on files nobody edited):

| | Downloads (2,777) | Camera imports (1,715) |
|---|---|---|
| Rule-based | 1,579 | 1,715 |
| **ContextFS** | **644** | **0** |

**Read honestly:**
- Most of the coverage gain comes from detecting bulk copies, which a rule-based estimator can
  also use. That fairer baseline beats ContextFS on several rows. ContextFS's own contributions are
  point accuracy and not dating events without evidence.
- The intervals are wide (median ~2.5 years after a content copy).
- Undamaged files pay for the calibration: their median arrival interval is 21 days wide, where
  the rule-based estimator gives a point.
- On time-cued queries, better file times barely help ranking. Even true file times leave Hit@1
  on temporal-cue queries at 0.118 (ContextFS 0.104; n = 144).

### The desktop app (hardening suite and scale runs, log.md Phases 33–36)
- 22 of 22 hardening cases pass on the final packaged build: read-only on your files; crashes,
  hangs and kills; damaged index; low disk; the window never stalls for more than 250 ms.
- With 10,000 files: search p95 66 ms, and a no-change launch update in 1.5 s.
- With 100,300 files, indexing on the processor (the default): peak indexer memory 1,098 MB in the
  measured run (worst level observed across runs ~1,350 MB), a no-change launch update in 10.3 s,
  search p95 456–505 ms.
- The final build gives bit-identical indexes and results to the build evaluated above
  (20 dev personas, 758 queries).

### Limits of this evidence
- **Simulated data.** The persona generator, the damage model and the queries are ours. The test
  split is an in-distribution replication of the development split, not evidence about real people.
- A real-user study protocol exists ([docs/study/](docs/study/)) and has not been run yet.
- The fidelity replay checks the *kind* of damage each tool does, not its timing.
- Windows/NTFS only; English only.

Earlier versions of this README reported numbers from the v1 build on a 40-file, 17-query smoke
fixture. Those were never independently verified and are withdrawn (log.md, Phase 29 onward).

---

## Principles

| Principle | What it means here |
|---|---|
| **Local-first** | No cloud calls and no telemetry. The app blocks network connections from its own Python code, and its models ship with it. |
| **Read-only** | Indexed files are never modified, renamed, moved or deleted. Enforced by tests and the hardening suite. |
| **Explained** | Every result says why it matched, including which file time it used and how sure that date is. |
| **Incremental** | Re-indexing processes only what changed, and its result equals a clean rebuild. |

---

## Run it

Windows 10/11. From a checkout:

```bash
start.bat
```

On first run, `start.bat` installs everything inside the project folder: portable Python, a
virtual environment, and the models. It then opens a menu (desktop app or command line). The
portable app is built with:

```bash
.venv\Scripts\python.exe scripts\build_app.py
```

and runs as `dist\ContextFS\ContextFS.exe`. Run it from an internal drive if you can; from a USB
hard disk the window can pause briefly while the disk wakes.

## Reproducing the evaluation

```bash
.venv\Scripts\python.exe scripts\p7_run.py verify
```

`verify` checks the code, models, split files and every persona's files against the frozen
configuration. The benchmark itself is released as generator + seeds (`scripts/generate_benchmark.py`,
`data/splits/split_v1.json`), not as files, because zipping files destroys their creation times.
`p7_run.py run --split dev` re-runs the full evaluation on the development split. It only executes
from a staged copy on an internal drive (`C:\ContextFS-scratch\p7stage`; how to build one is in
log.md Phase 35). The test split has had its one run.

---

## License

MIT. See [LICENSE](LICENSE).
