"""Verify the generated corpus against its ground truth, and check determinism.

Checks performed:

1. **Ground truth is valid JSON** and carries a schema version this tool knows.
2. **Every path referenced anywhere in the ground truth exists on disk** - file
   manifest, session membership, near-duplicate pairs, query targets, and query
   relevance sets. A benchmark that points at a missing file silently deflates
   recall, so this is checked exhaustively rather than by sampling.
3. **Every file on disk is accounted for** in the ground truth (no strays).
4. **Modification times match the specification**, because activity-session
   reconstruction and the metadata-consistency date signal both depend on them.
5. **Label sanity**: no date is labelled both meaningful and incidental, every
   near-duplicate points at a real file, and every session has members.
6. **Determinism**: the corpus is regenerated into a temporary directory and
   compared with the committed one at the *content* level (byte-identical for
   text and PDF; entry-by-entry XML comparison for OOXML ZIP containers, whose
   embedded ZIP timestamps are not reproducible by design).

Exit code 0 means the benchmark is sound.

Usage:
    python scripts/verify_corpus.py
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contextfs.config import load_config  # noqa: E402
from contextfs.datagen.corpus_spec import CORPUS_FILES  # noqa: E402
from contextfs.datagen.generate import (  # noqa: E402
    GROUND_TRUTH_SCHEMA_VERSION,
    generate_corpus,
)

OOXML_SUFFIXES = {".docx", ".pptx", ".xlsx"}
#: ZIP entries whose bytes legitimately vary between runs (they embed times).
VOLATILE_OOXML_ENTRIES = {"docProps/core.xml", "docProps/app.xml"}


class Report:
    """Accumulates pass/fail results so every check runs before exiting."""

    def __init__(self) -> None:
        """Start with no failures recorded."""
        self.failures: list[str] = []
        self.checks = 0

    def check(self, condition: bool, message: str) -> bool:
        """Record one assertion."""
        self.checks += 1
        if not condition:
            self.failures.append(message)
        return condition

    def section(self, title: str) -> None:
        """Print a section header."""
        print(f"\n--- {title} ---")

    def ok(self, message: str) -> None:
        """Print a passing line."""
        print(f"  PASS  {message}")

    def bad(self, message: str) -> None:
        """Print a failing line."""
        print(f"  FAIL  {message}")


def content_fingerprint(path: Path) -> object:
    """Return a comparable, timestamp-insensitive representation of a file.

    For OOXML this is the mapping of ZIP entry name to entry bytes, excluding
    the entries that embed creation/modification times. For everything else it
    is the raw bytes.
    """
    if path.suffix.lower() in OOXML_SUFFIXES:
        with zipfile.ZipFile(path) as archive:
            return {
                name: archive.read(name)
                for name in sorted(archive.namelist())
                if name not in VOLATILE_OOXML_ENTRIES
            }
    return path.read_bytes()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--skip-determinism", action="store_true", help="skip the regenerate-and-compare check"
    )
    args = parser.parse_args()

    cfg = load_config(args.config or (PROJECT_ROOT / "contextfs.toml"))
    corpus_root: Path = cfg.paths.root
    gt_path: Path = cfg.eval.ground_truth

    report = Report()
    print(f"corpus root : {corpus_root}")
    print(f"ground truth: {gt_path}")

    # -- 1. ground truth parses -------------------------------------------
    report.section("1. Ground truth is valid JSON")
    if not report.check(gt_path.is_file(), f"ground truth missing: {gt_path}"):
        report.bad(f"ground truth missing: {gt_path}")
        return finish(report)
    try:
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        report.check(False, f"ground truth is not valid JSON: {exc}")
        report.bad(f"invalid JSON: {exc}")
        return finish(report)
    report.ok(f"parsed, {len(gt_path.read_text(encoding='utf-8'))} bytes")
    report.check(
        gt.get("schema_version") == GROUND_TRUTH_SCHEMA_VERSION,
        f"schema_version {gt.get('schema_version')!r} != {GROUND_TRUTH_SCHEMA_VERSION!r}",
    )
    report.ok(f"schema_version = {gt.get('schema_version')}")

    # -- 2. every referenced path exists ----------------------------------
    report.section("2. Every path referenced in ground truth exists on disk")
    referenced: set[str] = set()
    for entry in gt["files"]:
        referenced.add(entry["path"])
    for session in gt["sessions"]:
        referenced.update(session["files"])
    for pair in gt["near_duplicate_pairs"]:
        referenced.add(pair["file"])
        referenced.add(pair["duplicate_of"])
    for query in gt["queries"]:
        referenced.update(query["targets"])
        referenced.update(query["relevant"])

    missing = sorted(p for p in referenced if not (corpus_root / Path(p)).is_file())
    report.check(not missing, f"{len(missing)} referenced paths missing: {missing[:5]}")
    if missing:
        for path in missing:
            report.bad(f"missing: {path}")
    else:
        report.ok(f"all {len(referenced)} referenced paths exist")

    # -- 3. no stray files ------------------------------------------------
    report.section("3. Every file on disk is declared in ground truth")
    on_disk = {p.relative_to(corpus_root).as_posix() for p in corpus_root.rglob("*") if p.is_file()}
    declared = {entry["path"] for entry in gt["files"]}
    strays = sorted(on_disk - declared)
    report.check(not strays, f"undeclared files on disk: {strays[:5]}")
    if strays:
        for path in strays:
            report.bad(f"stray: {path}")
    else:
        report.ok(f"{len(on_disk)} files on disk, all declared")

    # -- 4. mtimes match the specification --------------------------------
    report.section("4. Modification times match the specification")
    drift = []
    for spec in CORPUS_FILES:
        target = corpus_root / Path(spec.path)
        if not target.is_file():
            continue
        actual = datetime.fromtimestamp(target.stat().st_mtime).replace(microsecond=0)
        if abs((actual - spec.modified_at).total_seconds()) > 2:
            drift.append((spec.path, spec.mtime, actual.isoformat()))
    report.check(not drift, f"{len(drift)} files have unexpected mtimes")
    if drift:
        for path, expected, actual in drift[:10]:
            report.bad(f"{path}: expected {expected}, got {actual}")
    else:
        report.ok(f"all {len(CORPUS_FILES)} mtimes within 2s of specification")

    # -- 5. label sanity ---------------------------------------------------
    report.section("5. Label sanity")
    contradictions = []
    for entry in gt["files"]:
        meaningful = {d["date"] for d in entry["meaningful_dates"]}
        incidental = {d["date"] for d in entry["incidental_dates"]}
        overlap = meaningful & incidental
        if overlap:
            contradictions.append((entry["path"], sorted(overlap)))
    report.check(not contradictions, f"contradictory date labels: {contradictions[:3]}")
    if contradictions:
        for path, dates in contradictions:
            report.bad(f"{path}: {dates} labelled both meaningful and incidental")
    else:
        report.ok("no date is labelled both meaningful and incidental")

    empty_sessions = [s["id"] for s in gt["sessions"] if not s["files"]]
    report.check(not empty_sessions, f"sessions with no members: {empty_sessions}")
    report.ok(f"all {len(gt['sessions'])} sessions have members")

    self_dupes = [p for p in gt["near_duplicate_pairs"] if p["file"] == p["duplicate_of"]]
    report.check(not self_dupes, "a file is marked as a near-duplicate of itself")
    report.ok(f"{len(gt['near_duplicate_pairs'])} near-duplicate pairs, none self-referential")

    targets_in_relevant = [
        q["id"] for q in gt["queries"] if not set(q["targets"]) <= set(q["relevant"])
    ]
    report.check(
        not targets_in_relevant,
        f"queries whose targets are not a subset of relevant: {targets_in_relevant}",
    )
    report.ok("every query's targets are a subset of its relevance set")

    every_session_queried = {q["kind"] for q in gt["queries"]}
    report.check(
        {"activity", "temporal", "semantic", "entity", "hybrid"} <= every_session_queried,
        f"query kinds not fully covered: {sorted(every_session_queried)}",
    )
    report.ok(f"query kinds covered: {sorted(every_session_queried)}")

    # -- 6. determinism ----------------------------------------------------
    if args.skip_determinism:
        report.section("6. Determinism (SKIPPED)")
    else:
        report.section("6. Determinism: regenerate and compare content")
        with tempfile.TemporaryDirectory(prefix="contextfs_det_") as tmp:
            replica_root = Path(tmp) / "corpus"
            generate_corpus(replica_root, clean=True)

            differing = []
            for spec in CORPUS_FILES:
                original = corpus_root / Path(spec.path)
                replica = replica_root / Path(spec.path)
                if not replica.is_file():
                    differing.append((spec.path, "not regenerated"))
                    continue
                if content_fingerprint(original) != content_fingerprint(replica):
                    differing.append((spec.path, "content differs"))

            report.check(not differing, f"{len(differing)} files not reproducible")
            if differing:
                for path, why in differing[:10]:
                    report.bad(f"{path}: {why}")
            else:
                report.ok(
                    f"all {len(CORPUS_FILES)} files reproduce identically "
                    "(OOXML compared entry-by-entry, excluding embedded timestamps)"
                )

    # -- summary -----------------------------------------------------------
    counts = gt["counts"]
    report.section("Corpus summary")
    for key, value in counts.items():
        print(f"  {key:<40} {value}")

    return finish(report)


def finish(report: Report) -> int:
    """Print the final verdict and return a process exit code."""
    print()
    if report.failures:
        print(f"FAILED: {len(report.failures)} of {report.checks} checks")
        for failure in report.failures:
            print(f"  - {failure}")
        return 1
    print(f"OK: all {report.checks} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
