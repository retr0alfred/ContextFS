"""Phase 27-28 tests: desktop application scaffolding and the 3D visualisation.

Split by cost. The visualisation is pure data-to-HTML transformation and is
tested directly and cheaply. The Qt layer is tested for the properties that
would actually break the application — that nothing heavy is imported at module
scope, that the worker boundary never lets an exception escape, and that the
window builds — using the offscreen platform plugin.

What is deliberately *not* here: pixel or layout assertions. They break on font
and DPI differences without catching real defects, and this project's honesty
rule cuts both ways — a test that passes for the wrong reason is worse than an
absent one. The end-to-end behaviour (load, query, explain, export) is covered
by the scripted run recorded in log.md, Phase 27.
"""

from __future__ import annotations

import os

import networkx as nx
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from contextfs.gui.visualise import (  # noqa: E402
    EDGE_STYLES,
    NODE_COLOURS,
    graph_payload,
    render_html,
)

pyside = pytest.importorskip("PySide6", reason="the gui extra is not installed")


# ---------------------------------------------------------------------------
# Visualisation payload
# ---------------------------------------------------------------------------


def _graph():
    """A small graph with every node kind and two edge types."""
    graph = nx.MultiDiGraph()
    for a, b in ((1, 2), (2, 3), (1, 3)):
        graph.add_edge(f"file:{a}", f"file:{b}", type="semantic", weight=0.6)
    graph.add_edge("session:s1", "file:1", type="activity", weight=1.0)
    graph.add_edge("date:2025-11-10", "file:2", type="temporal", weight=0.9)
    return graph


def test_payload_indexes_edges_by_position_not_by_id():
    """Edges must reference array positions; the renderer indexes into arrays."""
    payload = graph_payload(_graph())
    count = len(payload["nodes"])
    for edge in payload["edges"]:
        assert 0 <= edge["s"] < count
        assert 0 <= edge["t"] < count


def test_payload_classifies_every_node_kind():
    kinds = {node["kind"] for node in graph_payload(_graph())["nodes"]}
    assert kinds == {"file", "session", "date"}
    assert all(kind in NODE_COLOURS for kind in kinds)


def test_payload_caps_nodes_and_reports_the_drop():
    """A silent truncation would show a partial graph as if it were complete."""
    graph = nx.MultiDiGraph()
    for i in range(60):
        graph.add_edge(f"file:{i}", f"file:{(i + 1) % 60}", type="semantic", weight=0.5)
    payload = graph_payload(graph, max_nodes=20)
    assert len(payload["nodes"]) == 20
    assert payload["dropped"] == 40


def test_payload_drops_edges_whose_endpoints_were_cut():
    """A dangling edge index would read past the end of the position array."""
    graph = nx.MultiDiGraph()
    for i in range(30):
        graph.add_edge(f"file:{i}", f"file:{(i + 1) % 30}", type="semantic", weight=0.5)
    payload = graph_payload(graph, max_nodes=10)
    count = len(payload["nodes"])
    assert all(e["s"] < count and e["t"] < count for e in payload["edges"])


def test_payload_rejects_a_missing_graph():
    with pytest.raises(ValueError, match="no relationship graph"):
        graph_payload(None)


def test_every_edge_type_has_a_style():
    """A type without a style would render invisibly and toggle nothing."""
    from contextfs.graph import EDGE_TYPES

    assert set(EDGE_TYPES) <= set(EDGE_STYLES)


# ---------------------------------------------------------------------------
# Rendered page
# ---------------------------------------------------------------------------


def test_rendered_page_is_self_contained():
    """The local-first rule applies to the visualisation too.

    A page that fetches its renderer from a CDN is a cloud dependency however
    local the rest of the system is, so this asserts there is nothing to fetch.
    """
    html = render_html(graph_payload(_graph()), "const REVISION='test';")
    for forbidden in ("https://", "cdn.", "unpkg", "jsdelivr", "import("):
        assert forbidden not in html, f"page references {forbidden}"
    # The one permitted absolute URL is the XML namespace, which is an
    # identifier rather than something the browser fetches.
    external = [line for line in html.splitlines() if "http://" in line and "w3.org" not in line]
    assert not external, external[:3]


def test_rendered_page_inlines_three_and_the_data():
    html = render_html(graph_payload(_graph()), "const REVISION='0.160.0';")
    assert "const REVISION='0.160.0';" in html
    assert '"nodes"' in html
    assert "/*__THREE__*/" not in html
    assert "/*__DATA__*/" not in html


def test_vendored_three_is_present_and_is_really_three():
    from contextfs.gui.visualise import VENDOR_DIR

    source = (VENDOR_DIR / "three.module.js").read_text(encoding="utf-8")
    assert "REVISION" in source
    assert "WebGLRenderer" in source
    assert "Three.js Authors" in source, "licence notice must be preserved"


# ---------------------------------------------------------------------------
# Qt layer
# ---------------------------------------------------------------------------


def test_importing_the_cli_does_not_pull_in_qt():
    """Startup cost rule: the CLI must not pay for PySide6 it may never use."""
    import subprocess
    import sys
    from pathlib import Path

    code = (
        "import sys, contextfs.cli.main; "
        "print('PySide6' in sys.modules or 'contextfs.gui.app' in sys.modules)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "False", "importing the CLI loaded Qt"


def test_worker_reports_failure_instead_of_raising(qapp):
    """An exception crossing a Qt slot boundary can take the process down."""
    from contextfs.gui.workers import JobRunner

    runner = JobRunner()
    seen = {}

    def boom():
        raise RuntimeError("deliberate")

    runner.submit(
        boom,
        on_done=lambda r: seen.setdefault("done", r),
        on_error=lambda m, d: seen.setdefault("error", (m, d)),
    )
    runner.wait(10_000)
    _drain(qapp)
    assert "done" not in seen
    assert seen["error"][0] == "deliberate"
    assert "RuntimeError" in seen["error"][1]


def test_worker_returns_values_from_jobs_without_a_progress_channel(qapp):
    from contextfs.gui.workers import JobRunner

    runner = JobRunner()
    seen = {}
    runner.submit(lambda: 21 * 2, on_done=lambda r: seen.setdefault("value", r))
    runner.wait(10_000)
    _drain(qapp)
    assert seen["value"] == 42


def test_job_runner_is_single_threaded(qapp):
    """Serialisation is what makes the shared cross-thread index handle safe."""
    from contextfs.gui.workers import JobRunner

    assert JobRunner().pool.maxThreadCount() == 1


def test_store_rejects_cross_thread_use_unless_asked(tmp_path):
    """The GUI's relaxation must be opt-in, never the default."""
    import sqlite3
    import threading

    from contextfs.store import Store

    strict = Store(tmp_path / "a.db")
    errors = []

    def touch(store):
        try:
            store.file_count()
        except sqlite3.ProgrammingError as exc:
            errors.append(exc)

    thread = threading.Thread(target=touch, args=(strict,))
    thread.start()
    thread.join()
    strict.close()
    assert errors, "default Store must not be usable from another thread"

    relaxed = Store(tmp_path / "b.db", cross_thread=True)
    errors.clear()
    thread = threading.Thread(target=touch, args=(relaxed,))
    thread.start()
    thread.join()
    relaxed.close()
    assert not errors, "cross_thread=True must permit it"


def test_window_builds_with_every_tab(qapp, tmp_path):
    from contextfs.config import load_config
    from contextfs.gui.app import ContextFSWindow

    config = load_config(None, root=tmp_path, data_dir=tmp_path / "derived")
    window = ContextFSWindow(config)
    try:
        assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == [
            "Search",
            "Insights",
            "Index",
        ]
        # No index exists, so the window must say so rather than hang or crash.
        # Called directly rather than waiting on the startup timer: a test that
        # depends on a 50 ms QTimer is a flaky test on a loaded machine.
        window.load_index()
        assert "scan" in window.statusBar().currentMessage().lower()
    finally:
        window.close()


def test_search_with_no_index_does_not_raise(qapp, tmp_path):
    from contextfs.config import load_config
    from contextfs.gui.app import ContextFSWindow

    config = load_config(None, root=tmp_path, data_dir=tmp_path / "derived")
    window = ContextFSWindow(config)
    try:
        window.load_index()
        window.search_tab.box.setText("anything")
        window.search_tab.run_search()  # must be a no-op with a message
        assert window.search_tab.table.rowCount() == 0
    finally:
        window.close()


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _drain(app):
    """Let queued cross-thread signals be delivered."""
    for _ in range(50):
        app.processEvents()


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole session; Qt allows only one."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
