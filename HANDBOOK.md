# ContextFS Handbook

Everything in one file: how to run it, how it works, what it's good at, what
it isn't, what's genuinely new, and what can't be built yet.

Final Year CS project · Alfred Mathew (43110050), Abu Ibrahim Mothi (43110024)
· Supervisor: Dr. Murari Devakannan Kamalesh

---

## 1. The one-line version

Filesystems assume you remember **paths**. People remember **projects,
deadlines, exams, meetings, and what they were doing at the time**. ContextFS is
a read-only memory layer over your files that lets you search the second way —
and tells you why every result came back.

---

## 2. Starting it

Double-click **`start.bat`**. That's the whole answer.

On first run it builds a virtual environment, installs everything, downloads the
two models, generates a 40-file demo corpus, and builds the index. That takes a
few minutes once. Afterwards it opens in seconds.

Then pick from the menu:

| | |
|---|---|
| **1** | Desktop application |
| **2** | Command line |
| **3** | 3D relationship graph |
| **4** | Re-scan / update the index |
| **5** | Run the research evaluation |
| **6** | Run the test suite |

`start.bat` also passes anything it doesn't recognise straight to the CLI, so
`start.bat query "the ML exam pdf"` works without touching the menu.

**Manual setup**, if you'd rather:

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install "torch>=2.2,<3" --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\python.exe -m pip install -e ".[dev,datagen,gui]"
.venv\Scripts\python.exe -m spacy download en_core_web_md
.venv\Scripts\python.exe scripts\generate_corpus.py
.venv\Scripts\python.exe -m contextfs scan
```

The CPU-only torch index is deliberate — ContextFS never uses a GPU, and the
default index pulls gigabytes of CUDA you'd never run.

**To point it at your own files**, copy `contextfs.toml` to
`contextfs.local.toml` (git-ignored) and change `root`. Or per-command:

```bash
contextfs --root "C:\Users\you\Documents" scan
```

ContextFS is **read-only** on whatever you point it at. It never writes,
renames, moves, or deletes there — that's enforced by a test, not a promise.

---

## 3. The desktop application

```bash
contextfs gui
```

Three tabs.

**Search** — type what you remember. Results on the left, the *reasoning* on the
right, permanently side by side. Each row carries a glyph saying which signal
found it: `◆` semantic, `◈` graph, `●` activity, `▲` timeline. A row marked `●`
was found because of *when you worked on it*, not because of its text — visible
before you read a word.

The right panel breaks the score into its parts: each signal's raw value, its
weight, and its contribution, plus the plain-English reasons. `✓ This was the
one` / `✗ Not this` record feedback. `Open file` opens it in its normal app.

**Insights** — what's on disk by type and age, projects and their lifecycle
stage, and near-duplicate groups. All reports; nothing is ever deleted.

**Index** — pick a folder, scan it, watch each stage report as it finishes.

`Ctrl+R` re-scans · `Ctrl+G` opens the 3D graph.

**Why use it over the CLI?** A measured reason, not a stylistic one. Loading the
models takes ~23 s, and the CLI pays that *on every command*, because each
command is a separate process. The GUI pays it once at startup and keeps the
models resident — every search after that is ~75 ms. The status bar tells you
when the load finishes rather than hiding the cost.

---

## 4. The command line

The CLI is the primary surface and does everything.

```bash
contextfs scan                    # build or update the index
contextfs query "..."             # search
contextfs timeline "March to April"
contextfs explain 1               # why did result #1 come back?
contextfs stats                   # index health
contextfs digest                  # what's on disk
contextfs duplicates              # near-duplicate groups
contextfs projects                # bodies of work + lifecycle
contextfs tags <file>             # tags ContextFS would give a file
contextfs feedback --pick 2       # that one was right
contextfs gui                     # desktop app
contextfs visualise               # 3D graph
contextfs config                  # resolved settings
contextfs reset                   # delete the index (never your files)
contextfs fetch-models            # the ONLY command that uses the network
```

Search the way you'd actually remember:

```bash
contextfs query "the PDF I studied before my ML exam" --explain
contextfs query "what I was working on the week of the hackathon"
contextfs query "that spreadsheet from around the capstone deadline"
```

Useful flags:

- `--explain` — show full reasoning inline
- `--compare` — run the plain-semantic baseline beside ContextFS on the same index
- `--signals semantic,graph` — switch layers off (this is the ablation harness)
- `--json` — machine-readable, on every reporting command

Everything is one-shot. No daemon, no background service, nothing running
between commands.

---

## 5. The 3D relationship graph

```bash
contextfs visualise
```

Writes one **self-contained HTML file** (~1.3 MB) and opens it. three.js is
bundled *inside* the file, so it fetches nothing — works offline, and you can
email it to someone who doesn't have ContextFS installed.

Drag to orbit · scroll to zoom · click a node.

The point isn't the animation, it's the **relation toggles** in the top-left.
Turn `semantic` off and leave `activity` on: files rearrange into clusters by
*when you worked on them* rather than by what they say. That's the project's
central claim, made visible in one interaction.

`structural` and `temporal` start hidden — structural alone is 326 of 597 edges
and drawing it over everything buries what you came to look at.

---

## 6. How it works

Eleven layers. Each one adds a kind of context the one below it can't see.

```
your files ──▶ 1 scan ──▶ 2 extract ──┬─▶ 3 entities ──┐
 (never written)                       ├─▶ 4 embeddings ┼─▶ 5 tree ─▶ 6 graph
                                       └─▶ 7 dates ─────┤       ▲
                                                8 sessions ──────┘
                                                        │
                                          9 hybrid retrieval + explanation
                                                        │
                                        10 CLI    ·   11 desktop app + 3D
```

1. **Scan** — walk the tree, hash with xxh3 (fast change *detection*, not
   cryptography). New/modified/deleted tracked; deletions tombstoned.
2. **Extract** — pdf, docx, pptx, xlsx, md, txt, code, csv. Keeps structure —
   headings, table rows, character spans — because later layers need to know
   *where* in a document something appeared.
3. **Entities** — people, organisations, places, dates, keywords. Two
   corpus-wide correction passes fix per-document mistakes.
4. **Embeddings** — MiniLM, 384-d, CPU-only, stored in LanceDB.
5. **Semantic tree** — chunk → file → folder → project, each level summarised.
6. **Relationship graph** — six edge types: semantic, entity, structural,
   duplicate, temporal, activity.
7. **Temporal intelligence** — *the novel core.* Every date is classified
   **meaningful** (a deadline, an exam, a meeting) or **incidental** (a
   copyright year, a footer stamp, a version string), from four weighted
   signals: surrounding keywords, structural position, distance from the file's
   own mtime, and how often that date recurs across the corpus.
8. **Activity sessions** — reconstructs *what you worked on together* by
   clustering modification times with semantic, entity and folder similarity.
9. **Retrieval** — four signals combined, weights re-normalised over whichever
   are enabled, with every result carrying its own reasoning.
10. **CLI** · 11. **Desktop app + 3D graph**

Derived data lives in `.contextfs/` — one SQLite database, one LanceDB
directory, one graph JSON. Delete it and nothing of yours is lost.

### What it actually does that plain search can't

```
Unit4_Ensemble_Methods.pdf
    ◆ semantic  0.181   ← near the floor
    ● activity  1.000   ← retrieved on this
    ◈ graph     0.800
    reason: same work session as your query: exam prep in
            College/Semester7/MachineLearning (10 Nov 2025)
```

That PDF contains no exam vocabulary. Embedding search can't find it at any
threshold that doesn't also return everything else. ContextFS returns it because
you worked on it during the same sitting — and says so.

---

## 7. What's new and novel

Against what already exists — Windows Search, Spotlight, Recoll, DocFetcher, and
modern RAG pipelines:

**1. Meaningful vs incidental date classification.** *The main contribution.*
Every existing tool treats a date as a date. A copyright footer "© 2019" and a
deadline "submit by 14 Feb 2026" are the same token to all of them, so temporal
search drowns in noise. ContextFS classifies them from four weighted signals and
gets **F1 0.986** against **0.700** for naive extraction. No filesystem search
tool does this at all.

**2. Activity sessions as a retrieval signal.** Timestamps are used for
*sorting* everywhere; they're used for *reconstructing what you were doing* here.
The system infers that six files formed one work session and can then return a
file because it belongs to a session your query described. Session
reconstruction scores **F1 1.000** on the benchmark, including an adversarial
case (two projects worked on the same afternoon) and a negative control.

**3. Explanation as an enforced invariant.** RAG systems cite sources. ContextFS
reports the *arithmetic* — each signal's value, weight, and contribution, plus
the graph path traversed. A result that can't say why it's there fails a
completeness check, measured at **100% coverage across all six configurations**.
Explainability is a test, not a feature.

**4. Query-adaptive weighting.** Weights shift with what the query is asking
for — but only when the query itself carries the cue. This was added because
entity queries were measured *degrading* under the context layers.

**5. Local-first, genuinely.** No cloud, no telemetry, no API keys, no
background service, no filesystem watcher. Runs on a 2019 laptop with no GPU.
Comparable "AI search" products are cloud-dependent by construction.

**6. Reproducible by construction.** The corpus is *generated* deterministically
rather than committed, so anyone can clone the repo and re-derive every number.
Verified: all six ablation configurations reproduce **bit-identically** from a
clean clone.

---

## 8. Advantages

- Finds files by memory — deadlines, sessions, people, roughly-when — not just
  by words in them.
- **Every result explains itself**, in your terms.
- **Read-only.** It cannot damage your files; it has no code path that writes there.
- **Private.** Nothing leaves the machine. One command touches the network
  (`fetch-models`) and it says so before it runs.
- **Fast on weak hardware.** 72 ms median query, 18 ms warm re-scan, no GPU.
- **Incremental.** Editing one file in forty reprocesses that one file —
  measured **25× faster** than a full rebuild.
- **One system, three surfaces.** GUI and CLI proven to return identical results
  on all 17 benchmark queries, every score matching to 1e-9.
- Every threshold in the system was **measured**, with the measurement table
  recorded beside the value in `contextfs.toml`.

## 9. Disadvantages — stated plainly

1. **The benchmark corpus is synthetic and self-authored.** 40 files whose
   ground truth was written by the same people who wrote the system. That makes
   the numbers reproducible; it also makes them unrepresentative. Nothing here
   has been validated on real personal files.
2. **n=17 queries**, with per-kind cells of 2–4. The breakdowns are directional.
   No significance test is claimed, because none would be honest at this n.
3. **Hit@1 is only 0.471.** ContextFS is much better at getting the right answer
   *into* the shortlist and ordering it than at nailing the first result.
4. **Entity extraction is weak** — F1 0.595, precision 0.489, so roughly half of
   extracted entities are wrong.
5. **Activity modelling depends entirely on mtime**, which cloud sync, bulk
   copies and restores routinely destroy. Where that's happened the layer is
   silently useless — no detection, no repair.
6. **Session clustering is O(n²) and global.** Incremental update has a ~450 ms
   floor that doesn't shrink with fewer changes. At tens of thousands of files
   this needs a design change that isn't implemented.
7. **English and text only.** No OCR — scanned PDFs are invisible. No
   multilingual models. No `.eml`, no proprietary formats.
8. **Feedback is unevaluated.** It's provably *bounded* — one pick adds exactly
   half the cap, and no amount of clicking overturns a clear win — but there's
   no measurement that it helps, because with one user there's no honest way to
   produce one.
9. **No automated UI tests.** The app is exercised end-to-end offscreen and its
   retrieval is proven identical to the CLI's, but nothing asserts layout.
10. **The 3D view caps at 1200 nodes.** It reports the drop rather than hiding it.

## 10. Where the numbers come from

| | |
|---|---|
| MRR (baseline → ContextFS) | 0.503 → **0.632** |
| Recall@10 | 0.686 → **0.856** |
| Hit@1 | 0.412 → **0.471** |
| Temporal-query MRR | 0.067 → **0.344** |
| Activity-query MRR | 0.312 → **0.583** |
| Date classification F1 | **0.986** (naive: 0.700) |
| Session reconstruction F1 | **1.000** |
| Explanation coverage | **100%** |
| Tests | **473 passing**, 82% coverage |

Reproduce any of them:

```bash
python scripts/evaluate.py           # retrieval + full ablation table
python scripts/date_eval.py          # meaningful vs incidental dates
python scripts/session_eval.py       # session accuracy
python scripts/entity_eval.py        # entity extraction
python scripts/incremental_check.py  # incremental correctness and speed-up
```

Nothing in this document is estimated.

---

## 11. Future scope — genuinely blocked by current technology

Not a wishlist. These are things that can't be built well *today*, with the
reason.

**1. Recovering context after timestamps are destroyed.** The activity layer
dies when cloud sync rewrites mtimes, and the information is *gone* — not
hidden, deleted. Recovering it would need OS-level provenance that no consumer
filesystem keeps. NTFS USN journals and ext4 don't retain enough, and they roll
over. Blocked until filesystems record durable provenance.

**2. True cross-application context.** ContextFS knows a PDF and a spreadsheet
were edited together. It can't know you had them open beside a lecture recording
and a browser tab, because that requires OS-wide activity capture — which is
exactly the surveillance this project refuses. The technical fix (screen and
process monitoring) is available today and is the wrong trade. What's *missing*
is a privacy-preserving OS-level activity API. Blocked on platform design, not
on capability.

**3. Semantic understanding of non-text content.** Images, video, audio and
diagrams are invisible. Local multimodal models exist but need far more RAM and
GPU than a 2019 laptop has. This is a hardware-timeline problem — it becomes
possible when small multimodal models run in ~2 GB on integrated graphics.

**4. Personal-scale learned ranking.** The four weights are hand-tuned because
one user generates a handful of feedback events — nowhere near enough to learn
from. Federated or few-shot personalisation would need either a population of
users (which breaks local-first) or sample efficiency current methods don't
have.

**5. Retrieval over decades.** Session clustering is O(n²) and the semantic
model has a fixed vocabulary snapshot. A twenty-year archive spans vocabulary
drift the embeddings can't follow — "the cloud" in 2005 and 2025 embed to
similar vectors and mean different things. Nobody has a good answer to temporal
embedding drift.

**6. Proving a negative.** ContextFS can't say "that file does not exist." It
can only say it didn't find one. Distinguishing *absent* from *not retrieved*
would need a completeness guarantee no ranked retrieval system provides.

**7. Multi-device context without a server.** Your files span a laptop, a phone
and a drive. Merging their contexts means either a sync server (breaks
local-first) or CRDT-style local-first sync over encrypted transport — buildable
in principle, but a whole second project.

---

## 12. Troubleshooting

| Symptom | Fix |
|---|---|
| `contextfs: command not found` | Use `start.bat`, or `.venv\Scripts\python.exe -m contextfs` |
| PowerShell blocks `Activate.ps1` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, or just use `start.bat` |
| `pip install` pulls gigabytes of CUDA | You skipped the CPU-only torch step |
| `No index at ...` | Run `contextfs scan` |
| "index was written by an older build" | Harmless. Run `contextfs scan` to upgrade it |
| Model-not-cached error | `contextfs fetch-models` once, online |
| Glyphs show as `*+o^` instead of `◆◈●▲` | Your console is on a legacy code page. Harmless — that's the deliberate ASCII fallback. `chcp 65001` fixes it |
| First query feels slow | Model load, paid once per process. The GUI pays it once per session |
| `en_core_web_md` not found | `.venv\Scripts\python.exe -m spacy download en_core_web_md` |

---

## 13. Where everything is

| | |
|---|---|
| `start.bat` | Launcher — start here |
| `README.md` | Project summary, results, strengths and weaknesses |
| `guide.md` | Step-by-step usage walkthrough |
| `HANDBOOK.md` | This file |
| `log.md` | Every design decision with its reasoning and measurements |
| `docs/architecture.md` | Layer-by-layer code map |
| `docs/real-data-migration.md` | What validating on real files would require |
| `contextfs.toml` | Every threshold, with the measurement that set it |
| `src/contextfs/` | Source |
| `scripts/` | Evaluation harnesses |
| `data/synthetic/` | Generated corpus and ground truth |

Licence: MIT. three.js is bundled under its own MIT licence, notice preserved.
