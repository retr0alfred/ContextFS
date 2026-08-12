"""ContextFS desktop application (Phase 27).

A native Windows application, not a web page in a frame. Every widget is a Qt
widget; there is no embedded browser, no local HTTP server, and no JavaScript in
the application itself. See log.md, Decision 82, for why that was chosen over
the Electron/webview shape that a "desktop app" often means in practice.

The window is organised around the project's actual claim rather than around its
features. Search results and the *reasons* they were returned sit side by side,
permanently, because "every result explains itself" is the thing this system
does that a conventional search box does not — and a feature you have to click a
menu to find is a feature the user will not see.
"""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from contextfs import __version__
from contextfs.gui.theme import MUTED, SIGNAL_COLOURS, STYLESHEET
from contextfs.gui.workers import JobRunner, RetrievalService

__all__ = ["ContextFSWindow", "launch"]


def _label(text, *, name="", bold=False, size=None):
    """Build a styled QLabel in one line."""
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    if bold or size:
        font = widget.font()
        if bold:
            font.setBold(True)
        if size:
            font.setPointSize(size)
        widget.setFont(font)
    widget.setWordWrap(True)
    return widget


class SearchTab(QWidget):
    """Query box, ranked results, and the explanation for the selected result."""

    def __init__(self, window: ContextFSWindow) -> None:
        """Build the search surface."""
        super().__init__()
        self.window = window
        self._response = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        layout.addWidget(
            _label(
                "Describe what you remember — a deadline, a work session, who was "
                "involved — not the filename.",
                name="hint",
            )
        )

        row = QHBoxLayout()
        self.box = QLineEdit()
        self.box.setPlaceholderText("the PDF I studied before my ML exam")
        self.box.returnPressed.connect(self.run_search)
        self.button = QPushButton("Search")
        self.button.setObjectName("primary")
        self.button.clicked.connect(self.run_search)
        self.compare = QCheckBox("Compare with plain semantic search")
        row.addWidget(self.box, 1)
        row.addWidget(self.button)
        layout.addLayout(row)
        layout.addWidget(self.compare)

        split = QSplitter(Qt.Horizontal)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["#", "Score", "File"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 70)
        self.table.itemSelectionChanged.connect(self._show_explanation)
        self.table.itemDoubleClicked.connect(self._reveal)
        split.addWidget(self.table)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(_label("Why this result", bold=True))
        self.explanation = QTextEdit()
        self.explanation.setReadOnly(True)
        self.explanation.setHtml(
            f"<p style='color:{MUTED}'>Select a result to see the reasoning "
            "that produced it.</p>"
        )
        right_layout.addWidget(self.explanation, 1)

        feedback_row = QHBoxLayout()
        self.right_button = QPushButton("✓ This was the one")
        self.right_button.clicked.connect(lambda: self._feedback("pick"))
        self.wrong_button = QPushButton("✗ Not this")
        self.wrong_button.clicked.connect(lambda: self._feedback("reject"))
        self.open_button = QPushButton("Open file")
        self.open_button.clicked.connect(self._reveal)
        for widget in (self.right_button, self.wrong_button, self.open_button):
            widget.setEnabled(False)
            feedback_row.addWidget(widget)
        right_layout.addLayout(feedback_row)
        split.addWidget(right)

        split.setSizes([560, 460])
        layout.addWidget(split, 1)

        self.summary = _label("", name="hint")
        layout.addWidget(self.summary)

    # -- actions -----------------------------------------------------------

    def run_search(self) -> None:
        """Dispatch the query to a worker thread."""
        text = self.box.text().strip()
        if not text:
            return
        if not self.window.service.ready:
            self.window.status("Index still loading…")
            return
        self.button.setEnabled(False)
        self.window.busy(True)
        self.window.status(f"searching: {text}")
        self.window.runner.submit(
            self.window.service.search,
            text,
            10,
            self.compare.isChecked(),
            on_done=self._results_ready,
            on_error=self.window.report_error,
        )

    def _results_ready(self, payload) -> None:
        """Populate the table from a completed search."""
        hybrid, flat = payload
        self._response = hybrid
        self.button.setEnabled(True)
        self.window.busy(False)

        self.table.setRowCount(0)
        for result in hybrid.results:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(result.rank)))
            score = QTableWidgetItem(f"{result.score:.3f}")
            self.table.setItem(row, 1, score)
            item = QTableWidgetItem(result.path)
            item.setData(Qt.UserRole, result.rank)
            # Tint the row by whichever signal contributed most. The colour is
            # the point: it makes "this was found by activity, not by text"
            # visible before the user reads anything.
            contributions = result.explanation.contributions or {}
            if contributions:
                top = max(contributions, key=contributions.get)
                if contributions[top] > 0:
                    item.setForeground(QColor(SIGNAL_COLOURS.get(top, "#e6e9f0")))
            self.table.setItem(row, 2, item)

        if hybrid.results:
            self.table.selectRow(0)

        parts = [
            f"{len(hybrid.results)} results",
            f"{hybrid.latency_ms:.0f} ms",
            f"{hybrid.expanded_nodes} candidates considered",
        ]
        if flat is not None:
            overlap = len(set(hybrid.paths[:5]) & set(flat.paths[:5]))
            parts.append(
                f"semantic-only baseline agreed on {overlap}/5 of the top 5 "
                f"({flat.latency_ms:.0f} ms)"
            )
        self.summary.setText(" · ".join(parts))
        self.window.status("ready")

    def _selected(self):
        """The currently selected result, or None."""
        rows = {index.row() for index in self.table.selectedIndexes()}
        if not rows or self._response is None:
            return None
        rank = self.table.item(min(rows), 2).data(Qt.UserRole)
        for result in self._response.results:
            if result.rank == rank:
                return result
        return None

    def _show_explanation(self) -> None:
        """Render the selected result's reasoning as formatted HTML."""
        result = self._selected()
        for widget in (self.right_button, self.wrong_button, self.open_button):
            widget.setEnabled(result is not None)
        if result is None:
            return

        explanation = result.explanation
        html = [f"<h3 style='margin:0 0 4px 0'>{Path(result.path).name}</h3>"]
        html.append(f"<p style='color:{MUTED};margin:0 0 12px 0'>{result.path}</p>")

        html.append("<table width='100%' cellpadding='4' style='margin-bottom:12px'>")
        for signal, value in (explanation.signal_scores or {}).items():
            weight = (explanation.signal_weights or {}).get(signal, 0.0)
            contribution = (explanation.contributions or {}).get(signal, 0.0)
            colour = SIGNAL_COLOURS.get(signal, "#e6e9f0")
            bar = int(round(value * 100))
            html.append(
                f"<tr><td style='color:{colour};font-weight:600'>{signal}</td>"
                f"<td align='right'>{value:.3f}</td>"
                f"<td><div style='background:{colour};height:8px;width:{bar}%'></div></td>"
                f"<td align='right' style='color:{MUTED}'>×{weight:.2f} = "
                f"{contribution:.3f}</td></tr>"
            )
        html.append("</table>")

        html.append(f"<p style='font-weight:600;margin-bottom:4px'>Score {result.score:.3f}</p>")
        reasons = explanation.reasons()
        if reasons:
            html.append("<ul style='margin-top:4px'>")
            for reason in reasons:
                html.append(f"<li style='margin-bottom:6px'>{reason}</li>")
            html.append("</ul>")
        else:
            html.append(f"<p style='color:{MUTED}'>No reasons recorded.</p>")

        self.explanation.setHtml("".join(html))

    def _feedback(self, event: str) -> None:
        """Record a pick or a rejection for the current query."""
        result = self._selected()
        if result is None or self._response is None:
            return
        from contextfs.store import Store

        try:
            with Store(self.window.config.db_path) as store:
                store.record_feedback(
                    self._response.query, result.file_id, result.path, event=event
                )
        except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
            self.window.report_error(str(exc), "")
            return
        verb = "Noted" if event == "pick" else "Noted as wrong"
        self.window.status(f"{verb}: {Path(result.path).name}")

    def _reveal(self) -> None:
        """Open the selected file with its default application."""
        result = self._selected()
        if result is None:
            return
        target = Path(self.window.config.paths.root) / result.path
        if not target.exists():
            self.window.status(f"file no longer on disk: {target}")
            return
        webbrowser.open(target.as_uri())


class InsightsTab(QWidget):
    """Digest, duplicates and project lifecycle, in one read-only surface."""

    def __init__(self, window: ContextFSWindow) -> None:
        """Build the insights surface."""
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        row = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        row.addWidget(self.refresh_button)
        row.addStretch(1)
        row.addWidget(_label("ContextFS never deletes, moves or renames anything.", name="hint"))
        layout.addLayout(row)

        self.view = QTextEdit()
        self.view.setReadOnly(True)
        layout.addWidget(self.view, 1)

    def refresh(self) -> None:
        """Recompute the insight surfaces off-thread."""
        self.refresh_button.setEnabled(False)
        self.window.busy(True)
        self.window.runner.submit(
            self._compute, on_done=self._render, on_error=self.window.report_error
        )

    def _compute(self, progress=None):
        """Read the index and build the three reports."""
        _ = progress
        from contextfs.graph import load_graph
        from contextfs.insights import digest, near_duplicates, projects
        from contextfs.store import Store

        cfg = self.window.config
        graph = load_graph(cfg.graph_file) if cfg.graph_file.is_file() else None
        with Store(cfg.db_path, read_only=True) as store:
            return (
                digest(store, graph).as_dict(),
                [g.as_dict() for g in near_duplicates(store, graph)],
                [p.as_dict() for p in projects(store)],
            )

    def _render(self, payload) -> None:
        """Format the three reports as HTML."""
        report, duplicates, found = payload
        self.refresh_button.setEnabled(True)
        self.window.busy(False)

        html = [
            f"<h2>{report['files']} files · {report['bytes'] / 1024 / 1024:.1f} MB</h2>",
            "<h3>By type</h3><table width='60%' cellpadding='4'>",
        ]
        for entry in report["by_extension"][:10]:
            html.append(
                f"<tr><td>{entry['ext']}</td><td align='right'>{entry['files']}</td>"
                f"<td align='right' style='color:{MUTED}'>{entry['bytes'] / 1024:.0f} KB</td></tr>"
            )
        html.append("</table><h3>By age</h3><table width='60%' cellpadding='4'>")
        for label, count in report["by_age"].items():
            html.append(f"<tr><td>{label}</td><td align='right'>{count}</td></tr>")
        html.append("</table>")

        stage_colours = {
            "upcoming": SIGNAL_COLOURS["timeline"],
            "active": "#34d399",
            "dormant": SIGNAL_COLOURS["activity"],
            "finished": MUTED,
        }
        html.append("<h3>Projects</h3>")
        if found:
            html.append("<table width='100%' cellpadding='4'>")
            for project in found:
                colour = stage_colours.get(project["stage"], MUTED)
                html.append(
                    f"<tr><td style='color:{colour};font-weight:600'>{project['stage']}</td>"
                    f"<td>{project['folder']}</td>"
                    f"<td align='right'>{project['files']} files</td>"
                    f"<td style='color:{MUTED}'>{project['reason']}</td></tr>"
                )
            html.append("</table>")
        else:
            html.append(f"<p style='color:{MUTED}'>No multi-file folders indexed.</p>")

        html.append("<h3>Near-duplicates</h3>")
        if duplicates:
            for index, group in enumerate(duplicates, start=1):
                html.append(
                    f"<p><b>Group {index}</b> — similarity {group['similarity']:.2f}, "
                    f"{group['wasted_bytes'] / 1024:.0f} KB redundant</p><ul>"
                )
                for path in group["members"]:
                    tag = (
                        "<span style='color:#34d399'>keep</span>"
                        if path == group["keeper"]
                        else f"<span style='color:{MUTED}'>dup</span>"
                    )
                    html.append(f"<li>{tag} &nbsp; {path}</li>")
                html.append("</ul>")
        else:
            html.append(f"<p style='color:{MUTED}'>None found.</p>")

        self.view.setHtml("".join(html))
        self.window.status("insights refreshed")


class IndexTab(QWidget):
    """Index status, and the button that rebuilds it."""

    def __init__(self, window: ContextFSWindow) -> None:
        """Build the index surface."""
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        self.root_label = _label("", name="hint")
        layout.addWidget(self.root_label)

        row = QHBoxLayout()
        self.choose_button = QPushButton("Choose folder…")
        self.choose_button.clicked.connect(self.choose_root)
        self.scan_button = QPushButton("Scan / update index")
        self.scan_button.setObjectName("primary")
        self.scan_button.clicked.connect(self.run_scan)
        self.viz_button = QPushButton("Open 3D graph")
        self.viz_button.clicked.connect(self.window.open_visualisation)
        row.addWidget(self.choose_button)
        row.addWidget(self.scan_button)
        row.addWidget(self.viz_button)
        row.addStretch(1)
        layout.addLayout(row)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 10))
        layout.addWidget(self.log, 1)

        layout.addWidget(
            _label(
                "ContextFS is read-only on the folder above. It never writes, renames, "
                "moves or deletes anything there; all derived data lives in its own "
                "data directory.",
                name="hint",
            )
        )
        self.refresh_labels()

    def refresh_labels(self) -> None:
        """Show the current root and data directory."""
        cfg = self.window.config
        self.root_label.setText(f"Indexing: {cfg.paths.root}\nDerived data: {cfg.paths.data_dir}")

    def choose_root(self) -> None:
        """Pick a different folder to index."""
        chosen = QFileDialog.getExistingDirectory(self, "Choose a folder to index")
        if not chosen:
            return
        self.window.set_root(Path(chosen))
        self.refresh_labels()
        self.append(f"root changed to {chosen} — run a scan to index it")

    def append(self, line: str) -> None:
        """Add a line to the scan log."""
        self.log.append(line)

    def run_scan(self) -> None:
        """Run the full indexing pipeline on a worker thread."""
        self.scan_button.setEnabled(False)
        self.window.busy(True)
        self.log.clear()
        self.append("starting scan…")
        self.window.runner.submit(
            self._scan,
            on_done=self._scan_done,
            on_error=self._scan_failed,
            on_progress=self.append,
        )

    def _scan(self, progress=None):
        """Drive the pipeline, reusing the CLI's own stage helpers.

        Calling the CLI helpers rather than reimplementing the pipeline is
        deliberate: two copies of the indexing order would drift, and the GUI
        producing a subtly different index from the CLI would be a bug nobody
        would find until the numbers disagreed.
        """

        def say(message):
            if progress:
                progress(message)

        from contextfs.cli import main as cli
        from contextfs.scanner import Scanner
        from contextfs.store import Store

        cfg = self.window.config
        cfg.ensure_data_dir()
        with Store(cfg.db_path) as store:
            say("scanning files…")
            result = Scanner(cfg).scan(store)
            say(
                f"  {result.seen} seen · {len(result.new)} new · "
                f"{len(result.modified)} modified · {len(result.deleted)} deleted"
            )
            if result.deleted:
                cli._forget_deleted(store, cfg, result.deleted)
                say(f"  purged {len(result.deleted)} deleted file(s) from every store")

            say("extracting content…")
            extraction = cli._run_extraction(store, cfg)
            say(f"  {len(extraction.documents)} document(s) extracted")

            say("extracting entities…")
            entities = cli._run_entities(store, cfg)
            say(f"  {entities['entities']} entities over {entities['files']} file(s)")

            say("embedding…")
            embeddings = cli._run_embeddings(store, cfg)
            say(f"  {embeddings.chunks} chunk(s) over {embeddings.files} file(s)")

            say("classifying dates…")
            dates = cli._run_date_classification(store, cfg)
            say(f"  {dates['meaningful']} meaningful / {dates['incidental']} incidental")

            say("reconstructing activity sessions…")
            sessions = cli._run_sessions(store, cfg)
            say(f"  {len(sessions.sessions)} session(s)")

            say("building semantic tree…")
            tree = cli._run_tree(store, cfg)
            say(f"  {tree.nodes} node(s)")

            say("building relationship graph…")
            graph = cli._run_graph(store, cfg)
            say(f"  {graph.nodes} node(s), {graph.edges} edge(s)")

            return {"files": result.seen}

    def _scan_done(self, payload) -> None:
        """Re-warm the retrieval service after a successful scan."""
        self.scan_button.setEnabled(True)
        self.window.busy(False)
        self.append(f"\ndone — {payload['files']} file(s) indexed")
        self.append("reloading the search index…")
        self.window.load_index()

    def _scan_failed(self, message: str, detail: str) -> None:
        """Report a scan failure in the log as well as the dialog."""
        self.scan_button.setEnabled(True)
        self.window.busy(False)
        self.append(f"\nFAILED: {message}")
        self.window.report_error(message, detail)


class ContextFSWindow(QMainWindow):
    """The application window."""

    def __init__(self, config) -> None:
        """Build the window against a resolved configuration."""
        super().__init__()
        self.config = config
        self.runner = JobRunner()
        self.service = RetrievalService(config)

        self.setWindowTitle(f"ContextFS {__version__}")
        self.resize(1180, 760)
        self.setStyleSheet(STYLESHEET)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 0)
        header_layout.addWidget(_label("ContextFS", name="title"))
        header_layout.addWidget(_label("find files by what you remember", name="hint"))
        header_layout.addStretch(1)
        layout.addWidget(header)

        self.tabs = QTabWidget()
        self.search_tab = SearchTab(self)
        self.insights_tab = InsightsTab(self)
        self.index_tab = IndexTab(self)
        self.tabs.addTab(self.search_tab, "Search")
        self.tabs.addTab(self.insights_tab, "Insights")
        self.tabs.addTab(self.index_tab, "Index")
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setMaximumWidth(160)
        self.statusBar().addPermanentWidget(self.progress)
        self.status("starting…")

        self._build_menu()
        QTimer.singleShot(50, self.load_index)

    def _build_menu(self) -> None:
        """Menu bar with the few genuinely global actions."""
        file_menu = self.menuBar().addMenu("&File")
        for text, shortcut, handler in (
            ("&Scan / update index", "Ctrl+R", self.index_tab.run_scan),
            ("Open &3D graph", "Ctrl+G", self.open_visualisation),
        ):
            action = QAction(text, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(handler)
            file_menu.addAction(action)
        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("&About ContextFS", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)

    def _about(self) -> None:
        """Say what the program is, including what it does not do."""
        QMessageBox.information(
            self,
            "About ContextFS",
            f"ContextFS {__version__}\n\n"
            "Context-aware, time-intelligent personal file retrieval.\n\n"
            "Local-first: no cloud calls, no telemetry, no background service.\n"
            "Read-only: your files are never modified, moved or deleted.\n"
            "Explainable: every result carries the reasoning that produced it.",
        )

    # -- plumbing ----------------------------------------------------------

    def status(self, message: str) -> None:
        """Set the status bar text."""
        self.statusBar().showMessage(message)

    def busy(self, active: bool) -> None:
        """Show or hide the indeterminate progress indicator."""
        self.progress.setVisible(active)

    def report_error(self, message: str, detail: str) -> None:
        """Surface a worker failure without taking the window down."""
        self.busy(False)
        self.status(f"error: {message}")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("ContextFS")
        box.setText(message)
        if detail:
            box.setDetailedText(detail)
        box.exec()

    def set_root(self, root: Path) -> None:
        """Point the application at a different folder."""
        from contextfs.config import load_config

        self.config = load_config(
            self.config.source_file, root=root, data_dir=self.config.paths.data_dir
        )
        self.service.close()
        self.service = RetrievalService(self.config)

    def load_index(self) -> None:
        """Load models and open the index, off the GUI thread."""
        if not self.config.db_path.is_file():
            self.status("no index yet — open the Index tab and run a scan")
            self.tabs.setCurrentWidget(self.index_tab)
            return
        self.busy(True)
        self.status("loading index…")
        self.service = RetrievalService(self.config)
        self.runner.submit(
            self.service.load,
            on_done=self._index_ready,
            on_error=self.report_error,
            on_progress=self.status,
        )

    def _index_ready(self, payload) -> None:
        """Report load time honestly — it is a real cost, paid once."""
        self.busy(False)
        self.status(
            f"ready — {payload['files']} files indexed, "
            f"models loaded in {payload['seconds']:.1f}s "
            "(paid once; every query after this is warm)"
        )
        self.search_tab.box.setFocus()

    def open_visualisation(self) -> None:
        """Build and open the 3D relationship-graph visualisation."""
        try:
            from contextfs.gui.visualise import build_visualisation

            target = build_visualisation(self.config)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self.report_error(str(exc), "")
            return
        webbrowser.open(target.as_uri())
        self.status(f"opened {target}")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Drain worker threads before the process exits."""
        self.runner.wait(5_000)
        self.service.close()
        super().closeEvent(event)


def launch(config) -> int:
    """Run the application event loop. Returns the process exit code."""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("ContextFS")
    app.setStyle("Fusion")
    window = ContextFSWindow(config)
    window.show()
    return app.exec()
