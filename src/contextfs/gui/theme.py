"""Qt stylesheet for the desktop application.

Every tone comes from :mod:`contextfs.theme`, which the CLI and the 3D page also
read, so the three surfaces cannot drift apart.

Two rules govern the whole sheet, both of them deliberate:

* **No rounded corners.** Sharp edges throughout. Rounding reads as friendly
  and consumer; this is meant to read as an instrument.
* **Structure comes from thin rules, not from fills.** Panels are separated by
  1px lines rather than by differently-shaded boxes, which keeps the window
  overwhelmingly black and puts the contrast budget on the content.
"""

from __future__ import annotations

from contextfs.theme import (
    FAINT,
    INK,
    MONO_STACK,
    MUTED,
    PAPER,
    RULE,
    SIGNAL_GLYPHS,
    SIGNAL_GREYS,
    STAGE_GREYS,
    SURFACE,
    SURFACE_HI,
    TEXT,
)

__all__ = [
    "STYLESHEET",
    "SIGNAL_GREYS",
    "SIGNAL_GLYPHS",
    "STAGE_GREYS",
    "MUTED",
    "INK",
    "TEXT",
    "FAINT",
    "MONO_STACK",
]

STYLESHEET = f"""
QWidget {{
    background: {PAPER};
    color: {TEXT};
    font-family: "Segoe UI", system-ui, sans-serif;
    font-size: 13px;
}}
QMainWindow, QDialog {{ background: {PAPER}; }}

QLineEdit {{
    background: {SURFACE};
    border: 1px solid {RULE};
    border-radius: 0px;
    padding: 11px 14px;
    font-size: 15px;
    font-family: {MONO_STACK};
    color: {INK};
    selection-background-color: {INK};
    selection-color: {PAPER};
}}
QLineEdit:focus {{ border: 1px solid {INK}; }}

QPushButton {{
    background: transparent;
    border: 1px solid {RULE};
    border-radius: 0px;
    padding: 8px 16px;
    color: {TEXT};
    font-size: 12px;
    letter-spacing: 0.4px;
}}
QPushButton:hover {{ border-color: {INK}; color: {INK}; }}
QPushButton:pressed {{ background: {SURFACE_HI}; }}
QPushButton:disabled {{ color: {FAINT}; border-color: #1e1e1e; }}
/* The one inverted element in the window. Exactly one primary action per
   screen earns full-white reverse; more than one and none of them read as
   primary. */
QPushButton#primary {{
    background: {INK}; border: 1px solid {INK}; color: {PAPER}; font-weight: 700;
}}
QPushButton#primary:hover {{ background: {TEXT}; border-color: {TEXT}; }}
QPushButton#primary:disabled {{ background: #2a2a2a; border-color: #2a2a2a; color: {FAINT}; }}

QTabWidget::pane {{ border: 1px solid {RULE}; border-radius: 0px; top: -1px; }}
QTabBar::tab {{
    background: transparent; padding: 9px 20px; margin-right: 0px;
    border: 1px solid transparent; border-bottom: 1px solid {RULE};
    color: {MUTED}; font-size: 12px; letter-spacing: 1.1px; text-transform: uppercase;
}}
QTabBar::tab:selected {{
    background: {PAPER}; color: {INK};
    border: 1px solid {RULE}; border-bottom-color: {PAPER};
}}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}

QTableWidget, QTreeWidget {{
    background: {PAPER}; border: 1px solid {RULE}; border-radius: 0px;
    gridline-color: transparent; alternate-background-color: {SURFACE};
    font-family: {MONO_STACK};
    outline: none;
}}
QTableWidget::item, QTreeWidget::item {{ padding: 7px 8px; border: none; }}
/* Selection is inverted rather than tinted - the only way to be unmistakable
   without a hue to spend. */
QTableWidget::item:selected, QTreeWidget::item:selected {{
    background: {INK}; color: {PAPER};
}}
QHeaderView::section {{
    background: {PAPER}; border: none; border-bottom: 1px solid {RULE};
    padding: 9px 8px; color: {MUTED}; font-weight: 600;
    font-size: 11px; letter-spacing: 1.1px; text-transform: uppercase;
    font-family: "Segoe UI", sans-serif;
}}
QTableCornerButton::section {{ background: {PAPER}; border: none; }}

QTextEdit, QPlainTextEdit {{
    background: {PAPER}; border: 1px solid {RULE}; border-radius: 0px;
    padding: 12px; selection-background-color: {INK}; selection-color: {PAPER};
}}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0px; }}
QScrollBar::handle:vertical {{ background: {RULE}; border-radius: 0px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0px; }}
QScrollBar::handle:horizontal {{ background: {RULE}; border-radius: 0px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {MUTED}; }}

QProgressBar {{
    background: {SURFACE}; border: 1px solid {RULE}; border-radius: 0px;
    text-align: center; height: 14px; color: {MUTED};
}}
QProgressBar::chunk {{ background: {INK}; border-radius: 0px; }}

QStatusBar {{
    background: {PAPER}; border-top: 1px solid {RULE}; color: {MUTED};
    font-family: {MONO_STACK}; font-size: 11px;
}}
QStatusBar::item {{ border: none; }}

QMenuBar {{ background: {PAPER}; border-bottom: 1px solid {RULE}; }}
QMenuBar::item {{ padding: 6px 12px; background: transparent; color: {MUTED}; }}
QMenuBar::item:selected {{ background: {INK}; color: {PAPER}; }}
QMenu {{ background: {SURFACE}; border: 1px solid {RULE}; }}
QMenu::item {{ padding: 7px 24px; }}
QMenu::item:selected {{ background: {INK}; color: {PAPER}; }}
QMenu::separator {{ height: 1px; background: {RULE}; margin: 4px 0; }}

QLabel#hint {{ color: {MUTED}; font-size: 12px; }}
QLabel#title {{
    font-size: 15px; font-weight: 700; color: {INK};
    letter-spacing: 3px; font-family: {MONO_STACK};
}}
QLabel#stamp {{
    color: {FAINT}; font-size: 11px; font-family: {MONO_STACK}; letter-spacing: 1px;
}}

QCheckBox {{ spacing: 9px; color: {MUTED}; font-size: 12px; }}
QCheckBox:hover {{ color: {TEXT}; }}
QCheckBox::indicator {{
    width: 13px; height: 13px; border: 1px solid {RULE}; background: transparent;
}}
QCheckBox::indicator:hover {{ border-color: {MUTED}; }}
QCheckBox::indicator:checked {{ background: {INK}; border-color: {INK}; }}

QSplitter::handle {{ background: {RULE}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

QComboBox {{
    background: {SURFACE}; border: 1px solid {RULE}; border-radius: 0px;
    padding: 7px 10px; color: {TEXT};
}}
QComboBox:hover {{ border-color: {MUTED}; }}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {RULE};
    selection-background-color: {INK}; selection-color: {PAPER};
}}

QToolTip {{
    background: {INK}; color: {PAPER}; border: none; padding: 5px 8px;
    font-family: {MONO_STACK}; font-size: 11px;
}}
"""
