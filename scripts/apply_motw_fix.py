r"""Re-apply the T v1.1 Mark-of-the-Web correction to already-generated personas (log.md Decision 106).

    python scripts/apply_motw_fix.py data/bench/* [--dry-run]

The damage simulator kept the ``Zone.Identifier`` stream on every damaged file; real content-only
copies and git clones drop it (T v1.1, measured). For each persona this removes the stream from the
files whose sealed damage label is in ``benchgen.damage.DROPS_MOTW``. That is exactly what a
regeneration from the same seeds with the corrected ``damage.py`` would produce: corpus bytes, file
times, truth and queries are untouched, so the split-freeze hashes stay valid.

For test personas only the damage labels are read, and only counts and a hash of the affected path
list are recorded (``data/splits/motw_fix_v1.json``). Deletions are paced: the project drive is a
USB hard disk that resets under bursts of small writes (Decision 104).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from contextfs.benchgen.damage import DROPS_MOTW, drop_motw  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("persona_dirs", type=Path, nargs="+")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "splits" / "motw_fix_v1.json")
    args = parser.parse_args()
    record = json.loads(args.out.read_text(encoding="utf-8")) if args.out.exists() else {}
    for d in sorted(args.persona_dirs):
        manifest = d / "truth" / "truth_manifest.jsonl"
        if not manifest.exists():
            continue
        affected = sorted(
            r["rel_path"] for r in map(json.loads, manifest.read_text(encoding="utf-8").splitlines())
            if r["damage"] in DROPS_MOTW
        )
        removed = 0
        for i, rel in enumerate(affected):
            if not args.dry_run and drop_motw(d / "corpus" / rel):
                removed += 1
            if i % 100 == 99:
                time.sleep(0.5)  # pace metadata writes on the USB disk
        record[d.name] = {
            "split": "test" if "-B-" in d.name else "dev",
            "affected_files": len(affected),
            "marks_removed": removed,
            "affected_paths_sha256": hashlib.sha256("\n".join(affected).encode("utf-8")).hexdigest(),
        }
        print(f"{d.name}: affected {len(affected)}, marks removed {removed}{' (dry run)' if args.dry_run else ''}")
    if not args.dry_run:
        blob = json.dumps(record, indent=1, sort_keys=True)
        args.out.write_text(blob, encoding="utf-8")
        print("record sha256:", hashlib.sha256(blob.encode("utf-8")).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
