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


def _run_extraction(store, cfg):
    """Extract content for files whose extraction is missing or stale.

    Only files whose *content hash* differs from the one recorded at extraction
    time are reprocessed, so a scan that merely re-observed the corpus performs
    no extraction at all.

    Returns:
        The :class:`~contextfs.extract.ExtractionReport` for the batch.
    """
    from pathlib import Path as _Path

    from contextfs.extract import extract_many

    pending = store.files_needing_extraction()
    if not pending:
        from contextfs.extract import ExtractionReport

        return ExtractionReport()

    rows_by_path = {row["path"]: row for row in pending}
    items = [(_Path(row["abs_path"]), row["path"]) for row in pending]
    report = extract_many(items, config=cfg)

    for doc in report.documents:
        row = rows_by_path.get(doc.rel_path)
        if row is not None:
            store.save_document(row["id"], doc, content_hash=row["content_hash"])
    return report


def _run_entities(store, cfg):
    """Run Layer 3 over files whose entity analysis is missing or stale.

    Returns:
        A dict of aggregate counts, or one with ``files=0`` if nothing was due.
    """
    import time

    from contextfs.entities import EntityExtractor

    pending = store.files_needing_entities()
    stats = {
        "files": 0,
        "entities": 0,
        "people": 0,
        "orgs": 0,
        "locations": 0,
        "dates": 0,
        "keywords": 0,
        "duration_ms": 0.0,
        "errors": [],
    }
    if not pending:
        return stats

    started = time.perf_counter()
    extractor = EntityExtractor(
        cfg.entities.spacy_model,
        max_keywords=cfg.entities.max_keywords,
        drop_acronym_orgs=cfg.entities.drop_acronym_orgs,
    )

    from datetime import datetime

    for row in pending:
        # The file's own mtime anchors year-less date mentions ("24 Nov").
        try:
            reference = datetime.fromisoformat(row["mtime"])
        except (TypeError, ValueError):
            reference = None
        result = extractor.extract(row["path"], row["doc_text"] or "", reference_date=reference)
        spans = _tabular_spans_from_store(store, row["id"])
        store.save_entities(
            row["id"], result, content_hash=row["content_hash"], tabular_spans=spans
        )

        stats["files"] += 1
        stats["entities"] += len(result.entities)
        stats["people"] += len(result.people)
        stats["orgs"] += len(result.orgs)
        stats["locations"] += len(result.locations)
        stats["dates"] += len(result.dates)
        stats["keywords"] += len(result.keywords)
        if result.error:
            stats["errors"].append((row["path"], result.error))

    # Corpus-level pass: the same entity string typed differently in different
    # documents is resolved to the category most files agree on.
    stats["reconciled"] = store.reconcile_entity_categories()
    stats["duration_ms"] = (time.perf_counter() - started) * 1000
    return stats


def _open_vector_store(cfg):
    """Open the LanceDB vector store for this configuration."""
    from contextfs.embed import VectorStore

    return VectorStore(cfg.vector_dir, cfg.embeddings.dimension)


def _run_embeddings(store, cfg):
    """Chunk and embed files whose vectors are missing or stale."""
    from contextfs.embed import Embedder, embed_documents

    embedder = Embedder(
        cfg.embeddings.model,
        device=cfg.embeddings.device,
        batch_size=cfg.embeddings.batch_size,
        expected_dimension=cfg.embeddings.dimension,
        backend=cfg.embeddings.backend,
        num_threads=cfg.embeddings.num_threads,
    )
    from contextfs.embed import ModelNotCachedError

    try:
        return embed_documents(store, _open_vector_store(cfg), embedder, cfg)
    except ModelNotCachedError as exc:
        err_console.print(f"[bold red]{exc}[/bold red]")
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc


def _run_tree(store, cfg):
    """Rebuild the semantic tree and persist it."""
    from contextfs.summarize import Summarizer
    from contextfs.tree import build_tree

    tree, report = build_tree(store, _open_vector_store(cfg), Summarizer(cfg))
    store.save_tree(tree)
    return report


def _forget_deleted(store, cfg, deleted_paths):
    """Remove derived data belonging to files that vanished from disk.

    Deletion has to reach every store, not just SQLite. A vector left behind in
    LanceDB stays searchable and would return text for a file that no longer
    exists - a wrong answer rather than a missing one.
    """
    ids = []
    for path in deleted_paths:
        row = store.get_file(path)
        if row is not None:
            ids.append(row["id"])
    if not ids:
        return
    _open_vector_store(cfg).delete_files(ids)
    store.clear_embeddings(ids)
    store.delete_documents(ids)


def _tabular_spans_from_store(store, file_id):
    """Rebuild tabular character spans for a file from its stored blocks."""
    return [
        (block["char_start"], block["char_end"])
        for block in store.get_blocks(file_id)
        if block["is_tabular"]
    ]


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
    rehash: Annotated[
        bool,
        typer.Option(
            "--rehash",
            help="Hash every file even if its size and timestamp are unchanged. Slower, "
            "but detects content edits that preserved the timestamp.",
        ),
    ] = False,
    show_files: Annotated[
        bool, typer.Option("--show-files", help="List the changed files, not just counts.")
    ] = False,
    no_extract: Annotated[
        bool,
        typer.Option("--no-extract", help="Stop after file discovery; skip content extraction."),
    ] = False,
) -> None:
    """Index the configured root directory (read-only, incremental by default)."""
    from contextfs.scanner import Scanner
    from contextfs.store import Store

    cfg = state.config()
    if not cfg.paths.root.is_dir():
        err_console.print(
            f"[bold red]Scan root does not exist:[/bold red] {cfg.paths.root}\n"
            "Set [cyan]paths.root[/cyan] in contextfs.toml, or pass --root."
        )
        raise typer.Exit(EXIT_CONFIG_ERROR)

    if dry_run:
        # Read the existing index if there is one; otherwise use a throwaway
        # in-memory store so a dry run creates nothing on disk.
        store_ctx = (
            Store(cfg.db_path, read_only=True) if cfg.db_path.is_file() else Store.ephemeral()
        )
    else:
        cfg.ensure_data_dir()
        store_ctx = Store(cfg.db_path)

    extraction = None
    with store_ctx as store:
        scanner = Scanner(cfg)
        result = scanner.scan(store, full=full, dry_run=dry_run, rehash=rehash)
        entity_stats = None
        embedding = None
        tree_report = None
        if not dry_run and not no_extract:
            extraction = _run_extraction(store, cfg)
            entity_stats = _run_entities(store, cfg)
            if result.deleted:
                _forget_deleted(store, cfg, result.deleted)
            embedding = _run_embeddings(store, cfg)
            tree_report = _run_tree(store, cfg)

    table = Table(
        title=f"Scan of {cfg.paths.root}" + (" [dry run]" if dry_run else ""),
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Classification")
    table.add_column("Files", justify="right")
    table.add_row("[green]new[/green]", str(len(result.new)))
    table.add_row("[yellow]modified[/yellow]", str(len(result.modified)))
    table.add_row("[dim]unchanged[/dim]", str(len(result.unchanged)))
    table.add_row("[red]deleted[/red]", str(len(result.deleted)))
    table.add_section()
    table.add_row("[bold]total present[/bold]", f"[bold]{result.seen}[/bold]")
    console.print(table)

    mib = result.bytes_hashed / (1024 * 1024)
    console.print(
        f"hashed {result.files_hashed}/{result.seen} files ({mib:.2f} MiB) "
        f"in {result.duration_ms:.0f} ms  |  "
        f"reprocessing {len(result.changed)}/{result.seen} "
        f"({result.touched_fraction:.1%} of corpus)"
    )
    if result.skipped_too_large:
        console.print(
            f"[dim]{result.skipped_too_large} file(s) over the size limit, not hashed[/dim]"
        )

    if show_files:
        for label, style, items in (
            ("new", "green", [r.path for r in result.new]),
            ("modified", "yellow", [r.path for r in result.modified]),
            ("deleted", "red", result.deleted),
        ):
            for item in items:
                console.print(f"  [{style}]{label:<9}[/{style}] {item}")

    if extraction is not None and extraction.total:
        console.print(
            f"extracted {len(extraction.succeeded)}/{extraction.total} documents "
            f"({extraction.success_rate:.1%}), {extraction.total_chars:,} chars, "
            f"{extraction.summary()['tabular_documents']} with tabular content, "
            f"in {extraction.duration_ms:.0f} ms"
        )
        for doc in extraction.failed:
            err_console.print(f"  [red]extract failed[/red] {doc.rel_path}: {doc.error}")
        if state.verbose:
            for doc in extraction.with_warnings:
                for warning in doc.warnings:
                    err_console.print(f"  [yellow]warn[/yellow] {doc.rel_path}: {warning}")
    elif extraction is not None:
        console.print("[dim]extraction: nothing to do, all documents current[/dim]")

    if entity_stats and entity_stats["files"]:
        console.print(
            f"entities: {entity_stats['entities']} mentions "
            f"({entity_stats['people']} people, {entity_stats['orgs']} orgs, "
            f"{entity_stats['locations']} locations), "
            f"{entity_stats['dates']} raw date mentions, "
            f"{entity_stats['keywords']} keywords "
            f"over {entity_stats['files']} files in {entity_stats['duration_ms']:.0f} ms"
        )
        for path, error in entity_stats["errors"]:
            err_console.print(f"  [red]entities failed[/red] {path}: {error}")
    elif entity_stats is not None:
        console.print("[dim]entities: nothing to do, all analyses current[/dim]")

    if embedding is not None and embedding.files:
        summary = embedding.summary()
        console.print(
            f"embeddings: {embedding.chunks} chunks over {embedding.files} files "
            f"in {embedding.duration_ms:.0f} ms "
            f"({summary['chunks_per_second']} chunks/s)"
        )
        for path, error in embedding.errors:
            err_console.print(f"  [red]embedding failed[/red] {path}: {error}")
    elif embedding is not None:
        console.print("[dim]embeddings: nothing to do, all vectors current[/dim]")

    if tree_report is not None:
        kinds = tree_report.by_kind
        console.print(
            f"semantic tree: {tree_report.nodes} nodes "
            f"({kinds.get('project', 0)} projects, {kinds.get('folder', 0)} folders, "
            f"{kinds.get('file', 0)} files, {kinds.get('chunk', 0)} chunks), "
            f"{tree_report.summaries} summaries via {tree_report.summary_backend} "
            f"in {tree_report.duration_ms:.0f} ms"
        )

    if result.errors:
        err_console.print(f"[yellow]{len(result.errors)} error(s) during scan:[/yellow]")
        for path, stage, message in result.errors[:10]:
            err_console.print(f"  [{stage}] {path}: {message}")

    if dry_run:
        console.print("[dim]Dry run: nothing was written to the index.[/dim]")
    console.print(
        "[dim]Entities, embeddings, graph, timeline and sessions land in Phase 6 onward.[/dim]"
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


@app.command(name="fetch-models")
def fetch_models() -> None:
    """Download the local models ContextFS needs. The only networked command.

    Indexing never contacts the network: model loading is forced offline so a
    scan cannot silently make an outbound request. This command exists so that
    the one-time download is explicit, announced, and separable from everyday
    use - which is what "local-first" has to mean in practice.
    """
    from contextfs.embed import download_models

    cfg = state.config()
    console.print(
        Panel(
            "This is the only ContextFS command that uses the network.\n"
            f"Fetching [cyan]{cfg.embeddings.model}[/cyan] into the local model cache.\n"
            "Nothing about your files is sent anywhere.",
            title="Fetching models",
            border_style="cyan",
        )
    )
    for line in download_models(cfg.embeddings.model, cfg.entities.spacy_model):
        console.print(f"  {line}")
    console.print("[green]Done.[/green] Indexing will now run fully offline.")


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
