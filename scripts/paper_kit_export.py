r"""Export the final results of v2 Phases 5-8 as plain tables for the paper kit (log.md Phase 37).

    python scripts/paper_kit_export.py --out DIR [--p8-hardening DIR\report.json]

Reads only result files already in the repo (and the Phase 8 hardening report, wherever it was run)
and writes CSV/JSON tables: no numbers are computed here except reshaping and rounding.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "data"


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def pa1_tables(summary: dict, out: Path, prefix: str) -> None:
    rows = []
    for key, s in summary["rows"].items():
        if not s:
            continue
        est, dmg, ev = key.split("|")
        ci = s.get("coverage_ci") or [None, None]
        rows.append([est, dmg, ev, s["n"], s["answered"], s["coverage"], ci[0], ci[1], s["within1d"], s["mdae_d"],
                     s["mdae_all_d"], s["width_p50_d"], s["width_p90_d"], s["width_all_p50_d"], s["is20_d"]])
    write_csv(out / f"{prefix}_rows.csv",
              ["estimator", "damage", "event", "n", "answered", "coverage", "coverage_ci_lo", "coverage_ci_hi",
               "within_1d", "mdae_days_answered", "mdae_days_all", "width_p50_days", "width_p90_days",
               "width_all_p50_days", "interval_score_median_days_alpha0.2"], rows)
    write_csv(out / f"{prefix}_macro.csv", ["estimator", "event", "coverage", "within_1d", "interval_score_days"],
              [[*k.split("|"), v["coverage"], v["within1d"], v["is20_d"]] for k, v in summary["macro"].items()])
    write_csv(out / f"{prefix}_existence.csv", ["estimator", "origin", "event", "TP", "FN", "FP", "TN", "precision", "recall"],
              [[*k.split("|"), v["TP"], v["FN"], v["FP"], v["TN"], v["precision"], v["recall"]]
               for k, v in summary["existence"].items()])
    if "p_a1_test" in summary:
        write_csv(out / f"{prefix}_test.csv",
                  ["comparison", "event", "n_personas", "mean_is_days_a", "mean_is_days_b", "diff_days_b_minus_a",
                   "p_signflip", "ci95_lo", "ci95_hi", "personas_a_better"],
                  [[k, v["event"], v["n_personas"], *[v[x] for x in v if x.startswith("mean_is_d_")], v["diff"], v["p"],
                    *v["ci95_persona"], v["personas_a_better"]] for k, v in summary["p_a1_test"].items()])


def pa2_tables(summary: dict, out: Path, prefix: str) -> None:
    pa2 = summary["p_a2"]
    write_csv(out / f"{prefix}_means.csv", ["n_time_cued", *[k for k, v in pa2.items() if not isinstance(v, dict) and k != "n"]],
              [[pa2["n"], *[v for k, v in pa2.items() if not isinstance(v, dict) and k != "n"]]])
    write_csv(out / f"{prefix}_pairs.csv",
              ["pair", "mean_diff", "p_randomisation", "ci95_persona_lo", "ci95_persona_hi", "sd", "n_for_0.03",
               "n_for_observed"],
              [[k, v["mean"], v["p"], *v["ci95_persona"], v["power"]["sd"], v["power"]["n_for_0.03"],
                v["power"]["n_for_observed"]] for k, v in pa2.items() if isinstance(v, dict)])
    s = summary["p_a2s"]
    write_csv(out / f"{prefix}s.csv", ["source", "n", "ndcg10", "share_of_oracle_headroom", "ci95_lo", "ci95_hi"],
              [["raw_mtime", s["n"], s["raw_mtime"], 0.0, None, None], ["oracle", s["n"], s["oracle"], 1.0, None, None]]
              + [[k, s["n"], v["ndcg10"], v["share_of_headroom"], *v["ci95_persona"]]
                 for k, v in s.items() if isinstance(v, dict)])


def systems_table(summary: dict, label: str) -> list[list]:
    return [[label, k, m["ndcg10"]["mean"], *m["ndcg10"]["ci95"], m["mrr"]["mean"], m["hit1"]["mean"], m["r10"]["mean"]]
            for k, m in summary["systems"].items()]


def hardening(report: Path, tag: str) -> list[list]:
    r = load(report)
    return [[tag, case, all(v["checks"].values()), sum(v["checks"].values()), len(v["checks"]),
             "; ".join(k for k, ok in v["checks"].items() if not ok)] for case, v in sorted(r.items())]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--p8-hardening", type=Path)
    args = ap.parse_args()
    o = args.out

    # ---- Phase 7: the one test run
    t = D / "p7" / "test"
    pa1 = load(t / "p_a1" / "TEST-p7" / "summary.json")
    pa1_tables(pa1, o / "phase7_test", "pa1")
    eng = load(t / "p_a2" / "TEST-p7" / "summary.json")
    pa2_tables(eng, o / "phase7_test", "pa2")
    bench = load(t / "baselines" / "TEST-p7-bench" / "summary.json")
    write_csv(o / "phase7_test" / "systems_all_queries.csv",
              ["harness", "system", "ndcg10", "ndcg10_ci_lo", "ndcg10_ci_hi", "mrr", "hit1", "recall10"],
              systems_table(bench, "baselines (bench_eval)") + systems_table(eng, "contextfs v2 engine (time source)"))
    cues = sorted(eng["by_cue"])
    write_csv(o / "phase7_test" / "ndcg10_by_cue.csv",
              ["cue", "n", *[f"engine_{k}" for k in ("time_off", "raw_mtime", "rules_v0", "rules_v0_bulk", "recon_v1", "oracle")],
               *[f"baseline_{k}" for k in ("bm25", "dense_maxp", "rrf60", "v1_full", "ce_rerank")]],
              [[c, eng["by_cue"][c]["n"], *[eng["by_cue"][c][k] for k in ("time_off", "raw_mtime", "rules_v0", "rules_v0_bulk",
                                                                            "recon_v1", "oracle")],
                *[bench["by_cue"][c].get(k) for k in ("bm25", "dense_maxp", "rrf60", "v1_full", "ce_rerank")]] for c in cues])
    ps = load(t / "p_s" / "p_s.json")
    write_csv(o / "phase7_test" / "p_s.csv",
              ["queries", "n", "contextfs_ndcg10", "ce_rerank_ndcg10", "diff", "p_randomisation", "ci95_persona_lo",
               "ci95_persona_hi"],
              [[k, ps[k]["n"], ps[k]["contextfs"], ps[k]["baseline"], ps[k]["diff"], ps[k]["p"], *ps[k]["ci95_persona"]]
               for k in ("all", "memory_cue", "content")])
    res = load(t / "RESULT.json")
    write_csv(o / "phase7_test" / "holm_primaries.csv", ["primary", "p", "p_holm"],
              [[k, v["p"], v["p_holm"]] for k, v in res["holm"].items()])
    cfg = load(D / "p7" / "frozen_config.json")
    (o / "phase7_test" / "run_record.json").write_text(json.dumps({
        "config_sha256": res["config_sha256"], "finished": res["finished"], "test_runs": load(D / "p7" / "test_runs.json"),
        "personas_test": cfg["personas"]["test"], "n_personas_test": len(cfg["personas"]["test"]),
        "engine": cfg["engine"], "holm_family": cfg["holm_family"], "p_a2": cfg["p_a2"], "p_s": cfg["p_s"],
        "added_baseline": cfg["added_baseline"], "n_queries_test": eng["n_queries"], "test_run_cap": cfg["test_run_cap"],
    }, indent=1), encoding="utf-8")
    rep = load(D / "transfer_table" / "replay_v1.json")
    write_csv(o / "phase7_test" / "simulator_fidelity_replay.csv",
              ["behaviour", "n", "errors", "mtime_not_compared", "birth_agree", "mtime_agree", "infile_agree", "motw_agree"],
              [[b, s["n"], s["errors"], s["mtime_not_compared_edited_after_migration"], s["birth"]["agree"],
                s["mtime"]["agree"], s["infile"]["agree"], s["motw"]["agree"]] for b, s in rep["summary"].items()])
    # dev rehearsal of the frozen pipeline (in-sample)
    dv = D / "p7" / "dev"
    write_csv(o / "phase7_dev_rehearsal" / "holm_primaries_dev.csv", ["primary", "p", "p_holm"],
              [[k, v["p"], v["p_holm"]] for k, v in load(dv / "RESULT.json")["holm"].items()])

    # ---- Phase 6: dev results of recon_v1 (LOPO calibration) and the dev P-A2
    pa1_tables(load(D / "time_eval" / "dev-recon-A-lopo" / "summary.json"), o / "phase6_dev", "pa1_lopo")
    pa2_tables(load(D / "bench_results" / "dev-pa2-expected" / "summary.json"), o / "phase6_dev", "pa2")

    # ---- Phases 5, 6, 8: hardening on the packaged build
    rows = hardening(D / "hardening" / "p5-final3" / "report.json", "p5-final3")
    rows += hardening(D / "hardening" / "p6-final" / "report.json", "p6-final (exe f79cb80a)")
    if args.p8_hardening and args.p8_hardening.exists():
        rows += hardening(args.p8_hardening, "p8-final (exe c8c926f5)")
    write_csv(o / "hardening" / "cases.csv", ["run", "case", "pass", "checks_passed", "checks", "failed_checks"], rows)
    src = args.p8_hardening if args.p8_hardening and args.p8_hardening.exists() else D / "hardening" / "p6-final" / "report.json"
    hr = load(src)
    mrows = []
    for case in ("C01", "C15", "C16"):
        for k, v in hr.get(case, {}).get("metrics", {}).items():
            if not isinstance(v, (dict, list)):
                mrows.append([case, k, v])
    write_csv(o / "hardening" / "scale_metrics.csv", ["case", "metric", "value"], mrows)

    # ---- Phase 8: identity and memory
    ref = load(D / "p8" / "ref_p7.json")
    ident = []
    for name, f in (("phase8 app path (CPU default)", "new_app.json"), ("packaged exe 9703ae0e", "exe_9703ae0e.json"),
                    ("app path after memory fix", "new_app_after_memfix.json"), ("packaged exe 69d3e019", "exe_69d3e019.json"),
                    ("packaged exe c8c926f5 (final)", "exe_c8c926f5.json")):
        p = D / "p8" / f
        if p.exists():
            b = load(p)
            diff = sum(ref["personas"][n]["index"]["logical"] != b["personas"][n]["index"]["logical"] for n in ref["personas"])
            def norm(top, rounded=f.startswith("exe")):  # the console reports scores to 6 places
                return [[p, repr(round(float(s), 6))] for p, s in top] if rounded and top else top

            qd = sum(norm(ref["personas"][n]["top10"][q]) != norm(b["personas"][n]["top10"].get(q))
                     for n in ref["personas"] for q in ref["personas"][n]["top10"])
            ident.append([name, len(b["personas"]), sum(len(v["top10"]) for v in b["personas"].values()), diff, qd])
    write_csv(o / "phase8" / "identity_vs_phase7.csv",
              ["candidate", "personas", "queries", "logical_index_differences", "top10_differences (exe: scores at 6 places)"], ident)
    mem = []
    for label, p in (("before (exe f79cb80a)", D / "p8" / "memory_100000_off.json"),
                     ("after fix (exe 69d3e019)", D / "p8" / "after_memfix" / "memory_100000_off.json")):
        r = load(p)
        mem.append([label, r["files"], r["gpu"], r["full"]["exit"], r["full"]["seconds"], r["full"]["peak_private_mb"],
                    r["noop"]["seconds"], r["search_ms"]["p50"], r["search_ms"]["p95"], r["index_mb"]])
        write_csv(o / "phase8" / f"memory_series_{'before' if 'before' in label else 'after'}.csv",
                  ["seconds", "files_processed", "private_mb", "working_set_mb"],
                  [[x["s"], x["processed"], x["private_mb"], x["working_set_mb"]] for x in r["full"]["series"]])
    write_csv(o / "phase8" / "memory_100k.csv",
              ["run", "files", "gpu", "exit", "index_seconds", "peak_private_mb", "noop_update_seconds", "search_p50_ms",
               "search_p95_ms", "index_mb"], mem)
    write_csv(o / "phase8" / "memory_limits.csv", ["limit", "mb"],
              [["job limit, machines with <= 8 GB RAM", 1536], ["job limit, other machines", 2048]])
    (o / "phase8" / "arena_ab.txt").write_text((D / "p8" / "arena_ab.txt").read_text(encoding="utf-8"), encoding="utf-8")
    write_csv(o / "phase8" / "session_rebuild_memory_100k.csv", ["code", "delta_mb", "sessions_hash"],
              [["before (load all vectors)", 321, "a7898cd34edbd8a1"], ["after (memory-mapped, per block)", 47,
                                                                     "a7898cd34edbd8a1"]])
    print(f"exported to {o}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
