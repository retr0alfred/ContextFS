"""Capture raw time evidence for one file (ADR-0006 §1). Nothing is interpreted here.

Sources:
- ``ntfs``: created / modified / accessed (UTC). Accessed is weak and never ranked.
- ``ooxml``: core.xml created / modified (UTC "Z").
- ``pdf``: CreationDate / ModDate (an offset if present, else floating).
- ``exif``: DateTimeOriginal (floating unless OffsetTimeOriginal is present).
- ``zip``: the latest entry time (DOS time: floating, 2 s resolution).
- ``motw``: Mark-of-the-Web ZoneId, which records a download. Only the zone number is kept; the
  source URL is never stored.

Timestamps are stored as UTC seconds when the zone is known. Floating times store their wall-clock
seconds plus the machine's current offset as a provisional UTC. Estimators widen floating times
into intervals.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from contextfs.datagen.filetimes import read_infile_times

__all__ = ["capture", "Evidence"]

Evidence = dict  # {"source","kind","utc_s","wall_s","tz_state","resolution_s","reliability","raw"}


def _row(
    source, kind, utc_s, tz_state="utc", wall_s=None, resolution_s=1, reliability="normal", raw=None
):
    return {
        "source": source,
        "kind": kind,
        "utc_s": int(utc_s) if utc_s is not None else None,
        "wall_s": wall_s,
        "tz_state": tz_state,
        "resolution_s": resolution_s,
        "reliability": reliability,
        "raw": raw,
    }


def local_datetime(utc_s: float) -> datetime:
    """``utc_s`` as a local datetime; never raises.

    ``datetime.fromtimestamp(...).astimezone()`` raises on Windows for times before 1970 and
    overflows for extreme ones (QA H3: a pre-1970 file crashed the window). Out of range, the UTC
    time is returned; beyond datetime's range, the nearest representable time.
    """
    from datetime import timedelta

    try:
        dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=float(utc_s))
    except (OverflowError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc) if utc_s < 0 else datetime.max.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone()
    except (OverflowError, OSError, ValueError):
        return dt


def _parse_infile(value: str) -> tuple[int, str, int]:
    """(utc_s, tz_state, wall_s) from an ISO string; floating if it has no offset."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        state = (
            "utc"
            if dt.utcoffset().total_seconds() == 0 and value.endswith(("Z", "+00:00"))
            else "offset"
        )
        return int(dt.timestamp()), state, int(dt.replace(tzinfo=None).timestamp())
    local = dt.astimezone()  # provisional: the machine's zone
    return int(local.timestamp()), "floating", int(dt.replace(tzinfo=timezone.utc).timestamp())


def capture(entry, parsed_copy: Path | None) -> list[Evidence]:
    """Evidence for a file. ``parsed_copy`` is the temp copy of its bytes (None if not read)."""
    rows = [
        _row("ntfs", "created", entry.birth_ns / 1e9),
        _row("ntfs", "modified", entry.mtime_ns / 1e9),
        _row("ntfs", "accessed", entry.atime_ns / 1e9, reliability="low"),
    ]
    if parsed_copy is not None:
        for key, value in read_infile_times(parsed_copy).items():
            source, _, kind = key.partition(":")
            try:
                utc_s, state, wall_s = _parse_infile(value)
            except ValueError:
                continue
            res = 2 if source == "zip" else 1
            rows.append(
                _row(
                    source, kind, utc_s, tz_state=state, wall_s=wall_s, resolution_s=res, raw=value
                )
            )
    if not entry.placeholder:
        zone = _mark_of_the_web(entry.path)
        if zone is not None:
            rows.append(_row("motw", "zone", None, tz_state="floating", raw=str(zone)))
    return rows


def _mark_of_the_web(path: str) -> int | None:
    """The ZoneId from a Zone.Identifier stream (3 = Internet), or None. Read-only."""
    try:
        with open(path + ":Zone.Identifier", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.strip().lower().startswith("zoneid="):
                    return int(line.split("=", 1)[1].strip() or 0)
    except (OSError, ValueError):
        return None
    return None
