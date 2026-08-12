"""Evaluate activity-session reconstruction against ground truth.

Reports pairwise precision/recall/F1 over "same session" judgements, plus how
many of the deliberately planted sessions were recovered.

Ground-truth convention
-----------------------
The corpus's ``personal_misc`` group is a **negative control**, not a session:
five personal files sharing a folder but spanning 223 days, included precisely
so that over-clustering is punished. Its members are therefore treated as
having *no* session, contributing no same-session pairs, so grouping them costs
precision. This is stated here because it is the single most consequential
choice in the protocol - scoring them as a true session would reward exactly the
failure the control exists to detect.

Usage:
    python scripts/session_eval.py
    python scripts/session_eval.py --sessions   # show every reconstructed session
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contextfs.activity import SessionBuilder, session_accuracy  # noqa: E402
from contextfs.config import load_config  # noqa: E402
from contextfs.datagen.corpus_spec import CORPUS_FILES, SESSIONS  # noqa: E402
from contextfs.embed import VectorStore  # noqa: E402
from contextfs.store import Store  # noqa: E402

ML = "College/Semester7/MachineLearning"
KEY_PDF = f"{ML}/Unit4_Ensemble_Methods.pdf"
TIMETABLE = f"{ML}/Exam_Timetable_Sem7.xlsx"


def ground_truth() -> dict[str, str | None]:
    """Return ``{path: session_id_or_None}``, negative control mapped to None."""
    control = {session.id for session in SESSIONS if session.kind == "none"}
    return {spec.path: (None if spec.session in control else spec.session) for spec in CORPUS_FILES}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--sessions", action="store_true", help="list reconstructed sessions")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    cfg = load_config(args.config or (PROJECT_ROOT / "contextfs.toml"))
    if not cfg.db_path.is_file():
        print(f"no index at {cfg.db_path}; run `contextfs scan` first")
        return 1

    store = Store(cfg.db_path, read_only=True)
    vectors = VectorStore(cfg.vector_dir, cfg.embeddings.dimension)
    report = SessionBuilder(cfg).build(store, vectors)
    truth = ground_truth()
    metrics = session_accuracy(report.sessions, truth)

    print("=" * 78)
    print("ACTIVITY SESSION RECONSTRUCTION")
    print("=" * 78)
    print(f"gap limit         {cfg.activity.session_gap_hours} h")
    print(f"link threshold    {cfg.activity.session_link_threshold}")
    print(f"min session size  {cfg.activity.min_session_size}")
    print(
        f"affinity weights  temporal {SessionBuilder.WEIGHT_TEMPORAL} / "
        f"semantic {SessionBuilder.WEIGHT_SEMANTIC} / "
        f"entity {SessionBuilder.WEIGHT_ENTITY} / folder {SessionBuilder.WEIGHT_FOLDER}"
    )
    print()
    summary = report.summary()
    for key, value in summary.items():
        print(f"  {key:<22} {value}")
    print()

    print("pairwise agreement with ground truth (positive = same session)")
    print(f"  true pairs        {metrics['true_pairs']}")
    print(f"  predicted pairs   {metrics['predicted_pairs']}")
    print(f"  tp / fp / fn      {metrics['tp']} / {metrics['fp']} / {metrics['fn']}")
    print(f"  PRECISION         {metrics['pairwise_precision']:.3f}")
    print(f"  RECALL            {metrics['pairwise_recall']:.3f}")
    print(f"  SESSION ACCURACY  {metrics['pairwise_f1']:.3f}   (pairwise F1)")
    print()
    print(
        f"planted sessions recovered: {metrics['sessions_recovered']}"
        f"/{metrics['true_sessions']}  (F1 >= 0.5 against some reconstructed session)"
    )
    for session_id, detail in sorted(metrics["per_session"].items()):
        status = "OK " if detail["f1"] >= 0.5 else "MISS"
        print(
            f"  {status} {session_id:<22} F1={detail['f1']:.2f}  "
            f"overlap={detail.get('overlap', 0)}/{detail.get('true_size', '?')}"
            f"  -> {detail.get('matched') or 'nothing'}"
        )
    print()

    # -- THE case the whole layer exists for -------------------------------
    print("THE ADVERSARIAL CASE (query q01)")
    print("  'the pdf I studied before my machine learning exam'")
    membership = {}
    for session in report.sessions:
        for path in session.paths:
            membership[path] = session.session_id
    pdf_session = membership.get(KEY_PDF)
    timetable_session = membership.get(TIMETABLE)
    print(f"  Unit4_Ensemble_Methods.pdf -> {pdf_session or 'NO SESSION'}")
    print(f"  Exam_Timetable_Sem7.xlsx   -> {timetable_session or 'NO SESSION'}")
    if pdf_session and pdf_session == timetable_session:
        print("  RESULT: same session. The PDF is reachable from an exam query")
        print("          even though it contains no exam vocabulary.")
    else:
        print("  RESULT: FAILED - they are not in the same session, so activity")
        print("          retrieval cannot recover the PDF.")
    print()

    control = [s.id for s in SESSIONS if s.kind == "none"]
    control_paths = {spec.path for spec in CORPUS_FILES if spec.session in control}
    grouped_control = {path: membership[path] for path in control_paths if path in membership}
    print("NEGATIVE CONTROL (Personal/Misc must NOT become a session)")
    if not grouped_control:
        print(f"  RESULT: correct - none of the {len(control_paths)} control files were clustered.")
    else:
        clusters = {}
        for path, session_id in grouped_control.items():
            clusters.setdefault(session_id, []).append(path)
        over = {sid: paths for sid, paths in clusters.items() if len(paths) > 1}
        if over:
            print(f"  RESULT: OVER-CLUSTERED - {len(over)} cluster(s) group control files:")
            for session_id, paths in over.items():
                print(f"    {session_id}: {[Path(p).name for p in paths]}")
        else:
            print("  RESULT: acceptable - control files only ever joined non-control sessions")
            for path, session_id in grouped_control.items():
                print(f"    {Path(path).name} -> {session_id}")
    print()

    if args.sessions:
        print("RECONSTRUCTED SESSIONS")
        for session in report.sessions:
            print(f"\n  {session.session_id}  [{session.kind}]  cohesion={session.cohesion:.3f}")
            print(f"    {session.label}")
            print(f"    {session.span_hours:.0f}h span, {session.size} files")
            if session.meaningful_dates:
                print(f"    dates: {', '.join(session.meaningful_dates)}")
            for path in session.paths:
                marker = "  <- ground truth: " + str(truth.get(path))
                print(f"      {path}{marker}")
        print()

    print(f"Sample size: {metrics['true_pairs']} true same-session pairs over 40 files,")
    print("across 5 planted sessions plus one negative control. Small - this")
    print("demonstrates the mechanism, it is not a generalisable accuracy figure.")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "config": {
                        "gap_hours": cfg.activity.session_gap_hours,
                        "link_threshold": cfg.activity.session_link_threshold,
                        "min_size": cfg.activity.min_session_size,
                    },
                    "summary": summary,
                    "metrics": metrics,
                    "adversarial_case_solved": bool(
                        pdf_session and pdf_session == timetable_session
                    ),
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
