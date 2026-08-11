"""Evaluate entity extraction against the manually-labelled gold set.

Reports precision and recall per category over the five hand-labelled documents
in ``contextfs.datagen.corpus_spec.ENTITY_GOLD``.

Adjudication rules, stated so the numbers are reproducible and defensible:

* **Recall** - a gold entity counts as found if any detected span in the same
  category *contains* the gold string (case-insensitive). This credits
  "Dr. Murari Devakannan Kamalesh" for the gold label "Murari Devakannan
  Kamalesh", and credits a longer detected span that covers the gold name.
* **Precision** - a detected entity counts as correct if it matches a gold
  entity by containment in *either* direction. A detection that is a fragment
  of a gold entity ("Murari" for "Murari Devakannan Kamalesh") is counted
  correct, since it identifies the right referent; a detection that names
  something not on the gold list is counted as a false positive.
* Categories are compared independently. A person detected as an organisation
  is a false positive in ``org`` and a miss in ``person`` - not a half-credit.

The rules are deliberately generous on span boundaries and strict on category,
because span boundaries do not affect downstream use (entity-overlap edges key
on the normalised string) while category confusion does.

Usage:
    python scripts/entity_eval.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contextfs.config import load_config  # noqa: E402
from contextfs.datagen.corpus_spec import ENTITY_GOLD  # noqa: E402
from contextfs.entities import (  # noqa: E402
    EntityExtractor,
    apply_consensus,
    build_gazetteer,
    consensus_categories,
    propagate_gazetteer,
)
from contextfs.extract import extract_file  # noqa: E402
from contextfs.scanner import Scanner  # noqa: E402

CATEGORIES = ("people", "orgs", "locations")
CATEGORY_TO_INTERNAL = {"people": "person", "orgs": "org", "locations": "location"}


def found(gold: str, detected: list[str]) -> bool:
    """Whether a gold entity is covered by any detected string."""
    needle = gold.lower()
    return any(needle in d.lower() for d in detected)


def correct(detection: str, gold_list: tuple[str, ...]) -> bool:
    """Whether a detected entity matches any gold entity in either direction."""
    lowered = detection.lower()
    return any(lowered in g.lower() or g.lower() in lowered for g in gold_list)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--verbose", action="store_true", help="list every detection")
    args = parser.parse_args()

    cfg = load_config(args.config or (PROJECT_ROOT / "contextfs.toml"))
    root = cfg.paths.root
    if not root.is_dir():
        print(f"corpus not found at {root}; run scripts/generate_corpus.py first")
        return 1

    extractor = EntityExtractor(
        cfg.entities.spacy_model,
        max_keywords=cfg.entities.max_keywords,
        drop_acronym_orgs=cfg.entities.drop_acronym_orgs,
    )
    totals = {cat: {"tp": 0, "fp": 0, "fn": 0} for cat in CATEGORIES}

    print(f"gold set: {len(ENTITY_GOLD)} manually-labelled documents")
    print(f"model   : {cfg.entities.spacy_model}")

    # Run over the WHOLE corpus, not just the gold documents: consensus and
    # gazetteer propagation are corpus-level corrections, so evaluating the gold
    # files in isolation would measure a system the user never actually runs.
    from datetime import datetime

    corpus_results = {}
    corpus_text = {}
    for path in Scanner(cfg).walk():
        rel = path.relative_to(root).as_posix()
        doc = extract_file(path, rel, config=cfg)
        if doc.ok:
            reference = datetime.fromtimestamp(path.stat().st_mtime)
            corpus_results[rel] = extractor.extract(rel, doc.text, reference_date=reference)
            corpus_text[rel] = doc.text

    results = list(corpus_results.values())
    propagated = 0
    if cfg.entities.gazetteer_propagation:
        gazetteer = build_gazetteer(results, min_length=cfg.entities.gazetteer_min_length)
        for rel, result in corpus_results.items():
            propagated += propagate_gazetteer(result, corpus_text[rel], gazetteer)
        print(f"gazetteer: {len(gazetteer)} terms, {propagated} mention(s) propagated")

    decisions = consensus_categories(results)
    changed = apply_consensus(results, decisions)
    print(f"corpus  : {len(corpus_results)} documents analysed")
    print(
        f"consensus: {len(decisions)} entity string(s) had a category disagreement; "
        f"{changed} mention(s) reassigned\n"
    )
    if args.verbose and decisions:
        for name, category in sorted(decisions.items()):
            print(f"    {name!r} -> {category}")
        print()

    for rel_path, gold in ENTITY_GOLD.items():
        path = root / Path(rel_path)
        if not path.is_file():
            print(f"MISSING {rel_path}")
            return 1
        result = corpus_results[rel_path]

        print("=" * 78)
        print(rel_path)
        print("=" * 78)

        for category in CATEGORIES:
            gold_items = gold[category]
            detected = result.by_category(CATEGORY_TO_INTERNAL[category])

            hits = [g for g in gold_items if found(g, detected)]
            misses = [g for g in gold_items if not found(g, detected)]
            false_positives = [d for d in detected if not correct(d, gold_items)]

            totals[category]["tp"] += len(hits)
            totals[category]["fn"] += len(misses)
            totals[category]["fp"] += len(false_positives)

            print(f"  {category}:")
            print(f"    gold      ({len(gold_items)}): {list(gold_items)}")
            if args.verbose or misses or false_positives:
                print(f"    detected  ({len(detected)}): {detected}")
            if misses:
                print(f"    MISSED    ({len(misses)}): {misses}")
            if false_positives:
                print(f"    SPURIOUS  ({len(false_positives)}): {false_positives}")
            if not misses and not false_positives:
                print("    exact match")
        print(f"  dates detected: {len(result.dates)}   keywords: {len(result.keywords)}")
        print()

    print("=" * 78)
    print("ENTITY EXTRACTION: PRECISION / RECALL")
    print("=" * 78)
    print(
        f"{'category':<12} {'TP':>4} {'FP':>4} {'FN':>4}  {'precision':>9} {'recall':>8} {'F1':>8}"
    )
    print("-" * 60)

    grand = {"tp": 0, "fp": 0, "fn": 0}
    for category in CATEGORIES:
        counts = totals[category]
        for key in grand:
            grand[key] += counts[key]
        precision, recall, f1 = prf(counts)
        print(
            f"{category:<12} {counts['tp']:>4} {counts['fp']:>4} {counts['fn']:>4}  "
            f"{precision:>9.3f} {recall:>8.3f} {f1:>8.3f}"
        )

    precision, recall, f1 = prf(grand)
    print("-" * 60)
    print(
        f"{'MICRO AVG':<12} {grand['tp']:>4} {grand['fp']:>4} {grand['fn']:>4}  "
        f"{precision:>9.3f} {recall:>8.3f} {f1:>8.3f}"
    )
    print(
        f"\nSample size: {grand['tp'] + grand['fn']} gold entities across "
        f"{len(ENTITY_GOLD)} documents.\n"
        "This is a spot check for sanity, NOT a statistically meaningful\n"
        "entity-recognition benchmark. Reported as such."
    )
    return 0


def prf(counts: dict[str, int]) -> tuple[float, float, float]:
    """Return precision, recall, F1 from a TP/FP/FN dict."""
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


if __name__ == "__main__":
    raise SystemExit(main())
