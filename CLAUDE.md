# ContextFS — standing rules for every session

Final-year B.E. CSE project → IEEE paper. Authors: Alfred Mathew, Abu Ibrahim Mothi.
Supervisor: Dr. Murari Devakannan Kamalesh.

## Before doing anything

1. **Read `log.md` top to bottom.** It is the project's memory (Phases 0–28 from the
   v1 build, Decisions 1–87, then the v2 programme). If it is missing, recreate it.
2. Read `docs/STATUS.md` (30-second view of every phase).

## The v2 programme and how it is numbered in `log.md`

The v2 plan has nine phases. They continue the existing log numbering:

| v2 phase | log.md phase | What |
|---|---|---|
| v2-P1 | Phase 29 | Audit → `docs/AUDIT_AND_ROADMAP.md` (CHECKPOINT: user picks novelty directions) |
| v2-P2 | Phase 30 | Architecture debate (subagents) → `docs/adr/` + fresh reviewer |
| v2-P3 | Phase 31 | Evaluation foundation: procedural multi-persona benchmark, frozen test split, baselines, stats |
| v2-P4 | Phase 32 | Build the app (engine library, first-run, incremental launch update, installer) |
| v2-P5 | Phase 33 | Hardening suite, independent QA agent, must pass on the packaged `.exe` |
| v2-P6 | Phase 34 | Novelty features/models — only the directions the user picked |
| v2-P7 | Phase 35 | Final evaluation (dev, then ONE run on frozen test), README with measured numbers only |
| v2-P8 | Phase 36 | Optimise/slim — tolerance signed off by user first; revert any change outside it |
| v2-P9 | Phase 37 | Paper kit in `docs/paper_kit/` (2021+ reputable refs only, real DOIs) |

Decisions continue from **Decision 88**. Never rewrite past entries; correct them
with a new entry that names the one it corrects.

## Log entry format

Every entry: date (YYYY-MM-DD), phase, what was done, why, what was measured
(actual command + actual output), what is still open.

## Hard rules

- **Stop at the end of EVERY numbered v2 phase.** Update `log.md` and
  `docs/STATUS.md`, give the five-line summary (done / next / needs from user),
  then wait for the user to say "continue". Never fold two phases into one run.
- **Every number already in the repo is unverified** (README, log.md, comments in
  `contextfs.toml`, `data/eval/*`, both paper drafts). Some were placeholders.
  Nothing goes into the README or the paper kit unless a script in this repo
  produced it during the v2 programme, with command and output logged. When an old
  number is replaced, log that the old value was unverified.
- Core principles: local-first (no cloud calls, no telemetry), read-only on user
  files, every result explained, incremental indexing. Change only via a logged
  decision.
- Use subagents for architecture debates and independent reviews. A reviewer must
  not have written the work it reviews.
- **Never tune on the test set.** Once the v2-P3 test split exists it is frozen;
  all tuning uses the dev split. The old 40-file / 17-query corpus is a smoke
  fixture only — never a source of paper results.
- Genuinely-the-user's or irreversible decisions → stop and ask. Otherwise decide,
  log, continue.
- **Install location:** everything goes inside the project (`.venv`, `.tools/`,
  `models/`, caches) or on D:. Never write to C: without asking first. Set
  `PIP_CACHE_DIR`, `HF_HOME`, `UV_CACHE_DIR` etc. to project-local paths.
- **GPU (hybrid graphics laptop):** the first time any GPU workload runs for real
  (model load, embeddings, training), stop first and report `torch.cuda.is_available()`,
  `torch.cuda.get_device_name(0)` and `nvidia-smi` VRAM-in-use for the process. The
  user verifies it is the RTX 5060 before it runs for real. The shipped app must
  work with no GPU (detect → use, else clean CPU fallback).
- Optimisation (v2-P8) only after the hardening suite (v2-P5) fully passes.

## Git and privacy

- Work on branch **`v2`**; commit and push as phases progress (Decision 93). Don't touch `main`
  unless the user says to merge.
- The GitHub repo is **public**. Paper-strategy material stays local and git-ignored:
  `RESEARCH_DOSSIER.md`, `docs/AUDIT_AND_ROADMAP.md`, `docs/research/`. Before pushing anything
  else of that kind (draft reviews, novelty assessments, paper plans), ask the user.

## Paper directions (Decision 92)

D (procedural benchmark) = foundation · A (temporal provenance / mtime-damage robustness) = primary ·
B (personal temporal grounding) = secondary only if time allows after A · C (learned fusion) =
engineering, not a paper direction.

## Machine (new laptop, 2026)

Lenovo LOQ, Ryzen 7 250 (8C/16T), RTX 5060 Laptop 8 GB (Blackwell, sm_120 — needs a
CUDA ≥ 12.8 torch build), 16 GB single-channel RAM, Windows 11.

## Environment gotchas on this machine

- Git reports "dubious ownership" (repo copied from the old laptop's SID). Use
  `git -c safe.directory=* ...` per command; do not write to the global gitconfig.
- The project path contains a space **and an ampersand** (`D:\Projects & Code\...`).
  Every path in `.bat` files and subprocess calls must be quoted.
- Use `.venv\Scripts\python.exe` via `start.bat`; there is no system Python on PATH.
