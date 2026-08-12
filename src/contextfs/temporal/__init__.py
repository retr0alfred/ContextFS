"""Layer 7 - temporal intelligence.

Distinguishes dates that are **commitments** (exams, deadlines, meetings, review
dates) from dates that are merely **mentioned** (birth years, publication years,
historical events, attendance records).

This is the project's highest-novelty component. Most retrieval systems treat
every date identically; the claim here is that separating the two classes is
both possible from local signals alone and useful for retrieval.
"""

from contextfs.temporal.classify import (
    INCIDENTAL_KEYWORDS,
    MEANINGFUL_KEYWORDS,
    PAST_RECORD_KEYWORDS,
    DateClassifier,
    DateSignals,
    DateVerdict,
)
from contextfs.temporal.timeline import (
    DateRange,
    RangeResolutionError,
    TimelineIndex,
    TimelineNode,
    resolve_best,
    resolve_range,
    resolve_range_candidates,
)

__all__ = [
    "DateClassifier",
    "DateSignals",
    "DateVerdict",
    "MEANINGFUL_KEYWORDS",
    "PAST_RECORD_KEYWORDS",
    "INCIDENTAL_KEYWORDS",
    "DateRange",
    "RangeResolutionError",
    "TimelineIndex",
    "TimelineNode",
    "resolve_range",
    "resolve_range_candidates",
    "resolve_best",
]
