"""ContextFS command-line entry point.

Performance note (target hardware: Ryzen 7 3700U, no GPU):
``contextfs --help`` must not pay for the ML stack. Importing ``torch`` and
``spacy`` at module scope costs several seconds of process start-up on this
machine, which would make every CLI invocation - including ``--help`` and
``stats`` - feel broken. Therefore **no heavy dependency is imported at module
level in this file**. Commands import what they need inside the function body.
See log.md, Phase 2, Decision 9.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from contextfs import __version__

#: Exit code used when a command exists but its implementation lands in a
#: later build phase. Distinct from 1 (error) so scripts can tell them apart.
EXIT_NOT_IMPLEMENTED = 3
#: Exit code for configuration problems.
EXIT_CONFIG_ERROR = 2

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    name="contextfs",
    help=(
        "ContextFS - context-aware, time-intelligent file retrieval.\n\n"
        "Find files by what you remember (projects, deadlines, exams, work "
        "sessions) rather than by where you filed them. Local-first, "
        "read-only, and explainable: every result tells you why it matched."
    ),
    no_args_is_help=True,
    add_completion=True,
    rich_markup_mode="rich",
)


class CLIState:
    """Global options shared by every subcommand.

    Attributes:
        config_path: Explicit config file from ``--config``, if given.
        root: Scan-root override from ``--root``, if given.
        data_dir: Derived-data override from ``--data-dir``, if given.
        verbose: Whether to emit diagnostic detail.
    """

    def __init__(self) -> None:
        """Start with no options set and no configuration loaded."""
        self.config_path: Path | None = None
        self.root: Path | None = None
        self.data_dir: Path | None = None
        self.verbose: bool = False
        self._config = None

    def configure(
        self,
        config_path: Path | None,
        root: Path | None,
        data_dir: Path | None,
        verbose: bool,
    ) -> None:
        """Record global options and invalidate any cached configuration.

        Invalidation matters: ``state`` is a module-level singleton, so in a
        long-lived process (the test runner, and the desktop GUI in Phase 27)
        a second invocation with different flags would otherwise silently reuse
        the first invocation's configuration.
        """
        self.config_path = config_path
        self.root = root
        self.data_dir = data_dir
        self.verbose = verbose
        self._config = None

    def config(self):
        """Load and cache the resolved configuration.

        Exits the process with :data:`EXIT_CONFIG_ERROR` and a readable message
        rather than a traceback if the configuration is invalid.
        """
        if self._config is None:
            from contextfs.config import ConfigError, load_config

            try:
                self._config = load_config(self.config_path, root=self.root, data_dir=self.data_dir)
            except ConfigError as exc:
                err_console.print(f"[bold red]Configuration error:[/bold red] {exc}")
                raise typer.Exit(EXIT_CONFIG_ERROR) from exc
        return self._config


state = CLIState()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"contextfs {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to a config file. Default: search upward for contextfs.local.toml, then contextfs.toml.",
        ),
    ] = None,
    root: Annotated[
        Path | None,
        typer.Option(
            "--root",
            "-r",
            help="Directory to index. Overrides the config file. ContextFS never writes here.",
        ),
    ] = None,
    data_dir: Annotated[
        Path | None,
        typer.Option("--data-dir", help="Where ContextFS stores its own derived data."),
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show diagnostic detail.")
    ] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
        ),
    ] = False,
) -> None:
    """Global options applied to every subcommand."""
    _ = version  # consumed by its eager callback
    state.configure(config, root, data_dir, verbose)


def _not_implemented(command: str, phase: int, what: str) -> None:
    """Report an unimplemented command clearly and exit without a traceback."""
    console.print(
        Panel(
            f"[yellow]`contextfs {command}` is not yet implemented.[/yellow]\n\n"
            f"{what}\n\n"
            f"[dim]Lands in build Phase {phase}. See log.md for phase status.[/dim]",
            title="Not yet implemented",
            border_style="yellow",
        )
    )
    raise typer.Exit(EXIT_NOT_IMPLEMENTED)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def scan(
    full: Annotated[
        bool, typer.Option("--full", help="Force a full reindex instead of an incremental scan.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report what would change without indexing.")
    ] = False,
) -> None:
    """Index the configured root directory (read-only, incremental by default)."""
    _ = (full, dry_run)
    _not_implemented(
        "scan",
        4,
        "Will walk the configured root, classify files as new/modified/unchanged/deleted, "
        "then run extraction, entities, embeddings, graph, timeline and sessions over "
        "only what changed.",
    )


@app.command()
def query(
    text: Annotated[str, typer.Argument(help="What you remember about the file.")],
    top_k: Annotated[int, typer.Option("--top-k", "-k", help="Number of results.")] = 10,
    explain: Annotated[
        bool, typer.Option("--explain", "-e", help="Show why each result matched.")
    ] = False,
    baseline: Annotated[
        bool,
        typer.Option(
            "--baseline", help="Use the pure-semantic baseline instead of the full system."
        ),
    ] = False,
) -> None:
    """Search the index using context, not just content."""
    _ = (text, top_k, explain, baseline)
    _not_implemented(
        "query",
        15,
        "Will decompose the query into topic / entity / temporal components, select seed "
        "nodes, traverse the relationship graph, and rank by a weighted combination of "
        "semantic, graph, activity and timeline signals.",
    )


@app.command()
def timeline(
    span: Annotated[
        str, typer.Argument(help='A date range, e.g. "March to April" or "last week".')
    ],
    top_k: Annotated[int, typer.Option("--top-k", "-k", help="Number of results.")] = 10,
) -> None:
    """List files whose meaningful dates fall inside a time range."""
    _ = (span, top_k)
    _not_implemented(
        "timeline",
        11,
        "Will resolve the natural-language range, query an interval tree over dates that "
        "were classified meaningful (not incidental), and return the files behind them.",
    )


@app.command()
def explain(
    result_id: Annotated[str, typer.Argument(help="Result id from a previous query.")],
) -> None:
    """Show, in full, why a particular result was retrieved."""
    _ = result_id
    _not_implemented(
        "explain",
        16,
        "Will print the complete explanation object: matched topic, matched entities, "
        "matched activity session, matched timeline dates, and the graph path that "
        "connected the seed node to this file.",
    )


@app.command()
def stats() -> None:
    """Report corpus size, index size, graph size, and index freshness."""
    _not_implemented(
        "stats",
        17,
        "Will report file counts by type, entity/embedding/graph/timeline/session counts, "
        "last scan time, and whether the index is stale relative to the root.",
    )


@app.command()
def reset(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Delete ContextFS's derived data. Never touches your files."""
    _ = yes
    _not_implemented(
        "reset",
        17,
        "Will delete only the ContextFS data directory (SQLite, LanceDB, graph). Scanned "
        "files are never touched - ContextFS opens them read-only.",
    )


@app.command(name="config")
def config_cmd(
    show_paths: Annotated[bool, typer.Option("--paths", help="Show only resolved paths.")] = False,
) -> None:
    """Show the resolved configuration and where each value came from.

    Implemented from Phase 2 onward: configuration is the one thing that must
    be inspectable before anything else works, otherwise every later phase is
    debugged blind.
    """
    cfg = state.config()
    info = cfg.describe()

    table = Table(show_header=True, header_style="bold cyan", title="ContextFS configuration")
    table.add_column("Setting", style="dim", no_wrap=True)
    # `overflow="fold"` rather than the default ellipsis: a truncated path in
    # the one command whose job is showing paths would be actively misleading.
    table.add_column("Value", overflow="fold")

    path_keys = {
        "config_file",
        "scan_root",
        "root_exists",
        "data_dir",
        "sqlite",
        "vectors",
        "graph",
    }
    for key, value in info.items():
        if show_paths and key not in path_keys:
            continue
        if key == "root_exists":
            value = "[green]yes[/green]" if value else "[yellow]no (not scanned yet)[/yellow]"
        table.add_row(key, str(value))

    console.print(table)

    if cfg.source_file is None:
        err_console.print(
            "[yellow]No config file found; using built-in defaults.[/yellow] "
            "Create contextfs.toml, or pass --config."
        )


if __name__ == "__main__":  # pragma: no cover
    app()
