"""3D relationship-graph visualisation (Phase 28).

Reads the NetworkX graph ContextFS already built and emits a **single
self-contained HTML file** with three.js inlined, then opens it. No CDN, no
network request, no local server — which is what the local-first constraint
actually requires: a visualisation that phones a CDN for its renderer is a
cloud dependency regardless of how the rest of the system behaves.

Why a generated page rather than an embedded browser widget: see log.md,
Decision 83. Briefly — Qt's WebEngine is a full Chromium (roughly 400 MB, and a
second render process at runtime), which on the target machine costs more than
the feature is worth, and it would make the "native application, not a web app"
property untrue in the one place a user would notice.

The layout is a **force-directed simulation run in the browser**, seeded from
the graph's own structure. Node colour encodes node type, edge colour encodes
relation type, and the six relation types can be toggled independently — which
is the point of the visualisation rather than decoration: switching off
``semantic`` and leaving ``activity`` on shows, directly, that files cluster by
when they were worked on and not only by what they say.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contextfs.theme import EDGE_GREYS, NODE_GLYPHS, NODE_GREYS

__all__ = ["build_visualisation", "graph_payload", "VENDOR_DIR"]

VENDOR_DIR = Path(__file__).parent / "vendor"

#: Grey per node type, from the shared design system.
NODE_COLOURS = dict(NODE_GREYS)

#: Grey per edge type, and whether it starts visible.
#:
#: `structural` starts hidden and is nearly black on purpose: it is by far the
#: densest relation (326 of 597 edges on the demo corpus) and drawing it over
#: everything else buries the relations a viewer actually came to look at.
#: `temporal` starts hidden for the same reason, less severely.
EDGE_STYLES = {
    name: {
        "colour": EDGE_GREYS[name],
        "on": name not in ("structural", "temporal"),
        "glyph": {
            "semantic": "─",
            "entity": "┈",
            "structural": "·",
            "duplicate": "═",
            "temporal": "┄",
            "activity": "━",
        }[name],
    }
    for name in ("semantic", "entity", "structural", "duplicate", "temporal", "activity")
}


def _node_kind(node_id: str, data: dict[str, Any]) -> str:
    """Classify a node from its id prefix, falling back to its attributes."""
    for prefix in ("file", "session", "date", "folder", "project"):
        if node_id.startswith(f"{prefix}:"):
            return prefix
    kind = str(data.get("kind") or data.get("type") or "file")
    return kind if kind in NODE_COLOURS else "file"


def graph_payload(graph, store=None, max_nodes: int = 1200) -> dict[str, Any]:
    """Convert a NetworkX graph into the JSON the page renders.

    ``max_nodes`` is a real cap, not a formality: a force simulation over more
    than a few thousand nodes stops being interactive on integrated graphics.
    When the cap bites, the nodes kept are those with the highest degree — the
    structurally important ones — and the page is told how many were dropped so
    it can say so on screen rather than quietly showing a partial graph.
    """
    if graph is None:
        raise ValueError("no relationship graph found - run a scan first")

    degrees = dict(graph.degree())
    kept = sorted(graph.nodes(), key=lambda n: -degrees.get(n, 0))[:max_nodes]
    keep = set(kept)
    dropped = graph.number_of_nodes() - len(keep)

    labels: dict[str, str] = {}
    if store is not None:
        try:
            labels = {f"file:{fid}": path for fid, path in store.path_by_file_id().items()}
        except Exception:  # noqa: BLE001 - labels are cosmetic, never fatal
            labels = {}

    index = {node: position for position, node in enumerate(kept)}
    nodes = []
    for node in kept:
        data = graph.nodes[node]
        kind = _node_kind(node, data)
        label = labels.get(node) or str(data.get("label") or node)
        nodes.append(
            {
                "id": node,
                "label": label,
                "short": Path(label).name if kind == "file" else label,
                "kind": kind,
                "degree": degrees.get(node, 0),
            }
        )

    edges = []
    for source, target, data in graph.edges(data=True):
        if source not in keep or target not in keep:
            continue
        edges.append(
            {
                "s": index[source],
                "t": index[target],
                "type": str(data.get("type", "semantic")),
                "w": round(float(data.get("weight", 0.5)), 3),
            }
        )

    return {
        "nodes": nodes,
        "edges": edges,
        "dropped": dropped,
        "nodeColours": NODE_COLOURS,
        "nodeGlyphs": NODE_GLYPHS,
        "edgeStyles": EDGE_STYLES,
    }


def build_visualisation(config, out: Path | None = None) -> Path:
    """Build the visualisation for an index and return the written file."""
    from contextfs.graph import load_graph
    from contextfs.store import Store

    if not config.graph_file.is_file():
        raise FileNotFoundError(
            f"no relationship graph at {config.graph_file} - run `contextfs scan` first"
        )
    three = VENDOR_DIR / "three.module.js"
    if not three.is_file():
        raise FileNotFoundError(
            f"three.js is not vendored at {three}. It ships with ContextFS; "
            "re-install the package."
        )

    graph = load_graph(config.graph_file)
    with Store(config.db_path, read_only=True) as store:
        payload = graph_payload(graph, store)

    config.ensure_data_dir()
    target = out or (config.paths.data_dir / "graph3d.html")
    target.write_text(render_html(payload, three.read_text(encoding="utf-8")), encoding="utf-8")
    return target


def render_html(payload: dict[str, Any], three_source: str) -> str:
    """Inline three.js and the graph data into the page template.

    Both substitutions are done on comment markers (``/*__THREE__*/``) rather
    than by string formatting, so the template stays a valid, editable HTML file
    that can be opened directly while working on it.
    """
    template = (VENDOR_DIR / "graph3d.template.html").read_text(encoding="utf-8")
    # three.js is an ES module ending in `export { ... }`, which is legal inside
    # an inline `<script type="module">` and leaves every class in scope for the
    # code that follows it. That is what makes a genuinely self-contained page
    # possible: no import, therefore no fetch, therefore no server.
    return template.replace("/*__THREE__*/", three_source).replace(
        "/*__DATA__*/", json.dumps(payload, separators=(",", ":"))
    )
