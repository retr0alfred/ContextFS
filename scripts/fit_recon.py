r"""Fit recon_v1's widen-only interval calibration on the dev split (log.md Phase 34).

    python scripts/fit_recon.py data/bench/*-A-* --index-root DIR --mode lopo|profile|archetype|final

recon_v1's evidence rules are fixed; the only fitted part is, per "<event>:<method>":
- the residual quantiles ``q_lo = Q(alpha/2)`` (<= 0) and ``q_hi = Q(1 - alpha/2)`` (>= 0) of
  truth - point, which *widen* the method's interval to a target coverage of 1 - alpha (never
  narrow it, so the fit cannot import the simulator's fixed timeline into a tighter interval);
- a confidence label from the method's within-1-day rate (>= 0.9 sure, >= 0.5 probably, else
  unknown).
Methods seen fewer than ``MIN_N`` times stay uncalibrated.

Modes:
- ``lopo``: for each persona, fit on the other 19 and write its file_time with that fit;
- ``profile``/``archetype``: hold out one damage profile / one archetype at a time;
- ``final``: fit on all dev personas and write ``src/contextfs/engine/recon_params.py`` (the shipped
  parameters, frozen with a hash before the test run).

Truth is read by this harness only. Test personas are refused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from time_eval import index_of, truth_of  # noqa: E402

ALPHA, MIN_N, DAY = 0.2, 20, 86400


def profile_of(name: str) -> str:
    return name.rsplit("-", 1)[1]


def archetype_of(name: str) -> str:
    return name.split("-", 1)[0]


def residuals(persona: Path, index_root: Path) -> list[dict]:
    """Uncalibrated recon_v1 answers for one persona, joined with truth."""
    from contextfs.engine.estimate import ReconV1
    from contextfs.engine.indexer import _folder_of, estimate_folder, refresh_bulk_events
    from contextfs.engine.schema import connect

    conn = connect(index_of(persona, index_root) / "contextfs.db")
    refresh_bulk_events(conn)
    est = ReconV1(params={})
    path = dict(conn.execute("SELECT file_id, rel_path FROM files"))
    out = []
    truth = truth_of(persona)
    for (root_id,) in conn.execute("SELECT root_id FROM roots WHERE state!='removed'").fetchall():
        bulk = [tuple(e) for e in json.loads(
            (conn.execute("SELECT value FROM meta WHERE key=?", (f"bulk_events:{root_id}",)).fetchone() or ["[]"])[0])]
        folders = {_folder_of(r[0]) for r in conn.execute(
            "SELECT rel_path FROM files WHERE root_id=? AND status='present'", (root_id,))}
        for folder in folders:
            _, rows = estimate_folder(conn, root_id, folder, bulk, estimators=[est])
            for fid, _name, event, _lo, _hi, point, _conf, method, _ev in rows:
                tv = truth.get(path[fid], {}).get("truth", {}).get(event)
                if tv is not None:
                    out.append({"persona": persona.name, "event": event, "method": method, "r": tv - point})
    conn.close()
    return out


def _quantile(sorted_values: list[int], p: float) -> int:
    n = len(sorted_values)
    return sorted_values[min(n - 1, max(0, int(p * (n - 1) + 0.5)))]


def fit(records: list[dict]) -> dict:
    by: dict[str, list[int]] = {}
    for r in records:
        by.setdefault(f"{r['event']}:{r['method']}", []).append(r["r"])
    params = {}
    for key, rs in sorted(by.items()):
        if len(rs) < MIN_N:
            continue
        s = sorted(rs)
        w1 = sum(abs(x) <= DAY for x in rs) / len(rs)
        params[key] = {"q_lo": min(0, int(_quantile(s, ALPHA / 2))), "q_hi": max(0, int(_quantile(s, 1 - ALPHA / 2))),
                       "conf": "sure" if w1 >= 0.9 else "probably" if w1 >= 0.5 else "unknown", "n": len(rs)}
    return params


def write_with(persona: Path, index_root: Path, params: dict) -> None:
    """Recompute every estimator's file_time for one persona, recon_v1 using ``params``."""
    import contextfs.engine.estimate as estimate
    from contextfs.engine.indexer import _folder_of, recompute_times
    from contextfs.engine.schema import connect

    saved = dict(estimate.RECON_PARAMS)
    estimate.RECON_PARAMS.clear()
    estimate.RECON_PARAMS.update(params)
    try:
        conn = connect(index_of(persona, index_root) / "contextfs.db")
        folders = {(r[0], _folder_of(r[1])) for r in conn.execute(
            "SELECT root_id, rel_path FROM files WHERE status='present'")}
        recompute_times(conn, folders)
        conn.close()
    finally:
        estimate.RECON_PARAMS.clear()
        estimate.RECON_PARAMS.update(saved)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("persona_dirs", type=Path, nargs="+")
    parser.add_argument("--index-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("lopo", "profile", "archetype", "final"), required=True)
    args = parser.parse_args()
    if any("-B-" in d.name for d in args.persona_dirs):
        print("refusing: test personas (family B) are frozen until v2-P7")
        return 2
    res = {d.name: residuals(d, args.index_root) for d in args.persona_dirs}
    if args.mode == "final":
        params = fit([r for rs in res.values() for r in rs])
        body = json.dumps(params, indent=1, sort_keys=True)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        out = ROOT / "src" / "contextfs" / "engine" / "recon_params.py"
        out.write_text(
            '"""recon_v1 calibration — GENERATED by scripts/fit_recon.py --mode final. Do not edit.\n\n'
            f"Fitted on the {len(res)} dev personas only, {datetime.now():%Y-%m-%d}; alpha = {ALPHA}, "
            f"widen-only. sha256 of the JSON body: {digest}\n"
            '"""\n\nimport json\n\n'
            f"PARAMS: dict[str, dict] = json.loads(r'''{body}''')\n",
            encoding="utf-8",
        )
        print(f"wrote {out} ({len(params)} methods), sha256 {digest}")
        return 0
    group = {"lopo": lambda n: n, "profile": profile_of, "archetype": archetype_of}[args.mode]
    for d in args.persona_dirs:
        train = [r for name, rs in res.items() if group(name) != group(d.name) for r in rs]
        write_with(d, args.index_root, fit(train))
    print(f"{args.mode}: wrote held-out calibrated file_time for {len(args.persona_dirs)} personas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
