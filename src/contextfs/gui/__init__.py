"""Native desktop application for ContextFS (Phases 27-28).

Imports are kept lazy: PySide6 is an optional extra, and `import contextfs`
must not fail on a machine that only wants the CLI.
"""

from __future__ import annotations

__all__ = ["launch", "build_visualisation"]


def launch(config) -> int:
    """Start the desktop application. Requires the `gui` extra."""
    from contextfs.gui.app import launch as _launch

    return _launch(config)


def build_visualisation(config, out=None):
    """Build the 3D graph page. Needs no GUI toolkit - pure HTML generation."""
    from contextfs.gui.visualise import build_visualisation as _build

    return _build(config, out)
