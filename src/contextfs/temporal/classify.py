r"""Meaningful vs. incidental date classification (Layer 7, core contribution).

The problem
-----------
A personal corpus is full of dates, and almost none of them matter. A
bibliography has a publication year per entry; a history essay has a date per
paragraph; an attendance sheet has one per row. Buried among them are the few
that are actually *commitments*: an exam on the 24th, a submission deadline, a
supervisor meeting. A timeline built from every extracted date is noise. A
timeline built from the commitments is the feature.

The scoring model
-----------------
Four signals, each normalised to ``[0, 1]``, combined by a weighted sum whose
weights are validated to total 1.0 (so the output really is on a 0-1 scale)::

    relevance = w_kw * S_keyword
              + w_st * S_structured
              + w_md * S_metadata
              + w_cf * S_crossfile

    default weights: 0.40, 0.25, 0.20, 0.15   (contextfs.toml, [temporal])

A date becomes a **timeline node** when ``relevance >= timeline_node_threshold``
(default 0.55, configurable - never a magic number in code).

**Neutral is 0.5, not 0.** This is the most important detail of the design and
it was found by working the arithmetic through by hand before writing the code.
If an absent signal contributed 0, then every signal would be evidence *against*
meaningfulness whenever it was silent. Concretely: a deadline written in prose
("Last date for submission: 31 December 2025") would be punished by the
structured-context signal for not being in a table, scoring 0.534 against a 0.55
threshold - a false negative on an unambiguous deadline. A date in a table is
evidence *for*; a date in prose is simply *no evidence either way*. So each
signal returns 0.5 when it has nothing to say, and moves up or down from there.

The four signals
----------------
**S1 - keyword proximity (0.40).** The strongest single signal. Searches a
window around the mention for three vocabularies: commitment words (*exam*,
*deadline*, *due*, *submission*, *viva*, *review*), past-record words
(*attendance*, *completed*, *logged*, *present*), and incidental words
(*born*, *published*, *founded*, *released*). Evidence decays with token
distance. Past-record and incidental vocabularies push the score *down*, which
is what separates an attendance spreadsheet from a timetable spreadsheet -
both are tables full of dates.

**S2 - structured context (0.25).** Whether the date sits inside a table,
spreadsheet or timetable. Computed in Phase 5/6 and read from the
``in_tabular`` column rather than re-parsed. Deliberately not sufficient on its
own: the corpus contains ``ml_lab_attendance.xlsx``, a table of purely
incidental dates, precisely so that "dates in tables are meaningful" cannot pass
as a rule.

**S3 - metadata consistency (0.20).** Distance between the mentioned date and
the document's own modification time. **Asymmetric on purpose**: people write
about deadlines *before* they happen, so a date shortly in the future of the
document is highly consistent, while a date in the past decays twice as fast.
A 1947 date in a 2025 file scores ~0.

**S4 - cross-file recurrence (0.15).** How many distinct files mention the same
date. A date the corpus keeps returning to is more likely a shared commitment.
Weighted lowest because a genuine one-off deadline (the scholarship form) must
still be able to clear the threshold on the other three signals alone.

The precision gate
------------------
Separate from the weighted sum, and applied afterwards: a mention with only
**year** precision ("published in 1998") is multiplied by
``year_only_penalty``. A bare year cannot be an actionable date - you cannot
attend an exam "in 1998". This is stated as its own rule rather than folded into
a signal because it is a categorical statement about the *kind* of mention, not
graded evidence about its context.

Explainability
--------------
Every verdict carries the individual signal values, the evidence that produced
them (which keywords matched, at what distance), and the arithmetic. The score
is reproducible by hand from the explanation, which is what makes it defensible
in a viva rather than a black box.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

__all__ = [
    "DateSignals",
    "DateVerdict",
    "DateClassifier",
    "MEANINGFUL_KEYWORDS",
    "PAST_RECORD_KEYWORDS",
    "INCIDENTAL_KEYWORDS",
]

#: Vocabulary indicating a commitment, mapped to evidence strength in [0, 1].
#: Strength reflects how unambiguously the word implies an obligation:
#: "deadline" is decisive, "schedule" is suggestive.
MEANINGFUL_KEYWORDS: dict[str, float] = {
    # decisive
    "deadline": 1.00,
    "due": 1.00,
    "exam": 1.00,
    "examination": 1.00,
    "viva": 1.00,
    "submission": 0.95,
    "submit": 0.90,
    "closes": 0.90,
    "closing": 0.90,
    "last date": 1.00,
    "expires": 0.90,
    # scheduled events
    "meeting": 0.85,
    "review": 0.85,
    "interview": 0.85,
    "presentation": 0.80,
    "defence": 0.90,
    "defense": 0.90,
    "test": 0.75,
    "quiz": 0.75,
    "appointment": 0.85,
    "scheduled": 0.80,
    "timetable": 0.85,
    "hall ticket": 0.80,
    # planning language
    "milestone": 0.75,
    "delivery": 0.70,
    "deliverable": 0.75,
    "target": 0.60,
    "reminder": 0.75,
    "by": 0.35,
    "before": 0.40,
    "on or before": 0.85,
    "no later than": 0.95,
    "starts": 0.55,
    "begins": 0.55,
    "ends": 0.55,
}

#: Vocabulary indicating a record of something already done. A log, not a plan.
#: This is what separates an attendance spreadsheet from a timetable.
PAST_RECORD_KEYWORDS: dict[str, float] = {
    "attendance": 0.90,
    "attended": 0.85,
    # NOTE: "present" is deliberately absent. It is polysemous in exactly the
    # place it matters: in an attendance sheet it is a status, but in meeting
    # notes "Present: Alfred, Abu, Dr. Murari" introduces the attendee list,
    # which is evidence the meeting *happened* - the opposite reading. It
    # caused both false negatives in the first evaluation run. Removing it
    # costs nothing, because "attendance"/"absent" already dominate the case
    # it was meant to catch (the maximum, not the sum, of evidence is used).
    "absent": 0.70,
    "completed": 0.80,
    "logged": 0.80,
    "recorded": 0.75,
    "history": 0.70,
    "log": 0.60,
    "reading": 0.50,
    "timestamp": 0.80,
    "applied on": 0.70,
    "marks": 0.55,
    "experiment": 0.50,
    "status": 0.35,
}

#: Vocabulary indicating a date that is merely referenced.
INCIDENTAL_KEYWORDS: dict[str, float] = {
    "born": 1.00,
    "birthday": 1.00,
    "birth": 0.95,
    "published": 0.90,
    "publication": 0.85,
    "founded": 0.90,
    "established": 0.85,
    "released": 0.80,
    "anniversary": 0.70,
    "century": 0.80,
    "historical": 0.85,
    "assassinated": 0.90,
    "independence": 0.75,
    "war": 0.60,
    "et al": 0.85,
    "film": 0.70,
    "movie": 0.70,
    "novel": 0.70,
    "author": 0.60,
}

#: Neutral value returned by a signal that has no evidence either way.
NEUTRAL = 0.5

#: Weight applied to keyword evidence found in a table's header row rather than
#: adjacent to the mention. Discounted below 1.0 because a header describes the
#: whole column, not the particular cell, so it is weaker evidence than the same
#: word sitting next to the date - but far from no evidence, which is what a
#: proximity-only window gives it.
HEADER_DISCOUNT = 0.7

_TOKEN = re.compile(r"[A-Za-z][A-Za-z'-]*|\d+")


@dataclass
class DateSignals:
    """The four signal values for one date mention, plus their evidence."""

    keyword: float = NEUTRAL
    structured: float = NEUTRAL
    metadata: float = NEUTRAL
    crossfile: float = NEUTRAL

    keyword_evidence: list[tuple[str, str, int, float]] = field(default_factory=list)
    structured_evidence: str = ""
    metadata_evidence: str = ""
    crossfile_evidence: str = ""

    def as_dict(self) -> dict[str, float]:
        """Signal values keyed by name, for the weighted sum."""
        return {
            "keyword_proximity": self.keyword,
            "structured_context": self.structured,
            "metadata_consistency": self.metadata,
            "cross_file_recurrence": self.crossfile,
        }


@dataclass
class DateVerdict:
    """A classified date mention and the complete reasoning behind it."""

    file_id: int
    rel_path: str
    iso_date: str
    surface: str
    char_start: int
    char_end: int
    precision: str
    score: float
    is_meaningful: bool
    signals: DateSignals
    weights: dict[str, float]
    threshold: float
    precision_penalty: float = 1.0
    year_inferred: bool = False

    def explain(self) -> dict[str, Any]:
        """Return a machine-readable, hand-checkable explanation of the score.

        The arithmetic is included so that a reader can reproduce the score
        without running the code - the difference between a defensible model and
        a black box.
        """
        contributions = {
            name: round(self.weights[name] * value, 4)
            for name, value in self.signals.as_dict().items()
        }
        return {
            "date": self.iso_date,
            "surface": self.surface,
            "file": self.rel_path,
            "verdict": "meaningful" if self.is_meaningful else "incidental",
            "score": round(self.score, 4),
            "threshold": self.threshold,
            "precision": self.precision,
            "year_inferred": self.year_inferred,
            "signals": {k: round(v, 4) for k, v in self.signals.as_dict().items()},
            "weights": self.weights,
            "contributions": contributions,
            "weighted_sum": round(sum(contributions.values()), 4),
            "precision_penalty": self.precision_penalty,
            "evidence": {
                "keyword": [
                    {"word": word, "kind": kind, "distance": distance, "strength": strength}
                    for word, kind, distance, strength in self.signals.keyword_evidence
                ],
                "structured": self.signals.structured_evidence,
                "metadata": self.signals.metadata_evidence,
                "cross_file": self.signals.crossfile_evidence,
            },
        }

    def reason(self) -> str:
        """A one-line human-readable justification."""
        parts = []
        for word, kind, distance, _ in self.signals.keyword_evidence[:3]:
            marker = {"meaningful": "+", "past_record": "-", "incidental": "-"}[kind]
            parts.append(f"{marker}{word}@{distance}")
        if self.signals.structured > NEUTRAL:
            parts.append("+in-table")
        if self.signals.metadata > 0.6:
            parts.append("+near-mtime")
        elif self.signals.metadata < 0.2:
            parts.append("-far-from-mtime")
        if self.signals.crossfile > NEUTRAL:
            parts.append("+recurs")
        if self.precision_penalty < 1.0:
            parts.append("-year-only")
        return f"{self.score:.2f} " + " ".join(parts)


class DateClassifier:
    """Scores date mentions as meaningful or incidental.

    The classifier is deliberately **rule-based and inspectable** rather than a
    trained model. Two reasons: there is no labelled training corpus of personal
    files to learn from (building one is the very problem this project has), and
    a learned scorer could not produce the per-signal explanation the system
    promises. The weights are exposed in configuration precisely so that a
    future project *can* fit them if a labelled corpus appears.
    """

    def __init__(self, config) -> None:
        """Bind the classifier to the ``[temporal]`` configuration section."""
        self.config = config
        temporal = config.temporal
        self.weights = temporal.weights
        self.threshold = temporal.timeline_node_threshold
        self.window = temporal.keyword_window_tokens
        self.consistency_days = temporal.metadata_consistency_window_days
        self.year_only_penalty = getattr(temporal, "year_only_penalty", 0.35)
        self.recurrence_saturation = getattr(temporal, "recurrence_saturation", 4)

    # -- signals -----------------------------------------------------------

    def keyword_signal(
        self, text: str, start: int, end: int, header: str = ""
    ) -> tuple[float, list]:
        """S1: search a token window around the mention for the three vocabularies.

        Evidence strength decays linearly with token distance, so a word next to
        the date counts far more than one at the edge of the window. Commitment
        vocabulary raises the score; past-record and incidental vocabulary lower
        it. The **best** evidence of each polarity is used rather than a sum, so
        a table repeating a status word ten times cannot out-vote one "deadline".

        Args:
            text: The document's full extracted text.
            start: Character offset where the date mention begins.
            end: Character offset where it ends.
            header: The header row of the containing table, if the mention is
                inside one. A column header governs every cell beneath it no
                matter how far down the sheet it appears, which a
                proximity-only window cannot see - this was the cause of the
                only false positive in the first evaluation run, where a
                "Timestamp" column header sat outside the window of the rows it
                labelled. Header evidence is discounted (see
                :data:`HEADER_DISCOUNT`) because a header describes a column
                rather than the individual cell.
        """
        window_chars = self.window * 8  # generous character proxy for the token window
        left = max(0, start - window_chars)
        right = min(len(text), end + window_chars)
        before, after = text[left:start].lower(), text[end:right].lower()
        header_text = header.lower()

        evidence: list[tuple[str, str, int, float]] = []
        best = {"meaningful": 0.0, "past_record": 0.0, "incidental": 0.0}

        for kind, vocabulary in (
            ("meaningful", MEANINGFUL_KEYWORDS),
            ("past_record", PAST_RECORD_KEYWORDS),
            ("incidental", INCIDENTAL_KEYWORDS),
        ):
            for word, strength in vocabulary.items():
                distance = self._nearest_token_distance(before, after, word)
                if distance is not None:
                    decay = max(0.0, 1.0 - distance / max(1, self.window))
                    value = strength * decay
                    if value > 0:
                        evidence.append((word, kind, distance, round(value, 3)))
                        best[kind] = max(best[kind], value)

                if header_text and word in header_text:
                    value = strength * HEADER_DISCOUNT
                    evidence.append((f"{word} (column header)", kind, 0, round(value, 3)))
                    best[kind] = max(best[kind], value)

        negative = max(best["past_record"], best["incidental"])
        score = NEUTRAL + 0.5 * best["meaningful"] - 0.5 * negative
        evidence.sort(key=lambda item: -item[3])
        return _clamp(score), evidence[:8]

    @staticmethod
    def _nearest_token_distance(before: str, after: str, phrase: str) -> int | None:
        """Token distance from the mention to the nearest occurrence of ``phrase``.

        Distances are counted in tokens rather than characters so that the
        window means the same thing in dense spreadsheet rows and in prose.
        """
        best: int | None = None

        if " " in phrase:
            position = before.rfind(phrase)
            if position >= 0:
                best = len(_TOKEN.findall(before[position + len(phrase) :]))
            position = after.find(phrase)
            if position >= 0:
                candidate = len(_TOKEN.findall(after[:position]))
                best = candidate if best is None else min(best, candidate)
            return best

        tokens_before = _TOKEN.findall(before)
        for offset, token in enumerate(reversed(tokens_before)):
            if token == phrase:
                best = offset
                break
        tokens_after = _TOKEN.findall(after)
        for offset, token in enumerate(tokens_after):
            if token == phrase:
                candidate = offset
                best = candidate if best is None else min(best, candidate)
                break
        return best

    def structured_signal(self, in_tabular: bool) -> tuple[float, str]:
        """S2: a date inside tabular structure is evidence *for* meaningfulness.

        Prose returns the neutral value, not zero. "Not in a table" is not
        evidence that a date is incidental - most real deadlines are written in
        sentences.
        """
        if in_tabular:
            return 1.0, "mention occurs inside a table or spreadsheet row"
        return NEUTRAL, "mention occurs in prose (no structural evidence either way)"

    def metadata_signal(self, iso_date: str, mtime: str) -> tuple[float, str]:
        """S3: consistency between the mentioned date and the document's mtime.

        Asymmetric by design. People write about a deadline *before* it falls
        due, so a date shortly after the document's timestamp is highly
        consistent with a commitment. A date long before it is far more likely a
        historical reference or a record of something already past, so the decay
        for past dates is twice as steep.
        """
        try:
            mentioned = date.fromisoformat(iso_date)
            modified = datetime.fromisoformat(mtime).date()
        except (ValueError, TypeError):
            return NEUTRAL, "document timestamp unavailable"

        delta = (mentioned - modified).days
        if delta >= 0:
            score = math.exp(-delta / max(1, self.consistency_days))
            direction = f"{delta} day(s) after the document was last modified"
        else:
            score = math.exp(-abs(delta) / max(1, self.consistency_days / 2))
            direction = f"{abs(delta)} day(s) before the document was last modified"
        return _clamp(score), direction

    def crossfile_signal(self, file_count: int) -> tuple[float, str]:
        """S4: how many distinct files mention this date.

        Saturating rather than linear: the difference between one file and three
        is meaningful, the difference between ten and twelve is not. A single
        occurrence returns the neutral value, not zero - a genuine one-off
        deadline must still be able to clear the threshold on other evidence.
        """
        if file_count <= 1:
            return NEUTRAL, "mentioned in only this file"
        score = NEUTRAL + 0.5 * min(1.0, (file_count - 1) / max(1, self.recurrence_saturation - 1))
        return _clamp(score), f"mentioned in {file_count} distinct files"

    # -- combination -------------------------------------------------------

    def classify(
        self,
        *,
        file_id: int,
        rel_path: str,
        text: str,
        iso_date: str,
        surface: str,
        char_start: int,
        char_end: int,
        precision: str,
        in_tabular: bool,
        mtime: str,
        file_count: int,
        year_inferred: bool = False,
        header: str = "",
    ) -> DateVerdict:
        """Score one date mention and return a fully explained verdict."""
        signals = DateSignals()
        signals.keyword, signals.keyword_evidence = self.keyword_signal(
            text, char_start, char_end, header
        )
        signals.structured, signals.structured_evidence = self.structured_signal(in_tabular)
        signals.metadata, signals.metadata_evidence = self.metadata_signal(iso_date, mtime)
        signals.crossfile, signals.crossfile_evidence = self.crossfile_signal(file_count)

        weighted = sum(self.weights[name] * value for name, value in signals.as_dict().items())

        # Precision gate: a bare year is not an actionable date in any useful
        # sense. Applied as a multiplier after the weighted sum, and reported
        # separately, because it is a categorical statement about the kind of
        # mention rather than graded evidence about its context.
        penalty = self.year_only_penalty if precision == "year" else 1.0
        score = _clamp(weighted * penalty)

        return DateVerdict(
            file_id=file_id,
            rel_path=rel_path,
            iso_date=iso_date,
            surface=surface,
            char_start=char_start,
            char_end=char_end,
            precision=precision,
            score=score,
            is_meaningful=score >= self.threshold,
            signals=signals,
            weights=dict(self.weights),
            threshold=self.threshold,
            precision_penalty=penalty,
            year_inferred=year_inferred,
        )

    def classify_store(self, store) -> list[DateVerdict]:
        """Classify every date mention in an index.

        Returns:
            One verdict per mention, in corpus order.
        """
        recurrence = store.date_recurrence()
        documents: dict[int, str] = {}
        blocks: dict[int, list] = {}
        files: dict[int, Any] = {row["id"]: row for row in store.all_files()}

        verdicts: list[DateVerdict] = []
        for row in store.get_date_mentions():
            if not row["iso_date"]:
                continue
            file_id = row["file_id"]
            if file_id not in documents:
                document = store.get_document(file_id)
                documents[file_id] = document["text"] if document else ""
                blocks[file_id] = store.get_blocks(file_id)
            file_row = files.get(file_id)
            if file_row is None:
                continue
            header = _header_for(blocks.get(file_id, []), row["char_start"])
            verdicts.append(
                self.classify(
                    file_id=file_id,
                    rel_path=row["path"],
                    text=documents[file_id],
                    iso_date=row["iso_date"],
                    surface=row["text"],
                    char_start=row["char_start"],
                    char_end=row["char_end"],
                    precision=row["precision"],
                    in_tabular=bool(row["in_tabular"]),
                    mtime=file_row["mtime"],
                    file_count=recurrence.get(row["iso_date"], 1),
                    year_inferred=bool(row["year_inferred"]),
                    header=header,
                )
            )
        return verdicts

    @staticmethod
    def collapse(verdicts: list[DateVerdict]) -> list[DateVerdict]:
        """Keep the best-scoring verdict per ``(file, date)`` pair.

        A date mentioned several times in one document is one fact, not several.
        The strongest-scoring mention wins, because a single occurrence next to
        the word "deadline" settles the question regardless of how many bare
        repetitions follow it in a table.
        """
        best: dict[tuple[int, str], DateVerdict] = {}
        for verdict in verdicts:
            key = (verdict.file_id, verdict.iso_date)
            if key not in best or verdict.score > best[key].score:
                best[key] = verdict
        return sorted(best.values(), key=lambda v: (v.rel_path, v.iso_date))


def _header_for(blocks: list, offset: int) -> str:
    """Return the structural heading governing the content at ``offset``.

    Documents carry context in their *structure*, not only in the words next to
    a date, and a proximity window is blind to it. Two cases, one principle:

    * **Table header.** A spreadsheet sheet or Markdown pipe table arrives as
      one block whose first lines are the sheet name and column headers. A
      ``Timestamp`` column governs every cell beneath it however far down the
      sheet they sit.
    * **Section heading.** A Markdown heading governs the section under it.
      ``## Supervisor meetings`` tells you that the dates below are meetings,
      even when the word "meeting" is two hundred characters away.

    Both were found by the same failure: the first evaluation run's remaining
    error in each direction was a date whose disambiguating word was present in
    the document but outside the token window.

    Returns:
        Heading text to be searched for keywords at a discount, or "".
    """
    chain: list[str] = []
    for block in blocks:
        if block["is_tabular"] and block["char_start"] <= offset < block["char_end"]:
            lines = block["text"].splitlines()
            # Spreadsheet blocks are prefixed with the sheet name, so the first
            # two lines together cover "sheet name" plus "column headers".
            return " ".join(lines[:2])
        if block["char_start"] > offset:
            break
        if block["is_heading"] and block["text"]:
            chain.append(block["text"].splitlines()[0])
    # The *chain* of headings, not just the nearest one. A date frequently sits
    # inside its own subheading ("## 16 January 2026, 11:00"), which says
    # nothing; the document heading above it ("# Supervisor meetings") is what
    # identifies the kind of date. Keeping only the nearest heading discards
    # exactly the level that carries the meaning.
    return " ".join(chain[-3:])


def _clamp(value: float) -> float:
    """Clamp a score into ``[0, 1]``."""
    return max(0.0, min(1.0, value))
