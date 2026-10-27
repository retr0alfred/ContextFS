"""Data model of the benchmark: personas, activities, file plans, and the sealed truth."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

__all__ = ["Persona", "Activity", "FilePlan", "TimeTruth", "DateMention"]

#: Template families. Dev personas use family "A", test personas family "B" (ADR-0015 §2),
#: so test content and query phrasings never share templates with what was tuned on.
FAMILIES = ("A", "B")


@dataclass
class Persona:
    """One simulated user."""

    persona_id: str
    archetype: str  # student | professional | researcher
    name: str
    seed: int
    family: str  # template family, "A" (dev) or "B" (test)
    start: datetime
    end: datetime  # the persona's "now": when the corpus is observed
    home_city: str
    org: str  # college / employer / lab


@dataclass
class Activity:
    """Something the persona did over a period: a course, a project, a trip, a job search."""

    activity_id: str
    kind: str  # course | assignment | exam_prep | hackathon | job_search | capstone | trip | ...
    title: str
    topic: str
    start: datetime
    end: datetime
    deadline: datetime | None = None
    people: list[str] = field(default_factory=list)
    place: str = ""
    keywords: list[str] = field(default_factory=list)
    folder: str = ""


@dataclass
class DateMention:
    """A date written inside a file, with its ground-truth label."""

    day: str  # ISO date
    surface: str  # how it is written in the file
    meaningful: bool
    why: str


@dataclass
class TimeTruth:
    """True per-event times of a file (ADR-0006 §2). Naive datetimes are local wall time."""

    authored: datetime  # when the content was first written (by anyone)
    arrived_here: datetime  # when the file first appeared on this machine
    last_user_edit: datetime | None  # last edit by THIS persona (None: never edited here)


@dataclass
class FilePlan:
    """One file the simulation will materialise."""

    rel_path: str
    kind: str  # txt | md | code | pdf | docx | pptx | xlsx | jpg | zip
    origin: str  # authored | downloaded | received | generated_noise
    activity_id: str | None
    truth: TimeTruth
    title: str = ""
    topic: str = ""
    keywords: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)  # named in the text
    dates: list[DateMention] = field(default_factory=list)
    content: Any = None  # format-specific payload, filled by the content provider
    infile_author: str = ""
    duplicate_of: str | None = None
    noise: bool = False
    damage: str = "none"  # filled by damage.apply

    def truth_record(self) -> dict[str, Any]:
        """The sealed ground-truth record for this file (never an engine input)."""
        record = asdict(self)
        record.pop("content", None)
        return record
