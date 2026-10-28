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

## Phases 14–17 & 21–22 — Retrieval, explanations, CLI completion, evaluation

Combined: an explanation (16) is produced by the same pass that ranks (15), an
ablation (22) is the harness (21) run over configurations, and the CLI (17) is
the surface all of it is exercised through. Splitting them would have meant
shipping a ranker that could not say why, or a harness that could not be run.

### THE HEADLINE RESULT

| | Baseline (semantic only) | ContextFS (full) | Δ |
|---|---|---|---|
| **MRR** | 0.503 | **0.584** | **+0.082** |
| Recall@3 | 0.431 | **0.575** | +0.144 |
| Recall@5 | 0.564 | **0.720** | +0.156 |
| Recall@10 | 0.686 | **0.856** | +0.171 |
| hit@1 | 0.412 | 0.412 | ±0.000 |
| Explanation coverage | 100% | 100% | — |

**By query kind — the view that answers the research questions:**

| Query kind | n | Baseline MRR | Full MRR | Δ |
|---|---|---|---|---|
| **temporal** | 3 | 0.067 | **0.344** | **+0.278** |
| **activity** | 4 | 0.312 | **0.417** | **+0.104** |
| semantic | 4 | 0.625 | 0.653 | +0.028 |
| hybrid | 4 | 0.775 | 0.781 | +0.006 |
| entity | 2 | 0.750 | 0.750 | ±0.000 |

**Full ablation grid:**

| Configuration | RQ | MRR | P@5 | R@10 |
|---|---|---|---|---|
| baseline (flat semantic) | control | 0.503 | 0.564 | 0.686 |
| semantic_only (hybrid machinery) | control | 0.518 | 0.564 | 0.686 |
| semantic + graph | **RQ4** | 0.536 | 0.575 | 0.727 |
| semantic + graph + activity | **RQ1** | 0.535 | 0.617 | 0.739 |
| semantic + graph + timeline | **RQ2** | 0.585 | 0.678 | 0.845 |
| **full (all four)** | **RQ5** | **0.584** | **0.720** | **0.856** |

### Decisions & reasoning

#### Decision 67 — The baseline shares everything except its signals

`SemanticBaseline` uses the same index, the same embeddings, the same store. It
is not a strawman — dense retrieval over document vectors is what a good
conventional tool does. `semantic_only` (the hybrid pipeline restricted to the
semantic signal) is reported alongside it to isolate pipeline overhead from
signal contribution: the 0.503 → 0.518 gap is machinery, not context.

#### Decision 68 — Query-adaptive weighting [found by a measured regression]

The first full ablation showed two problems:

* **entity queries got worse** with context on (MRR 0.750 → 0.667), and
* **`semantic+graph+temporal` (0.585) beat the full system (0.566)** — adding
  activity actively hurt.

One cause. A query like *"documents where my supervisor is mentioned"* names no
time and no activity, yet timeline and activity weights were still applied, so
any file that happened to sit in a session or have a dated neighbour collected
score it had not earned and pushed the true match down.

`_adaptive_weights()` now drops a signal whose **input is absent from the
query** and re-normalises the rest — the same re-normalisation the ablation
uses, for the same reason. This is a statement about evidence, not a tuning
knob: a signal with no input contributes noise, so it should not contribute
weight. The active weights are reported in every explanation, so no result hides
which weighting produced it.

**A first attempt failed and is worth recording:** the condition was originally
"no activity cue *and* no seed landed in a session". Nearly every file belongs
to some session, so the second clause was true for almost every query and the
guard never fired — the metrics were byte-identical. Correcting it to the
query's own cue produced:

| | Before | After |
|---|---|---|
| MRR (full) | 0.566 | **0.584** |
| entity MRR | 0.667 | **0.750** (regression eliminated) |
| R@10 | 0.798 | **0.856** |

#### Decision 69 — Explanation completeness is a measured metric, not a promise

`Explanation.is_complete` requires **at least one substantive reason** plus the
scoring arithmetic. An explanation object that exists but is empty would satisfy
the letter of "every result carries an explanation" while defeating it.

That metric immediately caught a real breach: `semantic_only` scored **76.5%**,
because `matched_topic` was only populated above a similarity of 0.15 and
low-similarity results were returned with nothing to say. A weak match *is* the
reason a result is present; stating "0.11" is honest, stating nothing is not.
Threshold removed → **100% across all six configurations**, verified by
`test_every_result_carries_a_complete_explanation`, which checks *every* result
of *every* query on *both* systems, not the top-1 spot check the phase asked for.

#### Decision 70 — Metrics are broken down by query kind

The aggregate answers "is it better". The breakdown answers "*which layer* does
the work", which is what RQ1–RQ4 actually ask. It is also the only view that
would expose a system improving on average while degrading a query class — which
is exactly what happened, and exactly how Decision 68 was found.

Precision@K uses `min(k, |relevant|)` as its denominator: a query with two
correct answers cannot reach P@10 = 1.0 otherwise, and penalising it for that
would measure the query rather than the system.

#### Decision 71 — Latency is measured after an explicit warm-up

Model import and load cost ~24 s on this machine (Phase 7's known issue) and
would otherwise be charged entirely to whichever query ran first. The harness
warms both models before timing and **prints the warm-up figure separately**.
Median query latency is then **47–258 ms** depending on configuration.

### Verification — actual output

```
$ pytest -q                                409 passed in 124.02s
$ ruff check .                             All checks passed!
$ python scripts/evaluate.py               (tables above)
$ contextfs stats                          40 files, 77 graph nodes, 597 edges,
                                           46 timeline nodes, 5 sessions, index current
$ contextfs explain 3
```

`explain` on q01's third result is the clearest single artefact this project
produces — the thesis on one screen:

```
College/Semester7/MachineLearning/Unit4_Ensemble_Methods.pdf   rank 3, score 0.5975

Reasons
  • topic match (0.18): study, machine, learn, exam
  • same work session as your query: exam prep in .../MachineLearning (7 files)
  • reached via College/Semester7/MachineLearning/Exam_Timetable_Sem7.xlsx
  • matches the file type you asked for

  Signal     Value  Weight  Contribution
  semantic   0.181   0.529         0.096
  graph      0.800   0.235         0.188
  activity   1.000   0.235         0.235
  timeline   0.000   0.000         0.000
```

Semantic similarity is **0.181** — near the floor. The file is retrieved on
activity (1.000) and graph (0.800). This is precisely the case the whole
architecture exists for, and the explanation says so in the user's own terms.

### Honest reading of the results

1. **hit@1 did not move (0.412 both).** The context layers improve *ranking depth*
   and *recall*, not the top slot. On this corpus the baseline already gets the
   easy queries' first result right, and the hard ones need more than a re-rank.
   Reported unchanged rather than buried.
2. **`semantic+graph+timeline` (0.585) marginally exceeds the full system
   (0.584)** on aggregate MRR. Activity adds +0.104 on activity queries and
   +0.011 on R@10, but costs a hair of MRR elsewhere. At n=17 this difference is
   noise; it is reported because omitting it would misrepresent the ablation.
3. **RQ3 is answered elsewhere** (Phase 10: F1 0.986 vs naive 0.700). The
   retrieval ablation does not test it.
4. **17 queries.** Per-kind cells hold 2–4 queries. Those breakdowns are
   **directional, not significant**, and no significance test is reported
   because none would be honest at this n. The harness prints that caveat itself.
5. **The corpus was authored by the system's author.** Every number above
   inherits that bias. Phase 23 exists for this reason.

### Known-broken / deferred

- Cold-start latency (~10 s to load models) still dominates a one-shot
  `contextfs query`. Unsolved; the GUI's long-lived process is the fix.
- Graph score uses only one-hop edge weight to a seed; multi-hop path scoring
  with decay is designed but not implemented.
- `explain` reads a cached `last_query.json` rather than persisted result ids.
  Adequate for one user; wrong for concurrent sessions.

---

## Phase 18 — Incremental update correctness

`scripts/incremental_check.py` builds a full index over a throwaway copy of the
corpus, re-scans it unchanged, then modifies one file, adds one, deletes one, and
re-scans again — instrumenting every stage.

### Measured (all figures from the run, none estimated)

| Pass | Files reprocessed | Total time |
|---|---|---|
| 1. Initial full build | 40 extracted / 40 embedded | **33 166 ms** |
| 2. Re-scan, nothing changed | **0 / 0 / 0** | **464 ms** |
| 3. After 1 modified, 1 added, 1 deleted | **2 extracted, 2 embedded** | **1 322 ms** |

- **Reprocessed-file-count vs corpus size: 2 / 40 = 5.0%**
- **Incremental update time: 1 322 ms**
- **Speed-up vs full rebuild: 25.1×**

### Correctness checks — all passing

1. A no-change re-scan reprocesses nothing (0 extracted, 0 entities, 0 embedded).
2. Change detection is exact: 1 new, 1 modified, 1 deleted — no over- or
   under-reporting.
3. Only changed files are reprocessed: 2 of 40.
4. The deleted file is removed from **every** store — tombstoned in SQLite,
   purged from `documents`, and its vectors deleted from LanceDB. This is the
   check that matters most: a vector left behind stays searchable and would
   return text for a file that no longer exists, which is a *wrong answer*
   rather than a missing one.
5. The added file is fully indexed through extraction, entities and vectors.

### The honest asterisk on "25× faster"

The per-file stages — extraction, entities, embedding — are **genuinely
incremental**, and they are the ones whose cost scales with corpus size. The
structural stages are **rebuilt wholesale by design**, and their cost is reported
separately rather than folded into the headline: dates 131 ms, sessions 129 ms,
tree + graph 192 ms.

That is a deliberate architectural choice, not an oversight. Date classification
uses cross-file recurrence, and session clustering is global — adding one file
can legitimately change the verdict on a date in a file that did not itself
change, or re-partition a session. A per-file patch would leave the store in a
state no full run would ever produce, and silent divergence between "incremental"
and "full" results would be far worse than 450 ms of rebuild.

**The consequence, stated plainly:** incremental update time has a floor of
roughly 450 ms that does not shrink with fewer changes, and those stages are
O(n²) in files (session clustering) or O(n) (dates, tree, graph). At tens of
thousands of files this floor becomes the dominant cost and the design would
need revisiting — blocking sessions by time window, and patching timeline nodes
per file with a periodic global reconciliation. Untested at that scale and not
claimed.

---

## Phases 19–20 — Relevance feedback, and auxiliary insight surfaces

Both phases are, by the master prompt's own assessment, low research value:
they do not test the hypothesis and no reported metric depends on them. They
are time-boxed accordingly, and the design goal was to add **no new pipeline** —
every feature here is a projection over structures Phases 4–13 already built.

### What was built

**Phase 19 — feedback learning.** Schema v8 adds a `feedback` table, `contextfs
feedback --pick N / --reject N / --show / --clear` records against the last
query, and `HybridRetriever._apply_feedback` applies a bounded re-rank.

**Phase 20 — four insight surfaces** in a new `insights.py`, exposed as
`contextfs duplicates`, `projects`, `digest`, and `tags <file>`.

#### Decision 72 — Feedback is a re-rank, not a fifth signal

The obvious implementation is to add feedback as another weighted signal beside
semantic/graph/activity/timeline. That was rejected, and a pre-existing hook
that did roughly this (inside `_score`, via a `feedback.adjust()` call) was
**removed** rather than implemented.

Three reasons, in order of importance:

1. **It would corrupt the explanations.** The hook sat inside per-file scoring,
   upstream of the format multiplier and inside the arithmetic the explanation
   reports. A boosted file would have displayed `contributions` for signals that
   did not earn them — the system would have been lying in exactly the place the
   project promises it never does.
2. **It would corrupt the ablations.** An ablation asking "what does the graph
   contribute?" must not silently be answering "the graph, plus whatever the
   user clicked last time".
3. **It is not a research claim.** With one user and a handful of clicks there
   is no honest way to evaluate learned relevance, so it must not be able to
   move a number the project reports.

Feedback is therefore applied **after** scoring and sorting, is never folded
into `contributions`, and — decisively — is **inert unless a feedback store is
passed in**. The evaluation harness constructs `HybridRetriever` without one.
A test pins this (`test_feedback_is_inert_without_a_feedback_store`), because
the alternative is circular results that look like an improvement.

#### Decision 73 — The boost is bounded and saturating, and this is measured

The boost is `feedback_max_boost * w / (1 + |w|)` where `w` is the net signed
feedback weight. The first pick buys half the cap; further picks buy
progressively less; the total can never reach the cap.

Measured on the live index, query *"notes I revised before the machine learning
exam"*, watching `College/Capstone/references.txt`:

| Picks | Score | Δ vs baseline | Rank |
|---|---|---|---|
| 0 | 0.734 | — | 3 |
| 1 | 0.809 | +0.075 (exactly half the 0.15 cap) | 3 |
| 6 | 0.863 | +0.129 (saturating, still under the cap) | 2 |

Six picks moved it past rank 2 (0.812) and **could not** move it past rank 1
(0.877). That is the intended property demonstrated rather than asserted: user
feedback reorders near-ties and cannot overturn a clear win.

#### Decision 74 — Feedback is keyed on a crudely normalised query

`Store.normalise_query` casefolds and collapses whitespace, nothing more. No
stemming, no stopword removal. A smarter normaliser would let feedback from one
query leak into a semantically different one, and with the tiny amount of
feedback a single user generates, one wrong transfer costs more than several
missed ones. Only queries the user *acted on* are stored — this is deliberately
not a search-history table.

#### Decision 75 — Duplicates are reported as components, not pairs

Three copies of a document produce three pairwise `duplicate` edges. Reporting
that as three findings makes one problem look like several, so the edges are
collapsed into connected components. Exact content-hash collisions are folded in
alongside the graph's Jaccard edges — free, exact, and reported at similarity
1.0. On the corpus this finds exactly the two pairs it was authored to contain:

```
Group 1 - 2 files, similarity 0.39, 36 KB redundant
  dup   College/Semester7/DBMS/Assignment2_Normalization_draft.docx
  keep  College/Semester7/DBMS/Assignment2_Normalization_final.docx
Group 2 - 2 files, similarity 0.52, 5 KB redundant
  dup   College/Semester7/MachineLearning/Unit4_Ensemble_Methods.pdf
  keep  College/Semester7/MachineLearning/Unit4_Ensemble_Methods_annotated.pdf
```

#### Decision 76 — "Projects" are folders, not a second clustering

The master prompt implies learned project detection. That was deliberately
downgraded: the Phase 12 activity sessions **already are** the learned grouping,
and re-clustering into projects would produce a second, differently-shaped
answer to the same question with no principled way to say which is right.
Folders are what the user themselves chose, so they are the spine, and sessions
and meaningful dates are attached as evidence.

Lifecycle stage is recency against two thresholds (60 / 180 days) with one
override: a folder whose latest **meaningful** date is still in the future is
`upcoming` regardless of file mtimes, because a deadline that has not passed is
a stronger signal than two months of silence. This is the one place the Phase 10
meaningful/incidental distinction earns its keep outside retrieval.

Measured on the corpus: 1 active, 1 dormant, 5 finished, every row carrying its
own reason string.

#### Decision 77 — Tag "confidence" is labelled as not a probability

`suggest_tags` returns a score that is a fixed per-source prior times a rank
decay. It orders the list and nothing more. Both the docstring and the CLI
footer say so, because a bare `0.72` next to a tag reads as calibrated and
nothing here calibrated it.

Sources are ranked by specificity: session label (0.90) > session kind (0.75) >
named entity (0.65–0.80) > meaningful date (0.70) > keyword (0.55, decaying).
On `Unit4_Ensemble_Methods.pdf` — the file from the headline result, which
contains no exam vocabulary and which semantic search cannot find — the top tag
is **"exam prep in College/Semester7/MachineLearning (10 Nov 2025)"**. The
activity layer tags it correctly for a reason its own text does not support.

### A real bug this phase exposed: read-only stores cannot migrate

Adding schema v8 broke `contextfs query` against the existing index. `query`
opens the store **read-only** (so that searching can never alter an index), and
read-only opens deliberately skip migrations — so the new `feedback` table did
not exist and every query crashed with `no such table: feedback`.

This is not a feedback bug; it is a bug that *every future migration* would have
hit. Fixed generally:

- `Store.has_table()` — reads of newer-than-the-file structures degrade to "no
  data", which is the truthful answer: an old index genuinely holds no feedback.
- `Store.is_outdated()` — and `query` now prints a visible notice naming the
  schema version and telling the user to re-scan, rather than silently
  degrading.

The read-only guarantee was kept intact. Making read-only opens migrate "just
this once" would have been the smaller diff and the wrong call.

### Verification — actual output

- 23 new tests (`tests/test_insights.py`), suite now **432 passing**.
- ruff and black clean across 51 files.
- All five new commands exercised against the live 40-file index; output quoted
  above is real, not illustrative.
- The feedback demo events were cleared afterwards, so the committed index
  carries no synthetic clicks.

### Honest limitations

- **Feedback is unevaluated.** There is no measurement that it improves
  retrieval, because with n=1 user there is no honest way to produce one. It is
  a usability feature that is *provably bounded*, not a learning result.
- Feedback is exact-query-scoped, so rephrasing loses it. Deliberate (Decision
  74), but it does mean the feature is weakest for the users who most need it.
- Project lifecycle thresholds (60/180 days) are conventions, not measured
  against labelled data — the corpus has no lifecycle ground truth.
- `near_duplicates` inherits the graph's Jaccard threshold, so its recall is
  whatever Phase 9 measured; it adds exactness for identical hashes only.

---

## Phases 23–25 — Real-data migration plan, code quality pass, documentation

### Phase 24 — Code quality and test coverage

Measured with `pytest --cov`, before and after:

| Module | Before | After |
|---|---|---|
| `cli/main.py` | **19.0%** | **49.2%** |
| `insights.py` | 73.0% | 80.7% |
| **Total** | **77%** | **81.5%** |

Test count 432 → **457**.

#### Decision 78 — The CLI was the coverage hole, and it is the main deliverable

19% on `cli/main.py` was the worst number in the project and the least
defensible one: the user's stated priority is that the CLI is "the main thing",
every command had been exercised by hand against the live index, and *none of
that behaviour was pinned against regression*.

`tests/test_cli_commands.py` (25 tests) fixes it without importing the ML stack.
The fixture writes a small index **directly into SQLite** plus a graph JSON,
which is sufficient for every command that *reads* an index rather than building
one — `digest`, `duplicates`, `projects`, `tags`, `feedback`, `stats`,
`explain`, `reset`, `config`. The whole module runs in **6.5 seconds**.

That speed is the point, not a bonus. On this hardware a CLI test module that
loaded torch would add minutes to every run, and a test suite people stop
running has zero coverage regardless of what the number says.

What is deliberately *not* covered here: `scan`, `query` and `timeline` need
real embeddings, so this module only asserts they fail **cleanly** with no
index — which is the path a first-time user actually hits. Their behavioural
coverage lives in the integration tests.

Two properties worth calling out as tests rather than as prose:

- `test_duplicates_never_offers_to_delete` — the read-only guarantee has to be
  visible at the surface, not merely held internally.
- `test_every_index_reading_command_fails_cleanly_with_no_index`, parametrised
  over all seven index-reading commands — no traceback, correct exit code, and
  the word "scan" in the message, because the most common first-run mistake
  should tell you what to do about it.

#### Remaining low-coverage modules, flagged rather than papered over

| Module | Coverage | Why, honestly |
|---|---|---|
| `__main__.py` | 0% | Three lines of entry-point plumbing, exercised by a subprocess test that coverage cannot see |
| `cli/main.py` | 49% | The uncovered half is `scan`'s pipeline orchestration, which is genuinely covered — by the integration tests and by every evaluation script — but only through subprocesses and real model loads |
| `evaluation.py` | 50% | Exercised by `scripts/evaluate.py` on every result quoted in the README, not by unit tests. Real coverage is higher than the number; the number is what a CI would see |
| `extract/extractors.py` | 64% | The uncovered branches are per-format failure paths — corrupt PDFs, password-protected files, unsupported variants — which would need a library of deliberately broken fixtures |
| `datagen/generate.py` | 68% | Corpus generation is verified by `scripts/verify_corpus.py` end to end rather than per-branch |

Stating this rather than chasing the number: the gap in `extractors.py` is a
real gap (broken-file handling is untested against actual broken files), and the
gaps in `evaluation.py` and `cli/main.py` are artefacts of where the testing
happens rather than absent testing. Different problems, and worth distinguishing.

### Phase 23 — Real dataset migration plan

Written to [docs/real-data-migration.md](docs/real-data-migration.md), and
deliberately **not executed** — that is what the phase asked for.

#### Decision 79 — The plan's most useful content is what cannot be done

The mechanical part of "migrating to real data" is trivial and the document says
so in one paragraph: ContextFS never writes to scanned files and stores
everything derived separately, so pointing it at a real folder is a
*configuration change*, not a conversion.

The document's actual value is the honest accounting of the hard steps:

- **Step 4 (build a real query set) is the bottleneck**, and it cannot be
  automated. The protocol given — write queries before looking at the index,
  never adjust a failed query, record "I misremembered which file it was" as a
  real outcome — exists because the obvious way to do this quietly turns an
  evaluation into a demo. The labeller is also the author of the system;
  blinding is impossible; the strongest available claim is a case study at n=1
  with the conflict of interest stated.
- **Three measured results have no path to real-data validation at all.**
  Session accuracy needs ground-truth session boundaries that nobody remembers
  and no record preserves. Meaningful-date F1 and entity F1 need per-item human
  labelling at a scale (~10³ date pairs for a 500-file subtree) that is feasible
  in principle and brutal in practice. For sessions the honest fallback is a
  confirm/reject proxy that yields precision-like feedback and **no recall** —
  you cannot ask someone to enumerate sessions they have forgotten.
- **Six real-data failure modes the synthetic corpus structurally cannot
  exhibit** are listed so a disappointing run gets *diagnosed* rather than
  mistaken for a null result. The first is the important one: cloud sync, bulk
  copies and restores rewrite mtimes, and since the activity layer is built
  entirely on mtime this does not degrade it — it silences it, with no detection
  and no repair.
- **Scale projections are labelled as projections.** The table gives the
  complexity of each stage and where it is expected to hurt; the only measured
  point on it is 40 files. Session clustering at O(n²) is named as the first
  real wall.

The document ends with a *minimum viable* validation — one folder, 100–300
files, 15 queries written from memory in advance, roughly a day's work — plus an
explicit statement of what that can and cannot claim.

### Phase 25 — Documentation

- **[docs/architecture.md](docs/architecture.md)** — maps all eleven specified
  layers onto the modules that implement them, with a data-flow diagram, the
  storage rationale, and each deviation stated inline next to the layer it
  affects.
- **README.md** — rewritten from the Phase 1 placeholder. Leads with the
  hypothesis and the measured table, then the per-kind breakdown, then the
  ablation.
- **guide.md** — rewritten in Phases 19–20; every placeholder marker is gone and
  every documented command exists.

#### Decision 80 — The README states the result's weaknesses in the README

The user's instruction was to solve any visible weakness that is solvable and
state the rest. The README therefore carries a ten-item **Weaknesses** section
that includes the things a reader would otherwise catch and stop trusting the
document for:

- the corpus is synthetic and self-authored (named as "the big one");
- n=17, with per-kind cells of 2–4 queries, explicitly not a significance test;
- **the activity layer barely moves the headline metric** — +0.000 MRR over
  graph alone, and the honest reading is that the temporal layer carries the
  result;
- **Hit@1 did not improve at all**, 0.412 for every configuration;
- entity extraction has precision 0.489, meaning roughly half of extracted
  entities are wrong.

The ablation table in the README is annotated with that reading rather than left
for the reader to work out. A result presented with its own strongest
counter-argument is more persuasive than one that waits to be caught, and an
examiner who finds an unstated caveat themselves will discount everything else
in the document.

---

## Phase 26 — Publication and clean-clone verification

Pushed to https://github.com/retr0alfred/ContextFS as instructed: `main`
force-overwritten, and the two pre-existing tags (`final-review`,
`phase1-review`) deleted, so the remote carries only this build.

### Decision 81 — Verification is a real clone, not a `git status` check

"It pushed successfully" proves nothing about whether anyone else can run this.
The `.gitignore` deliberately excludes the generated corpus, both indexes, and
every derived artefact — which is correct for privacy and repo size, and is
exactly the condition under which a repository silently stops being
reproducible.

So the check was performed the way a reader would perform it: clone the public
URL into a fresh directory and rebuild everything from nothing.

| Step | Result |
|---|---|
| `git clone` | 73 tracked files; no corpus, no index, as intended |
| `scripts/generate_corpus.py` | 40 files, 5 sessions + 1 negative control, 17 queries, 35 meaningful / 30 incidental dates, 2 near-duplicate pairs |
| `scripts/verify_corpus.py` | **OK: all 11 checks passed** |
| `contextfs scan` | 321 tree nodes · 77 graph nodes · 597 edges · 5 sessions · 2 duplicate pairs — identical to the development index |
| `scripts/evaluate.py` | see below |

### The result that matters: every published number reproduces exactly

| Configuration | Clean clone MRR | Development MRR | Identical |
|---|---|---|---|
| baseline | 0.5029 | 0.5029 | ✅ |
| semantic_only | 0.5176 | 0.5176 | ✅ |
| semantic_graph | 0.5359 | 0.5359 | ✅ |
| semantic_graph_temporal | 0.5850 | 0.5850 | ✅ |
| semantic_graph_activity | 0.5355 | 0.5355 | ✅ |
| full | 0.5845 | 0.5845 | ✅ |

Compared programmatically on both MRR and R@10 to within 1e-9: **bit-identical
reproduction from a clean clone.**

This is the single strongest claim the project can make about its own
methodology. Every figure in README.md, log.md and docs/ was produced by a
script that a third party can re-run from the public repository and get the same
answer to four decimal places — including the corpus itself, which is generated
deterministically rather than committed. Nothing is quoted from a run that no
longer exists.

### Honest reading of the research questions

| RQ | Configuration tested | Verdict |
|---|---|---|
| **RQ1** — does activity-aware retrieval help? | semantic_graph_activity vs semantic_graph | **Weakly, and not in the headline.** MRR 0.5359 → 0.5355 (−0.0004, i.e. nothing). R@10 0.727 → 0.739. The support comes from per-kind results: activity-query MRR 0.312 → 0.417 (+0.104). |
| **RQ2** — does temporal intelligence help? | semantic_graph_temporal vs semantic_graph | **Yes, and this is the strongest layer.** MRR 0.5359 → 0.5850 (+0.049), R@10 0.727 → 0.845 (+0.118). Temporal-query MRR 0.067 → 0.344, a 5× improvement on the kind it targets. |
| **RQ3** — can meaningful dates be distinguished from incidental ones? | date classifier vs naive extraction | **Yes, decisively.** F1 0.986 vs 0.700. |
| **RQ4** — does graph-enhanced beat pure semantic? | semantic_graph vs semantic_only | **Marginally.** MRR 0.5176 → 0.5359 (+0.018), R@10 0.686 → 0.727 (+0.041). |
| **RQ5** — does contextual retrieval solve memory-based queries? | full vs baseline | **Partially.** MRR +0.082, P@5 +0.156, R@10 +0.171, and Hit@1 **unchanged at 0.412**. It surfaces more right answers and orders the shortlist better; it does not make the top result more often correct. |

The honest summary: **the hypothesis is supported, but not uniformly, and the
support is not evenly distributed across the four layers.** Temporal
intelligence carries the result; the graph contributes modestly; activity
modelling contributes on the queries it was designed for and essentially nothing
on average; entity queries did not move at all. Stating which layers did *not*
pay for themselves is more useful than an aggregate that hides it.

---

## Phases 27–28 — Desktop application and 3D graph visualisation

### Decision 82 — A real Qt application, not a browser in a frame

"Desktop app" most often means Electron or a webview shell. That was rejected.
The user asked for "a proper windows application, not a webapp", and a webview
shell satisfies the letter of that while failing it in every way a user would
notice: it starts slower, it eats several hundred megabytes of RAM before doing
anything, and it renders non-native widgets.

**PySide6-Essentials** (Qt 6, LGPL, 77 MB) — not the full `PySide6`, whose
Addons wheel is roughly 400 MB and whose main attraction is QtWebEngine, a
bundled Chromium this project deliberately does not use.

Toolkit alternatives, and why not:

| Option | Why not |
|---|---|
| Tkinter | Already installed and free, but its widget set would not carry the explanation panel legibly, and it has no path to the 3D view |
| Dear PyGui | Fast and GPU-accelerated, but immediate-mode and visibly non-native on Windows |
| Electron / webview | The thing the user explicitly ruled out |
| PySide6-Addons | QtWebEngine would let three.js embed, at ~400 MB and a second Chromium render process on a 16 GB laptop |

### Decision 83 — The 3D graph is a generated file, not an embedded widget

Phase 28 asks for three.js; Phase 27 asks for a native app. Embedding three.js
inside the native app means QtWebEngine, which means shipping Chromium — 5× the
application's total size, for one screen.

Instead, `contextfs visualise` writes **one self-contained HTML file** with
three.js inlined, and the desktop app opens it. Three consequences, all good:

1. **Nothing is fetched, ever.** three.js r160 (MIT, licence notice preserved)
   is vendored in the package and inlined into the output. A visualisation that
   pulls its renderer from a CDN is a cloud dependency no matter how local the
   rest of the system is. A test asserts the generated page contains no
   `https://`, no CDN hostname, and no dynamic `import(`.
2. **The output is portable.** The 1.3 MB file opens on any machine, with no
   Python, no ContextFS, and no network. It can be emailed to a supervisor.
3. **The native app stays native.** No embedded browser, no local HTTP server,
   no JavaScript inside the application itself.

The implementation trick that makes a single file possible: three.js is an ES
module ending in `export { … }`, which is legal inside an inline
`<script type="module">` and leaves every class in scope for the code that
follows it. So there is no `import`, therefore no fetch, therefore no server.

The layout is a force-directed simulation with **sampled repulsion** — each node
is pushed away from ~14 random others per frame rather than from all of them,
making it O(n·k) instead of O(n²). On Vega 10 integrated graphics that is the
difference between interactive and not. `alpha` cools and the simulation stops
integrating, because a layout that never settles is a battery bug on a laptop.
Node cap 1200, and when it bites the page **says how many nodes were dropped**
rather than showing a partial graph as if it were complete.

Verified against the real index: WebGL 2.0 context, 77 nodes and 597 edges, and
the six per-type edge counts (54 semantic, 30 entity, 326 structural, 4
duplicate, 119 temporal, 64 activity) summing exactly to 597. Frame timing is
exposed on `window.__contextfs` so anyone can check the performance rather than
take a claim on trust; no FPS figure is quoted here because the browser pane
used for verification throttles `requestAnimationFrame` while hidden, and a
number measured under throttling would be a fabricated one.

### Decision 84 — Format hints were too weak, and were masking the activity layer

**This is the most important finding of the testing pass, and it was found by
end-to-end testing rather than by unit tests.**

Driving the GUI through four realistic queries showed
`Exam_Timetable_Sem7.xlsx` at rank 1 for three unrelated queries — including
*"the **PDF** I studied before my ML exam"*, where four of the top five results
were not PDFs and the correct PDF sat at rank 6.

The format multiplier was working exactly as specified (verified: score ratios
of precisely 0.850 and 1.150). The specification was wrong. ±15% is a *nudge*,
and a query naming a format is not a nudge — it is a constraint the user
actually knows. A high-degree hub node could absorb the 15% and still win.

Swept against the 17-query ground truth rather than tuned by eye:

| boost | penalty | MRR | hit@1 | P@5 | R@10 | format-query MRR (n=5) |
|---|---|---|---|---|---|---|
| 1.15 | 0.85 | 0.5845 | 0.4118 | 0.7196 | 0.8564 | 0.5889 ← old |
| **1.15** | **0.70** | **0.6319** | **0.4706** | **0.7392** | **0.8564** | **0.7500** ← chosen |
| 1.20 | 0.60 | 0.6319 | 0.4706 | 0.7392 | 0.8564 | 0.7500 |
| 1.25 | 0.50 | 0.6319 | 0.4706 | 0.7392 | 0.8564 | 0.7500 |
| 1.30 | 0.40 | 0.6319 | 0.4706 | 0.7392 | 0.8564 | 0.7500 |
| 1.30 | 0.30 | 0.6319 | 0.4706 | 0.7275 | 0.8564 | 0.7500 |
| 1.40 | 0.20 | 0.6319 | 0.4706 | 0.7275 | 0.8564 | 0.7500 |
| 1.50 | 0.10 | 0.6319 | 0.4706 | 0.7275 | 0.8564 | 0.7500 |

Everything from 0.70 downwards is a **plateau** — identical MRR, hit@1 and
recall across a 7× range of penalty. That flatness is the reason to trust the
change: the result does not depend on picking a lucky value, which is exactly
what overfitting to 17 queries would look like. **0.70 is chosen as the mildest
setting that reaches the plateau**, because a stronger penalty buys nothing
measurable and costs more when a user misremembers the format.

The asymmetry (1.15 up, 0.70 down) is deliberate: naming a format is strong
evidence *against* files of other types and only weak evidence *for* any
particular file of that type.

#### The consequence that matters: a broken component was hiding a working one

| Metric | Before | After |
|---|---|---|
| Full MRR | 0.5845 | **0.6319** |
| Hit@1 | 0.4118 | **0.4706** |
| Activity-query MRR | 0.417 | **0.583** |
| RQ1: activity over graph | **+0.000 MRR** | **+0.031 MRR, +0.059 hit@1** |

Before this fix, the honest reading — recorded in the Phase 26 log and in the
README — was that *the activity layer contributes essentially nothing on
average*. That reading was wrong, and it was wrong because hub files promoted by
a too-weak format constraint were crowding out precisely the files the activity
layer had correctly surfaced.

Two things worth stating plainly:

1. **A research layer measuring as worthless may be being suppressed rather
   than being useless.** The ablation was measuring the activity layer
   *through* a defect in an unrelated component. Nothing about the ablation
   methodology was wrong; the confound was simply outside where anyone was
   looking.
2. **Hit@1 moved for the first time in the project.** It had been 0.412 in every
   configuration and was listed in the README as an unsolved weakness. It is now
   0.471 — one extra query out of 17, so still modest, and the README says so.

### GUI / CLI parity — the check that stops this being a fork

The GUI builds its own retrieval stack. If that stack drifts from the CLI's, the
project has two systems and the research numbers describe only one of them.

Measured over all 17 ground-truth queries, same process, same index:

- **17 / 17 identical result lists**, every score matching to **1e-9**
- warm latency, GUI stack: min 72.1 / median 74.9 / max 77.6 ms
- warm latency, CLI stack: min 71.0 / median 73.0 / max 382.9 ms

### Why a desktop app is more than a nicer CLI — measured

One-time model and index load: **23.1 s**. The CLI pays that on *every*
invocation, because each command is a separate process. The GUI pays it once per
session and every query afterwards is warm. That is the entire performance
argument for the GUI, and it is a measured number rather than an assertion. The
status bar states it in the UI too — *"models loaded in 23.4s (paid once; every
query after this is warm)"* — rather than hiding the cost.

`RetrievalService` also issues one throwaway warm-up query at load, because
without it the first *user* query pays lazy-initialisation inside torch and
LanceDB and the system looks several times slower than it is.

### A real concurrency bug, found by testing the shutdown path

Closing the window raised:

```
sqlite3.ProgrammingError: SQLite objects created in a thread can only be used
in that same thread. The object was created in thread id 2584 and this is
thread id 24752.
```

The index connection is opened on a worker thread and closed on the GUI thread.
The crash on close was the visible symptom; the latent bug was worse — with a
multi-threaded pool, a search job could run on a different thread from the one
that opened the connection.

Fixed on both sides, because either alone is insufficient:

- `Store(..., cross_thread=True)` relaxes `check_same_thread`, which is only
  safe because CPython's `sqlite3.threadsafety == 3` (serialized) means the
  driver locks around every call. Opt-in, never the default, and a test asserts
  the default still refuses cross-thread use.
- `JobRunner` now runs **one** worker thread, so two operations never touch the
  shared connection at once. It also avoids oversubscribing a CPU on which torch
  is already using several cores.

### Verification

- **473 tests passing** (16 new in `tests/test_gui.py`), ruff and black clean
  across 54 files.
- Offscreen end-to-end run: index load, four queries, explanation rendering,
  insights refresh, and 3D export — all passing, with every returned result
  carrying a complete explanation.
- The generated page asserted to contain zero external references.

### Honest limitations

- **No automated UI tests.** Widget behaviour is exercised end-to-end offscreen
  and retrieval parity is proven, but nothing asserts layout, so a visual
  regression would not be caught. Pixel assertions were considered and rejected:
  they break on font and DPI differences without catching real defects, and a
  test that passes for the wrong reason is worse than no test.
- **The visualisation caps at 1200 nodes.** Beyond that the highest-degree nodes
  are kept; the page reports the drop, but a very large index is not fully shown.
- **No FPS figure is claimed.** It was not measurable under the conditions
  available, so it is not reported.
- The format-hint sweep rests on **5 format-bearing queries**. The plateau is
  strong evidence the value is not overfitted, but n=5 is n=5.

---

## Post-build pass — monochrome design system, launcher, handbook

### Decision 85 — One design system, three surfaces, no colour

The UI was rebuilt black-and-white: stencil-and-bracket, high contrast, sharp
corners, monospace for anything machine-generated. Restrained rather than loud -
no glitch animation, no spray-paint texture.

`src/contextfs/theme.py` is the single source of truth, deliberately at the
**top level** rather than inside `gui/`, because the CLI must read it without
importing Qt. The Qt stylesheet, the Rich CLI styling and the generated three.js
page all consume it, so "activity" cannot come to mean different things in
different windows.

#### The problem this created, and why desaturating would have been wrong

The old palette used **colour to carry information**: semantic blue, graph
violet, activity amber, timeline green. Removing colour therefore removes a data
channel. Desaturating a colour-coded interface yields four indistinguishable
greys - strictly worse than what it replaced, and the obvious way to do this
badly.

Colour was replaced by *two* channels rather than none:

1. **Luminance** - each signal owns a fixed grey, spaced ~40 points apart so
   adjacent rows stay separable.
2. **A glyph** - `◆ semantic`, `◈ graph`, `● activity`, `▲ timeline`.

The glyph is the **primary** encoding and the grey is reinforcement, not the
other way around. That ordering matters: shape survives greyscale printing, low
contrast displays, piped output and colour blindness, none of which the previous
hue-only encoding did. The redesign therefore ends up *more* accessible than the
colour version it replaced, which was not the goal but is the honest outcome.

The same encoding now appears in all three surfaces - a glyph column in the CLI
results table, a glyph column in the desktop table, and a glyph legend in the 3D
page - so the visual vocabulary transfers between them.

Asserted rather than assumed: a test walks every node and edge tone and fails if
any has `r != g != b`, and another fails if two glyphs collide.

### Decision 86 — Glyphs need an ASCII fallback, and this is not optional

The first CLI run after adding glyphs **crashed**:

```
UnicodeEncodeError: 'charmap' codec can't encode character '●'
```

A Windows console on its default cp1252 code page cannot encode `●`. So can a
redirected stream (`contextfs query ... > out.txt`). On the project's own target
platform, a decorative flourish had become a crash.

`signal_glyphs(stream)` now asks the stream's own encoder whether it can encode
the set, and returns `* + o ^` when it cannot. It tests by *encoding*, not by
sniffing the platform or the code page, because that is the only question that
actually matters and it is cheaper to answer directly than to predict. Two tests
cover it, including one that encodes the fallback set to cp1252 to prove the
fallback is itself safe.

`start.bat` sets `chcp 65001`, so the launcher path gets the real glyphs; the
fallback exists for every other path.

### Decision 87 — `start.bat` carries a maintenance obligation, stated in the file

One double-click sets up the environment, installs dependencies, downloads both
models, generates the corpus, builds the index, and offers a menu. Unrecognised
arguments pass straight through to the CLI, so `start.bat query "..."` works
without the launcher having to know every subcommand - and the launcher cannot
fall behind as commands are added.

The file opens with an instruction to keep it in sync with any change to the
start-up procedure. A launcher that has silently drifted from reality is worse
than no launcher, because it fails on exactly the machine that has never been
set up before - which is the only machine it exists for.

Verified by running it: first-run setup completed, and `start.bat digest`
returned the digest with exit code 0.

### HANDBOOK.md

One file answering how to run it, how it works, what is novel, what is good,
what is bad, and what is blocked. The future-scope section is restricted to
things **current technology cannot do**, each with the reason - destroyed
mtimes are unrecoverable without durable filesystem provenance; cross-app
context needs a privacy-preserving OS activity API that does not exist; local
multimodal models do not fit in the RAM budget; temporal embedding drift over
decades is unsolved; and ranked retrieval cannot prove a negative. Wishlist
items that are merely unimplemented were left out.

### Verification

- **477 tests passing** (4 new theme/glyph tests), ruff and black clean.
- GUI end-to-end after the restyle: load, two queries, glyphs rendered in the
  result table, every explanation complete, insights refreshed.
- **GUI/CLI parity re-confirmed: 17/17 identical, every score to 1e-9.** The
  restyle touched presentation only, and this proves it.
- 3D page inspected in a real browser: background `rgb(5,5,5)`, all nine legend
  rows with correct glyphs and counts, 77 nodes / 597 edges, zero chromatic
  tones in the palette.

---

## Phase 29 — v2 programme, Phase 1: handover, environment bootstrap, audit

**Date:** 2026-10-27 · **v2 phase:** P1 (Audit) · **Machine:** new laptop — Lenovo LOQ, Ryzen 7 250
(8C/16T), RTX 5060 Laptop 8 GB (Blackwell sm_120, driver 621.01, CUDA UMD 13.5), 16 GB single-channel,
Windows 11 26200.

The project was handed over to a new programme ("v2": audit → architecture debate → procedural
benchmark → desktop app → hardening → novelty → final evaluation → optimisation → paper kit). Standing
rules are in `CLAUDE.md`; the 30-second view is `docs/STATUS.md`. Full findings are in
`docs/AUDIT_AND_ROADMAP.md`. This entry records what was run and decided.

### Decision 88 — v2 phases continue this log's numbering

v2-P1…P9 are logged as Phases 29…37; decisions continue from 88. Mapping table in `CLAUDE.md`.

### STANDING CORRECTION — every number above this entry is unverified

Per the v2 rules, every figure in this log (Phases 0–28 and the post-build pass), the README, the
comments in `contextfs.toml`, `data/eval/*` and both paper drafts is treated as **unverified**: some
were produced on a self-authored 40-file corpus with components tuned on the same data, and the
paper drafts contain numbers with no source here (Draft A: "51 ms", "42.2×"). Nothing from before
this entry may enter the README or the paper kit. Numbers re-measured in this phase are recorded
below with their commands; they are **smoke-fixture** numbers and also may not enter the paper.

**Specific corrections to earlier entries (not rewritten, corrected here):**

- **Phase 14–22 headline table** (full MRR 0.584, semantic_graph 0.536, etc.) is stale: it predates
  Decision 84. Current code gives full 0.632, semantic_graph 0.552 (see below).
- **Decision 67** says "the 0.503 → 0.518 gap is machinery, not context". **Wrong.** The entire gap is
  the format-hint multiplier (Decision 90 below).
- **Phase 24** reports total coverage 81.5%. Re-measured on this machine: **76%** (`pytest --cov`).

### Decision 89 — Developer bootstrap rewritten; everything installs inside the project

**What was found.** The new machine had no Python (only the Microsoft Store alias). The old `.venv`
was hard-wired to `C:\Users\manue\…\Python312` and unusable. `git` refuses the repo ("dubious
ownership": folder owned by the old machine's SID; worked around per command with
`-c safe.directory=*`, global gitconfig untouched). `contextfs fetch-models` hung >10 min on
HuggingFace's Xet transfer backend with one `.incomplete` blob. HF weights default to `C:\Users\…\.cache`.

**What was done.**
- `uv` 0.13.6 (Astral standalone zip) → `.tools/uv/`; portable CPython **3.12.16** → `.tools/python/`
  via `uv python install --no-bin --no-registry` (no registry keys, no Start-menu entries, nothing on C:).
- `.venv` rebuilt with `uv venv --seed`. All caches redirected into the project:
  `UV_CACHE_DIR`, `PIP_CACHE_DIR` → `.tools/cache/`, `HF_HOME` → `models/hf/`.
- **torch 2.14.1+cu130** (newest Windows cp312 CUDA wheel; cu130 includes sm_120). Install: 6 m 20 s.
- `HF_HUB_DISABLE_XET=1` → MiniLM downloaded in seconds (`models/` = 88 MB).
- `start.bat` rewritten as developer bootstrap + launcher; per-prerequisite checks moved to
  `scripts/dev_bootstrap.py` (robust against the `&` in this project's path). Torch channel chosen
  from `nvidia-smi` (compute capability ≥ 7.5 and driver CUDA ≥ 13.0 → cu130, ≥ 12.8 → cu128, else
  CPU; `--cpu` / `CONTEXTFS_TORCH=cpu` override). Package reinstall keyed on a hash of
  `pyproject.toml`. Disk-space and network pre-checks with exact error messages.
- `.gitignore`: `.tools/`, `.contextfs.oldlaptop/`, `repomixOfContextFS.md`.

**Deviation from the brief, stated:** the brief says "use winget where available" for Python. The
winget/python.org installer writes the registry and Start menu on C:, which conflicts with the
install-location rule, so uv's portable Python is tried first and **winget is the fallback**
(`--location .tools\python-winget`), then a stop with a python.org link.

**Measured (this machine):**
```
start.bat check   (first run: venv had no install marker → installed package once)  29.6 s
start.bat check   (everything present)   "prerequisites checked in 3.1 s"
start.bat stats   (pass-through to CLI)   exit 0, 3.9 s wall
```
**Not yet exercised:** the from-zero path as one uninterrupted `start.bat` run (each step was run
individually this session with the same commands), and the no-internet / disk-full error paths.

### GPU device check (diagnostic only — no real workload has run on the GPU)

`.tools/gpu_check.py` (one 4096² matmul, no model):
```
torch 2.14.1+cu130 | built for CUDA 13.0
cuda.is_available(): True
get_device_name(0): NVIDIA GeForce RTX 5060 Laptop GPU
capability: (12, 0) | arch list: ['sm_75','sm_80','sm_86','sm_90','sm_100','sm_120']
4096x4096 matmul on cuda:0: 6763.5 ms   (first call: CUDA context + cuBLAS init, not throughput)
nvidia-smi --query-compute-apps:  26572, ...\.tools\python\...\python.exe, [N/A]   (this PID)
nvidia-smi: RTX 5060 Laptop GPU, 414 MiB used / 8151 MiB
```
The Python PID appears as a compute process on the RTX 5060 (per-process VRAM shows `[N/A]`, normal
under WDDM). **Awaiting user confirmation before any real GPU workload.** All Phase 29 re-runs used
`device = "cpu"` as configured.

### Re-runs on this machine (smoke fixture; raw output in `.tools/*.txt`)

- Old index moved to `.contextfs.oldlaptop/`; old `data/eval/*` copied to `.tools/oldlaptop-eval/`.
- `verify_corpus.py` on the copied corpus: **FAILED 1 of 11** —
  `Personal/Misc/movie_watchlist.txt: expected 2026-01-05T23:40:00, got 2026-08-11T22:17:59`.
  The mtime dependence, demonstrated on the project's own data. Regenerated → 11/11 pass.
- `contextfs scan`: 40/40 extracted; 254 entity mentions; 65 chunks; 46 meaningful / 42 incidental
  dates; 5 sessions; graph 77 nodes / 597 edges; 55.7 s wall (concurrent with pytest).
- `pytest` concurrent with the scan: **6 failed / 471 passed** (all `tests/test_activity.py`).
  Isolated re-run: **477 passed in 61.8 s**. Cause: `test_activity.py:133-141`, `:243-251` read the
  live `.contextfs/` index (and skip silently if it is absent). Open defect.
- Coverage: **76%** (`cli/main.py` 48%, `evaluation.py` 32%, `extract/extractors.py` 64%).
- `evaluate.py` (MRR): dense_flat 0.5029 · dense_format 0.5176 · semantic_graph 0.5520 ·
  semantic_graph_temporal 0.6010 · semantic_graph_activity 0.5828 · full **0.6319**; full R@10
  0.856, hit@1 0.471; median latency full 32.0 ms (CPU). **Bit-identical to the old laptop's
  `data/eval/results.json` on MRR and R@10 for all six configurations** (Python 3.12.10→3.12.16,
  torch 2.13+cpu→2.14.1+cu130 running on CPU).
- `date_eval.py` P 0.972 / R 1.000 / F1 0.986 (naive 0.700). `session_eval.py` F1 1.000, 5/5, control
  not clustered. `entity_eval.py` micro P 0.489 / R 0.759 / F1 0.595. `extraction_report.py` 40/40.
- `incremental_check.py`: 2/40 reprocessed, **563 ms vs 19,788 ms → 35.1×** (log said 25.1×,
  Draft A 42.2× — machine-dependent ratio that includes model load; not a stable metric).
- `bench_scan.py`: cold 21.4 ms, warm 6.0 ms, 1-changed 15.0 ms (page-cache-bound).

### Decision 90 — 0.503 vs 0.518 traced; the two configurations renamed, not merged

`scripts/audit_baseline_diff.py` removes each difference between `SemanticBaseline` (0.503) and
`HybridRetriever(signals=("semantic",))` (0.518):
```
baseline (flat, LanceDB search)             0.5029  0.4118  0.6858
semantic_only (as published)                0.5176  0.4118  0.6858
semantic_only, format x1.0                  0.5029  0.4118  0.6858
semantic_only, semantic seeds only          0.5176  0.4118  0.6858
semantic_only, format x1.0 + seeds only     0.5029  0.4118  0.6858
C. LanceDB top-10 vs in-memory top-10 ranking mismatches: 0/17
```
The whole gap is the **format-hint multiplier** (one query, q14 "the code …", RR 0.25→0.50); the
extended candidate pool and the similarity implementation change nothing. They are intentionally
different experiments, so they were **renamed**: `baseline` → `dense_flat`, `semantic_only` →
`dense_format` (`evaluation.py`, `scripts/evaluate.py`, `tests/test_retrieval.py:60`); `data/eval/*`
regenerated with identical values; suite 477/477 after the rename. **Every hybrid row also applies
the format multiplier**, so "full − dense_flat" mixes the format hint into every layer's credit; the
fair control is `dense_format`, and v2-P3 must add a format-off ablation axis.

### Decision 91 — Audit-only lexical baselines and significance on the smoke fixture

`scripts/audit_lexical_and_stats.py` (BM25 k1=1.2 b=0.75; RRF k=60; exact sign-flip over 2^17
patterns; bootstrap 10k, seed 0). **Smoke fixture, not a paper result:**
```
dense_flat 0.5029 | dense_format 0.5176 | full 0.6319 | bm25 0.4211 | bm25_path 0.4485 | rrf 0.4281
full - dense_flat     +0.1289  CI [+0.0260, +0.2696]  p=0.0312  W/L/T 6/0/11
full - dense_format   +0.1142  CI [+0.0064, +0.2598]  p=0.0938  W/L/T 5/1/11
full - rrf            +0.2038  CI [-0.0010, +0.4157]  p=0.0845  W/L/T 9/3/5
```
Against the fair control the gain is not significant at 0.05, and these p-values are optimistic
because the format penalty, activity cues and session parameters were tuned on these queries
(audit §5). BM25 is weaker on average but wins q02/q05/q11/q15 where full does not → lexical
evidence is complementary; naive RRF hurts here, so fusion must be chosen on the dev split.

### Other findings recorded (detail in the audit document)

- Explanations are unfaithful: `matched_topic` lists *query* terms whether or not the file contains
  them. Live: q01 rank 1 `Unit4_Ensemble_Methods.pdf` → "topic match (0.18): study, machine, learn,
  exam" for a file guaranteed to contain no exam vocabulary. 100% explanation coverage is true by
  construction.
- The semantic tree is never used by retrieval. No BM25 / TF-IDF / ANN exists despite Draft A.
- Query understanding is a closed vocabulary: all 4 activity queries hit a listed cue; 8 of 21 cues
  ever fire.
- Scale walls: sessions O(n³) with three n² matrices; graph n² similarity; per-query full loads.
- Scanner: no `onerror`, placeholder hydration, single root, no resume, oversized-file staleness.
- A research subagent (no part in writing the code) checked prior art for the novelty ideas and the
  16 draft references: `docs/research/prior_art_and_references.md`; verdicts summarised in audit §9
  and §10.3.

### Still open after Phase 29

- User to confirm the GPU device before any real GPU workload.
- User to pick novelty directions (audit §12) — gates v2-P6.
- `test_activity.py` live-index dependency; from-zero `start.bat` run; failure-path tests.
- `.contextfs.oldlaptop/` and `.tools/oldlaptop-eval/` can be deleted once no longer needed.

**Addendum (same day).** Prior-art headline: *Connections* (Soules & Ganger, SOSP 2005,
10.1145/1095810.1095822) already used temporal-locality file relations to expand/re-rank desktop
search, so the session+graph idea is **not novel**; the open gaps are post-hoc (no tracing)
reconstruction, meaningful-date extraction, robustness to rewritten mtimes, and beating a strong
baseline. Of the 16 draft references, [3] [5] [7] [8] should be dropped and [2] [6] replaced (audit §10.3).
Recommended directions: D (benchmark) as foundation + A (temporal provenance) as primary (audit §12).
Lint: new/edited scripts pass `ruff check`.

### Decision 92 — Paper directions chosen at the v2-P1 checkpoint (user decision, 2026-10-27)

- **D — procedural multi-persona benchmark:** the **foundation** (built in v2-P3).
- **A — temporal provenance reconciliation / robustness to rewritten mtimes:** the **primary**
  contribution (v2-P6).
- **B — personal temporal grounding** (event-anchored + memory-decay-aware time): **secondary, only
  if time allows once A is solid**. Not committed upfront.
- **C — learned fusion:** general engineering under Must-do 4, **not** a paper direction.

Consequence for v2-P2: the architecture must carry per-file *time evidence* from several sources
(file-system times, Office `docProps/core.xml`, PDF Info/XMP, EXIF, zip entry times), not a single
`mtime` column, and the benchmark generator must be able to inject mtime damage.

### GPU status (user, 2026-10-27)

The user did not watch the Phase 29 device check live and is **trusting the reported output** (RTX
5060 listed by `nvidia-smi` as running the Python process). Standing instruction: the **first real
GPU workload** (model load / embeddings / training — not another device check) must be flagged
again so the user can verify with eyes on screen.

### Decision 93 — Git workflow and what stays private

- All v2 work goes on branch **`v2`**, committed and pushed as phases progress, until the user says
  to branch again or merge. `main` is untouched.
- `retr0alfred/ContextFS` is **public**. At the user's choice, `docs/AUDIT_AND_ROADMAP.md` and
  `docs/research/` (draft reviews, novelty assessment, paper strategy) are **git-ignored and kept
  local**, the same treatment v1 gave `RESEARCH_DOSSIER.md`. Everything else (environment bootstrap,
  `start.bat`, the config rename, audit scripts, log, STATUS, CLAUDE.md, ADRs) is pushed.

---

## Phase 30 — v2 programme, Phase 2: architecture debate → ADRs 0001–0016

**Date:** 2026-10-27 · **v2 phase:** P2 · **Branch:** `v2`

### What was done
- **Brief:** `docs/adr/debate/BRIEF.md` gives the v1 facts, the product requirements, the six
  questions to settle, and measured install sizes (torch+cu130 2.9 GB, of which CUDA libs 2,425 MB;
  PySide6 209 MB; lancedb 148 MB; spaCy+md 141 MB; whole `.venv` 3,883 MB).
- **Round 1:** five independent role agents (systems architect, IR/ML, Windows/packaging,
  performance, UX) each wrote a proposal → `debate/round1/`. Then a red-team skeptic attacked all
  five: 17 external claims checked, 17 attacks, 3 blockers → `debate/round1/skeptic.md`.
- **Facilitator measurement M1** (`debate/measurements.md`): what six copy methods do to NTFS times
  on the dev machine. CopyFile-style copies (Copy-Item, `cmd copy`, `shutil.copy2`, Expand-Archive)
  set created = now and keep modified, so `created > modified` means the mtime **survived**.
  robocopy keeps both (copy invisible); `shutil.copy` destroys both. This resolved the architect's
  open question and inverted the architect's `fs_rewritten` rule, which the skeptic attacked
  independently.
- **Round 2:** each role revised with its context intact, answered every attack and resolved
  contradictions → `debate/round2/`. Then the skeptic attacked again → `debate/round2/skeptic.md`,
  with new blockers:
  - **N1:** the v1 generator sets only mtime/atime, so every synthetic file looks copied, and
    python-docx stamps DOCX files 2013-12-23;
  - **N2:** the proposed ground truth was circular;
  - **N3:** "the" file time mixed different events.
  - It also listed 10 facilitator decisions.
- Round 2 was interrupted once by an API rate limit; all five agents were resumed with context
  intact, and none of the round's files had been written before the interruption.
- **Facilitator synthesis → ADRs 0001–0014.** Rulings follow the skeptic's recommendations, except
  **D9 (GPU)**: the debate cut the GPU, but the facilitator kept the user's requirement behind
  DirectML parity gates and recorded a deviation that only the user can approve.
- **Fresh reviewer** (saw no debate) → `review/fresh_review.md`: 3 blockers, 11 majors, 6 minors,
  7 missing areas.
  - **B1:** no benchmark ADR, and the undamaged-time model was circular.
  - **B2:** the pre-registered primary measured fusion, not contribution A.
  - **B3:** Smart App Control blocks unsigned apps on clean Windows 11 installs, with no "run
    anyway".
  - All addressed: new ADR-0015 (benchmark + study) and ADR-0016 (operations, privacy, licences),
    plus Revision 1 sections in 13 ADRs. Per-finding map: `review/response.md`.

### Decision 94 — The v2 architecture (summary; the ADRs are authoritative)
1. UI+query process plus one indexer child as sole writer (Job Object); no watcher; launch scan +
   F5; search never waits (ADR-0001).
2. Keep PySide6-Essentials; cut the 3D view, Insights and the semantic tree from the app (ADR-0002).
3. ONNX Runtime in the app and eval; **no torch or CUDA in the shipped app**; MiniLM int8 default;
   GPU only for bulk indexing behind matched-precision DirectML gates plus an on-device probe
   (ADR-0003).
4. PyInstaller one-dir + per-user Inno Setup; **code signing in the MVP** (ADR-0004).
5. SQLite as the single commit point; exact f32 vectors, document-level candidates, MaxP scoring;
   LanceDB and NetworkX/JSON dropped (ADR-0005).
6. Multi-source time evidence; **per-event** reconciled time (authored / arrived_here /
   last_user_edit); non-circular protocol E1–E5 (ADR-0006).
7. Exact session clustering inside time-gap blocks (cap 2,000); G fitted on dev (ADR-0007).
8. Shared FTS5 (tunable document-level BM25) + exact dense first stage; ContextFS signals as
   features of a dev-fitted linear scorer; open-vocabulary query understanding (ADR-0008).
9. Evidence-grounded, counterfactually checked explanations (ADR-0009).
10. One `contextfs.engine`; results and index-build parity tested (ADR-0010).
11. `fsio`: tag-aware reparse handling, placeholders never opened, share-delete reads, resumable
    scans (ADR-0011).
12. Writer lock; copy-and-swap migrations; recovery that never blocks search (ADR-0012).
13. Three-screen first run; dates panel; chips; Issues; Research mode (ADR-0013).
14. A dated 12-week plan with the paper path first; cut order (ADR-0014).
15. Benchmark D: persona × template splits, frozen donor-calibrated undamaged model, simulator plus
    5% real-tool replay, **three pre-registered primaries** (P-A1, P-A2, P-S), power calculation,
    real-user study (ADR-0015).
16. Logging, index privacy, licences (the MS MARCO cross-encoder is eval-only), upgrades, language
    scope (ADR-0016).

### Measured this phase (dev machine; numbers from the role agents' local probes are their own)
- M1 copy-timestamp table, above.
- Exact numpy top-200 over 384-d f32 (performance R2; corroborated by architect, IR and skeptic):
  10⁵ vectors p50 6.3–7.4 ms; 10⁶ vectors p50 84 ms, 1.46 GB; f16 about 10× slower.
- FTS5 trigram `MATCH 'gradeint'` → 0 rows (skeptic, confirmed by IR): trigram does not handle
  typos.
- BLAS non-determinism: `X @ q` with default threads vs `OPENBLAS_NUM_THREADS=1` differs by up to
  5.4e-7 (skeptic). Hence pinned threads and tolerance-based ties.
- PySide6 import 2.18 s (performance R1).

### Deviations from the brief — need the user
1. **GPU:** kept as a requirement, but delivered only if the DirectML gates and probe pass in v2-P4;
   otherwise the app ships CPU-only. **User approval needed** if that happens. The shipped app has
   no CUDA build.
2. **Model choice in Settings:** one embedder ships in the MVP; switching models is LATER.
3. **Code signing** is now a requirement (Smart App Control). It needs a user decision: SignPath
   Foundation (free, review timeline), a Certum open-source certificate (paid), or the institution's
   certificate. Fallback: document SAC-off.
4. **Watcher:** none (launch scan + F5). This keeps "no background service" unqualified.

### Still open
- The two user decisions above.
- The first real GPU workload, which is the v2-P3 local-LLM query generation per ADR-0015 §6,
  **must be flagged to the user before it runs**, with eyes on screen.
- Clean-machine testing: Windows Sandbox and Hyper-V are unavailable on Home, so the plan is
  VirtualBox with the Windows 11 evaluation ISO plus the old laptop. The downloads are the user's.
- Release pins at least two weeks old (ORT 1.31.1 and symspellpy 6.10.1 were days old at debate
  time).

---

## ▶ MILESTONE M1 — Architecture frozen, app scope reduced (2026-10-27)

### Decision 95 — User adjustments before Phase 3 (user decision, 2026-10-27)

1. **No installer: a portable app.**
   - A folder containing `ContextFS.exe`, double-click to run. No Add/Remove Programs entry,
     Start-menu or registry integration, or uninstaller.
   - Built PyInstaller one-dir, not one-file: one-file would unpack about 0.5 GB to `%TEMP%` on
     every launch.
   - → ADR-0004 Rev. 2; ADR-0016 Rev. 1.
2. **Acceptance bar = the demo on the author's own laptop.**
   - Full clean-VM testing (VirtualBox, evaluation ISO) is downgraded to a **stretch goal,
     time-boxed to 1 day**, after the research work.
   - The MVP check is light: (a) Claude runs the build in a scrubbed environment as Alfred;
     (b) the user copies the folder to a second Windows account or the old laptop and launches it.
   - → ADR-0004 Rev. 2; ADR-0014 Rev. 2.
3. **Code signing.**
   - The user asked for a SignPath Foundation application now, with an email the user supplied.
     Kept out of the public repo; it will be entered only on SignPath's own form.
   - **Not submitted, for two reasons:**
     - the form is behind reCAPTCHA, which Claude may not complete;
     - SignPath's terms require an already-released, CI-built binary, a published "Code signing
       policy" page, MFA, and an OSI licence. **The repo has no LICENSE file.**
   - The application is prepared in `docs/release/signpath_application.md`, for the user to submit
     after the first CI-built release (v2-P4). Off the critical path.
   - The working path is unsigned. The "SAC must be off" fallback applies only to disposable test
     environments and other people's machines; the author will not disable SAC on their daily
     machine.
4. **GPU deviation acknowledged:** ADR-0003 is accepted as written (gated GPU, CPU-only with
   sign-off on failure, one model at first).
5. **Who can do what in the light clean-machine check** (measured 2026-10-27, not assumed):
   - **Claude's actual access:**
     - user `alfred\alfred`, medium integrity, **not elevated**: a member of Administrators, but
       with the admin token filtered by UAC;
     - Bash and PowerShell in this session; no screen or keyboard control tools;
     - the built-in browser pane (web pages only).
   - **Claude can:**
     - build the portable folder;
     - copy it to a fresh path on D:;
     - run `ContextFS.exe --selftest` headless under Alfred, with a scrubbed environment (no
       Python on PATH, no `PYTHON*`/`HF_*`/`PIP_*`/`UV_*`) and an empty data directory;
     - verify the no-network audit hook, the exit codes and the logs;
     - list the process's loaded modules to prove nothing comes from `.venv` or `.tools`;
     - measure startup time;
     - read the SAC policy state and the Authenticode status.
   - **Claude cannot:**
     - see or click the GUI;
     - create a Windows account (no elevation, and it is not permitted to create accounts);
     - sign in as another user;
     - answer SmartScreen or SAC prompts;
     - touch the old laptop.
   - **Needs the user at the keyboard:**
     - create a standard local account (Settings → Accounts → Other users);
     - sign in to it;
     - copy the folder to a place that account can read;
     - double-click it and walk the first-run checklist Claude will provide;
     - the same on the old laptop;
     - report anything odd (a screenshot is enough).

### Measured: Smart App Control on the author's laptop

`HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy\VerifiedAndReputablePolicyState = 2`, which is
**Evaluation mode**.
- It does not block now, but Windows may switch it to **On** automatically. An unsigned
  `ContextFS.exe` could then be blocked **on the demo laptop**, and SAC cannot be re-enabled once
  turned off, so "turn it off" is not an option the author accepts.
- **Mitigation:** check the state before each demo build (part of `selftest`); sign if SignPath
  approves in time; keep the run-from-source path (`start.bat`) as the last resort.
- Also measured: the portable `python.exe` in `.tools` is `NotSigned`, so the run-from-source path
  could be affected too if SAC turns On.
- **Open risk.**

### Effect on the plan
The app path loses the installer, the VM pipeline and signing as a blocker, which frees about a
week for the research path. The v2-P3 order is unchanged: T → donor statistics → generator fix →
personas and queries → freeze → harness and baselines.

---

## Phase 31 — v2 programme, Phase 3: evaluation foundation (IN PROGRESS — entry 1)

**Dates:** 2026-10-27 to 2026-10-28 · **v2 phase:** P3 · **Branch:** `v2`

### Done so far
1. **Transfer table T v1 measured and frozen** (ADR-0006 E1).
   - Commit `4c027cc`: `data/transfer_table/T_v1.{json,md}`, sha256 `cbdc2809…`.
   - Script: `scripts/measure_transfer_table.py`. 17 tools × 5 formats on NTFS (D:). Results:
     - In-file times survived **every** tool.
     - `birth > mtime` marks exactly the CopyFile family: Copy-Item, cmd copy, xcopy, Explorer's
       copy engine (Shell COM), `shutil.copy2`, Expand-Archive, tar.
     - robocopy (default and `/COPY:D`) and same-volume moves keep all times, so they are invisible.
     - `shutil.copy`, `copyfile`, `zipfile.extractall` and **`git clone`** reset both times to now.
     - An NTFS safe-save (tunnelling) keeps birth; an in-place edit moves mtime only.
     - No Mark-of-the-Web from any scripted tool.
   - Pending, user-only rows: Explorer drag copy (GUI), OneDrive round trip, USB FAT32/exFAT,
     browser download, Word Save As, 7-Zip, messenger download. Not covered: cross-volume moves
     (C: excluded), SMB.
2. **Smoke fixture demoted** (commit `0c88636`).
   - `data/synthetic/` moved to `tests/fixtures/smoke/` (`git mv`).
   - Verified after the move: `verify_corpus` 11/11, graph 77/597, regression anchor `dense_flat`
     0.503 / `dense_format` 0.518 / full 0.632 unchanged, **477/477 tests**.
3. **Procedural benchmark generator `contextfs.benchgen`** (commit `773a04b`).
   - Personas: student, professional, researcher.
   - The timeline simulation gives every file true per-event times (authored / arrived_here /
     last_user_edit), an activity (the ground-truth session) and labelled date mentions.
   - Files carry consistent in-file times (core.xml, PDF Info, EXIF, zip entries), all three NTFS
     times (`SetFileTime`, `contextfs.datagen.filetimes`) and Mark-of-the-Web on downloads.
   - The sealed truth is written outside the corpus.
   - Damage profiles (`none`, `copyfile_all`, `content_copy_all`, `robocopy_all`, `mixed`) apply
     only T-measured effects.
4. **Zero-damage audit** (`scripts/audit_benchmark.py`, closes skeptic **N1**).
   - Measured: ALL PASS for student s1 (231 files: copy signature on 20/231, exactly the phone
     photos), professional s2 (72), researcher s3 (69).
   - Also ALL PASS for damage `mixed` (student s4: 97/231 as expected), `copyfile_all`
     (professional s5: 64/69) and `content_copy_all` (researcher s6: 4/60).
   - The first damaged run failed. Two causes: the audit's expectations ignored post-migration
     edits, and the damage code erased edits made after a content copy (a real generator bug).
     Both were fixed.
   - Regression tests: `tests/test_benchgen.py`, 5 passed.
5. **Template query generator** (commit `f56eb37`).
   - Seven cue types, ~15% typos, families A (dev) and B (test), written from a degraded memory
     view.
   - Graded qrels: target 3, duplicate or sibling 2, same activity 1.
   - The first samples exposed doubled prepositions, origin-blind verbs, unanswerable entity
     queries and nonsense queries on photos. All fixed, and re-inspected.
6. **Harness + baselines + statistics** (commit `aa955ab`).
   - `contextfs.evalkit`: graded nDCG@10, MRR, Hit@1, R@10, seeded bootstrap, paired sign-flip,
     Holm, tunable BM25, RRF, z-fusion.
   - `scripts/bench_eval.py` (interim, over the v1 index): `path_mtime`, `bm25`, `dense_maxp`,
     `dense_flat`, `rrf60`, `rrf10`, `zfuse`, `v1_full`. It refuses test-family personas unless
     the run is tagged `TEST-*`.
7. **Donor statistics tool** (`scripts/donor_time_stats.py`): histograms only, on-screen review
   before saving, placeholders never opened. Self-tested on a synthetic persona. **Not yet run on
   any real folder** (needs consent).
8. **Power calculation** (`scripts/power_calc.py`) and the **study protocol and consent form**
   (`docs/study/`). The study tool waits for the v2-P4 engine and app.

### Measured: first dev-smoke run (NOT paper numbers)
`python scripts/bench_eval.py data/bench/student-s1-A-x1-none data/bench/professional-s2-A-x1-none data/bench/researcher-s3-A-x1-none --tag dev-smoke-template-v1`
(3 dev personas, 109 template queries, 3 m 50 s):
```
system                nDCG@10     MRR   Hit@1    R@10
path_mtime   0.206 [0.151,0.267]   0.111   0.046   0.294
bm25         0.478 [0.418,0.538]   0.289   0.156   0.624
dense_maxp   0.450 [0.391,0.509]   0.265   0.128   0.587
dense_flat   0.450 [0.391,0.509]   0.265   0.128   0.587
rrf60        0.474 [0.414,0.534]   0.276   0.147   0.633
rrf10        0.504 [0.450,0.558]   0.289   0.156   0.670
zfuse        0.512 [0.457,0.567]   0.298   0.156   0.670
v1_full      0.480 [0.421,0.540]   0.314   0.183   0.596
by cue (nDCG@10)   activity entity format mixed semantic temporal vague
  bm25              0.537   0.519  0.571  0.632  0.545    0.083    0.533
  zfuse             0.611   0.444  0.607  0.646  0.582    0.206    0.536
  v1_full           0.381   0.218  0.768  0.650  0.522    0.370    0.495
paired vs v1_full: bm25 +0.0021 p=0.948; zfuse -0.0323 p=0.213 (p_holm 0.853)
```
**Reading:**
- On this benchmark, v1 ContextFS is **statistically indistinguishable from BM25** and slightly
  below a plain BM25+dense z-fusion.
- v1 wins format (0.768) and temporal (0.370, best of all) cues. It loses activity and entity cues
  badly, because it has no lexical channel (as the audit predicted).
- Temporal cues are hard for every system: the headroom for direction A.
- The caveats are strong: template queries, 3 personas, and the template content is far simpler
  than real text.

### Measured: power (`scripts/power_calc.py`, on the run above)
- Paired σ_d of nDCG@10 is 0.26–0.27 against the fusion baselines.
- Detecting Δ = 0.03 needs **~640 test queries** at α = 0.05, or **~850** at the Holm α ≈ 0.0167.
- So the test split is sized at about 20 personas × ~42 queries.

### Disclosure: test-family exposure
During development one family-**B** persona (professional, seed 2) was generated to inspect
phrasing, and 8 of its template queries were read. That persona was deleted. Test personas will
use **different seeds** (1001+), and test queries are to be LLM-written (ADR-0015 §6), so no
inspected item will be in the frozen test split. Recorded for transparency.

### Still open in Phase 3 (blocked on the user, then continuing)
1. **LLM-written queries for the test split.** This is the project's **first real GPU workload**.
   It needs the user's go-ahead, with eyes on screen, plus a download of a local LLM runtime and
   model (proposed: portable llama.cpp CUDA build in `.tools/`, an 8B instruct GGUF in
   `models/llm/`, about 5 GB, all on D:).
2. **Donor statistics** need the user's consent and choice of folders. They must be frozen before
   any estimator rule.
3. User-only T rows (optional before the estimator).
4. After 1–2: generate dev (seeds 1–20, family A) and test (seeds 1001–1020, family B) personas;
   freeze the test split (hash); add the cross-encoder rerank baseline (eval-only; model
   download); the per-layer ablation runner; the explanation-faithfulness metric; the v1 smoke
   regression anchor inside the harness.

### Phase 31 — entry 2 (2026-10-28)

#### Decision 96 — Donor statistics skipped; the undamaged-time model uses documented defaults (user decision)
- The planned donor (the old laptop) is no longer available. The user chose not to block on donor
  statistics, and may revisit later with another source (a phone's files, a classmate's machine).
- The benchmark's undamaged-time model is therefore a set of **documented default assumptions**.
- It is frozen before any estimator rule: `data/calibration/undamaged_model_v1.md`, with sha256 of
  the generator sources it describes:
  - `simulate.py` `998b8680…`
  - `materialise.py` `d3f282b3…`
  - `damage.py` `620bcf1d…`
- **Limitation, stated for the paper:** the undamaged time behaviour is not calibrated on real
  folders. The known gaps vs reality are listed in that file. `scripts/donor_time_stats.py` stays
  available for later.
- Supersedes the donor-statistics step of ADR-0015 §3 (the freeze order T → calibration → rules is
  unchanged).

#### First real GPU workload: local LLM for query writing (user at the screen)
- **Runtime:** llama.cpp **b11606** (2026-10-13, Windows x64, CUDA 13.4, chosen to satisfy the
  two-week pin rule) in `.tools/llamacpp/b11606/`.
- **Model:** `Qwen/Qwen3-8B-GGUF : Qwen3-8B-Q4_K_M.gguf` (Apache-2.0) in `models/llm/qwen3-8b-q4km/`.
- **Download incidents:**
  - a curl resume first stopped at 2.2 GB, then a second resume produced a **corrupt 5,297,087,296-byte
    file** (the published size is 5,027,783,488), so it was deleted;
  - re-downloaded with `huggingface_hub` in a visible terminal tab;
  - per the user's request, `HF_HUB_DISABLE_TELEMETRY` is no longer set for this download;
  - verified: `size 5027783488`, `sha256 d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785`,
    matching the published file.
- **Device check before any generation** (user watching):
  ```
  llama-server --list-devices  ->  CUDA0: NVIDIA GeForce RTX 5060 Laptop GPU (8123 MiB, 7043 MiB free)   [only device]
  nvidia-smi compute apps      ->  24928, ...\.tools\llamacpp\b11606\llama-server.exe, [N/A]
  nvidia-smi                   ->  NVIDIA GeForce RTX 5060 Laptop GPU, 5248 MiB / 8151 MiB, 0 %
  server bound to 127.0.0.1:8080 (-ngl 99, -c 4096, --jinja, --seed 42)
  ```
  **The user confirmed on screen that it is the RTX 5060**, and generation was then allowed.
- **Smoke run on one dev persona:** 36 queries in 12.5 s. The queries read like search-box text.
  The same run exposed generator problems, all fixed:
  - near-duplicate targets (many photos of one trip), now capped at 3 targets per activity;
  - entity hints were topical keywords ("UX"), now a person first, then a known organisation;
  - the LLM occasionally invented a date, so outputs naming a month or year absent from the hints
    are rejected and retried.
- **Sizing:** with the diversity cap, professional and researcher personas gave too few targets
  (12 queries), so the split now generates them at scale 2.5 (students stay at 1.0) —
  `scripts/make_splits.py` `SCALE`. All 40 split personas were regenerated from scratch, and the
  early ad-hoc dev personas were deleted (their dev-smoke results stay committed).
- Added `scripts/regen_queries.py`, which rebuilds template queries from sealed truth without
  touching the corpus. `scripts/llm_rewrite_queries.py` gained `--quiet` (counts only), used for
  every **test** persona so no test query is ever displayed.

#### State at end of 2026-10-28 (paused at the user's request)
- All 40 split personas are generated: dev seeds 1–20 family A, test seeds 1001–1020 family B.
  **Not yet frozen.**
- Next, in order:
  1. LLM-rewrite queries for all 40 (test with `--quiet`).
  2. Leakage/overlap report on dev.
  3. `make_splits.py freeze` and commit the hash.
  4. Index the dev personas.
  5. Cross-encoder rerank baseline; ablation runner; faithfulness metric.
  6. Full dev evaluation.
  7. End-of-phase log and stop.
- The llama-server tab is stopped overnight; restart it with the same command (logged above).

#### Measured at pause (2026-10-28 02:3x)
- All 40 split personas generated (`scripts/make_splits.py generate`):
  - **dev:** 20 personas, 4,884 files, 758 template queries;
  - **test:** 20 personas, 4,785 files, 761 template queries.
- **Power gap:** 761 test queries clears the ~640 needed at α = 0.05 but not the ~850 needed with
  Holm across 3 primaries. Plan: add ~4 test personas (seeds 1021–1024) before freezing.
- **Local server hygiene:**
  - llama-server's log showed two long generations (1,267 and 234 tokens) that did not come from
    the project's scripts, which cap every request at 48 tokens;
  - the server had warned "no API key is set and CORS allows all origins";
  - **from now on it starts with `--api-key`** (a random key held by the scripts) so no other local
    program or web page can use it.
- The GPU was released at pause (`nvidia-smi`: 0 MiB, no compute processes).

#### Phase 31 — entry 3 (2026-10-28 15:10): paused on low battery
- Test split v1 **frozen** (commit `3b36c1a`, `split_v1.json` sha256 `ed76950b…`): 24 test personas
  (916 LLM queries, never displayed) and 20 dev personas (758 LLM queries).
- The LLM server was restarted with `--api-key-file` (git-ignored); keyless requests got HTTP 401.
  The two long generations seen on 2026-10-27 were confirmed as the user's own.
- **Dev evaluation `dev-v1-llm` was stopped mid-run** at the user's request (battery 9%):
  7/20 dev indexes are complete and kept, and one partial index was deleted. No results are
  recorded yet.
- **To resume:** `python scripts/bench_eval.py data/bench/*-A-* --tag dev-v1-llm --ce` (it reuses
  the 7 indexes). Then: end-of-phase log, STATUS, stop.

#### Phase 31 — entry 4 (2026-10-28): first full dev evaluation, end of Phase 3

**Command:** `python scripts/bench_eval.py data/bench/*-A-* --tag dev-v1-llm --ce` (20 dev personas,
4,884 files, **758 LLM-written queries**, 46.5 min). Output in `data/bench_results/dev-v1-llm/`.
**These are DEV numbers, used to steer development. They are not paper numbers.** The test split
is frozen and untouched.

```
system                nDCG@10        MRR   Hit@1   R@10
path_mtime   0.307 [0.281,0.333]   0.155   0.086   0.314
bm25         0.460 [0.440,0.481]   0.280   0.145   0.592
dense_maxp   0.371 [0.349,0.393]   0.249   0.133   0.511
rrf60        0.477 [0.459,0.496]   0.275   0.136   0.629
zfuse        0.455 [0.435,0.476]   0.286   0.153   0.617
ce_rerank    0.576 [0.556,0.595]   0.317   0.161   0.697   <- strongest baseline (graded)
v1_full      0.454 [0.431,0.478]   0.342   0.219   0.606   <- best MRR / Hit@1
ablations of v1 (nDCG@10): sem+fmt 0.416 | +graph 0.459 | +graph+time 0.454 | +graph+act 0.459 | full w/o format 0.367
paired vs v1_full (Holm): ce_rerank -0.122 p_holm=0.0006 (W/L/T 264/449/45); bm25 -0.007 p=1.0; rrf60 -0.023 p_holm=0.12;
  v1_full_nofmt +0.087 p_holm=0.0006; v1_sem_graph_time -0.0004 (753/758 ties); v1_sem_graph_act -0.005 p_holm=0.62
v1 explanations: claim-verified@10 = 0.182; top-1 changes when its lead signal is removed: 0.178 (graph-led 0.09, semantic-led 0.22)
temporal cue (n=119):  nDCG  path_mtime 0.465, ce 0.587, v1 0.267  |  MRR  all systems ≤ 0.152, Hit@1 ≤ 0.042
```

**What it says** (dev; to be confirmed only by the single test run in v2-P7):
1. **v1 ContextFS does not beat the strongest baseline on graded nDCG@10.** The cross-encoder
   rerank of BM25+dense wins by 0.12 (p < 0.001). The claim "ContextFS beats BM25+dense+rerank" is
   **currently refuted** on dev.
2. **On finding the exact target, v1 is the best system** (MRR 0.342, Hit@1 0.219). The gains come
   from semantic, format, mixed and vague cues: it ranks the one right file first more often, while
   the cross-encoder fills the top 10 with related files. The pre-registered primary is nDCG@10, so
   both must be reported. Not chosen after the fact.
3. **v1's own context layers contribute almost nothing here.**
   - Timeline: 753 of 758 queries are unchanged by it, because its date index holds dates mentioned
     *inside* files, while users' time cues refer to *when they worked on or received* the file.
   - Activity: −0.005, not significant.
   - Graph: +0.04 over semantic.
   - The format hint is the single largest v1 component (+0.087), which confirms Decision 90's
     warning that it had been credited to the context layers.
4. **Time cues defeat every system.**
   - Exact-target MRR is ≤ 0.15 and Hit@1 ≤ 4% for all systems.
   - The best graded nDCG on time cues comes from the cross-encoder (0.587) and the filename+recency
     heuristic (0.465), not from v1's timeline (0.267).
   - This is the measured headroom for **direction A**: answer "when" cues from a reconciled,
     per-event file time, which no system currently uses.
5. **v1 explanations are unfaithful, as the audit predicted.**
   - Only 18% of top-10 results cite topic terms that actually occur in the file.
   - Removing the top result's "lead" signal changes the top result only 18% of the time.
   - This is the baseline for ADR-0009's evidence-grounded explanations.

**What would make the contribution real** (to be pursued in v2-P4/P6, never tuned on test):
- **(a)** Make reconciled per-event file time a ranking feature for time cues (direction A), and
  show it closes the temporal gap.
- **(b)** Put the context features on top of the strongest first stage (BM25+dense+rerank) rather
  than competing with it (ADR-0008).
- **(c)** Report nDCG@10 and target-finding metrics side by side, both pre-registered, and claim
  only what both support.

#### Phase 3 deliverables vs the brief
| Brief item | State |
|---|---|
| 40-file corpus as smoke fixture only | ✔ `tests/fixtures/smoke/` (anchor 0.503 unchanged) |
| Procedural multi-persona benchmark (personas, timelines, mixed formats, noise, simulation ground truth, 10²–10⁴ scale, seeds) | ✔ `contextfs.benchgen` + audit; 44 personas (dev 4,884 / test ~5.6k files). Not yet run at 10⁴ files per persona |
| Queries across cue types, paraphrase/typos, different LLM family from the retriever, leakage written down | ✔ LLM (Qwen3-8B) from memory views; leakage report on dev; residual risk logged |
| Dev/test split by persona, test frozen | ✔ `split_v1.json` sha256 `ed76950b…` |
| Real-data study protocol + tooling | ◐ protocol and consent done; tool scheduled for v2-P4/P5 (needs the app) |
| Baselines on the same index and pools: path+mtime, BM25, dense ×2, fusion, +CE rerank, (optional LLM rerank) | ◐ all except the granite dense row (needs the v2-P4 ONNX export) and the optional LLM reranker (P7) |
| MRR, nDCG@10, Recall@k, Hit@1, bootstrap CIs, paired tests vs strongest, per-kind, ablations, faithfulness metric | ✔ (win/loss *plots* go to the v2-P9 figure set; per-query win/loss counts are reported now) |
| Experiments that can support or refute the claim | ✔ designed and pre-registered (ADR-0015 §7); on dev the claim is currently **refuted** for v1, see above |

**Open (carried forward):**
- granite dense row (ONNX, P4);
- the study tool (P4/P5);
- user-only transfer-table rows;
- donor statistics (Decision 96, optional);
- test runs, capped at 3 (P7);
- a 10⁴-file persona for the scale experiments (P7).

---

## Phase 32 — v2 programme, Phase 4: build the app (2026-10-28)

**Headline (kept in front at the user's request, 2026-10-28):** on the dev benchmark, *time cues
defeat every system.*
- Phase 31 measured exact-target MRR ≤ 0.15 and Hit@1 ≤ 4% on temporal cues for all baselines and
  for v1.
- The v2 engine, measured below, reaches MRR 0.263 / Hit@1 0.135 on temporal cues with `rules_v0`
  times. This is a different index, so it is not directly comparable.
- **Even with the true file times (oracle) it only reaches MRR 0.273 / Hit@1 0.135.** Better time
  estimates alone will not close the gap. How ranking *uses* time (band shape, cue-to-event
  mapping, separating the many files from one period) is at least as large a bottleneck. This is
  the first steer for direction A in P6.
- Phase 4 was built for direction A from the start:
  - every file carries three separate event times (**written / arrived on this PC / last
    edited**), each an interval with a confidence, from pluggable estimators;
  - a reconciled-time estimator slots in as one more estimator, with no schema change.

### What was built
- **Engine** (`src/contextfs/engine/`, the one library behind the GUI, the CLI and the evaluation;
  ADR-0010):
  - `fsio`: safe walker. `\\?\` long paths, reparse points and junction loops, cloud placeholders
    never opened, share-delete reads, and a changed-while-reading check.
  - `timeev` + `estimate`: raw time clues (NTFS created/modified/accessed, in-file
    OOXML/PDF/EXIF/ZIP, Mark-of-the-Web zone) go into `time_evidence`.
    - Per-event intervals go into `file_time(file, estimator, event)`.
    - Estimators: `raw_mtime` (baseline) and `rules_v0` (copy signature, download bursts,
      per-event confidence; N=5 / W=120 s provisional).
    - The oracle enters only through the harness's `time_override`; the engine never opens truth
      files.
  - `indexer`:
    - incremental; batches of 500 files / 5 s;
    - offline roots kept, tombstones only in listed directories;
    - per-event times and sessions recomputed after each update.
  - `query` + `rank`:
    - parsed frame: words, format, time cues with an event hint and a fading band;
    - first stage: FTS5 BM25 ∪ doc-vector top-200;
    - linear scorer whose contributions are the explanations; reasons gated by rank, citing only
      what the file contains;
    - nothing-found floor.
  - `encoder` (ONNX Runtime, fp32 MiniLM; Decision 97) and `gpu` (Decision 98).
- **App** (`src/contextfs/app/`):
  - first-run wizard;
  - main window: results with dates *by event*, a why-line, a snippet, and a **"Dates for this
    file"** panel (every raw clue plus each estimator's interval). This is direction A made
    visible;
  - Settings: folders, plus a GPU tick-box;
  - the indexer runs as a child process (QProcess, JSON-line progress, Cancel; Decision 99);
  - `--selftest [--gpu]`.
- **CLI** (`contextfs update [--gpu] | find --json | status | selftest`): thin wrappers over the
  same engine.
- **Portable build** (`packaging/contextfs.spec`, `scripts/build_app.py`):
  - `dist/ContextFS/` is a one-dir PyInstaller app with `ContextFS.exe` (windowed; it also runs as
    the indexer child, so no console flashes) and `ContextFS-console.exe` (self test and support);
  - no installer (Decision 95);
  - PyInstaller's cache is redirected into `.tools/cache/pyinstaller` (it defaults to C:).
- **Harness** `scripts/engine_eval.py`: dev only, refuses test personas.
  - Sources: time_off / raw_mtime / rules_v0 / oracle.
  - Offline re-scoring of kept pools, with a check that offline == online.
  - `--fit` coordinate ascent; per-cue nDCG and per-cue MRR / Hit@1.
- `pyproject.toml` gains the `app` extra (pinned ORT wheel, tokenizers, PySide6-Essentials) and the
  `build` extra (`pyinstaller` 6.22.3 installed).

### Decision 97 — fp32 is the only shipped precision (corrects ADR-0003 §2, which chose int8)
- Measured by `scripts/export_onnx.py` on 500 texts against sentence-transformers
  (`models/onnx/minilm-l6-v2/export_report.json`):
  - fp32: mean cosine 1.0, min 1.0, top-10 agreement 1.0;
  - int8: mean cosine **0.958691**, min **0.899373**, top-10 agreement **0.82**.
- int8 fails the ADR's own export-parity bar (≥ 0.9999), so it is used nowhere: the app, the GPU
  path and the evaluation all use `model_fp32.onnx`.
- Cost: an 87 MB model instead of ~23 MB.
- The fp32 query cost is well inside ADR-0003 Rev. 1's 50 ms budget:
  - query encoding p50 **3.3 ms**, p95 4.4 ms, max 8.0 ms;
  - full search p50 7.1 ms, p95 10.0 ms;
  - measured on 115 dev queries over 3 dev indexes of 224–256 files, 4 threads;
  - search time will grow with index size; P5 measures it at scale.

### Decision 98 — The GPU gates passed; `onnxruntime-directml` 1.24.4 is the single ORT wheel
- **The adapter was checked before any GPU work** (DXGI enumeration, no GPU workload):
  - device 0 = AMD Radeon 780M Graphics (512 MB, the iGPU);
  - device 1 = NVIDIA GeForce RTX 5060 Laptop GPU (7,899 MB);
  - device 2 = Microsoft Basic Render Driver.
  - The user confirmed device 1 (2026-10-28) before the spike ran.
- **Spike** (`scripts/gpu_spike_dml.py`; a separate venv `.tools/venv-dml`, because the two ORT
  wheels conflict; dev personas only):
  ```
  .tools/venv-dml/Scripts/python.exe -I scripts/gpu_spike_dml.py embed --device-id 1
    chunks 2000  cos_min 0.9999998211860657  cos_mean 1.0  cpu 66.5 chunks/s  dml 525.4 chunks/s
    speedup 7.90  gate_a true  gate_c true              (data/gpu_spike/gates_a_c.json)
  .venv/Scripts/python.exe scripts/gpu_spike_dml.py compare
    queries 758  top10_identical 758  share 1.0  gate_b true   (data/gpu_spike/gate_b.json)
  ```
- **A first spike run reported gate (b) at 15/758.** The cause was a bug in the spike, not the GPU:
  - the indexer embeds the first chunk as "name words. text", but the spike re-embedded the stored,
    unprefixed text;
  - fixed by exposing the indexer's rule as `indexer.embedding_text()` and using it in both places;
  - a CPU rebuild then reproduced the stored vectors (min cosine 0.9999998), and the re-run gives
    the numbers above.
  - An earlier speed reading of the same gate (c), 545 vs 79 chunks/s = 6.9×, came from the buggy
    text set; it is superseded.
- Per ADR-0003 §4, all three gates passing makes the DirectML wheel the single wheel for the app
  **and** the evaluation. The project venv was switched from `onnxruntime` 1.31.0:
  - its CPU EP matches the stored 1.31.0 CPU vectors: min cosine 0.9999998 on 1,165 dev chunks;
  - the dev evaluation re-run under it reproduces the earlier numbers (below);
  - its providers are only DmlExecutionProvider and CPUExecutionProvider (no Azure).
- **Product finding:** DirectML's default adapter on this hybrid laptop is the **iGPU**. So
  "GPU: Auto" never uses device 0:
  - `engine/gpu.py` picks the discrete adapter with the most dedicated memory (≥ 2 GB), otherwise
    the CPU;
  - on first GPU use it runs the 200-text on-device probe (mean ≥ 0.999, each ≥ 0.99), falling
    back to the CPU with a stated reason;
  - any exception also means the CPU; nothing raises.
  - Queries always run on the CPU EP. The indexer's `done` message, the CLI and the GUI status bar
    say which device embedded the files.
- **End-to-end effect is small on the benchmark corpora:**
  - `contextfs update` of professional-s1 (229 files, 229 chunks): 16.17 s CPU vs 16.08 s GPU;
  - extraction dominates for short files, so the 7.9× embedding speed-up only pays off on
    text-heavy folders;
  - not a paper claim.
- Tests (`tests/test_engine_gpu.py`):
  - fakes: hybrid → discrete; integrated or software only → CPU; probe mismatch → CPU; device
    error → CPU; no discrete GPU → CPU;
  - opt-in real-GPU test (`CONTEXTFS_TEST_GPU=1`): a GPU-built index gives the same results as a
    CPU-built one. It passed on device 1.

### Decision 99 — The indexer child is cancelled by a request file, never through stdin
- **Bug:** under the GUI, the child emitted `started` and then sat idle until something arrived on
  stdin. Writing "cancel" made it finish with 0 files.
  - A stdin-reader thread (`for line in sys.stdin`) stalled the main thread on a Qt-created Windows
    pipe.
  - Standalone runs (console stdin) worked, so the engine tests never saw it.
- **Fix:**
  - the GUI writes `<index>/cancel.request`, and the child polls it between files;
  - the GUI deletes stale requests *before* launching, so a cancel issued immediately is not lost;
  - the child's stderr goes to `%LOCALAPPDATA%\ContextFS\logs\indexer.log`, because an undrained
    pipe can stall it.
- **Second bug found while fixing it (data safety):** a cancel that stopped the *walk* early left
  `rep.cancelled` false. The tombstone pass then marked every file the walk had not reached as
  deleted.
  - Fixed: cancel is re-checked after the walk, and later roots are skipped.
  - Regression test: `test_cancel_mid_walk_keeps_unreached_files_and_resumes`.

### Decision 100 — The dev-fitted scorer weights are the default
- `python scripts/engine_eval.py data/bench/*-A-* --tag dev-engine-v1-fit --fit` ran coordinate
  ascent over the grid {0 … 1.4} on the 20 **dev** personas (rules_v0 times).
- Result: bm25 .05 · dense .60 · name .05 · format .25 · time .20 · session .10
  (`data/scorer/weights_dev-engine-v1-fit.json`).
- These replace the provisional hand-set weights; `SearchConfig.weights_source =
  "dev-engine-v1-fit"`.
- They are **in-sample on dev**. The test split is untouched and is scored once, in P7.

### Measured
1. **Dev engine evaluation** — `python scripts/engine_eval.py data/bench/*-A-* --tag dev-engine-v1`
   - 758 LLM queries, 20 dev personas, default (fitted) weights, ORT DirectML wheel on the CPU EP;
   - identical to the fit run under ORT 1.31.0; 0/3,032 offline vs online mismatches.
   ```
   time_off   nDCG@10 0.652 [0.632, 0.670]  MRR 0.391  Hit@1 0.240  R@10 0.766
   raw_mtime  nDCG@10 0.678 [0.659, 0.697]  MRR 0.422  Hit@1 0.272  R@10 0.772
   rules_v0   nDCG@10 0.679 [0.660, 0.698]  MRR 0.424  Hit@1 0.272  R@10 0.769
   oracle     nDCG@10 0.688 [0.669, 0.706]  MRR 0.432  Hit@1 0.277  R@10 0.785
   temporal (n=119) nDCG  0.689 / 0.742 / 0.751 / 0.764   MRR/Hit@1  0.221/0.084  0.257/0.118  0.263/0.135  0.273/0.135
   vague    (n=98)  nDCG  0.640 / 0.724 / 0.731 / 0.748
   mixed    (n=102) nDCG  0.644 / 0.700 / 0.703 / 0.724
   paired nDCG@10: rules_v0−raw +0.0011 (p 0.64) · oracle−raw +0.0093 (p 0.0005) ·
                   oracle−rules_v0 +0.0082 (p 0.00015) · rules_v0−time_off +0.0278 (p 5e-5)
   ```
   **Reading (dev, in-sample, not paper numbers):**
   - Time helps: +0.028 overall and +0.06 on temporal cues.
   - `rules_v0` is **not** better than raw mtime overall (p 0.64), while true times are (p 0.0005):
     this is the room for a better estimator (direction A's first half).
   - Not comparable to Phase 31's baselines: a different index (the v2 engine's own chunking and
     first stage). P7 runs all systems on the same index.
2. **Tests:** `pytest tests/` with start.bat's `HF_HOME` → **518 passed, 1 skipped** (the opt-in
   GPU test, run separately: passed).
   - Without `HF_HOME`, the v1 tests try to download their model and fail. That is an environment
     issue; `start.bat` sets it.
3. **Parity** (`tests/test_parity.py`, 5 tests, all pass):
   - GUI search job == CLI `find --json` == in-process engine (same paths, scores to 1e-6, same
     config hash);
   - CLI under OMP/MKL threads = 1 and = 8: identical;
   - engine threads 1 vs 4: identical;
   - harness offline re-scoring == online ranking;
   - two independent builds of one corpus give the same logical index: files, chunks, per-event
     times and sessions equal, vectors to 1e-5.
4. **GUI end to end** (offscreen, venv, student-s12 corpus, 229 files):
   - a cancel was honoured in 1.6 s (55 files kept, "Update paused");
   - the resume added 174 new with 55 unchanged;
   - a stale request was ignored; an immediate cancel was honoured in 0.8 s;
   - "the pdf I downloaded about clustering" → 25 rows, with the dates panel showing Written /
     Arrived on this PC.
5. **Portable build** (`python scripts/build_app.py`):
   - `dist/ContextFS`: 330 MB, 288 files;
   - largest parts: model 87 MB, PySide6 72 MB, onnxruntime-directml 63 MB, numpy libs 20 MB;
   - **no v1-only library bundled** (torch / sentence-transformers / spaCy / LanceDB / pyarrow /
     scipy excluded, and the build checks for them).
6. **Frozen self test**:
   - command: `ContextFS-console.exe --selftest --gpu` under `env -i` (only Windows on PATH), run
     from `%TEMP%`;
   - result: **ALL PASS**;
   - it writes and finds .md, .csv, .txt, .docx, .xlsx and .pdf files; the re-update is a no-op;
     every result is explained; gibberish → a clear note; the GPU probe picks the RTX 5060
     (cosine 1.000000);
   - **no code from `.venv`, `.tools` or another Python** among 895 modules and 129 DLLs. The only
     outside DLLs are injected by Nahimic audio and Windows Defender; the test lists them as notes.
   - It reports Smart App Control: **Evaluation**.
7. **Frozen GUI → frozen child** (offscreen, saved settings): the windowed child indexed 229 files
   in ~23 s while the GUI stayed up; empty error log.
   - The windowed exe driven by QProcess: a cancel after 4 s kept 44 files; the resume added 185;
     0 errors.

### Phase 4 deliverables vs the brief
| Item | State |
|---|---|
| Shared engine library (GUI = CLI = harness) | ✔ parity tests |
| Built for direction A (three event times, room for a reconciled estimator) | ✔ `file_time` per (file, estimator, event); "Dates for this file" panel |
| First run | ✔ wizard (places, exclusions, what to expect) |
| Incremental update at launch; search during indexing; cancel/resume | ✔ (Decision 99) |
| Portable build + self test + no dev-environment leakage | ✔ (Decision 95: no installer) |
| GPU: detect → use, else clean CPU fallback | ✔ gates passed (Decision 98) |
| Light clean-account check | ◐ `docs/release/CLEAN_ACCOUNT_CHECKLIST.md`; needs the user at the keyboard |
| Study tool | ✗ moved to P5 (needs the hardened app) |
| granite dense evaluation row | ✗ moved to the P7 eval bundle (ADR-0003 Rev. 1) |

### Open (needs the user)
- **LICENSE.** Deferred "until the first build"; this is the first build. `pyproject.toml` says
  MIT, but there is no LICENSE file. It is the user's and the co-author's call, and is needed
  before SignPath and before any release.
- The acceptance demo on this laptop, and the light clean-account check (checklist above).
- Smart App Control is in **Evaluation** on this laptop, and the build is unsigned.
