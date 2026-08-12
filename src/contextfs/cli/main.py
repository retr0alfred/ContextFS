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


def _run_date_classification(store, cfg):
    """Classify every date mention as meaningful or incidental."""
    import time

    from contextfs.temporal import DateClassifier

    started = time.perf_counter()
    classifier = DateClassifier(cfg)
    verdicts = classifier.collapse(classifier.classify_store(store))
    store.save_classified_dates(verdicts)
    meaningful = sum(1 for v in verdicts if v.is_meaningful)
    return {
        "total": len(verdicts),
        "meaningful": meaningful,
        "incidental": len(verdicts) - meaningful,
        "duration_ms": (time.perf_counter() - started) * 1000,
    }


def _run_sessions(store, cfg):
    """Reconstruct activity sessions and persist them."""
    from contextfs.activity import SessionBuilder

    report = SessionBuilder(cfg).build(store, _open_vector_store(cfg))
    store.save_sessions(report.sessions)
    return report


def _run_graph(store, cfg):
    """Rebuild the relationship graph and persist it."""
    from contextfs.graph import build_graph, save_graph

    graph, report = build_graph(store, _open_vector_store(cfg), cfg)
    save_graph(graph, cfg.graph_file)
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
        graph_report = None
        date_stats = None
        session_report = None
        if not dry_run and not no_extract:
            extraction = _run_extraction(store, cfg)
            entity_stats = _run_entities(store, cfg)
            if result.deleted:
                _forget_deleted(store, cfg, result.deleted)
            embedding = _run_embeddings(store, cfg)
            tree_report = _run_tree(store, cfg)
            date_stats = _run_date_classification(store, cfg)
            session_report = _run_sessions(store, cfg)
            graph_report = _run_graph(store, cfg)

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

    if date_stats is not None and date_stats["total"]:
        console.print(
            f"dates: {date_stats['meaningful']} meaningful / "
            f"{date_stats['incidental']} incidental "
            f"of {date_stats['total']} distinct (file, date) pairs "
            f"in {date_stats['duration_ms']:.0f} ms"
        )

    if session_report is not None and session_report.sessions:
        summary = session_report.summary()
        console.print(
            f"sessions: {summary['sessions']} reconstructed "
            f"({summary['clustered_files']} files clustered, "
            f"{summary['unsessioned_files']} unsessioned), "
            f"kinds {summary['by_kind']} in {session_report.duration_ms:.0f} ms"
        )
        if state.verbose:
            for session in session_report.sessions:
                console.print(
                    f"  [dim]{session.session_id}: {session.label} " f"({session.size} files)[/dim]"
                )

    if graph_report is not None:
        by_type = ", ".join(
            f"{count} {name}" for name, count in sorted(graph_report.by_type.items())
        )
        console.print(
            f"graph: {graph_report.nodes} nodes, {graph_report.edges} edges "
            f"({by_type}) in {graph_report.duration_ms:.0f} ms"
        )
        if graph_report.context_nodes:
            context = graph_report.context_nodes
            console.print(
                f"[dim]  context nodes: {context['session_nodes']} session, "
                f"{context['date_nodes']} timeline[/dim]"
            )
        if graph_report.duplicate_pairs:
            console.print(
                f"[dim]  near-duplicate pairs detected: "
                f"{len(graph_report.duplicate_pairs)}[/dim]"
            )
        if graph_report.isolated:
            console.print(
                f"[dim]  {len(graph_report.isolated)} isolated file(s) with no relationships[/dim]"
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


def _open_retrieval(cfg, store, signals=None):
    """Construct the retrieval stack shared by `query` and the eval harness."""
    from contextfs.embed import Embedder
    from contextfs.graph import load_graph
    from contextfs.retrieval import ALL_SIGNALS, HybridRetriever, SemanticBaseline
    from contextfs.temporal import TimelineIndex

    embedder = Embedder(
        cfg.embeddings.model,
        device=cfg.embeddings.device,
        batch_size=cfg.embeddings.batch_size,
        expected_dimension=cfg.embeddings.dimension,
        backend=cfg.embeddings.backend,
        num_threads=cfg.embeddings.num_threads,
    )
    vectors = _open_vector_store(cfg)
    graph = load_graph(cfg.graph_file)
    timeline = TimelineIndex.from_store(store)

    hybrid = HybridRetriever(
        store,
        vectors,
        embedder,
        graph,
        cfg,
        signals=tuple(signals) if signals else ALL_SIGNALS,
        timeline_index=timeline,
        # Interactive use gets the feedback re-rank; the evaluation harness
        # builds its own retriever without one, so measured numbers stay clean.
        feedback=store,
    )
    return hybrid, SemanticBaseline(store, vectors, embedder)


def _render_results(response, show_explanations: bool) -> None:
    """Print a retrieval response as a table, optionally with reasoning."""
    if not response.results:
        console.print("[yellow]No results.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("File", overflow="fold")
    if not show_explanations:
        table.add_column("Why", overflow="fold", style="dim")

    for result in response.results:
        row = [str(result.rank), f"{result.score:.3f}", result.path]
        if not show_explanations:
            reasons = result.explanation.reasons()
            row.append(reasons[0] if reasons else "-")
        table.add_row(*row)
    console.print(table)

    if show_explanations:
        for result in response.results:
            console.print(f"\n[bold]{result.rank}. {result.path}[/bold]  ({result.score:.3f})")
            for reason in result.explanation.reasons():
                console.print(f"   • {reason}")
            contributions = result.explanation.contributions
            console.print(
                "   [dim]" + "  ".join(f"{k}={v:.3f}" for k, v in contributions.items()) + "[/dim]"
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
    compare: Annotated[
        bool, typer.Option("--compare", help="Run both systems side by side.")
    ] = False,
    signals: Annotated[
        str,
        typer.Option(
            "--signals",
            help="Comma-separated subset of semantic,graph,activity,timeline (ablation).",
        ),
    ] = "",
) -> None:
    """Search the index using context, not just content."""
    from contextfs.store import Store

    cfg = state.config()
    if not cfg.db_path.is_file():
        err_console.print(f"[bold red]No index at[/bold red] {cfg.db_path}. Run `contextfs scan`.")
        raise typer.Exit(EXIT_CONFIG_ERROR)

    chosen = tuple(s.strip() for s in signals.split(",") if s.strip()) or None

    with Store(cfg.db_path, read_only=True) as store:
        if store.is_outdated():
            # Read-only opens deliberately do not migrate, so an index written
            # by an older build still answers - it just cannot use anything a
            # later schema added. Saying so beats silently degrading.
            err_console.print(
                "[yellow]This index was written by an older build "
                f"(schema v{store.schema_version}).[/yellow] It still works; "
                "run `contextfs scan` to upgrade it and enable newer features."
            )
        hybrid, flat = _open_retrieval(cfg, store, chosen)

        if compare:
            hybrid_response = hybrid.search(text, top_k)
            baseline_response = flat.search(text, top_k)
            console.print(f"[bold]Query:[/bold] {text}")
            console.print(f"[dim]read as: {hybrid_response.decomposition.describe()}[/dim]\n")
            console.print("[bold cyan]BASELINE (semantic only)[/bold cyan]")
            _render_results(baseline_response, False)
            console.print(f"[dim]{baseline_response.latency_ms:.0f} ms[/dim]\n")
            console.print("[bold green]CONTEXTFS (hybrid)[/bold green]")
            _render_results(hybrid_response, explain)
            console.print(
                f"[dim]{hybrid_response.latency_ms:.0f} ms, "
                f"{hybrid_response.expanded_nodes} candidates, "
                f"seeds: {', '.join(hybrid_response.seeds[:4]) or 'none'}[/dim]"
            )
            _remember_results(cfg, hybrid_response)
            return

        response = flat.search(text, top_k) if baseline else hybrid.search(text, top_k)

    console.print(f"[bold]Query:[/bold] {text}")
    if response.decomposition is not None:
        console.print(f"[dim]read as: {response.decomposition.describe()}[/dim]")
    if response.seeds:
        console.print(f"[dim]seeds: {', '.join(response.seeds[:5])}[/dim]")
    console.print()
    _render_results(response, explain)
    console.print(
        f"\n[dim]{response.system} | {response.latency_ms:.0f} ms | "
        f"{response.expanded_nodes} candidates considered | "
        f"weights {response.weights}[/dim]"
    )
    _remember_results(cfg, response)


def _remember_results(cfg, response) -> None:
    """Cache the last result set so `contextfs explain <id>` can reference it."""
    import json

    cfg.ensure_data_dir()
    path = cfg.paths.data_dir / "last_query.json"
    path.write_text(json.dumps(response.as_dict(), indent=2), encoding="utf-8")


def _load_last_query(cfg):
    """Return the cached last query response, or None."""
    import json

    cached = cfg.paths.data_dir / "last_query.json"
    if not cached.is_file():
        return None
    return json.loads(cached.read_text(encoding="utf-8"))


@app.command()
def feedback(
    pick: Annotated[
        int, typer.Option("--pick", "-p", help="Rank number from the last query that was right.")
    ] = 0,
    reject: Annotated[int, typer.Option("--reject", "-r", help="Rank number that was wrong.")] = 0,
    show: Annotated[bool, typer.Option("--show", help="List recorded feedback.")] = False,
    clear: Annotated[bool, typer.Option("--clear", help="Delete all recorded feedback.")] = False,
) -> None:
    """Tell ContextFS which of the last query's results was the one you wanted.

    Feedback is scoped to the exact query text and only nudges near-ties - it
    is a small bounded re-rank, never an override, and it never touches the
    measured evaluation numbers.
    """
    from contextfs.store import Store

    cfg = state.config()
    if not cfg.db_path.is_file():
        err_console.print(f"[bold red]No index at[/bold red] {cfg.db_path}. Run `contextfs scan`.")
        raise typer.Exit(EXIT_CONFIG_ERROR)

    with Store(cfg.db_path) as store:
        if clear:
            removed = store.clear_feedback()
            console.print(f"Cleared [bold]{removed}[/bold] feedback event(s).")
            return

        if show or not (pick or reject):
            events = store.feedback_events()
            if not events:
                console.print(
                    "[yellow]No feedback recorded yet.[/yellow] "
                    "Run a query, then `contextfs feedback --pick 1`."
                )
                return
            table = Table(show_header=True, header_style="bold cyan")
            table.add_column("When")
            table.add_column("Query", overflow="fold")
            table.add_column("File", overflow="fold")
            table.add_column("Event")
            for row in events:
                colour = "green" if row["event"] == "pick" else "red"
                table.add_row(
                    row["created_at"][:19],
                    row["query_norm"],
                    row["path"],
                    f"[{colour}]{row['event']}[/{colour}]",
                )
            console.print(table)
            return

        cached = _load_last_query(cfg)
        if cached is None:
            err_console.print("[bold red]No previous query to give feedback on.[/bold red]")
            raise typer.Exit(EXIT_CONFIG_ERROR)

        by_rank = {result["rank"]: result for result in cached["results"]}
        for rank, event in ((pick, "pick"), (reject, "reject")):
            if not rank:
                continue
            result = by_rank.get(rank)
            if result is None:
                err_console.print(f"[bold red]No result at rank {rank}.[/bold red]")
                raise typer.Exit(EXIT_CONFIG_ERROR)
            store.record_feedback(cached["query"], result["file_id"], result["path"], event=event)
            verb = "Recorded" if event == "pick" else "Recorded rejection of"
            console.print(
                f"{verb} [bold]{result['path']}[/bold] for "
                f'"[italic]{cached["query"]}[/italic]".'
            )
        console.print(
            "[dim]This will nudge that file up (or down) the next time you run the "
            "same query. It cannot outrank a clearly better match.[/dim]"
        )


@app.command()
def duplicates(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """List groups of near-duplicate files, and how much space they waste."""
    import json

    from contextfs.graph import load_graph
    from contextfs.insights import near_duplicates
    from contextfs.store import Store

    cfg = state.config()
    if not cfg.db_path.is_file():
        err_console.print(f"[bold red]No index at[/bold red] {cfg.db_path}. Run `contextfs scan`.")
        raise typer.Exit(EXIT_CONFIG_ERROR)

    with Store(cfg.db_path, read_only=True) as store:
        groups = near_duplicates(store, load_graph(cfg.graph_file))

    if as_json:
        console.print_json(json.dumps([g.as_dict() for g in groups]))
        return
    if not groups:
        console.print("[green]No near-duplicates found.[/green]")
        return

    for index, group in enumerate(groups, start=1):
        console.print(
            f"[bold]Group {index}[/bold] - {len(group.members)} files, "
            f"similarity {group.similarity:.2f}, "
            f"{group.wasted_bytes / 1024:.0f} KB redundant"
        )
        for member in group.members:
            marker = "[green]keep[/green]" if member is group.keeper else "[dim]dup [/dim]"
            console.print(f"  {marker}  {member['path']}")
        console.print()
    console.print("[dim]ContextFS never deletes anything. This is a report, not an action.[/dim]")


@app.command()
def projects(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Show bodies of work and where each sits in its lifecycle."""
    import json

    from contextfs.insights import projects as detect_projects
    from contextfs.store import Store

    cfg = state.config()
    if not cfg.db_path.is_file():
        err_console.print(f"[bold red]No index at[/bold red] {cfg.db_path}. Run `contextfs scan`.")
        raise typer.Exit(EXIT_CONFIG_ERROR)

    with Store(cfg.db_path, read_only=True) as store:
        found = detect_projects(store)

    if as_json:
        console.print_json(json.dumps([p.as_dict() for p in found]))
        return
    if not found:
        console.print("[yellow]No multi-file folders in the index.[/yellow]")
        return

    colours = {
        "upcoming": "bold magenta",
        "active": "bold green",
        "dormant": "yellow",
        "finished": "dim",
    }
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Stage")
    table.add_column("Folder", overflow="fold")
    table.add_column("Files", justify="right")
    table.add_column("Last touched")
    table.add_column("Why", overflow="fold", style="dim")
    for project in found:
        style = colours.get(project.stage, "")
        table.add_row(
            f"[{style}]{project.stage}[/{style}]" if style else project.stage,
            project.folder,
            str(project.files),
            project.last_activity[:10],
            project.reason,
        )
    console.print(table)


@app.command()
def digest(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Summarise what is in the indexed tree: kinds, ages, sizes, redundancy."""
    import json

    from contextfs.graph import load_graph
    from contextfs.insights import digest as build_digest
    from contextfs.store import Store

    cfg = state.config()
    if not cfg.db_path.is_file():
        err_console.print(f"[bold red]No index at[/bold red] {cfg.db_path}. Run `contextfs scan`.")
        raise typer.Exit(EXIT_CONFIG_ERROR)

    graph = load_graph(cfg.graph_file) if cfg.graph_file.is_file() else None
    with Store(cfg.db_path, read_only=True) as store:
        report = build_digest(store, graph)

    if as_json:
        console.print_json(json.dumps(report.as_dict()))
        return

    console.print(
        f"[bold]{report.files}[/bold] files, "
        f"[bold]{report.bytes / 1024 / 1024:.1f} MB[/bold] indexed\n"
    )

    table = Table(title="By file type", show_header=True, header_style="bold cyan")
    table.add_column("Type")
    table.add_column("Files", justify="right")
    table.add_column("Size", justify="right")
    for ext, count, size in report.by_extension[:10]:
        table.add_row(ext, str(count), f"{size / 1024:.0f} KB")
    console.print(table)

    ages = Table(title="By age", show_header=True, header_style="bold cyan")
    ages.add_column("Age")
    ages.add_column("Files", justify="right")
    for label, count in report.by_age.items():
        ages.add_row(label, str(count))
    console.print(ages)

    if report.duplicate_groups:
        console.print(
            f"\n[yellow]{report.duplicate_groups}[/yellow] near-duplicate group(s), "
            f"about [yellow]{report.duplicate_waste / 1024:.0f} KB[/yellow] redundant "
            "([dim]contextfs duplicates[/dim])"
        )
    if report.unextracted or report.unembedded:
        console.print(
            f"\n[dim]{report.unextracted} file(s) awaiting extraction, "
            f"{report.unembedded} awaiting embedding.[/dim]"
        )


@app.command()
def tags(
    path: Annotated[str, typer.Argument(help="Indexed file path (or a unique substring).")],
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Suggest tags for a file from the context ContextFS already knows."""
    import json

    from contextfs.insights import suggest_tags
    from contextfs.store import Store

    cfg = state.config()
    if not cfg.db_path.is_file():
        err_console.print(f"[bold red]No index at[/bold red] {cfg.db_path}. Run `contextfs scan`.")
        raise typer.Exit(EXIT_CONFIG_ERROR)

    with Store(cfg.db_path, read_only=True) as store:
        resolved = path
        if store.get_file(path) is None:
            matches = [
                row["path"] for row in store.all_files() if path.lower() in row["path"].lower()
            ]
            if not matches:
                err_console.print(f"[bold red]No indexed file matching[/bold red] {path}")
                raise typer.Exit(EXIT_CONFIG_ERROR)
            if len(matches) > 1:
                err_console.print(f"[yellow]Ambiguous;[/yellow] {len(matches)} files match:")
                for match in matches[:10]:
                    err_console.print(f"  {match}")
                raise typer.Exit(EXIT_CONFIG_ERROR)
            resolved = matches[0]
        suggestions = suggest_tags(store, resolved)

    if as_json:
        console.print_json(json.dumps([s.as_dict() for s in suggestions]))
        return
    console.print(f"[bold]{resolved}[/bold]\n")
    if not suggestions:
        console.print("[yellow]No tags could be derived - is the file indexed?[/yellow]")
        return
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Tag", overflow="fold")
    table.add_column("Drawn from")
    table.add_column("Rank score", justify="right")
    for suggestion in suggestions:
        table.add_row(suggestion.tag, suggestion.source, f"{suggestion.confidence:.2f}")
    console.print(table)
    console.print(
        "[dim]Rank score orders the list; it is a fixed per-source prior, not a "
        "calibrated probability.[/dim]"
    )


@app.command()
def timeline(
    span: Annotated[
        str, typer.Argument(help='A date range, e.g. "March to April" or "last week".')
    ],
    top_k: Annotated[int, typer.Option("--top-k", "-k", help="Maximum files to show.")] = 10,
    show_incidental: Annotated[
        bool,
        typer.Option(
            "--show-incidental",
            help="Also list dates the classifier judged incidental, for comparison.",
        ),
    ] = False,
    bench: Annotated[
        bool, typer.Option("--bench", help="Report query latency over repeated runs.")
    ] = False,
) -> None:
    """List files whose meaningful dates fall inside a time range."""
    from contextfs.store import Store
    from contextfs.temporal import RangeResolutionError, TimelineIndex, resolve_best

    cfg = state.config()
    if not cfg.db_path.is_file():
        err_console.print(f"[bold red]No index at[/bold red] {cfg.db_path}. Run `contextfs scan`.")
        raise typer.Exit(EXIT_CONFIG_ERROR)

    with Store(cfg.db_path, read_only=True) as store:
        index = TimelineIndex.from_store(store)
        try:
            # Disambiguate a year-less month against the index rather than
            # against today, so "September" finds the September the user means.
            date_range = resolve_best(span, index)
        except RangeResolutionError as exc:
            err_console.print(f"[bold red]{exc}[/bold red]")
            raise typer.Exit(1) from exc
        grouped = index.files_in_range(date_range)
        paths = store.path_by_file_id()
        incidental = (
            [
                row
                for row in store.classified_dates()
                if not row["is_meaningful"]
                and date_range.start.isoformat() <= row["iso_date"] <= date_range.end.isoformat()
            ]
            if show_incidental
            else []
        )
        measurement = index.benchmark(date_range) if bench else None

    console.print(
        f"[bold]{date_range.expression}[/bold] -> [cyan]{date_range}[/cyan]  "
        f"({date_range.days} day(s))"
    )
    if not grouped:
        console.print("[yellow]No files have a meaningful date in that range.[/yellow]")
    else:
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Date")
        table.add_column("File", overflow="fold")
        table.add_column("Matched", overflow="fold")
        table.add_column("Why", overflow="fold", style="dim")
        ordered = sorted(grouped.items(), key=lambda kv: kv[1][0].day)[:top_k]
        for file_id, nodes in ordered:
            first = nodes[0]
            table.add_row(
                first.day.isoformat(),
                paths.get(file_id, first.rel_path),
                ", ".join(sorted({node.surface for node in nodes})),
                first.reason,
            )
        console.print(table)
        console.print(
            f"{len(grouped)} file(s), {sum(len(v) for v in grouped.values())} "
            f"meaningful date(s) in range"
        )

    if show_incidental:
        console.print(
            f"\n[dim]{len(incidental)} date(s) in this range were classified "
            f"INCIDENTAL and excluded:[/dim]"
        )
        for row in incidental[:15]:
            console.print(f"  [dim]{row['iso_date']}  {row['path']}  ({row['reason']})[/dim]")

    if measurement:
        console.print(
            f"\n[dim]query latency: median {measurement['median_ms']:.4f} ms "
            f"(min {measurement['min_ms']:.4f}, max {measurement['max_ms']:.4f}) "
            f"over {measurement['repeats']} runs, {measurement['nodes']} timeline nodes[/dim]"
        )


@app.command()
def explain(
    result_id: Annotated[
        str,
        typer.Argument(
            help="A rank number from the last query (e.g. 1), or a file path substring."
        ),
    ],
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the raw machine-readable explanation.")
    ] = False,
) -> None:
    """Show, in full, why a particular result was retrieved."""
    import json

    cfg = state.config()
    cached = cfg.paths.data_dir / "last_query.json"
    if not cached.is_file():
        err_console.print(
            "[bold red]No previous query to explain.[/bold red] Run `contextfs query` first."
        )
        raise typer.Exit(1)

    payload = json.loads(cached.read_text(encoding="utf-8"))
    results = payload.get("results", [])

    match = None
    if result_id.isdigit():
        match = next((r for r in results if r["rank"] == int(result_id)), None)
    if match is None:
        needle = result_id.lower()
        match = next((r for r in results if needle in r["path"].lower()), None)
    if match is None:
        err_console.print(
            f"[bold red]No result matching[/bold red] {result_id!r} in the last query.\n"
            f"Available: " + ", ".join(f"{r['rank']}={Path(r['path']).name}" for r in results[:5])
        )
        raise typer.Exit(1)

    if as_json:
        console.print_json(json.dumps(match["explanation"]))
        return

    explanation = match["explanation"]
    console.print(
        Panel(
            f"[bold]{match['path']}[/bold]\n" f"rank {match['rank']}  ·  score {match['score']}",
            title=f"Why this matched: {payload['query']!r}",
            border_style="cyan",
        )
    )

    console.print("\n[bold cyan]Reasons[/bold cyan]")
    for reason in explanation["reasons"]:
        console.print(f"  • {reason}")

    table = Table(show_header=True, header_style="bold cyan", title="\nScore breakdown")
    table.add_column("Signal")
    table.add_column("Value", justify="right")
    table.add_column("Weight", justify="right")
    table.add_column("Contribution", justify="right")
    for signal, value in explanation["signals"].items():
        weight = explanation["weights"].get(signal, 0.0)
        table.add_row(
            signal,
            f"{value:.3f}",
            f"{weight:.3f}",
            f"{explanation['contributions'].get(signal, 0.0):.3f}",
        )
    table.add_section()
    table.add_row(
        "[bold]total[/bold]",
        "",
        "",
        f"[bold]{sum(explanation['contributions'].values()):.3f}[/bold]",
    )
    console.print(table)

    if explanation["matched_entities"]:
        console.print(
            "\n[bold cyan]Shared entities[/bold cyan]  "
            + ", ".join(explanation["matched_entities"])
        )
    if explanation["matched_session"]:
        session = explanation["matched_session"]
        console.print(
            f"\n[bold cyan]Activity session[/bold cyan]  {session['label']}\n"
            f"  {session['size']} files, {session['start']} to {session['end']}"
        )
    if explanation["matched_timeline"]:
        console.print("\n[bold cyan]Timeline matches[/bold cyan]")
        for item in explanation["matched_timeline"]:
            console.print(
                f"  {item['date']}  ({item['surface']})  "
                f"relevance {item['score']}  [dim]{item.get('reason', '')}[/dim]"
            )
    if explanation["graph_path"]:
        console.print("\n[bold cyan]Graph path[/bold cyan]")
        for hop in explanation["graph_path"]:
            console.print(f"  {hop['from']} --[{hop['type']}]--> {hop['to_label']}")

    console.print(
        f"\n[dim]seeded by: {explanation['seed_origin']}  ·  "
        f"explanation complete: {explanation['complete']}[/dim]"
    )


@app.command()
def stats() -> None:
    """Report corpus size, index size, graph size, and index freshness."""
    from contextfs.graph import graph_stats, load_graph
    from contextfs.store import Store
    from contextfs.temporal import TimelineIndex

    cfg = state.config()
    if not cfg.db_path.is_file():
        err_console.print(f"[bold red]No index at[/bold red] {cfg.db_path}. Run `contextfs scan`.")
        raise typer.Exit(EXIT_CONFIG_ERROR)

    with Store(cfg.db_path, read_only=True) as store:
        last = store.last_scan()
        dates = store.date_counts()
        sessions = store.sessions()
        timeline = TimelineIndex.from_store(store)
        extensions = store.counts_by_extension()
        graph = load_graph(cfg.graph_file)
        vectors = _open_vector_store(cfg).counts()

        corpus = Table(title="Corpus", show_header=True, header_style="bold cyan")
        corpus.add_column("Metric")
        corpus.add_column("Value", justify="right")
        corpus.add_row("scan root", str(cfg.paths.root))
        corpus.add_row("files indexed", str(store.file_count()))
        corpus.add_row("files tombstoned", str(store.file_count(True) - store.file_count()))
        corpus.add_row("documents extracted", str(store.document_count()))
        corpus.add_row("by extension", ", ".join(f"{k} {v}" for k, v in extensions.items()))
        console.print(corpus)

        index = Table(title="\nIndex", show_header=True, header_style="bold cyan")
        index.add_column("Layer")
        index.add_column("Size", justify="right")
        index.add_row("entities (L3)", str(store.entity_count()))
        index.add_row("date mentions (L3)", str(store.date_mention_count()))
        index.add_row(
            "embeddings (L4)", f"{vectors['documents']} docs / {vectors['chunks']} chunks"
        )
        index.add_row("tree nodes (L5)", str(store.tree_node_count()))
        index.add_row(
            "graph (L6)",
            f"{graph.number_of_nodes()} nodes / {graph.number_of_edges()} edges",
        )
        index.add_row(
            "dates classified (L7)",
            f"{dates['meaningful']} meaningful / {dates['incidental']} incidental",
        )
        index.add_row("timeline nodes (L7)", str(len(timeline.nodes)))
        index.add_row("sessions (L8)", str(len(sessions)))
        console.print(index)

        if graph.number_of_nodes():
            stats_report = graph_stats(graph)
            console.print(
                f"[dim]graph edges by type: {stats_report['by_type']}  ·  "
                f"mean degree {stats_report['mean_degree']}  ·  "
                f"components {stats_report['connected_components']}[/dim]"
            )

        span = timeline.span()
        if span:
            console.print(f"[dim]timeline spans {span[0]} to {span[1]}[/dim]")

        freshness = Table(title="\nFreshness", show_header=True, header_style="bold cyan")
        freshness.add_column("Metric")
        freshness.add_column("Value", justify="right")
        if last:
            freshness.add_row("last scan", last["finished_at"] or "(incomplete)")
            freshness.add_row("scan duration", f"{last['duration_ms']:.0f} ms")
            freshness.add_row(
                "last scan changed",
                f"{last['count_new']} new / {last['count_modified']} modified "
                f"/ {last['count_deleted']} deleted",
            )
        else:
            freshness.add_row("last scan", "never")

        pending = len(store.files_needing_extraction()) + len(store.files_needing_embedding())
        freshness.add_row(
            "index state",
            "[green]current[/green]" if pending == 0 else f"[yellow]{pending} stale[/yellow]",
        )
        console.print(freshness)

    size = sum(p.stat().st_size for p in cfg.paths.data_dir.rglob("*") if p.is_file())
    console.print(f"\n[dim]derived data: {size / 1024 / 1024:.2f} MB at {cfg.paths.data_dir}[/dim]")
    console.print("[dim]Your files are never modified: ContextFS opens them read-only.[/dim]")


@app.command()
def reset(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Delete ContextFS's derived data. Never touches your files."""
    import shutil

    cfg = state.config()
    target = cfg.paths.data_dir
    if not target.exists():
        console.print(f"[dim]Nothing to reset: {target} does not exist.[/dim]")
        return

    size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    console.print(
        Panel(
            f"This deletes [bold]{target}[/bold] ({size / 1024 / 1024:.2f} MB).\n\n"
            f"Your scanned files in [cyan]{cfg.paths.root}[/cyan] are [bold]not[/bold] "
            "touched - ContextFS never had write access to them.\n"
            "You can rebuild the index with `contextfs scan`.",
            title="Reset the ContextFS index",
            border_style="yellow",
        )
    )
    if not yes and not typer.confirm("Delete the derived data?"):
        console.print("[dim]Cancelled.[/dim]")
        raise typer.Exit(1)

    shutil.rmtree(target)
    console.print(f"[green]Deleted[/green] {target}")


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


@app.command()
def gui() -> None:
    """Open the ContextFS desktop application."""
    cfg = state.config()
    try:
        from contextfs.gui import launch
    except ImportError as exc:
        err_console.print(
            "[bold red]The desktop application needs the `gui` extra.[/bold red]\n"
            'Install it with: pip install -e ".[gui]"'
        )
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc
    raise typer.Exit(launch(cfg))


@app.command()
def visualise(
    open_it: Annotated[
        bool, typer.Option("--open/--no-open", help="Open the page when it is built.")
    ] = True,
    out: Annotated[Path | None, typer.Option("--out", help="Where to write the HTML file.")] = None,
) -> None:
    """Build the 3D relationship-graph visualisation as a standalone page.

    The output is one self-contained HTML file with three.js inlined - no CDN,
    no server, no network access. It can be opened on any machine, or handed to
    someone else, without ContextFS installed.
    """
    import webbrowser

    from contextfs.gui import build_visualisation

    cfg = state.config()
    if not cfg.graph_file.is_file():
        err_console.print(
            f"[bold red]No relationship graph at[/bold red] {cfg.graph_file}. "
            "Run `contextfs scan` first."
        )
        raise typer.Exit(EXIT_CONFIG_ERROR)

    target = build_visualisation(cfg, out)
    size_mb = target.stat().st_size / 1024 / 1024
    console.print(f"[green]Wrote[/green] {target} [dim]({size_mb:.1f} MB, self-contained)[/dim]")
    if open_it:
        webbrowser.open(target.as_uri())


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
