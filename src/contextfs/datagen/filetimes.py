"""File-system and in-file timestamps for the benchmark generator and its audits.

Two jobs:

1. **Write** all three NTFS times (creation, last-write, last-access). ``os.utime`` sets only
   access and modification, so every generated file would get "now" as its creation time and
   look *copied* to any reconciler (skeptic attack N1, log.md Phase 30). ``set_file_times`` uses
   ``SetFileTime`` on Windows. It is used **only on the generator's own files**; the engine never
   requests write access to user files (ADR-0011).
2. **Read** the times a file carries *inside itself*: Office ``docProps/core.xml``, PDF Info
   dates, EXIF ``DateTimeOriginal``, zip entry times. Used by the transfer-table measurement and
   the zero-damage audit. The engine's own capture layer (v2-P4) will reuse these readers.
"""

from __future__ import annotations

import os
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["FsTimes", "get_file_times", "set_file_times", "read_infile_times"]

_EPOCH_AS_FILETIME = 116444736000000000  # 1601-01-01 → 1970-01-01, in 100 ns ticks


@dataclass(frozen=True)
class FsTimes:
    """File-system times as timezone-aware UTC datetimes."""

    created: datetime
    modified: datetime
    accessed: datetime


def _to_utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def get_file_times(path: Path | str) -> FsTimes:
    """Return creation (birth), modification and access times in UTC."""
    st = os.stat(path)
    birth = getattr(st, "st_birthtime", None)
    if birth is None:  # pragma: no cover - non-Windows fallback
        birth = st.st_ctime
    return FsTimes(_to_utc(birth), _to_utc(st.st_mtime), _to_utc(st.st_atime))


def _filetime(when: datetime):
    import ctypes.wintypes as wt

    if when.tzinfo is None:
        when = when.astimezone()  # naive → local wall time, as a person means it
    ticks = int(when.timestamp() * 10_000_000) + _EPOCH_AS_FILETIME
    return wt.FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)


def set_file_times(
    path: Path | str,
    created: datetime | None = None,
    modified: datetime | None = None,
    accessed: datetime | None = None,
) -> None:
    """Set any of a file's three times. ``None`` leaves that time unchanged.

    Naive datetimes are interpreted as local wall-clock time.
    """
    if sys.platform != "win32":  # pragma: no cover
        stamp_m = (modified or datetime.now()).timestamp()
        stamp_a = (accessed or modified or datetime.now()).timestamp()
        os.utime(path, (stamp_a, stamp_m))
        return

    import ctypes
    import ctypes.wintypes as wt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wt.HANDLE
    kernel32.CreateFileW.argtypes = [
        wt.LPCWSTR,
        wt.DWORD,
        wt.DWORD,
        wt.LPVOID,
        wt.DWORD,
        wt.DWORD,
        wt.HANDLE,
    ]
    kernel32.SetFileTime.argtypes = [wt.HANDLE, wt.LPVOID, wt.LPVOID, wt.LPVOID]
    kernel32.CloseHandle.argtypes = [wt.HANDLE]

    FILE_WRITE_ATTRIBUTES = 0x0100
    share = 0x1 | 0x2 | 0x4  # read | write | delete
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000  # allows directories too
    handle = kernel32.CreateFileW(
        str(Path(path).resolve()),
        FILE_WRITE_ATTRIBUTES,
        share,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle in (None, wt.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        refs = [
            ctypes.byref(_filetime(t)) if t is not None else None
            for t in (created, accessed, modified)
        ]
        if not kernel32.SetFileTime(handle, *refs):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)


# ---------------------------------------------------------------------------
# In-file timestamps
# ---------------------------------------------------------------------------

_OOXML_EXT = {".docx", ".pptx", ".xlsx", ".docm", ".pptm", ".xlsm"}
_CORE_RE = re.compile(r"<dcterms:(created|modified)[^>]*>([^<]+)</dcterms:\1>", re.IGNORECASE)
_PDF_DATE_RE = re.compile(
    r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?([Zz+\-])?(\d{2})?'?(\d{2})?'?"
)


def _parse_pdf_date(raw: str) -> str | None:
    m = _PDF_DATE_RE.search(str(raw))
    if not m:
        return None
    y, mo, d, h, mi, s, sign, oh, om = m.groups()
    out = f"{y}-{mo or '01'}-{d or '01'}T{h or '00'}:{mi or '00'}:{s or '00'}"
    if sign in ("Z", "z"):
        out += "+00:00"
    elif sign in ("+", "-") and oh:
        out += f"{sign}{oh}:{om or '00'}"
    return out  # no sign → floating (zone unknown)


def read_infile_times(path: Path | str) -> dict[str, str]:
    """Return the timestamps a file carries inside itself, as ISO strings.

    Keys are ``source:kind``, e.g. ``ooxml:created``, ``pdf:modified``, ``exif:original``,
    ``zip:max_entry``. A value without a UTC offset is *floating* (its zone is unknown).
    Never raises: an unreadable file yields ``{}``.
    """
    p = Path(path)
    ext = p.suffix.lower()
    out: dict[str, str] = {}
    try:
        if ext in _OOXML_EXT:
            with zipfile.ZipFile(p) as zf:
                xml = zf.read("docProps/core.xml").decode("utf-8", "replace")
            for kind, value in _CORE_RE.findall(xml):
                out[f"ooxml:{kind.lower()}"] = value.strip()
        elif ext == ".pdf":
            from pypdf import PdfReader

            info = PdfReader(str(p)).metadata or {}
            for key, kind in (("/CreationDate", "created"), ("/ModDate", "modified")):
                if key in info and (parsed := _parse_pdf_date(info[key])):
                    out[f"pdf:{kind}"] = parsed
        elif ext in {".jpg", ".jpeg", ".tif", ".tiff"}:
            from PIL import Image

            with Image.open(p) as im:
                exif_ifd = im.getexif().get_ifd(0x8769)
            raw = exif_ifd.get(36867)  # DateTimeOriginal "YYYY:MM:DD HH:MM:SS"
            if raw:
                date, _, clock = str(raw).partition(" ")
                value = date.replace(":", "-") + "T" + clock
                offset = exif_ifd.get(36881)  # OffsetTimeOriginal "+05:30"
                out["exif:original"] = value + (str(offset) if offset else "")
        elif ext == ".zip":
            with zipfile.ZipFile(p) as zf:
                stamps = [datetime(*info.date_time) for info in zf.infolist()]
            if stamps:
                out["zip:max_entry"] = max(stamps).isoformat()  # DOS time: floating, 2 s
    except Exception:  # noqa: BLE001 - an audit reader must never crash the audit
        return {}
    return out
