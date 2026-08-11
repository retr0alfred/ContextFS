"""Layer 3 - named entity and keyword extraction.

Runs a spaCy pipeline over extracted document text and records people,
organisations, locations, keywords, and **raw date mentions**.

Date mentions are deliberately *not* classified here. Phase 10 decides whether a
date is meaningful or incidental, and it needs signals this layer cannot see:
the document's mtime, whether the date sits inside a table, and whether the same
date recurs across files. Splitting detection from classification also means the
Phase 10 classifier can be evaluated against a fixed set of detected mentions,
so a change in the classifier does not silently change what it is classifying.

Model loading
-------------
The spaCy model is loaded once and cached per model name. On the target hardware
loading ``en_core_web_sm`` costs roughly a second, which would be paid per
document without the cache.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

__all__ = [
    "EntityMention",
    "DateMention",
    "DocumentEntities",
    "EntityExtractor",
    "load_spacy",
    "prepare_for_ner",
    "consensus_categories",
    "apply_consensus",
    "build_gazetteer",
    "propagate_gazetteer",
    "is_bare_acronym",
    "ENTITY_LABEL_MAP",
    "DATE_PATTERNS",
]

#: spaCy entity labels mapped onto ContextFS's four categories. Labels not
#: listed here (CARDINAL, ORDINAL, PERCENT, MONEY, ...) are discarded: they add
#: noise to entity-overlap edges without helping anyone re-find a file.
ENTITY_LABEL_MAP = {
    "PERSON": "person",
    "ORG": "org",
    "GPE": "location",
    "LOC": "location",
    "FAC": "location",
    "NORP": "org",
    "EVENT": "event",
    "PRODUCT": "product",
    "WORK_OF_ART": "work",
}

#: Regular expressions for date surface forms, applied *in addition* to spaCy's
#: DATE entities. spaCy's statistical NER reliably misses bare numeric dates in
#: spreadsheet rows - exactly where this corpus's most important dates live -
#: because a table row carries none of the sentence context the model relies on.
#: Losing those would make the temporal layer untestable on its primary case.
DATE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # 24-11-2025, 24/11/2025, 24.11.2025
    ("dmy_numeric", re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b")),
    # 2025-11-24
    ("iso", re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")),
    # 24 November 2025 / 24 Nov 2025 / 24 Nov
    (
        "dmy_written",
        re.compile(
            r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
            r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
            r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
            r"Nov(?:ember)?|Dec(?:ember)?)"
            r"(?:\s+(\d{4}))?\b",
            re.IGNORECASE,
        ),
    ),
    # November 24, 2025 / Nov 24 2025
    (
        "mdy_written",
        re.compile(
            r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
            r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
            r"Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2})(?:,?\s+(\d{4}))?\b",
            re.IGNORECASE,
        ),
    ),
    # A bare four-digit year, 1900-2099. Kept because bibliographies and
    # historical prose - the corpus's main sources of *incidental* dates -
    # express dates this way, and the classifier must be given the chance to
    # reject them rather than never seeing them.
    ("year_only", re.compile(r"\b(19\d{2}|20\d{2})\b")),
]

#: Part-of-speech tags a keyword may have.
_KEYWORD_POS = {"NOUN", "PROPN"}

#: Tokens excluded from keywords regardless of part of speech.
_KEYWORD_STOPWORDS = {
    "thing",
    "things",
    "stuff",
    "note",
    "notes",
    "item",
    "items",
    "page",
    "file",
    "files",
    "day",
    "days",
    "time",
    "times",
    "year",
    "years",
    "week",
    "month",
    "lot",
    "bit",
    "part",
    "way",
    "kind",
    "sort",
    "number",
    "e.g",
    "i.e",
}


#: Markup that must be neutralised before NER. See :func:`prepare_for_ner`.
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")
_MD_BULLET = re.compile(r"^\s*[-*+]\s+(?![-*+])")
_MD_NUMBERED = re.compile(r"^\s*\d+[.)]\s+")
_MD_EMPHASIS = re.compile(r"(\*\*|__|\*|_|`)")
_MD_RULE = re.compile(r"^\s*[-=*_]{3,}\s*$")
_CHECKBOX = re.compile(r"^\s*\[[ xX]\]\s*")
_TRAILING_PUNCT = ".!?:;"


def prepare_for_ner(text: str) -> str:
    """Neutralise document markup so spaCy's sentence segmentation works.

    spaCy's statistical models are trained on running prose. Fed raw Markdown,
    spreadsheet rows, or checklist lines, they lose sentence boundaries and
    produce badly-typed, badly-bounded entities. The concrete failure that
    motivated this function: the line ``## Zoho`` followed by ``Chennai/Tenkasi.``
    was read as a *single* span ``Zoho Chennai/Tenkasi`` and typed ``PERSON``.

    The transformation is purely structural - no word is added, removed or
    reordered, so character content is preserved even though offsets shift:

    * heading markers are stripped and a full stop is appended if the heading
      does not already end in terminal punctuation, so a heading becomes its own
      sentence rather than merging with the line beneath it;
    * bullet, numbering and checkbox markers are stripped, and the same
      sentence-terminator rule applied;
    * table pipe separators become full stops, so each cell is its own
      sentence - this is what lets NER work at all on spreadsheet rows;
    * emphasis markers and horizontal rules are removed.

    Args:
        text: Extracted document text.

    Returns:
        Text suitable for a prose-trained NER model.

    Note:
        Character offsets in the returned text do **not** align with the input.
        Date mentions are therefore matched against the *original* text, not
        this one, so that stored offsets index the same string as the extracted
        blocks. Only NER consumes this transformation.
    """
    lines: list[str] = []
    for raw in text.splitlines():
        if _MD_RULE.match(raw):
            continue
        line = raw
        heading = _MD_HEADING.match(line)
        if heading:
            line = heading.group(1)
        line = _CHECKBOX.sub("", line)
        line = _MD_BULLET.sub("", line)
        line = _MD_NUMBERED.sub("", line)
        line = _MD_EMPHASIS.sub("", line)
        if "|" in line:
            # Table row: each cell becomes its own sentence.
            cells = [cell.strip(" -") for cell in line.split("|")]
            cells = [cell for cell in cells if cell]
            if cells:
                line = " ".join(
                    cell if cell[-1] in _TRAILING_PUNCT else cell + "." for cell in cells
                )
            else:
                continue
        line = line.strip()
        if not line:
            lines.append("")
            continue
        if (heading or raw.strip() != line) and line[-1] not in _TRAILING_PUNCT:
            line += "."
        lines.append(line)
    return "\n".join(lines)


@dataclass(frozen=True)
class EntityMention:
    """One occurrence of a named entity in a document."""

    text: str
    normalised: str
    category: str
    spacy_label: str
    start: int
    end: int

    @property
    def key(self) -> str:
        """Category-qualified identity, used for entity-overlap edges."""
        return f"{self.category}:{self.normalised}"


@dataclass(frozen=True)
class DateMention:
    """A raw date mention. Classification happens in Phase 10, not here.

    Attributes:
        text: The surface form as it appears in the document.
        start: Character offset into the document's flattened text.
        end: Exclusive end offset.
        source: ``"spacy"`` or the name of the regex pattern that matched.
        iso: Resolved ``YYYY-MM-DD`` if the mention is unambiguous, else None.
        precision: ``"day"``, ``"month"``, or ``"year"`` - how specific the
            mention is. A bare year cannot be a deadline in any useful sense,
            and Phase 10 uses this to say so.
        year_inferred: True when the document wrote no year ("24 Nov") and the
            year was inferred from the document's own timestamp. Phase 10 can
            discount such mentions, since the inference could be wrong.
    """

    text: str
    start: int
    end: int
    source: str
    iso: str | None = None
    precision: str = "day"
    year_inferred: bool = False


@dataclass
class DocumentEntities:
    """Everything Layer 3 knows about one document."""

    rel_path: str
    entities: list[EntityMention] = field(default_factory=list)
    dates: list[DateMention] = field(default_factory=list)
    keywords: list[tuple[str, int]] = field(default_factory=list)
    error: str = ""
    duration_ms: float = 0.0

    def by_category(self, category: str) -> list[str]:
        """Distinct normalised entity strings in one category, in first-seen order."""
        seen: dict[str, None] = {}
        for mention in self.entities:
            if mention.category == category:
                seen.setdefault(mention.normalised, None)
        return list(seen)

    @property
    def people(self) -> list[str]:
        """Distinct people mentioned."""
        return self.by_category("person")

    @property
    def orgs(self) -> list[str]:
        """Distinct organisations mentioned."""
        return self.by_category("org")

    @property
    def locations(self) -> list[str]:
        """Distinct locations mentioned."""
        return self.by_category("location")

    @property
    def entity_keys(self) -> set[str]:
        """Category-qualified keys, for computing entity overlap between files."""
        return {m.key for m in self.entities}

    @property
    def keyword_terms(self) -> list[str]:
        """Keyword strings without their counts."""
        return [term for term, _ in self.keywords]

    def summary(self) -> dict[str, Any]:
        """A flat, printable summary."""
        return {
            "path": self.rel_path,
            "people": len(self.people),
            "orgs": len(self.orgs),
            "locations": len(self.locations),
            "entities_total": len(self.entities),
            "dates": len(self.dates),
            "keywords": len(self.keywords),
            "duration_ms": round(self.duration_ms, 2),
            "error": self.error,
        }


def is_bare_acronym(surface: str) -> bool:
    """Whether a surface form is a short all-capitals token with no lowercase.

    Used to suppress a specific, measured failure mode: on technical personal
    corpora, spaCy types domain acronyms (``API``, ``SQL``, ``DBMS``, ``FYP``,
    ``CS``) as ``ORG``. On the gold set those five accounted for the majority of
    organisation false positives.

    **The trade-off is real and is not hidden:** this rule also discards
    genuinely short organisation acronyms such as IBM, BBC or NASA. It is
    therefore exposed as the config flag ``entities.drop_acronym_orgs`` rather
    than hardcoded, so a corpus where short acronyms *are* organisations can
    switch it off. The default is on because acronym-as-concept is far more
    common than acronym-as-organisation in student and technical corpora, and
    such tokens remain retrievable through the keyword layer regardless.
    """
    return len(surface) <= 5 and surface.isupper() and surface.isalpha()


def build_gazetteer(results: list[DocumentEntities], *, min_length: int = 4) -> dict[str, str]:
    """Derive a corpus-level entity gazetteer from confident detections.

    Statistical NER reads an entity's type from its sentence context. Documents
    that are mostly headings, bullet lists, or spreadsheet cells supply almost no
    context, so entities that are recognised perfectly well in prose are missed
    entirely a few files away. Measured instance: "Zoho", "Freshworks",
    "Postman" and "Chargebee" are recognised in a cover letter and an
    application tracker, and missed in a notes file written as Markdown
    headings.

    Propagating confident detections back across the corpus fixes that, and does
    so using only evidence the corpus already contains - no external word list,
    no hand-written rules about which companies exist.

    Only terms containing a lowercase letter are propagated. Propagating an
    all-capitals token would broadcast the acronym false positives described in
    :func:`is_bare_acronym` to every document that mentions them, converting a
    local precision problem into a corpus-wide one.

    Args:
        results: Per-document extraction results across the corpus.
        min_length: Shortest term eligible for propagation.

    Returns:
        ``{surface: category}`` for terms worth searching for everywhere.
    """
    decisions = consensus_categories(results)
    gazetteer: dict[str, str] = {}
    for result in results:
        for mention in result.entities:
            surface = mention.normalised
            if len(surface) < min_length:
                continue
            if not any(char.islower() for char in surface):
                continue
            gazetteer[surface] = decisions.get(surface, mention.category)
    return gazetteer


def propagate_gazetteer(result: DocumentEntities, text: str, gazetteer: dict[str, str]) -> int:
    """Add mentions of known corpus entities that NER missed in this document.

    Matching is case-sensitive and word-boundary anchored. Case sensitivity is
    deliberate: lowercasing would match "postman" in "the postman delivered" as
    the company Postman.

    Args:
        result: The document's extraction result, modified in place.
        text: The document's original text.
        gazetteer: ``{surface: category}`` from :func:`build_gazetteer`.

    Returns:
        The number of mentions added.
    """
    existing = {(m.normalised, m.start) for m in result.entities}
    covered = [(m.start, m.end) for m in result.entities if m.start >= 0]
    added = 0

    for surface, category in gazetteer.items():
        pattern = re.compile(rf"(?<![\w-]){re.escape(surface)}(?![\w-])")
        for match in pattern.finditer(text):
            start, end = match.start(), match.end()
            if (surface, start) in existing:
                continue
            if any(s <= start < e for s, e in covered):
                continue
            result.entities.append(
                EntityMention(
                    text=surface,
                    normalised=surface,
                    category=category,
                    spacy_label="GAZETTEER",
                    start=start,
                    end=end,
                )
            )
            covered.append((start, end))
            added += 1

    result.entities.sort(key=lambda m: (m.start if m.start >= 0 else 1 << 30))
    return added


def consensus_categories(results: list[DocumentEntities]) -> dict[str, str]:
    """Decide one category per entity string from corpus-wide agreement.

    In-memory twin of :meth:`contextfs.store.Store.reconcile_entity_categories`,
    used by evaluation scripts that run without a database. Same rule: the
    category supported by the strictly greatest number of distinct documents
    wins; ties change nothing.

    Args:
        results: Per-document extraction results across the corpus.

    Returns:
        ``{normalised: category}`` for entities whose category should be
        overridden. Entities with no disagreement are omitted.
    """
    votes: dict[str, Counter[str]] = {}
    for result in results:
        seen: set[tuple[str, str]] = set()
        for mention in result.entities:
            pair = (mention.normalised, mention.category)
            if pair in seen:
                continue
            seen.add(pair)
            votes.setdefault(mention.normalised, Counter())[mention.category] += 1

    decisions: dict[str, str] = {}
    for normalised, tally in votes.items():
        if len(tally) < 2:
            continue
        ranked = tally.most_common(2)
        if ranked[0][1] == ranked[1][1]:
            continue
        decisions[normalised] = ranked[0][0]
    return decisions


def apply_consensus(results: list[DocumentEntities], decisions: dict[str, str]) -> int:
    """Rewrite entity categories in place according to ``decisions``.

    Returns:
        The number of mentions whose category changed.
    """
    changed = 0
    for result in results:
        updated: list[EntityMention] = []
        for mention in result.entities:
            winner = decisions.get(mention.normalised)
            if winner and winner != mention.category:
                changed += 1
                updated.append(
                    EntityMention(
                        text=mention.text,
                        normalised=mention.normalised,
                        category=winner,
                        spacy_label=mention.spacy_label,
                        start=mention.start,
                        end=mention.end,
                    )
                )
            else:
                updated.append(mention)
        result.entities = updated
    return changed


@lru_cache(maxsize=4)
def load_spacy(model_name: str = "en_core_web_md"):
    """Load and cache a spaCy pipeline.

    Raises:
        RuntimeError: If the model is not installed, with the exact command
            needed to install it. A bare ``OSError`` from spaCy here is one of
            the most common first-run failures, and an actionable message is
            worth more than a stack trace.
    """
    import spacy

    try:
        return spacy.load(model_name)
    except OSError as exc:
        raise RuntimeError(
            f"spaCy model {model_name!r} is not installed.\n"
            f"Install it with:  python -m spacy download {model_name}"
        ) from exc


_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _infer_year(month: int, day: int, reference) -> str | None:
    """Resolve a year-less date such as "24 Nov" against a reference date.

    People routinely omit the year in personal documents ("exam on 24 Nov"),
    and those are frequently the *most* actionable dates in a corpus. Dropping
    them would lose real deadlines and, worse, would break the cross-file
    recurrence signal: a date written "24-11-2025" in a timetable and "24 Nov"
    in a checklist would look like two unrelated facts.

    The reference is the document's own modification time. Of the three
    candidate years (reference year minus one, the reference year, and plus
    one), the one whose resulting date is closest to the reference wins. This
    encodes the assumption that a document mentions dates near its own time -
    true for deadlines, meetings and exams, and the only assumption available
    without reading the author's mind.

    Args:
        month: Month number.
        day: Day of month.
        reference: A ``date``/``datetime`` anchor, or None.

    Returns:
        ISO date string, or None if no reference was available or the
        day/month combination is not a real date in any candidate year.
    """
    if reference is None:
        return None
    from datetime import date

    best: tuple[int, str] | None = None
    anchor = date(reference.year, reference.month, reference.day)
    for year in (reference.year - 1, reference.year, reference.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        distance = abs((candidate - anchor).days)
        if best is None or distance < best[0]:
            best = (distance, candidate.isoformat())
    return best[1] if best else None


def _resolve_iso(pattern_name: str, match: re.Match[str], reference=None) -> tuple[str | None, str]:
    """Turn a regex match into ``(iso_date, precision)``.

    Ambiguity policy: ``dd-mm-yyyy`` is assumed for numeric dates, because the
    corpus persona is Indian and every literal date in the source material uses
    that order. Where the first field exceeds 12 the interpretation is forced
    and unambiguous. This assumption is a **configurable-in-principle,
    hardcoded-in-practice** limitation and is recorded as such in log.md.
    """
    try:
        if pattern_name == "dmy_numeric":
            day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            if day > 31 or month > 12:
                if month <= 31 and day <= 12:  # it was mm-dd-yyyy after all
                    day, month = month, day
                else:
                    return None, "day"
            return f"{year:04d}-{month:02d}-{day:02d}", "day"

        if pattern_name == "iso":
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return f"{year:04d}-{month:02d}-{day:02d}", "day"

        if pattern_name == "dmy_written":
            day = int(match.group(1))
            month = _MONTHS[match.group(2)[:3].lower()]
            year_text = match.group(3)
            if year_text:
                return f"{int(year_text):04d}-{month:02d}-{day:02d}", "day"
            return _infer_year(month, day, reference), "day"

        if pattern_name == "mdy_written":
            month = _MONTHS[match.group(1)[:3].lower()]
            day = int(match.group(2))
            year_text = match.group(3)
            if year_text:
                return f"{int(year_text):04d}-{month:02d}-{day:02d}", "day"
            return _infer_year(month, day, reference), "day"

        if pattern_name == "year_only":
            return f"{int(match.group(1)):04d}-01-01", "year"
    except (ValueError, KeyError):
        return None, "day"
    return None, "day"


def _valid_date(iso: str | None) -> bool:
    """Whether an ISO string is a real calendar date."""
    if not iso:
        return False
    from datetime import date

    try:
        year, month, day = (int(part) for part in iso.split("-"))
        date(year, month, day)
    except ValueError:
        return False
    return 1900 <= year <= 2099


class EntityExtractor:
    """Extracts entities, keywords, and raw date mentions from document text."""

    def __init__(
        self,
        model_name: str = "en_core_web_md",
        *,
        max_keywords: int = 25,
        max_chars: int = 200_000,
        drop_acronym_orgs: bool = True,
    ) -> None:
        """Configure the extractor.

        Args:
            model_name: spaCy model to load.
            max_keywords: How many keywords to keep per document.
            max_chars: Hard ceiling per document. spaCy's default limit is 1 MB;
                this lower bound keeps peak memory predictable on a 14 GB laptop
                where several documents may be in flight.
            drop_acronym_orgs: Suppress short all-capitals tokens typed as
                organisations. See :func:`is_bare_acronym` for the trade-off.
        """
        self.model_name = model_name
        self.max_keywords = max_keywords
        self.max_chars = max_chars
        self.drop_acronym_orgs = drop_acronym_orgs
        self._nlp = None

    @property
    def nlp(self):
        """The loaded spaCy pipeline (loaded on first use)."""
        if self._nlp is None:
            self._nlp = load_spacy(self.model_name)
        return self._nlp

    def extract(self, rel_path: str, text: str, reference_date=None) -> DocumentEntities:
        """Run the full Layer-3 pipeline over one document's text.

        Never raises: a failure is recorded in ``DocumentEntities.error``, for
        the same reason Layer 2 captures its errors.

        Args:
            rel_path: Corpus-relative path, recorded on the result.
            text: The document's extracted text.
            reference_date: The document's modification time, used to resolve
                year-less date mentions such as "24 Nov". See :func:`_infer_year`.

        Returns:
            A :class:`DocumentEntities` for this document.
        """
        import time

        started = time.perf_counter()
        result = DocumentEntities(rel_path=rel_path)
        if not text.strip():
            result.duration_ms = (time.perf_counter() - started) * 1000
            return result

        clipped = text[: self.max_chars]
        try:
            doc = self.nlp(prepare_for_ner(clipped))
        except Exception as exc:  # noqa: BLE001 - one bad document must not stop a build
            result.error = f"{type(exc).__name__}: {exc}"
            result.duration_ms = (time.perf_counter() - started) * 1000
            return result

        result.entities = self._remap(self._entities(doc), clipped)
        # Dates are matched against the ORIGINAL text so their stored offsets
        # index the same string as the extracted blocks - which is what lets
        # Phase 10 ask "is this date inside a table?" from stored data alone.
        result.dates = self._dates(clipped, reference_date)
        result.keywords = self._keywords(doc)
        result.duration_ms = (time.perf_counter() - started) * 1000
        return result

    @staticmethod
    def _remap(mentions: list[EntityMention], original: str) -> list[EntityMention]:
        """Rewrite entity offsets from the NER-prepared text back to the original.

        :func:`prepare_for_ner` only removes and substitutes markup, never
        reorders words, so entities occur in the same order in both strings and
        a single forward-scanning cursor resolves them. An entity whose surface
        cannot be located (because markup sat inside it) keeps offsets of -1
        rather than a wrong offset - a missing offset is recoverable, a
        plausible-but-wrong one is not.
        """
        remapped: list[EntityMention] = []
        cursor = 0
        for mention in mentions:
            position = original.find(mention.text, cursor)
            if position < 0:
                position = original.find(mention.text)
            if position < 0:
                remapped.append(
                    EntityMention(
                        text=mention.text,
                        normalised=mention.normalised,
                        category=mention.category,
                        spacy_label=mention.spacy_label,
                        start=-1,
                        end=-1,
                    )
                )
                continue
            remapped.append(
                EntityMention(
                    text=mention.text,
                    normalised=mention.normalised,
                    category=mention.category,
                    spacy_label=mention.spacy_label,
                    start=position,
                    end=position + len(mention.text),
                )
            )
            cursor = position + len(mention.text)
        return remapped

    # -- components --------------------------------------------------------

    def _entities(self, doc) -> list[EntityMention]:
        """Map spaCy entities onto ContextFS categories, dropping noise labels."""
        mentions: list[EntityMention] = []
        for ent in doc.ents:
            category = ENTITY_LABEL_MAP.get(ent.label_)
            if category is None:
                continue
            surface = ent.text.strip()
            normalised = self.normalise(surface)
            if len(normalised) < 2 or normalised.isdigit():
                continue
            if (
                self.drop_acronym_orgs
                and category in {"org", "product", "work"}
                and is_bare_acronym(normalised)
            ):
                continue
            mentions.append(
                EntityMention(
                    text=surface,
                    normalised=normalised,
                    category=category,
                    spacy_label=ent.label_,
                    start=ent.start_char,
                    end=ent.end_char,
                )
            )
        return mentions

    @staticmethod
    def normalise(surface: str) -> str:
        """Canonicalise an entity's surface form for cross-document matching.

        Strips honorifics and possessives and collapses whitespace, so that
        "Dr. Murari Devakannan Kamalesh", "Murari Devakannan Kamalesh's" and
        "Murari  Devakannan  Kamalesh" all resolve to one entity and therefore
        produce one entity edge instead of three.
        """
        cleaned = " ".join(surface.split())
        for prefix in ("Dr. ", "Dr ", "Prof. ", "Prof ", "Mr. ", "Mr ", "Ms. ", "Ms ", "Mrs. "):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix) :]
        for suffix in ("'s", "’s"):
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
        return cleaned.strip(" .,;:")

    def _dates(self, text: str, reference=None) -> list[DateMention]:
        """Collect date mentions by pattern matching over the original text.

        spaCy's ``DATE`` entities are deliberately **not** used. Two reasons:

        1. They are produced from the NER-prepared text, so their offsets do not
           index the original string, and a remapped date offset that is subtly
           wrong would corrupt the structured-context signal silently.
        2. The mentions they add beyond these patterns are relative expressions
           ("next week", "last semester") which resolve to no calendar date and
           therefore cannot become timeline nodes.

        **Stated limitation:** relative date expressions are consequently out of
        scope for the temporal layer in this build.

        Overlapping mentions are resolved in favour of the one that resolved to
        a real date, then the longest span, so "24 November 2025" yields one
        mention rather than three overlapping ones.
        """
        candidates: list[DateMention] = []

        for name, pattern in DATE_PATTERNS:
            for match in pattern.finditer(text):
                iso, precision = _resolve_iso(name, match, reference)
                if iso is not None and not _valid_date(iso):
                    continue
                inferred = bool(
                    reference is not None
                    and name in {"dmy_written", "mdy_written"}
                    and match.lastindex
                    and match.group(match.lastindex) is None
                )
                candidates.append(
                    DateMention(
                        text=match.group(0),
                        start=match.start(),
                        end=match.end(),
                        source=name,
                        iso=iso,
                        precision=precision,
                        year_inferred=inferred,
                    )
                )

        return self._dedupe_spans(candidates)

    @staticmethod
    def _dedupe_spans(mentions: list[DateMention]) -> list[DateMention]:
        """Keep the best mention for each overlapping character region."""
        ordered = sorted(mentions, key=lambda m: (m.start, -(m.end - m.start)))
        kept: list[DateMention] = []
        for mention in ordered:
            overlap = next(
                (k for k in kept if mention.start < k.end and k.start < mention.end), None
            )
            if overlap is None:
                kept.append(mention)
                continue
            # Prefer the mention that resolved to a real date, then the longer one.
            better = (mention.iso is not None and overlap.iso is None) or (
                (mention.iso is None) == (overlap.iso is None)
                and (mention.end - mention.start) > (overlap.end - overlap.start)
            )
            if better:
                kept[kept.index(overlap)] = mention
        return sorted(kept, key=lambda m: m.start)

    def _keywords(self, doc) -> list[tuple[str, int]]:
        """Rank content-bearing nouns and noun phrases by frequency.

        Frequency, not TF-IDF: TF-IDF needs a corpus-wide document-frequency
        table, which would make per-document extraction depend on the whole
        corpus and break incrementality - adding one file would invalidate every
        other file's keywords. Corpus-level term weighting happens in the
        embedding and retrieval layers instead, where it belongs.
        """
        counts: Counter[str] = Counter()

        for chunk in getattr(doc, "noun_chunks", []):
            phrase = " ".join(
                token.lemma_.lower()
                for token in chunk
                if not token.is_stop and not token.is_punct and token.pos_ in _KEYWORD_POS
            ).strip()
            if len(phrase) > 3 and phrase not in _KEYWORD_STOPWORDS and not phrase.isdigit():
                counts[phrase] += 1

        for token in doc:
            if (
                token.pos_ in _KEYWORD_POS
                and not token.is_stop
                and not token.is_punct
                and len(token.lemma_) > 3
            ):
                lemma = token.lemma_.lower()
                if lemma not in _KEYWORD_STOPWORDS and not lemma.isdigit():
                    counts[lemma] += 1

        return counts.most_common(self.max_keywords)
