"""The redesigned window (log.md Phase 36): it still searches, shows rows and details, and themes.

The window is built off-screen on a small index with the launch update disabled. These tests cover
presentation only; ranking parity with the CLI and the engine is ``test_parity``.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from contextfs.engine import Engine, default_model_dir  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (default_model_dir() / "model_fp32.onnx").exists(), reason="ONNX model not exported"
)


@pytest.fixture(scope="module")
def window(tmp_path_factory):
    from PySide6.QtWidgets import QApplication

    from contextfs.app import gui
    from contextfs.app.settings import Settings
    from contextfs.app.theme import theme

    base = tmp_path_factory.mktemp("window")
    os.environ["CONTEXTFS_HOME"] = str(base / "home")
    corpus = base / "corpus"
    for rel, text in {"College/DBMS/normalization_notes.md": "BCNF and 3NF decomposition notes.",
                      "Personal/budget.csv": "month,rent\nmarch,9000\n",
                      "Personal/recipe_biryani.txt": "Biryani: rice, saffron, onions, mint."}.items():
        (corpus / rel).parent.mkdir(parents=True, exist_ok=True)
        (corpus / rel).write_text(text, encoding="utf-8")
    with Engine.open(base / "index", mode="write") as eng:
        eng.update([corpus])
    app = QApplication.instance() or QApplication([])
    theme.apply(app, "light")
    original = gui.MainWindow.start_update
    gui.MainWindow.start_update = lambda self: None  # no indexer child in a unit test
    win = gui.MainWindow(Settings(roots=[str(corpus)], index_dir=str(base / "index"), first_run_done=True))
    yield app, win, gui
    win.close()
    gui.MainWindow.start_update = original
    gui.SearchJob.engine = None


def _wait(app, cond, seconds: float = 30) -> None:
    t = time.perf_counter()
    while not cond() and time.perf_counter() - t < seconds:
        app.processEvents()
        time.sleep(0.02)


def test_search_fills_rows_and_details(window):
    app, win, _ = window
    assert win.left.currentIndex() == 0  # the start page before any search
    win.box.setText("biryani recipe")
    win.search()
    _wait(app, lambda: win.current is not None)
    assert win.left.currentIndex() == 1 and win.results.count() == len(win.current[0].results) > 0
    from contextfs.app.gui import ROW

    first = win.results.item(0).data(ROW)
    assert first["name"] == "recipe_biryani.txt" and first["folder"] == "Personal" and first["why"]
    assert win.right.currentIndex() == 1 and win.open_btn.isEnabled() and win.folder_btn.isEnabled()
    assert "Dates for this file" in win.details.toPlainText()
    assert win.d_path.text().endswith("recipe_biryani.txt")


def test_the_switches_still_send_the_same_overrides(window):
    app, win, gui = window
    sent = []
    real = gui.SearchJob.__init__

    def spy(self, index_dir, query, overrides, seq, reopen):
        sent.append(overrides)
        real(self, index_dir, query, overrides, seq, reopen)

    gui.SearchJob.__init__ = spy
    try:
        win.box.setText("cricket")
        win.use_time.setChecked(False)  # each toggle searches again
        win.use_kind.setChecked(False)
        win.use_time.setChecked(True)
        win.use_kind.setChecked(True)
        _wait(app, lambda: not win._live_jobs)
    finally:
        gui.SearchJob.__init__ = real
    assert sent == [(("time", "removed"),), (("time", "removed"), ("kind", "removed")), (("kind", "removed"),), ()]


def test_light_and_dark_restyle_without_errors(window):
    app, win, _ = window
    from contextfs.app.theme import DARK, LIGHT, theme

    for choice, pal in (("dark", DARK), ("light", LIGHT), ("system", None)):
        theme.apply(app, choice)
        assert pal is None or theme.c == pal
        win.grab()  # paints every row with the delegate
    assert app.styleSheet()


def test_the_hardening_suite_can_still_drive_the_window(window):
    _, win, _ = window
    for name in ("box", "search", "seq", "proc", "status", "show_results", "restarts_total", "results", "close"):
        assert hasattr(win, name), name
    assert hasattr(win.status, "currentMessage")


def test_dates_read_as_words():
    from contextfs.app.gui import when_short

    day = 1_755_000_000
    assert when_short({"lo_utc": day, "hi_utc": day, "point_utc": day, "confidence": "sure"}).count(" ") == 2
    assert when_short({"lo_utc": day, "hi_utc": day, "point_utc": day, "confidence": "unknown"}).endswith("(uncertain)")
    assert when_short({"lo_utc": day, "hi_utc": day + 400 * 86400, "point_utc": day, "confidence": "unknown"}).endswith(
        "estimated")
