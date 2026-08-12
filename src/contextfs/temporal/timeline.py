"""The timeline index and natural-language date-range resolution (Phase 11).

Two pieces:

**Range resolution.** Turns what a user types ("March to April", "last week",
"the third week of October", "September") into a concrete ``(start, end)``
interval. Users do not remember ISO dates; the phrases they *do* use are a small
and enumerable set, so this is deliberately a rule-based resolver rather than a
model - it is fast, it is auditable, and every failure is a missing rule rather
than an inscrutable one.

**Interval index.** An interval tree over dates the Phase 10 classifier judged
*meaningful*. This is the project's "speed story": a range query touches only
the intervals that overlap it, rather than scanning every file. Note that the
speed claim is about asymptotics, not about this corpus - at 44 timeline nodes a
linear scan would also be instant, and the benchmark output says so.
"""

from __future__ import annotations

import calendar
import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

__all__ = [
    "DateRange",
    "TimelineNode",
    "TimelineIndex",
    "resolve_range",
    "RangeResolutionError",
]

MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

ORDINALS = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "fifth": 5,
    "5th": 5,
    "last": -1,
}


class RangeResolutionError(ValueError):
    """Raised when a range expression cannot be resolved to concrete dates."""


@dataclass(frozen=True)
class DateRange:
    """A resolved, inclusive date interval and how it was derived."""

    start: date
    end: date
    expression: str
    interpretation: str

    def contains(self, day: date) -> bool:
        """Whether a date falls inside this range."""
        return self.start <= day <= self.end

    @property
    def days(self) -> int:
        """Length of the range in days, inclusive."""
        return (self.end - self.start).days + 1

    def __str__(self) -> str:
        """Human-readable rendering."""
        return f"{self.start.isoformat()} .. {self.end.isoformat()} ({self.interpretation})"


def _month_range(year: int, month: int) -> tuple[date, date]:
    """First and last day of a month."""
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _pick_year(month: int, reference: date, explicit: int | None) -> int:
    """Choose a year for a bare month name.

    Picks whichever of (reference year - 1, reference year, + 1) puts the month
    closest to the reference date. A user typing "September" in November means
    the September two months ago, not one ten months away.
    """
    if explicit is not None:
        return explicit
    best_year, best_distance = reference.year, None
    for year in (reference.year - 1, reference.year, reference.year + 1):
        distance = abs((date(year, month, 15) - reference).days)
        if best_distance is None or distance < best_distance:
            best_year, best_distance = year, distance
    return best_year


def resolve_range(expression: str, reference: date | None = None) -> DateRange:
    """Resolve a natural-language date range.

    Supported forms (case-insensitive)::

        2025-11-24                    a single day
        2025-11-24 to 2025-12-01      explicit span
        24-11-2025                    day-first numeric
        November / Nov                a month, year inferred from context
        November 2025                 a month with an explicit year
        March to April                a span of months
        third week of October         an ordinal week within a month
        last week / this week         relative to the reference date
        last month / this month / next month
        2025                          a whole year
        Q1 2026                       a quarter

    Args:
        expression: What the user typed.
        reference: "Today" for relative expressions. Defaults to the current
            date; tests and the evaluation harness pass it explicitly so
            relative phrases are deterministic.

    Returns:
        A :class:`DateRange`.

    Raises:
        RangeResolutionError: If nothing matched, listing the supported forms.
    """
    today = reference or date.today()
    text = " ".join(expression.lower().strip().split())
    if not text:
        raise RangeResolutionError("empty date range")

    for resolver in (
        _resolve_relative,
        _resolve_ordinal_week,
        _resolve_quarter,
        _resolve_explicit_span,
        _resolve_month_span,
        _resolve_single_month,
        _resolve_single_date,
        _resolve_year,
    ):
        result = resolver(text, today, expression)
        if result is not None:
            return result

    raise RangeResolutionError(
        f"could not interpret {expression!r} as a date range.\n"
        "Try: '2025-11-24', 'November', 'November 2025', 'March to April', "
        "'third week of October', 'last week', 'Q1 2026', or '2025'."
    )


#: Phrases whose year is genuinely ambiguous and worth disambiguating against
#: the index. Explicit years, relative phrases and full dates are never ambiguous.
_YEARLESS = re.compile(
    r"(?:^|\s)(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?:\s|$)",
    re.IGNORECASE,
)


def resolve_range_candidates(
    expression: str, reference: date | None = None, years: int = 2
) -> list[DateRange]:
    """Return every plausible reading of a year-less expression, best guess first.

    "September" is ambiguous: it could be any September. :func:`resolve_range`
    picks the one nearest the reference date, which is the right default with no
    other information. When an index *is* available, :func:`resolve_best` uses
    this to pick the reading that actually contains files - see its docstring
    for why that matters.

    Args:
        expression: What the user typed.
        reference: "Today". Defaults to the current date.
        years: How many years either side of the reference to consider.

    Returns:
        Candidate ranges ordered by distance from the reference date. A single
        candidate when the expression carries an explicit year or is relative.
    """
    today = reference or date.today()
    primary = resolve_range(expression, today)

    has_explicit_year = re.search(r"\b(19|20)\d{2}\b", expression) is not None
    if has_explicit_year or not _YEARLESS.search(expression):
        return [primary]

    candidates: list[DateRange] = []
    for offset in sorted(range(-years, years + 1), key=abs):
        anchor = date(today.year + offset, today.month, min(today.day, 28))
        try:
            candidate = resolve_range(expression, anchor)
        except RangeResolutionError:
            continue
        if all(candidate.start != existing.start for existing in candidates):
            candidates.append(candidate)
    return candidates or [primary]


def resolve_best(
    expression: str, index: TimelineIndex | None = None, reference: date | None = None
) -> DateRange:
    """Resolve a range, preferring a reading that actually contains files.

    **Why this exists.** Re-finding is backward-looking: a user asking about
    "September" is almost always thinking of a September that has happened, not
    the one approaching. Resolving purely by nearness to today produces a
    confidently empty answer - on this corpus, asking for "September" in August
    2026 resolves to September *2026* and returns nothing, while the files the
    user wants sit in September 2025.

    Choosing the candidate year with the most timeline nodes fixes that using
    evidence rather than a hardcoded "prefer the past" rule, and the chosen
    interpretation is reported to the user so the inference is visible rather
    than magical. Ties break toward the reading nearest the reference date.

    Args:
        expression: What the user typed.
        index: The timeline index to consult. Without one this degrades to
            :func:`resolve_range`.
        reference: "Today". Defaults to the current date.

    Returns:
        The chosen :class:`DateRange`, with ``interpretation`` annotated when
        the index was used to disambiguate.
    """
    candidates = resolve_range_candidates(expression, reference)
    if index is None or len(candidates) == 1:
        return candidates[0]

    best = None
    best_count = -1
    for candidate in candidates:  # already ordered by nearness, so ties keep the nearest
        count = len(index.query(candidate))
        if count > best_count:
            best, best_count = candidate, count

    if best is None or best_count <= 0:
        return candidates[0]
    if best.start == candidates[0].start:
        return best
    return DateRange(
        best.start,
        best.end,
        best.expression,
        f"{best.interpretation} - chosen over {candidates[0].interpretation} "
        f"because it is where your files are ({best_count} dated file(s))",
    )


def _resolve_relative(text: str, today: date, original: str) -> DateRange | None:
    """this/last/next week, month, year."""
    match = re.fullmatch(r"(this|last|next|past|previous)\s+(week|month|year|fortnight)", text)
    if not match:
        if text in {"today", "yesterday", "tomorrow"}:
            offset = {"today": 0, "yesterday": -1, "tomorrow": 1}[text]
            day = today + timedelta(days=offset)
            return DateRange(day, day, original, text)
        return None

    which, unit = match.group(1), match.group(2)
    direction = {"this": 0, "last": -1, "past": -1, "previous": -1, "next": 1}[which]

    if unit in {"week", "fortnight"}:
        span = 14 if unit == "fortnight" else 7
        start_of_week = today - timedelta(days=today.weekday())
        start = start_of_week + timedelta(days=direction * span)
        return DateRange(start, start + timedelta(days=span - 1), original, f"{which} {unit}")

    if unit == "month":
        month = today.month + direction
        year = today.year
        if month < 1:
            month, year = 12, year - 1
        elif month > 12:
            month, year = 1, year + 1
        start, end = _month_range(year, month)
        return DateRange(start, end, original, f"{which} month ({calendar.month_name[month]})")

    year = today.year + direction
    return DateRange(date(year, 1, 1), date(year, 12, 31), original, f"{which} year ({year})")


def _resolve_ordinal_week(text: str, today: date, original: str) -> DateRange | None:
    """Ordinal weeks: "third week of October", "last week of March 2026"."""
    match = re.fullmatch(
        r"(?:the\s+)?(first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th)\s+week\s+"
        r"(?:of\s+|in\s+)?([a-z]+)(?:\s+(\d{4}))?",
        text,
    )
    if not match:
        return None
    ordinal = ORDINALS[match.group(1)]
    month_name = match.group(2)
    if month_name not in MONTHS:
        return None
    month = MONTHS[month_name]
    year = _pick_year(month, today, int(match.group(3)) if match.group(3) else None)

    first, last = _month_range(year, month)
    if ordinal == -1:
        start = last - timedelta(days=6)
        return DateRange(start, last, original, f"last week of {calendar.month_name[month]} {year}")
    # Weeks counted from the 1st in blocks of seven - the way people actually
    # mean it ("the third week of October" = the 15th to the 21st), not ISO
    # week numbering, which would put the boundary on an arbitrary weekday.
    start = first + timedelta(days=7 * (ordinal - 1))
    end = min(start + timedelta(days=6), last)
    if start > last:
        raise RangeResolutionError(f"{calendar.month_name[month]} {year} has no week {ordinal}")
    return DateRange(start, end, original, f"week {ordinal} of {calendar.month_name[month]} {year}")


def _resolve_quarter(text: str, today: date, original: str) -> DateRange | None:
    """Quarters: "Q1", "Q3 2026"."""
    match = re.fullmatch(r"q([1-4])(?:\s+(\d{4}))?", text)
    if not match:
        return None
    quarter = int(match.group(1))
    year = int(match.group(2)) if match.group(2) else today.year
    start_month = 3 * (quarter - 1) + 1
    start, _ = _month_range(year, start_month)
    _, end = _month_range(year, start_month + 2)
    return DateRange(start, end, original, f"Q{quarter} {year}")


_DATE_PATTERNS = (
    (re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"), "ymd"),
    (re.compile(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})"), "dmy"),
)


def _parse_day(text: str) -> date | None:
    """Parse a single explicit date, day-first for numeric forms."""
    for pattern, order in _DATE_PATTERNS:
        match = pattern.fullmatch(text.strip())
        if not match:
            continue
        try:
            if order == "ymd":
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            return None

    match = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\s+(\d{4})", text.strip())
    if match and match.group(2) in MONTHS:
        try:
            return date(int(match.group(3)), MONTHS[match.group(2)], int(match.group(1)))
        except ValueError:
            return None
    return None


_SPAN_SPLIT = re.compile(r"\s+(?:to|until|till|through|-|–|—)\s+")


def _resolve_explicit_span(text: str, today: date, original: str) -> DateRange | None:
    """Explicit spans: "2025-11-24 to 2025-12-01"."""
    parts = _SPAN_SPLIT.split(text)
    if len(parts) != 2:
        return None
    start, end = _parse_day(parts[0]), _parse_day(parts[1])
    if start and end:
        if start > end:
            start, end = end, start
        return DateRange(start, end, original, "explicit span")
    return None


def _resolve_month_span(text: str, today: date, original: str) -> DateRange | None:
    """Month spans: "March to April", "March 2026 to May 2026"."""
    parts = _SPAN_SPLIT.split(text)
    if len(parts) != 2:
        return None

    def month_of(fragment: str) -> tuple[int, int | None] | None:
        match = re.fullmatch(r"([a-z]+)(?:\s+(\d{4}))?", fragment.strip())
        if not match or match.group(1) not in MONTHS:
            return None
        return MONTHS[match.group(1)], int(match.group(2)) if match.group(2) else None

    left, right = month_of(parts[0]), month_of(parts[1])
    if not left or not right:
        return None

    left_month, left_year = left
    right_month, right_year = right
    left_year = _pick_year(left_month, today, left_year)
    right_year = right_year if right_year is not None else left_year
    if (right_year, right_month) < (left_year, left_month):
        right_year += 1

    start, _ = _month_range(left_year, left_month)
    _, end = _month_range(right_year, right_month)
    return DateRange(
        start,
        end,
        original,
        f"{calendar.month_name[left_month]} {left_year} to "
        f"{calendar.month_name[right_month]} {right_year}",
    )


def _resolve_single_month(text: str, today: date, original: str) -> DateRange | None:
    """Single months: "November", "November 2025"."""
    match = re.fullmatch(r"(?:in\s+)?([a-z]+)(?:\s+(\d{4}))?", text)
    if not match or match.group(1) not in MONTHS:
        return None
    month = MONTHS[match.group(1)]
    year = _pick_year(month, today, int(match.group(2)) if match.group(2) else None)
    start, end = _month_range(year, month)
    return DateRange(start, end, original, f"{calendar.month_name[month]} {year}")


def _resolve_single_date(text: str, today: date, original: str) -> DateRange | None:
    """A single explicit day."""
    day = _parse_day(text)
    return DateRange(day, day, original, "single day") if day else None


def _resolve_year(text: str, today: date, original: str) -> DateRange | None:
    """A bare four-digit year."""
    match = re.fullmatch(r"(\d{4})", text)
    if not match:
        return None
    year = int(match.group(1))
    if not 1900 <= year <= 2199:
        return None
    return DateRange(date(year, 1, 1), date(year, 12, 31), original, f"the year {year}")


@dataclass
class TimelineNode:
    """A meaningful date attached to a file."""

    file_id: int
    rel_path: str
    day: date
    surface: str
    score: float
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "file_id": self.file_id,
            "path": self.rel_path,
            "date": self.day.isoformat(),
            "surface": self.surface,
            "score": round(self.score, 4),
            "reason": self.reason,
        }


@dataclass
class TimelineIndex:
    """Interval tree over meaningful dates, supporting fast range queries."""

    nodes: list[TimelineNode] = field(default_factory=list)
    _tree: Any = None

    @classmethod
    def from_store(cls, store) -> TimelineIndex:
        """Build the index from dates the classifier judged meaningful.

        Only meaningful dates enter the timeline. That is the entire point of
        Phase 10: an index over every extracted date would be dominated by
        publication years and attendance rows.
        """
        nodes = [
            TimelineNode(
                file_id=row["file_id"],
                rel_path=row["path"],
                day=date.fromisoformat(row["iso_date"]),
                surface=row["surface"],
                score=row["score"],
                reason=row["reason"],
            )
            for row in store.meaningful_dates()
        ]
        return cls.build(nodes)

    @classmethod
    def build(cls, nodes: list[TimelineNode]) -> TimelineIndex:
        """Build an interval tree over the given nodes."""
        from intervaltree import IntervalTree

        tree = IntervalTree()
        for index, node in enumerate(nodes):
            ordinal = node.day.toordinal()
            # Intervals are half-open in intervaltree, so a single day spans
            # [ordinal, ordinal + 1) to remain queryable.
            tree.addi(ordinal, ordinal + 1, index)
        return cls(nodes=nodes, _tree=tree)

    def query(self, date_range: DateRange) -> list[TimelineNode]:
        """Return the nodes whose date falls inside ``date_range``.

        Sorted by date, then by descending relevance score, so the caller sees
        chronological order with the strongest evidence first within a day.
        """
        if self._tree is None or not self.nodes:
            return []
        start = date_range.start.toordinal()
        end = date_range.end.toordinal() + 1
        hits = {interval.data for interval in self._tree.overlap(start, end)}
        return sorted(
            (self.nodes[index] for index in hits),
            key=lambda node: (node.day, -node.score),
        )

    def files_in_range(self, date_range: DateRange) -> dict[int, list[TimelineNode]]:
        """Group the nodes in a range by file."""
        grouped: dict[int, list[TimelineNode]] = {}
        for node in self.query(date_range):
            grouped.setdefault(node.file_id, []).append(node)
        return grouped

    def span(self) -> tuple[date, date] | None:
        """Earliest and latest meaningful date, or None if the index is empty."""
        if not self.nodes:
            return None
        days = [node.day for node in self.nodes]
        return min(days), max(days)

    def stats(self) -> dict[str, Any]:
        """Descriptive statistics for the ``stats`` command."""
        span = self.span()
        return {
            "timeline_nodes": len(self.nodes),
            "distinct_dates": len({node.day for node in self.nodes}),
            "distinct_files": len({node.file_id for node in self.nodes}),
            "earliest": span[0].isoformat() if span else None,
            "latest": span[1].isoformat() if span else None,
        }

    def benchmark(self, date_range: DateRange, repeats: int = 200) -> dict[str, float]:
        """Measure query latency over ``repeats`` runs.

        Reports the median, so a single scheduling hiccup does not become the
        headline number.
        """
        import statistics

        samples = []
        for _ in range(repeats):
            started = time.perf_counter()
            self.query(date_range)
            samples.append((time.perf_counter() - started) * 1000)
        return {
            "median_ms": statistics.median(samples),
            "min_ms": min(samples),
            "max_ms": max(samples),
            "repeats": repeats,
            "nodes": len(self.nodes),
        }
