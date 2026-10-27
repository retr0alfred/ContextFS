# Undamaged file-time model v1: default assumptions (log.md Decision 96)

Donor statistics (ADR-0015 §3) were not collected: the planned donor machine is unavailable and the
user deprioritised other sources (2026-10-28). The benchmark's model of *undamaged* time behaviour
is therefore a set of **documented default assumptions**. It is frozen here, before any estimator
rule exists, and is a stated **limitation** of the benchmark.

| Origin | NTFS created | NTFS modified | In-file times | Basis |
|---|---|---|---|---|
| authored by the persona | first work session | last work session (≤ deadline) | Office/PDF created = first session, modified = last session | ordinary editing; T v1 (in-place edit keeps birth, moves mtime; safe-save tunnelling keeps birth) |
| downloaded (browser) | arrival | arrival | authored by someone else, earlier (days to years) | browsers stamp the download time; Mark-of-the-Web added |
| received (phone photos) | import time | capture time | EXIF DateTimeOriginal = capture (floating) | import is a CopyFile-family copy, so mtime is kept (T v1) |
| duplicates "x (1).ext" | re-download time | re-download time | identical to the original | a second browser download |

Session-time spread, work hours (09:00–23:59), session weighting toward deadlines, and folder bursts
come from the simulator (`simulate.py`), not from real data.

**Known gaps vs reality** (to state in the paper):
- No sync clients.
- No editors that rewrite created time.
- No files created by apps with clock skew.
- No FAT/exFAT origins.
- Zone always the generating machine's (IST).

Frozen source hashes (sha256):
```
{
  "src/contextfs/benchgen/simulate.py": "998b868042e4a520c153c7c3d652b0cee0629b44f1e66f082e92f162a1a7080a",
  "src/contextfs/benchgen/materialise.py": "d3f282b33f5d91ec6d54f52199325e890c37e89ec10170d786fd25f7f78bfd04",
  "src/contextfs/benchgen/damage.py": "620bcf1db9bbfb02fdc34717c0edc148f5f023e9d85bb82eed4777dfce7c3d38"
}
```
