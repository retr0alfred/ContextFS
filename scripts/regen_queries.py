"""Regenerate a persona's template queries from its sealed truth (corpus untouched).

Usage:
    python scripts/regen_queries.py data/bench/<persona> [more ...] [--n 42]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contextfs.benchgen.queries import generate_queries  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("persona_dirs", type=Path, nargs="+")
    parser.add_argument("--n", type=int, default=42)
    args = parser.parse_args()
    for d in args.persona_dirs:
        truth = d / "truth"
        meta = json.loads((truth / "persona.json").read_text(encoding="utf-8"))
        acts = [
            json.loads(x)
            for x in (truth / "activities.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        recs = [
            json.loads(x)
            for x in (truth / "truth_manifest.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        p = meta["persona"]
        qs = generate_queries(p, acts, recs, args.n, p["family"], int(p["seed"]))
        (truth / "queries.jsonl").write_text(
            "".join(json.dumps(asdict(q)) + "\n" for q in qs), encoding="utf-8"
        )
        meta["queries"] = len(qs)
        (truth / "persona.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"{d.name}: {len(qs)} queries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
