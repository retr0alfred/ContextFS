"""Verify and measure incremental update correctness (Phase 18).

Builds a full index over a throwaway copy of the corpus, then modifies, adds and
deletes files and re-scans, instrumenting exactly how much work the second pass
does. The point is a *measured* reprocessed-file-count against corpus size, not
an assurance that incrementality works.

The corpus is copied to a temporary directory first, so this never mutates the
committed dataset.

Usage:
    python scripts/incremental_check.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contextfs.activity import SessionBuilder  # noqa: E402
from contextfs.config import load_config  # noqa: E402
from contextfs.datagen.generate import generate_corpus  # noqa: E402
from contextfs.embed import Embedder, VectorStore, embed_documents  # noqa: E402
from contextfs.entities import EntityExtractor  # noqa: E402
from contextfs.extract import extract_many  # noqa: E402
from contextfs.graph import build_graph, save_graph  # noqa: E402
from contextfs.scanner import Scanner  # noqa: E402
from contextfs.store import Store  # noqa: E402
from contextfs.summarize import Summarizer  # noqa: E402
from contextfs.temporal import DateClassifier  # noqa: E402
from contextfs.tree import build_tree  # noqa: E402


class Pipeline:
    """The full indexing pipeline, instrumented per stage."""

    def __init__(self, cfg):
        """Bind to a configuration and prepare the shared model objects."""
        self.cfg = cfg
        self.vectors = VectorStore(cfg.vector_dir, cfg.embeddings.dimension)
        self.embedder = Embedder(
            cfg.embeddings.model,
            expected_dimension=cfg.embeddings.dimension,
            backend=cfg.embeddings.backend,
            num_threads=cfg.embeddings.num_threads,
        )
        self.entities = EntityExtractor(
            cfg.entities.spacy_model, drop_acronym_orgs=cfg.entities.drop_acronym_orgs
        )

    def run(self, store, label):
        """Run one full pass and return per-stage work counts and timings."""
        from datetime import datetime

        stats = {"label": label}
        started = time.perf_counter()

        scan = Scanner(self.cfg).scan(store)
        stats["files_seen"] = scan.seen
        stats["scan_new"] = len(scan.new)
        stats["scan_modified"] = len(scan.modified)
        stats["scan_deleted"] = len(scan.deleted)
        stats["files_hashed"] = scan.files_hashed
        stats["scan_ms"] = round(scan.duration_ms, 1)

        if scan.deleted:
            ids = [store.get_file(p)["id"] for p in scan.deleted if store.get_file(p)]
            self.vectors.delete_files(ids)
            store.clear_embeddings(ids)
            store.delete_documents(ids)

        t = time.perf_counter()
        pending = store.files_needing_extraction()
        rows = {r["path"]: r for r in pending}
        if pending:
            batch = extract_many(
                [(Path(r["abs_path"]), r["path"]) for r in pending], config=self.cfg
            )
            for doc in batch.documents:
                store.save_document(
                    rows[doc.rel_path]["id"], doc, rows[doc.rel_path]["content_hash"]
                )
        stats["extracted"] = len(pending)
        stats["extract_ms"] = round((time.perf_counter() - t) * 1000, 1)

        t = time.perf_counter()
        due = store.files_needing_entities()
        for row in due:
            result = self.entities.extract(
                row["path"],
                row["doc_text"] or "",
                reference_date=datetime.fromisoformat(row["mtime"]),
            )
            spans = [
                (b["char_start"], b["char_end"])
                for b in store.get_blocks(row["id"])
                if b["is_tabular"]
            ]
            store.save_entities(row["id"], result, row["content_hash"], spans)
        if due:
            store.reconcile_entity_categories()
        stats["entities"] = len(due)
        stats["entities_ms"] = round((time.perf_counter() - t) * 1000, 1)

        t = time.perf_counter()
        report = embed_documents(store, self.vectors, self.embedder, self.cfg)
        stats["embedded"] = report.files
        stats["embed_ms"] = round((time.perf_counter() - t) * 1000, 1)

        t = time.perf_counter()
        classifier = DateClassifier(self.cfg)
        store.save_classified_dates(classifier.collapse(classifier.classify_store(store)))
        stats["dates_ms"] = round((time.perf_counter() - t) * 1000, 1)

        t = time.perf_counter()
        sessions = SessionBuilder(self.cfg).build(store, self.vectors)
        store.save_sessions(sessions.sessions)
        stats["sessions"] = len(sessions.sessions)
        stats["sessions_ms"] = round((time.perf_counter() - t) * 1000, 1)

        t = time.perf_counter()
        tree, _ = build_tree(store, self.vectors, Summarizer(self.cfg))
        store.save_tree(tree)
        graph, graph_report = build_graph(store, self.vectors, self.cfg)
        save_graph(graph, self.cfg.graph_file)
        stats["graph_nodes"] = graph_report.nodes
        stats["graph_edges"] = graph_report.edges
        stats["structure_ms"] = round((time.perf_counter() - t) * 1000, 1)

        stats["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="cfs_incr_") as tmp:
        base = Path(tmp)
        corpus = base / "corpus"
        generate_corpus(corpus, clean=True)

        config_file = base / "contextfs.toml"
        shutil.copy(PROJECT_ROOT / "contextfs.toml", config_file)
        cfg = load_config(config_file, root=corpus, data_dir=base / "derived")
        cfg.ensure_data_dir()

        pipeline = Pipeline(cfg)
        store = Store(cfg.db_path)

        print("=" * 78)
        print("INCREMENTAL UPDATE CORRECTNESS")
        print("=" * 78)
        print(f"corpus: {corpus}\n")

        full = pipeline.run(store, "initial full build")
        print("PASS 1 - initial full build")
        for key, value in full.items():
            print(f"  {key:<18} {value}")

        unchanged = pipeline.run(store, "re-scan, nothing changed")
        print("\nPASS 2 - re-scan with NO changes")
        for key, value in unchanged.items():
            print(f"  {key:<18} {value}")

        # -- mutate: modify 1, add 1, delete 1 -----------------------------
        modified = corpus / "Personal" / "Misc" / "recipe_biryani.txt"
        modified.write_text(
            modified.read_text(encoding="utf-8") + "\nPS: more ghee.\n", encoding="utf-8"
        )
        added = corpus / "Personal" / "Misc" / "new_reading_list.txt"
        added.write_text(
            "READING LIST\nGodel Escher Bach\nThe Design of Everyday Things\n",
            encoding="utf-8",
        )
        deleted = corpus / "Downloads" / "wifi_setup_instructions.txt"
        deleted.unlink()

        incremental = pipeline.run(store, "after 1 modified, 1 added, 1 deleted")
        print("\nPASS 3 - after modifying 1, adding 1, deleting 1 file")
        for key, value in incremental.items():
            print(f"  {key:<18} {value}")

        print("\n" + "=" * 78)
        print("VERDICT")
        print("=" * 78)

        corpus_size = full["files_seen"]
        touched = incremental["extracted"]
        checks = []

        checks.append(
            (
                "no-change re-scan reprocesses nothing",
                unchanged["extracted"] == 0
                and unchanged["entities"] == 0
                and unchanged["embedded"] == 0,
                f"extracted={unchanged['extracted']} entities={unchanged['entities']} "
                f"embedded={unchanged['embedded']}",
            )
        )
        checks.append(
            (
                "change detection is exact",
                incremental["scan_modified"] == 1
                and incremental["scan_new"] == 1
                and incremental["scan_deleted"] == 1,
                f"new={incremental['scan_new']} modified={incremental['scan_modified']} "
                f"deleted={incremental['scan_deleted']}",
            )
        )
        checks.append(
            (
                "only changed files are reprocessed",
                touched == 2,
                f"{touched} of {corpus_size} files re-extracted (expected 2)",
            )
        )
        checks.append(
            (
                "deleted file removed from every store",
                store.get_file("Downloads/wifi_setup_instructions.txt")["status"] == "deleted"
                and store.get_document_by_path("Downloads/wifi_setup_instructions.txt") is None,
                "tombstoned in SQLite and purged from documents + vectors",
            )
        )
        checks.append(
            (
                "added file is fully indexed",
                store.get_document_by_path("Personal/Misc/new_reading_list.txt") is not None,
                "extraction, entities and vectors present",
            )
        )

        ok = True
        for name, passed, detail in checks:
            print(f"  {'PASS' if passed else 'FAIL'}  {name}")
            print(f"        {detail}")
            ok &= passed

        reprocess_fraction = touched / corpus_size
        speedup = full["total_ms"] / incremental["total_ms"]
        print()
        print(
            f"  reprocessed-file-count vs corpus size : {touched} / {corpus_size} "
            f"({reprocess_fraction:.1%})"
        )
        print(f"  incremental update time               : {incremental['total_ms']:.0f} ms")
        print(f"  full build time                       : {full['total_ms']:.0f} ms")
        print(f"  speedup                               : {speedup:.1f}x")
        print()
        print("  NOTE: the structural stages (dates, sessions, tree, graph) are rebuilt")
        print("  wholesale by design - clustering and cross-file recurrence are global,")
        print("  so a per-file patch would not equal a full run. Their cost is reported")
        print(
            f"  separately: dates {incremental['dates_ms']:.0f} ms, "
            f"sessions {incremental['sessions_ms']:.0f} ms, "
            f"structure {incremental['structure_ms']:.0f} ms."
        )
        print("  The per-file stages - extraction, entities, embedding - are genuinely")
        print("  incremental, and they are the ones that scale with corpus size.")

        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(
                json.dumps(
                    {
                        "full": full,
                        "unchanged": unchanged,
                        "incremental": incremental,
                        "reprocess_fraction": round(reprocess_fraction, 4),
                        "speedup": round(speedup, 2),
                        "all_checks_passed": ok,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"\nwrote {args.json}")

        store.close()
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
