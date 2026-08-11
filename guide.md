# ContextFS — User Guide

How to install, start, use, and stop ContextFS.

> **Status:** this guide grows with the build. Sections marked ⏳ describe
> commands that do not exist yet at the current phase. As of **Phase 1**, only
> the *Install* section below is live.

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

## 2. Configure ⏳ *(Phase 2)*

All settings live in `contextfs.toml`. The one you will actually change is the
scan root:

```toml
[paths]
root = "data/synthetic/corpus"
```

**Do not edit `contextfs.toml` directly if you are pointing ContextFS at your own
personal files.** Copy it to `contextfs.local.toml` and edit that — the local
copy is git-ignored, so your real folder paths never reach version control.

ContextFS is strictly read-only on everything under `root`. It never writes,
renames, moves, or deletes a scanned file; all derived data goes to the
`.contextfs/` directory.

---

## 3. Index your files ⏳ *(Phase 4+)*

```bash
contextfs scan
```

---

## 4. Search ⏳ *(Phase 15+)*

```bash
contextfs query "the PDF I studied before my ML exam"
contextfs timeline "March to April"
contextfs explain <result-id>
contextfs stats
```

---

## 5. Stop / clean up ⏳ *(Phase 17)*

ContextFS runs no background service — there is nothing to "stop". Every command
is a one-shot process that exits when it is done.

To discard the index and start over:

```bash
contextfs reset
```

This deletes only ContextFS's own `.contextfs/` data directory. Your scanned
files are untouched, because ContextFS never had write access to them in the
first place.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `contextfs: command not found` | The virtual environment is not active. Run `.venv\Scripts\Activate.ps1`, or call `.venv\Scripts\python.exe -m contextfs` directly. |
| PowerShell refuses to run `Activate.ps1` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` (one-time), or skip activation and use the full interpreter path. |
| `pip install` pulls a multi-gigabyte torch | You skipped the CPU-only index step in section 1. |
