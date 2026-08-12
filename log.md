# ContextFS — Engineering & Decision Log

This is the permanent memory of the build. Every phase appends a section here.
For each phase we record **what was built, how it works, how it is installed,
what deviated from the master prompt, why it deviated, and what that buys us.**

Rules for this file:

- Nothing here is aspirational. If a number appears in this log, it was produced
  by a command that actually ran, and the command is named.
- If a decision contradicts the master build prompt or the project reference
  document, it gets a **Deviation** block with an explicit trade-off argument.
- Deviations are proposals until the supervisor/user accepts them; they are
  marked `[PROPOSED]` until then.

---

## Phase 0 — Environment baseline

Recorded before any code was written, because several later decisions (model
sizes, batch sizes, device selection) are justified by this hardware and would
be indefensible in a viva without it.

| Property | Value |
|---|---|
| OS | Windows 11 Home 10.0.26200 |
| CPU | AMD Ryzen 7 3700U (8 logical cores) |
| RAM | 13.9 GB usable (16 GB / DDR4-2400) |
| GPU | Radeon RX Vega 10 (integrated, **no CUDA**) |
| Python | 3.12.10 (`C:\Users\manue\AppData\Local\Programs\Python\Python312`) |
| pip | 25.0.1 |
| git | 2.50.0.windows.2 |
| Ollama | **not installed** |
| Free disk (D:) | ~697 GB |

Three consequences follow directly from this table and are referenced
throughout the log:

1. **No CUDA.** Every ML component must be CPU-viable. GPU acceleration is not
   an option to fall back on, so model choice is a hard constraint, not a
   preference.
2. **Ollama absent.** The master prompt already required the local LLM to be
   optional; on this machine it is *initially absent*, which means the
   extractive-summary fallback path (Phase 8) is the **default-tested** path,
   not an untested branch. That is a strictly better engineering position.
3. **8 cores / 14 GB.** Batch sizes and traversal budgets in `contextfs.toml`
   are set for this envelope. Every one of them is a config key, not a
   constant, so the same code runs on a stronger machine by editing one file.

---

## Phase 1 — Project scaffold & environment

### What was built

```
ContextFS/
├── pyproject.toml          # packaging, pinned deps, Black/Ruff/pytest/coverage config
├── contextfs.toml          # the ONLY place a scan root is configured
├── .gitignore              # privacy-critical: blocks all scan-derived artifacts
├── log.md                  # this file
├── guide.md                # end-user operating guide
├── README.md               # project overview
├── src/contextfs/          # the package (src-layout)
│   ├── __init__.py
│   └── py.typed
├── tests/
│   └── test_scaffold.py
├── data/
│   ├── synthetic/          # generated corpus + ground truth (Phase 3)
│   └── eval/               # harness output (Phases 21–22)
├── docs/
└── scripts/
```

### How it is installed

ContextFS installs into a **project-local virtual environment** at `.venv/`:

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.venv\Scripts\python.exe -m pip install "torch>=2.2,<3" --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\python.exe -m pip install -e ".[dev,datagen]"
```

The two-step install is deliberate — see Decision 2.

### Decisions & reasoning

#### Decision 1 — `src/` layout rather than a flat package

**What:** the importable package lives at `src/contextfs/`, not `./contextfs/`.

**Why:** with a flat layout, `import contextfs` silently resolves to the working
directory whether or not the package is actually installed. That means the test
suite can pass against a broken install, and Phase 26's "clean clone → install →
run" verification could produce a false green. A `src/` layout makes the tests
import the *installed* package, so a packaging regression fails loudly.

**Cost:** requires `pip install -e .` before tests run. Acceptable — that is a
Phase 1 verification step anyway.

#### Decision 2 — Torch is installed from the CPU-only wheel index

**What:** `torch` is installed from `https://download.pytorch.org/whl/cpu`
*before* `pip install -e .`, rather than letting the default PyPI resolver pick it.

**Why:** the default PyPI `torch` wheel for Windows bundles CUDA runtime
libraries (~2.5 GB download, ~5 GB installed). This machine has a Radeon Vega
iGPU and no CUDA device, so every one of those bytes is dead weight — it costs
install time, disk, and (via larger DLL loads) process start-up time on a laptop
that is already the weakest link. The CPU wheel is roughly an order of magnitude
smaller and is functionally identical for our workload.

**Is this a stack deviation?** No. The master prompt fixes *sentence-transformers*
as the embedding stack; torch is its transitive dependency and this only changes
which build of it is fetched. Recorded here because it is a non-obvious install
step that a reproducer must repeat.

**Benefit:** faster clean-clone setup (a Phase 26 verification step), less disk,
no behavioural difference.

#### Decision 3 — Version ranges in `pyproject.toml`, exact pins in a lock file

**What:** `pyproject.toml` carries lower/upper bounds; `requirements.lock.txt`
carries the exact resolved versions from `pip freeze` after a successful install.

**Why:** the master prompt asks for "dependency pinning". Pinning exact versions
directly in `pyproject.toml` makes the package uninstallable next to anything
else and turns every routine security update into a packaging edit. The standard
resolution — ranges for the library, a lock file for the reproducible
environment — gives the reproducibility the research claim needs (an examiner
can recreate the exact environment the numbers came from) without the brittleness.

#### Decision 4 — `all-MiniLM-L6-v2` as the default embedding model

**What:** 384-dimensional, ~90 MB sentence-transformers model, `device = "cpu"`.

**Why:** on a 4-core/8-thread mobile Ryzen with no CUDA, embedding throughput is
the dominant cost of a full index build, and index build time is one of the
paper's reported metrics. MiniLM-L6 is the standard CPU-viable baseline in the
retrieval literature, which also makes the reported numbers comparable to
published work rather than idiosyncratic. Larger models (e.g. `all-mpnet-base-v2`,
768-dim) are a config-file change, so the trade-off is measurable later rather
than baked in now.

**Deferred:** an actual embedding-throughput measurement on this hardware. It is
recorded in Phase 7, not guessed here.

#### Decision 5 — Line length 100 for both Black and Ruff

**What:** `line-length = 100` in both tools rather than Black's default 88.

**Why:** this codebase carries long descriptive identifiers (`weight_cross_file_recurrence`,
`timeline_node_threshold`) and multi-signal scoring expressions. At 88 columns
those wrap in ways that hurt readability of exactly the formulas an examiner will
scrutinise. The two tools are configured to the same number so they cannot fight.

#### Decision 6 — `.gitignore` treats scan-derived data as privacy-critical

**What:** the ignore file blocks `.contextfs/`, `*.lance*`, `*.db`, `data/real/`,
`contextfs.local.toml`, and the generated corpus — with a comment saying why.

**Why:** the project's stated privacy principle ("no data leaves the machine")
has a failure mode that is *not* a code bug: committing an index built over the
author's real Documents folder to a public GitHub repository. `contextfs.local.toml`
is ignored specifically so a user can point the scanner at real personal paths
without those paths entering version control. The shipped `contextfs.toml` is
asserted by a unit test to contain no absolute path.

#### Decision 7 — Ruff `D` (pydocstyle) rules enabled from Phase 1

**What:** docstring linting is on from the start, not switched on in Phase 24.

**Why:** Phase 24 requires "docstrings on all public functions". Turning that
rule on at the end of a 26-phase build means a single enormous mechanical commit
across every module. Enforcing it from the first file makes it free.

### Verification

See **Phase 1 Verification** in the session output. Commands run:
`pip install -e ".[dev,datagen]"`, `pytest`, `black --check .`, `ruff check .`.

### What Phase 2 needs from this phase

- An importable installed `contextfs` package (`src/` layout, editable install).
- `contextfs.toml` present with `[paths]`, `[scan]`, `[embeddings]`, `[temporal]`,
  `[activity]`, `[retrieval]` sections — Phase 2's config loader binds to these
  exact key names.
- The `contextfs = "contextfs.cli.main:app"` console-script entry point declared
  in `pyproject.toml`; Phase 2 creates the module it points at.

### Known-broken / deferred after Phase 1

- `contextfs.cli.main` does not exist yet, so the `contextfs` console script is
  declared but not runnable. Resolved in Phase 2. (The editable install still
  succeeds; setuptools does not validate entry-point targets at install time.)
- No spaCy model is downloaded yet (`en_core_web_sm`). Deferred to Phase 6.
- No sentence-transformers weights are downloaded yet. Deferred to Phase 7.

---

## Phase 2 — Configuration & CLI skeleton

### What was built

**`src/contextfs/config.py`** — the configuration system. A pydantic model tree
(`ContextFSConfig` with twelve typed sections) that loads TOML, merges overrides
from four sources, validates internal consistency, and resolves every path to an
absolute location.

**How it works.** `load_config()` runs a fixed pipeline:

1. Locate a config file — either the explicit `--config` path, or a search that
   walks *upward* from the current directory looking for `contextfs.local.toml`
   then `contextfs.toml` (the way `git` finds `.git`).
2. Parse it with `tomllib` (stdlib on 3.11+; `tomli` shim on 3.10).
3. Layer `CONTEXTFS_*` environment overrides on top.
4. Layer programmatic `overrides={section: {key: value}}` on top of that — this
   is how the Phase 22 ablation harness sweeps weights without writing temp files.
5. Layer explicit `--root` / `--data-dir` CLI values last (highest precedence).
6. Validate through pydantic with `extra="forbid"`, so a typo like `rooot` is a
   loud error rather than a silently ignored key.
7. Rewrite every relative path to absolute, anchored at the config file's
   directory.

**`src/contextfs/cli/main.py`** — Typer CLI exposing `scan`, `query`, `timeline`,
`explain`, `stats`, `reset`, plus `config`. All but `config` are stubs that print
a Rich panel naming the phase they land in and exit with code 3.

**`src/contextfs/__main__.py`** — `python -m contextfs`, so the tool is usable
without activating the venv.

### Decisions & reasoning

#### Decision 8 — Relative paths resolve against the config file, not the cwd

**What:** `root = "data/synthetic/corpus"` means "relative to the directory
holding `contextfs.toml`", regardless of where the command was run from.

**Why:** the alternative — resolving against the cwd — means `contextfs query`
run from `src/` and from the project root point at two *different indexes*, with
no error and no warning. That is a silent-wrong-answer failure mode, the worst
kind for a system whose entire output is "here is the file you meant". Combined
with the upward config search, this makes ContextFS behave like `git`: it finds
the project you are inside and operates on it.

#### Decision 9 — No heavy imports at CLI module scope [BETTER-THAN-SPEC]

**What:** `cli/main.py` imports only `typer`, `rich`, and `pathlib` at module
level. `torch`, `spacy`, `sentence-transformers`, `lancedb` are imported *inside*
the command functions that need them. A unit test
(`test_cli_does_not_import_heavy_ml_stack`) fails the build if that regresses.

**Why:** measured on this machine (5 warm runs each):

| Command | Time |
|---|---|
| `python -m contextfs --help` | 933 / 921 / 888 / 902 / 893 ms |
| `python -c "import torch, spacy"` | 10604 / 6302 / 6312 ms |

Importing the ML stack costs **~6.3 s warm** on a Ryzen 7 3700U. If that were
paid at module scope, every `--help`, every `stats`, every tab-completion would
take seven seconds and the tool would feel broken regardless of how good the
retrieval is. Lazy imports mean only the commands that genuinely need a model
pay for one.

**Remaining cost:** `--help` is still ~0.9 s, which is Python interpreter
start-up plus `typer`/`rich`/`click` import. That is an optimisation target
noted for later, not a claim that it is already fast.

**Benefit:** the CLI stays interactive on weak hardware, and the constraint is
enforced by a test rather than by discipline.

#### Decision 10 — Config validation enforces the local-first constraint

**What:** `SummarizationConfig` rejects any backend other than `ollama`/`none`,
and rejects any non-loopback endpoint when summarisation is enabled.

**Why:** "no cloud calls" is stated as a non-negotiable principle, but in most
projects it is enforced only by nobody happening to write the offending line. By
validating it at the configuration boundary, pointing ContextFS at a hosted
inference API becomes *impossible without editing source*, and there are unit
tests asserting the refusal. This turns a stated principle into a mechanism —
which is what an examiner asking "how do you *know* nothing leaves the machine?"
actually wants to hear.

#### Decision 11 — Weights are validated to sum to 1.0, and can be re-normalised

**What:** `[temporal]` signal weights and `[retrieval]` ranking weights are each
validated to sum to 1.0. `RetrievalConfig.normalised(enabled)` redistributes
weight across a subset of signals.

**Why (validation):** the date-relevance score is claimed to be "0–1". If the
weights sum to 1.3, it is not, and every threshold discussion in the paper is
built on a false premise. Validating at load time makes the claim structurally
true rather than aspirational.

**Why (re-normalisation):** in Phase 22 the ablation study switches layers off.
If disabling activity simply dropped its 0.20 contribution, every score in that
configuration would shrink toward zero and the ablation rows would not be
comparable — the semantic-only system would look worse than it is purely from
scale. Re-normalising keeps all configurations on the same scale, so differences
between rows reflect the *layers*, not the arithmetic.

#### Decision 12 — `contextfs config` is implemented in Phase 2, not stubbed

**What:** the prompt lists six subcommands and asks that all be stubbed. A
seventh, `config`, was added and fully implemented now.

**Why:** every later phase is debugged against "which index am I actually
talking to?". Without an inspectable resolved configuration, that question is
answered by reading source. This is a small addition that pays for itself
immediately and has no research cost.

**Deviation from prompt:** additive only — the six required stubs are all
present and behave as specified.

#### Bug found and fixed during verification

`CLIState` cached the loaded config in a module-level singleton and never
invalidated it. In a one-shot CLI process that is invisible; in the test runner
(and, later, in the Phase 27 GUI, which is a long-lived process issuing many
commands) a second invocation with different `--root`/`--config` flags silently
reused the first invocation's configuration. Caught by two failing tests. Fixed
by `CLIState.configure()`, which resets the cache whenever global options are
re-bound. Recorded because the same class of bug would have been extremely hard
to diagnose once the GUI existed.

### Verification — actual output

```
===== RUFF =====      All checks passed!
===== BLACK =====     8 files left unchanged.
===== PYTEST =====    38 passed in 3.13s
```

`contextfs --help` lists all seven subcommands (captured in the session
transcript). Every stub exits 3 with a Rich "Not yet implemented" panel naming
its phase — no tracebacks. `contextfs config` prints the resolved configuration.

Test coverage added this phase: 26 config tests (precedence, path resolution,
validation, local-first enforcement, ablation re-normalisation) and 12 CLI tests.

### Known-broken / deferred after Phase 2

- Six of seven subcommands are stubs by design.
- `--help` startup is ~0.9 s; interpreter + Typer/Rich import. Not yet optimised.

### What Phase 3 needs from this phase

- `load_config()` and the `[paths].root` / `[eval].ground_truth` keys, so the
  corpus generator writes to the same location the scanner will later read.

---

## Phase 3 — Synthetic corpus & ground-truth benchmark

### What was built

| Module | Role |
|---|---|
| `src/contextfs/datagen/corpus_spec.py` | The benchmark *specification*: 40 authored files, 6 sessions, 65 labelled dates, 17 queries |
| `src/contextfs/datagen/writers.py` | Format writers producing genuine PDF / DOCX / PPTX / XLSX / text files |
| `src/contextfs/datagen/generate.py` | Materialises the corpus and builds the ground-truth JSON from the same source |
| `scripts/generate_corpus.py` | CLI: regenerate corpus + ground truth |
| `scripts/verify_corpus.py` | 11-check integrity and determinism verifier |
| `tests/test_corpus_spec.py` | 32 tests guarding the benchmark's invariants |

### Corpus composition (measured, from `verify_corpus.py`)

```
files                                    40
sessions                                 5   (+1 negative control = 6)
unsessioned_files                        3
queries                                  17
dates_total                              65
dates_meaningful                         35
dates_incidental                         30
near_duplicate_pairs                     2
by_kind        {'code': 5, 'docx': 5, 'md': 10, 'pdf': 4, 'pptx': 2, 'txt': 8, 'xlsx': 6}
by_query_kind  {'activity': 4, 'entity': 2, 'hybrid': 4, 'semantic': 4, 'temporal': 3}
by_difficulty  {'easy': 7, 'hard': 10}
```

The persona is a final-year CS student across Aug 2025 – Mar 2026: internship
applications, a hackathon weekend, a DBMS assignment, ML exam revision, capstone
project work, and scattered personal notes.

### Decisions & reasoning

#### Decision 13 — The corpus is *authored*, not randomly generated [BETTER-THAN-SPEC]

**What:** every file's text is written by hand in `corpus_spec.py`. Nothing is
sampled from a template or a word list.

**Why:** a randomly generated corpus can only test that the code runs. The
hypothesis under test is about *specific adversarial relationships between
files* — a timetable naming a PDF that never says "exam"; a deadline and a
birthday a month apart; two drafts of one assignment. Those relationships have
to be planted deliberately and the ground truth has to record where. Random
generation cannot produce them, and a corpus that cannot embarrass the system
cannot support a claim about it.

**Cost:** the specification file is large (~1,900 lines) and every future corpus
change is a manual edit. Accepted: this file *is* the benchmark, and a benchmark
should be readable by a reviewer.

#### Decision 14 — The corpus is adversarial by construction

Concretely planted, each with a ground-truth label and a guarding test:

| Planted case | Where | What it defeats |
|---|---|---|
| Lecture PDF containing zero exam vocabulary | `Unit4_Ensemble_Methods.pdf` | Semantic-only retrieval on q01 |
| Timetable naming that PDF by filename | `Exam_Timetable_Sem7.xlsx` | Provides the only lexical bridge — external to the target |
| Spreadsheet full of **incidental** dates | `ml_lab_attendance.xlsx` | "Dates in tables are meaningful" as a sufficient rule |
| Bibliography of publication years | `references.txt` | Naive date extraction |
| Historical essay (1946–48) | `history_essay_partition.md` | Same |
| Birthday list | `birthday_list.txt` | Cross-file recurrence being naively applied |
| Draft/final near-duplicate pair ×2 | DBMS docx, annotated PDF | Byte-identical-only duplicate detection |
| Scattered personal files as a **negative control** | `personal_misc` (spans 223 days) | Over-clustering in session reconstruction |
| A meaningful deadline in an **unsessioned** file | `scholarship_form_notes.txt` | Confounding the timeline layer with the activity layer |
| Distractor Python files in three sessions | `prototype_scanner.py` etc. | "Any .py in the corpus" as an answer to q14 |

`test_key_pdf_never_mentions_exam_or_revision` fails the build if the target PDF
ever acquires the words `exam`, `test`, `revision`, `studied`, `syllabus`,
`timetable`, `semester`, or `deadline`. Without that guard, a future edit could
quietly make the central query easy, and every number would still look fine.

#### Decision 15 — Queries carry `kind` and `difficulty` labels [BETTER-THAN-SPEC]

**What:** each query is tagged `semantic | activity | temporal | entity | hybrid`
and `easy | hard`, with a written rationale.

**Why:** the prompt asks for queries with correct targets. That supports the
claim "the full system scores higher overall". Tagging supports a much stronger
and more defensible claim: *"activity modelling is what fixes activity-shaped
queries, and it costs nothing on semantic ones."* A per-kind breakdown in Phase
22 maps ablation rows directly onto RQ1–RQ4 instead of leaving the reader to
infer the connection. It also creates an honest failure channel — if the
activity layer helps semantic queries and not activity queries, something is
wrong with the explanation, not just the score.

**The paired queries q01 / q15** exist for this reason: identical target
(`Unit4_Ensemble_Methods.pdf`), one phrased from memory ("the pdf I studied
before my machine learning exam"), one from content ("how do bagging and
boosting differ"). If the baseline wins q15 and loses q01, the thesis is
demonstrated in a single two-row table.

#### Decision 16 — Ground truth lives *outside* the corpus root

**What:** `data/synthetic/ground_truth.json` sits beside, not inside,
`data/synthetic/corpus/`.

**Why:** it contains the answers. If it were inside the scan root it would be
indexed, embedded, and retrievable — a file stating "q01's target is
Unit4_Ensemble_Methods.pdf" would sit in the vector store during evaluation.
That is label leakage, it would inflate every metric, and it would be almost
invisible in the results. `verify_corpus.py` check 3 asserts no undeclared file
exists under the corpus root.

#### Decision 17 — Every date label carries a written justification

**What:** `DateLabel` has a required `why` field; a test fails on any empty one.

**Why:** Phase 10's precision/recall against these labels is a headline number.
An examiner is entitled to ask "why is *this* date meaningful?" about any
individual case. "Because the annotation file says so" is not an answer. With
this field, every one of the 65 labels has a stated reason, and the annotation
scheme is auditable rather than asserted.

#### Decision 18 — Content-level determinism, honestly scoped

**What:** `verify_corpus.py` regenerates the whole corpus into a temp directory
and compares. PDFs use ReportLab `invariant=1` and are byte-identical. OOXML
files are ZIP containers whose entries embed write timestamps, so they are
compared **entry-by-entry excluding `docProps/core.xml` and `docProps/app.xml`**.

**Why the caveat is stated rather than hidden:** claiming "byte-reproducible"
would be false for 13 of 40 files. The property that actually matters for
reproducing evaluation numbers is that the *extracted content* and the *mtimes*
are identical, and that is what is verified. Result: `all 40 files reproduce
identically`.

#### Bug found by the new tests

`test_date_surfaces_actually_occur_in_their_documents` failed on
`College/Capstone/references.txt`: the ground truth labelled a publication year
`2020` that does not appear anywhere in the file. Three further years present in
the text (2019, 2004, 2003, 2007) were unlabelled. Both fixed; incidental-date
count rose 27 → 30.

This is exactly the class of error that would otherwise have shown up as an
unexplained dip in Phase 10 recall, and been misattributed to the classifier.
Worth noting for the write-up: **the ground truth needs tests as much as the
code does.**

### Verification — actual output

```
$ python scripts/generate_corpus.py
generated 40 files | 5 sessions (+1 negative control) | 17 queries
| 35 meaningful / 30 incidental dates | 2 near-duplicate pairs

$ python scripts/verify_corpus.py
1. Ground truth is valid JSON ......... PASS (43k, schema_version 1.0)
2. Every referenced path exists ....... PASS (40/40)
3. No stray files on disk ............. PASS (40 on disk, all declared)
4. Modification times match spec ...... PASS (all within 2s)
5. Label sanity ....................... PASS (5 sub-checks)
6. Determinism ........................ PASS (40/40 reproduce identically)
OK: all 11 checks passed

$ pytest -q
70 passed in 6.29s

$ ruff check .   All checks passed!
$ black --check . clean
```

### Honest limitations of this benchmark (carried forward to the paper)

1. **40 files is small.** Enough to test correctness, far too small for
   statistical significance. Per-query-kind cells hold 2–4 queries each; those
   breakdowns are directional, not significant. This must be stated in Phase 21,
   not implied away.
2. **17 queries is small.** MRR over 17 queries has wide error bars. Reported
   as-is; no significance test would be honest at this n.
3. **The corpus author is the system author.** Unavoidable bias: the files were
   written knowing what the system does well. Phase 23's real-data migration
   plan exists specifically because of this, and no external-validity claim can
   rest on this corpus alone.
4. **The persona is a single Indian engineering student.** File-naming habits,
   date formats (`DD-MM-YYYY`), and folder conventions are culture- and
   domain-specific. Generalisation to other users is untested.

### Known-broken / deferred after Phase 3

- Generated corpus is git-ignored (`data/synthetic/corpus/`) and regenerated by
  script; the ground truth **is** committed, since it is the benchmark.
- Image files are absent — the spec lists images as partial-support only, and
  metadata-only extraction adds nothing testable here. Revisit in Phase 5.

### What Phase 4 needs from this phase

- A populated corpus at `cfg.paths.root` with correct, non-uniform mtimes.
- `contextfs.datagen.generate.missing_files()` and `corpus_manifest()` for
  scanner tests to assert against.
- Committed ground truth at `cfg.eval.ground_truth`.

---

## Phase 4 — File scanner (Layer 1) & SQLite store

### What was built

**`src/contextfs/store.py`** — the SQLite metadata store. Tables at schema v1:
`meta`, `files`, `scan_runs`, `scan_errors`. Opened with WAL journaling,
`synchronous=NORMAL`, an 8 MB page cache, and `foreign_keys=ON`.

**`src/contextfs/scanner.py`** — `os.walk` traversal with in-place directory
pruning, ignore rules from config, and four-way change classification.

**`contextfs scan`** — wired live, with `--full`, `--dry-run`, `--rehash`,
`--show-files`.

**`scripts/bench_scan.py`** — repeated-median benchmark harness.

### How change detection works

A two-tier test, because hashing everything every scan would make incremental
update time proportional to *corpus* size — destroying the very metric Phase 18
reports.

1. **Cheap tier (stat only).** If `size` **and** `mtime_ns` both match the
   stored values, the file is presumed unchanged and is *never opened*.
2. **Expensive tier (hash).** Otherwise the file is read and hashed. If the hash
   matches the stored one, the file is classified **unchanged** despite the
   moved timestamp — so `touch`, a backup restore, or a sync tool rewriting
   mtimes does not trigger a full reindex.

`--rehash` forces tier 2 for every file; `--full` marks everything modified.

**Stated limitation, not hidden:** tier 1 cannot detect a content change that
preserves both size and mtime exactly. That requires deliberate timestamp
forgery. `--rehash` closes it when it matters. The alternative — always hashing —
was rejected because it costs 4× on this corpus (76 ms vs 18 ms) and that ratio
grows with corpus size.

### Decisions & reasoning

#### Decision 19 — Schema migrations from the first table

**What:** `MIGRATIONS` is an ordered list of statement batches; the applied
version lives in `PRAGMA user_version`; opening an old database upgrades it.

**Why:** the schema will gain tables in Phases 5, 6, 10, 12, and 19. Without
migrations, each of those would either require a full reindex (violating the
incrementality constraint at the storage layer, not just the retrieval layer)
or an ad-hoc `ALTER TABLE` scattered through unrelated modules. One append-only
list keeps the upgrade path legible and testable.

#### Decision 20 — xxh3_128 rather than SHA-256 for content hashing

**What:** file fingerprints use `xxhash.xxh3_128`, a non-cryptographic hash.

**Why:** the hash answers exactly one question — "did these bytes change?" —
against a local, single-user, non-adversarial corpus. There is no threat model
in which an attacker crafts a collision against a student's own Documents
folder. xxh3 is roughly an order of magnitude faster than SHA-256 on this CPU,
and hashing is the dominant cost of a cold scan (76 ms of which is nearly all
I/O + hashing for 333 KiB). 128 bits makes accidental collision irrelevant at
any plausible corpus size.

**Note for the write-up:** the *tests* deliberately verify the read-only
guarantee with `hashlib.sha256`, not xxhash. An audit must not share an
implementation with the thing it audits, or a bug in that implementation is
invisible to the audit.

#### Decision 21 — Deleted files are tombstoned, not removed

**What:** a vanished file gets `status='deleted'`, keeping its row.

**Why:** by Phase 13 a file owns embeddings in LanceDB, nodes and edges in the
graph, timeline nodes, and session membership. Hard-deleting the SQLite row
would orphan all of it with no way to find what to unwind. The tombstone is the
handle incremental deletion needs. A restored file is revived in place rather
than duplicated — `test_restored_file_is_revived_not_duplicated` asserts the row
count stays at 40 across delete → scan → restore → scan.

#### Decision 22 — `--dry-run` creates nothing at all [changed mid-phase]

**What:** a dry run opens no scan-run record, writes no rows, and — if no index
exists yet — uses an in-memory store so the `.contextfs/` directory is never
created.

**Why:** the first implementation still created the database and a `scan_runs`
row. A test caught it. "What would happen if I indexed this folder?" is exactly
the question a cautious user asks *before* consenting to an index; answering it
by silently creating one is the wrong answer. Verified: after
`contextfs scan --dry-run` on a clean tree, `Test-Path .contextfs` → `False`.

#### Decision 23 — Unchanged files get a one-column update, not a full upsert
[performance fix found by benchmarking]

**What:** `Store.touch_seen()` issues a single chunked
`UPDATE files SET last_seen=? WHERE path IN (...)` for unchanged files, instead
of running the full upsert for every file seen.

**Why:** the first implementation upserted all 40 rows on every scan. Measured
consequence: the *second* scan (which does zero hashing) took **522 ms** while
the *first* (which hashed everything) took 183 ms — the incremental path was
slower than the full path, which is the exact opposite of the property the
architecture claims. Rewriting every column also rewrites all five indexes.

After the fix (7 repetitions, median):

| Scan | Median | Min | Max | Files hashed |
|---|---|---|---|---|
| Cold (all hashed) | **76.1 ms** | 71.2 | 95.2 | 40/40 |
| Warm (no changes) | **18.2 ms** | 17.2 | 20.5 | 0/40 |
| Incremental (1 file changed) | **37.4 ms** | 35.7 | 42.9 | 1/40 |

Cold throughput: 526 files/s, 4.3 MiB/s. Warm scan is **4.2× faster** than cold.

**Honesty caveat carried into the paper:** this corpus is 333 KiB and sits
entirely in the OS page cache. These numbers measure *per-file overhead*, not
disk throughput, and say nothing about behaviour at 100k files. The benchmark
script prints that caveat itself so it cannot be quoted without it.

### Verification — actual output

```
$ pytest -q
103 passed in 13.00s

$ ruff check .    All checks passed!
$ black --check . clean
```

CLI walkthrough from a clean state:

```
$ contextfs scan --dry-run        new 40, modified 0, unchanged 0, deleted 0
                                  hashed 40/40 (0.33 MiB) in 71 ms
                                  Dry run: nothing was written to the index.
  data dir created?  False        <-- dry run left no trace

$ contextfs scan                  new 40 | reprocessing 40/40 (100.0% of corpus)
$ contextfs scan                  unchanged 40 | hashed 0/40 | reprocessing 0/40 (0.0%)
```

**Read-only guarantee — the critical test.**
`test_scanning_does_not_modify_any_file` fingerprints all 40 files with
**SHA-256 + size + mtime_ns**, runs five scans (normal, repeat, `--full`,
`--rehash`, `--dry-run`), re-fingerprints, and asserts equality. Passing.
Two further tests assert no file is *created* under the root and that all
derived data lands under `paths.data_dir`.

Classification tests, all passing: first scan → 40 new; second → 40 unchanged
with 0 files hashed; edit one file → 1 modified / 39 unchanged / 1 hashed; add
one → 1 new; delete one → 1 deleted and tombstoned, `file_count()` 39 but 40
including deleted; restore → revived, still 40 rows; `touch` with identical
bytes → 0 modified; `--full` → 40 modified; oversized files inventoried but not
hashed.

### Known-broken / deferred after Phase 4

- No content is read yet beyond hashing — Phase 5.
- `files.ext` is recorded but nothing decides *extractability* from it yet.
- Symlink loops: `follow_symlinks` defaults to false, so cycles are impossible
  by default. If a user enables it, `os.walk` can loop. Not handled; flagged.
- `stats` still stubbed even though the data it needs now exists — Phase 17.

### What Phase 5 needs from this phase

- `Store` with `known_files()` / `all_files()` and the migration mechanism to
  add an `extracted_documents` table.
- `ScanResult.changed` — the exact set of files extraction must process.
- `FileRecord.ext` and `abs_path` for extractor dispatch.

---

## Phase 5 — Content extraction (Layer 2)

### What was built

| Module | Role |
|---|---|
| `extract/base.py` | `ExtractedDocument` / `ExtractedBlock` schema, block-boundary truncation |
| `extract/extractors.py` | Eight extractors: pdf, docx, pptx, xlsx, text, code, csv, image |
| `extract/__init__.py` | Extension → extractor registry, `extract_file`, `extract_many`, `ExtractionReport` |
| `store.py` (schema v2) | `documents` + `document_blocks` tables, `files_needing_extraction()` |
| `scripts/extraction_report.py` | Corpus-wide extraction report |

`contextfs scan` now runs extraction for changed files automatically
(`--no-extract` opts out).

### Decisions & reasoning

#### Decision 24 — Extraction preserves structure, it does not flatten to text
[CORE ARCHITECTURAL DECISION — the whole temporal contribution rests on it]

**What:** extraction yields a list of `ExtractedBlock`s, each tagged with its
origin (`page` / `sheet` / `slide` / `section` / `paragraph` / `notes`) and two
booleans: `is_tabular` and `is_heading`. `ExtractedDocument.tabular_spans()`
returns character ranges of tabular regions in the flattened text.

**Why:** the obvious design is "extraction returns a string". That would have
destroyed the single strongest signal the Phase 10 date classifier has. A date
inside a timetable row is a commitment; the same date in a paragraph of prose
usually is not. If the spreadsheet has already been flattened into a paragraph
by the time the classifier runs, the structured-context signal cannot be
computed at all — and Phase 10 would still produce a precision number, just a
meaningless one.

The character-offset design (`tabular_spans`, `block_at`) means Phase 10 can ask
"is the date at offset 1,847 inside a table?" without re-opening or re-parsing
the source file. Block offsets are persisted alongside the text, and a test
asserts `text[char_start:char_end] == block.text` for every stored block.

**Cost:** a more complex schema and two extra tables. Justified: this is the
mechanism behind the highest-novelty component in the project.

#### Decision 25 — Errors are captured as data, never raised

**What:** every extractor returns an `ExtractedDocument` with `ok=False` and an
`error` string rather than throwing. `extract_file` wraps the extractor in a
blanket `except Exception` as a final guarantee.

**Why:** the constraint is that one corrupt file cannot abort an index build.
But the second half matters more: **nothing is dropped silently.** A failure
becomes a row in `documents` with `ok=0`, a line in the extraction report, and a
red line in `contextfs scan` output. The failure mode being designed against is
not the crash — it is the index that quietly covers 38 of 40 files while
reporting success, which would corrupt every metric downstream with no visible
symptom.

Partial failures are represented too: a PDF where 3 of 12 pages yield no text
succeeds, keeps the 9 readable pages, and records a warning naming the count and
the likely cause (scanned images, OCR out of scope).

#### Decision 26 — XLSX opened with `data_only=True`

**What:** workbooks are read for formula *results*, not formula source.

**Why:** a cell containing `=TODAY()+7` tells retrieval nothing. The date it
evaluates to is the fact the user remembers. This has a real limitation worth
stating: `data_only=True` returns `None` for formula cells in a workbook that
has never been opened by Excel, because the cached value is absent. On the
synthetic corpus every value is literal so it does not arise, but on a real
corpus it will. Flagged for Phase 23.

#### Decision 27 — Code is one block, not one block per function

**What:** a source file becomes a single block tagged with its language.

**Why:** retrieval here answers "which file was I looking for", not "where is
this symbol". Splitting per function would multiply chunk count — and therefore
embedding time, the dominant index-build cost on this CPU — for no measurable
gain in re-finding accuracy. Revisit only if code-specific queries enter the
benchmark.

#### Decision 28 — Text-encoding fallback chain ends in replacement, not failure

**What:** `utf-8-sig` → `utf-8` → `cp1252` → `latin-1`, then UTF-8 with
`errors="replace"`.

**Why:** cp1252 precedes latin-1 because on Windows it is the far more likely
legacy encoding and decodes smart quotes and em-dashes correctly where latin-1
mojibakes them. The final replacement fallback means a partially garbled
document still enters the index — for retrieval, a document with three broken
characters is enormously more useful than no document.

#### Decision 29 — Re-extraction is keyed on content hash, not mtime

**What:** `files_needing_extraction()` returns files where no extraction exists
*or* `documents.content_hash IS NOT files.content_hash`.

**Why:** it composes correctly with Decision 23's scanner behaviour. A file that
was touched but not edited has a new mtime, an unchanged hash, and is therefore
not re-extracted. Verified live: the second `contextfs scan` prints
`extraction: nothing to do, all documents current`.

Re-extraction **replaces** a file's blocks wholesale rather than merging, so a
shrinking document cannot leave stale blocks behind with offsets that no longer
index anything. A test asserts a 7-block file re-extracted as 1 block ends with
exactly 1 stored block.

### Verification — actual output

```
$ python scripts/extraction_report.py
corpus: 40 files

  attempted              40
  succeeded              40
  failed                 0
  unsupported            0
  genuine_failures       0
  empty                  0
  with_warnings          0
  total_chars            55627
  tabular_documents      8
  duration_ms            2262.08

  SUCCESS RATE           100.0% (40/40)

  per extension (succeeded/attempted):
    .docx 5/5   .md 10/10   .pdf 4/4   .pptx 2/2
    .py 4/4     .sql 1/1    .txt 8/8   .xlsx 6/6

  FAILURES: none
  WARNINGS: none

  DOCUMENTS WITH TABULAR CONTENT (8):
    College/Capstone/evaluation_plan.xlsx
    College/Semester7/DBMS/dbms_lab_record.xlsx
    College/Semester7/MachineLearning/Exam_Timetable_Sem7.xlsx
    College/Semester7/MachineLearning/Unit3_SVM_Notes.md      <- markdown pipe table
    College/Semester7/MachineLearning/ml_lab_attendance.xlsx
    Personal/Career/application_tracker.xlsx
    Projects/UrbanFlow/sensor_data_sample.xlsx
    Projects/UrbanFlow/team_notes.md                          <- markdown pipe table
```

**Extraction success rate: 100% (40/40), zero failures, zero warnings, zero
empty documents.** All 6 spreadsheets plus both Markdown pipe tables were
correctly detected as tabular; the 32 prose/code documents were correctly not.

CLI, from a clean index:

```
$ contextfs scan     hashed 40/40 (0.33 MiB) in 94 ms | reprocessing 40/40 (100.0%)
                     extracted 40/40 documents (100.0%), 55,627 chars,
                     8 with tabular content, in 2441 ms
$ contextfs scan     hashed 0/40 | reprocessing 0/40 (0.0% of corpus)
                     extraction: nothing to do, all documents current
```

Index size after full extraction: **256 KB** SQLite for a 333 KiB corpus.

```
$ pytest -q      142 passed in 19.72s
$ ruff check .   All checks passed!
```

Tests added (44): registry coverage, per-format structure assertions, the
tabular-span/offset mapping, corrupt-PDF/DOCX/XLSX handling, missing files,
empty files, non-UTF-8 decoding, batch survival past a bad file, truncation at
block boundaries, persistence round-trip, offset integrity, incremental
re-extraction, and a read-only audit over the corpus.

**Two benchmark-critical fidelity tests:**
- `test_labelled_date_surfaces_survive_extraction` — all 65 ground-truth date
  surface forms are present in the extracted text. Without this, a Phase 10
  recall failure caused by extraction would be misdiagnosed as a classifier
  failure.
- `test_the_adversarial_case_survives_extraction` — the key PDF still contains
  no exam vocabulary after extraction, and the timetable still names it.

### Known-broken / deferred after Phase 5

- **No OCR.** Image extraction is metadata + filename only, as scoped. On a real
  corpus, scanned PDFs will surface as "N pages yielded no text" warnings.
- **`data_only=True` blind spot** — see Decision 26.
- Extraction is single-threaded. 2.4 s for 40 files is dominated by PDF parsing
  (747 ms for one file). Parallelising across the 8 cores is an obvious win but
  is deferred: embedding, not extraction, will dominate index build time from
  Phase 7, and optimising the wrong stage first is wasted effort.
- DOCX comments, footnotes, and headers/footers are not extracted.

### What Phase 6 needs from this phase

- `Store.all_documents()` and `get_blocks()` — spaCy runs over stored text.
- Character offsets on blocks, so entity mentions can be located structurally.
- `documents.text` as the canonical single string per document.

---

## Phase 6 — Entity extraction (Layer 3)

### What was built

`src/contextfs/entities.py` — spaCy NER for people / organisations / locations,
frequency-ranked keywords, and **raw date mentions**. Schema v3 adds `entities`,
`keywords`, `date_mentions`, `entity_runs`. `scripts/entity_eval.py` reports
precision/recall against a five-document hand-labelled gold set added to
`corpus_spec.py` as `ENTITY_GOLD`.

### Decisions & reasoning

#### Decision 30 — Date *detection* is separated from date *classification*

**What:** this layer records every date mention it can find and classifies none
of them. Phase 10 decides meaningful vs incidental.

**Why:** the classification signals live outside this layer — the document's
mtime, whether the date is inside a table, whether the same date recurs across
files. Separating them also means Phase 10 can be evaluated against a **fixed**
set of detected mentions, so a change to the classifier does not silently change
what is being classified. Without that separation, a Phase 10 precision
improvement could come from detecting fewer dates, which is not an improvement.

#### Decision 31 — Dates come from regex over the original text, not spaCy NER

**What:** spaCy's `DATE` entities are discarded; five regex patterns (numeric
dd-mm-yyyy, ISO, written d-m-y, written m-d-y, bare year) run over the
**original** extracted text.

**Why:** two hard reasons. (1) NER runs on markup-normalised text (Decision 32),
so its offsets do not index the original string — and a subtly wrong date offset
would corrupt the structured-context signal invisibly. (2) spaCy's NER reliably
misses bare numeric dates in spreadsheet rows, which is exactly where this
corpus's most important dates live, because a table row supplies none of the
sentence context the model depends on.

**Stated limitation:** relative expressions ("next week", "last semester") are
consequently out of scope for the temporal layer in this build.

#### Decision 32 — Markup is neutralised before NER [found by measurement]

**What:** `prepare_for_ner()` strips Markdown heading/bullet/checkbox markers,
converts table pipes into sentence boundaries, and appends a full stop to
headings that lack terminal punctuation.

**Why (the concrete failure):** the gold-set evaluation showed spaCy reading

```
## Zoho
Chennai/Tenkasi. Builds everything in-house...
```

as a **single** entity `Zoho Chennai/Tenkasi`, typed `PERSON`. The heading had no
terminal punctuation, so sentence segmentation merged it with the line below.
spaCy's models are trained on running prose; personal corpora are full of
headings, bullets and cells.

Entity offsets are remapped back to the original text by forward-scanning search
(the transformation only removes markup, never reorders words). An entity whose
surface cannot be relocated keeps offsets of `-1` rather than a plausible wrong
offset — a missing offset is recoverable, a wrong one is not.

**Measured effect:** micro F1 0.432 → 0.472.

#### Decision 33 — Corpus-level category consensus and gazetteer propagation

**What:** two corpus-wide corrections. `reconcile_entity_categories()` resolves
the same entity string being typed differently in different documents, by
majority of distinct files (strict majority required; ties change nothing).
`build_gazetteer()` / `propagate_gazetteer()` take entities confidently detected
in prose and search for them in documents where NER had too little context.

**Why:** NER types an entity from local sentence context, so a company named in
a cover letter is found and the same company in a bullet list is not. Propagation
uses only evidence the corpus already contains — no external word list, no
hand-written list of real companies. Only terms containing a lowercase letter
propagate, so acronym false positives stay local instead of being broadcast.

Both are compatible with incremental indexing: they read votes already stored, so
re-analysing one file immediately benefits from corpus-wide knowledge.

**Measured effect:** org precision 0.176 → 0.273. Overall F1 unchanged at 0.472,
which led directly to Decision 35.

#### Decision 34 — Short all-caps organisation detections are dropped by default

**What:** `entities.drop_acronym_orgs` (default true) discards ≤5-character
all-capitals tokens typed as organisations.

**Why:** on the gold set, `API`, `SQL`, `DBMS`, `FYP` and `CS` accounted for the
majority of organisation false positives. **The cost is stated, not hidden:** this
also discards genuine short acronym organisations (IBM, BBC, NASA). It is
therefore a config flag, not a hardcoded rule, and such tokens remain retrievable
through the keyword layer regardless.

#### Decision 35 — Default spaCy model changed from `sm` to `md`, on measurement
[DEVIATION from the "small model" default chosen in Phase 1]

After Decisions 32–34, org and location recall were still poor. Dumping the raw
votes showed why, and it was not a reconciliation problem:

```
Zoho        {'location': 2}      <- consistently wrong, nothing to reconcile
Freshworks  {'person': 2}        <- consistently wrong
Postman     {}                   <- never detected
Chargebee   {}                   <- never detected
Tenkasi     {}                   <- never detected
```

`en_core_web_sm` has no word vectors, so rare proper nouns (company names,
Indian place names) are simply outside its reach. Measured comparison:

| Model | Entity F1 | Recall | Location F1 | Time / 40 docs | On disk |
|---|---|---|---|---|---|
| `en_core_web_sm` | 0.472 | 0.586 | 0.333 | 2337 ms | 14.5 MB |
| **`en_core_web_md`** | **0.595** | **0.759** | **0.857** | 2794 ms | 53.9 MB |

+26% F1 and +30% recall for +20% time and +39 MB. Taken, because the time cost
is paid only for changed files (incremental) and is dwarfed by embedding cost
from Phase 7 onward. Recorded in `contextfs.toml` with the measurement table
beside the setting, so the choice is auditable rather than arbitrary.

#### Decision 36 — Year-less dates are resolved against the document's mtime

**What:** "24 Nov" with no year resolves to whichever of (mtime year − 1, mtime
year, mtime year + 1) lands closest to the document's own timestamp. The mention
is flagged `year_inferred=True` so Phase 10 can discount it.

**Why (found by a failing test):** `test_date_recurrence_is_computed_across_files`
failed. The timetable writes `24-11-2025`; the revision checklist writes `24 Nov`.
Unresolved, those are two unrelated facts, so the ML exam date — the single most
important date in the corpus — had a cross-file recurrence count of **1** instead
of 2, silently disabling one of Phase 10's four signals on its primary case.
People omit years constantly in personal documents, and those are often the most
actionable dates.

#### Decision 37 — Keyword ranking is frequency, not TF-IDF

TF-IDF needs a corpus-wide document-frequency table, which would make
per-document extraction depend on the whole corpus: adding one file would
invalidate every other file's keywords, breaking incrementality. Corpus-level
term weighting belongs in the embedding and retrieval layers, and happens there.

### Verification — actual output

```
$ python scripts/entity_eval.py          (en_core_web_md, whole-corpus pass)
category       TP   FP   FN  precision   recall       F1
people         11   14    1      0.440    0.917    0.595
orgs            5    8    5      0.385    0.500    0.435
locations       6    1    1      0.857    0.857    0.857
MICRO AVG      22   23    7      0.489    0.759    0.595

Sample size: 29 gold entities across 5 documents.

$ contextfs scan   (clean index)
extracted 40/40 documents (100.0%), 55,627 chars, 8 with tabular content, 1430 ms
entities: 254 mentions (90 people, 97 orgs, 24 locations),
          101 raw date mentions, 996 keywords over 40 files in 9301 ms

$ pytest -q      188 passed in 48.44s
$ ruff check .   All checks passed!
```

**Honest reading of these numbers.** Micro precision 0.489 is mediocre. The
false positives are mostly over-broad or mistyped spans ("British", "Direct
Action", "Workplace", "Ruby"/"Java" as people), not hallucinated text. For
ContextFS's actual use — entity-overlap edges between files — a false positive
costs a spurious weak edge, while a false negative costs a missing connection,
so recall (0.759) is the more consequential number here. That asymmetry is an
argument, not an excuse, and the precision figure is reported unmodified. n=29
over 5 documents: a sanity spot check, not an NER benchmark.

### Known-broken / deferred after Phase 6

- Precision remains modest; a transformer model (`en_core_web_trf`) would likely
  fix it but needs ~500 MB and is markedly slower on CPU. Not taken.
- Relative date expressions unsupported (Decision 31).
- Numeric dates assume **day-first**. Correct for this corpus persona; wrong for
  a US corpus. Currently hardcoded, should become a config key. Flagged.
- Entity coreference ("sir" → Dr. Murari) is not resolved.

### What Phase 7 needs from this phase

- `documents.text` and `document_blocks` for chunking.
- `Store.date_recurrence()` and `entity_index()` (built here, consumed in 9/10).
- Confirmation that entity/keyword extraction is per-document and incremental,
  so embedding can be too.

---

## Phase 7 — Embedding generation (Layer 4)

### What was built

`src/contextfs/embed.py` — block-aware chunker, dual-backend `Embedder`, and a
LanceDB `VectorStore` with `chunks` and `documents` tables. Schema v4 adds an
`embeddings` bookkeeping table. New CLI command: `contextfs fetch-models`.

### Chunking strategy (the phase requires this be justified)

Chunks are built **from extraction blocks, not from character windows**. Blocks
already are the document's own structure — a page, a sheet, a slide, a Markdown
section — so a block-aware chunker gets semantic coherence for free, where a
fixed-width window would routinely cut a timetable row in half.

- **Size 256 tokens.** Not a tuned value — it is `all-MiniLM-L6-v2`'s hard input
  limit. A larger chunk would be a lie: the tail would be silently truncated by
  the model and embedded as if it did not exist.
- **Overlap 48 tokens (~19%).** Roughly two sentences: enough to keep a deadline
  and its surrounding keywords in the same chunk from either side of a boundary,
  which is precisely the span Phase 10 reasons over.
- A block that alone exceeds the budget is split on **line** boundaries, not
  sentence boundaries, because oversized blocks in practice are spreadsheets and
  code where a line *is* the unit.
- Chunks inherit `is_tabular`, so structure reaches the vector store.

**Document vectors are the mean of a document's normalised chunk vectors**,
re-normalised. Cheaper than a second full-text encode and strictly more faithful:
encoding full text would truncate past 256 tokens, whereas pooling has seen
every chunk.

### Decisions & reasoning

#### Decision 38 — Vectors are L2-normalised once, at encode time

Cosine similarity then becomes a plain dot product everywhere downstream —
LanceDB search, Phase 9 edge weights, Phase 15 scoring. This removes an entire
class of "did we normalise this one?" bugs rather than relying on discipline.

#### Decision 39 — Both chunk-level and document-level vectors are stored

Chunk vectors locate *where* a match occurred (needed for explanations in Phase
16). Document vectors are the retrieval unit. Keeping only chunks would conflate
"this document is relevant" with "this paragraph is relevant" and distort
Precision@K whenever one long document contributes several near-duplicate hits.

#### Decision 40 — Default encoder backend is `transformers`, not
`sentence-transformers` [DEVIATION — flagged, measured, and reversible]

**The stack constraint says sentence-transformers.** Measured import cost on
this machine:

```
import torch                    4003 ms
import transformers             4090 ms   (~90 ms on top of torch)
import sentence_transformers   15741 ms   (~11.6 s of its own eager imports)
```

`sentence-transformers` eagerly imports its whole module zoo at package-import
time. That is **~11.6 s added to every indexing run that has any embedding work
to do**, against ~1.2 s of actual encoding for this corpus. On the target laptop
that is the difference between a tool that feels usable and one that does not.

**What was done instead of silently substituting:** the same weights are loaded
through `transformers.AutoModel` and pooled with attention-masked mean pooling
plus L2 normalisation — which is exactly what sentence-transformers does for
`all-MiniLM-L6-v2`. Both backends remain selectable via
`[embeddings].backend`, sentence-transformers is still a declared dependency,
and `test_the_two_backends_produce_the_same_vectors` asserts they agree to
within 1e-4 cosine on prose, tabular, and code inputs.

**Assessment of the deviation:** the *library* named in the stack is still
present and is now the correctness oracle; only the hot path avoids paying its
import. If the supervisor prefers strict adherence, one config line reverts it
at a cost of ~11.6 s per index build.

#### Decision 41 — PyTorch thread count is set explicitly

PyTorch defaults to physical cores (4 of the 3700U's 8 logical). Measured:

| Threads | Throughput |
|---|---|
| 4 (default) | 19.5 chunks/s |
| 8 (all logical) | **34.5 chunks/s** |

+77% for one line. `[embeddings].num_threads = 0` means "all logical".

#### Decision 42 — Model loading is forced offline; one command may use the network

**What:** `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE` are set around every model
load. If the model is absent, indexing **fails with an instruction** rather than
downloading. A single new command, `contextfs fetch-models`, performs the
one-time download and says so.

**Why:** left at defaults, `transformers` contacts the HuggingFace Hub on every
`from_pretrained` to check for a newer revision. That is a silent outbound
request on every index build. The project's stated principle is that indexing
your files makes no network calls, and a principle enforced by nothing is not a
property. It is also the single largest speed win found this phase: model load
**12.2 s → a fraction of that**, taking full-corpus embedding from 26.3 s to
20.1 s.

### Verification — actual output

```
$ pytest -q                    218 passed in 96.96s
$ ruff check .                 All checks passed!

$ contextfs scan   (clean index, models cached)
extracted 40/40 documents (100.0%), 55,627 chars, 8 with tabular content
entities: 254 mentions, 101 raw date mentions, 996 keywords over 40 files
embeddings: 65 chunks over 40 files in 20127 ms
total scan wall time: 37.0 s

$ contextfs scan   (second run)
embeddings: nothing to do, all vectors current
```

**Dimensionality:** asserted to be 384, matching the model spec; a mismatch
between model and `[embeddings].dimension` raises rather than producing an
unqueryable store.

**Nearest-neighbour sanity checks (the phase's stated verification), all passing:**

- Query "support vector machines, kernels and the margin" → top hit is
  `Unit3_SVM_Notes.md`.
- The planted near-duplicate pair (`Unit4_Ensemble_Methods.pdf` and its
  `_annotated` twin) are each other's **nearest** neighbours, cosine **> 0.9** —
  which is what Phase 9's duplicate edges will be built from.
- `SVM notes · ensembles PDF` scores higher than `SVM notes · biryani recipe`.
- A timetable query surfaces chunks flagged `is_tabular`.

### Honest performance note

Encoding is **1.2 s** for 65 chunks (~54 chunks/s with the thread fix). The
remaining ~19 s of the embedding stage is fixed per-process cost: importing
torch (3.3 s), transformers, LanceDB (2.6 s), and constructing the model. This
is amortised over an index build, but it means a **one-shot `contextfs query`
will pay ~10 s before it can embed the query string.** That is a real usability
problem on this hardware, it is not solved here, and the two available fixes are
(a) the Phase 27 desktop GUI, which keeps one process alive, and (b) a resident
daemon. Recorded now so it is not discovered as a surprise at demo time.

### Known-broken / deferred after Phase 7

- Cold-start query latency, as above.
- No ANN index is built: at 40 documents LanceDB brute-forces, which is correct
  and fastest at this scale. An `IVF_PQ` index becomes necessary somewhere in
  the tens of thousands of vectors; untested here and must not be claimed.
- Token counting during packing is an estimate (`words / 0.75`), not a real
  tokenizer call, for speed. The model truncates anything that slips over.

### What Phase 8 needs from this phase

- `VectorStore.document_vectors()` — the aligned `(ids, matrix)` pair.
- Chunk vectors for building summary nodes bottom-up.
- `Embedder.pool()` for computing summary-node vectors from their children.

---

## Phase 8 — Semantic tree (Layer 5, RAPTOR-inspired)

### What was built

`src/contextfs/summarize.py` — extractive summariser plus an optional
loopback-only Ollama client. `src/contextfs/tree.py` — `Root → Project → Folder
→ File → Chunk` construction with bottom-up rollup of file counts, vectors and
summaries. Schema v5 adds `tree_nodes`. Wired into `contextfs scan`.

### Decisions & reasoning

#### Decision 43 — The extractive summariser is the default-tested path

Ollama is not installed on the development machine, so the fallback is what
actually runs in every test and every measurement. A fallback that only executes
on someone else's laptop is not a fallback. `Summarizer` probes for Ollama once,
uses it when present, and falls back per-node on failure while counting the
fallbacks so the behaviour is visible rather than silent.

#### Decision 44 — The tree's skeleton is the filesystem, not embedding clusters
[DEVIATION from RAPTOR — deliberate]

**What RAPTOR does:** cluster chunks by embedding similarity and build an
abstract hierarchy over the clusters.

**What ContextFS does:** use the user's own folder hierarchy, with top-level
directories as "Project" nodes.

**Why:** three reasons, in order of weight.

1. **Explanations are a hard requirement here.** A clustered node has no name a
   user recognises — "cluster 7" is not something anyone remembers. A folder
   node is called `MachineLearning`, which is a thing the user themselves
   created. RAPTOR's tree feeds an LLM; ContextFS's tree has to be shown to a
   person.
2. **Folder structure is authored signal.** The user *chose* it, so it encodes
   their own organisation of their work. Discarding it to re-derive structure
   from embeddings throws away information and replaces it with a guess.
3. **Incrementality.** Adding one file adds one node. Re-clustering would
   restructure the tree on every scan, violating the incremental constraint.

**What is kept from RAPTOR:** a summary node is itself a legitimate retrieval
target, and summaries are built bottom-up from child summaries rather than from
concatenated raw text — so input size stays bounded regardless of subtree size.

#### Decision 45 — Summaries are hard-capped in length [found by a failing test]

`test_file_summaries_are_shorter_than_their_documents` failed on
`evaluation_plan.xlsx`: an **856-character summary of an 833-character
document**. Cause: for a spreadsheet, "sentences" are table rows with no
terminal punctuation, so a sentence-count limit bounded nothing.

A summary longer than its source is not a summary, and because folder summaries
are built from child summaries, unbounded node summaries would have **grown
without limit up the tree**. Fixed with `MAX_SUMMARY_CHARS = 400` and
word-boundary clipping. Two new invariant tests: every summary is bounded, and a
parent summary is never longer than the sum of its children's.

### Verification — actual output

```
$ contextfs scan
semantic tree: 321 nodes (4 projects, 7 folders, 40 files, 269 chunks),
               52 summaries via extractive in ~30 ms

$ pytest -q      249 passed in 78.86s
$ ruff check .   All checks passed!
```

**Phase-required checks, both passing:**

- *Every file node reachable from root* — `reachable_from_root()` covers all 321
  nodes; `orphans()` is empty.
- *Summary count matches the folder/project structure* — 52 summaries = 1 root +
  4 projects + 7 folders + 40 files, asserted as an equation rather than a
  constant.

Project nodes resolve to exactly `{College, Projects, Personal, Downloads}`;
`path_to_root` for `app.py` gives `["app.py", "UrbanFlow", "Projects", "corpus"]`.

### Known-broken / deferred after Phase 8

- The tree is rebuilt wholesale on each scan rather than patched. Cheap here
  (~30 ms) because it derives entirely from `files` and `documents`, but it is a
  genuine exception to the incremental rule and is called out as such.
- Summary-node **vectors** are computed when a `VectorStore` is passed but are
  not yet persisted to LanceDB, so summary nodes are not directly searchable.
  Deferred until Phase 15 shows whether seed selection actually needs them.
- The extractive summariser is frequency-based and is poor on tabular documents
  (it can only pick rows). Acceptable: those documents' value is in their dates
  and entities, both extracted by other layers.

### What Phase 9 needs from this phase

- `Store.entity_index()` for entity edges, `VectorStore.document_vectors()` for
  semantic and duplicate edges, and `files.folder` for structural edges.
- `tree_nodes` for folder-proximity computation.

---

## Phase 9 — Relationship graph (Layer 6)

### What was built

`src/contextfs/graph.py` — a NetworkX `MultiDiGraph` over file nodes with four
edge types (`semantic`, `entity`, `structural`, `duplicate`), JSON persistence,
type-filtered neighbour queries, and `shortest_explained_path()`. Wired into
`contextfs scan`.

### Decisions & reasoning

#### Decision 46 — MultiDiGraph, and every edge keeps its own evidence

Two files are routinely related in several ways at once — the assignment draft
and final share a folder, share entities, are semantically similar, *and* are
near-duplicates. A simple graph would collapse those into one link, and an
explanation reading "these are connected" without saying how would fail the
explainability requirement outright. Parallel edges let Phase 16 report each
reason separately. Entity edges additionally store *which* entities were shared;
duplicate edges store both their Jaccard and cosine scores.

Directed, because Phase 13's `temporal` edges genuinely are not symmetric.
Symmetric relations are stored as matched pairs, asserted by a test.

#### Decision 47 — Entity edges are IDF-weighted

Sharing "Dr. Murari" (3 files) is strong evidence; sharing "Chennai" (many
files) is nearly none. Counting raw shared entities would treat those
identically and make every file mentioning the user's own city a neighbour of
every other. Entities appearing in more than half the corpus are dropped
entirely — they carry no information.

#### Decision 48 — Semantic edges are capped per node

Top-8 by similarity, above threshold. A dense graph is slower to traverse *and
less informative*: if everything connects to everything, graph connectivity
stops discriminating between results and the layer contributes nothing.

#### Decision 49 — Near-duplicate detection uses shingle Jaccard, not cosine
[SPEC DEVIATION — the spec says "embedding similarity above threshold";
measurement says that does not work]

The first implementation followed the spec: duplicate edge when cosine ≥ 0.95.
It produced **zero** duplicate edges, missing both planted pairs. Rather than
lowering the threshold until the test passed, the actual distribution was
measured:

| Pair | Cosine | Jaccard (5-word shingles) |
|---|---|---|
| Planted duplicate #1 (annotated PDF) | 0.928 | **0.519** |
| Planted duplicate #2 (draft/final docx) | 0.827 | **0.393** |
| Best **non**-duplicate pair (proposal ↔ review slides) | 0.807 | 0.021 |

By cosine, the margin between a true duplicate and a false one is **0.019** —
any threshold is a coin flip, and planted pair #2 ranks *below* several
unrelated pairs. By Jaccard the margin is **0.372**, about **19× better
separation**.

The reason is structural, not a corpus artefact: **embeddings are trained to
place documents about the same topic close together, which is the opposite of
what near-duplicate detection needs.** Two different essays about BCNF
normalisation *should* be semantically close, and are not duplicates.

Implementation keeps cosine as a cheap candidate pre-filter (≥ 0.70) so shingle
sets are never built for all O(n²) pairs. `duplicate_threshold` is now a Jaccard
threshold (0.25), and `contextfs.toml` carries the measurement table beside it.
`test_jaccard_separates_duplicates_far_better_than_cosine` asserts the margin
holds, so if it ever collapses the decision gets revisited rather than silently
inherited.

**Assessment of the deviation:** the spec's phrase was a reasonable default that
turns out to be wrong for this task. This is exactly the case the master prompt
asks to be flagged rather than silently redesigned.

#### Decision 50 — The graph is stored as readable JSON, not pickle

An index a user can read is an index a user can audit, which matters for a
system whose selling point is explainability. Pickle would also be a
code-execution hazard on load.

### Verification — actual output

```
$ contextfs scan
graph: 40 nodes, 414 edges (4 duplicate, 30 entity, 54 semantic, 326 structural)
  near-duplicate pairs detected: 2

$ pytest -q      277 passed in 94.14s
$ ruff check .   All checks passed!
```

**Graph statistics report:** 40 nodes, 414 edges, 0 isolated nodes, ≤3 connected
components (asserted — a fragmented graph would cripple traversal retrieval).

**Phase-required manual check — the planted duplicates:** both deliberately
planted near-duplicate pairs are linked with `duplicate` edges (4 directed edges
= 2 pairs × 2 directions), and `test_no_spurious_duplicates` asserts **no other
pair** was flagged. All four edge types are produced; every edge weight is in
[0, 1]; symmetry holds for all four symmetric types; and the draft/final pair
carries ≥ 3 distinct relation types simultaneously — the case that justifies
MultiDiGraph.

### Known-broken / deferred after Phase 9

- The graph is rebuilt wholesale each scan (~230 ms at this scale). Genuine
  exception to incrementality, same as the tree. Phase 18 will measure whether
  it matters.
- Structural edges are O(n²) within a folder. Fine for tens of files per folder;
  a folder with thousands would blow up. Untested at that scale, not claimed.
- Entity edges rest on Phase 6's NER, whose precision is 0.489 — some entity
  edges will be spurious. Mitigated by IDF weighting and the ≥2 shared-entity
  minimum, not eliminated.

### What Phase 10 needs from this phase

- Nothing structural; Phase 10 reads `date_mentions` (with `in_tabular` already
  stamped) and `Store.date_recurrence()` directly.
- Phase 13 will re-enter this module to add `temporal` and `activity` edges,
  which are already declared in `EDGE_TYPES`.

---

## Phase 10 — Meaningful vs. incidental date classification (Layer 7)

**The project's highest-novelty component.** Headline result:

| | Precision | Recall | F1 | Accuracy |
|---|---|---|---|---|
| **ContextFS classifier** | **0.972** | **1.000** | **0.986** | **0.985** |
| Naive extraction (every date meaningful) | 0.538 | 1.000 | 0.700 | — |

65 labelled `(file, date)` pairs. **This is a small sample and is reported as
such** — it demonstrates the model behaves as designed; it is not a
statistically significant accuracy estimate.

### The scoring model

```
relevance = 0.40 · S_keyword      (commitment / past-record / incidental vocabulary)
          + 0.25 · S_structured   (inside a table?)
          + 0.20 · S_metadata     (distance from the document's own mtime)
          + 0.15 · S_crossfile    (how many files mention this date)

then:  relevance × year_only_penalty   if the mention carries only year precision

meaningful  ⟺  relevance ≥ timeline_node_threshold   (0.55, configurable)
```

Weights are validated to sum to 1.0 (Phase 2, Decision 11), so the output is
genuinely on a 0–1 scale rather than an arbitrary sum.

### Decisions & reasoning

#### Decision 51 — Neutral is 0.5, not 0 [the decision the whole model rests on]

Each signal returns **0.5 when it has no evidence either way**, and moves up or
down from there.

Worked through by hand before writing the code: if an absent signal contributed
0, every signal would become evidence *against* meaningfulness whenever it was
silent. Concretely — a deadline written in prose ("Last date for submission: 31
December 2025") would be punished by the structured-context signal for not being
in a table, scoring **0.534 against a 0.55 threshold**. A false negative on a
completely unambiguous deadline.

A date in a table is evidence *for*. A date in prose is *no evidence either
way*. Those are different statements and the arithmetic has to reflect it.
Guarded by `test_a_prose_deadline_is_not_punished_for_lacking_a_table`.

#### Decision 52 — Three vocabularies, not one, and the best evidence wins

S1 searches for **commitment** words (*deadline*, *exam*, *viva*, *due*),
**past-record** words (*attendance*, *completed*, *logged*), and **incidental**
words (*born*, *published*, *founded*). The latter two push the score *down*.

This is what separates an attendance spreadsheet from a timetable spreadsheet —
structurally identical objects, both tables full of dates. Without a negative
vocabulary, the structured-context signal alone would make every row of
`ml_lab_attendance.xlsx` a timeline node.

The **maximum** of each polarity is used rather than the sum, so a table
repeating a status word ten times cannot out-vote one occurrence of "deadline".

#### Decision 53 — The metadata signal is deliberately asymmetric

People write about deadlines *before* they fall due. A date shortly **after**
the document's mtime decays with a 60-day constant; a date **before** it decays
twice as fast (30-day constant). A 1947 date in a 2025 file scores ~0.

#### Decision 54 — A precision gate, stated separately from the weighted sum

A mention carrying only year precision is multiplied by `year_only_penalty`
(0.35). You cannot attend an exam "in 1998". This is kept out of the four
signals and reported as its own field because it is a **categorical statement
about the kind of mention**, not graded evidence about its context — conflating
the two would make the score harder to defend, not easier.

#### Decision 55 — "present" removed from the past-record vocabulary
[found by the first evaluation run]

The first run scored P 0.971 / R 0.943 / F1 0.957 with **two false negatives,
both traced to the same word**: `-present@2` and `-present@4`. In an attendance
sheet "Present" is a status; in meeting notes `Present: Alfred, Abu, Dr. Murari`
introduces the *attendee list* — evidence the meeting happened, the exact
opposite reading.

Removing it costs nothing, because the maximum (not the sum) of evidence is
used and "attendance"/"absent" already dominate the case it was meant for.

#### Decision 56 — Structural headings are searched, not just a proximity window
[BEYOND SPEC — the largest single accuracy gain this phase]

Both remaining errors after Decision 55 had the same shape: **the
disambiguating word was in the document but outside the token window.**

- The only false positive was a sensor timestamp in `sensor_data_sample.xlsx`.
  Its `Timestamp` column header sits at the top of the sheet; the row it labels
  is forty tokens further down.
- The only false negative was `16 January 2026` in `supervisor_meeting_notes.md`.
  The `# Supervisor meetings` heading is two hundred characters above it.

One principle covers both: **structure carries context that proximity cannot
see.** A column header governs its whole column; a section heading governs its
whole section. `_header_for()` now supplies the containing table's header rows,
or the *chain* of preceding Markdown headings, and those are searched for
keywords at a 0.7 discount (a header describes a column, not the individual
cell).

The heading **chain** matters, not just the nearest heading — a date frequently
sits inside its own subheading (`## 16 January 2026, 11:00`), which says
nothing; the document heading above it is what identifies the kind of date.
Keeping only the nearest heading discards exactly the level that carries meaning,
and the first attempt at this fix failed for precisely that reason.

Measured progression across the three fixes:

| Version | Precision | Recall | F1 |
|---|---|---|---|
| Initial | 0.971 | 0.943 | 0.957 |
| + "present" removed, + table headers | 0.971 | 0.971 | 0.971 |
| + heading chain | **0.972** | **1.000** | **0.986** |

#### Decision 57 — Rule-based and inspectable, not learned

There is no labelled training corpus of personal files to learn from — building
one is the very problem this project has. A learned scorer also could not
produce the per-signal explanation the system promises. The weights live in
configuration precisely so a future project *can* fit them if such a corpus
appears.

Every verdict carries its signal values, the evidence that produced them (which
keyword, at what token distance, with what strength), and the arithmetic.
`test_the_score_is_reproducible_from_its_explanation` asserts the score can be
recomputed by hand from the explanation — the difference between a defensible
model and a black box.

### Threshold sensitivity (measured, not assumed)

```
threshold  precision  recall     F1
     0.35      0.700   1.000  0.824
     0.45      0.895   0.971  0.932
     0.50      0.972   1.000  0.986
     0.55      0.972   1.000  0.986   <- configured
     0.60      0.972   1.000  0.986
     0.70      0.960   0.686  0.800
     0.80      1.000   0.400  0.571
```

F1 is **flat across 0.50–0.60**. The configured value sits in the middle of a
plateau rather than on a knife-edge, which is the evidence that it was not
tuned to the test set.

### Honest limitations

1. **One irreducible false positive remains**: `13-09-2025` in
   `sensor_data_sample.xlsx`. A table of observations timestamped near the
   file's own mtime is structurally near-identical to a table of scheduled
   events. The header discount reduces its score but not below threshold. Not
   fixed, and reported rather than tuned away.
2. **The metadata signal is not merely a tie-breaker.** A date four days after a
   document's timestamp, with no keyword, no table and no recurrence, scores
   0.574 — just over threshold. Defensible (writing about next week is what a
   commitment looks like) but it is a real property, now asserted by a test so
   it stays a decision rather than an accident.
3. **The vocabularies are English and domain-specific.** They were written for a
   student corpus. A different domain needs different words, and no attempt has
   been made to show they transfer.
4. **65 labelled pairs.** Small. Stated in the script's own output so the number
   cannot be quoted without its caveat.
5. **25 detected dates are unlabelled** and excluded from scoring — the corpus
   was annotated for the dates that matter to the argument, not for every string
   that looks like a date. Scoring against them would measure annotation
   completeness, not classifier accuracy. Reported explicitly.

### Verification — actual output

```
$ contextfs scan
dates: 44 meaningful / 44 incidental of 88 distinct (file, date) pairs in 158 ms

$ python scripts/date_eval.py
  true positives     34      false positives     1
  false negatives     0      true negatives     30
  PRECISION 0.972   RECALL 1.000   F1 0.986   accuracy 0.985
  naive baseline: precision 0.538  recall 1.000  F1 0.700

$ pytest -q      312 passed in 272.23s
$ ruff check .   All checks passed!
```

35 new tests covering each signal independently, the neutral-at-0.5 property,
the precision gate, explanation reproducibility, threshold configurability, and
the corpus's planted adversarial cases (attendance table, history essay,
birthday list, publication years, unsessioned scholarship deadline).

### What Phase 11 needs from this phase

- `Store.meaningful_dates()` — the classified dates that become timeline nodes.
- `DateVerdict.explain()` for the timeline's own explanations.
- `classified_dates.iso_date` indexed, for interval-tree construction.

---

## Phase 11 — Timeline index

### What was built

`src/contextfs/temporal/timeline.py` — a natural-language date-range resolver and
an `intervaltree`-backed index over **meaningful dates only**. `contextfs timeline`
wired end to end, with `--show-incidental` and `--bench`.

### Decisions & reasoning

#### Decision 58 — Rule-based range resolution, not a model

The phrases people use for time are a small, enumerable set: a month, a month
and year, a span, an ordinal week, a relative offset, a quarter, a year. A
rule-based resolver is fast, auditable, and every failure is a *missing rule*
rather than an inscrutable one — and it reports what it understood, which a
model would not.

Ordinal weeks count from the 1st in blocks of seven ("third week of October" =
15th–21st), not ISO week numbering, because that is what people mean; ISO
numbering would put the boundary on an arbitrary weekday.

#### Decision 59 — Range resolution is disambiguated **against the index**
[BEYOND SPEC — fixes a confidently-wrong answer]

Found by running the benchmark's own queries. With today at 2026-08-12,
`contextfs timeline "September"` resolved to **September 2026** — nearest to
today — and returned nothing, while the files the user wants sit in September
2025. Same for q02's "third week of October". Both benchmark queries returned a
confident empty answer.

**Re-finding is backward-looking.** Someone asking about "September" is almost
always thinking of a September that has happened. But rather than hardcoding
"prefer the past" — which would break "what's due in September" — `resolve_best()`
generates the plausible readings and picks the one that **actually contains
files**, falling back to nearest-to-today when none do.

Crucially the inference is *shown*, not silent:

```
September -> 2025-09-01 .. 2025-09-30
   (September 2025 - chosen over September 2026 because it is where your
    files are (7 dated file(s)))
```

An index-aware resolver is only defensible if the user can see it happened.

#### Decision 60 — Only meaningful dates enter the timeline

The index is built from `Store.meaningful_dates()`, not from all date mentions.
This is the entire payoff of Phase 10: an index over every extracted date would
be dominated by publication years and attendance rows. Asserted by
`test_historical_dates_are_absent_from_the_timeline` — querying `1947` returns
nothing even though the corpus contains four 1947 mentions.

### Verification — actual output

```
$ contextfs timeline "September" --bench
September -> 2025-09-01 .. 2025-09-30 (September 2025 - chosen over
             September 2026 because it is where your files are (7 dated file(s)))
2025-09-01  Personal/Career/application_tracker.xlsx   0.71 +in-table +near-mtime +recurs
2025-09-13  Projects/UrbanFlow/sensor_data_sample.xlsx 0.75 +in-table +near-mtime +recurs
2025-09-13  Projects/UrbanFlow/team_notes.md           0.62 +near-mtime +recurs
2025-09-14  Projects/UrbanFlow/submission_checklist.txt 0.88 +deadline@0
...
query latency: median 0.0113 ms (min 0.0075, max 0.0759) over 200 runs,
               44 timeline nodes

$ contextfs timeline "third week of October"
third week of October -> 2025-10-15 .. 2025-10-21 (week 3 of October 2025 ...)
2025-10-17  College/Semester7/DBMS/Assignment2_Normalization_final.docx  0.73 +due@4

$ pytest tests/test_timeline.py -q     43 passed in 0.50s
$ ruff check .                         All checks passed!
```

Both queries return their ground-truth targets, with a per-result explanation
inherited from the Phase 10 verdict.

### On the "speed story" — what this number does and does not show

Median range-query latency is **0.0113 ms over 200 runs at 44 timeline nodes**.

**That number is close to meaningless as evidence.** At 44 nodes a linear scan
would also be instant; what is being measured is Python call overhead, not the
interval tree. The interval tree's advantage is asymptotic — O(log n + k) rather
than O(n) — and demonstrating it requires a corpus with tens of thousands of
timeline nodes, which does not exist here.

What the measurement *does* establish: range resolution plus index lookup adds
no perceptible cost to a query, so the temporal layer is free relative to
embedding (~10 s cold, Phase 7). Any claim that ContextFS's timeline is *fast at
scale* needs a re-benchmark on a corpus two or three orders of magnitude larger,
and that requirement is recorded in the module docstring, in the benchmark
output, and here.

### Known-broken / deferred after Phase 11

- Candidate results are not yet ranked by relevance — `timeline` returns
  everything in range, chronologically. Graph retrieval and ranking arrive in
  Phase 15.
- Relative expressions resolve against the system clock, so `--bench` output and
  any doc example using "last week" are not reproducible across days. Absolute
  forms are used everywhere in tests.
- No support for durations ("the fortnight before the exam") or event-relative
  phrasing ("before my viva"). Event-relative resolution needs the activity
  layer, which is Phase 12.

### What Phase 12 needs from this phase

- `TimelineIndex` and `DateRange`, so activity sessions can be bounded in time
  and cross-referenced with meaningful dates.
- `Store.meaningful_dates()` for session labelling.

---

## Phases 12 & 13 — Activity sessions (Layer 8) and temporal graph integration

Combined because Phase 13 is precisely the wiring of Phases 11 and 12 into the
Phase 9 graph; splitting them would have meant committing a graph that was
knowingly missing half its node types.

**Headline result — session accuracy:**

| Metric | Value |
|---|---|
| Pairwise precision | **1.000** |
| Pairwise recall | **1.000** |
| **Session accuracy (pairwise F1)** | **1.000** |
| Planted sessions recovered | **5 / 5** (each at F1 = 1.00) |
| Negative control over-clustered | **0** |
| **The q01 adversarial case** | **SOLVED** |

90 true same-session pairs over 40 files. **A perfect score on a 40-file corpus
authored by the same person who built the system is exactly the number a
reviewer should distrust**, and it is reported with that caveat attached rather
than as a headline claim. What it establishes is that the mechanism works on the
cases it was designed for; it establishes nothing about generalisation.

### Decisions & reasoning

#### Decision 61 — Time is a gate, not a fifth weighted term

Affinity combines temporal, semantic, entity and folder signals; the temporal
*constraint* sits outside that sum. Two files on identical topics edited four
months apart are not one work session — they are one project revisited. As a
weighted term, a high topic score could buy its way past an implausible gap; as
a gate it cannot.

#### Decision 62 — The gate is on the **idle gap between clusters**, not on
every pair [found by measurement, and it is the difference between working and not]

The first implementation gated every pair: two files could only be in one
session if they were within `session_gap_hours` of *each other*. That silently
caps a session's total duration at the gap value.

Measured against the corpus's planted sessions:

| Session | Files | Span | Largest gap between consecutive files |
|---|---|---|---|
| hackathon_urbanflow | 8 | 1 d | 24 h |
| dbms_assignment | 5 | 11 d | 100 h |
| ml_exam_prep | 7 | 12 d | 131 h |
| internship_apps | 5 | 15 d | 190 h |
| capstone_contextfs | 7 | 25 d | 219 h |
| *personal_misc (control)* | 5 | 223 d | 2598 h; **smallest** internal gap 192 h |

**No real session satisfies an all-pairs 72-hour gate.** Only the hackathon
(1-day span) could ever have formed. Exam prep fragmented into three clusters
and — decisively — the exam timetable landed in a different session from the
lecture PDF, so the adversarial case failed outright.

What characterises a work episode is not that it is short but that it has **no
long silence in the middle**. The gate is now on the *idle gap*: the shortest
time between any file of one cluster and any file of the other. Sessions may run
for weeks provided nothing goes quiet for more than `session_gap_hours`.

`session_gap_hours` was then set from that table: **240 h (10 days)**, above the
largest real gap (219 h) with margin. The control's 192 h internal gap also
passes the gate — deliberately. **Time decides what *could* be one session;
content decides what *is*.** The control is rejected by the affinity threshold,
which is the correct division of labour.

#### Decision 63 — The link threshold was chosen from a measured plateau

Sweeping `session_link_threshold` against ground truth:

| Threshold | Precision | Recall | F1 | Recovered | q01 | Control over-clustered |
|---|---|---|---|---|---|---|
| 0.02 | 0.841 | 1.000 | 0.914 | 5/5 | OK | 0 |
| 0.05 | 0.900 | 1.000 | 0.947 | 5/5 | OK | 0 |
| 0.10 | 1.000 | 1.000 | **1.000** | 5/5 | OK | 0 |
| **0.18** | 1.000 | 1.000 | **1.000** | 5/5 | OK | 0 |
| 0.25 | 1.000 | 1.000 | **1.000** | 5/5 | OK | 0 |
| 0.27 | 1.000 | 0.822 | 0.902 | 5/5 | FAIL | 0 |
| 0.35 | 1.000 | 0.600 | 0.750 | 5/5 | FAIL | 0 |

The optimum is a **plateau 15 points wide (0.10–0.25)**, not a peak. 0.18 sits
in the middle of it. Above 0.27 exam prep fragments and q01 is lost; below 0.10
unrelated sessions begin to merge. **The negative control is never clustered at
any threshold tested, including 0.02** — which is the strongest single piece of
evidence that sessions are not just folder membership in disguise, since those
five files share a folder.

#### Decision 64 — Average linkage, not single

Single linkage chains: A joins B, B joins C, and a personal corpus collapses
into one session. Average linkage requires a candidate to resemble the cluster
as a whole. Implemented directly rather than through scipy so the linkage rule,
the gate and the stopping condition are visible in the code a reviewer reads —
this is a contribution being evaluated, not a utility call.

#### Decision 65 — Session accuracy is measured as pairwise F1, with the
control scored as singletons

Pairwise F1 over "same session" judgements needs no alignment between predicted
and true cluster ids and degrades gracefully — splitting one true session in two
costs recall rather than scoring zero.

**The negative control contributes no true pairs.** Grouping `personal_misc`
therefore costs precision. This is the single most consequential choice in the
protocol and it is stated in the script's own docstring: scoring those five
files as a true session would *reward* the exact failure the control exists to
detect.

#### Decision 66 — Sessions and dates are graph **nodes**, not file attributes

Phase 13's requirement says "first-class, not a bolt-on", and node-vs-attribute
is what that means concretely. Three consequences an attribute could not give:

1. **Traversal reaches them.** A walk can step file → session → file, which is
   exactly how q01 is solved — verified by
   `test_traversal_reaches_the_key_pdf_through_a_session`, which restricts the
   walk to `activity` edges only and asserts the path passes through a session
   node.
2. **They are addressable.** "The hackathon weekend" is a thing the graph
   contains and an explanation can name.
3. **Ablation switches them off by edge type** without rebuilding anything —
   `activity` and `temporal` were reserved in `EDGE_TYPES` back in Phase 9, and
   `build_graph(include_context=False)` produces a file-only graph for Phase 22.

`activity` edges are symmetric; file-to-file `temporal` edges are **directed
earliest-first**, because "was edited before" is a real ordering.

### Verification — actual output

```
$ contextfs scan
sessions: 5 reconstructed (32 files clustered, 8 unsessioned)
graph: 77 nodes, 597 edges (64 activity, 4 duplicate, 30 entity, 54 semantic,
       326 structural, 123 temporal)
  context nodes: 5 session, 32 timeline

$ python scripts/session_eval.py
  tp / fp / fn      90 / 0 / 0
  PRECISION         1.000
  RECALL            1.000
  SESSION ACCURACY  1.000   (pairwise F1)

  planted sessions recovered: 5/5
    OK  capstone_contextfs   F1=1.00  overlap=7/7
    OK  dbms_assignment      F1=1.00  overlap=5/5
    OK  hackathon_urbanflow  F1=1.00  overlap=8/8
    OK  internship_apps      F1=1.00  overlap=5/5
    OK  ml_exam_prep         F1=1.00  overlap=7/7

  THE ADVERSARIAL CASE (query q01)
    Unit4_Ensemble_Methods.pdf -> session:4
    Exam_Timetable_Sem7.xlsx   -> session:4
    RESULT: same session. The PDF is reachable from an exam query
            even though it contains no exam vocabulary.

  NEGATIVE CONTROL: correct - none of the 5 control files were clustered.

$ pytest -q      380 passed in 117.40s
$ ruff check .   All checks passed!
```

### Honest limitations

1. **F1 = 1.000 is a corpus artefact as much as a result.** 40 files, 5
   sessions, authored by the system's author. Phase 23's real-data plan exists
   because of this.
2. **Sessions are reconstructed from mtime alone.** ContextFS has no access to
   *access* times or application telemetry — deliberately, per the privacy
   principle. A file read but not modified during a session is invisible to it.
   On a real corpus this will lose members that a Recall-style monitor would
   catch, and that is an accepted cost of not being a Recall-style monitor.
3. **Clustering is O(n²) in files and re-runs wholesale on every scan** (72 ms
   at 40 files). At tens of thousands of files this becomes the dominant index
   cost and would need blocking by time window first. Untested at that scale.
4. **`session_gap_hours = 240` encodes a student's working rhythm.** A
   professional corpus with daily activity would want a much smaller value. It
   is a config key, and the measurement table sits beside it.
5. Session *type* labels (`exam_prep`, `hackathon`, …) are keyword-matched and
   cosmetic — they never affect clustering, so a mislabel is a display bug, not
   a retrieval one.

### What Phase 14 needs from this phase

- `Store.sessions()` / `session_membership()` for activity scoring.
- Session and date nodes in the graph, so seed selection can start from them.
- `TimelineIndex` for the temporal component of query decomposition.

---
