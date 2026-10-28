r"""The only module that touches user files (ADR-0011). Read-only, by construction.

Walking:
- ``os.scandir`` on ``\\\\?\\`` paths (long paths), with an explicit stack;
- reparse points decided **by tag**: junctions, symlinks and unknown tags are skipped; OneDrive
  ``CLOUD*`` directories are descended (Rev. 1);
- a (volume, file id) loop guard;
- default exclusions anchored to system folders plus name rules, all editable;
- the index directory is always self-excluded;
- every unreadable directory becomes an error row. Nothing is skipped silently.

Reading:
- ``CreateFileW(GENERIC_READ, SHARE_READ|WRITE|DELETE)``, read into memory up to a cap, re-check
  the handle's size and mtime, close. The handle is held for milliseconds, so Word can still save.
- Cloud-only placeholders are **never opened** (that would download them).
- Nothing here requests write access or ``FILE_WRITE_ATTRIBUTES``.
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "Entry",
    "WalkError",
    "ReadError",
    "walk",
    "read_bytes",
    "long_path",
    "is_placeholder",
    "DEFAULT_EXCLUDED_NAMES",
    "default_anchored_exclusions",
]

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
FILE_ATTRIBUTE_ENCRYPTED = 0x4000
_PLACEHOLDER = (
    FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
IO_REPARSE_TAG_SYMLINK = 0xA000000C

#: Directory names skipped anywhere (caches, VCS, environments). Editable in Settings.
DEFAULT_EXCLUDED_NAMES = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".gradle",
        ".idea",
        ".vs",
        "$recycle.bin",
        "system volume information",
        ".contextfs",
        ".cache",
    }
)


def default_anchored_exclusions() -> list[Path]:
    """System and application folders excluded by absolute location (not by name)."""
    env = os.environ
    out = [
        Path(env.get("SystemRoot", r"C:\Windows")),
        Path(env.get("ProgramFiles", r"C:\Program Files")),
        Path(env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(env.get("ProgramData", r"C:\ProgramData")),
        Path(env.get("LOCALAPPDATA", "")),
        Path(env.get("APPDATA", "")),
    ]
    return [p for p in out if str(p) not in ("", ".")]


def long_path(path: Path | str) -> str:
    """Absolute path with the Win32 long-path prefix (no-op elsewhere)."""
    p = os.path.abspath(str(path))
    if sys.platform != "win32" or p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):  # UNC
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def plain_path(path: str) -> str:
    """Strip the long-path prefix for display and for ``os.startfile``."""
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    return path[4:] if path.startswith("\\\\?\\") else path


def is_placeholder(attributes: int) -> bool:
    """True for cloud-only files that would be downloaded if opened."""
    return bool(attributes & _PLACEHOLDER)


def _is_cloud_tag(tag: int) -> bool:
    return (tag & 0xFFFF0FFF) == 0x9000001A


@dataclass
class Entry:
    """One file found by the walker (metadata only; nothing has been opened)."""

    root_id: int
    rel_path: str  # root-relative, forward slashes
    path: str  # long-path form
    size: int
    mtime_ns: int
    birth_ns: int
    atime_ns: int
    attributes: int
    file_id: int  # NTFS file index (0 if unavailable)
    volume: int

    @property
    def placeholder(self) -> bool:
        """True for a cloud-only file (never opened)."""
        return is_placeholder(self.attributes)

    @property
    def encrypted(self) -> bool:
        """True for an EFS-encrypted file (metadata-only by default, ADR-0016)."""
        return bool(self.attributes & FILE_ATTRIBUTE_ENCRYPTED)


@dataclass
class WalkError:
    """A directory or entry the walker could not process, with the reason."""

    path: str
    kind: str  # denied | missing | io | loop | reparse_skipped
    detail: str = ""


@dataclass
class WalkStats:
    """Counters and errors from one walk."""

    files: int = 0
    dirs: int = 0
    placeholders: int = 0
    errors: list[WalkError] = field(default_factory=list)
    listed_dirs: set[str] = field(default_factory=set)  # dirs listed successfully (may tombstone)
    #: For each listed dir, the subfolders it still contains (descended, unreadable or reparse
    #: points alike; folders excluded by name are left out). A folder missing here has gone.
    child_dirs: dict[str, set[str]] = field(default_factory=dict)


def walk(
    root: Path | str,
    root_id: int,
    *,
    excluded_names: frozenset[str] = DEFAULT_EXCLUDED_NAMES,
    anchored: list[Path] | None = None,
    self_exclude: list[Path] | None = None,
    stats: WalkStats | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Iterator[Entry]:
    """Yield every file under ``root``. Never raises for per-directory problems."""
    stats = stats if stats is not None else WalkStats()
    root_lp = long_path(root)
    blocked = {os.path.normcase(long_path(p)) for p in (anchored or []) + (self_exclude or [])}
    seen: set[tuple[int, int]] = set()
    stack = [root_lp]
    while stack:
        if cancel and cancel():
            return
        d = stack.pop()
        try:
            st = os.stat(d)
            key = (st.st_dev, st.st_ino)
            if key in seen and st.st_ino:
                stats.errors.append(WalkError(plain_path(d), "loop"))
                continue
            seen.add(key)
            with os.scandir(d) as it:
                entries = list(it)
        except PermissionError as exc:
            stats.errors.append(WalkError(plain_path(d), "denied", str(exc)))
            continue
        except FileNotFoundError as exc:
            stats.errors.append(WalkError(plain_path(d), "missing", str(exc)))
            continue
        except OSError as exc:
            stats.errors.append(WalkError(plain_path(d), "io", str(exc)))
            continue
        stats.dirs += 1
        here = _rel(root_lp, d)
        stats.listed_dirs.add(here)
        kids = stats.child_dirs.setdefault(here, set())
        for e in sorted(entries, key=lambda x: x.name):
            try:
                est = e.stat(follow_symlinks=False)
            except OSError as exc:
                stats.errors.append(WalkError(plain_path(e.path), "io", str(exc)))
                continue
            attrs = getattr(est, "st_file_attributes", 0)
            is_dir = stat.S_ISDIR(est.st_mode) or (attrs & 0x10)
            if is_dir:
                if e.name.lower() in excluded_names or os.path.normcase(e.path) in blocked:
                    continue
                kids.add(e.name)
                if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
                    tag = getattr(est, "st_reparse_tag", 0)
                    if not _is_cloud_tag(tag):
                        stats.errors.append(
                            WalkError(plain_path(e.path), "reparse_skipped", hex(tag))
                        )
                        continue
                stack.append(e.path)
                continue
            if attrs & FILE_ATTRIBUTE_REPARSE_POINT and getattr(est, "st_reparse_tag", 0) in (
                IO_REPARSE_TAG_SYMLINK,
                IO_REPARSE_TAG_MOUNT_POINT,
            ):
                continue  # file symlinks: the target is indexed where it really lives
            stats.files += 1
            if is_placeholder(attrs):
                stats.placeholders += 1
            yield Entry(
                root_id=root_id,
                rel_path=_rel(root_lp, e.path),
                path=e.path,
                size=est.st_size,
                mtime_ns=_clamp_ns(est.st_mtime_ns),
                birth_ns=_clamp_ns(int(getattr(est, "st_birthtime_ns", est.st_ctime_ns))),
                atime_ns=_clamp_ns(est.st_atime_ns),
                attributes=attrs,
                file_id=est.st_ino,
                volume=est.st_dev,
            )


#: Nanosecond times must fit SQLite's 64-bit integers: a zero Windows file time (1601-01-01) is
#: -1.16e19 ns and aborted every scan (QA H2, Phase 33). Clamping here keeps the stored value and
#: the next walk's value identical, so such a file still counts as unchanged.
_NS_MIN, _NS_MAX = -(2**63) + 1, 2**63 - 1


def _clamp_ns(ns: int) -> int:
    return min(max(int(ns), _NS_MIN), _NS_MAX)


def _rel(root_lp: str, path: str) -> str:
    rel = os.path.relpath(path, root_lp)
    return "" if rel == "." else rel.replace("\\", "/")


class ReadError(Exception):
    """A file could not be read safely; ``kind`` says why."""

    def __init__(self, kind: str, detail: str = "") -> None:
        """Record why the read failed."""
        super().__init__(f"{kind}: {detail}")
        self.kind = kind  # placeholder | locked | denied | missing | too_large | changed | io
        self.detail = detail


def read_bytes(entry: Entry, cap: int = 100 * 2**20) -> bytes:
    """Read a whole file with full sharing, then verify it did not change while reading."""
    if entry.placeholder:
        raise ReadError("placeholder", "cloud-only file: never opened")
    if entry.size > cap:
        raise ReadError("too_large", f"{entry.size} bytes > cap {cap}")
    if sys.platform != "win32":  # pragma: no cover
        with open(entry.path, "rb") as fh:
            return fh.read()
    import ctypes
    import ctypes.wintypes as wt
    import msvcrt

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wt.HANDLE
    k32.CreateFileW.argtypes = [
        wt.LPCWSTR,
        wt.DWORD,
        wt.DWORD,
        wt.LPVOID,
        wt.DWORD,
        wt.DWORD,
        wt.HANDLE,
    ]
    GENERIC_READ = 0x80000000
    SHARE_ALL = 0x1 | 0x2 | 0x4
    OPEN_EXISTING = 3
    FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
    h = k32.CreateFileW(
        entry.path, GENERIC_READ, SHARE_ALL, None, OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, None
    )
    if h in (None, wt.HANDLE(-1).value):
        err = ctypes.get_last_error()
        kind = {2: "missing", 3: "missing", 5: "denied", 32: "locked", 33: "locked"}.get(err, "io")
        raise ReadError(kind, f"WinError {err}")
    fd = msvcrt.open_osfhandle(h, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb", closefd=True) as fh:
        before = os.fstat(fh.fileno())
        data = fh.read(cap + 1)
        after = os.fstat(fh.fileno())
    if len(data) > cap:
        raise ReadError("too_large", f"grew beyond cap {cap}")
    if (before.st_size, before.st_mtime_ns) != (
        after.st_size,
        after.st_mtime_ns,
    ) or after.st_size != len(data):
        raise ReadError("changed", "file changed while being read")
    return data
