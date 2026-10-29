"""The desktop window's look: one neutral grey scale, light and dark (log.md Phase 36).

There is no accent hue. Hierarchy comes from weight, size and spacing, and the only "strong" colour
is the ink (near-black on light, near-white on dark), used for the primary action, focus and the
selected row's marker. The scheme follows Windows (and changes live with it) unless Settings picks
Light or Dark. Icons are glyphs from the system icon font, drawn in the current ink.

Nothing here touches search, indexing or any evaluated behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QPainter,
    QPalette,
    QPixmap,
)

__all__ = ["Palette", "Theme", "glyph_icon", "theme"]


@dataclass(frozen=True)
class Palette:
    """Every colour the window uses."""

    name: str
    bg: str  # window background
    surface: str  # panes, fields, lists
    sunken: str  # quotes, code-like text
    border: str
    border_strong: str
    text: str
    muted: str
    faint: str
    hover: str
    selected: str
    ink: str  # the one strong colour: primary button, focus, selection marker
    on_ink: str


LIGHT = Palette("light", bg="#F5F5F3", surface="#FFFFFF", sunken="#F7F7F5", border="#E4E4E1",
                border_strong="#CBCBC7", text="#1A1A19", muted="#5E5E5A", faint="#8F8F8A", hover="#F1F1EF",
                selected="#EAEAE7", ink="#1A1A19", on_ink="#FFFFFF")
DARK = Palette("dark", bg="#131313", surface="#1A1A1A", sunken="#161616", border="#2A2A2A",
               border_strong="#3B3B3B", text="#ECECEA", muted="#A2A29E", faint="#6E6E6A", hover="#212121",
               selected="#282828", ink="#ECECEA", on_ink="#131313")

#: Glyphs from Segoe Fluent Icons (Windows 11) / Segoe MDL2 Assets (Windows 10); same code points.
GLYPH = {"search": "", "settings": "", "refresh": "", "open": "", "folder": "",
         "add": "", "remove": "", "delete": "", "cancel": ""}


def _icon_family() -> str | None:
    fams = set(QFontDatabase.families())
    return next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in fams), None)


def _text_family(display: bool = False) -> str:
    fams = set(QFontDatabase.families())
    for f in (("Segoe UI Variable Display",) if display else ()) + ("Segoe UI Variable Text", "Segoe UI"):
        if f in fams:
            return f
    return QGuiApplication.font().family()


def glyph_icon(name: str, color: str, px: int = 16) -> QIcon:
    """An icon drawn from the system icon font (empty if the font is missing)."""
    family = _icon_family()
    if family is None:
        return QIcon()
    ratio = 2.0
    pm = QPixmap(int(px * ratio), int(px * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    f = QFont(family)
    f.setPixelSize(int(px * 0.85))
    p.setFont(f)
    p.setPen(QColor(color))
    p.drawText(QRectF(0, 0, px, px), Qt.AlignmentFlag.AlignCenter, GLYPH[name])
    p.end()
    return QIcon(pm)


def _qss(c: Palette) -> str:
    return f"""
* {{ outline: 0; }}
QMainWindow, QDialog, QWizard, QWizard QWidget#qt_wizard_page {{ background: {c.bg}; }}
QWidget {{ color: {c.text}; }}
QToolTip {{ background: {c.surface}; color: {c.text}; border: 1px solid {c.border_strong}; padding: 4px 6px; }}

QLineEdit#search {{
    background: {c.surface}; border: 1px solid {c.border_strong}; border-radius: 8px;
    padding: 0 10px; min-height: 38px; font-size: 15px; selection-background-color: {c.ink};
    selection-color: {c.on_ink};
}}
QLineEdit#search:focus {{ border: 1px solid {c.ink}; }}
QLineEdit, QPlainTextEdit, QComboBox {{
    background: {c.surface}; border: 1px solid {c.border}; border-radius: 6px; padding: 4px 8px;
    selection-background-color: {c.ink}; selection-color: {c.on_ink};
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {c.ink}; }}
QComboBox::drop-down {{ border: 0; width: 18px; }}
QComboBox QAbstractItemView {{ background: {c.surface}; border: 1px solid {c.border_strong};
    selection-background-color: {c.selected}; selection-color: {c.text}; }}

QPushButton {{
    background: transparent; border: 1px solid {c.border_strong}; border-radius: 6px;
    padding: 5px 12px; min-height: 20px;
}}
QPushButton:hover {{ background: {c.hover}; }}
QPushButton:pressed {{ background: {c.selected}; }}
QPushButton:focus {{ border-color: {c.ink}; }}
QPushButton:disabled {{ color: {c.faint}; border-color: {c.border}; }}
QPushButton[variant="primary"] {{ background: {c.ink}; color: {c.on_ink}; border-color: {c.ink}; }}
QPushButton[variant="primary"]:hover {{ background: {c.muted}; border-color: {c.muted}; }}
QPushButton[variant="quiet"] {{ border-color: transparent; color: {c.muted}; }}
QPushButton[variant="quiet"]:hover {{ color: {c.text}; background: {c.hover}; }}
QPushButton[variant="danger"] {{ color: {c.text}; }}
QPushButton[variant="pill"] {{
    border-radius: 13px; padding: 3px 12px; min-height: 18px; color: {c.faint}; border-color: {c.border};
}}
QPushButton[variant="pill"]:checked {{ color: {c.text}; background: {c.selected}; border-color: {c.border_strong}; }}

QLineEdit#path {{ background: transparent; border: 0; padding: 0; color: {c.muted}; }}
QListView#results, QTextBrowser#details, QListWidget {{
    background: {c.surface}; border: 0;
}}
QListWidget {{ border: 1px solid {c.border}; border-radius: 6px; padding: 2px; }}
QListWidget::item {{ padding: 5px 6px; border-radius: 4px; }}
QListWidget::item:selected {{ background: {c.selected}; color: {c.text}; }}
QFrame#pane {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 10px; }}
QFrame#rule {{ background: {c.border}; max-height: 1px; min-height: 1px; border: 0; }}
QSplitter::handle {{ background: transparent; width: 10px; }}

QLabel[role="title"] {{ font-size: 17px; font-weight: 600; }}
QLabel[role="heading"] {{ font-size: 13px; font-weight: 600; }}
QLabel[role="muted"] {{ color: {c.muted}; }}
QLabel[role="faint"] {{ color: {c.faint}; }}
QLabel[role="token"] {{
    color: {c.muted}; background: {c.sunken}; border: 1px solid {c.border}; border-radius: 4px; padding: 1px 6px;
}}

QStatusBar {{ background: {c.bg}; color: {c.muted}; border-top: 1px solid {c.border}; }}
QStatusBar::item {{ border: 0; }}
QStatusBar QLabel {{ color: {c.muted}; padding: 0 4px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c.border_strong}; border-radius: 3px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {c.faint}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c.border_strong}; border-radius: 3px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def _qpalette(c: Palette) -> QPalette:
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: c.bg, QPalette.ColorRole.WindowText: c.text, QPalette.ColorRole.Base: c.surface,
        QPalette.ColorRole.AlternateBase: c.sunken, QPalette.ColorRole.Text: c.text, QPalette.ColorRole.Button: c.surface,
        QPalette.ColorRole.ButtonText: c.text, QPalette.ColorRole.Highlight: c.ink,
        QPalette.ColorRole.HighlightedText: c.on_ink, QPalette.ColorRole.ToolTipBase: c.surface,
        QPalette.ColorRole.ToolTipText: c.text, QPalette.ColorRole.PlaceholderText: c.faint,
        QPalette.ColorRole.Link: c.text, QPalette.ColorRole.Mid: c.border_strong, QPalette.ColorRole.Dark: c.border_strong,
        QPalette.ColorRole.Light: c.surface, QPalette.ColorRole.Midlight: c.border, QPalette.ColorRole.Shadow: c.border,
    }
    for role, col in roles.items():
        p.setColor(role, QColor(col))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(c.faint))
    return p


class Theme(QObject):
    """Applies the palette to the application and tells windows when it changes."""

    changed = Signal()

    def __init__(self) -> None:
        """Start with the light palette until ``apply`` runs."""
        super().__init__()
        self.c = LIGHT
        self.choice = "system"
        self._hooked = False

    def apply(self, app, choice: str = "system") -> Palette:
        """Use ``choice`` ("system", "light" or "dark") and restyle the whole application."""
        self.choice = choice if choice in ("system", "light", "dark") else "system"
        hints = app.styleHints()
        if self.choice == "system":
            hints.unsetColorScheme()
        else:  # also gives the native title bar the matching colour
            hints.setColorScheme(Qt.ColorScheme.Dark if self.choice == "dark" else Qt.ColorScheme.Light)
        dark = (hints.colorScheme() == Qt.ColorScheme.Dark) if self.choice == "system" else self.choice == "dark"
        self.c = DARK if dark else LIGHT
        app.setStyle("Fusion")
        app.setPalette(_qpalette(self.c))
        font = QFont(_text_family())
        font.setPointSizeF(10)
        app.setFont(font)
        app.setStyleSheet(_qss(self.c))
        if not self._hooked:
            hints.colorSchemeChanged.connect(lambda _=None: self.apply(app, self.choice) if self.choice == "system"
                                             else None)
            self._hooked = True
        self.changed.emit()
        return self.c

    @staticmethod
    def display_font(px: int, weight: QFont.Weight = QFont.Weight.DemiBold) -> QFont:
        """The heading face at a pixel size."""
        f = QFont(_text_family(display=True))
        f.setPixelSize(px)
        f.setWeight(weight)
        return f


theme = Theme()
