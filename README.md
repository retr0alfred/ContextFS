# ContextFS

**A local-first, context-aware, time-intelligent personal file retrieval system.**

> Filesystems assume you remember *paths*. People remember *projects, deadlines,
> exams, meetings, and work sessions*. ContextFS is a contextual memory layer
> that sits above your filesystem and closes that gap — without ever touching
> your files.

Final Year CS research project · Alfred Mathew (43110050), Abu Ibrahim Mothi (43110024)
· Supervisor: Dr. Murari Devakannan Kamalesh

📖 [User guide](guide.md) · 🏗 [Architecture](docs/architecture.md) · 📝 [Build log & every design decision](log.md)

---

## The claim, and the evidence for it

> **Hypothesis.** Context-aware retrieval — combining semantic understanding,
> relationship modelling, activity modelling, and temporal intelligence —
> outperforms pure semantic (embedding-only) retrieval for **memory-based** file
> re-finding.

A pure-semantic baseline and the full hybrid system are built side by side, over
the *same* index and the *same* embeddings, and evaluated on the same 17-query
ground truth. Result:

| System | MRR | P@5 | Recall@10 | Hit@1 | Explanation coverage |
|---|---|---|---|---|---|
| Semantic baseline | 0.503 | 0.564 | 0.686 | 0.412 | 100% |
| **ContextFS (full)** | **0.585** | **0.720** | **0.856** | 0.412 | 100% |
| Δ | **+0.082** | **+0.156** | **+0.171** | ±0.000 | — |

**The effect is entirely concentrated in the query kinds the extra layers were
built for**, which is the result that actually supports the hypothesis:

| Query kind | n | Baseline MRR | ContextFS MRR | Δ |
|---|---|---|---|---|
| Temporal | 3 | 0.067 | 0.344 | **+0.278** |
| Activity | 4 | 0.312 | 0.417 | **+0.104** |
| Semantic | 4 | 0.625 | 0.653 | +0.028 |
| Hybrid | 4 | 0.775 | 0.781 | +0.006 |
| Entity | 2 | 0.750 | 0.750 | ±0.000 |

Purely semantic queries barely move — as they should. A system that "improved"
those too would be suspicious.

### Ablation (which layer earns its place)

| Configuration | MRR | R@10 | Question |
|---|---|---|---|
| Semantic only | 0.518 | 0.686 | control |
| + graph | 0.536 | 0.727 | RQ4 |
| + graph + activity | 0.536 | 0.739 | RQ1 |
| + graph + timeline | 0.585 | 0.845 | RQ2 |
| **Full (all four)** | **0.585** | **0.856** | RQ5 |

Read honestly: **the temporal layer is doing most of the work.** Adding activity
on top of graph moves MRR by 0.000 and R@10 by 0.012; adding the timeline moves
MRR by 0.049 and R@10 by 0.118. The activity layer's value shows up in per-kind
results (+0.104 on activity queries) and in recall, not in the headline average.

### What the system does that semantic search structurally cannot

```
$ contextfs explain 2

College/Semester7/MachineLearning/Unit4_Ensemble_Methods.pdf
    semantic  0.181   ← near the floor
    activity  1.000   ← retrieved on this
    graph     0.800
    reason: same work session as your query: exam prep in
            College/Semester7/MachineLearning (10 Nov 2025)
```

That PDF contains no exam vocabulary. Embedding search cannot find it at any
threshold that does not also return everything else. ContextFS returns it
because you worked on it during the same sitting — and it says so.

### Other measured results

| What | Result |
|---|---|
| Meaningful vs incidental date classification | **P 0.972 / R 1.000 / F1 0.986** (naive extraction: F1 0.700) |
| Activity session reconstruction | **P 1.000 / R 1.000 / F1 1.000**, 5/5 sessions, adversarial case separated, negative control never clustered |
| Incremental update | **2 of 40 files reprocessed (5%), 25.1× faster** than a full rebuild |
| Entity extraction | P 0.489 / R 0.759 / **F1 0.595** (n=29 — reported unflatteringly on purpose) |
| Explanation coverage | **100%** across all six configurations |
| Median query latency | **92 ms** (warm, Ryzen 7 3700U, CPU only) |
| Test suite | **457 tests**, 82% line coverage |

Every number above comes from a script in [`scripts/`](scripts/) that you can
re-run. None are estimated. See [Reproducing the results](#reproducing-the-results).

---

## Non-negotiable principles

| Principle | What it means here |
|---|---|
| **Local-first** | No cloud calls, no telemetry, no external APIs, no paid APIs. The one optional network feature (local Ollama summarisation) refuses non-loopback addresses and is off by default. |
| **Read-only** | Scanned files are never modified, renamed, moved, or deleted. Enforced by test, not by convention. Even `contextfs duplicates` only reports. |
| **Explainable** | Every result carries a machine-readable reason. An empty explanation fails the completeness check. |
| **Incremental** | Re-scanning reprocesses only what changed. |
| **Private** | No screenshots, no activity monitoring, no background service. Every command is a one-shot process. |

---

## Install

Windows 10/11 (macOS and Linux work too), Python 3.10–3.12, ~3 GB free disk.

```bash
git clone https://github.com/retr0alfred/ContextFS.git
cd ContextFS
python -m venv .venv
```

Install the **CPU-only** PyTorch wheels first — ContextFS never uses a GPU, and
the default index would pull several gigabytes of CUDA:

```bash
.venv\Scripts\python.exe -m pip install "torch>=2.2,<3" --index-url https://download.pytorch.org/whl/cpu
```

Then the package, the language model, and the demo corpus:

```bash
.venv\Scripts\python.exe -m pip install -e ".[dev,datagen]"
.venv\Scripts\python.exe -m spacy download en_core_web_md
.venv\Scripts\python.exe scripts\generate_corpus.py
```

## Use

```bash
contextfs scan
contextfs query "the PDF I studied before my ML exam" --explain
contextfs query "notes from the ML exam" --compare     # baseline vs ContextFS
contextfs timeline "March to April"
contextfs digest
```

Full walkthrough in **[guide.md](guide.md)**.

## Reproducing the results

```bash
python scripts/evaluate.py           # retrieval metrics + full ablation table
python scripts/date_eval.py          # meaningful vs incidental dates
python scripts/session_eval.py       # activity session accuracy
python scripts/entity_eval.py        # entity extraction
python scripts/incremental_check.py  # incremental correctness and speed-up
```

---

## Strengths

- **The central result is real and is concentrated where theory predicts.**
  Temporal queries improve 5× in MRR; purely semantic queries barely move.
- **Meaningful-date classification is the strongest single contribution.**
  F1 0.986 versus 0.700 for naive extraction, from four weighted signals rather
  than a keyword list.
- **Every threshold was measured, not guessed**, with the measurement table
  recorded inline in `contextfs.toml` beside the value it justifies.
- **Three specified approaches were measured to be wrong and replaced** —
  cosine-based duplicate detection (found 0 of 2 known pairs), the all-pairs
  session gate (made every real session impossible), and the smaller spaCy
  model. Each replacement is logged with its tradeoff rather than silently
  substituted.
- **Explainability is enforced, not decorative.** 100% coverage across all six
  configurations, and the explanation reports the actual scoring arithmetic.
- **Genuinely fast on weak hardware.** 92 ms median query, 18 ms warm re-scan,
  no GPU, no background service.
- **Honest reporting throughout.** Where a layer contributes little, the README
  says so; entity F1 is reported at 0.595 rather than quietly dropped.

## Weaknesses — unsolved, and stated plainly

These are limitations of the work as it stands, not to-do items.

1. **The corpus is synthetic and self-authored — this is the big one.** 40 files
   whose ground truth was written by the same people who wrote the system. It
   makes the numbers reproducible and checkable; it also makes them
   unrepresentative of real personal data. Nothing here has been validated on
   real files. The full argument, and what validation would require, is in
   [docs/real-data-migration.md](docs/real-data-migration.md).

2. **n=17 queries.** Per-kind cells contain 2–4 queries each. A ±0.10 MRR swing
   on a 3-query cell is one query changing rank. The per-kind breakdown is
   directional evidence, not a significance test, and no significance test is
   claimed.

3. **The activity layer barely moves the headline metric** (+0.000 MRR over
   graph alone). It earns its place on activity-kind queries and on recall, but
   the honest reading is that the temporal layer is carrying the result.

4. **Hit@1 did not improve at all** (0.412 for every configuration). ContextFS
   gets more right answers *into* the top 10 and orders the top 5 better, but it
   does not make the single first result more often correct.

5. **Session clustering is O(n²) and global.** Incremental update has a ~450 ms
   floor that does not shrink with fewer changes. At tens of thousands of files
   this dominates and would need a design change — time-windowed blocking with
   periodic global reconciliation — that is not implemented.

6. **Entity extraction is the weakest layer** (F1 0.595). Precision 0.489 means
   roughly half of extracted entities are wrong. It contributes to retrieval
   anyway because the consensus and gazetteer passes suppress the worst of it,
   but it is not good.

7. **Activity modelling depends entirely on mtime**, which cloud sync, bulk
   copies, and restores routinely destroy. Where that has happened, the layer is
   silently useless — there is no detection and no repair.

8. **English-only, text-only.** No OCR (scanned PDFs are invisible), no
   multilingual models, no `.eml`/proprietary formats.

9. **Relevance feedback is unevaluated.** It is provably bounded — measured:
   one pick adds exactly half the cap, six picks cannot overturn a clear win —
   but there is no measurement that it *helps*, because with one user there is
   no honest way to produce one.

10. **No desktop GUI yet.** The CLI is complete; the native Windows application
    and the 3D graph visualisation are not built.

---

## Stack

Python 3.10–3.12 · spaCy · transformers / sentence-transformers · LanceDB ·
NetworkX · SQLite · Typer · Rich · pytest · Black · Ruff. CPU-only throughout.
Optional local Ollama for abstractive summaries, off by default.

Exact pins in [`requirements.lock.txt`](requirements.lock.txt).

## License

MIT.
