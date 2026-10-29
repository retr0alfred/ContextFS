r"""Figures for the paper kit (log.md Phase 37): three diagrams and two result charts.

    python scripts/paper_kit_figures.py --data DIR --out DIR

Plain systems-paper style: black lines on white, two greys, one sans face, explicit arrowheads (Qt's
SVG renderer supports SVG Tiny only: no markers or patterns). Every figure is written as SVG, vector
PDF and a 300 dpi PNG. Chart values are read from the exported tables (scripts/paper_kit_export.py).
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from html import escape
from pathlib import Path

INK, MID, LIGHT, FAINT, BG = "#1F1F1F", "#7A7A7A", "#D9D9D9", "#F3F3F3", "#FFFFFF"
FONT = "Arial"


class Svg:
    """A tiny SVG builder in user units (1 unit = 1 px at 96 dpi)."""

    def __init__(self, w: int, h: int) -> None:
        self.w, self.h, self.parts = w, h, []

    def rect(self, x, y, w, h, fill=BG, stroke=INK, sw=1.0, rx=0, dash=None) -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" '
                          f'stroke-width="{sw}"{d}/>')

    def line(self, x1, y1, x2, y2, stroke=INK, sw=1.0, dash=None) -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" stroke-width="{sw}"{d}/>')

    def text(self, x, y, s, size=11, anchor="middle", weight="normal", fill=INK, italic=False) -> None:
        st = ' font-style="italic"' if italic else ""
        self.parts.append(f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" text-anchor="{anchor}" '
                          f'font-weight="{weight}" fill="{fill}"{st}>{escape(s)}</text>')

    def arrow(self, pts: list[tuple[float, float]], stroke=INK, sw=1.0, dash=None, head=6.5) -> None:
        """A polyline ending in a filled arrowhead."""
        for (x1, y1), (x2, y2) in zip(pts, pts[1:], strict=False):
            self.line(x1, y1, x2, y2, stroke, sw, dash)
        (x1, y1), (x2, y2) = pts[-2], pts[-1]
        a = math.atan2(y2 - y1, x2 - x1)
        p1 = (x2 - head * math.cos(a) + head * 0.45 * math.sin(a), y2 - head * math.sin(a) - head * 0.45 * math.cos(a))
        p2 = (x2 - head * math.cos(a) - head * 0.45 * math.sin(a), y2 - head * math.sin(a) + head * 0.45 * math.cos(a))
        self.parts.append(f'<polygon points="{x2},{y2} {p1[0]:.1f},{p1[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}" fill="{stroke}"/>')

    def box(self, x, y, w, h, title, lines=(), fill=BG, stroke=INK, sw=1.0, dash=None, size=11) -> None:
        """A component box: a bold title and smaller detail lines, centred."""
        self.rect(x, y, w, h, fill, stroke, sw, 2, dash)
        n = 1 + len(lines)
        top = y + h / 2 - (n - 1) * 7 + 4
        self.text(x + w / 2, top, title, size, weight="bold")
        for i, ln in enumerate(lines):
            self.text(x + w / 2, top + 14 * (i + 1), ln, size - 1.5, fill="#3A3A3A")

    def save(self, path: Path) -> None:
        body = "\n".join(self.parts)
        path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" version="1.2" baseProfile="tiny" width="{self.w}" '
                        f'height="{self.h}" viewBox="0 0 {self.w} {self.h}">\n<rect x="0" y="0" width="{self.w}" '
                        f'height="{self.h}" fill="{BG}"/>\n{body}\n</svg>\n', encoding="utf-8")


# ---------------------------------------------------------------------------------------------
# Diagrams
# ---------------------------------------------------------------------------------------------


def architecture() -> Svg:
    s = Svg(1000, 600)
    s.text(500, 24, "ContextFS: components and data flow", 14, weight="bold")
    # user files
    s.box(20, 150, 150, 92, "User's folders", ["read-only", "(never modified)"], fill=FAINT)
    # indexer process group
    s.rect(200, 46, 420, 330, fill=BG, stroke=MID, sw=1, dash="4,3")
    s.text(212, 64, "Indexer child process (Job Object: memory limit, dies with the window)", 10.5, "start", fill=MID)
    s.box(220, 80, 180, 56, "Walker", ["files, folders, NTFS times"])
    s.box(220, 156, 180, 70, "Extractors", ["text: PDF, Office, plain", "in-file times, EXIF,", "download mark (MotW)"])
    s.box(220, 246, 180, 56, "Embedder", ["MiniLM-L6, ONNX, CPU"])
    s.box(430, 80, 170, 56, "Time evidence", ["every raw clue, per file"])
    s.box(430, 156, 170, 70, "Event-time estimators", ["raw mtime, rules_v0,", "recon_v1 (default)"])
    s.box(430, 246, 170, 56, "Sessions", ["gap blocks (<= 500)"])
    s.box(430, 316, 170, 44, "Bulk-copy events", ["per root"], size=10.5)
    # storage
    s.box(660, 150, 150, 70, "SQLite index", ["files, chunks, FTS5,", "file_time, sessions"], fill=FAINT)
    s.box(660, 240, 150, 56, "Vector files", ["f32, append-only"], fill=FAINT)
    # GUI / query side
    s.rect(200, 412, 780, 168, fill=BG, stroke=MID, sw=1, dash="4,3")
    s.text(212, 430, "Window process (read-only engine; never writes the index)", 10.5, "start", fill=MID)
    s.box(220, 446, 140, 56, "Query", ["typed by the user"])
    s.box(385, 446, 150, 56, "Query understanding", ["time cues, file-type words"])
    s.box(560, 446, 150, 56, "Candidate pool", ["BM25 (FTS5) + exact dense"])
    s.box(735, 446, 230, 70, "Linear scorer (6 signals, dev-fitted)", ["bm25, dense, name, format,", "time (expected overlap), session"])
    s.box(735, 538, 150, 34, "Results + reasons", [], size=10.5)
    s.box(560, 538, 150, 34, "Dates panel (per event)", [], size=10.5)
    s.box(20, 446, 150, 56, "CLI / evaluation", ["same Engine API", "(parity-tested)"], fill=FAINT)
    # arrows: indexing
    s.arrow([(170, 196), (220, 108)])
    s.arrow([(310, 136), (310, 156)])
    s.arrow([(310, 226), (310, 246)])
    s.arrow([(400, 108), (430, 108)])
    s.arrow([(515, 136), (515, 156)])
    s.arrow([(515, 316), (515, 304), (605, 304), (605, 226)], head=6)
    s.arrow([(600, 191), (660, 185)])
    s.arrow([(600, 274), (660, 268)])
    s.arrow([(400, 274), (430, 274)])
    s.arrow([(400, 191), (430, 191)])
    # arrows: query
    s.arrow([(360, 474), (385, 474)])
    s.arrow([(535, 474), (560, 474)])
    s.arrow([(710, 474), (735, 474)])
    s.arrow([(810, 516), (810, 538)], head=5)
    s.arrow([(735, 555), (710, 555)], head=5)
    s.arrow([(735, 296), (735, 412), (635, 412), (635, 446)], dash="4,3")
    s.text(745, 392, "reads", 9.5, "start", fill=MID, italic=True)
    s.arrow([(170, 474), (220, 474)], dash="4,3")
    return s


def benchmark() -> Svg:
    s = Svg(1000, 500)
    s.text(500, 24, "Benchmark: from a simulated person to a damaged test corpus", 14, weight="bold")
    s.box(20, 60, 170, 80, "Persona spec", ["archetype, seed,", "timeline (years)"], fill=FAINT)
    s.box(230, 60, 200, 80, "Generator", ["files with true event times", "(SetFileTime), in-file", "metadata, download marks"])
    s.box(470, 50, 200, 46, "Clean corpus", ["undamaged NTFS times"], fill=FAINT)
    s.box(470, 106, 200, 46, "Sealed truth manifest", ["per file, per event"], fill=FAINT, dash="4,3")
    s.box(710, 60, 270, 80, "Damage simulator", ["one migration (end - 120 d)", "none / copyfile / content_copy /", "robocopy / git_clone"])
    s.box(710, 190, 270, 70, "Damaged corpus", ["what ContextFS indexes", "(births reset, marks lost, ...)"], fill=FAINT)
    s.box(470, 190, 200, 70, "Queries", ["template + local LLM,", "memory and content cues"])
    s.box(230, 190, 200, 70, "Measured transfer table", ["real Windows tools: which", "times and marks survive"])
    s.box(20, 190, 170, 70, "Fidelity replay", ["5% sample through the", "real tools: 162/162 agree"])
    s.arrow([(190, 100), (230, 100)])
    s.arrow([(430, 82), (470, 73)])
    s.arrow([(430, 118), (470, 129)])
    s.arrow([(670, 73), (710, 90)])
    s.arrow([(900, 140), (900, 190)])
    s.arrow([(330, 190), (330, 168), (780, 168), (780, 140)], dash="4,3", head=5)
    s.text(720, 162, "parameters", 9.5, fill=MID, italic=True)
    s.arrow([(190, 225), (230, 225)], dash="4,3")
    s.text(210, 218, "checks", 9, fill=MID, italic=True)
    s.arrow([(570, 152), (570, 190)])
    # split
    s.rect(20, 300, 960, 180, fill=BG, stroke=MID, dash="4,3")
    s.text(32, 318, "Split (frozen before any test run; all fitting on dev)", 10.5, "start", fill=MID)
    s.box(60, 336, 380, 60, "Dev: family A, seeds 1-20", ["20 personas, 4,884 files, 758 queries", "all rules, weights and calibration fitted here"])
    s.box(560, 336, 380, 60, "Test: family B, seeds 1001-1024", ["24 personas, 5,715 files, 916 queries", "one hashed, recorded run (Phase 7)"])
    s.box(310, 414, 380, 52, "Scorer (harness only)", ["reads the sealed truth; the engine never does"], fill=FAINT)
    s.arrow([(845, 260), (845, 284), (750, 284), (750, 336)])
    s.arrow([(845, 284), (330, 284), (330, 336)])
    s.arrow([(250, 396), (250, 440), (310, 440)], dash="4,3")
    s.arrow([(750, 396), (750, 440), (690, 440)], dash="4,3")
    return s


def recon() -> Svg:
    s = Svg(1240, 500)
    s.text(620, 24, "recon_v1: from raw time clues to a calibrated range per event", 14, weight="bold")
    clues = [("NTFS created (b), modified (m)", "file system"), ("In-file created / modified", "Office, PDF, zip"),
             ("Capture time", "EXIF"), ("Download mark", "Mark-of-the-Web zone"), ("Root birth-time distribution", "all files")]
    s.text(130, 56, "Evidence", 12, weight="bold")
    for i, (a, b) in enumerate(clues):
        s.box(20, 68 + i * 64, 220, 50, a, [b], fill=FAINT, size=10.5)
    s.box(290, 92, 200, 92, "Copy signatures", ["copy: b > m + 2 s", "reset: b in a bulk event", "reset, then edited: m later"])
    s.box(290, 300, 200, 60, "Bulk-copy events", ["gap clustering of births:", "30 min gap, >= 30 files"])
    s.box(540, 92, 200, 80, "Existence gating", ["an event gets a date only", "on positive evidence"])
    s.box(540, 210, 200, 92, "Per-event anchors", ["written / came into your", "files / last edited:", "point + raw interval, method"])
    s.box(790, 216, 200, 80, "Widen-only calibration", ["per method, split conformal", "alpha = 0.2, fitted on dev"])
    s.box(1030, 80, 190, 130, "Per event", ["interval [lo, hi]", "point", "confidence label", "method name",
                                            "(or: did not happen)"], fill=FAINT)
    s.box(1030, 270, 190, 56, "Ranking", ["expected soft overlap", "with the query's time cue"])
    s.box(1030, 350, 190, 56, "Explanations", ["\"between X and Y\" or a", "date; never a false edit"])
    for i in range(4):
        s.arrow([(240, 93 + i * 64), (290, 138)], head=5)
    s.arrow([(240, 349), (290, 335)])
    s.arrow([(390, 300), (390, 184)])
    s.arrow([(490, 132), (540, 132)])
    s.arrow([(640, 172), (640, 210)])
    s.arrow([(740, 256), (790, 256)])
    s.arrow([(990, 256), (1030, 180)])
    s.arrow([(1125, 210), (1125, 270)])
    s.arrow([(1220, 145), (1232, 145), (1232, 378), (1220, 378)], head=5)
    s.text(620, 452, "Only the calibration is fitted (13 methods, 20 dev personas); rules and anchors were developed on dev.",
           10.5, fill=MID)
    s.text(620, 470, "Rows for rules_v0 and raw mtime are kept beside recon_v1, so every estimator is evaluated on the same "
           "index.", 10.5, fill=MID)
    return s


# ---------------------------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------------------------


def r3(x: str, places: str = "0.001") -> str:
    """Round a decimal string half-up (re-rounding a float like 0.6715 would give 0.671)."""
    from decimal import ROUND_HALF_UP, Decimal

    return str(Decimal(x).quantize(Decimal(places), rounding=ROUND_HALF_UP))


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def axis(s: Svg, x0, y0, w, h, ymax, step, label) -> None:
    for i in range(int(round(ymax / step)) + 1):
        v = i * step
        y = y0 + h - h * v / ymax
        s.line(x0, y, x0 + w, y, stroke=LIGHT if i else INK, sw=1 if i == 0 else 0.6)
        s.text(x0 - 6, y + 4, f"{v:.1f}", 10, "end", fill="#3A3A3A")
    s.parts.append(f'<text x="{x0 - 42}" y="{y0 + h / 2}" font-family="{FONT}" font-size="11" text-anchor="middle" '
                   f'fill="{INK}" transform="rotate(-90 {x0 - 42} {y0 + h / 2})">{escape(label)}</text>')


def chart_ps(data: Path) -> Svg:
    rows = {r["queries"]: r for r in read_csv(data / "phase7_test" / "p_s.csv")}
    s = Svg(640, 400)
    s.text(320, 22, "Search quality on the held-out test split (nDCG@10)", 13, weight="bold")
    x0, y0, w, h = 80, 70, 520, 250
    axis(s, x0, y0, w, h, 0.8, 0.2, "nDCG@10")
    groups = [("all", "All queries"), ("memory_cue", "Memory cues"), ("content", "Content cues")]
    gw, bw = w / 3, 46
    for i, (k, lab) in enumerate(groups):
        r = rows[k]
        cx = x0 + gw * i + gw / 2
        for j, (raw, fill) in enumerate(((r["contextfs_ndcg10"], INK), (r["ce_rerank_ndcg10"], BG))):
            val = float(raw)
            bx = cx - bw - 2 + j * (bw + 4)
            bh = h * val / 0.8
            s.rect(bx, y0 + h - bh, bw, bh, fill=fill, stroke=INK, sw=1)
            s.text(bx + bw / 2, y0 + h - bh - 5, r3(raw), 10)
        s.text(cx, y0 + h + 18, lab, 11)
        s.text(cx, y0 + h + 32, f"n = {r['n']}  ·  difference +{r3(r['diff'])}", 9.5, fill="#3A3A3A")
        s.text(cx, y0 + h + 45, f"95% CI [{r3(r['ci95_persona_lo'])}, {r3(r['ci95_persona_hi'])}]", 9.5, fill="#3A3A3A")
    s.rect(x0 + 8, y0 - 30, 12, 10, fill=INK)
    s.text(x0 + 26, y0 - 21, "ContextFS (recon_v1, default configuration)", 10, "start")
    s.rect(x0 + 290, y0 - 30, 12, 10, fill=BG)
    s.text(x0 + 308, y0 - 21, "BM25 + dense + cross-encoder rerank", 10, "start")
    return s


def chart_coverage(data: Path) -> Svg:
    rows = read_csv(data / "phase7_test" / "pa1_rows.csv")
    get = {(r["estimator"], r["damage"], r["event"]): r for r in rows}
    ests = [("rules_v0", "rules_v0", BG), ("rules_v0_bulk", "rules_v0 + bulk-copy detection", MID),
            ("recon_v1", "recon_v1", INK)]
    dmg = [("none", "No damage"), ("copyfile", "Copy-Item"), ("robocopy", "robocopy"),
           ("content_copy", "Content-only copy"), ("git_clone", "git clone")]
    s = Svg(760, 420)
    s.text(380, 22, "Arrival-date interval coverage by damage type (test split)", 13, weight="bold")
    x0, y0, w, h = 80, 60, 650, 240
    axis(s, x0, y0, w, h, 1.0, 0.2, "Coverage (truth inside the interval)")
    gw, bw = w / len(dmg), 30
    for i, (dk, lab) in enumerate(dmg):
        cx = x0 + gw * i + gw / 2
        widths = []
        for j, (ek, _, fill) in enumerate(ests):
            r = get[(ek, dk, "arrived_here")]
            val = float(r["coverage"])
            bx = cx - 1.5 * bw - 3 + j * (bw + 3)
            bh = h * val
            s.rect(bx, y0 + h - bh, bw, max(bh, 0.6), fill=fill, stroke=INK, sw=1)
            lo, hi = float(r["coverage_ci_lo"]), float(r["coverage_ci_hi"])
            if hi - lo > 0.005:
                xm = bx + bw / 2
                s.line(xm, y0 + h - h * lo, xm, y0 + h - h * hi, stroke="#555555" if fill == INK else INK, sw=1)
            top = max(val, float(r["coverage_ci_hi"]))
            inside = val > 0.3  # tall bars carry their value at the base, clear of the error bar
            s.text(bx + bw / 2, y0 + h - 8 if inside else y0 + h - h * top - 6, r3(r["coverage"], "0.01"), 8.5,
                   fill=BG if (inside and fill in (INK, MID)) else INK)
            widths.append(r["width_p50_days"])
        n = get[("recon_v1", dk, "arrived_here")]["n"]
        s.text(cx, y0 + h + 17, lab, 10.5)
        s.text(cx, y0 + h + 30, f"n = {n} files", 9, fill="#3A3A3A")
        s.text(cx, y0 + h + 43, "median width (d):", 8.5, fill=MID)
        s.text(cx, y0 + h + 55, " / ".join(f"{float(x):,.0f}" for x in widths), 8.5, fill=MID)
    lx = x0 + 6
    for j, (_, lab, fill) in enumerate(ests):
        s.rect(lx + j * 215, y0 - 26, 12, 10, fill=fill)
        s.text(lx + j * 215 + 18, y0 - 17, lab, 10, "start")
    s.text(380, 410, "Error bars: 95% persona-cluster bootstrap CI. Widths are listed in legend order.", 9.5, fill=MID)
    return s


# ---------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------


def render(svg_path: Path) -> None:
    from PySide6.QtCore import QMarginsF, QRectF, QSizeF
    from PySide6.QtGui import QImage, QPageSize, QPainter, QPdfWriter
    from PySide6.QtSvg import QSvgRenderer

    r = QSvgRenderer(str(svg_path))
    size = r.defaultSize()
    scale = 300 / 96
    img = QImage(int(size.width() * scale), int(size.height() * scale), QImage.Format.Format_ARGB32)
    img.setDotsPerMeterX(int(300 / 0.0254))
    img.setDotsPerMeterY(int(300 / 0.0254))
    img.fill(0xFFFFFFFF)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r.render(p)
    p.end()
    img.save(str(svg_path.with_suffix(".png")))
    pdf = QPdfWriter(str(svg_path.with_suffix(".pdf")))
    pdf.setResolution(96)
    pdf.setPageSize(QPageSize(QSizeF(size.width() / 96 * 25.4, size.height() / 96 * 25.4), QPageSize.Unit.Millimeter))
    pdf.setPageMargins(QMarginsF(0, 0, 0, 0))
    p = QPainter(pdf)
    r.render(p, QRectF(0, 0, size.width(), size.height()))
    p.end()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    os.environ.pop("QT_QPA_PLATFORM", None)  # the offscreen platform has no fonts on Windows
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841 - fonts need an application
    args.out.mkdir(parents=True, exist_ok=True)
    figs = {"fig_architecture": architecture(), "fig_benchmark_pipeline": benchmark(), "fig_recon_v1_pipeline": recon(),
            "chart_ps_ndcg10": chart_ps(args.data), "chart_arrival_coverage": chart_coverage(args.data)}
    for name, svg in figs.items():
        path = args.out / f"{name}.svg"
        svg.save(path)
        render(path)
        print(f"  {name}: svg, pdf, png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
