"""Query-to-target lexical overlap: a leakage check for generated queries (ADR-0015 §6).

For each query, the share of its content words (≥3 letters, not stopwords) that occur in:
- the target's extracted text;
- the target's filename/path;
- a random other file of the same persona (the chance baseline).

A query writer that copies from the file shows target overlap far above the baseline. Memory-style
queries should sit much closer to it, especially for filename tokens. Run on the **dev** split
only; test queries are never displayed or analysed before the single test run.

Usage:
    python scripts/query_overlap_report.py data/bench/*-A-* [--queries llm|template]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contextfs.extract import extract_file  # noqa: E402

STOP = set(
    "the a an and or of for to in on at by with from about my me i was were is it this that what "
    "which some something thing file files doc document notes note when did had have from back "
    "around last early late mid".split()
)
WORD = re.compile(r"[a-z]{3,}")


def words(text: str) -> set[str]:
    return {w for w in WORD.findall(text.lower()) if w not in STOP}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("persona_dirs", type=Path, nargs="+")
    parser.add_argument("--queries", choices=["llm", "template"], default="llm")
    args = parser.parse_args()
    rng = random.Random(0)
    rows = {"target_text": [], "target_name": [], "random_text": []}
    for d in args.persona_dirs:
        meta = json.loads((d / "truth" / "persona.json").read_text(encoding="utf-8"))
        if meta["persona"]["family"] != "A":
            print(f"refusing: {d.name} is not a dev persona")
            return 2
        qfile = d / "truth" / ("queries_llm.jsonl" if args.queries == "llm" else "queries.jsonl")
        queries = [json.loads(x) for x in qfile.read_text(encoding="utf-8").splitlines()]
        files = [p for p in (d / "corpus").rglob("*") if p.is_file()]
        cache: dict[Path, set[str]] = {}

        def text_words(path: Path, cache=cache) -> set[str]:
            if path not in cache:
                doc = extract_file(path)
                cache[path] = words(doc.text if doc and doc.ok else "")
            return cache[path]

        for q in queries:
            qw = words(q["text"])
            if not qw:
                continue
            target = d / "corpus" / q["target"]
            other = rng.choice([f for f in files if f != target])
            rows["target_text"].append(len(qw & text_words(target)) / len(qw))
            rows["target_name"].append(len(qw & words(q["target"].replace("_", " "))) / len(qw))
            rows["random_text"].append(len(qw & text_words(other)) / len(qw))
    n = len(rows["target_text"])
    print(f"{args.queries} queries: n={n}")
    for k, v in rows.items():
        print(
            f"  overlap with {k:<12} mean {statistics.mean(v):.3f}   share==1.0 {sum(x == 1.0 for x in v) / n:.3f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
