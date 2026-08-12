# ContextFS — User Guide

How to install, start, use, and stop ContextFS.

> Every command in this guide exists and works. Nothing here is aspirational.

---

## 1. Install

**Prerequisites:** Windows 10/11 (macOS and Linux work too), Python 3.10–3.12,
and about 3 GB of free disk for the environment and models.

```bash
cd D:\Projects\Claude\ContextFS
python -m venv .venv
```

Install PyTorch from the CPU-only wheel index first (much smaller and faster than
the default CUDA build; ContextFS never uses a GPU):

```bash
.venv\Scripts\python.exe -m pip install "torch>=2.2,<3" --index-url https://download.pytorch.org/whl/cpu
```

Then install ContextFS itself, in editable mode, with the dev and data-generation
extras:

```bash
.venv\Scripts\python.exe -m pip install -e ".[dev,datagen]"
```

Then download the spaCy language model (~54 MB):

```bash
.venv\Scripts\python.exe -m spacy download en_core_web_md
```

`en_core_web_md` is the default because it was measured to be substantially more
accurate than the smaller `en_core_web_sm` on this kind of corpus (entity F1
0.595 vs 0.472). If disk space matters more than accuracy, install
`en_core_web_sm` instead and set `spacy_model = "en_core_web_sm"` under
`[entities]` in your config.

Finally, generate the synthetic demo corpus so there is something to index:

```bash
.venv\Scripts\python.exe scripts\generate_corpus.py
```

### Activating the environment

Every command below assumes the virtual environment is active. Activate it once
per terminal:

```bash
.venv\Scripts\Activate.ps1
```

If PowerShell blocks the activation script, either run the one-time policy change
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, or skip activation
entirely and call the interpreter directly as `.venv\Scripts\python.exe -m contextfs ...`.

### Verify the install

```bash
.venv\Scripts\python.exe -m pytest
```

---

## 2. Configure

All settings live in `contextfs.toml`. The one you will actually change is the
scan root:

```toml
[paths]
root = "data/synthetic/corpus"
```

**Do not edit `contextfs.toml` directly if you are pointing ContextFS at your own
personal files.** Copy it to `contextfs.local.toml` and edit that — the local
copy is git-ignored, so your real folder paths never reach version control.

You can also override the root per-command without editing anything:

```bash
contextfs --root "C:\Users\you\Documents" scan
```

ContextFS is strictly read-only on everything under `root`. It never writes,
renames, moves, or deletes a scanned file; all derived data goes to the
`.contextfs/` directory.

To see exactly what configuration is in effect:

```bash
contextfs config
```

---

## 3. Index your files

```bash
contextfs scan
```

This runs the whole pipeline: scan → extract → entities → embed → classify
dates → reconstruct sessions → build the semantic tree and relationship graph.
The first run on the 40-file demo corpus takes roughly 30 seconds on a Ryzen 7
3700U; most of that is embedding.

Re-running it is cheap. A scan with nothing changed does no reprocessing at all,
and a scan after editing one file reprocesses only that file (measured: 2 of 40
files, 25× faster than a full rebuild — see log.md, Phase 18).

Useful flags:

```bash
contextfs scan --dry-run      # report what would change; touches nothing
contextfs scan --full         # force a complete rebuild
```

Before the very first scan on a machine with no internet access, pre-fetch the
models:

```bash
contextfs fetch-models
```

---

## 4. Search

The main command. Describe what you *remember*, not what the file is called:

```bash
contextfs query "the PDF I studied before my ML exam"
contextfs query "that spreadsheet from around the capstone deadline"
contextfs query "what I was working on the week of the hackathon"
```

### Seeing why something matched

Every result carries its reasoning. Show it inline:

```bash
contextfs query "notes from the ML exam" --explain
```

Or drill into one result from the last query by its rank number:

```bash
contextfs explain 1
contextfs explain 1 --json     # machine-readable
```

This is where the system's central claim is visible. A file can rank highly on
`activity 1.000` while sitting near the floor on `semantic 0.181` — retrieved
because you worked on it during the same session, not because its text matches.

### Comparing against plain semantic search

```bash
contextfs query "notes from the ML exam" --compare
```

Runs the pure-embedding baseline and the full context-aware system side by side
on the same index. `--baseline` runs only the baseline; `--signals` switches
individual layers off for ablation:

```bash
contextfs query "..." --signals semantic,graph
```

### Searching by time

```bash
contextfs timeline "March to April"
contextfs timeline "last week"
contextfs timeline "around the capstone deadline"
```

This searches *meaningful* dates — deadlines, exam dates, meeting dates — and
ignores incidental ones such as copyright years and footer stamps. That
distinction is the system's own classification, measured at F1 0.986.

---

## 5. Tell it when it got the answer right

```bash
contextfs query "notes I revised before the ML exam"
contextfs feedback --pick 3
```

The file at rank 3 is nudged up the next time you run that same query.
`--reject N` nudges one down. `--show` lists what has been recorded, `--clear`
erases all of it.

Feedback is deliberately weak: it can reorder near-ties and it cannot overturn a
clear win, no matter how many times you click. It is also scoped to the exact
query text, and it never touches the system's measured evaluation numbers.

---

## 6. Understand your files

```bash
contextfs digest          # what is on disk, by type, age and size
contextfs duplicates      # near-duplicate groups, and space they waste
contextfs projects        # bodies of work, and whether each is active or finished
contextfs tags <file>     # the tags ContextFS would give a file
contextfs stats           # index health: counts per layer, per-stage timings
```

All of these are reports. **ContextFS never deletes, moves, or renames
anything** — `duplicates` tells you what is redundant and stops there.

Every one of these accepts `--json` for scripting.

---

## 7. Stop / clean up

ContextFS runs no background service — there is nothing to "stop". Every command
is a one-shot process that exits when it is done. Nothing watches your
filesystem, nothing phones home, and nothing runs between commands.

To discard the index and start over:

```bash
contextfs reset
```

This deletes only ContextFS's own `.contextfs/` data directory. Your scanned
files are untouched, because ContextFS never had write access to them in the
first place.

---

## Reproducing the research results

```bash
python scripts/evaluate.py          # retrieval metrics + the full ablation table
python scripts/date_eval.py         # meaningful vs incidental date classification
python scripts/session_eval.py      # activity session reconstruction accuracy
python scripts/entity_eval.py       # entity extraction against hand labels
python scripts/incremental_check.py # incremental update correctness and speed-up
```

Each writes its numbers to stdout and, where relevant, to `data/eval/`. Every
figure quoted in README.md and log.md comes from one of these scripts. None are
estimated.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `contextfs: command not found` | The virtual environment is not active. Run `.venv\Scripts\Activate.ps1`, or call `.venv\Scripts\python.exe -m contextfs` directly. |
| PowerShell refuses to run `Activate.ps1` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` (one-time), or skip activation and use the full interpreter path. |
| `pip install` pulls a multi-gigabyte torch | You skipped the CPU-only index step in section 1. |
| `No index at ...` | Run `contextfs scan` first. |
| "This index was written by an older build" | Harmless. Run `contextfs scan` to upgrade the schema. |
| `OSError` about a model not being cached | Run `contextfs fetch-models` once while online. |
| `en_core_web_md` not found | `.venv\Scripts\python.exe -m spacy download en_core_web_md` |
| Queries feel slow on first run | The first query in a process pays model load time. Subsequent queries in the same process are much faster; the CLI is one-shot, so each invocation pays it once. |
