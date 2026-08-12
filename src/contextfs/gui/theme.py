"""Visual style for the desktop GUI.

A single stylesheet string rather than a theming framework. The palette is
chosen for a specific reason: retrieval results are ranked by *why* they matched
and each signal gets its own hue, so the same colour always means the same
signal — semantic is blue, graph is violet, activity is amber, timeline is
green. A user who has seen one explanation can read the next one at a glance.
"""

from __future__ import annotations

__all__ = ["SIGNAL_COLOURS", "STYLESHEET", "BACKGROUND", "SURFACE", "ACCENT", "TEXT", "MUTED"]

BACKGROUND = "#0f1117"
SURFACE = "#171a23"
SURFACE_HI = "#1f2430"
BORDER = "#2a3040"
TEXT = "#e6e9f0"
MUTED = "#8b93a7"
ACCENT = "#5b9cff"

#: One hue per retrieval signal, used everywhere that signal is shown.
SIGNAL_COLOURS = {
    "semantic": "#5b9cff",
    "graph": "#a78bfa",
    "activity": "#fbbf24",
    "timeline": "#34d399",
}

STYLESHEET = f"""
QWidget {{
    background: {BACKGROUND};
    color: {TEXT};
    font-family: "Segoe UI", system-ui, sans-serif;
    font-size: 13px;
}}
QMainWindow, QDialog {{ background: {BACKGROUND}; }}

QLineEdit {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 15px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{ border: 1px solid {ACCENT}; }}

QPushButton {{
    background: {SURFACE_HI};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 8px 16px;
}}
QPushButton:hover {{ background: #262c3a; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: #303849; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {BORDER}; background: {SURFACE}; }}
QPushButton#primary {{
    background: {ACCENT}; border: none; color: #07101f; font-weight: 600;
}}
QPushButton#primary:hover {{ background: #74acff; }}
QPushButton#primary:disabled {{ background: #33455f; color: {MUTED}; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; top: -1px; }}
QTabBar::tab {{
    background: transparent; padding: 9px 18px; margin-right: 2px;
    border-top-left-radius: 7px; border-top-right-radius: 7px; color: {MUTED};
}}
QTabBar::tab:selected {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER};
                         border-bottom-color: {SURFACE}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}

QTableWidget, QTreeWidget {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
    gridline-color: {BORDER}; alternate-background-color: #1b1f2a;
}}
QTableWidget::item, QTreeWidget::item {{ padding: 6px 8px; }}
QTableWidget::item:selected, QTreeWidget::item:selected {{
    background: #24334d; color: {TEXT};
}}
QHeaderView::section {{
    background: {SURFACE_HI}; border: none; border-bottom: 1px solid {BORDER};
    padding: 8px; color: {MUTED}; font-weight: 600;
}}

QTextEdit, QPlainTextEdit {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 10px;
}}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #333b4d; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #414b61; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #333b4d; border-radius: 5px; min-width: 30px; }}

QProgressBar {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 7px;
    text-align: center; height: 16px; color: {MUTED};
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 6px; }}

QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QLabel#hint {{ color: {MUTED}; }}
QLabel#title {{ font-size: 19px; font-weight: 600; }}
QCheckBox {{ spacing: 8px; }}
QSplitter::handle {{ background: {BORDER}; }}
QComboBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 7px; padding: 7px 10px;
}}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {BORDER}; selection-background-color: #24334d;
}}
"""
