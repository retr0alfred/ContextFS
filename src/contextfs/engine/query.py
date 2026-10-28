"""Open-vocabulary query understanding → ``QueryFrame`` (ADR-0008 §5, DESIGN.md §2.4).

Never raises. Every stage degrades to plain lexical + dense search.

- **Format:** an editable lexicon gives a soft hint over file kinds ("pdf", "slides", "photo").
- **Time:** month/season/year phrases, "early/mid/late X", relative phrases ("last month",
  "3 weeks ago"). Readings look *backwards* from ``as_of``, because re-finding is about the past.
  Each cue is a soft interval with a band that widens with the memory's age, and carries an
  **event hint** taken from the verbs:
  - downloaded/got/saved/received → ``arrived_here``;
  - edited/worked on/wrote/updated → ``last_user_edit``;
  - published/written in <year> → ``authored``;
  - otherwise ``any``.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

__all__ = ["QueryFrame", "TimeCue", "parse", "FORMAT_LEXICON"]

FORMAT_LEXICON: dict[str, tuple[str, ...]] = {
    "pdf": (".pdf",),
    "slides": (".pptx", ".ppt"),
    "slide": (".pptx", ".ppt"),
    "deck": (".pptx", ".ppt"),
    "presentation": (".pptx", ".ppt"),
    "ppt": (".pptx", ".ppt"),
    "spreadsheet": (".xlsx", ".xls", ".csv"),
    "sheet": (".xlsx", ".xls", ".csv"),
    "excel": (".xlsx", ".xls"),
    "csv": (".csv",),
    "word": (".docx", ".doc"),
    "docx": (".docx",),
    "report": (".docx", ".pdf"),
    "photo": (".jpg", ".jpeg", ".png", ".heic"),
    "photos": (".jpg", ".jpeg", ".png", ".heic"),
    "picture": (".jpg", ".jpeg", ".png", ".heic"),
    "pictures": (".jpg", ".jpeg", ".png", ".heic"),
    "pic": (".jpg", ".jpeg", ".png"),
    "image": (".jpg", ".jpeg", ".png", ".heic"),
    "zip": (".zip", ".7z", ".rar"),
    "archive": (".zip", ".7z", ".rar"),
    "code": (".py", ".js", ".ts", ".java", ".c", ".cpp", ".sql", ".sh", ".ipynb"),
    "script": (".py", ".sh", ".js", ".ps1"),
    "notebook": (".ipynb",),
    "notes": (".md", ".txt", ".docx"),
    "note": (".md", ".txt"),
    "text": (".txt", ".md"),
}
EVENT_VERBS = {
    "arrived_here": ("download", "downloaded", "got", "saved", "received", "sent me", "shared"),
    "last_user_edit": (
        "edited",
        "editing",
        "worked on",
        "working on",
        "wrote",
        "writing",
        "updated",
        "made",
        "changed",
        "typed",
    ),
    "authored": ("published", "written in", "from the year"),
}
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS |= {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
MONTHS["sept"] = 9


@dataclass
class TimeCue:
    """A soft time interval from the query."""

    lo: datetime
    hi: datetime
    band_days: float
    event_hint: str  # authored | arrived_here | last_user_edit | any
    surface: str

    def overlap(self, lo_utc: int, hi_utc: int) -> float:
        """1 inside the core, decaying linearly to 0 across the band."""
        a, b = self.lo.timestamp(), self.hi.timestamp()
        if hi_utc >= a and lo_utc <= b:
            return 1.0
        gap = (a - hi_utc) if hi_utc < a else (lo_utc - b)
        return max(0.0, 1.0 - gap / (self.band_days * 86400))


@dataclass
class QueryFrame:
    """How a query was read. Every field can be overridden from the UI or CLI (ADR-0013 §4)."""

    text: str
    words: list[str] = field(default_factory=list)
    kinds: tuple[str, ...] = ()
    kind_word: str = ""
    time: list[TimeCue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")[:2000]
    return " ".join(text.split())


def _month_range(year: int, month: int, part: str | None) -> tuple[date, date]:
    last = calendar.monthrange(year, month)[1]
    if part == "early":
        return date(year, month, 1), date(year, month, 10)
    if part in ("mid", "middle"):
        return date(year, month, 11), date(year, month, 20)
    if part == "late":
        return date(year, month, 21), date(year, month, last)
    return date(year, month, 1), date(year, month, last)


_TIME_RE = re.compile(
    r"\b(?:(early|mid|middle|late)[\s-]+)?("
    + "|".join(sorted(MONTHS, key=len, reverse=True))
    + r")\b(?:\s+(?:of\s+)?((?:19|20)\d{2}))?"
    r"|\b(last|this|past|previous)\s+(week|month|year|semester|sem)\b"
    r"|\b(\d{1,2}|a|one|two|three|four|five|six)\s+(day|week|month|year)s?\s+ago\b"
    r"|\b(yesterday|today|recently)\b"
    r"|\b(?:in|from|back in|around|during|of)\s+((?:19|20)\d{2})\b|\b((?:19|20)\d{2})\b",
    re.IGNORECASE,
)
_NUM = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def _event_hint(lowered: str) -> str:
    for event, verbs in EVENT_VERBS.items():
        if any(re.search(rf"\b{re.escape(v)}\b", lowered) for v in verbs):
            return event
    return "any"


def _cue(lo: date, hi: date, as_of: datetime, hint: str, surface: str) -> TimeCue:
    age_days = max(0.0, (as_of.date() - hi).days)
    band = 7.0 + 0.05 * age_days  # memory gets fuzzier with age
    def to_dt(d: date, end: bool = False) -> datetime:
        return datetime.combine(d, datetime.max.time() if end else datetime.min.time()).astimezone()

    return TimeCue(to_dt(lo), to_dt(hi, True), band, hint, surface)


def _parse_time(text: str, as_of: datetime, hint: str) -> list[TimeCue]:
    cues = []
    for m in _TIME_RE.finditer(text):
        try:
            if m.group(2):
                month = MONTHS[m.group(2).lower()]
                year = (
                    int(m.group(3))
                    if m.group(3)
                    else (as_of.year if month <= as_of.month else as_of.year - 1)
                )  # most recent past
                lo, hi = _month_range(year, month, (m.group(1) or "").lower() or None)
            elif m.group(4):
                unit = m.group(5).lower()
                if unit == "week":
                    start = as_of.date() - timedelta(
                        days=as_of.weekday() + (7 if m.group(4).lower() != "this" else 0)
                    )
                    lo, hi = start, start + timedelta(days=6)
                elif unit == "month":
                    y, mo = (as_of.year, as_of.month)
                    if m.group(4).lower() != "this":
                        y, mo = (y - 1, 12) if mo == 1 else (y, mo - 1)
                    lo, hi = _month_range(y, mo, None)
                elif unit in ("semester", "sem"):
                    lo, hi = as_of.date() - timedelta(days=300), as_of.date() - timedelta(days=60)
                else:
                    y = as_of.year - (0 if m.group(4).lower() == "this" else 1)
                    lo, hi = date(y, 1, 1), date(y, 12, 31)
            elif m.group(6):
                n = _NUM.get(m.group(6).lower()) or int(m.group(6))
                days = {"day": 1, "week": 7, "month": 30, "year": 365}[m.group(7).lower()] * n
                center = as_of.date() - timedelta(days=days)
                half = max(1, days // 4)
                lo, hi = center - timedelta(days=half), center + timedelta(days=half)
            elif m.group(8):
                w = m.group(8).lower()
                d = as_of.date() - timedelta(days=1 if w == "yesterday" else 0)
                lo, hi = (
                    (d, d) if w != "recently" else (as_of.date() - timedelta(days=30), as_of.date())
                )
            else:
                year = int(m.group(9) or m.group(10))
                if not 1990 <= year <= as_of.year:
                    continue
                lo, hi = date(year, 1, 1), date(year, 12, 31)
                if hint == "any" and m.group(9) is None:
                    pass
            cues.append(_cue(lo, hi, as_of, hint, m.group(0)))
        except (ValueError, KeyError):
            continue
    # Keep the most specific reading when phrases overlap ("March 2025" vs "2025").
    cues.sort(key=lambda c: (c.hi - c.lo))
    kept: list[TimeCue] = []
    for c in cues:
        if not any(k.lo >= c.lo and k.hi <= c.hi for k in kept):
            kept.append(c)
    return kept[:2]


def parse(text: str, as_of: datetime | None = None, overrides: dict | None = None) -> QueryFrame:
    """Read a query. ``as_of`` is injected (no wall clock) for reproducible evaluation."""
    as_of = as_of or datetime.now(timezone.utc).astimezone()
    norm = _normalise(text)
    frame = QueryFrame(text=norm)
    try:
        lowered = norm.lower()
        frame.words = re.findall(r"[^\W_]+", lowered)
        for word, kinds in FORMAT_LEXICON.items():
            if re.search(rf"\b{re.escape(word)}\b", lowered):
                frame.kinds, frame.kind_word = kinds, word
                break
        frame.time = _parse_time(lowered, as_of, _event_hint(lowered))
    except Exception as exc:  # noqa: BLE001 - understanding degrades, search never fails
        frame.notes.append(f"query parsing degraded: {type(exc).__name__}")
    for key, value in (overrides or {}).items():
        if key == "time" and value == "removed":
            frame.time = []
        elif key == "kind" and value == "removed":
            frame.kinds, frame.kind_word = (), ""
    return frame
