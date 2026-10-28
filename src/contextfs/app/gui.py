"""ContextFS desktop GUI MVP (ADR-0001, ADR-0002, ADR-0013).

- **First run** (three pages): places, exclusions, what to expect. Saved; never asked again.
- **Every launch**: the window opens on the existing index; an indexer child process updates it,
  with a progress chip and Cancel. Search never waits.
- **Search**: one box, any query. Each row shows the file, its dates *by event*, the gated "why"
  line and a snippet, with Open file / Open folder.
- **Details**: the "Dates for this file" panel lists every raw time clue and each estimator's
  per-event interval with its confidence. This is direction A made visible.

Searches run on one worker thread with a read-only engine. The GUI process never writes the index.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QRunnable, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from contextfs.app.settings import Settings, validate_index_dir
from contextfs.engine.fsio import DEFAULT_EXCLUDED_NAMES, plain_path

EVENT_LABEL = {"authored": "Written", "arrived_here": "Arrived on this PC", "last_user_edit": "Last edited"}


def _fmt(utc: int | None, resolution_day: bool = False) -> str:
    if utc is None:
        return "—"
    dt = datetime.fromtimestamp(utc, timezone.utc).astimezone()
    return dt.strftime("%d %b %Y") if resolution_day else dt.strftime("%d %b %Y, %H:%M")


# ---------------------------------------------------------------------------------------------
# First run
# ---------------------------------------------------------------------------------------------


class FirstRun(QWizard):
    """Places → exclusions → what to expect."""

    def __init__(self, settings: Settings) -> None:
        """Build the three pages."""
        super().__init__()
        self.settings = settings
        self.setWindowTitle("Set up ContextFS")
        self.roots = QListWidget()
        places = QWizardPage()
        places.setTitle("Which folders should ContextFS search?")
        places.setSubTitle("Pick one or more folders, or a whole drive. You can change this later in Settings.")
        add, remove = QPushButton("Add folder…"), QPushButton("Remove")
        add.clicked.connect(self._add)
        remove.clicked.connect(lambda: [self.roots.takeItem(self.roots.row(i)) for i in self.roots.selectedItems()])
        lay = QVBoxLayout(places)
        lay.addWidget(self.roots)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        lay.addLayout(row)
        onedrive = os.environ.get("ONEDRIVE")  # Windows env names are case-insensitive
        if onedrive:
            lay.addWidget(QLabel(f"OneDrive found at {onedrive}. Files that are online-only are indexed by name "
                                 "and dates and are never downloaded."))
        self.addPage(places)

        excl = QWizardPage()
        excl.setTitle("What should be skipped?")
        excl.setSubTitle("System folders (Windows, Program Files, AppData) are always skipped. These folder "
                         "names are skipped anywhere; add your own, one per line.")
        self.extra = QPlainTextEdit("\n".join(sorted(DEFAULT_EXCLUDED_NAMES)))
        QVBoxLayout(excl).addWidget(self.extra)
        self.addPage(excl)

        expect = QWizardPage()
        expect.setTitle("What to expect")
        QVBoxLayout(expect).addWidget(QLabel(
            "• ContextFS reads your files but never changes, moves or deletes them.\n"
            "• Everything stays on this computer. There is no internet use and no telemetry.\n"
            "• The first index takes a while (minutes for a few thousand files, longer for a whole drive).\n"
            "  You can search while it runs; results get better as it goes.\n"
            "• Every result tells you why it was found, including which dates it trusted."))
        self.addPage(expect)

    def _add(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder or drive")
        if folder and not self.roots.findItems(folder, Qt.MatchExactly):
            self.roots.addItem(folder)

    def accept(self) -> None:
        """Validate and save."""
        roots = [self.roots.item(i).text() for i in range(self.roots.count())]
        if not roots:
            QMessageBox.warning(self, "ContextFS", "Add at least one folder.")
            return
        problem = validate_index_dir(self.settings.index_dir, roots)
        if problem:
            QMessageBox.warning(self, "ContextFS", f"Index location problem: {problem}")
            return
        self.settings.roots = roots
        defaults = set(DEFAULT_EXCLUDED_NAMES)
        self.settings.excluded_names = [x.strip().lower() for x in self.extra.toPlainText().splitlines()
                                        if x.strip() and x.strip().lower() not in defaults]
        self.settings.first_run_done = True
        self.settings.save()
        super().accept()


# ---------------------------------------------------------------------------------------------
# Search worker
# ---------------------------------------------------------------------------------------------


class _Signals(QObject):
    done = Signal(object, int)
    failed = Signal(str, int)


class SearchJob(QRunnable):
    """Run one search on the worker thread."""

    engine = None  # one read-only engine per worker thread, reopened after each index update

    def __init__(self, index_dir: Path, query: str, overrides: tuple, seq: int, reopen: bool) -> None:
        """Bind the query."""
        super().__init__()
        self.index_dir, self.query, self.overrides, self.seq, self.reopen = index_dir, query, overrides, seq, reopen
        self.signals = _Signals()

    @Slot()
    def run(self) -> None:
        """Search; never let an exception escape the thread."""
        from contextfs.engine import Engine
        from contextfs.engine.rank import SearchConfig

        try:
            if SearchJob.engine is None or self.reopen:
                if SearchJob.engine is not None:
                    SearchJob.engine.close()
                SearchJob.engine = Engine.open(self.index_dir)
            resp = SearchJob.engine.search(self.query, SearchConfig(overrides=self.overrides, top_k=25))
            dates = {r.file_id: SearchJob.engine.time_evidence(r.file_id) for r in resp.results}
            self.signals.done.emit((resp, dates), self.seq)
        except FileNotFoundError:
            self.signals.failed.emit("The index is being built for the first time. Try again in a moment.", self.seq)
        except Exception as exc:  # noqa: BLE001 - shown in the status bar
            self.signals.failed.emit(f"Search failed: {type(exc).__name__}: {exc}", self.seq)


# ---------------------------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------------------------


class MainWindow(QMainWindow):
    """Search box, results, details; the indexer runs as a child process."""

    def __init__(self, settings: Settings) -> None:
        """Lay out the window and start the launch update."""
        super().__init__()
        self.settings = settings
        self.index_dir = Path(settings.index_dir)
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(1)
        self.seq, self.reopen, self.proc = 0, False, None
        self.setWindowTitle("ContextFS — find files by what you remember")
        self.resize(1200, 760)

        self.box = QLineEdit()
        self.box.setPlaceholderText('Describe the file: "the PDF I downloaded before my ML exam", '
                                    '"budget sheet from March"…')
        self.box.setAccessibleName("Search")
        self.box.returnPressed.connect(self.search)
        self.read_as = QLabel("")
        self.use_time, self.use_kind = QCheckBox("use time"), QCheckBox("use file type")
        for cb in (self.use_time, self.use_kind):
            cb.setChecked(True)
            cb.toggled.connect(lambda _=None: self.search())
        self.results = QListWidget()
        self.results.setAccessibleName("Results")
        self.results.currentRowChanged.connect(self.show_details)
        self.results.itemDoubleClicked.connect(lambda _: self.open_file())
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        open_btn, folder_btn = QPushButton("Open file"), QPushButton("Open folder")
        open_btn.clicked.connect(self.open_file)
        folder_btn.clicked.connect(self.open_folder)

        top = QHBoxLayout()
        top.addWidget(self.box, 1)
        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self.open_settings)
        top.addWidget(settings_btn)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(self.details, 1)
        btns = QHBoxLayout()
        btns.addWidget(open_btn)
        btns.addWidget(folder_btn)
        rl.addLayout(btns)
        split = QSplitter()
        split.addWidget(self.results)
        split.addWidget(right)
        split.setSizes([700, 500])
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addLayout(top)
        chips = QHBoxLayout()
        chips.addWidget(self.read_as, 1)
        chips.addWidget(self.use_time)
        chips.addWidget(self.use_kind)
        lay.addLayout(chips)
        lay.addWidget(split, 1)
        self.setCentralWidget(central)
        self.status = QStatusBar()
        self.cancel_btn = QPushButton("Cancel update")
        self.cancel_btn.clicked.connect(self.cancel_update)
        self.cancel_btn.hide()
        self.status.addPermanentWidget(self.cancel_btn)
        self.setStatusBar(self.status)
        self.current = None
        QTimer.singleShot(200, self.start_update)

    # -- indexing child -----------------------------------------------------------------------

    def _child_command(self) -> tuple[str, list[str]]:
        gpu = "auto" if self.settings.gpu == "auto" else "off"
        args = ["--indexer", "--gpu", gpu, str(self.index_dir), *self.settings.roots]
        if getattr(sys, "frozen", False):
            return sys.executable, args
        return sys.executable, ["-m", "contextfs.app", *args]

    def start_update(self) -> None:
        """Launch-time incremental update in a child process."""
        if self.proc is not None or not self.settings.roots:
            return
        self.proc = QProcess(self)
        self.proc.readyReadStandardOutput.connect(self._child_output)
        self.proc.finished.connect(self._child_finished)
        self.proc.errorOccurred.connect(
            lambda err: self.status.showMessage(f"Could not run the indexer: {err}"))
        self.last_child_exit = None
        # stderr goes to a file: a pipe nobody drains can fill and stall the child
        from contextfs.app.settings import home_dir

        log_dir = home_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.child_log = log_dir / "indexer.log"
        self.proc.setStandardErrorFile(str(self.child_log))
        from contextfs.app.indexer_child import CANCEL_FILE

        (self.index_dir / CANCEL_FILE).unlink(missing_ok=True)  # stale request from a crash
        program, args = self._child_command()
        self.proc.start(program, args)
        self.cancel_btn.show()
        self.status.showMessage("Updating the index… search works meanwhile")

    def _child_output(self) -> None:
        for raw in bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace").splitlines():
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("type") == "progress":
                self.status.showMessage(f"Updating · {msg['processed']} new or changed · "
                                        f"{msg['seen']} checked — search works meanwhile")
            elif msg.get("type") == "done":
                r = msg["report"]
                head = ("Update paused (resumes next launch)" if r["cancelled"]
                        else "Index up to date")
                device = f" · embedded on {msg['device']}" if msg.get("device") else ""
                self.status.showMessage(f"{head}: {r['new']} new, {r['changed']} changed, "
                                        f"{r['deleted']} removed, {r['unchanged']} unchanged{device}")
                self.reopen = True
            elif msg.get("type") == "error":
                self.status.showMessage(f"Indexing stopped: {msg.get('message')}")

    def _child_finished(self, code: int = 0, _status=None) -> None:
        self._child_output()  # drain lines that arrived together with 'finished'
        try:
            err = self.child_log.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            err = ""
        self.last_child_exit = (code, err[-2000:])
        if code != 0 and err:
            self.status.showMessage(f"Indexing stopped (exit {code}): {err.splitlines()[-1][:200]}")
        self.cancel_btn.hide()
        self.proc = None
        if self.box.text().strip():
            self.search()

    def _request_cancel(self) -> None:
        from contextfs.app.indexer_child import CANCEL_FILE

        self.index_dir.mkdir(parents=True, exist_ok=True)
        (self.index_dir / CANCEL_FILE).write_text("cancel", encoding="utf-8")

    def cancel_update(self) -> None:
        """Ask the child to commit its batch and stop (a cancel-request file, not stdin)."""
        if self.proc is not None:
            self._request_cancel()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        """Clean stop on close: cancel, wait up to 3 s, then kill (ADR-0001 Rev. 1)."""
        if self.proc is not None:
            self._request_cancel()
            if not self.proc.waitForFinished(3000):
                self.proc.kill()
        super().closeEvent(event)

    # -- search ---------------------------------------------------------------------------------

    def search(self) -> None:
        """Run the query on the worker thread."""
        text = self.box.text()
        if not text.strip():
            return
        overrides = tuple(x for x in (("time", "removed") if not self.use_time.isChecked() else None,
                                      ("kind", "removed") if not self.use_kind.isChecked() else None) if x)
        self.seq += 1
        job = SearchJob(self.index_dir, text, overrides, self.seq, self.reopen)
        self.reopen = False
        job.signals.done.connect(self.show_results)
        job.signals.failed.connect(lambda msg, seq: self.status.showMessage(msg))
        self.pool.start(job)

    def show_results(self, payload, seq: int) -> None:
        """Render rows (stale responses are ignored)."""
        if seq != self.seq:
            return
        resp, dates = payload
        self.current = (resp, dates)
        frame = resp.frame
        bits = []
        for c in frame["time"]:
            hint = "" if c["event_hint"] == "any" else f" ({EVENT_LABEL[c['event_hint']].lower()})"
            bits.append(f"time: {c['surface']}{hint}, fading ±{c['band_days']:.0f} days")
        if frame["kind_word"]:
            bits.append(f"type: {frame['kind_word']}")
        self.read_as.setText("Read as: " + ("; ".join(bits) if bits else "words and meaning only"))
        self.results.clear()
        if not resp.results:
            self.results.addItem(QListWidgetItem("Nothing found. Try other words, or untick 'use time'."))
            self.details.clear()
            return
        for r in resp.results:
            ft = {row["event"]: row for row in dates[r.file_id]["file_time"] if row["estimator"] == "rules_v0"}
            when = " · ".join(f"{EVENT_LABEL[e]} {_fmt(ft[e]['point_utc'], True)}"
                              f"{'' if ft[e]['confidence'] == 'sure' else ' (' + ft[e]['confidence'] + ')'}"
                              for e in ("last_user_edit", "arrived_here") if e in ft)
            why = r.reasons[0] if r.reasons else ""
            item = QListWidgetItem(f"{Path(r.rel_path).name}\n{r.rel_path}\n{when}\nWhy: {why}")
            item.setToolTip(r.snippet or r.rel_path)
            item.setData(Qt.AccessibleTextRole, f"{Path(r.rel_path).name}. {why}")
            self.results.addItem(item)
        self.results.setCurrentRow(0)

    def show_details(self, row: int) -> None:
        """Evidence, reasons and the 'Dates for this file' panel."""
        if self.current is None or row < 0 or row >= len(self.current[0].results):
            return
        resp, dates = self.current
        r = resp.results[row]
        d = dates[r.file_id]
        html = [f"<h3>{escape(Path(r.rel_path).name)}</h3><p>{escape(r.path)}</p>",
                f"<p><i>Found by {escape(r.entered_via)}.</i></p><h4>Why it ranks here</h4><ul>"]
        html += [f"<li>{escape(x)}</li>" for x in r.reasons] + ["</ul>"]
        if r.snippet:
            html.append(f"<h4>Evidence</h4><p>{escape(r.snippet)}</p>")
        html.append("<h4>Dates for this file</h4><table cellpadding='3'>"
                    "<tr><th align='left'>event</th><th align='left'>estimate</th><th align='left'>how</th></tr>")
        for row_ in sorted(d["file_time"], key=lambda x: (x["estimator"] != "rules_v0", x["event"])):
            if row_["estimator"] != "rules_v0":
                continue
            span = (_fmt(row_["point_utc"]) if row_["hi_utc"] - row_["lo_utc"] <= 2
                    else f"{_fmt(row_['lo_utc'], True)} – {_fmt(row_['hi_utc'], True)}")
            html.append(f"<tr><td>{EVENT_LABEL[row_['event']]}</td><td>{span} <i>({row_['confidence']})</i></td>"
                        f"<td>{escape(row_['method'])}</td></tr>")
        html.append("</table><h4>Raw clues</h4><table cellpadding='3'>")
        for e in d["evidence"]:
            if e["utc_s"] is None and e["source"] != "motw":
                continue
            val = "downloaded from the internet" if e["source"] == "motw" else _fmt(e["utc_s"])
            zone = " (time zone not recorded)" if e["tz_state"] == "floating" else ""
            html.append(f"<tr><td>{escape(e['source'])} {escape(e['kind'])}</td><td>{val}{zone}</td></tr>")
        html.append("</table>")
        self.details.setHtml("".join(html))

    def _selected(self):
        if self.current is None:
            return None
        row = self.results.currentRow()
        res = self.current[0].results
        return res[row] if 0 <= row < len(res) else None

    def open_file(self) -> None:
        """Open with the default app (long-path prefix stripped)."""
        r = self._selected()
        if r is not None:
            try:
                os.startfile(plain_path(r.path))  # noqa: S606 - user-initiated
            except OSError as exc:
                self.status.showMessage(f"Could not open: {exc}")

    def open_folder(self) -> None:
        """Show the file in Explorer."""
        r = self._selected()
        if r is not None:
            subprocess.Popen(["explorer", "/select,", plain_path(r.path)])  # noqa: S603,S607

    def open_settings(self) -> None:
        """Add or remove folders; changes apply on the next update."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Settings")
        lst = QListWidget()
        lst.addItems(self.settings.roots)
        add, rem = QPushButton("Add folder…"), QPushButton("Remove")
        add.clicked.connect(lambda: (f := QFileDialog.getExistingDirectory(dlg, "Choose a folder")) and lst.addItem(f))
        rem.clicked.connect(lambda: [lst.takeItem(lst.row(i)) for i in lst.selectedItems()])
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Folders ContextFS searches:"))
        lay.addWidget(lst)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(rem)
        lay.addLayout(row)
        lay.addWidget(QLabel(f"Index location: {self.settings.index_dir}"))
        gpu = QCheckBox("Use a dedicated graphics card to speed up indexing, when there is one\n"
                        "(checked against the CPU on first use; search always runs on the CPU)")
        gpu.setChecked(self.settings.gpu == "auto")
        lay.addWidget(gpu)
        lay.addWidget(bb)
        if dlg.exec():
            self.settings.roots = [lst.item(i).text() for i in range(lst.count())]
            self.settings.gpu = "auto" if gpu.isChecked() else "off"
            self.settings.save()
            self.start_update()


def run() -> int:
    """Start the GUI (first run if needed)."""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("ContextFS")
    settings = Settings.load()
    if not settings.first_run_done:
        wizard = FirstRun(settings)
        if not wizard.exec():
            return 0
    win = MainWindow(settings)
    win.show()
    return app.exec()
