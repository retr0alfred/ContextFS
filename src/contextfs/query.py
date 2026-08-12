"""Layer 9a - query understanding and decomposition (Phase 14).

A memory query is not a bag of words. "the pdf I studied before my machine
learning exam" carries four separable claims: a **format** (pdf), an **activity**
(studying, before an exam), a **topic** (machine learning), and an implicit
**time** (whenever that exam was). Retrieval can only use those separately if
they are separated first.

Decomposition produces:

* ``topic_terms``  - content words for semantic matching
* ``entities``     - named entities, matched against the entity index
* ``date_range``   - a resolved interval, if the query names a time
* ``format_hint``  - a file-type constraint ("pdf", "slides", "spreadsheet")
* ``activity_cue`` - whether the query is asking about a *period of work*
  rather than about content

The activity cue is the component that makes q01 answerable. Phrases like
"before my exam", "during the hackathon", "while I was applying" describe *when
the user was doing something*, not what a document says - and that is precisely
the class of query pure semantic retrieval cannot serve.

No LLM is used. Decomposition is spaCy plus rules, so it is fast enough to run
per keystroke in the GUI and produces a structure the explanation can quote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from contextfs.temporal import DateRange, RangeResolutionError, resolve_best

__all__ = ["QueryDecomposition", "QueryDecomposer", "FORMAT_HINTS", "ACTIVITY_CUES"]

#: Words that constrain the *kind* of file, mapped to matching extensions.
#: A user saying "slides" is stating a hard-ish constraint, not a topic.
FORMAT_HINTS: dict[str, tuple[str, ...]] = {
    "pdf": (".pdf",),
    "slides": (".pptx",),
    "slide": (".pptx",),
    "deck": (".pptx",),
    "presentation": (".pptx",),
    "spreadsheet": (".xlsx", ".csv"),
    "sheet": (".xlsx", ".csv"),
    "excel": (".xlsx",),
    "document": (".docx", ".pdf"),
    "doc": (".docx",),
    "word": (".docx",),
    "note": (".md", ".txt"),
    "notes": (".md", ".txt"),
    "code": (".py", ".js", ".ts", ".java", ".c", ".cpp", ".sql", ".sh"),
    "script": (".py", ".sh", ".js"),
    "readme": (".md",),
}

#: Phrases indicating the query is about a period of activity rather than
#: about document content. Weighted by how strongly they imply it.
ACTIVITY_CUES: dict[str, float] = {
    "before my": 1.0,
    "before the": 0.8,
    "after my": 1.0,
    "after the": 0.8,
    "during": 1.0,
    "while i": 1.0,
    "while working": 1.0,
    "when i": 0.9,
    "i was working": 1.0,
    "worked on": 0.8,
    "working on": 0.8,
    "studied": 0.9,
    "studying": 0.9,
    "revising": 0.9,
    "revised": 0.9,
    "weekend": 0.8,
    "session": 0.7,
    "everything from": 0.9,
    "all the files from": 0.9,
    "at the same time": 0.9,
    "alongside": 0.7,
}

#: Substrings that look like a time expression, tried against the resolver.
_TEMPORAL_CANDIDATES = re.compile(
    r"(?:the\s+)?(?:first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th)\s+week\s+"
    r"(?:of\s+|in\s+)?[a-z]+(?:\s+\d{4})?"
    r"|(?:this|last|next|past|previous)\s+(?:week|month|year|fortnight)"
    r"|\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?"
    r"|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"(?:\s+\d{4})?\b"
    r"|\bq[1-4](?:\s+\d{4})?\b"
    r"|\b\d{4}-\d{1,2}-\d{1,2}\b"
    r"|\b\d{1,2}[-/.]\d{1,2}[-/.]\d{4}\b"
    r"|\b(?:19|20)\d{2}\b",
    re.IGNORECASE,
)

#: Query words that should never be treated as topic terms.
_QUERY_STOPWORDS = {
    "file",
    "files",
    "thing",
    "things",
    "stuff",
    "something",
    "anything",
    "document",
    "documents",
    "one",
    "ones",
    "kind",
    "sort",
    "bit",
}


@dataclass
class QueryDecomposition:
    """The structured reading of a natural-language query."""

    text: str
    topic_terms: list[str] = field(default_factory=list)
    entities: list[tuple[str, str]] = field(default_factory=list)
    date_range: DateRange | None = None
    date_expression: str = ""
    format_hint: tuple[str, ...] = ()
    format_word: str = ""
    activity_cue: float = 0.0
    activity_phrases: list[str] = field(default_factory=list)

    @property
    def has_temporal(self) -> bool:
        """Whether the query names a time."""
        return self.date_range is not None

    @property
    def has_activity(self) -> bool:
        """Whether the query asks about a period of work."""
        return self.activity_cue > 0.0

    @property
    def entity_names(self) -> list[str]:
        """Just the entity surface strings."""
        return [name for name, _ in self.entities]

    def as_dict(self) -> dict[str, Any]:
        """JSON-serialisable form, quoted verbatim in explanations."""
        return {
            "query": self.text,
            "topic_terms": self.topic_terms,
            "entities": [{"text": name, "category": kind} for name, kind in self.entities],
            "date_range": (
                {
                    "start": self.date_range.start.isoformat(),
                    "end": self.date_range.end.isoformat(),
                    "expression": self.date_expression,
                    "interpretation": self.date_range.interpretation,
                }
                if self.date_range
                else None
            ),
            "format_hint": {"word": self.format_word, "extensions": list(self.format_hint)},
            "activity_cue": round(self.activity_cue, 3),
            "activity_phrases": self.activity_phrases,
        }

    def describe(self) -> str:
        """One-line human summary of how the query was read."""
        parts = []
        if self.topic_terms:
            parts.append("topic=" + "/".join(self.topic_terms[:4]))
        if self.entities:
            parts.append("entities=" + "/".join(self.entity_names[:3]))
        if self.date_range:
            parts.append(f"time={self.date_range.start}..{self.date_range.end}")
        if self.format_hint:
            parts.append("format=" + "/".join(self.format_hint))
        if self.has_activity:
            parts.append(f"activity={self.activity_cue:.1f}")
        return "  ".join(parts) or "(no structure extracted)"


class QueryDecomposer:
    """Turns a natural-language query into a :class:`QueryDecomposition`."""

    def __init__(self, config, timeline_index=None) -> None:
        """Bind to configuration and, optionally, a timeline for disambiguation."""
        self.config = config
        self.timeline_index = timeline_index
        self._nlp = None

    @property
    def nlp(self):
        """The spaCy pipeline, loaded on first use."""
        if self._nlp is None:
            from contextfs.entities import load_spacy

            self._nlp = load_spacy(self.config.entities.spacy_model)
        return self._nlp

    def decompose(self, text: str, reference: date | None = None) -> QueryDecomposition:
        """Decompose a query. Never raises on malformed input."""
        result = QueryDecomposition(text=text)
        lowered = text.lower()

        result.format_word, result.format_hint = self._format(lowered)
        result.activity_cue, result.activity_phrases = self._activity(lowered)
        result.date_expression, result.date_range = self._temporal(text, reference)

        try:
            doc = self.nlp(text)
        except Exception:  # noqa: BLE001 - decomposition degrades, never fails
            result.topic_terms = self._fallback_terms(lowered)
            return result

        from contextfs.entities import ENTITY_LABEL_MAP, EntityExtractor

        seen: set[str] = set()
        for ent in doc.ents:
            category = ENTITY_LABEL_MAP.get(ent.label_)
            if category is None:
                continue
            normalised = EntityExtractor.normalise(ent.text)
            if len(normalised) < 2 or normalised.lower() in seen:
                continue
            seen.add(normalised.lower())
            result.entities.append((normalised, category))

        terms: list[str] = []
        consumed = result.date_expression.lower()
        for token in doc:
            if (
                token.is_stop
                or token.is_punct
                or token.pos_ not in {"NOUN", "PROPN", "ADJ", "VERB"}
            ):
                continue
            lemma = token.lemma_.lower()
            if len(lemma) < 3 or lemma in _QUERY_STOPWORDS:
                continue
            # A month name already consumed as the temporal component is not
            # also a topic; otherwise "September" would compete semantically
            # with the documents' actual subject matter.
            if consumed and lemma in consumed:
                continue
            if lemma == result.format_word:
                continue
            if lemma not in terms:
                terms.append(lemma)
        result.topic_terms = terms
        return result

    # -- components --------------------------------------------------------

    @staticmethod
    def _format(lowered: str) -> tuple[str, tuple[str, ...]]:
        """Detect a file-type constraint."""
        for word, extensions in FORMAT_HINTS.items():
            if re.search(rf"\b{re.escape(word)}\b", lowered):
                return word, extensions
        return "", ()

    @staticmethod
    def _activity(lowered: str) -> tuple[float, list[str]]:
        """Detect phrases implying the query is about a period of work."""
        found = [phrase for phrase in ACTIVITY_CUES if phrase in lowered]
        if not found:
            return 0.0, []
        return max(ACTIVITY_CUES[phrase] for phrase in found), sorted(found)

    def _temporal(self, text: str, reference: date | None) -> tuple[str, DateRange | None]:
        """Find and resolve a time expression inside the query.

        Candidates are tried longest-first so "third week of October" wins over
        the bare "October" nested inside it.
        """
        candidates = sorted(
            (match.group(0) for match in _TEMPORAL_CANDIDATES.finditer(text)),
            key=len,
            reverse=True,
        )
        for candidate in candidates:
            try:
                resolved = resolve_best(candidate, self.timeline_index, reference)
            except RangeResolutionError:
                continue
            return candidate, resolved
        return "", None

    @staticmethod
    def _fallback_terms(lowered: str) -> list[str]:
        """Terms extracted without spaCy, if the model fails to load."""
        return [
            word
            for word in re.findall(r"[a-z][a-z'-]{2,}", lowered)
            if word not in _QUERY_STOPWORDS
        ]
