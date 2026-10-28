"""ContextFS desktop app entry point (also the PyInstaller entry).

- ``ContextFS.exe``                 → the GUI
- ``ContextFS.exe --indexer ...``   → the indexer child (started by the GUI)
- ``ContextFS.exe --selftest [--gpu]`` → headless offline self test (Decision 95); ``--gpu`` also
  runs the GPU parity probe
"""

from __future__ import annotations

import sys


def main() -> int:
    """Dispatch on the command line."""
    args = sys.argv[1:]
    if args[:1] == ["--indexer"]:
        from contextfs.app.indexer_child import main as child

        return child(args[1:])
    if args[:1] == ["--selftest"]:
        from contextfs.app.selftest import run_selftest

        return run_selftest(gpu="--gpu" in args)
    from contextfs.app.gui import run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
