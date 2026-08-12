"""Auxiliary insight surfaces built on the existing index (Phase 20).

Nothing in this module adds a new layer or a new store. Every function here is
a *read* over structures Phases 4-13 already built - the file inventory, the
entity index, the relationship graph, the meaningful-date classification, and
the activity sessions - reshaped into an answer a user would actually ask for.

That is the whole design rationale. These are demo features with, by the
project's own assessment, low research value: they do not test the hypothesis
and they are not part of any reported metric. Implementing them as thin
projections rather than as new pipelines keeps that honest - if a feature here
needed its own extraction pass or its own table, it would be earning its keep
in maintenance cost that the research does not repay.

Four surfaces:

* :func:`near_duplicates` - clusters of files the graph already linked with a
  ``duplicate`` edge, presented as groups rather than pairs.
* :func:`projects` - folder-scoped lifecycle: when work on a body of files
  started, when it stopped, and whether it looks finished or abandoned.
* :func:`digest` - what is actually on disk, by kind, age and size.
* :func:`suggest_tags` - the tags a file would carry if ContextFS tagged it,
  drawn from its entities, its session, and its meaningful dates.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

__all__ = [
    "DuplicateGroup",
    "Project",
    "DigestReport",
    "TagSuggestion",
    "near_duplicates",
    "projects",
    "digest",
    "suggest_tags",
]


def _parse(value: str | None) -> datetime | None:
    """Parse an ISO timestamp, tolerating None and trailing 'Z'."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Near-duplicate detection
# ---------------------------------------------------------------------------


@dataclass
class DuplicateGroup:
    """A set of mutually near-duplicate files."""

    members: list[dict[str, Any]] = field(default_factory=list)
    similarity: float = 0.0
    wasted_bytes: int = 0

    @property
    def keeper(self) -> dict[str, Any]:
        """The member a user would most likely keep: newest, then largest."""
        return max(self.members, key=lambda m: (m["mtime"], m["size"]))

    def as_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "similarity": round(self.similarity, 4),
            "wasted_bytes": self.wasted_bytes,
            "keeper": self.keeper["path"],
            "members": [m["path"] for m in self.members],
        }


def near_duplicates(store, graph) -> list[DuplicateGroup]:
    """Group the graph's ``duplicate`` edges into connected components.

    Pairs are the wrong unit to show a user: three copies of one document
    produce three pairwise edges, and reporting them as three findings makes a
    single problem look like several. Connected components collapse that back
    to one group, which is also the unit a "delete the extras" action would
    operate on.

    Exact-duplicate detection (identical content hash) is folded in here too -
    the graph's Jaccard threshold catches those as well, but the hash check is
    free, exact, and worth reporting separately in the similarity figure.
    """
    if graph is None:
        return []

    adjacency: dict[int, set[int]] = defaultdict(set)
    weights: dict[tuple[int, int], float] = {}
    for source, target, data in graph.edges(data=True):
        if data.get("type") != "duplicate":
            continue
        if not (source.startswith("file:") and target.startswith("file:")):
            continue
        a, b = int(source.split(":")[1]), int(target.split(":")[1])
        adjacency[a].add(b)
        adjacency[b].add(a)
        weights[(min(a, b), max(a, b))] = float(data.get("weight", 0.0))

    # Exact content-hash collisions, which are duplicates by definition.
    by_hash: dict[str, list[int]] = defaultdict(list)
    for row in store.all_files():
        if row["content_hash"]:
            by_hash[row["content_hash"]].append(row["id"])
    for ids in by_hash.values():
        if len(ids) > 1:
            for other in ids[1:]:
                adjacency[ids[0]].add(other)
                adjacency[other].add(ids[0])
                weights[(min(ids[0], other), max(ids[0], other))] = 1.0

    files = {row["id"]: row for row in store.all_files()}
    seen: set[int] = set()
    groups: list[DuplicateGroup] = []

    for start in sorted(adjacency):
        if start in seen:
            continue
        component, stack = set(), [start]
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            stack.extend(adjacency[node] - component)
        seen |= component
        if len(component) < 2:
            continue

        members = [
            {
                "file_id": fid,
                "path": files[fid]["path"],
                "size": files[fid]["size"],
                "mtime": files[fid]["mtime"],
            }
            for fid in sorted(component)
            if fid in files
        ]
        if len(members) < 2:
            continue
        pair_weights = [w for (a, b), w in weights.items() if a in component and b in component]
        sizes = sorted((m["size"] for m in members), reverse=True)
        groups.append(
            DuplicateGroup(
                members=members,
                similarity=sum(pair_weights) / len(pair_weights) if pair_weights else 0.0,
                wasted_bytes=sum(sizes[1:]),
            )
        )

    groups.sort(key=lambda g: -g.wasted_bytes)
    return groups


# ---------------------------------------------------------------------------
# Project lifecycle detection
# ---------------------------------------------------------------------------

#: How long a body of work must be untouched before it stops counting as active.
DORMANT_AFTER_DAYS = 60
#: And how long before it is treated as concluded rather than merely paused.
FINISHED_AFTER_DAYS = 180


@dataclass
class Project:
    """A folder-scoped body of work with a detected lifecycle stage."""

    folder: str
    files: int = 0
    bytes: int = 0
    first_activity: str = ""
    last_activity: str = ""
    span_days: int = 0
    sessions: list[str] = field(default_factory=list)
    deadlines: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    stage: str = "unknown"
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "folder": self.folder,
            "files": self.files,
            "bytes": self.bytes,
            "first_activity": self.first_activity,
            "last_activity": self.last_activity,
            "span_days": self.span_days,
            "sessions": self.sessions,
            "deadlines": self.deadlines,
            "keywords": self.keywords,
            "stage": self.stage,
            "reason": self.reason,
        }


def projects(store, *, now: datetime | None = None, min_files: int = 2) -> list[Project]:
    """Detect bodies of work and where each one sits in its lifecycle.

    A "project" here is a folder, not a learned cluster. That is a deliberate
    downgrade from what the master prompt implies: the activity sessions from
    Phase 12 already are the learned grouping, and re-clustering them into
    projects would produce a second, differently-shaped answer to the same
    question with no way to say which is right. Folders are what the user
    themselves chose, so they are used as the spine and the *sessions* and
    *meaningful dates* are attached to them as evidence.

    Stage is decided by recency against two thresholds, with one override: a
    folder whose latest meaningful date is still in the future is ``upcoming``
    regardless of when its files were last touched, because a deadline that has
    not passed yet is a stronger signal than two months of silence.
    """
    now = now or datetime.now()
    rows = [row for row in store.all_files() if row["status"] != "deleted"]
    if not rows:
        return []

    membership = store.session_membership()
    sessions_by_id = {row["session_id"]: row for row in store.sessions()}
    meaningful: dict[int, list[str]] = defaultdict(list)
    for row in store.meaningful_dates():
        meaningful[row["file_id"]].append(row["iso_date"])

    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        grouped[row["folder"] or "."].append(row)

    out: list[Project] = []
    for folder, members in grouped.items():
        if len(members) < min_files:
            continue
        times = [t for t in (_parse(m["mtime"]) for m in members) if t]
        if not times:
            continue
        first, last = min(times), max(times)

        session_ids = {membership[m["id"]] for m in members if m["id"] in membership}
        labels = [
            sessions_by_id[sid]["label"] for sid in sorted(session_ids) if sid in sessions_by_id
        ]
        dates = sorted({d for m in members for d in meaningful[m["id"]]})

        keywords: Counter[str] = Counter()
        for member in members:
            for kw in store.get_keywords(member["id"])[:5]:
                keywords[kw["term"]] += 1

        idle_days = (now - last).days
        future = [d for d in dates if d > now.date().isoformat()]
        if future:
            stage, reason = (
                "upcoming",
                f"has a meaningful date still ahead ({future[0]}), so it is not dormant "
                f"even though the files were last touched {idle_days} days ago",
            )
        elif idle_days >= FINISHED_AFTER_DAYS:
            stage, reason = (
                "finished",
                f"untouched for {idle_days} days (>= {FINISHED_AFTER_DAYS})",
            )
        elif idle_days >= DORMANT_AFTER_DAYS:
            stage, reason = "dormant", f"untouched for {idle_days} days"
        else:
            stage, reason = "active", f"touched {idle_days} days ago"

        out.append(
            Project(
                folder=folder,
                files=len(members),
                bytes=sum(m["size"] for m in members),
                first_activity=first.isoformat(timespec="seconds"),
                last_activity=last.isoformat(timespec="seconds"),
                span_days=(last - first).days,
                sessions=labels,
                deadlines=dates[-3:],
                keywords=[term for term, _ in keywords.most_common(5)],
                stage=stage,
                reason=reason,
            )
        )

    order = {"upcoming": 0, "active": 1, "dormant": 2, "finished": 3, "unknown": 4}
    out.sort(key=lambda p: (order[p.stage], -p.files))
    return out


# ---------------------------------------------------------------------------
# Disk digest
# ---------------------------------------------------------------------------


@dataclass
class DigestReport:
    """A plain summary of what is in the indexed tree."""

    files: int = 0
    bytes: int = 0
    by_extension: list[tuple[str, int, int]] = field(default_factory=list)
    by_age: dict[str, int] = field(default_factory=dict)
    largest: list[dict[str, Any]] = field(default_factory=list)
    stalest: list[dict[str, Any]] = field(default_factory=list)
    duplicate_groups: int = 0
    duplicate_waste: int = 0
    unextracted: int = 0
    unembedded: int = 0

    def as_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "files": self.files,
            "bytes": self.bytes,
            "by_extension": [{"ext": e, "files": n, "bytes": b} for e, n, b in self.by_extension],
            "by_age": self.by_age,
            "largest": self.largest,
            "stalest": self.stalest,
            "duplicate_groups": self.duplicate_groups,
            "duplicate_waste": self.duplicate_waste,
            "unextracted": self.unextracted,
            "unembedded": self.unembedded,
        }


#: Age buckets, in days, oldest boundary last.
AGE_BUCKETS = (("< 1 week", 7), ("< 1 month", 30), ("< 6 months", 182), ("< 1 year", 365))


def digest(store, graph=None, *, now: datetime | None = None, top: int = 5) -> DigestReport:
    """Summarise the indexed tree by kind, age, size and redundancy."""
    now = now or datetime.now()
    rows = [row for row in store.all_files() if row["status"] != "deleted"]
    report = DigestReport(files=len(rows), bytes=sum(r["size"] for r in rows))

    per_ext: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        bucket = per_ext[row["ext"] or "(none)"]
        bucket[0] += 1
        bucket[1] += row["size"]
    report.by_extension = sorted(
        ((ext, n, b) for ext, (n, b) in per_ext.items()), key=lambda item: -item[2]
    )

    ages = {label: 0 for label, _ in AGE_BUCKETS}
    ages["older"] = 0
    for row in rows:
        stamp = _parse(row["mtime"])
        if stamp is None:
            continue
        age = (now - stamp).days
        for label, limit in AGE_BUCKETS:
            if age < limit:
                ages[label] += 1
                break
        else:
            ages["older"] += 1
    report.by_age = ages

    report.largest = [
        {"path": r["path"], "size": r["size"]} for r in sorted(rows, key=lambda r: -r["size"])[:top]
    ]
    report.stalest = [
        {"path": r["path"], "mtime": r["mtime"]}
        for r in sorted(rows, key=lambda r: r["mtime"])[:top]
    ]

    if graph is not None:
        groups = near_duplicates(store, graph)
        report.duplicate_groups = len(groups)
        report.duplicate_waste = sum(g.wasted_bytes for g in groups)

    report.unextracted = len(store.files_needing_extraction())
    report.unembedded = len(store.files_needing_embedding())
    return report


# ---------------------------------------------------------------------------
# Auto-tag suggestions
# ---------------------------------------------------------------------------


@dataclass
class TagSuggestion:
    """One suggested tag, with the evidence that produced it."""

    tag: str
    source: str
    confidence: float

    def as_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {"tag": self.tag, "source": self.source, "confidence": round(self.confidence, 3)}


def suggest_tags(store, path: str, *, limit: int = 8) -> list[TagSuggestion]:
    """Suggest tags for one file from evidence the index already holds.

    Confidence is *not* a probability and is not calibrated against anything -
    it is a fixed per-source prior times a within-source rank decay, and it
    exists only to order the list. Sources are ranked by how specific they are:
    a session label describes what the user was actually doing, an organisation
    or person name is concrete, and a keyword is the weakest because TF-IDF
    terms are frequently generic. Saying so here is cheaper than letting a
    reader mistake 0.72 for a measured number.
    """
    row = store.get_file(path)
    if row is None:
        return []
    file_id = row["id"]
    out: list[TagSuggestion] = []

    for session in store.sessions_for_file(file_id):
        out.append(TagSuggestion(session["label"], "activity session", 0.90))
        if session["kind"] and session["kind"] != "unknown":
            out.append(TagSuggestion(session["kind"], "session type", 0.75))

    priors = {"ORG": 0.80, "PERSON": 0.78, "EVENT": 0.78, "PRODUCT": 0.70, "GPE": 0.65}
    for entity in store.get_entities(file_id):
        prior = priors.get(entity["category"])
        if prior is None:
            continue
        out.append(TagSuggestion(entity["text"], f"entity ({entity['category'].lower()})", prior))

    for dated in store.classified_dates(file_id):
        if dated["is_meaningful"]:
            out.append(TagSuggestion(dated["iso_date"], "meaningful date", 0.70))

    for rank, kw in enumerate(store.get_keywords(file_id)[:5]):
        out.append(TagSuggestion(kw["term"], "keyword", 0.55 * (0.9**rank)))

    deduped: dict[str, TagSuggestion] = {}
    for suggestion in out:
        key = suggestion.tag.casefold()
        if not key or len(key) < 2:
            continue
        if key not in deduped or suggestion.confidence > deduped[key].confidence:
            deduped[key] = suggestion

    ranked = sorted(deduped.values(), key=lambda s: -s.confidence)
    return ranked[:limit]
