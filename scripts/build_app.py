r"""Build the portable one-dir app: dist/ContextFS/ (Decision 95).

    .venv\Scripts\python.exe scripts\build_app.py [--clean]

Everything stays inside the project: PyInstaller's work dir is build/, its cache is
.tools/cache/pyinstaller (it defaults to %LOCALAPPDATA% on C:), and the output is dist/ContextFS/.
Prints the folder size, the largest components, and any excluded v1 library that slipped in.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
FORBIDDEN = ("torch", "sentence_transformers", "transformers", "spacy", "lancedb", "pyarrow", "scipy")


def folder_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", action="store_true", help="drop PyInstaller's cache first")
    args = parser.parse_args()
    env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(ROOT / ".tools" / "cache" / "pyinstaller"),
               PYTHONUTF8="1")
    cmd = [sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "contextfs.spec"), "--noconfirm",
           "--distpath", str(DIST), "--workpath", str(ROOT / "build" / "pyinstaller"), "--log-level", "WARN"]
    if args.clean:
        cmd.append("--clean")
    t = time.perf_counter()
    rc = subprocess.run(cmd, cwd=str(ROOT), env=env).returncode
    if rc:
        print(f"PyInstaller failed (exit {rc})")
        return rc
    app = DIST / "ContextFS"
    internal = app / "_internal"
    shutil.copy2(ROOT / "LICENSE", app / "LICENSE.txt")  # MIT, beside the exe (log.md Decision 105)
    print(f"built {app} in {time.perf_counter() - t:.0f}s: {folder_size(app) / 2**20:.0f} MB, "
          f"{sum(1 for f in app.rglob('*') if f.is_file())} files")
    parts = sorted(((folder_size(p) if p.is_dir() else p.stat().st_size, p.name) for p in internal.iterdir()),
                   reverse=True)
    for size, name in parts[:12]:
        print(f"  {size / 2**20:7.1f} MB  {name}")
    leaked = [n for n in FORBIDDEN if (internal / n).exists()]
    print("v1-only libraries bundled:", leaked or "none")
    return 1 if leaked else 0


if __name__ == "__main__":
    raise SystemExit(main())
