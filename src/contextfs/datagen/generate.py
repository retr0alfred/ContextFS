"""Materialise the synthetic corpus and emit the ground-truth benchmark file.

The corpus and its ground truth are produced by the *same* run so they cannot
drift apart. The ground truth is written **outside** the corpus root, so the
labels can never be scanned, embedded, or otherwise leak into the index the
system is evaluated on - a mistake that would silently inflate every number.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from contextfs.datagen.corpus_spec import (
    CORPUS_FILES,
    CORPUS_SEED,
    QUERIES,
    SESSIONS,
    FileSpec,
)
from contextfs.datagen.writers import write_file

__all__ = ["generate_corpus", "build_ground_truth", "GROUND_TRUTH_SCHEMA_VERSION"]

#: Bumped whenever the ground-truth JSON layout changes, so downstream tools can
#: refuse a file they do not understand instead of misreading it.
GROUND_TRUTH_SCHEMA_VERSION = "1.0"


def generate_corpus(corpus_root: Path, *, clean: bool = True) -> list[Path]:
    """Write every file in :data:`CORPUS_FILES` under ``corpus_root``.

    Args:
        corpus_root: Directory to create the corpus in. Created if absent.
        clean: If True, remove any previously generated corpus first so a
            regeneration cannot leave orphaned files behind that would then be
            indexed as if they were part of the benchmark.

    Returns:
        The absolute paths written, in specification order.
    """
    corpus_root = Path(corpus_root)
    if clean and corpus_root.exists():
        _remove_tree(corpus_root)
    corpus_root.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for spec in CORPUS_FILES:
        target = corpus_root / Path(spec.path)
        write_file(target, spec.kind, spec.content, spec.modified_at)
        written.append(target)
    return written


def _remove_tree(path: Path) -> None:
    """Recursively delete a directory tree.

    Only ever called on the *generated corpus* directory, which this module
    created. ContextFS never deletes anything it did not author.
    """
    import shutil

    shutil.rmtree(path)


def build_ground_truth(corpus_root: Path) -> dict[str, Any]:
    """Assemble the ground-truth benchmark object.

    Returns:
        A JSON-serialisable dict containing the corpus manifest, session
        groupings, per-file date labels, planted near-duplicate pairs, and the
        query set with target and relevance judgements.
    """
    corpus_root = Path(corpus_root)

    files: list[dict[str, Any]] = []
    for spec in CORPUS_FILES:
        files.append(
            {
                "path": spec.path,
                "kind": spec.kind,
                "session": spec.session,
                "mtime": spec.mtime,
                "near_duplicate_of": spec.near_duplicate_of,
                "meaningful_dates": [asdict(d) for d in spec.meaningful_dates],
                "incidental_dates": [asdict(d) for d in spec.incidental_dates],
                "notes": spec.notes,
            }
        )

    sessions: list[dict[str, Any]] = []
    for session in SESSIONS:
        members = [f.path for f in CORPUS_FILES if f.session == session.id]
        sessions.append({**asdict(session), "files": members, "size": len(members)})

    duplicate_pairs = [
        {"file": f.path, "duplicate_of": f.near_duplicate_of}
        for f in CORPUS_FILES
        if f.near_duplicate_of
    ]

    queries = [
        {
            "id": q.id,
            "text": q.text,
            "targets": list(q.targets),
            "relevant": list(q.relevant),
            "kind": q.kind,
            "difficulty": q.difficulty,
            "rationale": q.rationale,
            "field_note": q.field_note,
        }
        for q in QUERIES
    ]

    all_dates = [d for f in CORPUS_FILES for d in f.dates]
    unsessioned = [f.path for f in CORPUS_FILES if f.session is None]

    return {
        "schema_version": GROUND_TRUTH_SCHEMA_VERSION,
        "generator": "contextfs.datagen.generate:generate_corpus",
        "seed": CORPUS_SEED,
        "corpus_root": corpus_root.name,
        "provenance": (
            "Fully synthetic. Authored in contextfs/datagen/corpus_spec.py. "
            "No file from any real user machine was read, copied, or derived from."
        ),
        "counts": {
            "files": len(CORPUS_FILES),
            "sessions": len([s for s in SESSIONS if s.kind != "none"]),
            "sessions_including_negative_control": len(SESSIONS),
            "unsessioned_files": len(unsessioned),
            "queries": len(QUERIES),
            "dates_total": len(all_dates),
            "dates_meaningful": len([d for d in all_dates if d.kind == "meaningful"]),
            "dates_incidental": len([d for d in all_dates if d.kind == "incidental"]),
            "near_duplicate_pairs": len(duplicate_pairs),
            "by_kind": _count_by(lambda f: f.kind),
            "by_query_kind": {
                kind: len([q for q in QUERIES if q.kind == kind])
                for kind in sorted({q.kind for q in QUERIES})
            },
            "by_query_difficulty": {
                level: len([q for q in QUERIES if q.difficulty == level])
                for level in sorted({q.difficulty for q in QUERIES})
            },
        },
        "sessions": sessions,
        "files": files,
        "near_duplicate_pairs": duplicate_pairs,
        "unsessioned_files": unsessioned,
        "queries": queries,
    }


def _count_by(key) -> dict[str, int]:
    """Count corpus files grouped by a key function."""
    counts: dict[str, int] = {}
    for spec in CORPUS_FILES:
        counts[key(spec)] = counts.get(key(spec), 0) + 1
    return dict(sorted(counts.items()))


def write_ground_truth(path: Path, corpus_root: Path) -> dict[str, Any]:
    """Write the ground-truth JSON file and return the object written."""
    payload = build_ground_truth(corpus_root)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return payload


def corpus_manifest(corpus_root: Path) -> list[tuple[str, int, str]]:
    """Return ``(relative_path, size_bytes, iso_mtime)`` for the corpus on disk."""
    corpus_root = Path(corpus_root)
    rows: list[tuple[str, int, str]] = []
    for spec in CORPUS_FILES:
        target = corpus_root / Path(spec.path)
        if target.exists():
            stat = target.stat()
            rows.append(
                (
                    spec.path,
                    stat.st_size,
                    datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                )
            )
        else:
            rows.append((spec.path, -1, "MISSING"))
    return rows


def missing_files(corpus_root: Path) -> list[str]:
    """Return specification paths that are absent from disk."""
    corpus_root = Path(corpus_root)
    return [s.path for s in CORPUS_FILES if not (corpus_root / Path(s.path)).is_file()]


def spec_by_path() -> dict[str, FileSpec]:
    """Index the corpus specification by relative path."""
    return {s.path: s for s in CORPUS_FILES}
