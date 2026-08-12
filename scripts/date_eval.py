"""Evaluate meaningful-vs-incidental date classification against ground truth.

This produces the headline number for the project's highest-novelty component,
so the evaluation protocol is stated explicitly rather than left implicit.

Protocol
--------
* The ground truth labels ``(file, date)`` pairs, not individual mentions. The
  classifier's verdicts are therefore collapsed to one per pair, keeping the
  best-scoring mention, before comparison.
* **Only labelled pairs are scored.** The classifier detects more dates than
  the ground truth labels (the corpus was authored with labels for the dates
  that matter to the argument, not for every string that looks like a date).
  Scoring against unlabelled detections would measure the annotation's
  completeness rather than the classifier's accuracy. The number of unlabelled
  detections is reported separately so this is visible, not hidden.
* Positive class = meaningful. Precision = of the dates called meaningful, how
  many were; recall = of the meaningful dates, how many were found.

Usage:
    python scripts/date_eval.py
    python scripts/date_eval.py --sweep      # threshold sensitivity
    python scripts/date_eval.py --errors     # show every mistake with its reasoning
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contextfs.config import load_config  # noqa: E402
from contextfs.datagen.corpus_spec import CORPUS_FILES  # noqa: E402
from contextfs.store import Store  # noqa: E402
from contextfs.temporal import DateClassifier  # noqa: E402


def ground_truth() -> dict[tuple[str, str], str]:
    """Return ``{(path, iso_date): "meaningful" | "incidental"}``."""
    labels: dict[tuple[str, str], str] = {}
    for spec in CORPUS_FILES:
        for label in spec.dates:
            labels[(spec.path, label.date)] = label.kind
    return labels


def score(verdicts, labels, threshold=None):
    """Compute the confusion matrix over labelled pairs only."""
    counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    errors = {"false_positive": [], "false_negative": []}
    unlabelled = 0

    for verdict in verdicts:
        key = (verdict.rel_path, verdict.iso_date)
        truth = labels.get(key)
        if truth is None:
            unlabelled += 1
            continue
        predicted = verdict.score >= threshold if threshold is not None else verdict.is_meaningful
        if truth == "meaningful" and predicted:
            counts["tp"] += 1
        elif truth == "incidental" and predicted:
            counts["fp"] += 1
            errors["false_positive"].append(verdict)
        elif truth == "meaningful" and not predicted:
            counts["fn"] += 1
            errors["false_negative"].append(verdict)
        else:
            counts["tn"] += 1
    return counts, errors, unlabelled


def prf(counts):
    """Precision, recall, F1 and accuracy from a confusion matrix."""
    tp, fp, fn, tn = counts["tp"], counts["fp"], counts["fn"], counts["tn"]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (tp + tn) / max(1, tp + tn + fp + fn)
    return precision, recall, f1, accuracy


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--sweep", action="store_true", help="threshold sensitivity table")
    parser.add_argument("--errors", action="store_true", help="show every misclassification")
    parser.add_argument("--json", type=Path, default=None, help="write results as JSON")
    args = parser.parse_args()

    cfg = load_config(args.config or (PROJECT_ROOT / "contextfs.toml"))
    if not cfg.db_path.is_file():
        print(f"no index at {cfg.db_path}; run `contextfs scan` first")
        return 1

    store = Store(cfg.db_path)
    classifier = DateClassifier(cfg)
    verdicts = classifier.collapse(classifier.classify_store(store))
    labels = ground_truth()

    counts, errors, unlabelled = score(verdicts, labels)
    precision, recall, f1, accuracy = prf(counts)

    print("=" * 78)
    print("MEANINGFUL vs INCIDENTAL DATE CLASSIFICATION")
    print("=" * 78)
    print(f"threshold          {classifier.threshold}")
    print(f"weights            {classifier.weights}")
    print(f"year-only penalty  {classifier.year_only_penalty}")
    print()
    print(f"(file, date) pairs detected  {len(verdicts)}")
    print(f"  of which labelled          {sum(counts.values())}")
    print(f"  of which unlabelled        {unlabelled}  (excluded from scoring)")
    print(f"ground-truth labels          {len(labels)}")
    missing = set(labels) - {(v.rel_path, v.iso_date) for v in verdicts}
    print(f"  labelled but NOT detected  {len(missing)}  (counted as false negatives below)")
    print()

    # Undetected ground-truth dates are recall failures and must be counted.
    for path, iso in missing:
        if labels[(path, iso)] == "meaningful":
            counts["fn"] += 1
        else:
            counts["tn"] += 1
    precision, recall, f1, accuracy = prf(counts)

    print("confusion matrix (positive class = meaningful)")
    print(f"  true positives   {counts['tp']:>4}")
    print(f"  false positives  {counts['fp']:>4}")
    print(f"  false negatives  {counts['fn']:>4}")
    print(f"  true negatives   {counts['tn']:>4}")
    print()
    print(f"  PRECISION        {precision:.3f}")
    print(f"  RECALL           {recall:.3f}")
    print(f"  F1               {f1:.3f}")
    print(f"  accuracy         {accuracy:.3f}")
    print()

    baseline_total = counts["tp"] + counts["fn"] + counts["fp"] + counts["tn"]
    positives = counts["tp"] + counts["fn"]
    print("baseline for comparison (naive extraction: treat EVERY date as meaningful)")
    naive_precision = positives / baseline_total if baseline_total else 0.0
    print(
        f"  precision {naive_precision:.3f}   recall 1.000   "
        f"F1 {2 * naive_precision / (naive_precision + 1) if naive_precision else 0:.3f}"
    )
    print("  (this is RQ3: does meaningful-date detection beat naive extraction?)")
    print()

    if args.errors:
        for kind in ("false_positive", "false_negative"):
            group = errors[kind]
            print(f"{kind.upper().replace('_', ' ')}S ({len(group)}):")
            for verdict in sorted(group, key=lambda v: -v.score):
                print(f"  {verdict.iso_date}  {verdict.rel_path}")
                print(f"    surface: {verdict.surface!r}")
                print(f"    {verdict.reason()}")
                explanation = verdict.explain()
                print(f"    contributions: {explanation['contributions']}")
            print()

    if args.sweep:
        print("threshold sensitivity")
        print(f"  {'threshold':>9} {'precision':>10} {'recall':>8} {'F1':>8}")
        for step in range(20, 90, 5):
            candidate = step / 100
            swept, _, _ = score(verdicts, labels, threshold=candidate)
            for path, iso in missing:
                if labels[(path, iso)] == "meaningful":
                    swept["fn"] += 1
                else:
                    swept["tn"] += 1
            p, r, f, _ = prf(swept)
            marker = "  <- configured" if abs(candidate - classifier.threshold) < 1e-9 else ""
            print(f"  {candidate:>9.2f} {p:>10.3f} {r:>8.3f} {f:>8.3f}{marker}")
        print()

    print(f"Sample size: {baseline_total} labelled (file, date) pairs.")
    print("This corpus is small. These figures show the model behaves as designed;")
    print("they are NOT a statistically significant accuracy estimate.")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "threshold": classifier.threshold,
                    "weights": classifier.weights,
                    "counts": counts,
                    "precision": precision,
                    "recall": recall,
                    "f1": f1,
                    "accuracy": accuracy,
                    "labelled_pairs": baseline_total,
                    "unlabelled_detections": unlabelled,
                    "naive_baseline_precision": naive_precision,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote {args.json}")

    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
