"""Benchmark harness: index personas, run every system on the same index, score with graded metrics.

**Interim (v2-P3):** this runs over the v1 index and the v1 retriever. v2-P4's engine (ADR-0010)
replaces the internals behind the same system interface (`query -> ranked rel_paths`).

Systems (all over the same index; ranked top-100 relative paths):
- `path_mtime`:   BM25 over filename/path tokens, recency tie-break (Windows Search / Everything-like)
- `bm25`:         document-level BM25 over extracted text + filename tokens (k1, b tunable)
- `dense_maxp`:   max chunk cosine per file (MiniLM, CPU)
- `dense_flat`:   v1 document-vector kNN (regression anchor of Decision 90)
- `rrf60`, `rrf10`, `zfuse`: BM25 + dense_maxp fusions (the method is chosen on dev; ADR-0008 §2)
- `v1_full`:      v1 ContextFS HybridRetriever, all signals

Usage:
    python scripts/bench_eval.py data/bench/student-s1-A-x1-none [more persona dirs] --tag dev-smoke
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import numpy as np  # noqa: E402

from contextfs.evalkit import (  # noqa: E402
    BM25,
    bootstrap_ci,
    hit_at_1,
    holm,
    ndcg_at_k,
    paired_randomisation_p,
    recall_at_k,
    reciprocal_rank,
    rrf,
    tokenize,
    zscore_fuse,
)

TOP = 100


def ensure_index(persona_dir: Path, reindex: bool, index_root: Path | None = None) -> Path:
    # --index-root keeps derived indexes off the project's USB disk (log.md Decision 104)
    index = (index_root / persona_dir.name) if index_root else persona_dir / "index"
    if reindex or not (index / "contextfs.db").exists():
        cmd = [
            sys.executable,
            "-m",
            "contextfs",
            "--root",
            str(persona_dir / "corpus"),
            "--data-dir",
            str(index),
            "scan",
        ]
        proc = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"indexing {persona_dir.name} failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
            )
    return index


class PersonaSystems:
    """All systems over one persona's index."""

    def __init__(
        self,
        persona_dir: Path,
        index: Path,
        embedder,
        bm25_k1: float,
        bm25_b: float,
        cross_encoder=None,
    ) -> None:
        """Open the persona's index and build every system over it."""
        from contextfs.config import load_config
        from contextfs.embed import VectorStore
        from contextfs.graph import load_graph
        from contextfs.retrieval import HybridRetriever, SemanticBaseline
        from contextfs.store import Store
        from contextfs.temporal import TimelineIndex

        self.cfg = load_config(
            PROJECT_ROOT / "contextfs.toml", root=persona_dir / "corpus", data_dir=index
        )
        self.store = Store(self.cfg.db_path, read_only=True)
        self.vectors = VectorStore(self.cfg.vector_dir, self.cfg.embeddings.dimension)
        self.embedder = embedder
        files = self.store.all_files()
        self.paths = [row["path"] for row in files]
        self.mtime = {row["path"]: row["mtime"] for row in files}
        id_to_path = self.store.path_by_file_id()
        texts = {id_to_path[d["file_id"]]: d["text"] or "" for d in self.store.all_documents()}
        self.texts = texts
        self.cross_encoder = cross_encoder

        def name_tokens(p: str) -> list[str]:
            return tokenize(p.replace("_", " ").replace("-", " ").replace("/", " "))

        self.bm25 = BM25(
            {p: tokenize(texts.get(p, "")) + name_tokens(p) for p in self.paths}, bm25_k1, bm25_b
        )
        self.path_index = BM25({p: name_tokens(p) for p in self.paths})

        table = self.vectors._table(self.vectors.CHUNKS).to_arrow()
        self.chunk_paths = [id_to_path.get(int(f), "") for f in table.column("file_id").to_pylist()]
        self.chunk_matrix = np.asarray(table.column("vector").to_pylist(), dtype=np.float32)

        graph = load_graph(self.cfg.graph_file)
        timeline = TimelineIndex.from_store(self.store)
        retrieval = (
            self.cfg.retrieval.model_copy(update={"top_k": TOP})
            if hasattr(self.cfg.retrieval, "model_copy")
            else self.cfg.retrieval
        )
        cfg_full = (
            self.cfg.model_copy(update={"retrieval": retrieval})
            if hasattr(self.cfg, "model_copy")
            else self.cfg
        )
        self.v1_full = HybridRetriever(
            self.store, self.vectors, embedder, graph, cfg_full, timeline_index=timeline
        )
        self.v1_flat = SemanticBaseline(self.store, self.vectors, embedder)
        # Per-layer ablations of v1, plus the format hint as its own axis (Decision 90).
        no_format = cfg_full.model_copy(
            update={
                "retrieval": cfg_full.retrieval.model_copy(
                    update={"format_boost": 1.0, "format_penalty": 1.0}
                )
            }
        )

        def hybrid(signals, cfg=cfg_full):
            return HybridRetriever(
                self.store,
                self.vectors,
                embedder,
                graph,
                cfg,
                signals=signals,
                timeline_index=timeline,
            )

        self._hybrid = hybrid
        self.ablations = {
            "v1_sem_fmt": hybrid(("semantic",)),
            "v1_sem_graph": hybrid(("semantic", "graph")),
            "v1_sem_graph_time": hybrid(("semantic", "graph", "timeline")),
            "v1_sem_graph_act": hybrid(("semantic", "graph", "activity")),
            "v1_full_nofmt": hybrid(("semantic", "graph", "activity", "timeline"), no_format),
        }
        self._cf_cache: dict[tuple[str, ...], object] = {}

    @staticmethod
    def _rank(scores: dict[str, float]) -> list[str]:
        return [d for d, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))][:TOP]

    def run_all(self, query: str) -> dict[str, list[str]]:
        """Rank the persona's files for ``query`` with every system."""
        bm = self.bm25.scores(query)
        qv = self.embedder.encode_one(query)
        sims = self.chunk_matrix @ qv if len(self.chunk_matrix) else np.zeros(0)
        dense: dict[str, float] = {}
        for p, s in zip(self.chunk_paths, sims.tolist(), strict=True):
            if p and s > dense.get(p, -2.0):
                dense[p] = s
        path_scores = self.path_index.scores(query)
        recency = sorted(self.paths, key=lambda p: self.mtime[p], reverse=True)
        path_rank = self._rank(path_scores)
        path_rank += [p for p in recency if p not in path_scores][: TOP - len(path_rank)]
        bm_rank, de_rank = self._rank(bm), self._rank(dense)
        return {
            "path_mtime": path_rank,
            "bm25": bm_rank,
            "dense_maxp": de_rank,
            "dense_flat": self.v1_flat.search(query, TOP).paths,
            "rrf60": rrf([bm_rank, de_rank], 60)[:TOP],
            "rrf10": rrf([bm_rank, de_rank], 10)[:TOP],
            "zfuse": zscore_fuse([bm, dense], [0.5, 0.5])[:TOP],
            "v1_full": self.v1_full.search(query, TOP).paths,
            **{name: r.search(query, TOP).paths for name, r in self.ablations.items()},
            **({"ce_rerank": self._ce_rerank(query, bm, dense)} if self.cross_encoder else {}),
        }

    def _ce_rerank(self, query: str, bm: dict, dense: dict, depth: int = 50) -> list[str]:
        """Cross-encoder rerank of the BM25+dense z-fusion top-``depth`` (eval-only baseline)."""
        pool = zscore_fuse([bm, dense], [0.5, 0.5])[:depth]
        if not pool:
            return []
        pairs = [(query, (p.replace("_", " ") + ". " + self.texts.get(p, ""))[:2000]) for p in pool]
        scores = self.cross_encoder.predict(pairs, batch_size=32, show_progress_bar=False)
        reranked = [
            p for p, _ in sorted(zip(pool, scores, strict=True), key=lambda x: (-x[1], x[0]))
        ]
        rest = [p for p in zscore_fuse([bm, dense], [0.5, 0.5])[:TOP] if p not in set(reranked)]
        return (reranked + rest)[:TOP]

    def faithfulness(self, query: str) -> dict:
        """Explanation checks on v1_full (ADR-0009 metrics, measured on the old system).

        - claim_verified@10: share of top-10 results whose cited topic terms all occur in the file.
        - cf_drop: for the top-1 result, its rank after removing its largest-contributing signal
          (101 = fell out of the top 100). A faithful "mostly because of X" should drop.
        """
        response = self.v1_full.search(query, TOP)
        results = response.results[:10]
        verified = []
        for r in results:
            terms = [t.lower() for t in r.explanation.matched_topic]
            text = (self.texts.get(r.path, "") + " " + r.path).lower()
            verified.append(all(t in text for t in terms) if terms else True)
        out = {"claim_verified10": sum(verified) / len(verified) if verified else None}
        if results:
            top = results[0]
            contrib = top.explanation.contributions or {}
            if contrib:
                lead = max(contrib, key=contrib.get)
                remaining = tuple(
                    s
                    for s in ("semantic", "graph", "activity", "timeline")
                    if s in contrib and s != lead
                ) or ("semantic",)
                if remaining not in self._cf_cache:
                    self._cf_cache[remaining] = self._hybrid(remaining)
                ranked = self._cf_cache[remaining].search(query, TOP).paths
                out["cf_lead_signal"] = lead
                out["cf_rank_after"] = ranked.index(top.path) + 1 if top.path in ranked else TOP + 1
        return out

    def close(self) -> None:
        """Release the index."""
        self.store.close()


def summarise(per_query: list[dict], systems: list[str], reference: str) -> dict:
    out = {"n_queries": len(per_query), "systems": {}, "by_cue": {}, "paired_vs_reference": {}}
    for s in systems:
        vals = {
            m: [q["metrics"][s][m] for q in per_query] for m in ("ndcg10", "mrr", "hit1", "r10")
        }
        out["systems"][s] = {
            m: {
                "mean": round(statistics.mean(v), 4),
                "ci95": [round(x, 4) for x in bootstrap_ci(v)],
            }
            for m, v in vals.items()
        }
    cues = sorted({q["cue"] for q in per_query})
    for cue in cues:
        sub = [q for q in per_query if q["cue"] == cue]
        out["by_cue"][cue] = {"n": len(sub)} | {
            s: round(statistics.mean(q["metrics"][s]["ndcg10"] for q in sub), 4) for s in systems
        }
    raw = {}
    for s in systems:
        if s == reference:
            continue
        diffs = [q["metrics"][reference]["ndcg10"] - q["metrics"][s]["ndcg10"] for q in per_query]
        raw[s] = paired_randomisation_p(diffs)
        wins = sum(d > 0 for d in diffs)
        losses = sum(d < 0 for d in diffs)
        out["paired_vs_reference"][s] = {
            "mean_diff_ndcg10": round(statistics.mean(diffs), 4),
            "p": round(raw[s], 5),
            "wins": wins,
            "losses": losses,
            "ties": len(diffs) - wins - losses,
        }
    for s, p in holm(raw).items():
        out["paired_vs_reference"][s]["p_holm"] = round(p, 5)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("persona_dirs", type=Path, nargs="+")
    parser.add_argument("--tag", required=True, help="run label, e.g. dev-smoke")
    parser.add_argument("--reindex", action="store_true")
    parser.add_argument("--k1", type=float, default=1.2)
    parser.add_argument("--b", type=float, default=0.75)
    parser.add_argument("--reference", default="v1_full")
    parser.add_argument("--ce", action="store_true", help="add the cross-encoder rerank baseline")
    parser.add_argument("--queries", choices=["template", "llm"], default="llm")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "data" / "bench_results")
    parser.add_argument("--index-root", type=Path, default=None,
                        help="build/read v1 indexes at <root>/<persona> instead of <persona>/index")
    args = parser.parse_args()

    for d in args.persona_dirs:
        persona_meta = json.loads((d / "truth" / "persona.json").read_text(encoding="utf-8"))
        if persona_meta["persona"]["family"] == "B" and not args.tag.startswith("TEST-"):
            print(f"refusing: {d.name} is a test-family persona; test runs must be tagged TEST-*")
            return 2

    from contextfs.config import load_config
    from contextfs.embed import Embedder

    cfg = load_config(PROJECT_ROOT / "contextfs.toml")
    embedder = Embedder(
        cfg.embeddings.model,
        device=cfg.embeddings.device,
        batch_size=cfg.embeddings.batch_size,
        expected_dimension=cfg.embeddings.dimension,
        backend=cfg.embeddings.backend,
        num_threads=cfg.embeddings.num_threads,
    )
    cross_encoder = None
    if args.ce:
        from sentence_transformers import CrossEncoder

        # Eval-only baseline: trained on MS MARCO (non-commercial terms), never shipped (ADR-0016).
        cross_encoder = CrossEncoder("cross-encoder/ms-marco-MiniLM-L6-v2", device="cpu")
    per_query: list[dict] = []
    systems: list[str] = []
    started = time.perf_counter()
    for d in args.persona_dirs:
        index = ensure_index(d, args.reindex, args.index_root)
        ps = PersonaSystems(d, index, embedder, args.k1, args.b, cross_encoder)
        qfile = "queries_llm.jsonl" if args.queries == "llm" else "queries.jsonl"
        for line in (d / "truth" / qfile).read_text(encoding="utf-8").splitlines():
            q = json.loads(line)
            ranked = ps.run_all(q["text"])
            systems = list(ranked)
            metrics = {
                s: {
                    "ndcg10": ndcg_at_k(r, q["qrels"], 10),
                    "mrr": reciprocal_rank(r, q["target"]),
                    "hit1": hit_at_1(r, q["target"]),
                    "r10": recall_at_k(r, q["target"], 10),
                }
                for s, r in ranked.items()
            }
            per_query.append(
                {
                    "query_id": q["query_id"],
                    "persona": q["persona_id"],
                    "cue": q["cue"],
                    "typo": q["typo"],
                    "text": q["text"],
                    "target": q["target"],
                    "metrics": metrics,
                    "faithfulness": ps.faithfulness(q["text"]),
                    "top3": {s: r[:3] for s, r in ranked.items()},
                }
            )
        ps.close()

    summary = summarise(per_query, systems, args.reference)
    summary |= {
        "tag": args.tag,
        "personas": [d.name for d in args.persona_dirs],
        "bm25": {"k1": args.k1, "b": args.b},
        "reference": args.reference,
        "ran_at": datetime.now().isoformat(timespec="seconds"),
        "seconds": round(time.perf_counter() - started, 1),
    }
    faith = [q["faithfulness"] for q in per_query]
    cv = [f["claim_verified10"] for f in faith if f.get("claim_verified10") is not None]
    cf = [f for f in faith if "cf_rank_after" in f]
    by_lead: dict[str, list[int]] = {}
    for f in cf:
        by_lead.setdefault(f["cf_lead_signal"], []).append(f["cf_rank_after"])
    summary["v1_explanations"] = {
        "claim_verified_at10": round(statistics.mean(cv), 4) if cv else None,
        "cf_top1_dropped_share": (
            round(sum(f["cf_rank_after"] > 1 for f in cf) / len(cf), 4) if cf else None
        ),
        "cf_median_rank_after": statistics.median(f["cf_rank_after"] for f in cf) if cf else None,
        "cf_by_lead_signal": {
            k: {"n": len(v), "dropped": round(sum(r > 1 for r in v) / len(v), 4)}
            for k, v in sorted(by_lead.items())
        },
    }
    run_dir = args.out / f"{args.tag}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (run_dir / "per_query.jsonl").write_text(
        "".join(json.dumps(q) + "\n" for q in per_query), encoding="utf-8"
    )

    print(f"{args.tag}: {len(per_query)} queries over {len(args.persona_dirs)} personas")
    print(f"{'system':<12} {'nDCG@10':>16} {'MRR':>7} {'Hit@1':>7} {'R@10':>7}")
    for s, m in summary["systems"].items():
        lo, hi = m["ndcg10"]["ci95"]
        print(
            f"{s:<12} {m['ndcg10']['mean']:.3f} [{lo:.3f},{hi:.3f}] {m['mrr']['mean']:>7.3f} "
            f"{m['hit1']['mean']:>7.3f} {m['r10']['mean']:>7.3f}"
        )
    cue_counts = ", ".join(f"{c}:{v['n']}" for c, v in summary["by_cue"].items())
    print(f"\nnDCG@10 by cue ({cue_counts})")
    print(f"{'cue':<10}" + "".join(f"{s[:10]:>11}" for s in systems))
    for cue, row in summary["by_cue"].items():
        print(f"{cue:<10}" + "".join(f"{row[s]:>11.3f}" for s in systems))
    ve = summary["v1_explanations"]
    print(
        f"\nv1 explanations: claim-verified@10 {ve['claim_verified_at10']}; top-1 changes when "
        f"its lead signal is removed: {ve['cf_top1_dropped_share']} "
        f"(by lead signal {ve['cf_by_lead_signal']})"
    )
    print(f"\npaired vs {args.reference} (nDCG@10; + means {args.reference} better)")
    for s, r in summary["paired_vs_reference"].items():
        print(
            f"  {s:<12} {r['mean_diff_ndcg10']:+.4f}  p={r['p']:.4f}  p_holm={r['p_holm']:.4f}  "
            f"W/L/T {r['wins']}/{r['losses']}/{r['ties']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
