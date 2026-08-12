"""Layer 8 - activity session reconstruction (core contribution).

The idea
--------
A file's meaning often lives outside its own text. The lecture PDF a student
revised before an exam contains no word connecting it to that exam; what
connects them is that they were *used together, in one stretch of work*.
Reconstructing those stretches lets ContextFS answer "the PDF I studied before
my ML exam" - a query that pure semantic retrieval cannot answer even in
principle, because the evidence is not in the document.

How sessions are found
----------------------
Agglomerative clustering over files, with a **hard temporal gate** and a
weighted affinity:

    affinity(a, b) = w_t · temporal(a, b)
                   + w_s · semantic(a, b)
                   + w_e · entity_overlap(a, b)
                   + w_f · folder_proximity(a, b)

    two clusters may merge only if the IDLE GAP between them - the shortest
    time between any file of one and any file of the other - is at most
    ``session_gap_hours``

The gate is on the **idle gap between clusters**, not on every pair of files.
That distinction was forced by measurement (log.md, Decision 62): an all-pairs
gate caps a session's total duration at ``session_gap_hours``, and *no* real
session in the corpus satisfies that - exam preparation runs 12 days, capstone
work 25. What actually characterises a work episode is not that it is short, but
that it has **no long silence in the middle**. A gap gate expresses that; a
pairwise gate expresses something nobody means.

Time is a gate rather than another weighted term because it is a different kind
of statement. Two files about identical topics edited four months apart are not
one work session - they are the same project revisited. As a weighted term, a
high topic score could buy its way past an implausible gap; as a gate, it cannot.

Linkage is **average**, not single. Single linkage chains: A joins B, B joins C,
and a corpus of loosely-related personal files collapses into one giant session.
Average linkage requires a candidate to resemble the cluster as a whole.

Why this is not just clustering by folder
-----------------------------------------
Folder proximity is one of four signals and is weighted lowest. The corpus
contains a deliberate negative control - `Personal/Misc`, five files sharing a
folder but spanning 223 days - which must **not** become a session. Folder
membership alone must not be sufficient, and the session-accuracy metric is
designed to catch it if it is.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

__all__ = [
    "Session",
    "SessionReport",
    "SessionBuilder",
    "SESSION_TYPE_KEYWORDS",
    "session_accuracy",
]

#: Vocabulary used to label a reconstructed session with a human-meaningful
#: type. Labels are descriptive only - they never affect clustering, so a
#: mislabelled session is a cosmetic error rather than a retrieval one.
SESSION_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "exam_prep": ("exam", "revision", "syllabus", "unit", "timetable", "hall ticket", "viva"),
    "hackathon": ("hackathon", "hack", "pitch", "demo", "submission", "team"),
    "assignment": ("assignment", "homework", "submission", "marks", "lab record", "brief"),
    "project": ("project", "proposal", "review", "supervisor", "literature", "evaluation"),
    "career": ("resume", "cv", "internship", "cover letter", "interview", "application"),
    "meeting": ("meeting", "minutes", "agenda", "attendees", "discussion"),
}


@dataclass
class Session:
    """A reconstructed stretch of coherent work."""

    session_id: str
    file_ids: list[int] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    start: datetime | None = None
    end: datetime | None = None
    label: str = ""
    kind: str = "unknown"
    keywords: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    meaningful_dates: list[str] = field(default_factory=list)
    cohesion: float = 0.0

    @property
    def size(self) -> int:
        """Number of files in the session."""
        return len(self.file_ids)

    @property
    def span_hours(self) -> float:
        """Wall-clock span of the session, in hours."""
        if not (self.start and self.end):
            return 0.0
        return (self.end - self.start).total_seconds() / 3600.0

    def as_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "session_id": self.session_id,
            "label": self.label,
            "kind": self.kind,
            "size": self.size,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "span_hours": round(self.span_hours, 1),
            "cohesion": round(self.cohesion, 4),
            "files": self.paths,
            "keywords": self.keywords[:10],
            "entities": self.entities[:10],
            "meaningful_dates": self.meaningful_dates,
        }


@dataclass
class SessionReport:
    """Outcome of a session-reconstruction pass."""

    sessions: list[Session] = field(default_factory=list)
    unsessioned: list[int] = field(default_factory=list)
    duration_ms: float = 0.0

    def summary(self) -> dict[str, Any]:
        """Flat printable summary."""
        return {
            "sessions": len(self.sessions),
            "clustered_files": sum(session.size for session in self.sessions),
            "unsessioned_files": len(self.unsessioned),
            "by_kind": dict(Counter(session.kind for session in self.sessions)),
            "mean_size": (
                round(sum(s.size for s in self.sessions) / len(self.sessions), 2)
                if self.sessions
                else 0.0
            ),
            "duration_ms": round(self.duration_ms, 2),
        }


class SessionBuilder:
    """Reconstructs activity sessions from an indexed corpus."""

    #: Affinity weights. Deliberately not in ``contextfs.toml``: unlike the
    #: retrieval and date weights, these are not part of any research claim and
    #: exposing every constant as configuration makes the config file unusable.
    #: They are named constants here so they remain visible and adjustable.
    WEIGHT_TEMPORAL = 0.40
    WEIGHT_SEMANTIC = 0.30
    WEIGHT_ENTITY = 0.20
    WEIGHT_FOLDER = 0.10

    def __init__(self, config) -> None:
        """Bind to the ``[activity]`` configuration section."""
        self.config = config
        self.gap_hours = config.activity.session_gap_hours
        self.threshold = config.activity.session_link_threshold
        self.min_size = config.activity.min_session_size

    # -- pairwise signals --------------------------------------------------

    def temporal_affinity(self, left: datetime, right: datetime) -> float:
        """Closeness in time, linear from 1.0 at zero gap to 0.0 at the limit."""
        gap = abs((left - right).total_seconds()) / 3600.0
        if gap > self.gap_hours:
            return 0.0
        return 1.0 - gap / self.gap_hours

    @staticmethod
    def entity_affinity(left: set[str], right: set[str]) -> float:
        """Jaccard overlap of two files' entity sets."""
        if not left or not right:
            return 0.0
        union = len(left | right)
        return len(left & right) / union if union else 0.0

    @staticmethod
    def folder_affinity(left: str, right: str) -> float:
        """Folder proximity: 1.0 for the same folder, decaying with distance."""
        if left == right:
            return 1.0
        left_parts = left.split("/") if left else []
        right_parts = right.split("/") if right else []
        shared = 0
        for a, b in zip(left_parts, right_parts, strict=False):
            if a != b:
                break
            shared += 1
        if shared == 0:
            return 0.0
        depth = max(len(left_parts), len(right_parts))
        return shared / (depth + 1)

    # -- clustering --------------------------------------------------------

    def build(self, store, vectors=None) -> SessionReport:
        """Reconstruct sessions over every present file.

        Args:
            store: SQLite metadata store.
            vectors: Optional vector store; without it the semantic term is 0
                and its weight is redistributed over the remaining signals.

        Returns:
            A :class:`SessionReport`.
        """
        started = time.perf_counter()
        report = SessionReport()

        files = store.all_files()
        if len(files) < 2:
            report.duration_ms = (time.perf_counter() - started) * 1000
            return report

        file_ids = [row["id"] for row in files]
        index = {file_id: position for position, file_id in enumerate(file_ids)}
        mtimes = [datetime.fromisoformat(row["mtime"]) for row in files]
        folders = [row["folder"] for row in files]
        paths = [row["path"] for row in files]

        entity_sets: list[set[str]] = []
        for row in files:
            entity_sets.append(
                {f"{e['category']}:{e['normalised']}" for e in store.get_entities(row["id"])}
            )

        gaps = np.zeros((len(files), len(files)), dtype=np.float32)
        for i in range(len(files)):
            for j in range(i + 1, len(files)):
                hours = abs((mtimes[i] - mtimes[j]).total_seconds()) / 3600.0
                gaps[i, j] = gaps[j, i] = hours

        similarity = np.zeros((len(files), len(files)), dtype=np.float32)
        have_vectors = False
        if vectors is not None:
            vector_ids, matrix = vectors.document_vectors()
            if len(vector_ids) >= 2:
                have_vectors = True
                dense = np.zeros((len(files), matrix.shape[1]), dtype=np.float32)
                for position, file_id in enumerate(vector_ids):
                    if file_id in index:
                        dense[index[file_id]] = matrix[position]
                similarity = dense @ dense.T

        weights = self._weights(have_vectors)
        affinity = self._affinity_matrix(
            mtimes, folders, entity_sets, similarity, weights, have_vectors
        )
        clusters = self._agglomerate(affinity, gaps)

        report.sessions, report.unsessioned = self._materialise(
            clusters, file_ids, paths, mtimes, affinity, store
        )
        report.duration_ms = (time.perf_counter() - started) * 1000
        return report

    def _weights(self, have_vectors: bool) -> dict[str, float]:
        """Affinity weights, renormalised if the semantic signal is unavailable."""
        weights = {
            "temporal": self.WEIGHT_TEMPORAL,
            "semantic": self.WEIGHT_SEMANTIC if have_vectors else 0.0,
            "entity": self.WEIGHT_ENTITY,
            "folder": self.WEIGHT_FOLDER,
        }
        total = sum(weights.values())
        return {key: value / total for key, value in weights.items()}

    def _affinity_matrix(
        self, mtimes, folders, entity_sets, similarity, weights, have_vectors
    ) -> np.ndarray:
        """Compute the pairwise affinity matrix, applying the temporal gate."""
        count = len(mtimes)
        affinity = np.zeros((count, count), dtype=np.float32)
        for i in range(count):
            for j in range(i + 1, count):
                score = weights["temporal"] * self.temporal_affinity(mtimes[i], mtimes[j])
                if have_vectors:
                    score += weights["semantic"] * max(0.0, float(similarity[i, j]))
                score += weights["entity"] * self.entity_affinity(entity_sets[i], entity_sets[j])
                score += weights["folder"] * self.folder_affinity(folders[i], folders[j])
                affinity[i, j] = affinity[j, i] = score
        return affinity

    def _agglomerate(self, affinity: np.ndarray, gaps: np.ndarray) -> list[list[int]]:
        """Average-linkage agglomerative clustering down to the threshold.

        Implemented directly rather than via scipy so the linkage rule, the
        idle-gap gate and the stopping condition are visible in the code a
        reviewer will read - this is a contribution being evaluated, not a
        utility call.
        """
        clusters: list[list[int]] = [[i] for i in range(affinity.shape[0])]

        while len(clusters) > 1:
            best_score = self.threshold
            best_pair: tuple[int, int] | None = None
            for a in range(len(clusters)):
                for b in range(a + 1, len(clusters)):
                    if self._idle_gap(gaps, clusters[a], clusters[b]) > self.gap_hours:
                        continue
                    score = self._average_linkage(affinity, clusters[a], clusters[b])
                    if score > best_score:
                        best_score, best_pair = score, (a, b)
            if best_pair is None:
                break
            a, b = best_pair
            clusters[a] = clusters[a] + clusters[b]
            clusters.pop(b)
        return clusters

    @staticmethod
    def _idle_gap(gaps: np.ndarray, left: list[int], right: list[int]) -> float:
        """Shortest time in hours between any file of one cluster and the other.

        The *minimum*, not the maximum: a session is characterised by having no
        long silence in the middle, so what matters is whether the two stretches
        of work touch, not how far their extremes are apart.
        """
        return min(float(gaps[i, j]) for i in left for j in right)

    @staticmethod
    def _average_linkage(affinity: np.ndarray, left: list[int], right: list[int]) -> float:
        """Mean affinity between every cross-cluster pair."""
        total = sum(float(affinity[i, j]) for i in left for j in right)
        return total / (len(left) * len(right))

    def _materialise(
        self, clusters, file_ids, paths, mtimes, affinity, store
    ) -> tuple[list[Session], list[int]]:
        """Turn index clusters into labelled :class:`Session` objects."""
        sessions: list[Session] = []
        unsessioned: list[int] = []

        for cluster in sorted(clusters, key=lambda c: min(mtimes[i] for i in c)):
            if len(cluster) < self.min_size:
                unsessioned.extend(file_ids[i] for i in cluster)
                continue

            members = sorted(cluster, key=lambda i: mtimes[i])
            session = Session(
                session_id=f"session:{len(sessions) + 1}",
                file_ids=[file_ids[i] for i in members],
                paths=[paths[i] for i in members],
                start=mtimes[members[0]],
                end=mtimes[members[-1]],
                cohesion=self._cohesion(affinity, members),
            )
            self._describe(session, store)
            sessions.append(session)

        return sessions, unsessioned

    @staticmethod
    def _cohesion(affinity: np.ndarray, members: list[int]) -> float:
        """Mean pairwise affinity inside a cluster."""
        if len(members) < 2:
            return 0.0
        scores = [
            float(affinity[i, j])
            for position, i in enumerate(members)
            for j in members[position + 1 :]
        ]
        return sum(scores) / len(scores) if scores else 0.0

    def _describe(self, session: Session, store) -> None:
        """Attach keywords, entities, dates, a type and a human label."""
        keywords: Counter[str] = Counter()
        entities: Counter[str] = Counter()
        dates: list[str] = []

        for file_id in session.file_ids:
            for row in store.get_keywords(file_id):
                keywords[row["term"]] += row["count"]
            for row in store.get_entities(file_id):
                entities[row["normalised"]] += 1
            for row in store.classified_dates(file_id):
                if row["is_meaningful"]:
                    dates.append(row["iso_date"])

        session.keywords = [term for term, _ in keywords.most_common(15)]
        session.entities = [name for name, _ in entities.most_common(10)]
        session.meaningful_dates = sorted(set(dates))

        haystack = " ".join(session.keywords + [path.lower() for path in session.paths]).lower()
        best_kind, best_hits = "unknown", 0
        for kind, vocabulary in SESSION_TYPE_KEYWORDS.items():
            hits = sum(1 for word in vocabulary if word in haystack)
            if hits > best_hits:
                best_kind, best_hits = kind, hits
        session.kind = best_kind

        folder = _common_folder(session.paths)
        when = session.start.strftime("%d %b %Y") if session.start else ""
        session.label = f"{best_kind.replace('_', ' ')} in {folder or 'various'} ({when})"


def _common_folder(paths: list[str]) -> str:
    """Longest common folder prefix of a set of paths."""
    if not paths:
        return ""
    parts = [path.split("/")[:-1] for path in paths]
    shared: list[str] = []
    for level in zip(*parts, strict=False):
        if len(set(level)) == 1:
            shared.append(level[0])
        else:
            break
    return "/".join(shared)


def session_accuracy(predicted: list[Session], truth: dict[str, str | None]) -> dict[str, Any]:
    """Score reconstructed sessions against ground truth.

    **Metric: pairwise F1 over "same session" judgements.** For every pair of
    files, ground truth says whether they belong to one session and the
    reconstruction says whether it put them together. Pairwise F1 is the
    standard choice for clustering evaluation, needs no alignment between
    predicted and true cluster ids, and degrades gracefully - splitting one true
    session into two costs recall rather than scoring zero.

    **The negative control is treated as singletons.** Files whose ground-truth
    session is ``None`` (the corpus's ``personal_misc`` group, five files
    spanning 223 days) form *no* same-session pairs. Grouping them is therefore
    counted as a precision failure, which is the entire purpose of including
    them: a metric that rewarded clustering them would reward over-clustering.

    Also reported: how many true sessions were recovered, where "recovered"
    means some predicted session overlaps it with F1 >= 0.5.

    Args:
        predicted: Reconstructed sessions.
        truth: ``{path: session_id_or_None}``.

    Returns:
        Pairwise precision/recall/F1, per-session recovery, and the counts.
    """
    assignment: dict[str, str] = {}
    for session in predicted:
        for path in session.paths:
            assignment[path] = session.session_id

    paths = sorted(truth)
    true_pairs: set[tuple[str, str]] = set()
    predicted_pairs: set[tuple[str, str]] = set()

    for i, left in enumerate(paths):
        for right in paths[i + 1 :]:
            true_left, true_right = truth[left], truth[right]
            if true_left is not None and true_left == true_right:
                true_pairs.add((left, right))
            predicted_left, predicted_right = assignment.get(left), assignment.get(right)
            if predicted_left is not None and predicted_left == predicted_right:
                predicted_pairs.add((left, right))

    tp = len(true_pairs & predicted_pairs)
    fp = len(predicted_pairs - true_pairs)
    fn = len(true_pairs - predicted_pairs)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    true_sessions: dict[str, set[str]] = {}
    for path, session_id in truth.items():
        if session_id is not None:
            true_sessions.setdefault(session_id, set()).add(path)

    recovered: dict[str, dict[str, Any]] = {}
    for session_id, members in true_sessions.items():
        best = {"f1": 0.0, "matched": None, "overlap": 0}
        for session in predicted:
            overlap = len(members & set(session.paths))
            if not overlap:
                continue
            p = overlap / session.size
            r = overlap / len(members)
            score = 2 * p * r / (p + r) if p + r else 0.0
            if score > best["f1"]:
                best = {
                    "f1": round(score, 3),
                    "matched": session.session_id,
                    "overlap": overlap,
                    "true_size": len(members),
                    "predicted_size": session.size,
                }
        recovered[session_id] = best

    return {
        "pairwise_precision": round(precision, 4),
        "pairwise_recall": round(recall, 4),
        "pairwise_f1": round(f1, 4),
        "true_pairs": len(true_pairs),
        "predicted_pairs": len(predicted_pairs),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "true_sessions": len(true_sessions),
        "predicted_sessions": len(predicted),
        "sessions_recovered": sum(1 for v in recovered.values() if v["f1"] >= 0.5),
        "per_session": recovered,
    }
