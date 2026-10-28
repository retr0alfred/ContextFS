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
- ``recon_v1`` (direction A, v2-P6; ``docs/engine/RECON_V1.md``, log.md Phase 34): reconciles the
  file-system times with in-file times, the Mark-of-the-Web, camera capture times, sibling files
  and the root's *bulk-copy events* (bursts of births that mark a migration). An event gets a date
  only on positive evidence that it happened. Each answer names its *method*; the only fitted part
  is a per-method, widen-only interval calibration (``RECON_PARAMS``, dev split only).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

__all__ = [
    "FileEvidence",
    "FolderContext",
    "EventTime",
    "ESTIMATORS",
    "RawMtime",
    "RulesV0",
    "ReconV1",
    "RECON_PARAMS",
    "detect_bulk_events",
]

EPOCH_1990 = 631152000
EPOCH_1995 = 788918400  # in-file "created" earlier than this is a template or tool default
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
    """What an estimator may know beyond one file: its folder and its root."""

    burst_sizes: dict[int, int] = field(default_factory=dict)  # file_id -> files born within W s
    bulk_events: list[tuple[int, int]] = field(default_factory=list)  # root's bulk-copy [start, end]
    sibling_created: list[int] = field(default_factory=list)  # siblings' in-file created times


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


#: Bulk-copy detection: births split into clusters at gaps over BULK_GAP seconds; a cluster is a
#: bulk-copy event when it holds at least BULK_MIN files and is BULK_DENSITY times denser than the
#: root's average birth rate. Chosen on dev data (log.md Phase 34), so declared as dev-fitted.
BULK_GAP, BULK_MIN, BULK_DENSITY = 1800, 30, 100.0


def detect_bulk_events(births: list[int]) -> list[tuple[int, int]]:
    """Bulk-copy events of a root from its files' NTFS birth times (a pure function of the set)."""
    if len(births) < BULK_MIN:
        return []
    s = sorted(births)
    rate = len(s) / max(s[-1] - s[0], 86400)
    events, start = [], 0
    for i in range(1, len(s) + 1):
        if i == len(s) or s[i] - s[i - 1] > BULK_GAP:
            size, span = i - start, s[i - 1] - s[start]
            if size >= BULK_MIN and size / max(span, 600) >= BULK_DENSITY * rate:
                events.append((s[start], s[i - 1]))
            start = i
    return events


#: Per-method calibration, fitted on dev only by ``scripts/fit_recon.py`` (generated module
#: ``recon_params.py``). Maps "<event>:<method>" to {"q_lo": s, "q_hi": s, "conf": label};
#: q_lo <= 0 <= q_hi, so intervals only ever widen. Empty = uncalibrated.
try:
    from contextfs.engine.recon_params import PARAMS as RECON_PARAMS
except ImportError:  # not fitted yet
    RECON_PARAMS: dict[str, dict] = {}


class ReconV1:
    """Reconciled per-event time (direction A). See the module docstring and RECON_V1.md."""

    name = "recon_v1"
    COPY_SLACK = 2  # b > m + 2 s: a CopyFile-family copy (birth reset, mtime kept; T v1)
    EDIT_SLACK = 60  # m later than b (or a bulk event) by more than this: a real edit
    UNEDITED = 600  # in-file modified - created <= 10 min: the document was never re-saved
    #: Arrival of a reset file with no download mark or camera date: True = anywhere between its
    #: creation and the copy (variant B), False = at its creation (variant A). Variant A won on the
    #: pre-declared criterion, the lowest dev (LOPO) macro interval score over damage types: 230.8 vs
    #: 314.6 (log.md Phase 34). B is reported as an ablation.
    AGNOSTIC_MOVED_ARRIVAL = False

    def __init__(self, params: dict | None = None) -> None:
        """``params`` overrides RECON_PARAMS (the fitting harness uses this; the engine does not)."""
        self.params = params

    def _p(self) -> dict:
        return RECON_PARAMS if self.params is None else self.params

    def _event(self, event: str, lo: int, hi: int, point: int, conf: str, method: str, keys: list) -> EventTime:
        p = self._p().get(f"{event}:{method}")
        if p:
            lo, hi = min(lo, point + p["q_lo"]), max(hi, point + p["q_hi"])
            conf = p["conf"]
        return EventTime(lo, hi, point, conf, method, keys)

    def estimate(self, ev: FileEvidence, ctx: FolderContext) -> dict[str, EventTime]:
        """Per-event intervals; see RECON_V1.md for the rules."""
        b, m = ev.utc("ntfs:created"), ev.utc("ntfs:modified")
        if b is None or m is None:
            return {}
        bulk = next(((s, e) for s, e in ctx.bulk_events if s - 60 <= b <= e + 60), None)
        copy = b > m + self.COPY_SLACK  # precedence: a copy signature beats bulk membership
        reset = bulk is not None and not copy and m <= bulk[1] + self.EDIT_SLACK
        reset_edited = bulk is not None and not copy and m > bulk[1] + self.EDIT_SLACK
        moved = reset or reset_edited  # birth (and maybe mtime) reset by a content-only copy
        upper = bulk[0] if bulk else b  # the file existed elsewhere before this
        motw = "motw:zone" in ev.rows
        capture = ev.interval("exif:original")
        created = [(k, iv) for k in ("exif:original", "ooxml:created", "pdf:created", "zip:max_entry")
                   if (iv := ev.interval(k)) and EPOCH_1995 <= iv[0] <= b + 86400]
        modified = [(k, iv) for k in ("ooxml:modified", "pdf:modified")
                    if (iv := ev.interval(k)) and iv[0] >= EPOCH_1995]
        sib = sorted(ctx.sibling_created)
        floor = sib[0] if sib else EPOCH_1990
        out: dict[str, EventTime] = {}

        # authored: in-file creation survives every transfer (T v1); else the file system.
        if created:
            k, (lo, hi) = min(created, key=lambda kv: kv[1][0])
            out["authored"] = self._event("authored", lo, hi, (lo + hi) // 2, "probably", "infile_created", [k])
        elif motw and not moved:
            # someone else wrote it before it was downloaded: on or before the download stamp
            t = m if copy else b
            out["authored"] = self._event("authored", min(floor, t), t, t, "unknown", "download_no_infile",
                                          ["ntfs:created", "motw:zone"])
        elif copy and not moved:
            # a copy reset the birth; the kept mtime is the last edit, so creation is on or before it
            out["authored"] = self._event("authored", min(floor, m), m + 1, m, "unknown", "copy_mtime",
                                          ["ntfs:modified"])
        elif not moved:
            t = min(b, m)
            out["authored"] = self._event("authored", t, t + 1, t, "probably", "fs_min",
                                          ["ntfs:created", "ntfs:modified"])
        else:
            lo, hi = min(floor, upper), upper
            point = min(max(sib[len(sib) // 2], lo), hi) if sib else hi
            out["authored"] = self._event("authored", lo, hi, point, "unknown", "reset_no_infile",
                                          ["ntfs:created"])
        a = out["authored"]

        # came into your files (arrived_here): download, import, or creation.
        if motw:
            if copy:
                arr = self._event("arrived_here", m, m + 1, m, "probably", "download_copy_mtime",
                                  ["ntfs:modified", "motw:zone"])
            elif moved:
                arr = self._event("arrived_here", a.lo, upper, a.point, "unknown", "download_reset",
                                  ["motw:zone", *a.evidence])
            else:
                arr = self._event("arrived_here", b, b + 1, b, "sure", "download_birth",
                                  ["ntfs:created", "motw:zone"])
        elif capture:
            if copy or moved:
                arr = self._event("arrived_here", capture[0], max(upper, capture[0]), capture[0], "unknown",
                                  "camera_copied", ["exif:original", "ntfs:created"])
            else:
                arr = self._event("arrived_here", b, b + 1, b, "sure", "camera_import_birth", ["ntfs:created"])
        elif moved and self.AGNOSTIC_MOVED_ARRIVAL:
            # a content-only copy reset the times and (T v1.1) dropped any download mark: the file
            # came into the collection somewhere between its creation and the copy. The interval
            # covers a user's own file and a download that lost its mark alike (no origin guess).
            arr = self._event("arrived_here", a.lo, max(upper, a.hi), a.point, "unknown", "moved_unmarked",
                              [*a.evidence, "ntfs:created"])
        else:
            arr = self._event("arrived_here", a.lo, a.hi, a.point, a.confidence, "own_created", a.evidence)
        out["arrived_here"] = arr

        # last_user_edit: only on positive evidence that an edit happened.
        edit = None
        if reset_edited:
            edit = ("fs_mtime_after_copy", m, m + 1, m, "sure", ["ntfs:modified"])
        elif motw:
            if not copy and not moved and m > b + self.EDIT_SLACK:
                edit = ("download_edited", m, m + 1, m, "sure", ["ntfs:modified", "motw:zone"])
        elif capture:
            if not moved and not (capture[0] - 3600 <= m <= capture[1] + 3600):  # m left the capture
                edit = ("photo_edited", m, m + 1, m, "probably", ["ntfs:modified", "exif:original"])
        elif not moved:
            edit = ("fs_mtime", m, m + 1, m, "sure", ["ntfs:modified"])
        elif modified:
            k, (lo, hi) = max(modified, key=lambda kv: kv[1][0])
            if created and lo - min(iv[0] for _, iv in created) <= self.UNEDITED:
                # never re-saved: a user's quick export, or a download whose mark the copy dropped;
                # kept as a weak event, not deleted (soft existence; review finding 1)
                edit = ("reset_infile_unedited", lo, hi, (lo + hi) // 2, "unknown", [k])
            else:
                edit = ("reset_infile_modified", lo, hi, (lo + hi) // 2, "probably", [k])
        else:
            edit = ("reset_no_evidence", a.lo, upper, upper, "unknown", ["ntfs:created"])
        if edit:
            method, lo, hi, point, conf, keys = edit
            out["last_user_edit"] = self._event("last_user_edit", lo, hi, point, conf, method, keys)
        return out


ESTIMATORS = {e.name: e for e in (RawMtime(), RulesV0(), ReconV1())}


def folder_context(
    births: dict[int, int],
    window: int = RulesV0.BURST_W,
    bulk_events: list[tuple[int, int]] | None = None,
    sibling_created: list[int] | None = None,
) -> FolderContext:
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
    return FolderContext(sizes, list(bulk_events or []), list(sibling_created or []))
