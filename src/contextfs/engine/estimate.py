"""Per-event file-time estimators: the plug-in slot for direction A (DESIGN.md §2.3).

An estimator maps a file's raw evidence (``time_evidence`` + ``observations``) and its folder
context to an interval per event. Several estimators write side by side into ``file_time``, so
P-A2 compares them by switching ``SearchConfig.time_source``. No re-index is needed.

**Input firewall (ADR-0006 E3).** Estimators receive only ``FileEvidence`` and ``FolderContext``,
built from index tables. They cannot see the benchmark's sealed truth, and the module must not
import ``contextfs.benchgen`` (enforced by test).

- ``raw_mtime``: every event is the file system's modified time, treated as sure. This is the
  baseline direction A must beat.
- ``rules_v0``: rules derived **only** from the frozen transfer table T v1
  (``data/transfer_table/T_v1.json``) and the frozen default calibration
  (``data/calibration/undamaged_model_v1.md``). No parameter is fitted. The two burst parameters
  are provisional, to be fitted on the dev split only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

__all__ = ["FileEvidence", "FolderContext", "EventTime", "ESTIMATORS", "RawMtime", "RulesV0"]

EPOCH_1990 = 631152000
FLOATING_LO, FLOATING_HI = 14 * 3600, 12 * 3600  # a zone-less time is somewhere in UTC−14…+12


@dataclass
class EventTime:
    """An interval for one event, with how it was obtained."""

    lo: int
    hi: int
    point: int
    confidence: str  # sure | probably | unknown
    method: str
    evidence: list[str] = field(default_factory=list)  # "source:kind" keys used

    def to_row(self) -> tuple:
        """Values for the ``file_time`` table (after file_id, estimator, event)."""
        return (
            self.lo,
            self.hi,
            self.point,
            self.confidence,
            self.method,
            json.dumps(self.evidence),
        )


@dataclass
class FileEvidence:
    """Everything an estimator may know about one file."""

    file_id: int
    rows: dict[str, dict]  # "source:kind" -> evidence row
    first_seen: int | None = None
    superseded_mtimes: list[int] = field(default_factory=list)

    def utc(self, key: str) -> int | None:
        """UTC seconds of an evidence row, if present."""
        r = self.rows.get(key)
        return r["utc_s"] if r and r.get("utc_s") is not None else None

    def interval(self, key: str) -> tuple[int, int] | None:
        """UTC interval for a time, widened for floating zones and coarse resolution."""
        r = self.rows.get(key)
        if not r or r.get("utc_s") is None:
            return None
        t, res = r["utc_s"], int(r.get("resolution_s") or 1)
        if r["tz_state"] == "floating":
            wall = r["wall_s"] if r.get("wall_s") is not None else t
            return wall - FLOATING_HI, wall + FLOATING_LO + res
        return t, t + res


@dataclass
class FolderContext:
    """Sibling statistics for burst detection: how many files in the folder share a birth time."""

    burst_sizes: dict[int, int] = field(default_factory=dict)  # file_id -> files born within W s


class RawMtime:
    """Baseline: trust the file system's modified time for every event."""

    name = "raw_mtime"

    def estimate(self, ev: FileEvidence, ctx: FolderContext) -> dict[str, EventTime]:
        """Every event = the file system's modified time."""
        m = ev.utc("ntfs:modified")
        if m is None:
            return {}
        t = EventTime(m, m + 1, m, "sure", "raw ntfs:modified", ["ntfs:modified"])
        return {"authored": t, "arrived_here": t, "last_user_edit": t}


class RulesV0:
    """Rules from T v1 and the frozen default calibration. Nothing here is fitted on data."""

    name = "rules_v0"
    #: Provisional (ADR-0006 §3: to be fitted on dev only): a folder burst is ≥ N files whose
    #: NTFS birth times fall within W seconds, i.e. a bulk copy or extraction.
    BURST_N = 5
    BURST_W = 120

    def estimate(self, ev: FileEvidence, ctx: FolderContext) -> dict[str, EventTime]:
        """Per-event intervals from the T v1 rules (see module docstring)."""
        b, m = ev.utc("ntfs:created"), ev.utc("ntfs:modified")
        if b is None or m is None:
            return {}
        out: dict[str, EventTime] = {}
        copy_sig = b > m + 2  # T v1: CopyFile family → birth reset, mtime survived
        burst = ctx.burst_sizes.get(ev.file_id, 1) >= self.BURST_N
        suspect = burst and abs(b - m) <= 2 and not copy_sig  # content-only copy: both reset
        downloaded = "motw:zone" in ev.rows

        # authored: the earliest in-file creation clue (survives every transfer in T v1).
        infile_created = [
            (k, ev.interval(k))
            for k in ("exif:original", "ooxml:created", "pdf:created", "zip:max_entry")
        ]
        infile_created = [(k, iv) for k, iv in infile_created if iv]
        infile_modified = [(k, ev.interval(k)) for k in ("ooxml:modified", "pdf:modified")]
        infile_modified = [(k, iv) for k, iv in infile_modified if iv and iv[0] > EPOCH_1990]
        if infile_created:
            k, (lo, hi) = min(infile_created, key=lambda kv: kv[1][0])
            out["authored"] = EventTime(
                lo, hi, (lo + hi) // 2, "probably", "earliest in-file creation", [k]
            )
        elif not suspect:
            t = min(b, m)
            out["authored"] = EventTime(
                t,
                t + 1,
                t,
                "probably",
                "min(ntfs created, modified)",
                ["ntfs:created", "ntfs:modified"],
            )
        else:
            out["authored"] = EventTime(
                EPOCH_1990, b, b, "unknown", "bulk copy destroyed fs times", ["ntfs:created"]
            )

        # last_user_edit
        never_edited_download = downloaded and abs(m - b) <= 60 and not copy_sig
        if never_edited_download:
            pass  # a download nobody edited has no edit event here
        elif not suspect:
            out["last_user_edit"] = EventTime(
                m,
                m + 1,
                m,
                "sure",
                "ntfs:modified (survives CopyFile copies, T v1)",
                ["ntfs:modified"],
            )
        elif infile_modified:
            k, (lo, hi) = max(infile_modified, key=lambda kv: kv[1][0])
            out["last_user_edit"] = EventTime(
                lo,
                hi,
                (lo + hi) // 2,
                "probably",
                "in-file modified (fs times reset by bulk copy)",
                [k],
            )
        else:
            lo = out["authored"].lo
            out["last_user_edit"] = EventTime(
                lo, b, b, "unknown", "bulk copy; no in-file time", ["ntfs:created"]
            )

        # arrived_here
        if suspect:
            lo = out["authored"].lo
            out["arrived_here"] = EventTime(
                lo, b, b, "unknown", "bulk copy reset arrival time", ["ntfs:created"]
            )
        elif copy_sig and downloaded:
            out["arrived_here"] = EventTime(
                m,
                m + 1,
                m,
                "probably",
                "download stamp kept as mtime through a copy",
                ["ntfs:modified", "motw:zone"],
            )
        elif copy_sig:
            a = out["authored"]
            out["arrived_here"] = EventTime(
                min(a.lo, m),
                b,
                min(a.point, m),
                "probably",
                "copied here at birth time; first existed by authoring",
                ["ntfs:created", "ntfs:modified", *a.evidence],
            )
        else:
            out["arrived_here"] = EventTime(b, b + 1, b, "sure", "ntfs:created", ["ntfs:created"])
        return out


ESTIMATORS = {e.name: e for e in (RawMtime(), RulesV0())}


def folder_context(births: dict[int, int], window: int = RulesV0.BURST_W) -> FolderContext:
    """Burst sizes from {file_id: birth_utc} of one folder (sorted sweep, O(n log n))."""
    items = sorted(births.items(), key=lambda kv: kv[1])
    sizes: dict[int, int] = {}
    start = 0
    for end in range(len(items)):
        while items[end][1] - items[start][1] > window:
            start += 1
        run = end - start + 1
        for i in range(start, end + 1):
            sizes[items[i][0]] = max(sizes.get(items[i][0], 1), run)
    return FolderContext(sizes)
