"""Generate the ContextFS synthetic evaluation corpus and its ground truth.

Usage:
    python scripts/generate_corpus.py
    python scripts/generate_corpus.py --root some/other/dir --ground-truth gt.json

Writes the corpus under the configured scan root and the ground truth to the
configured evaluation path. The ground truth deliberately lives *outside* the
corpus so labels cannot be indexed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contextfs.config import load_config  # noqa: E402
from contextfs.datagen.generate import (  # noqa: E402
    corpus_manifest,
    generate_corpus,
    write_ground_truth,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None, help="config file to read paths from")
    parser.add_argument("--root", type=Path, default=None, help="override corpus output directory")
    parser.add_argument(
        "--ground-truth", type=Path, default=None, help="override ground-truth output path"
    )
    parser.add_argument(
        "--no-clean", action="store_true", help="do not delete an existing corpus first"
    )
    parser.add_argument("--quiet", action="store_true", help="only print the summary line")
    args = parser.parse_args()

    cfg = load_config(args.config or (PROJECT_ROOT / "contextfs.toml"))
    corpus_root = args.root or cfg.paths.root
    gt_path = args.ground_truth or cfg.eval.ground_truth

    written = generate_corpus(corpus_root, clean=not args.no_clean)
    payload = write_ground_truth(gt_path, corpus_root)

    if not args.quiet:
        print(f"corpus root : {corpus_root}")
        print(f"ground truth: {gt_path}")
        print()
        print(f"{'file':<62} {'bytes':>9}  modified")
        print("-" * 100)
        for rel, size, mtime in corpus_manifest(corpus_root):
            print(f"{rel:<62} {size:>9}  {mtime}")
        print()

    counts = payload["counts"]
    print(
        f"generated {len(written)} files | "
        f"{counts['sessions']} sessions (+1 negative control) | "
        f"{counts['queries']} queries | "
        f"{counts['dates_meaningful']} meaningful / "
        f"{counts['dates_incidental']} incidental dates | "
        f"{counts['near_duplicate_pairs']} near-duplicate pairs"
    )
    print(f"by format: {counts['by_kind']}")
    print(f"by query kind: {counts['by_query_kind']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
