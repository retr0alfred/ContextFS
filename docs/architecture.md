# ContextFS architecture

How the eleven specified layers map onto the code that exists, what each one
actually does, and where the design departs from the original specification.

Every deviation listed here is recorded with its reasoning and its measurement
in [log.md](../log.md); this document is the map, log.md is the argument.

---

## The shape of the system

```
                    ┌───────────────────────────────┐
   your files ─────▶│  L1  Scanner                  │  read-only, xxh3 hashing
   (never written)  └───────────────┬───────────────┘
                                    ▼
                    ┌───────────────────────────────┐
                    │  L2  Content extraction       │  pdf docx pptx xlsx md py …
                    └───────────────┬───────────────┘
                                    ▼
            ┌───────────────────────┼───────────────────────┐
            ▼                       ▼                       ▼
  ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────────┐
  │ L3  Entities     │   │ L4  Embeddings   │   │ L7  Temporal         │
  │ people orgs      │   │ MiniLM, LanceDB  │   │ meaningful vs        │
  │ dates keywords   │   │                  │   │ incidental dates     │
  └────────┬─────────┘   └────────┬─────────┘   └──────────┬───────────┘
           │                      │                        │
           │            ┌─────────▼─────────┐              ▼
           │            │ L5 Semantic tree  │   ┌──────────────────────┐
           │            │ RAPTOR-inspired   │   │ L8  Activity         │
           │            └─────────┬─────────┘   │ session clustering   │
           │                      │             └──────────┬───────────┘
           └──────────┬───────────┘                        │
                      ▼                                    │
          ┌───────────────────────────┐                    │
          │ L6  Relationship graph    │◀───────────────────┘
          │ NetworkX MultiDiGraph     │  temporal + activity edges
          └─────────────┬─────────────┘
                        ▼
          ┌───────────────────────────┐
          │ L9  Hybrid retrieval      │  4 weighted signals, re-normalised
          │     + explanation         │  every result explains itself
          └─────────────┬─────────────┘
                        ▼
          ┌───────────────────────────┐
          │ L10 CLI    │  L11 GUI     │
          └───────────────────────────┘
```

Nothing above L1 ever writes to your files. Everything derived lives in
`.contextfs/`: one SQLite database, one LanceDB directory, one graph JSON.

---

## Layer-by-layer map

| # | Layer | Module | Persistence | Status |
|---|---|---|---|---|
| 1 | File scanner | [`scanner.py`](../src/contextfs/scanner.py) | `files`, `scan_runs` | ✅ |
| 2 | Content extraction | [`extract/`](../src/contextfs/extract/) | `documents`, `blocks` | ✅ |
| 3 | Entity extraction | [`entities.py`](../src/contextfs/entities.py) | `entities`, `keywords`, `date_mentions` | ✅ |
| 4 | Embeddings | [`embed.py`](../src/contextfs/embed.py) | LanceDB `vectors.lance` | ✅ |
| 5 | Semantic tree | [`tree.py`](../src/contextfs/tree.py), [`summarize.py`](../src/contextfs/summarize.py) | `tree_nodes` | ✅ |
| 6 | Relationship graph | [`graph.py`](../src/contextfs/graph.py) | `graph.json` | ✅ |
| 7 | Temporal intelligence | [`temporal/`](../src/contextfs/temporal/) | `classified_dates` | ✅ |
| 8 | Activity sessions | [`activity.py`](../src/contextfs/activity.py) | `sessions`, `session_members` | ✅ |
| 9 | Retrieval & ranking | [`retrieval.py`](../src/contextfs/retrieval.py), [`query.py`](../src/contextfs/query.py) | — | ✅ |
| 10 | CLI | [`cli/main.py`](../src/contextfs/cli/main.py) | `feedback`, `last_query.json` | ✅ |
| 11 | Desktop GUI | [`gui/`](../src/contextfs/gui/) | `graph3d.html` | ✅ |

Supporting modules outside the layer stack: [`config.py`](../src/contextfs/config.py)
(single source of every threshold), [`store.py`](../src/contextfs/store.py)
(SQLite with versioned migrations), [`evaluation.py`](../src/contextfs/evaluation.py)
(the measurement harness), [`insights.py`](../src/contextfs/insights.py)
(read-only projections: duplicates, projects, digest, tags),
[`datagen/`](../src/contextfs/datagen/) (the authored corpus and its ground truth).

---

### L1 — File scanner

Walks the configured root, records size/mtime/extension, and content-hashes with
**xxh3_128** rather than a cryptographic hash: this is change *detection*, not
integrity verification, and xxh3 is several times faster on the target CPU.

Change detection is three-way — new, modified, deleted — with deletions
tombstoned rather than dropped, so a file that reappears keeps its history.
Unchanged files take a single-column `touch_seen` update rather than a full
upsert; this one change took a warm re-scan from 522 ms to 18.2 ms.

### L2 — Content extraction

One extractor per format behind a registry, all returning `ExtractedDocument`
built from `ExtractedBlock`s. Blocks carry structural facts the later layers
need — is this a heading, is this a table row, what is its char span — because
the temporal layer's accuracy depends on knowing whether a date sat under a
heading called "Deadlines" or inside a copyright footer.

### L3 — Entity extraction

spaCy `en_core_web_md` (not `sm`: measured entity F1 0.595 vs 0.472), plus
regex date-surface extraction, plus TF-IDF keywords. Two corpus-level passes
correct per-document mistakes: **consensus categories** (a name labelled PERSON
in nine files and ORG in one becomes PERSON everywhere) and a **gazetteer**
propagating confident entities into files where spaCy missed them.

### L4 — Embeddings

`all-MiniLM-L6-v2`, 384-dimensional, CPU-only, loaded offline. Chunked at block
boundaries so a chunk never straddles a table and a paragraph. Stored in LanceDB
at both chunk and document granularity.

Deviation: the primary backend is `transformers` rather than `sentence-transformers`
(startup cost and dependency weight on this hardware). `sentence-transformers`
is retained as a **correctness oracle** — a test asserts both backends agree to
1e-4 cosine, so the substitution is verified rather than assumed.

### L5 — Semantic tree

RAPTOR-inspired hierarchy: chunk → file → folder → project, each level
summarised. Summarisation is extractive by default and never invents text;
a local Ollama endpoint can be enabled, restricted to loopback addresses, and is
off by default so the system never depends on it.

### L6 — Relationship graph

A NetworkX `MultiDiGraph` with six edge types: `semantic`, `entity`,
`structural`, `duplicate`, `temporal`, `activity`. Directed because temporal and
activity relations genuinely are ("edited before" has a direction).

Deviation: near-duplicate edges use **5-word shingle Jaccard**, not embedding
cosine. Measured: at the specified cosine ≥ 0.95 threshold, zero duplicate pairs
were found in a corpus containing two authored duplicate pairs — the cosine
margin between duplicates and non-duplicates was 0.019, versus a Jaccard margin
of 0.372. Cosine similarity does not separate near-duplicates from
same-topic documents; shingle overlap does.

### L7 — Temporal intelligence

**The layer that carries the most novelty.** Classifies every extracted date as
*meaningful* (a deadline, an exam, a meeting) or *incidental* (a copyright year,
a footer stamp, a version string) using four weighted signals — keyword context,
structural position, file-metadata proximity, and cross-file recurrence —
behind a precision gate.

Measured: **P 0.972 / R 1.000 / F1 0.986**, against F1 0.700 for naive date
extraction. The single largest gain came from making the classifier aware of the
*heading chain* above a date rather than just the nearest line.

`timeline.py` builds an interval tree over meaningful dates and resolves
natural-language ranges ("March to April", "around the capstone deadline")
against what the index actually contains, so ambiguous ranges are disambiguated
by evidence rather than by convention.

### L8 — Activity sessions

Reconstructs *what you were working on together* from mtime clustering plus
semantic, entity and folder similarity (weights .40/.30/.20/.10), agglomerative
with average linkage.

Deviation: the specified all-pairs temporal gate was **measured to make every
real session impossible to form** — the gap distribution inside genuine sessions
exceeded the gate. Replaced with an idle-gap gate between clusters. Measured
afterwards: **P 1.000 / R 1.000 / F1 1.000**, 5/5 sessions recovered, the
adversarial case (two different projects worked on the same afternoon) correctly
separated, and the negative control never clustered.

### L9 — Retrieval and ranking

Four signals — semantic, graph, activity, timeline — weighted and
**re-normalised over whichever are enabled**, so ablation scores stay comparable
in scale. Seeds come from four independent routes; the graph expands them; a
query decomposer reads format hints, temporal spans and activity cues.

A format hint ("the PDF", "that spreadsheet") is applied as a bounded
multiplier outside the weighted mix — 1.15 up, **0.70 down**. The asymmetry is
deliberate and measured: naming a format is strong evidence against files of
other types and weak evidence for any given file of that type. This was 0.85
until end-to-end testing found it too weak to honour a stated constraint; the
correction raised full MRR 0.585 → 0.632 and hit@1 0.412 → 0.471, and revealed
that the activity layer's apparent zero contribution had been a masking effect
(log.md, Decision 84).

Two things are enforced rather than encouraged:

- **Every result explains itself.** `Explanation.is_complete` requires at least
  one substantive reason plus the scoring arithmetic. Explanation coverage is
  measured at **100% across all six ablation configurations.**
- **Query-adaptive weighting.** Entity-style queries were measured *degrading*
  under context layers; weights now shift only when the query itself carries the
  relevant cue.

Feedback (Phase 19) is applied **after** ranking as a bounded, saturating
re-rank, never as a fifth signal, and is inert unless a feedback store is passed
— the evaluation harness passes none, so measured numbers cannot be inflated by
recorded clicks.

### L10 — CLI

Typer + Rich. Commands: `scan`, `query`, `timeline`, `explain`, `stats`,
`feedback`, `duplicates`, `projects`, `digest`, `tags`, `reset`, `config`,
`fetch-models`.

One hard constraint: **no heavy import at module scope.** Importing torch costs
seconds on the target CPU, which would make `--help` feel broken. A test asserts
that importing the CLI pulls in none of torch, spacy, transformers, lancedb.

### L11 — Desktop GUI

A native Qt (PySide6) Windows application — no embedded browser, no local
server, no JavaScript in the application itself. Three tabs: Search (results
beside their reasoning), Insights, and Index.

Its reason to exist is measured rather than aesthetic: model load costs ~23 s,
and the one-shot CLI pays that on every invocation. The GUI pays it once and
keeps the retriever resident, so every query afterwards is warm (~75 ms). All
work runs on a **single** background thread — both so the shared index handle is
never touched concurrently, and because torch already uses several cores.

Verified not to be a fork of the CLI: **17/17 ground-truth queries return
identical results and identical scores to 1e-9.**

The 3D relationship graph (`contextfs visualise`) is a *generated* self-contained
HTML file with three.js inlined — nothing is fetched, and the output is portable
to a machine without ContextFS. Force-directed layout with sampled repulsion
(O(n·k), not O(n²)) so it stays interactive on integrated graphics.

---

## Data flow through one query

```
"the PDF I studied before my ML exam"
   │
   ├─ decompose ──▶ format hint: pdf · temporal cue: "before" · activity cue: "studied"
   │
   ├─ seed ───────▶ semantic NN · entity index · timeline range · session match
   │
   ├─ expand ─────▶ graph traversal, ≤2 hops, edge types filtered by enabled signals
   │
   ├─ score ──────▶ w_sem·semantic + w_graph·graph + w_act·activity + w_time·timeline
   │                (weights re-normalised; format hint as a bounded multiplier)
   │
   ├─ re-rank ────▶ bounded feedback boost, outside the signal mix
   │
   └─ explain ────▶ matched topic · shared entities · session · dates · graph path
```

---

## Storage

| Store | Contents | Why |
|---|---|---|
| SQLite (`contextfs.db`) | Files, documents, blocks, entities, dates, sessions, tree, feedback | Relational queries, transactions, versioned migrations via `PRAGMA user_version` (currently v8) |
| LanceDB (`vectors.lance`) | Chunk and document vectors | Columnar ANN search without a server |
| JSON (`graph.json`) | The relationship graph | NetworkX round-trips it losslessly; human-inspectable |

WAL journaling lets a reader (a GUI, or `stats`) run while a scan writes.
`synchronous = NORMAL`, not `FULL`: this is a rebuildable derived index, not a
ledger — losing the last transaction to a power cut costs one re-scan.

---

## Design rules the code is held to

1. **Read-only on user files.** Enforced by test, not by convention.
2. **Local-first.** No cloud calls, no telemetry, no paid APIs. The one optional
   network feature (Ollama summarisation) refuses non-loopback endpoints.
3. **Every result explains itself.** An empty explanation fails `is_complete`.
4. **Every threshold is measured.** Each one carries its measurement table
   inline in `contextfs.toml` next to the value.
5. **Deviations are flagged, never silent.** Where the specified approach was
   measured to be wrong, the replacement, the measurement, and the tradeoff are
   all recorded in log.md.
6. **Numbers are never estimated.** Every figure in this repository comes from a
   script that can be re-run.
