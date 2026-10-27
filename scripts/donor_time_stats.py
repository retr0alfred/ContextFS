r"""Donor time statistics: histograms of real file-time behaviour (ADR-0015 §3).

Calibrates the benchmark's model of *undamaged* time behaviour on real folders. That model must be
frozen (hashed) before any estimator rule exists, which breaks the circularity found in the fresh
review (B1).

**Privacy by construction.** The script reads only metadata:
- `os.stat` times;
- in-file timestamps of Office, PDF, JPEG and zip files.

It never stores a path, file name, folder name, content, author, or any exact timestamp. Only
**bucketed counts** leave it. It shows the full output on screen and writes it only after the
donor types `yes`. Cloud-only placeholders (OneDrive Files-On-Demand) are counted by attribute and
**never opened**. Read-only: nothing under the folders is modified.

Usage (run by each donor on their own machine, on folders they choose):
    python scripts/donor_time_stats.py "D:\\Documents" "D:\\Downloads" --donor d01 --out data/donor_stats
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contextfs.datagen.filetimes import read_infile_times  # noqa: E402

RECALL = 0x00400000 | 0x00040000 | 0x00001000  # RECALL_ON_DATA_ACCESS | RECALL_ON_OPEN | OFFLINE
INFILE_EXT = {".docx", ".pptx", ".xlsx", ".pdf", ".jpg", ".jpeg", ".zip"}
SKIP_DIRS = {"$recycle.bin", "system volume information", "appdata", "node_modules", ".git",
             ".venv", "venv", "__pycache__", "windows", "program files", "program files (x86)"}
EXT_GROUPS = {
    ".docx": "office", ".pptx": "office", ".xlsx": "office", ".doc": "office", ".xls": "office",
    ".ppt": "office", ".pdf": "pdf", ".jpg": "image", ".jpeg": "image", ".png": "image",
    ".heic": "image", ".zip": "archive", ".rar": "archive", ".7z": "archive", ".txt": "text",
    ".md": "text", ".csv": "text", ".py": "code", ".java": "code", ".c": "code", ".cpp": "code",
    ".js": "code", ".ipynb": "code",
}


def log_bucket(seconds: float) -> str:
    """Signed log-scale bucket of a time difference."""
    if abs(seconds) < 2:
        return "≈0"
    sign = "+" if seconds > 0 else "-"
    edges = [(60, "<1m"), (3600, "<1h"), (86400, "<1d"), (7 * 86400, "<1w"), (30 * 86400, "<1mo"),
             (365 * 86400, "<1y"), (math.inf, "≥1y")]
    for edge, label in edges:
        if abs(seconds) < edge:
            return sign + label
    return sign + "≥1y"


def _parse(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.astimezone()


def collect(roots: list[Path]) -> dict:
    by_group = Counter()
    birth_minus_mtime = defaultdict(Counter)       # group -> bucket -> n
    infile_minus_mtime = defaultdict(Counter)      # source:kind -> bucket -> n
    birth_gt_mtime = Counter()                     # group -> n
    placeholders = 0
    unreadable_dirs = 0
    births_per_dir: list[list[float]] = []
    for root in roots:
        stack = [root]
        while stack:
            d = stack.pop()
            try:
                entries = list(os.scandir(d))
            except OSError:
                unreadable_dirs += 1
                continue
            births = []
            for e in entries:
                try:
                    if e.is_symlink():
                        continue
                    if e.is_dir(follow_symlinks=False):
                        attrs = e.stat(follow_symlinks=False).st_file_attributes
                        if e.name.lower() in SKIP_DIRS or attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT and not attrs & RECALL:
                            continue
                        stack.append(Path(e.path))
                        continue
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                ext = os.path.splitext(e.name)[1].lower()
                group = EXT_GROUPS.get(ext, "other")
                by_group[group] += 1
                birth = getattr(st, "st_birthtime", st.st_ctime)
                births.append(birth)
                birth_minus_mtime[group][log_bucket(birth - st.st_mtime)] += 1
                if birth > st.st_mtime + 2:
                    birth_gt_mtime[group] += 1
                if st.st_file_attributes & RECALL:
                    placeholders += 1
                    continue  # never open a cloud-only file
                if ext in INFILE_EXT:
                    mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                    for key, value in read_infile_times(e.path).items():
                        dt = _parse(value)
                        if dt:
                            infile_minus_mtime[key][log_bucket((dt - mtime).total_seconds())] += 1
            if len(births) >= 2:
                births_per_dir.append(sorted(births))
    # Folder "bursts": runs of births within 60 s of each other (bulk copies/extractions).
    burst_sizes = Counter()
    for births in births_per_dir:
        run = 1
        for a, b in zip(births, births[1:], strict=False):
            if b - a <= 60:
                run += 1
            else:
                burst_sizes[min(run, 1000) if run < 10 else ("10-99" if run < 100 else "100+")] += 1
                run = 1
        burst_sizes[min(run, 1000) if run < 10 else ("10-99" if run < 100 else "100+")] += 1
    return {
        "schema": "contextfs.donor_stats/1",
        "files_by_group": dict(by_group),
        "birth_minus_mtime": {g: dict(c) for g, c in birth_minus_mtime.items()},
        "birth_gt_mtime_by_group": dict(birth_gt_mtime),
        "infile_minus_mtime": {k: dict(c) for k, c in infile_minus_mtime.items()},
        "folder_burst_sizes": {str(k): v for k, v in burst_sizes.items()},
        "placeholders": placeholders,
        "unreadable_dirs": unreadable_dirs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folders", type=Path, nargs="+")
    parser.add_argument("--donor", required=True, help="an opaque donor code, e.g. d01 (not a name)")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "data" / "donor_stats")
    parser.add_argument("--yes", action="store_true", help="skip the on-screen confirmation")
    args = parser.parse_args()

    stats = collect([f.resolve() for f in args.folders])
    stats["donor"] = args.donor
    stats["n_roots"] = len(args.folders)
    text = json.dumps(stats, indent=2, ensure_ascii=False, sort_keys=True)
    print(text)
    print("\nThe JSON above is EVERYTHING that will be saved: counts only, no paths, names or dates.")
    if not args.yes and input("Save it? Type yes: ").strip().lower() != "yes":
        print("Not saved.")
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{args.donor}.json"
    path.write_text(text + "\n", encoding="utf-8")
    digest = hashlib.sha256((text + "\n").encode("utf-8")).hexdigest()
    print(f"saved {path}  sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
