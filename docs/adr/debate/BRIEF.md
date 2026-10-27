# Architecture debate brief (v2 Phase 2 · log.md Phase 30)

You are one role in a design review for **ContextFS**, a final-year B.E. CSE project that will become
an IEEE paper. Read this brief fully. You may read anything under `src/contextfs/`, `tests/`,
`scripts/`, `contextfs.toml`, `pyproject.toml`, `README.md`, `docs/architecture.md`, and `log.md`.
Use web search to check **current** (late-2026) facts about tools and libraries, and cite a URL for
each such claim. Do not rely on memory for version numbers or tool capabilities.

**Hard rules for every role:**
- Do not install anything, run GPU workloads, download models, or modify any file except your own
  output file.
- Small read-only commands are fine (`grep`, reading files, `python -c` that imports nothing heavy).
- Do not read `docs/AUDIT_AND_ROADMAP.md` or `docs/research/`. They are private and the facts you
  need are below.

## 1. What ContextFS is today (v1, as audited)

A local-first Python system that re-finds personal files from memory-style natural-language
queries ("the PDF I studied before my ML exam"). Pipeline (`src/contextfs/`):

- `scanner.py`: `os.walk` from **one** root; size+mtime_ns, then xxh3 change detection; tombstones.
- `extract/`: structure-preserving text blocks (PDF/DOCX/PPTX/XLSX/MD/code/CSV; images
  metadata-only).
- `entities.py`: spaCy `en_core_web_md` NER, regex date mentions, frequency keywords.
- `embed.py`: MiniLM-L6 (384-d) chunk + mean-pooled document vectors in LanceDB (brute force, no ANN
  index). Device hard-coded to `"cpu"`.
- `temporal/classify.py`: rule-based "meaningful vs incidental" date classifier.
  `temporal/timeline.py`: interval tree over meaningful dates plus an NL date-range resolver.
- `activity.py`: work-session reconstruction by average-linkage clustering on mtime, cosine, entity
  and folder affinity. **O(n³) time, three dense n×n matrices.**
- `graph.py`: NetworkX MultiDiGraph (semantic/entity/structural/duplicate/temporal/activity edges),
  saved as one JSON. Dense n×n similarity to build semantic edges.
- `tree.py`: folder-skeleton "semantic tree" with extractive summaries. **Never used by retrieval.**
- `query.py`: rule-based decomposer: 16 hard-coded format words, 21 hard-coded "activity cue"
  phrases, regex time expressions. **Closed vocabulary.**
- `retrieval.py`: seeds (dense top-10, entity substring, timeline), session expansion, ≤2-hop graph
  BFS, weighted 4-signal score + format multiplier. Loads **all** document vectors, the entity index
  and session membership **per query**. No BM25 / lexical channel, no reranker.
- `store.py`: SQLite (WAL), migrations via `PRAGMA user_version`; read-only opens skip migrations.
- `cli/main.py` (1,507 lines): Typer CLI. **The indexing orchestration lives here.**
- `gui/`: PySide6-Essentials desktop app (search / insights / index tabs). Calls the CLI's private
  `_run_*` helpers for scanning; one worker thread; single root; no first-run flow; no cancellation;
  no single-instance lock. A 3D graph view is exported as a standalone three.js HTML file.
- Tree, graph, dates and sessions are **rebuilt wholesale on every scan**. Scan rows are committed
  only at the end of the walk (no resume). `os.walk` has no `onerror`, so permission-denied
  directories vanish silently. Hashing reads every changed file, which would **download OneDrive
  cloud-only placeholders**. Ignore rules match a directory *name* at any depth.
- Tests: 477 pass. Some read the developer's live index instead of building their own.

## 2. Measured on the development machine (2026-10-27, smoke corpus of 40 files)

- Full index build: 19.8 s (most of it model load + spaCy + embedding). Incremental 1+1+1 change:
  563 ms.
- Model warm-up in one process (MiniLM + spaCy): 16.7 s. Warm query median: 32 ms (CPU).
- Installed sizes: see §7 (filled in by the facilitator).
- Machine: Ryzen 7 250 (8C/16T), **16 GB single-channel RAM**, RTX 5060 Laptop 8 GB VRAM
  (Blackwell), Windows 11. **Target users have no GPU and possibly 8 GB RAM.**

## 3. The product we are designing (v2)

A normal Windows desktop app: install it, double-click it, use it, like Chrome.

- **First run:** ask for the corpus location: one folder or several, possibly a whole drive. Save
  it. Never ask again unless changed in Settings (add/remove/change roots any time).
- **Every launch:** check saved roots for new, changed and deleted files and update the index
  incrementally: embeddings, entities, dates, sessions, graph, everything. The UI stays responsive.
  *Open question: does search wait for the update, or run on the existing index with a visible
  progress indicator?*
- **Search:** any free-form natural-language query, including ones never seen before. Typos,
  vague time ("last sem", "around Diwali", "before the exam"), paraphrase, any length, non-English.
  It never crashes and always returns explained results or a clear "nothing found".
- **Results:** file, snippet, *why it matched* (a faithful explanation), and actions: open file, open
  folder. Timeline, sessions and graph views only if they earn their place.
- **Settings:** roots, exclusions (editable defaults), GPU on/off, model choice.
- **Must run on a clean Windows machine with no Python, offline after installation.** GPU detected
  and used if present; clean CPU fallback.
- One **engine library** that the GUI, the CLI and the evaluation harness all call, with
  **identical results** across the three (a test will prove it).
- **Core principles (unchanged unless a decision argues otherwise):** local-first (no cloud calls,
  no telemetry), read-only on user files, every result explained, incremental indexing.
- **Time evidence must be first-class.** The index must keep, per file, timestamps from several
  sources: file-system created/modified/accessed; Office `docProps/core.xml`; PDF Info/XMP dates;
  EXIF `DateTimeOriginal`; zip entry times. It must be able to tell when file-system times were
  rewritten (copies, cloud sync, restores, archive extraction). A single `mtime` column is no
  longer acceptable. Sessions and the timeline must be able to use a *reconciled* time per file.
- **Evaluation hooks:** v2 Phase 3 will build a procedurally generated benchmark (10²–10⁴+ files per
  persona, seeded) and run baselines on **the same index and candidate pools**: filename/path +
  mtime heuristic, BM25, dense-only (current + a stronger small model), BM25+dense fusion,
  BM25+dense+cross-encoder rerank, optionally a local-LLM reranker. ContextFS must be expressible
  as signals/features on top of the same first stage. Ablations switch layers off without
  rebuilding.

## 4. Questions the debate must settle (with trade-offs written down)

1. **UI stack:** keep PySide6 or switch? Justify against packaging size and startup time.
2. **Process model:** single process, or UI + background indexer process? A file watcher while the
   app is open, or only the scan at launch? The current principle says "no background service":
   is a watcher that runs only while the app is open consistent with it?
3. **Packaging:** PyInstaller vs Nuitka vs other; one-dir build + installer (e.g. Inno Setup);
   bundling model weights so the first run works offline; CUDA in the bundle (separate GPU build,
   or optional runtime detection/download?).
4. **Whole-drive realities:**
   - excluding system/app folders (Windows, Program Files, AppData, node_modules, .git, venvs,
     caches) by default, editable;
   - OneDrive/Dropbox **cloud-only placeholders must not be downloaded** just by reading them;
   - junction/symlink loops; paths > 260 chars;
   - locked files, permission denied; files changing during a scan;
   - removable/network roots that come and go;
   - very large and encrypted files;
   - resuming a scan after a crash or sleep.
5. **Scale:** 10⁵–10⁶ files. Session clustering must stop being O(n²)+ over the whole corpus
   (time-windowed blocking + periodic reconciliation, or better). Vector index choice. Memory
   limits on 16 GB single-channel RAM (and 8 GB target machines).
6. **Index safety:** schema migrations, corruption recovery, preventing two instances, index
   location (`%LOCALAPPDATA%` by default, never inside the user's folders; configurable so a
   developer can keep it off C:).

Also in scope: the engine/library boundary and API; the storage layout (what lives in SQLite vs a
vector store vs elsewhere; whether the graph stays NetworkX/JSON); how the multi-source time
evidence is stored and reconciled; how query understanding becomes open-vocabulary while staying
local and fast; GPU/CPU model strategy and memory budgets; how explanations stay faithful.

## 5. Roles

- **Systems architect:** process model, indexing pipeline, storage, engine API.
- **IR/ML researcher:** retrieval quality, model choices, query understanding, evaluation hooks,
  explanation faithfulness.
- **Windows/packaging engineer:** installer, bundling, paths, permissions, updates, CUDA delivery.
- **Performance engineer:** startup time, whole-drive scans, memory on 16 GB (and 8 GB), GPU vs CPU.
- **UX lead:** first-run flow, search UX, how explanations are shown, progress and status.
- **Red-team skeptic:** attacks every proposal and hunts for failure modes.

## 6. Output format (proposers)

Write your file (path given in your task) in Markdown, **≤ 1,800 words**:

1. **Position summary** (5 bullets).
2. **Your proposals** on the questions you own, each with: decision · alternatives considered ·
   trade-offs · what would change your mind · a measurement that would settle it.
3. **Positions on the other questions** (brief).
4. **Risks / failure modes you see in your own proposal.**
5. **Sources** (URLs for every external factual claim).

## 7. Installed sizes on the dev machine (facilitator-measured)

| Component | Installed size |
|---|---|
| `torch` 2.14.1+cu130 (total) | 2.9 GB |
| …of which CUDA/NVIDIA libs in `torch/lib` | 2,425 MB |
| PySide6 (Essentials) | 209 MB |
| lancedb | 148 MB |
| spacy + `en_core_web_md` | 87 + 54 MB |
| scipy / pyarrow / transformers / sklearn / numpy | 83 / 81 / 49 / 26 / 20 MB |
| MiniLM-L6 weights (`models/`) | 88 MB |
| **whole `.venv` (incl. dev tools)** | **3,883 MB** |

A CPU-only torch wheel is far smaller (the CUDA libs are ~83% of the torch install). Verify current
CPU-wheel sizes yourself if your argument depends on them.
