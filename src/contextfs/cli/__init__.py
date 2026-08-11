"""Command-line interface for ContextFS (Layer 10).

The CLI is the primary product surface. Every capability of ContextFS is
reachable from here; the desktop GUI is a shell around these same code paths
and adds no functionality the CLI lacks.
"""

from contextfs.cli.main import app

__all__ = ["app"]
