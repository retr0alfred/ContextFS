"""Run content extraction over the whole corpus and report the outcome.

Prints a per-file table and an aggregate success rate. Failures are listed with
their reasons; warnings are listed separately. Nothing is dropped silently -
that is the specific behaviour this report exists to make impossible.

Usage:
    python scripts/extraction_report.py
    python scripts/extraction_report.py --failures-only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contextfs.config import load_config  # noqa: E402
from contextfs.extract import extract_many  # noqa: E402
from contextfs.scanner import Scanner  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--failures-only", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config or (PROJECT_ROOT / "contextfs.toml"))
    root = cfg.paths.root
    if not root.is_dir():
        print(f"corpus not found at {root}; run scripts/generate_corpus.py first")
        return 1

    scanner = Scanner(cfg)
    items = [(path, path.relative_to(root).as_posix()) for path in scanner.walk()]
    print(f"corpus: {len(items)} files at {root}\n")

    report = extract_many(items, config=cfg)

    if not args.failures_only:
        header = f"{'file':<58} {'extractor':<10} {'blocks':>6} {'chars':>7} {'words':>6}  tab  ms"
        print(header)
        print("-" * len(header))
        for doc in report.documents:
            flag = "" if doc.ok else "  <-- FAILED"
            print(
                f"{doc.rel_path:<58} {doc.extractor:<10} {doc.block_count:>6} "
                f"{doc.char_count:>7} {doc.word_count:>6}  "
                f"{'Y' if doc.has_tabular_content else '.'}   "
                f"{doc.meta.get('extract_ms', 0):>6}{flag}"
            )
        print()

    print("=" * 78)
    print("EXTRACTION SUMMARY")
    print("=" * 78)
    summary = report.summary()
    for key, value in summary.items():
        print(f"  {key:<22} {value}")
    print(
        f"\n  SUCCESS RATE           {report.success_rate:.1%} "
        f"({len(report.succeeded)}/{report.total})"
    )

    print("\n  per extension (succeeded/attempted):")
    for ext, (ok, total) in report.by_extension().items():
        marker = "  " if ok == total else "  <-- incomplete"
        print(f"    {ext or '(none)':<12} {ok}/{total}{marker}")

    if report.failed:
        print(f"\n  FAILURES ({len(report.failed)}):")
        for doc in report.failed:
            print(f"    {doc.rel_path}\n      reason: {doc.error}")
    else:
        print("\n  FAILURES: none")

    if report.empty:
        print(f"\n  EXTRACTED BUT EMPTY ({len(report.empty)}):")
        for doc in report.empty:
            print(f"    {doc.rel_path}")

    if report.with_warnings:
        print(f"\n  WARNINGS ({len(report.with_warnings)} documents):")
        for doc in report.with_warnings:
            for warning in doc.warnings:
                print(f"    {doc.rel_path}: {warning}")
    else:
        print("\n  WARNINGS: none")

    tabular = [d.rel_path for d in report.succeeded if d.has_tabular_content]
    print(f"\n  DOCUMENTS WITH TABULAR CONTENT ({len(tabular)}):")
    print("  (these feed the Phase 10 structured-context signal)")
    for path in tabular:
        print(f"    {path}")

    return 0 if not report.genuine_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
