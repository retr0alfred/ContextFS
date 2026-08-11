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
