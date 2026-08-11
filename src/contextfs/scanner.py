"""Layer 1 - file discovery and change detection.

The scanner walks the configured root, records ``(path, size, mtime, hash)`` for
every file that passes the ignore rules, and classifies each against the
previous scan as **new**, **modified**, **unchanged**, or **deleted**.

Read-only guarantee
-------------------
This module opens files in binary read mode and nothing else. It never writes,
truncates, renames, moves, deletes, or changes the permissions or timestamps of
anything under the scan root. ``tests/test_scanner.py`` enforces this by
fingerprinting the entire corpus (content hash, size, and mtime of every file)
before and after a scan and asserting the fingerprints are identical.

Change detection strategy
-------------------------
Hashing every file on every scan is correct but wasteful: it reads the whole
corpus from disk each time. Instead the scanner uses a two-tier test.

1. **Cheap tier (stat only).** If ``size`` and ``mtime_ns`` both match the
   stored values, the file is *presumed unchanged* and is not opened at all.
2. **Expensive tier (hash).** If either differs - or if ``--full`` / ``rehash``
   is requested - the file is read and hashed. A file whose mtime moved but
   whose hash did not is reported as **unchanged**, so touching a file does not
   trigger a pointless reindex of every downstream layer.

The trade-off is stated rather than hidden: tier 1 misses a content change that
preserves both size and mtime exactly. That requires deliberate timestamp
forgery, and ``contextfs scan --full`` closes the hole when it matters. The
alternative - always hashing - would make incremental update time proportional
to corpus size rather than to change size, which would defeat the very metric
Phase 18 reports.
"""

from __future__ import annotations

import fnmatch
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import xxhash

from contextfs.config import ContextFSConfig
from contextfs.store import Store, utc_now

__all__ = [
    "FileRecord",
    "ScanResult",
    "Scanner",
    "hash_file",
    "HASH_ALGORITHM",
]

#: Non-cryptographic 128-bit hash. See Decision 20 in log.md for why this is
#: preferred to SHA-256 here.
HASH_ALGORITHM = "xxh3_128"

_HASH_CHUNK_BYTES = 1 << 20  # 1 MiB


def hash_file(path: Path, chunk_size: int = _HASH_CHUNK_BYTES) -> tuple[str, int]:
    """Compute the content hash of a file without loading it into memory.

    Args:
        path: File to read. Opened read-only.
        chunk_size: Bytes per read.

    Returns:
        ``(hex_digest, bytes_read)``.
    """
    digest = xxhash.xxh3_128()
    total = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


@dataclass(frozen=True)
class FileRecord:
    """Layer-1 metadata for a single file. No content is read beyond hashing."""

    path: str
    abs_path: str
    name: str
    stem: str
    ext: str
    folder: str
    depth: int
    size: int
    mtime_ns: int
    mtime: str
    content_hash: str | None

    def as_row(self, seen_at: str) -> dict[str, Any]:
        """Render as a parameter dict for :meth:`Store.upsert_files`."""
        return {
            "path": self.path,
            "abs_path": self.abs_path,
            "name": self.name,
            "stem": self.stem,
            "ext": self.ext,
            "folder": self.folder,
            "depth": self.depth,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "mtime": self.mtime,
            "content_hash": self.content_hash,
            "seen_at": seen_at,
            "content_changed_at": seen_at,
        }


@dataclass
class ScanResult:
    """The outcome of one scan, classified against the previous scan state."""

    root: Path
    new: list[FileRecord] = field(default_factory=list)
    modified: list[FileRecord] = field(default_factory=list)
    unchanged: list[FileRecord] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    errors: list[tuple[str, str, str]] = field(default_factory=list)
    skipped_ignored: int = 0
    skipped_too_large: int = 0
    files_hashed: int = 0
    bytes_hashed: int = 0
    duration_ms: float = 0.0
    scan_id: int | None = None
    dry_run: bool = False

    @property
    def seen(self) -> int:
        """Number of files present under the root and not ignored."""
        return len(self.new) + len(self.modified) + len(self.unchanged)

    @property
    def changed(self) -> list[FileRecord]:
        """Files needing reprocessing by downstream layers."""
        return self.new + self.modified

    @property
    def touched_fraction(self) -> float:
        """Fraction of the corpus that changed. The Phase 18 headline metric."""
        return len(self.changed) / self.seen if self.seen else 0.0

    def summary(self) -> dict[str, Any]:
        """Return a flat, printable and JSON-serialisable summary."""
        return {
            "root": str(self.root),
            "files_seen": self.seen,
            "count_new": len(self.new),
            "count_modified": len(self.modified),
            "count_unchanged": len(self.unchanged),
            "count_deleted": len(self.deleted),
            "files_hashed": self.files_hashed,
            "bytes_hashed": self.bytes_hashed,
            "skipped_ignored": self.skipped_ignored,
            "skipped_too_large": self.skipped_too_large,
            "errors": len(self.errors),
            "duration_ms": round(self.duration_ms, 2),
        }


class Scanner:
    """Walks a root directory and classifies files against the stored state."""

    def __init__(self, config: ContextFSConfig) -> None:
        """Bind the scanner to a resolved configuration."""
        self.config = config
        self.root = config.paths.root
        self._ignore_dirs = {d.lower() for d in config.scan.ignore_dirs}
        self._ignore_globs = list(config.scan.ignore_globs)
        self._max_bytes = config.scan.max_file_size_bytes
        self._follow = config.scan.follow_symlinks

    # -- traversal ---------------------------------------------------------

    def is_ignored_dir(self, name: str) -> bool:
        """Whether a directory name is excluded from traversal."""
        return name.lower() in self._ignore_dirs

    def is_ignored_file(self, name: str) -> bool:
        """Whether a file name matches any ignore glob."""
        return any(fnmatch.fnmatch(name, pattern) for pattern in self._ignore_globs)

    def walk(self) -> Iterator[Path]:
        """Yield every non-ignored file under the root.

        Uses :func:`os.walk` with in-place pruning of ``dirnames``, so an
        ignored directory is never descended into at all rather than being
        walked and filtered afterwards.
        """
        if not self.root.is_dir():
            return
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=self._follow):
            dirnames[:] = sorted(d for d in dirnames if not self.is_ignored_dir(d))
            for filename in sorted(filenames):
                if self.is_ignored_file(filename):
                    continue
                yield Path(dirpath) / filename

    def _describe(self, path: Path, content_hash: str | None) -> FileRecord:
        """Build a :class:`FileRecord` from a path's stat data."""
        stat = path.stat()
        relative = path.relative_to(self.root)
        folder = relative.parent.as_posix()
        return FileRecord(
            path=relative.as_posix(),
            abs_path=str(path),
            name=path.name,
            stem=path.stem,
            ext=path.suffix.lower(),
            folder="" if folder == "." else folder,
            depth=len(relative.parts) - 1,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            # Local time on purpose: activity sessions model a human working
            # day, and UTC would split a late-night session across two dates.
            mtime=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            content_hash=content_hash,
        )

    # -- scanning ----------------------------------------------------------

    def scan(
        self,
        store: Store,
        *,
        full: bool = False,
        dry_run: bool = False,
        rehash: bool = False,
    ) -> ScanResult:
        """Walk the root and classify every file against the stored state.

        Args:
            store: Where previous scan state lives and results are written.
            full: Treat every file as changed (forces a downstream reindex).
            dry_run: Classify and report without writing anything to the store.
            rehash: Hash every file even when size and mtime are unchanged.
                Slower, but immune to timestamp forgery.

        Returns:
            A :class:`ScanResult` with the four classification buckets, the
            errors encountered, and the work actually performed.
        """
        started = time.perf_counter()
        result = ScanResult(root=self.root, dry_run=dry_run)

        if not self.root.is_dir():
            result.errors.append((str(self.root), "walk", "scan root does not exist"))
            result.duration_ms = (time.perf_counter() - started) * 1000
            return result

        # A dry run must be genuinely side-effect free: it does not even open a
        # scan-run record, so asking "what would change?" never creates an index.
        scan_id = None if dry_run else store.start_scan(str(self.root), full=full, dry_run=False)
        result.scan_id = scan_id
        known = store.known_files()
        seen_paths: set[str] = set()
        seen_at = utc_now()
        records: list[dict[str, Any]] = []

        for path in self.walk():
            try:
                stat = path.stat()
            except OSError as exc:
                result.errors.append((str(path), "stat", str(exc)))
                continue

            relative = path.relative_to(self.root).as_posix()
            seen_paths.add(relative)
            previous = known.get(relative)

            if stat.st_size > self._max_bytes:
                # Still inventoried - the user should be able to see that a
                # large file exists - but never opened or hashed.
                result.skipped_too_large += 1
                record = self._describe(path, None)
                if previous:
                    result.unchanged.append(record)
                else:
                    result.new.append(record)
                    records.append(record.as_row(seen_at))
                continue

            stat_unchanged = (
                previous is not None
                and previous["size"] == stat.st_size
                and previous["mtime_ns"] == stat.st_mtime_ns
            )

            needs_hash = full or rehash or not stat_unchanged
            content_hash = previous["content_hash"] if previous else None
            if needs_hash:
                try:
                    content_hash, read_bytes = hash_file(path)
                except OSError as exc:
                    result.errors.append((str(path), "hash", str(exc)))
                    continue
                result.files_hashed += 1
                result.bytes_hashed += read_bytes

            record = self._describe(path, content_hash)

            if previous is None:
                result.new.append(record)
                records.append(record.as_row(seen_at))
            elif full:
                result.modified.append(record)
                records.append(record.as_row(seen_at))
            elif stat_unchanged:
                result.unchanged.append(record)
            elif content_hash is not None and content_hash == previous["content_hash"]:
                # Timestamp moved, bytes did not. Reporting this as modified
                # would reindex the whole corpus after a `touch -r` or a backup
                # restore, which is precisely what incrementality must avoid.
                # The new mtime is still persisted so the next scan's cheap
                # tier matches and the file is not re-hashed forever.
                result.unchanged.append(record)
                records.append(record.as_row(seen_at))
            else:
                result.modified.append(record)
                records.append(record.as_row(seen_at))

        result.deleted = sorted(set(known) - seen_paths)
        result.duration_ms = (time.perf_counter() - started) * 1000

        if not dry_run and scan_id is not None:
            if records:
                store.upsert_files(records)
            # Unchanged files get a single-column update rather than a full
            # rewrite, so scan cost scales with change size, not corpus size.
            written = {row["path"] for row in records}
            store.touch_seen([r.path for r in result.unchanged if r.path not in written], seen_at)
            if result.deleted:
                store.mark_deleted(result.deleted, when=seen_at)
            store.record_errors(scan_id, result.errors)
            store.set_meta("last_scan_at", seen_at)
            store.set_meta("scan_root", str(self.root))
            store.finish_scan(scan_id, result.summary())
        return result
