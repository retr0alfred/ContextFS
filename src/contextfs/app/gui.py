"""ContextFS desktop window (ADR-0001, ADR-0002, ADR-0013; design: log.md Phase 36).

- **First run** (three pages): places, exclusions, what to expect. Saved; never asked again.
- **Every launch**: the window opens on the existing index; an indexer child process updates it,
  with progress in the status line and Cancel. Search never waits. **Update** (F5) runs it again.
- **Search**: one box, any query. The line under it shows how the query was understood, with the
  Time and File type switches. Each result shows the file, its folder, its dates *by event* and
  why it was found.
- **Details**: Open / Show in folder, why it ranks there, the evidence, and "Dates for this file":
  each event's estimate, how sure it is and what it is based on, then the raw time clues.

Searches run on one worker thread with a read-only engine. The GUI process never writes the index.
The look (``app.theme``) is presentation only: what is searched, ranked and shown is unchanged.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from html import escape
from pathlib import Path

from PySide6.QtCore import (
    QObject,
    QProcess,
    QRect,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
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
    QStackedWidget,
    QStatusBar,
    QStyle,
    QStyledItemDelegate,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from contextfs.app.settings import Settings, validate_index_dir
from contextfs.app.theme import glyph_icon, theme
from contextfs.engine.fsio import DEFAULT_EXCLUDED_NAMES, plain_path

#: The estimator whose dates the window shows and ranks with (log.md Phase 34, Decision 107).
DATE_SOURCE = "recon_v1"
EVENT_LABEL = {"authored": "Written", "arrived_here": "Came into your files", "last_user_edit": "Last edited"}
#: How sure a date is, in words ("sure" needs no word).
CONF_WORD = {"sure": "", "probably": "probably", "unknown": "uncertain"}
#: What each recon_v1 method rests on, in plain words (the method name is shown beside it).
HOW = {
    "infile_created": "the document's own “created” date",
    "fs_min": "the file system's dates",
    "copy_mtime": "the modified date, which survived a copy",
    "download_no_infile": "when it was downloaded",
    "reset_no_infile": "estimated: a copy reset this file's dates",
    "own_created": "the same as when it was written",
    "download_birth": "when it was downloaded",
    "download_copy_mtime": "the download date, which survived a copy",
    "download_reset": "estimated: downloaded, then copied with its dates reset",
    "camera_import_birth": "when the photo was imported",
    "camera_copied": "estimated from when the photo was taken",
    "moved_unmarked": "estimated: copied in, with no download or camera clue",
    "fs_mtime": "the file's modified date",
    "fs_mtime_after_copy": "a save after the file was copied",
    "reset_infile_modified": "the document's own “modified” date",
    "reset_infile_unedited": "weak estimate: a copy reset its dates",
    "reset_no_evidence": "estimated: a copy reset its dates and left no clue",
    "download_edited": "a save after it was downloaded",
    "photo_edited": "an edit after the photo was taken and imported",
}
CLUE_SOURCE = {"ntfs": "File system", "pdf": "PDF metadata", "ooxml": "Office document", "exif": "Photo (EXIF)",
               "zip": "Archive entries", "motw": "Download mark"}
EXAMPLES = ("the PDF I downloaded before my exam", "budget spreadsheet from March", "slides from the hackathon weekend")


def child_args(settings: Settings, index_dir: Path) -> list[str]:
    """The indexer child's arguments: the GPU only when the user turned it on (Decision 110)."""
    gpu = "auto" if settings.gpu == "auto" else "off"
    return ["--indexer", "--gpu", gpu, str(index_dir), *settings.roots]


def _fmt(utc: int | None, resolution_day: bool = False) -> str:
    if utc is None:
        return "—"
    from contextfs.engine.timeev import local_datetime

    dt = local_datetime(utc)  # never raises (pre-1970 dates crashed the window: QA H3)
    return dt.strftime("%d %b %Y") if resolution_day else dt.strftime("%d %b %Y, %H:%M")


def _month(utc: int) -> str:
    from contextfs.engine.timeev import local_datetime

    return local_datetime(utc).strftime("%b %Y")


def _past(utc: int) -> bool:
    """Whether a range end is still in the past (calibrated ranges can reach past today)."""
    import time

    return utc <= time.time()


def when_short(row: dict) -> str:
    """One event's date for a result row: a day, or a month range when it is an estimate.

    A range is shown ending "now" when its end lies in the future: that part cannot have happened.
    Display only; the stored interval and ranking are unchanged.
    """
    if row["hi_utc"] - row["lo_utc"] > 2 * 86400:  # an estimated range (recon_v1)
        lo = _month(row["lo_utc"])
        hi = _month(row["hi_utc"]) if _past(row["hi_utc"]) else "now"
        return f"{lo} – {hi}, estimated" if lo != hi else f"{lo}, estimated"
    word = CONF_WORD.get(row["confidence"], row["confidence"])
    return _fmt(row["point_utc"], True) + (f" ({word})" if word else "")


# ---------------------------------------------------------------------------------------------
# First run
# ---------------------------------------------------------------------------------------------


def _note(text: str, role: str = "muted") -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setProperty("role", role)
    return lab


def _button(text: str, icon: str | None = None, variant: str | None = None, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    if variant:
        b.setProperty("variant", variant)
    if icon:
        b.setProperty("glyph", icon)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def _refresh_icons(root: QWidget) -> None:
    """Redraw every glyph button in the current ink (after a theme change)."""
    for b in root.findChildren(QPushButton):
        name = b.property("glyph")
        if name:
            col = theme.c.on_ink if b.property("variant") == "primary" else theme.c.text
            b.setIcon(glyph_icon(name, col))


def _wizard_page(title: str, subtitle: str = "") -> tuple[QWizardPage, QVBoxLayout]:
    """A first-run page whose heading lines up with its content (Qt's own header is not used)."""
    page = QWizardPage()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(8, 4, 8, 4)
    lay.setSpacing(10)
    t = QLabel(title)
    t.setProperty("role", "title")
    t.setWordWrap(True)
    lay.addWidget(t)
    if subtitle:
        lay.addWidget(_note(subtitle))
    lay.addSpacing(4)
    return page, lay


class FirstRun(QWizard):
    """Places → exclusions → what to expect."""

    def __init__(self, settings: Settings) -> None:
        """Build the three pages."""
        super().__init__()
        self.settings = settings
        self.setWindowTitle("Set up ContextFS")
        self.setWizardStyle(QWizard.WizardStyle.ClassicStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        for which in (QWizard.WizardButton.NextButton, QWizard.WizardButton.FinishButton):
            b = self.button(which)
            b.setProperty("variant", "primary")
            b.style().unpolish(b)  # a property set after creation needs a re-polish to restyle
            b.style().polish(b)
        self.resize(640, 520)
        self.roots = QListWidget()
        places, lay = _wizard_page("Which folders should ContextFS search?",
                                   "Pick one or more folders, or a whole drive. You can change this later in Settings.")
        add = _button("Add folder…", "add")
        remove = _button("Remove", "remove", "quiet")
        add.clicked.connect(self._add)
        remove.clicked.connect(lambda: [self.roots.takeItem(self.roots.row(i)) for i in self.roots.selectedItems()])
        lay.addWidget(self.roots)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        lay.addLayout(row)
        onedrive = os.environ.get("ONEDRIVE")  # Windows env names are case-insensitive
        if onedrive:
            lay.addWidget(_note(f"OneDrive found at {onedrive}. Files that are online-only are indexed by name "
                                "and dates and are never downloaded."))
        self.addPage(places)

        excl, xl = _wizard_page("What should be skipped?",
                                "System folders (Windows, Program Files, AppData) are always skipped. These folder "
                                "names are skipped anywhere; add your own, one per line.")
        self.extra = QPlainTextEdit("\n".join(sorted(DEFAULT_EXCLUDED_NAMES)))
        xl.addWidget(self.extra)
        self.addPage(excl)

        expect, el = _wizard_page("What to expect")
        for head, body in (
            ("Your files are only read.", "ContextFS never changes, moves or deletes them."),
            ("Everything stays on this computer.", "There is no internet use and no telemetry."),
            ("The first index takes a while.", "Minutes for a few thousand files, longer for a whole drive. "
                                               "You can search while it runs; results get better as it goes."),
            ("Every result says why it was found,", "including which dates it trusted and how sure they are."),
        ):
            h = QLabel(head)
            h.setProperty("role", "heading")
            el.addWidget(h)
            el.addWidget(_note(body))
            el.addSpacing(2)
        el.addStretch()
        self.addPage(expect)
        _refresh_icons(self)

    def _add(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder or drive")
        if folder and not self.roots.findItems(folder, Qt.MatchFlag.MatchExactly):
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
        except sqlite3.OperationalError:
            # busy, or a stopped indexer's journal is waiting for recovery: not damage
            if SearchJob.engine is not None:
                SearchJob.engine.close()
                SearchJob.engine = None
            self.signals.failed.emit("The index is being updated or recovered; search works again in a "
                                     "moment.", self.seq)
        except sqlite3.DatabaseError:
            if SearchJob.engine is not None:
                SearchJob.engine.close()
                SearchJob.engine = None
            self.signals.failed.emit("The index looks damaged. The next update (F5) checks it and rebuilds it "
                                     "if needed; your files are not affected.", self.seq)
        except Exception as exc:  # noqa: BLE001 - shown in the status bar
            self.signals.failed.emit(f"Search failed: {type(exc).__name__}: {exc}", self.seq)


# ---------------------------------------------------------------------------------------------
# Result rows
# ---------------------------------------------------------------------------------------------

ROW = Qt.ItemDataRole.UserRole + 1


class ResultDelegate(QStyledItemDelegate):
    """One result: name and type, folder, dates by event, why it was found."""

    PAD_X, PAD_Y, GAP = 16, 11, 3

    def _fonts(self) -> tuple[QFont, QFont, QFont]:
        base = QApplication.font()
        name = QFont(base)
        name.setPixelSize(14)
        name.setWeight(QFont.Weight.DemiBold)
        small = QFont(base)
        small.setPixelSize(12)
        tag = QFont(base)
        tag.setPixelSize(11)
        tag.setWeight(QFont.Weight.DemiBold)
        return name, small, tag

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt API
        """Fixed height: three or four lines."""
        name, small, _ = self._fonts()
        d = index.data(ROW) or {}
        lines = 2 + bool(d.get("when")) + bool(d.get("why"))
        h = QFontMetrics(name).height() + (lines - 1) * (QFontMetrics(small).height() + self.GAP)
        return QSize(option.rect.width(), h + 2 * self.PAD_Y + 2)

    def paint(self, p: QPainter, option, index) -> None:
        """Draw the row in the current palette."""
        d = index.data(ROW) or {}
        c = theme.c
        name_f, small_f, tag_f = self._fonts()
        r = option.rect
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = r.adjusted(6, 3, -6, -3)
        if option.state & QStyle.StateFlag.State_Selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c.selected))
            p.drawRoundedRect(box, 6, 6)
            p.setBrush(QColor(c.ink))
            p.drawRoundedRect(QRect(box.left(), box.top() + 10, 3, box.height() - 20), 1.5, 1.5)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c.hover))
            p.drawRoundedRect(box, 6, 6)
        x, w = r.left() + self.PAD_X, r.width() - 2 * self.PAD_X
        y = r.top() + self.PAD_Y
        # line 1: name, and the type on the right
        p.setFont(tag_f)
        tag = d.get("kind", "")
        tag_w = QFontMetrics(tag_f).horizontalAdvance(tag) + (10 if tag else 0)
        nh = QFontMetrics(name_f).height()
        if tag:
            p.setPen(QColor(c.faint))
            p.drawText(QRect(x + w - tag_w, y, tag_w, nh), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, tag)
        p.setFont(name_f)
        p.setPen(QColor(c.text))
        p.drawText(QRect(x, y, w - tag_w, nh), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(name_f).elidedText(d.get("name", ""), Qt.TextElideMode.ElideMiddle, w - tag_w))
        y += nh + self.GAP
        sh = QFontMetrics(small_f).height()
        p.setFont(small_f)
        for key, col, mode in (("folder", c.muted, Qt.TextElideMode.ElideMiddle),
                               ("when", c.muted, Qt.TextElideMode.ElideRight),
                               ("why", c.faint, Qt.TextElideMode.ElideRight)):
            text = d.get(key)
            if not text:
                continue
            p.setPen(QColor(col))
            p.drawText(QRect(x, y, w, sh), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(small_f).elidedText(text, mode, w))
            y += sh + self.GAP
        p.restore()


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
        self._live_jobs: dict[int, SearchJob] = {}  # in-flight searches, owned by this thread
        self.setWindowTitle("ContextFS")
        self.resize(1240, 800)
        self.setMinimumSize(860, 560)
        self.current = None

        # -- header: search, Update, Settings
        self.box = QLineEdit()
        self.box.setObjectName("search")
        self.box.setPlaceholderText('Describe the file: words from it, what kind of file, roughly when…')
        self.box.setAccessibleName("Search")
        self.box.setClearButtonEnabled(True)
        self.box.returnPressed.connect(self.search)
        self._search_icon = self.box.addAction(glyph_icon("search", theme.c.faint), QLineEdit.ActionPosition.LeadingPosition)
        self.update_btn = _button("Update", "refresh", tip="Check your folders for new and changed files (F5)")
        self.update_btn.clicked.connect(self.start_update)
        settings_btn = _button("Settings", "settings", tip="Folders, graphics card, appearance, your index (Ctrl+,)")
        settings_btn.clicked.connect(self.open_settings)
        for b in (self.update_btn, settings_btn):
            b.setFixedHeight(40)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(self.box, 1)
        header.addWidget(self.update_btn)
        header.addWidget(settings_btn)

        # -- how the query was read, and the two switches
        self.read_as = QLabel("")
        self.read_as.setProperty("role", "muted")
        self.read_as.setTextFormat(Qt.TextFormat.RichText)
        self.use_time = _button("Time", variant="pill", tip="Use dates when ranking. Turn off if a remembered date "
                                                             "might be wrong.")
        self.use_kind = _button("File type", variant="pill", tip="Use the kind of file (PDF, slides, photo…) when "
                                                                  "ranking.")
        for b in (self.use_time, self.use_kind):
            b.setCheckable(True)
            b.setChecked(True)
            b.toggled.connect(lambda _=None: self.search())
        use = QLabel("Use")
        use.setProperty("role", "faint")
        chips = QHBoxLayout()
        chips.setSpacing(6)
        chips.addWidget(self.read_as, 1)
        chips.addWidget(use)
        chips.addWidget(self.use_time)
        chips.addWidget(self.use_kind)

        # -- left pane: start page / results / nothing found
        self.results = QListWidget()
        self.results.setObjectName("results")
        self.results.setAccessibleName("Results")
        self.results.setItemDelegate(ResultDelegate(self.results))
        self.results.setMouseTracking(True)
        self.results.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.results.setUniformItemSizes(False)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.currentRowChanged.connect(self.show_details)
        self.results.itemDoubleClicked.connect(lambda _: self.open_file())
        self.left = QStackedWidget()
        self.left.addWidget(self._start_page())
        self.left.addWidget(self.results)
        self.nothing = _note("", "muted")
        self.nothing.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.left.addWidget(self.nothing)
        left_pane = self._pane(self.left)

        # -- right pane: header with actions, then the details
        self.d_name = QLabel("")
        self.d_name.setProperty("role", "title")
        self.d_name.setWordWrap(True)
        self.d_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.d_path = QLineEdit()
        self.d_path.setReadOnly(True)
        self.d_path.setFrame(False)
        self.d_path.setObjectName("path")
        self.d_path.setAccessibleName("Full path")
        self.open_btn = _button("Open", "open", "primary", "Open with its usual app (Enter)")
        self.folder_btn = _button("Show in folder", "folder", tip="Show it in File Explorer (Ctrl+E)")
        self.open_btn.clicked.connect(self.open_file)
        self.folder_btn.clicked.connect(self.open_folder)
        acts = QHBoxLayout()
        acts.setSpacing(8)
        acts.addWidget(self.open_btn)
        acts.addWidget(self.folder_btn)
        acts.addStretch()
        self.details = QTextBrowser()
        self.details.setObjectName("details")
        self.details.setOpenExternalLinks(False)
        self.details.setViewportMargins(14, 2, 10, 10)
        head = QWidget()
        hl = QVBoxLayout(head)
        hl.setContentsMargins(20, 18, 20, 12)
        hl.setSpacing(6)
        hl.addWidget(self.d_name)
        hl.addWidget(self.d_path)
        hl.addSpacing(6)
        hl.addLayout(acts)
        rule = QFrame()
        rule.setObjectName("rule")
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        bl.addWidget(head)
        bl.addWidget(rule)
        bl.addWidget(self.details, 1)
        self.right = QStackedWidget()
        self.right.addWidget(_note("Select a result to see why it was found and what its dates are based on.",
                                   "faint"))
        self.right.widget(0).setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.right.addWidget(body)
        right_pane = self._pane(self.right)

        split = QSplitter()
        split.addWidget(left_pane)
        split.addWidget(right_pane)
        split.setSizes([640, 560])
        split.setChildrenCollapsible(False)
        split.setHandleWidth(12)
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(16, 14, 16, 10)
        lay.setSpacing(10)
        lay.addLayout(header)
        lay.addLayout(chips)
        lay.addWidget(split, 1)
        self.setCentralWidget(central)

        self.status = QStatusBar()
        self.status.setSizeGripEnabled(False)
        self.cancel_btn = _button("Cancel update", "cancel", "quiet")
        self.cancel_btn.clicked.connect(self.cancel_update)
        self.cancel_btn.hide()
        self.status.addPermanentWidget(self.cancel_btn)
        self.setStatusBar(self.status)
        self._set_actions_enabled(False)
        from contextfs.app.winjob import ChildJob

        self.job = ChildJob()  # the child dies with this window, and has a memory limit (ADR-0001)
        self.restarts, self.restarts_total, self.closing, self.user_cancelled = 0, 0, False, False
        QShortcut(QKeySequence("F5"), self, activated=self.start_update)  # Refresh (ADR-0001 §4)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._focus_search)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)
        QShortcut(QKeySequence("Ctrl+,"), self, activated=self.open_settings)
        QShortcut(QKeySequence("Ctrl+E"), self, activated=self.open_folder)
        for key, widget, slot in ((Qt.Key.Key_Return, self.results, self.open_file),
                                  (Qt.Key.Key_Down, self.box, self._focus_results)):
            QShortcut(QKeySequence(key), widget, activated=slot).setContext(Qt.ShortcutContext.WidgetShortcut)
        theme.changed.connect(self._restyle)
        self._restyle()
        QTimer.singleShot(200, self.start_update)
        if os.environ.get("CONTEXTFS_SELFDRIVE"):  # hardening suite only; inert otherwise
            from contextfs.app.autotest import attach

            attach(self, os.environ["CONTEXTFS_SELFDRIVE"])

    # -- layout helpers ------------------------------------------------------------------------

    @staticmethod
    def _pane(inner: QWidget) -> QFrame:
        f = QFrame()
        f.setObjectName("pane")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.addWidget(inner)
        return f

    def _start_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(28, 28, 28, 28)
        lay.setSpacing(8)
        lay.addStretch(2)
        title = QLabel("Find a file by what you remember")
        title.setProperty("role", "title")
        lay.addWidget(title)
        lay.addWidget(_note("Words from it, what kind of file it was, roughly when you had it — in any order. "
                            "Every result says why it was found."))
        lay.addSpacing(10)
        try_ = QLabel("For example")
        try_.setProperty("role", "faint")
        lay.addWidget(try_)
        for ex in EXAMPLES:
            b = _button(f"“{ex}”", "search", "quiet", "Search for this")
            b.setStyleSheet("text-align: left; padding-left: 0;")
            b.clicked.connect(lambda _=None, q=ex: (self.box.setText(q), self.search()))
            lay.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addSpacing(12)
        self.scope = _note("", "faint")
        lay.addWidget(self.scope)
        lay.addStretch(3)
        return page

    def _restyle(self) -> None:
        """Redraw what the stylesheet cannot: glyphs, rows and the details document."""
        _refresh_icons(self)
        self._search_icon.setIcon(glyph_icon("search", theme.c.faint))
        n = len(self.settings.roots)
        self.scope.setText(f"Searching {n} folder{'s' if n != 1 else ''}. Index: {self.index_dir}" if n
                           else "No folders chosen yet: add some in Settings.")
        self.results.viewport().update()
        if self.current is not None:
            self.show_details(self.results.currentRow())

    def _focus_search(self) -> None:
        self.box.setFocus()
        self.box.selectAll()

    def _focus_results(self) -> None:
        if self.results.count():
            self.results.setFocus()
            self.results.setCurrentRow(max(0, self.results.currentRow()))

    def _set_actions_enabled(self, on: bool) -> None:
        self.open_btn.setEnabled(on)
        self.folder_btn.setEnabled(on)

    # -- indexing child -----------------------------------------------------------------------

    def _child_command(self) -> tuple[str, list[str]]:
        args = child_args(self.settings, self.index_dir)
        if getattr(sys, "frozen", False):
            return sys.executable, args
        return sys.executable, ["-m", "contextfs.app", *args]

    def start_update(self) -> None:
        """Launch-time incremental update in a child process."""
        if self.proc is not None:
            return
        self.user_cancelled = False
        if not self.settings.roots and not (self.index_dir / "contextfs.db").exists():
            return  # nothing chosen and nothing indexed; with an index, an empty list prunes it
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
        if self.proc.waitForStarted(10000):
            self.job.assign(int(self.proc.processId()))
        self.cancel_btn.show()
        self.update_btn.setEnabled(False)
        self.status.showMessage("Updating the index… search works meanwhile")

    def _child_output(self) -> None:
        for raw in bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace").splitlines():
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("type") == "progress":
                self.restarts = 0  # progress since the last restart: a new problem file gets its own budget
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
            elif msg.get("type") == "recovered":
                self.status.showMessage("The index was damaged and is being rebuilt from your files "
                                        "(the damaged copy is kept in the index folder).")
            elif msg.get("type") == "error":
                self.status.showMessage(f"Indexing stopped: {msg.get('message')}")

    def _child_finished(self, code: int = 0, status=None) -> None:
        from contextfs.app.indexer_child import EXIT_DISK, EXIT_LOCKED

        self._child_output()  # drain lines that arrived together with 'finished'
        try:
            err = self.child_log.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            err = ""
        self.last_child_exit = (code, err[-2000:])
        if code != 0 and err:
            self.status.showMessage(f"Indexing stopped (exit {code}): {err.splitlines()[-1][:200]}")
        self.cancel_btn.hide()
        self.update_btn.setEnabled(True)
        self.proc = None
        # A hang (exit 3) or a crash: the file in progress got a strike; restarting retries it once
        # and then quarantines it, so a bad file costs a restart, never the whole scan.
        crashed = status == QProcess.ExitStatus.CrashExit or code not in (0, EXIT_LOCKED, EXIT_DISK)
        if crashed and not self.closing and not self.user_cancelled and self.restarts < 3:
            self.restarts += 1
            self.restarts_total += 1
            self.status.showMessage(f"The indexer stopped on a problem file; restarting ({self.restarts}/3)…")
            QTimer.singleShot(1000, self.start_update)
            return
        if code == 0:
            self.restarts = 0
        if self.box.text().strip():
            self.search()

    def _request_cancel(self) -> None:
        from contextfs.app.indexer_child import CANCEL_FILE

        self.index_dir.mkdir(parents=True, exist_ok=True)
        (self.index_dir / CANCEL_FILE).write_text("cancel", encoding="utf-8")

    def cancel_update(self) -> None:
        """Ask the child to commit its batch and stop (a cancel-request file, not stdin)."""
        if self.proc is not None:
            self.user_cancelled = True  # no automatic restart after the user's Cancel (QA L3)
            self._request_cancel()

    def _kill_child_for_user(self, wait_ms: int) -> None:
        """Clean stop first; if the child does not stop in time, kill it.

        The kill is recorded as the user's stop, so the file the child was in gets no strike (QA H4).
        """
        from contextfs.engine.guard import note_user_stop

        self._request_cancel()
        if not self.proc.waitForFinished(wait_ms):
            note_user_stop(self.index_dir)
            self.proc.kill()
            self.proc.waitForFinished(3000)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        """Clean stop on close: cancel, wait up to 3 s, then kill (ADR-0001 Rev. 1)."""
        self.closing = True
        if self.proc is not None:
            self._kill_child_for_user(3000)
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
        # The job (and its signal object) must outlive the queued delivery of its result. Left to
        # Qt's auto-delete, it could be destroyed on the worker thread while this thread was still
        # delivering the signal: a use-after-free that crashed the window (Phase 33 suite C10).
        job.setAutoDelete(False)
        self._live_jobs[self.seq] = job
        job.signals.done.connect(self._job_done)
        job.signals.failed.connect(self._job_failed)
        self.pool.start(job)

    def _job_done(self, payload, seq: int) -> None:
        self._live_jobs.pop(seq, None)  # released here, on the GUI thread
        self.show_results(payload, seq)

    def _job_failed(self, msg: str, seq: int) -> None:
        self._live_jobs.pop(seq, None)
        self.status.showMessage(msg)

    def show_results(self, payload, seq: int) -> None:
        """Render rows (stale responses are ignored)."""
        if seq != self.seq:
            return
        resp, dates = payload
        self.current = (resp, dates)
        frame = resp.frame
        bits = []
        for c in frame["time"]:
            hint = "" if c["event_hint"] == "any" else f" · {EVENT_LABEL[c['event_hint']].lower()}"
            bits.append(f"time&nbsp;&nbsp;<b>{escape(c['surface'])}</b>{hint}, ±{c['band_days']:.0f} days")
        if frame["kind_word"]:
            bits.append(f"type&nbsp;&nbsp;<b>{escape(frame['kind_word'])}</b>")
        sep = "&nbsp;&nbsp;&nbsp;·&nbsp;&nbsp;&nbsp;"
        self.read_as.setText("Understood as&nbsp;&nbsp;&nbsp;" + (sep.join(bits) if bits else "words and meaning only"))
        self.results.clear()
        if not resp.results:
            self.nothing.setText("Nothing found.\nTry other words, or turn off Time if the date you remember "
                                 "might be wrong.")
            self.left.setCurrentIndex(2)
            self.right.setCurrentIndex(0)
            self._set_actions_enabled(False)
            return
        self.left.setCurrentIndex(1)
        for r in resp.results:
            ft = {row["event"]: row for row in dates[r.file_id]["file_time"] if row["estimator"] == DATE_SOURCE}
            when = "  ·  ".join(f"{EVENT_LABEL[e]} {when_short(ft[e])}" for e in ("last_user_edit", "arrived_here")
                                if e in ft)
            why = r.reasons[0] if r.reasons else ""
            rel = Path(r.rel_path)
            item = QListWidgetItem()
            item.setData(ROW, {"name": rel.name, "kind": rel.suffix.lstrip(".").upper(),
                               "folder": str(rel.parent) if str(rel.parent) != "." else "", "when": when,
                               "why": why[:1].upper() + why[1:]})
            item.setToolTip(r.snippet or r.rel_path)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, f"{rel.name}. {why}")
            self.results.addItem(item)
        self.results.setCurrentRow(0)

    def show_details(self, row: int) -> None:
        """Actions, reasons, evidence and the 'Dates for this file' panel."""
        if self.current is None or row < 0 or row >= len(self.current[0].results):
            self.right.setCurrentIndex(0)
            self._set_actions_enabled(False)
            return
        resp, dates = self.current
        r = resp.results[row]
        d = dates[r.file_id]
        c = theme.c
        self.right.setCurrentIndex(1)
        self._set_actions_enabled(True)
        self.d_name.setText(Path(r.rel_path).name)
        self.d_path.setText(r.path)
        self.d_path.setCursorPosition(0)
        self.d_path.setToolTip(r.path)
        self.details.document().setDefaultStyleSheet(
            f"body {{ color: {c.text}; }} h4 {{ font-size: 13px; font-weight: 600; margin: 18px 0 6px 0; }}"
            f"p {{ margin: 0 0 4px 0; }} .muted {{ color: {c.muted}; }} .faint {{ color: {c.faint}; }}"
            f"td {{ padding: 4px 14px 4px 0; vertical-align: top; }} th {{ color: {c.faint}; font-weight: 400;"
            f" padding: 0 14px 4px 0; text-align: left; }} .small {{ font-size: 11px; }}")
        html = ["<h4 style='margin-top:4px'>Why it is here</h4>"]
        html += [f"<p>{escape(x[:1].upper() + x[1:])}</p>" for x in r.reasons]
        html.append(f"<p class='faint small'>Found by {escape(r.entered_via)}.</p>")
        if r.snippet:
            html.append("<h4>Evidence</h4>"
                        f"<table width='100%' cellspacing='0' cellpadding='10' bgcolor='{c.sunken}'"
                        f" style='border-left: 3px solid {c.border_strong}'><tr><td class='muted'>"
                        f"{escape(r.snippet)}</td></tr></table>")
        html.append("<h4>Dates for this file</h4><table cellspacing='0'>"
                    "<tr><th>Event</th><th>When</th><th>Based on</th></tr>")
        for row_ in sorted(d["file_time"], key=lambda x: x["event"]):
            if row_["estimator"] != DATE_SOURCE:
                continue
            lo_day = _fmt(row_["lo_utc"], True)
            hi_day = _fmt(row_["hi_utc"], True) if _past(row_["hi_utc"]) else "now"
            span = (_fmt(row_["point_utc"]) if row_["hi_utc"] - row_["lo_utc"] <= 2
                    else lo_day if lo_day == hi_day else f"between {lo_day}<br>and {hi_day}")
            word = CONF_WORD.get(row_["confidence"], row_["confidence"]) or "sure"
            how = HOW.get(row_["method"], row_["method"].replace("_", " "))
            html.append(f"<tr><td>{EVENT_LABEL[row_['event']]}</td><td>{span}<br><span class='faint small'>"
                        f"{escape(word)}</span></td><td>{escape(how)}<br><span class='faint small'>"
                        f"{escape(row_['method'])}</span></td></tr>")
        html.append("</table><h4>Raw time clues</h4><table cellspacing='0' class='small'>")
        for e in d["evidence"]:
            if e["utc_s"] is None and e["source"] != "motw":
                continue
            src = CLUE_SOURCE.get(e["source"], e["source"])
            if e["source"] == "motw":
                label, val = src, "downloaded from the internet"
            else:
                label, val = f"{src} · {e['kind']}", _fmt(e["utc_s"])
            zone = " <span class='faint'>(time zone not recorded)</span>" if e["tz_state"] == "floating" else ""
            html.append(f"<tr><td class='muted'>{escape(label)}</td><td>{val}{zone}</td></tr>")
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
        """Folders, the GPU option, appearance and the index; folder changes apply on the next update."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Settings")
        dlg.resize(600, 620)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(8)

        def section(title: str, note: str | None = None) -> None:
            if lay.count():
                lay.addSpacing(14)
            h = QLabel(title)
            h.setProperty("role", "heading")
            lay.addWidget(h)
            if note:
                lay.addWidget(_note(note))

        section("Folders", "ContextFS searches these folders and everything inside them.")
        lst = QListWidget()
        lst.addItems(self.settings.roots)
        lst.setMinimumHeight(120)
        lay.addWidget(lst, 1)
        add, rem = _button("Add folder…", "add"), _button("Remove", "remove", "quiet")
        add.clicked.connect(lambda: (f := QFileDialog.getExistingDirectory(dlg, "Choose a folder")) and lst.addItem(f))
        rem.clicked.connect(lambda: [lst.takeItem(lst.row(i)) for i in lst.selectedItems()])
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch()
        lay.addLayout(row)

        section("Indexing")
        gpu = QCheckBox("Use the graphics card (GPU) to index")
        gpu.setChecked(self.settings.gpu == "auto")
        lay.addWidget(gpu)
        lay.addWidget(_note("Off by default. It can be faster on long documents but uses more memory. It is checked "
                            "against the processor on first use; search always runs on the processor.", "faint"))

        section("Appearance")
        scheme = QComboBox()
        scheme.addItems(["Same as Windows", "Light", "Dark"])
        scheme.setCurrentIndex({"system": 0, "light": 1, "dark": 2}.get(self.settings.theme, 0))
        srow = QHBoxLayout()
        srow.addWidget(scheme)
        srow.addStretch()
        lay.addLayout(srow)

        section("Your index", "A private copy of your files' text, names and dates, kept on this computer.")
        loc = QLineEdit(str(self.settings.index_dir))
        loc.setReadOnly(True)
        loc.setAccessibleName("Index location")
        lay.addWidget(loc)
        wipe = _button("Delete my index…", "delete", "danger",
                       "Removes the copy of text, names and dates ContextFS keeps. Your files are not touched.")
        wipe.clicked.connect(lambda: self.delete_index(dlg))
        wrow = QHBoxLayout()
        wrow.addWidget(wipe)
        wrow.addStretch()
        lay.addLayout(wrow)

        lay.addSpacing(16)
        foot = QHBoxLayout()
        foot.addStretch()
        cancel, save = _button("Cancel"), _button("Save", variant="primary")
        cancel.clicked.connect(dlg.reject)
        save.clicked.connect(dlg.accept)
        save.setDefault(True)
        foot.addWidget(cancel)
        foot.addWidget(save)
        lay.addLayout(foot)
        _refresh_icons(dlg)
        if dlg.exec():
            self.settings.roots = [lst.item(i).text() for i in range(lst.count())]
            self.settings.gpu = "auto" if gpu.isChecked() else "off"
            self.settings.theme = ("system", "light", "dark")[scheme.currentIndex()]
            self.settings.save()
            app = QApplication.instance()
            if app is not None:
                theme.apply(app, self.settings.theme)
            self._restyle()
            self.start_update()

    def delete_index(self, parent=None) -> bool:
        """Privacy action (ADR-0016 §2): delete the index (text, names, dates); keep settings."""
        import shutil

        answer = QMessageBox.question(
            parent or self, "Delete my index?",
            "This deletes ContextFS's copy of your files' text, names and dates. Your files are not "
            "touched. Your folder choices are kept, and the index is rebuilt at the next update (F5).")
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self.closing = True  # no automatic restart while deleting
        if self.proc is not None:
            self._kill_child_for_user(5000)
        self.pool.waitForDone(5000)
        if SearchJob.engine is not None:
            SearchJob.engine.close()
            SearchJob.engine = None
        for name in ("contextfs.db", "contextfs.db-wal", "contextfs.db-shm", "writer.lock", "inprogress.marker",
                     "cancel.request"):
            (self.index_dir / name).unlink(missing_ok=True)
        for name in ("vectors", "tmp", *[d.name for d in self.index_dir.glob("damaged-*")]):
            shutil.rmtree(self.index_dir / name, ignore_errors=True)
        self.closing = False
        self.results.clear()
        self.current = None
        self.left.setCurrentIndex(0)
        self.right.setCurrentIndex(0)
        self._set_actions_enabled(False)
        self.status.showMessage("Index deleted. Your files were not touched; press Update (F5) to rebuild it.")
        return True


def run() -> int:
    """Start the GUI (first run if needed)."""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("ContextFS")
    settings = Settings.load()
    theme.apply(app, settings.theme)
    if not settings.first_run_done:
        wizard = FirstRun(settings)
        if not wizard.exec():
            return 0
    win = MainWindow(settings)
    win.show()
    return app.exec()
