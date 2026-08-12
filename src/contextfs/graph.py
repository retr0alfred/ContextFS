"""Layer 6 - the relationship graph.

A ``networkx.MultiDiGraph`` over file nodes. MultiDiGraph rather than Graph
because two files can be related in **several ways at once** - the draft and
final of an assignment share a folder, share entities, are semantically similar,
*and* are near-duplicates - and an explanation that says "these are connected"
without saying how would fail the explainability requirement. Keeping parallel
edges lets Phase 16 report every reason separately.

Edge types built here
---------------------
``semantic``   cosine similarity above a threshold, capped per node
``entity``     shared named entities, weighted by how distinctive they are
``structural`` folder proximity
``duplicate``  near-identical content

``temporal`` and ``activity`` edges are added in Phase 13 once the timeline and
session layers exist. They are listed in :data:`EDGE_TYPES` from the start so
the ablation harness can switch them off by name without special-casing.

Direction
---------
Semantic, entity and duplicate relations are symmetric, and are stored as a
matched pair of directed edges. The graph is directed because *temporal* and
*activity* edges (Phase 13) genuinely are not symmetric - "this file was edited
before that one" has a direction - and mixing a Graph and a DiGraph later would
be worse than paying for symmetry now.
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np

__all__ = [
    "EDGE_TYPES",
    "GraphReport",
    "build_graph",
    "save_graph",
    "load_graph",
    "graph_stats",
    "neighbours",
    "shortest_explained_path",
]

#: Every edge type the system may produce, including those added in Phase 13.
EDGE_TYPES = ("semantic", "entity", "structural", "duplicate", "temporal", "activity")


@dataclass
class GraphReport:
    """Outcome of a graph build."""

    nodes: int = 0
    edges: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    duplicate_pairs: list[tuple[str, str, float]] = field(default_factory=list)
    isolated: list[str] = field(default_factory=list)
    duration_ms: float = 0.0

    def summary(self) -> dict[str, Any]:
        """Flat printable summary."""
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            **{f"edges_{name}": count for name, count in sorted(self.by_type.items())},
            "duplicate_pairs": len(self.duplicate_pairs),
            "isolated_nodes": len(self.isolated),
            "duration_ms": round(self.duration_ms, 2),
        }


def build_graph(store, vectors=None, config=None) -> tuple[nx.MultiDiGraph, GraphReport]:
    """Build the relationship graph over the corpus.

    Args:
        store: SQLite metadata store.
        vectors: Optional :class:`VectorStore`; without it, semantic and
            duplicate edges are skipped and the graph is structural/entity only.
        config: Resolved configuration supplying thresholds.

    Returns:
        ``(graph, report)``.
    """
    started = time.perf_counter()
    graph = nx.MultiDiGraph()
    report = GraphReport()

    settings = config.graph if config else None
    semantic_threshold = settings.semantic_edge_threshold if settings else 0.55
    edges_per_node = settings.semantic_edges_per_node if settings else 8
    min_shared = settings.min_shared_entities if settings else 2
    duplicate_threshold = settings.duplicate_threshold if settings else 0.25
    candidate_threshold = settings.duplicate_candidate_similarity if settings else 0.70

    files = store.all_files()
    for row in files:
        graph.add_node(
            node_id(row["id"]),
            file_id=row["id"],
            path=row["path"],
            name=row["name"],
            folder=row["folder"],
            ext=row["ext"],
            mtime=row["mtime"],
            size=row["size"],
        )

    _add_structural_edges(graph, files)
    _add_entity_edges(graph, store, min_shared)
    if vectors is not None:
        _add_semantic_edges(
            graph,
            store,
            vectors,
            semantic_threshold,
            edges_per_node,
            duplicate_threshold,
            candidate_threshold,
            report,
        )

    report.nodes = graph.number_of_nodes()
    report.edges = graph.number_of_edges()
    report.by_type = _count_by_type(graph)
    report.isolated = sorted(graph.nodes[n]["path"] for n in graph.nodes if graph.degree(n) == 0)
    report.duration_ms = (time.perf_counter() - started) * 1000
    return graph, report


def node_id(file_id: int) -> str:
    """Graph node id for a file."""
    return f"file:{file_id}"


_WORD = __import__("re").compile(r"[a-z0-9]+")


def shingles(text: str, size: int = 5) -> set[tuple[str, ...]]:
    """Return the set of overlapping word n-grams ("shingles") in a text.

    Word-level rather than character-level: character shingles are dominated by
    common substrings and blur the distinction this is meant to draw.
    """
    words = _WORD.findall(text.lower())
    if len(words) < size:
        return {tuple(words)} if words else set()
    return {tuple(words[i : i + size]) for i in range(len(words) - size + 1)}


def jaccard(left: set, right: set) -> float:
    """Jaccard similarity of two sets; 0.0 if both are empty."""
    if not left and not right:
        return 0.0
    union = len(left | right)
    return len(left & right) / union if union else 0.0


def _add_edge_pair(graph, a: str, b: str, kind: str, weight: float, **attrs: Any) -> None:
    """Add a symmetric relation as two directed edges."""
    graph.add_edge(a, b, key=kind, type=kind, weight=float(weight), **attrs)
    graph.add_edge(b, a, key=kind, type=kind, weight=float(weight), **attrs)


def _add_structural_edges(graph, files) -> None:
    """Link files by folder proximity.

    Same folder is a strong link; sibling folders under a common parent are a
    weak one. Deeper shared prefixes score higher, because ``College/Semester7``
    is a far more specific statement of relatedness than ``College``.
    """
    by_folder: dict[str, list[Any]] = defaultdict(list)
    for row in files:
        by_folder[row["folder"]].append(row)

    for folder, rows in by_folder.items():
        depth = len(folder.split("/")) if folder else 0
        weight = min(1.0, 0.5 + 0.1 * depth)
        for i, left in enumerate(rows):
            for right in rows[i + 1 :]:
                _add_edge_pair(
                    graph,
                    node_id(left["id"]),
                    node_id(right["id"]),
                    "structural",
                    weight,
                    relation="same_folder",
                    folder=folder,
                )

    folders = sorted(by_folder)
    for i, left_folder in enumerate(folders):
        for right_folder in folders[i + 1 :]:
            shared = _shared_prefix_depth(left_folder, right_folder)
            if shared == 0:
                continue
            if left_folder == right_folder:
                continue
            # Only link *sibling* folders, not ancestor/descendant chains, and
            # keep the weight low: living two folders apart is weak evidence.
            left_parts = left_folder.split("/")
            right_parts = right_folder.split("/")
            if shared != len(left_parts) - 1 or shared != len(right_parts) - 1:
                continue
            weight = min(0.45, 0.15 + 0.1 * shared)
            for left in by_folder[left_folder]:
                for right in by_folder[right_folder]:
                    _add_edge_pair(
                        graph,
                        node_id(left["id"]),
                        node_id(right["id"]),
                        "structural",
                        weight,
                        relation="sibling_folder",
                        folder=("/".join(left_parts[:shared]) or "/"),
                    )


def _shared_prefix_depth(left: str, right: str) -> int:
    """Number of leading path components two folders share."""
    left_parts = left.split("/") if left else []
    right_parts = right.split("/") if right else []
    shared = 0
    for a, b in zip(left_parts, right_parts, strict=False):
        if a != b:
            break
        shared += 1
    return shared


def _add_entity_edges(graph, store, min_shared: int) -> None:
    """Link files that share named entities, weighted by entity distinctiveness.

    Weighting uses inverse document frequency. Sharing "Dr. Murari", who appears
    in three files, is strong evidence of a relationship; sharing "Chennai",
    which appears in a dozen, is nearly none. Counting raw shared entities would
    treat those identically and would make every file mentioning the user's own
    city a neighbour of every other.
    """
    index = store.entity_index()
    total_files = max(1, store.file_count())

    shared_by_pair: dict[tuple[int, int], list[tuple[str, float]]] = defaultdict(list)
    for key, file_ids in index.items():
        if len(file_ids) < 2:
            continue
        # An entity in nearly every file carries no information.
        if len(file_ids) > max(2, total_files * 0.5):
            continue
        idf = math.log(total_files / len(file_ids)) + 1.0
        ordered = sorted(file_ids)
        for i, left in enumerate(ordered):
            for right in ordered[i + 1 :]:
                shared_by_pair[(left, right)].append((key, idf))

    for (left, right), shared in shared_by_pair.items():
        if len(shared) < min_shared:
            continue
        if not (graph.has_node(node_id(left)) and graph.has_node(node_id(right))):
            continue
        score = sum(idf for _, idf in shared)
        weight = min(1.0, score / (score + 4.0))  # saturating, keeps weights in [0,1)
        _add_edge_pair(
            graph,
            node_id(left),
            node_id(right),
            "entity",
            weight,
            shared_count=len(shared),
            shared=[key for key, _ in sorted(shared, key=lambda item: -item[1])[:8]],
        )


def _add_semantic_edges(
    graph, store, vectors, threshold, per_node, duplicate_threshold, candidate_threshold, report
) -> None:
    """Link files by embedding similarity, and flag near-duplicates.

    An all-pairs similarity over the document matrix is used rather than one
    nearest-neighbour query per file: the matrix is small, one matrix multiply
    is dramatically faster than N searches, and it gives exact results where ANN
    search would give approximate ones.
    """
    ids, matrix = vectors.document_vectors()
    if len(ids) < 2:
        return

    paths = store.path_by_file_id()
    similarity = matrix @ matrix.T
    np.fill_diagonal(similarity, -1.0)

    for position, file_id in enumerate(ids):
        row = similarity[position]
        # Keep only the strongest `per_node` neighbours: a dense graph is both
        # slower to traverse and less informative, since everything connects to
        # everything and the graph signal stops discriminating.
        top = np.argsort(-row)[:per_node]
        for other_position in top:
            score = float(row[other_position])
            if score < threshold:
                break
            other_id = ids[int(other_position)]
            if file_id >= other_id:
                continue  # emit each pair once; _add_edge_pair makes it symmetric
            if not (graph.has_node(node_id(file_id)) and graph.has_node(node_id(other_id))):
                continue
            _add_edge_pair(
                graph, node_id(file_id), node_id(other_id), "semantic", score, similarity=score
            )

    _add_duplicate_edges(
        graph, store, ids, similarity, paths, duplicate_threshold, candidate_threshold, report
    )


def _add_duplicate_edges(
    graph, store, ids, similarity, paths, threshold, candidate_threshold, report
) -> None:
    """Flag near-duplicates by token overlap, using cosine only as a pre-filter.

    Embedding cosine is a poor near-duplicate signal, and this was measured
    rather than assumed. On the synthetic corpus:

    ============================  ==========  =========
    pair                          cosine      jaccard
    ============================  ==========  =========
    planted duplicate #1            0.928       0.519
    planted duplicate #2            0.827       0.393
    best non-duplicate pair         0.807       0.021
    ============================  ==========  =========

    By cosine the margin between a true duplicate and a false one is **0.019**
    - any threshold is a coin flip, and the second planted pair sits below
    several unrelated pairs. By Jaccard over 5-word shingles the margin is
    **0.372**, roughly a nineteen-fold improvement in separation.

    The reason is structural, not incidental: embeddings are trained to make
    documents *about the same topic* land close together, which is the opposite
    of what near-duplicate detection needs. Two different documents about BCNF
    normalisation should be semantically close and are not duplicates.

    Cosine is still used, as a cheap candidate filter: computing shingle sets
    for every pair would be O(n^2) over full document text, while the matrix
    multiply is nearly free and discards almost everything.
    """
    candidates = np.argwhere(similarity >= candidate_threshold)
    texts: dict[int, set] = {}
    seen: set[tuple[int, int]] = set()

    for left_position, right_position in candidates:
        left_id, right_id = ids[int(left_position)], ids[int(right_position)]
        pair = (min(left_id, right_id), max(left_id, right_id))
        if pair in seen:
            continue
        seen.add(pair)

        for file_id in pair:
            if file_id not in texts:
                document = store.get_document(file_id)
                texts[file_id] = shingles(document["text"] if document else "")

        score = jaccard(texts[pair[0]], texts[pair[1]])
        if score < threshold:
            continue
        cosine = float(similarity[left_position, right_position])
        _add_edge_pair(
            graph,
            node_id(pair[0]),
            node_id(pair[1]),
            "duplicate",
            score,
            jaccard=score,
            similarity=cosine,
        )
        report.duplicate_pairs.append((paths.get(pair[0], ""), paths.get(pair[1], ""), score))


def _count_by_type(graph) -> dict[str, int]:
    """Count edges by ``type`` attribute."""
    counts: dict[str, int] = {}
    for _, _, data in graph.edges(data=True):
        kind = data.get("type", "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def graph_stats(graph) -> dict[str, Any]:
    """Return a descriptive statistics report for a graph."""
    undirected = nx.Graph(graph)
    degrees = [degree for _, degree in graph.degree()]
    return {
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "by_type": _count_by_type(graph),
        "isolated_nodes": sum(1 for degree in degrees if degree == 0),
        "mean_degree": round(sum(degrees) / len(degrees), 2) if degrees else 0.0,
        "max_degree": max(degrees) if degrees else 0,
        "connected_components": nx.number_connected_components(undirected),
        "density": round(nx.density(graph), 4),
    }


def neighbours(
    graph, node: str, edge_types: set[str] | None = None
) -> list[tuple[str, str, float]]:
    """Return ``(neighbour, edge_type, weight)`` for one node.

    Args:
        graph: The relationship graph.
        node: Node id.
        edge_types: Restrict to these edge types. Used by the ablation harness
            to disable a layer without rebuilding the graph.
    """
    if node not in graph:
        return []
    out: list[tuple[str, str, float]] = []
    for _, target, data in graph.out_edges(node, data=True):
        kind = data.get("type", "unknown")
        if edge_types is not None and kind not in edge_types:
            continue
        out.append((target, kind, float(data.get("weight", 0.0))))
    return sorted(out, key=lambda item: -item[2])


def shortest_explained_path(
    graph, source: str, target: str, edge_types: set[str] | None = None
) -> list[tuple[str, str, str]] | None:
    """Find a path and report which edge type carried each hop.

    This is the "graph path" component of the explanation object required by
    Phase 16 - a result must be able to say *how* it was reached, not merely
    that it was.

    Returns:
        ``[(from_node, edge_type, to_node), ...]``, or None if unreachable.
    """
    if source not in graph or target not in graph:
        return None
    view = graph
    if edge_types is not None:
        view = nx.MultiDiGraph()
        view.add_nodes_from(graph.nodes(data=True))
        for u, v, data in graph.edges(data=True):
            if data.get("type") in edge_types:
                view.add_edge(u, v, **data)
    try:
        nodes = nx.shortest_path(view, source, target)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None

    hops: list[tuple[str, str, str]] = []
    for left, right in zip(nodes, nodes[1:], strict=False):
        best = max(
            view.get_edge_data(left, right).values(),
            key=lambda data: float(data.get("weight", 0.0)),
        )
        hops.append((left, best.get("type", "unknown"), right))
    return hops


def save_graph(graph, path) -> None:
    """Serialise the graph to JSON.

    JSON node-link format rather than pickle: an index that a user can read is
    an index a user can audit, which matters for a system whose selling point is
    explainability. Pickle would also be a code-execution hazard on load.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = nx.node_link_data(graph, edges="links")
    path.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8", newline="\n")


def load_graph(path) -> nx.MultiDiGraph:
    """Load a graph previously written by :func:`save_graph`."""
    path = Path(path)
    if not path.is_file():
        return nx.MultiDiGraph()
    data = json.loads(path.read_text(encoding="utf-8"))
    return nx.node_link_graph(data, directed=True, multigraph=True, edges="links")
