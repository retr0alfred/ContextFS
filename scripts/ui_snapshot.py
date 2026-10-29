r"""Render the real desktop window to PNGs for design review (dev tool; log.md Phase 36).

    python scripts/ui_snapshot.py --index DIR --query "..." --out DIR [--tag NAME] [--schemes light dark]

Opens ``MainWindow`` on a *copy* of an existing index, off-screen, with the launch update disabled
(nothing is indexed and the index is never written), runs one search, and saves: the window with
results, the empty start state, Settings, and the first-run wizard, in each colour scheme.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", type=Path, required=True)
    ap.add_argument("--query", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--tag", default="ui")
    ap.add_argument("--schemes", nargs="+", default=["light", "dark"])
    ap.add_argument("--size", default="1280x800")
    args = ap.parse_args()
    if len(args.schemes) > 1:  # one window per process, as in the app
        import subprocess

        for scheme in args.schemes:
            rest = [x for x in sys.argv[1:] if x not in args.schemes and x != "--schemes"]
            subprocess.run([sys.executable, __file__, *rest, "--schemes", scheme], check=True)
        return 0
    home = Path(tempfile.mkdtemp(prefix="cfs-ui-"))
    os.environ["CONTEXTFS_HOME"] = str(home)
    shutil.copytree(args.index, home / "index")
    from PySide6.QtCore import QEventLoop, Qt, QTimer
    from PySide6.QtWidgets import QApplication

    from contextfs.app import gui
    from contextfs.app.settings import Settings

    app = QApplication.instance() or QApplication(sys.argv)
    gui.MainWindow.start_update = lambda self: None  # never index while rendering
    s = Settings(roots=[str(args.index)], index_dir=str(home / "index"), first_run_done=True)
    w, h = map(int, args.size.split("x"))
    args.out.mkdir(parents=True, exist_ok=True)

    def settle(ms: int = 400) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    for scheme in args.schemes:
        app.styleHints().setColorScheme(Qt.ColorScheme.Light if scheme == "light" else Qt.ColorScheme.Dark)
        try:
            from contextfs.app.theme import theme
        except ImportError:  # the window before Phase 36 had no theme module
            theme = None
        if theme is not None:
            theme.apply(app, scheme)
        win = gui.MainWindow(s)
        win.resize(w, h)
        win.move(-4000, 0)
        win.show()
        settle()
        win.grab().save(str(args.out / f"{args.tag}-{scheme}-empty.png"))
        win.box.setText(args.query)
        win.search()
        t = time.perf_counter()
        while win.current is None and time.perf_counter() - t < 20:
            settle(100)
        settle()
        win.grab().save(str(args.out / f"{args.tag}-{scheme}-results.png"))
        def grab_settings(scheme=scheme, win=win) -> None:
            for x in app.topLevelWidgets():
                if x.isVisible() and x is not win and x.windowTitle().startswith("Settings"):
                    x.grab().save(str(args.out / f"{args.tag}-{scheme}-settings.png"))
                    x.reject()

        QTimer.singleShot(600, grab_settings)
        win.open_settings()
        wiz = gui.FirstRun(Settings(index_dir=str(home / "index")))
        wiz.move(-4000, 0)
        wiz.show()
        settle()
        wiz.grab().save(str(args.out / f"{args.tag}-{scheme}-firstrun.png"))
        wiz.close()
        win.close()
        settle(200)
    shutil.rmtree(home, ignore_errors=True)
    print(f"saved to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
