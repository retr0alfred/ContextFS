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
