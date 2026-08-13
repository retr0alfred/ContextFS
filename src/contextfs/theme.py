"""The single source of truth for how ContextFS looks, everywhere.

One monochrome design system, consumed by three very different surfaces: the
Rich-rendered CLI, the Qt desktop application, and the generated three.js page.
They share this module so that "activity" looks like the same thing in all
three, and so a change here changes all of them at once.

Why monochrome
--------------
Black and white, stencil-and-bracket, high-contrast — the visual language of
in-world hacker broadcasts. Restrained rather than loud: no glitch animation,
no spray-paint textures, no chromatic aberration. Sharp corners, thin rules,
monospace for anything machine-generated.

The design problem this created, and how it is solved
-----------------------------------------------------
The previous palette used **colour to carry information**: semantic was blue,
graph violet, activity amber, timeline green. Removing colour therefore removes
a data channel, and desaturating a colour-coded UI produces four indistinguishable
greys — strictly worse than what it replaced.

So colour is replaced by *two* channels rather than none:

1. **Luminance.** Each signal owns a fixed grey. They are spaced far enough
   apart to stay distinguishable side by side.
2. **A glyph.** Each signal owns a mark - ``◆ ◈ ● ▲``. This is the channel that
   actually does the work: it survives greyscale printing, low contrast
   displays, and colour blindness, none of which the old palette did.

The glyph is the primary encoding and the grey is secondary reinforcement. That
ordering is deliberate: a legend keyed on shape is readable in conditions where
one keyed on brightness is not.
"""

from __future__ import annotations

__all__ = [
    "INK",
    "PAPER",
    "SURFACE",
    "SURFACE_HI",
    "RULE",
    "TEXT",
    "MUTED",
    "FAINT",
    "SIGNAL_GLYPHS",
    "SIGNAL_GLYPHS_ASCII",
    "signal_glyphs",
    "SIGNAL_GREYS",
    "NODE_GREYS",
    "NODE_GLYPHS",
    "EDGE_GREYS",
    "STAGE_GREYS",
    "CLI",
    "MONO_STACK",
    "rule",
    "banner",
]

# -- core tones -------------------------------------------------------------
# Pure #000 is avoided for large fills: on OLED it produces smearing on scroll,
# and on LCD it crushes the thin rules that carry most of this design's
# structure. #050505 reads as black while keeping 1px borders visible.
PAPER = "#050505"
SURFACE = "#0d0d0d"
SURFACE_HI = "#161616"
RULE = "#2b2b2b"
INK = "#ffffff"
TEXT = "#e8e8e8"
MUTED = "#8a8a8a"
FAINT = "#5a5a5a"

#: Monospace stack. Used for every machine-generated value - scores, paths,
#: counts - so numbers align in columns and never reflow between proportional
#: glyph widths.
MONO_STACK = '"Cascadia Mono", "JetBrains Mono", Consolas, "Courier New", monospace'

# -- signal identity --------------------------------------------------------
#: The primary encoding. Shape survives what brightness does not.
SIGNAL_GLYPHS = {
    "semantic": "◆",
    "graph": "◈",
    "activity": "●",
    "timeline": "▲",
}

#: ASCII fallback for terminals that cannot encode the box-drawing set.
#:
#: This is not defensive padding - it is required. A Windows console left on the
#: default cp1252 code page raises UnicodeEncodeError on "●", which would
#: turn a decorative flourish into a crash on the project's own target platform.
#: Redirected output (`contextfs query ... > out.txt`) hits the same path.
SIGNAL_GLYPHS_ASCII = {
    "semantic": "*",
    "graph": "+",
    "activity": "o",
    "timeline": "^",
}

#: Secondary reinforcement. Spaced ~40 luminance points apart so that adjacent
#: rows remain separable; anything tighter turns into one grey at a glance.
SIGNAL_GREYS = {
    "semantic": "#ffffff",
    "graph": "#b8b8b8",
    "activity": "#8a8a8a",
    "timeline": "#5f5f5f",
}

# -- graph node identity ----------------------------------------------------
NODE_GREYS = {
    "file": "#ffffff",
    "session": "#bdbdbd",
    "date": "#8a8a8a",
    "folder": "#6e6e6e",
    "project": "#565656",
}

NODE_GLYPHS = {
    "file": "▪",
    "session": "●",
    "date": "▲",
    "folder": "◆",
    "project": "■",
}

#: Edge tones. `structural` is deliberately near-invisible and starts hidden:
#: it is by far the densest edge type (326 of 597 on the demo corpus) and
#: leaving it on renders the interesting relations unreadable underneath it.
EDGE_GREYS = {
    "semantic": "#ffffff",
    "entity": "#c4c4c4",
    "structural": "#3a3a3a",
    "duplicate": "#ededed",
    "temporal": "#7d7d7d",
    "activity": "#a8a8a8",
}

#: Project-lifecycle stages, brightest = most current.
STAGE_GREYS = {
    "upcoming": "#ffffff",
    "active": "#d6d6d6",
    "dormant": "#8a8a8a",
    "finished": "#5a5a5a",
}


class CLI:
    """Rich style names for the terminal.

    Rich cannot address arbitrary hex reliably across every Windows terminal,
    so the CLI works in the 16-colour space and expresses hierarchy through
    weight and dimming instead of hue. The result is the same design read
    through a coarser instrument, not a different one.
    """

    #: Headings and anything the user should read first.
    HEAD = "bold white"
    #: Ordinary emphasis.
    STRONG = "bold"
    #: Body text needs no markup at all; the terminal's default is the body.
    BODY = ""
    #: Secondary detail - counts, timings, provenance.
    DIM = "dim"
    #: Table headers and structural chrome.
    CHROME = "bold white"
    #: A value worth noticing but not an error.
    NOTICE = "bold white"
    #: Something went wrong. Reverse video rather than red - it is the only
    #: high-salience device available without colour, and it is unmissable.
    ALERT = "bold reverse"
    #: Success. Stated plainly; a tick does the work a green would have.
    OK = "bold white"
    #: Rules, borders, panels.
    BORDER = "grey42"


def signal_glyphs(stream=None) -> dict[str, str]:
    """Return the glyph set the given stream can actually print.

    Asks the stream's own encoder rather than guessing from the platform or the
    code page: the question is only ever "can this specific output sink encode
    this specific character", and encoding it is the cheapest way to find out.
    """
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        "".join(SIGNAL_GLYPHS.values()).encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return dict(SIGNAL_GLYPHS_ASCII)
    return dict(SIGNAL_GLYPHS)


def rule(width: int = 78, char: str = "─") -> str:
    """A horizontal rule of the given width."""
    return char * width


def banner(text: str, width: int = 78) -> str:
    """A bracketed section header, e.g. ``── [ RESULTS ] ─────``.

    The bracket motif is the one decorative flourish in the whole system. It is
    kept because it marks machine output as machine output at a glance, and
    dropped everywhere it would compete with real content.
    """
    label = f"─ [ {text.upper()} ] "
    return label + "─" * max(0, width - len(label))
