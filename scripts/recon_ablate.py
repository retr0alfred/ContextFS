r"""recon_v1 ablations and perturbations on the dev split (log.md Phase 34, Decision 106).

    python scripts/recon_ablate.py data/bench/*-A-* --index-root DIR --tag NAME

Everything runs in memory through the engine's own ``estimate_folder`` (no index writes), with
*uncalibrated* estimators (no fitted widening) so that only the rules differ between variants.

Variants (from Decision 106 and the two independent reviews; arrival_B is the variant not chosen):
- ``recon_v1``            the shipped rules;
- ``no_existence``        an edit is dated for every file, as rules_v0 does;
- ``arrival_B``           a reset file came in "between creation and the copy" (variant B);
- ``rules_v0_bulk``       baseline: rules_v0 with recon_v1's bulk-copy detector (review 2, finding 5);
- ``sim_motw``            the uncorrected simulator view: download marks restored on content-copied
                          downloads (what the benchmark looked like before T v1.1);
Perturbations (departures from the simulator's conventions; truth adjusted where the event changes):
- ``own_export_dt0``      user-authored documents whose in-file modified == created (a quick export);
- ``edited_download``     undamaged downloads edited 3 days after download (truth gains that edit);
- ``photo_mtime_import``  undamaged camera files whose mtime is the import time, not the capture.

Reported per variant: coverage and within-1-day for every event on all files and on content-copied
files, and the existence precision/recall of last_user_edit by origin. Test personas are refused.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from time_eval import index_of, truth_of  # noqa: E402

DAY = 86400
DROPS = {"content_copy", "git_clone"}


def make_hook(kind: str, truth: dict):
    def hook(rel: str, rows: dict) -> dict:
        t = truth.get(rel)
        if t is None:
            return rows
        rows = {k: dict(v) for k, v in rows.items()}
        if kind == "sim_motw" and t["origin"] == "downloaded" and t["damage"] in DROPS:
            rows["motw:zone"] = {"source": "motw", "kind": "zone", "utc_s": None, "wall_s": None,
                                 "tz_state": "utc", "resolution_s": 1, "reliability": "normal", "raw": "3"}
        elif kind == "own_export_dt0" and t["origin"] == "authored":
            for doc in ("ooxml", "pdf"):
                if f"{doc}:created" in rows and f"{doc}:modified" in rows:
                    rows[f"{doc}:modified"]["utc_s"] = rows[f"{doc}:created"]["utc_s"]
        elif kind == "edited_download" and t["origin"] == "downloaded" and t["damage"] == "none":
            rows["ntfs:modified"]["utc_s"] = rows["ntfs:created"]["utc_s"] + 3 * DAY
        elif kind == "photo_mtime_import" and t["origin"] == "received" and t["damage"] == "none":
            rows["ntfs:modified"]["utc_s"] = rows["ntfs:created"]["utc_s"]
        return rows

    return hook


def adjusted_truth(kind: str, truth: dict, rel: str, ev_rows_m: int | None) -> dict:
    t = truth[rel]["truth"]
    if kind == "edited_download" and truth[rel]["origin"] == "downloaded" and truth[rel]["damage"] == "none":
        return {**t, "last_user_edit": ev_rows_m}
    return t


def run(persona: Path, index_root: Path, variant: str) -> list[dict]:
    from contextfs.engine.estimate import ReconV1, RulesV0Bulk
    from contextfs.engine.indexer import _folder_of, estimate_folder
    from contextfs.engine.schema import connect

    est = RulesV0Bulk() if variant == "rules_v0_bulk" else ReconV1(params={})
    if variant == "no_existence":
        est.EXISTENCE_GATING = False
    if variant == "arrival_B":
        est.AGNOSTIC_MOVED_ARRIVAL = True
    truth = truth_of(persona)
    hook = make_hook(variant, truth) if variant in (
        "sim_motw", "own_export_dt0", "edited_download", "photo_mtime_import") else None
    conn = connect(index_of(persona, index_root) / "contextfs.db")
    path = dict(conn.execute("SELECT file_id, rel_path FROM files"))
    created = {path[r[0]]: r[1] for r in conn.execute(
        "SELECT file_id, utc_s FROM time_evidence WHERE source='ntfs' AND kind='created'")}
    out = []
    for (root_id,) in conn.execute("SELECT root_id FROM roots WHERE state!='removed'").fetchall():
        row = conn.execute("SELECT value FROM meta WHERE key=?", (f"bulk_events:{root_id}",)).fetchone()
        bulk = [tuple(e) for e in json.loads(row[0] if row else "[]")]
        folders = {_folder_of(r[0]) for r in conn.execute(
            "SELECT rel_path FROM files WHERE root_id=? AND status='present'", (root_id,))}
        answers: dict[tuple, tuple] = {}
        for folder in folders:
            _, rows = estimate_folder(conn, root_id, folder, bulk, estimators=[est], evidence_hook=hook)
            for fid, _n, event, lo, hi, point, _c, _m, _e in rows:
                answers[(path[fid], event)] = (lo, hi, point)
        for rel, t in truth.items():
            m_new = created.get(rel, 0) + 3 * DAY if variant == "edited_download" else None
            tt = adjusted_truth(variant, truth, rel, m_new)
            for event in ("authored", "arrived_here", "last_user_edit"):
                a = answers.get((rel, event))
                out.append({"origin": t["origin"], "damage": t["damage"], "event": event, "truth": tt.get(event),
                            "answer": a})
    conn.close()
    return out


def score(recs: list[dict]) -> dict:
    out = {}
    for scope in ("ALL", "content_copy"):
        for event in ("authored", "arrived_here", "last_user_edit"):
            rs = [r for r in recs if r["event"] == event and r["truth"] is not None
                  and (scope == "ALL" or r["damage"] == scope)]
            if rs:
                cov = sum(r["answer"] is not None and r["answer"][0] <= r["truth"] <= r["answer"][1] for r in rs)
                w1 = sum(r["answer"] is not None and abs(r["answer"][2] - r["truth"]) <= DAY for r in rs)
                out[f"{scope}|{event}"] = {"n": len(rs), "coverage": round(cov / len(rs), 4),
                                           "within1d": round(w1 / len(rs), 4)}
    for origin in ("authored", "downloaded", "received"):
        rs = [r for r in recs if r["event"] == "last_user_edit" and r["origin"] == origin]
        tp = sum(r["truth"] is not None and r["answer"] is not None for r in rs)
        fp = sum(r["truth"] is None and r["answer"] is not None for r in rs)
        fn = sum(r["truth"] is not None and r["answer"] is None for r in rs)
        out[f"edit_existence|{origin}"] = {"TP": tp, "FP": fp, "FN": fn,
                                           "precision": round(tp / (tp + fp), 4) if tp + fp else None,
                                           "recall": round(tp / (tp + fn), 4) if tp + fn else None}
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("persona_dirs", type=Path, nargs="+")
    parser.add_argument("--index-root", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    if any("-B-" in d.name for d in args.persona_dirs):
        print("refusing: test personas (family B) are frozen until v2-P7")
        return 2
    variants = ("recon_v1", "rules_v0_bulk", "no_existence", "arrival_B", "sim_motw", "own_export_dt0",
                "edited_download", "photo_mtime_import")
    result = {}
    for v in variants:
        recs = [r for d in args.persona_dirs for r in run(d, args.index_root, v)]
        result[v] = score(recs)
        s = result[v]
        print(f"{v:<19} cc-arrival cov={s['content_copy|arrived_here']['coverage']:.3f} "
              f"cc-edit cov={s['content_copy|last_user_edit']['coverage']:.3f} "
              f"edit P/R own={s['edit_existence|authored']['precision']}/{s['edit_existence|authored']['recall']} "
              f"dl={s['edit_existence|downloaded']['precision']}/{s['edit_existence|downloaded']['recall']} "
              f"(FP {s['edit_existence|downloaded']['FP']}) "
              f"photo FP={s['edit_existence|received']['FP']}")
    out = ROOT / "data" / "time_eval" / args.tag
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
