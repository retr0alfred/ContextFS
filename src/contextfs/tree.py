"""Layer 5 - the semantic tree (RAPTOR-inspired).

Builds ``Root -> Project -> Folder -> File -> Chunk`` and attaches a summary to
every non-leaf node, generated bottom-up from its children.

What is borrowed from RAPTOR, and what is not
---------------------------------------------
Borrowed: the idea that a *summary node is itself a legitimate retrieval
target*, so a query can match "the UrbanFlow project" rather than only matching
individual files inside it.

Not borrowed: RAPTOR clusters chunks by embedding similarity and builds an
abstract hierarchy over those clusters. ContextFS instead uses the **filesystem's
own hierarchy** as the tree's skeleton. That is a deliberate departure, argued
in log.md (Decision 44): users remember where they put things, folder structure
is a signal a user actually authored, and a clustered hierarchy would produce
nodes with no name a user could recognise - which would be unusable in an
explanation, and explanations are a hard requirement here.

"Project" nodes are the top-level directories beneath the scan root. On the
synthetic corpus that yields College, Projects, Personal and Downloads.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

__all__ = ["TreeNode", "SemanticTree", "build_tree", "TreeReport"]

#: Node kinds, from root downward.
NODE_KINDS = ("root", "project", "folder", "file", "chunk")


@dataclass
class TreeNode:
    """One node of the semantic tree."""

    node_id: str
    kind: str
    label: str
    parent_id: str | None = None
    children: list[str] = field(default_factory=list)
    file_id: int | None = None
    rel_path: str = ""
    summary: str = ""
    summary_backend: str = ""
    depth: int = 0
    file_count: int = 0
    vector: np.ndarray | None = None

    @property
    def is_leaf(self) -> bool:
        """Whether this node has no children."""
        return not self.children


@dataclass
class TreeReport:
    """Outcome of a tree build."""

    nodes: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    summaries: int = 0
    summary_backend: str = "extractive"
    llm_fallbacks: int = 0
    duration_ms: float = 0.0

    def summary(self) -> dict[str, Any]:
        """Flat printable summary."""
        return {
            "nodes": self.nodes,
            **{f"nodes_{kind}": count for kind, count in self.by_kind.items()},
            "summaries": self.summaries,
            "summary_backend": self.summary_backend,
            "llm_fallbacks": self.llm_fallbacks,
            "duration_ms": round(self.duration_ms, 2),
        }


class SemanticTree:
    """An in-memory semantic tree with traversal helpers."""

    ROOT_ID = "root"

    def __init__(self) -> None:
        """Create a tree containing only its root."""
        self.nodes: dict[str, TreeNode] = {
            self.ROOT_ID: TreeNode(node_id=self.ROOT_ID, kind="root", label="corpus", depth=0)
        }

    # -- construction ------------------------------------------------------

    def add(self, node: TreeNode) -> TreeNode:
        """Insert a node and link it to its parent."""
        self.nodes[node.node_id] = node
        if node.parent_id and node.parent_id in self.nodes:
            parent = self.nodes[node.parent_id]
            if node.node_id not in parent.children:
                parent.children.append(node.node_id)
        return node

    # -- traversal ---------------------------------------------------------

    def get(self, node_id: str) -> TreeNode | None:
        """Look up a node by id."""
        return self.nodes.get(node_id)

    def children_of(self, node_id: str) -> list[TreeNode]:
        """Direct children of a node."""
        node = self.nodes.get(node_id)
        return [self.nodes[child] for child in node.children] if node else []

    def descendants(self, node_id: str) -> list[TreeNode]:
        """Every node beneath ``node_id``, depth-first."""
        out: list[TreeNode] = []
        stack = list(self.nodes[node_id].children) if node_id in self.nodes else []
        while stack:
            current = self.nodes[stack.pop()]
            out.append(current)
            stack.extend(current.children)
        return out

    def ancestors(self, node_id: str) -> list[TreeNode]:
        """Path from a node's parent up to the root."""
        out: list[TreeNode] = []
        node = self.nodes.get(node_id)
        while node and node.parent_id:
            parent = self.nodes.get(node.parent_id)
            if parent is None:
                break
            out.append(parent)
            node = parent
        return out

    def path_to_root(self, node_id: str) -> list[str]:
        """Labels from a node up to the root, root last."""
        node = self.nodes.get(node_id)
        if node is None:
            return []
        return [node.label] + [ancestor.label for ancestor in self.ancestors(node_id)]

    def nodes_of_kind(self, kind: str) -> list[TreeNode]:
        """All nodes of one kind."""
        return [node for node in self.nodes.values() if node.kind == kind]

    def file_nodes(self) -> list[TreeNode]:
        """All file nodes."""
        return self.nodes_of_kind("file")

    def reachable_from_root(self) -> set[str]:
        """Ids reachable from the root by following child links.

        Used by the phase's verification: every file node must be reachable, or
        the tree has an orphan and retrieval through it would silently miss
        files.
        """
        seen = {self.ROOT_ID}
        stack = [self.ROOT_ID]
        while stack:
            current = self.nodes.get(stack.pop())
            if current is None:
                continue
            for child in current.children:
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        return seen

    def orphans(self) -> list[str]:
        """Nodes not reachable from the root."""
        reachable = self.reachable_from_root()
        return sorted(set(self.nodes) - reachable)

    def stats(self) -> dict[str, int]:
        """Node counts by kind."""
        counts: dict[str, int] = {}
        for node in self.nodes.values():
            counts[node.kind] = counts.get(node.kind, 0) + 1
        return counts

    def render(self, node_id: str | None = None, max_depth: int = 3) -> list[str]:
        """Render the tree as indented lines, for CLI display."""
        lines: list[str] = []

        def walk(current_id: str, indent: int) -> None:
            node = self.nodes.get(current_id)
            if node is None or indent > max_depth:
                return
            marker = {"root": "*", "project": "+", "folder": "-", "file": " ", "chunk": "."}
            lines.append(f"{'  ' * indent}{marker.get(node.kind, ' ')} {node.label}")
            for child in node.children:
                walk(child, indent + 1)

        walk(node_id or self.ROOT_ID, 0)
        return lines


def build_tree(
    store, vectors=None, summarizer=None, *, max_chunks_per_file: int = 0
) -> tuple[SemanticTree, TreeReport]:
    """Build the semantic tree from indexed files.

    Args:
        store: The SQLite metadata store.
        vectors: Optional :class:`VectorStore`, used to give summary nodes their
            own vectors (mean of descendants) so they are retrievable.
        summarizer: Optional :class:`~contextfs.summarize.Summarizer`. If None,
            summaries are skipped and only structure is built.
        max_chunks_per_file: Cap on chunk nodes per file, 0 for unlimited.

    Returns:
        ``(tree, report)``.
    """
    started = time.perf_counter()
    tree = SemanticTree()
    report = TreeReport()

    files = store.all_files()
    file_vectors: dict[int, np.ndarray] = {}
    if vectors is not None:
        ids, matrix = vectors.document_vectors()
        file_vectors = {file_id: matrix[i] for i, file_id in enumerate(ids)}

    # --- structural skeleton ---------------------------------------------
    for row in files:
        folder = row["folder"] or ""
        parts = folder.split("/") if folder else []
        parent_id = SemanticTree.ROOT_ID

        for depth, part in enumerate(parts):
            node_id = "dir:" + "/".join(parts[: depth + 1])
            if node_id not in tree.nodes:
                tree.add(
                    TreeNode(
                        node_id=node_id,
                        kind="project" if depth == 0 else "folder",
                        label=part,
                        parent_id=parent_id,
                        rel_path="/".join(parts[: depth + 1]),
                        depth=depth + 1,
                    )
                )
            parent_id = node_id

        file_node_id = f"file:{row['id']}"
        tree.add(
            TreeNode(
                node_id=file_node_id,
                kind="file",
                label=row["name"],
                parent_id=parent_id,
                file_id=row["id"],
                rel_path=row["path"],
                depth=len(parts) + 1,
                file_count=1,
                vector=file_vectors.get(row["id"]),
            )
        )

        document = store.get_document(row["id"])
        if document is not None and document["ok"]:
            blocks = store.get_blocks(row["id"])
            if max_chunks_per_file:
                blocks = blocks[:max_chunks_per_file]
            for block in blocks:
                tree.add(
                    TreeNode(
                        node_id=f"chunk:{row['id']}:{block['block_index']}",
                        kind="chunk",
                        label=block["label"] or f"block {block['block_index']}",
                        parent_id=file_node_id,
                        file_id=row["id"],
                        rel_path=row["path"],
                        summary=block["text"][:400],
                        depth=len(parts) + 2,
                    )
                )

    # --- roll up file counts, summaries and vectors, bottom-up ------------
    _rollup(tree, store, summarizer, report)

    report.nodes = len(tree.nodes)
    report.by_kind = tree.stats()
    report.duration_ms = (time.perf_counter() - started) * 1000
    if summarizer is not None:
        report.summary_backend = summarizer.backend_name
        report.llm_fallbacks = summarizer.fallbacks
    return tree, report


def _rollup(tree: SemanticTree, store, summarizer, report: TreeReport) -> None:
    """Propagate counts, vectors and summaries from leaves to the root.

    Deepest-first so a parent's summary is built from summaries its children
    already have - the bottom-up construction RAPTOR relies on. A folder summary
    is therefore a summary *of summaries*, not a summary of concatenated raw
    text, which keeps input size bounded regardless of how large the subtree is.
    """
    ordered = sorted(tree.nodes.values(), key=lambda node: -node.depth)

    for node in ordered:
        if node.kind == "chunk":
            continue

        children = tree.children_of(node.node_id)

        if node.kind == "file":
            node.file_count = 1
            if summarizer is not None:
                document = store.get_document(node.file_id) if node.file_id else None
                text = document["text"] if document is not None else ""
                if text:
                    result = summarizer.summarize(text, title=node.label)
                    node.summary = result.text
                    node.summary_backend = result.backend
                    report.summaries += 1
            continue

        node.file_count = sum(child.file_count for child in children)

        child_vectors = [child.vector for child in children if child.vector is not None]
        if child_vectors:
            pooled = np.mean(np.vstack(child_vectors), axis=0)
            norm = np.linalg.norm(pooled)
            node.vector = (pooled / norm if norm else pooled).astype(np.float32)

        if summarizer is not None and children:
            material = "\n".join(
                child.summary for child in children if child.summary and child.kind != "chunk"
            )
            if not material:
                material = "\n".join(child.label for child in children)
            result = summarizer.summarize(material, title=node.label)
            node.summary = result.text
            node.summary_backend = result.backend
            report.summaries += 1
