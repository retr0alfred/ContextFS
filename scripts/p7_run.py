r"""The frozen v2-P7 evaluation run (log.md Phase 35).

    python scripts/p7_run.py freeze                 # write data/p7/frozen_config.json + .sha256
    python scripts/p7_run.py run --split dev [--skip-bench]
    python scripts/p7_run.py begin --split test     # record the one test run (commit this first)
    python scripts/p7_run.py run --split test

    python scripts/p7_run.py verify                 # check the frozen hashes against this root

``run`` first verifies that the frozen configuration's hash matches, and that every code file,
parameter file, model and split file it lists, and every persona's corpus and truth (content,
NTFS birth and modified times, Mark-of-the-Web), still has its frozen hash; any difference refuses
the run. On the test split the run counter is capped at ONE: a started run may resume its
remaining stages (same configuration), a finished run can never be repeated, and nothing is ever
tuned on test.

``run`` executes only from the staged copy in ``STAGE`` (inputs, models and Python environment
copied off the USB drive, log.md Phase 35): it refuses when this script, the interpreter or any
``sys.path`` entry lies outside it. Hugging Face models load offline from ``<root>/models/hf``.

Stages (each skipped when its output already exists, so an interrupted run resumes rather than
restarts):
1. v2 indexes (engine, CPU) in the scratch folder; bulk-copy events; per-event times with the
   frozen recon_v1 parameters; the evaluation-only rules_v0_bulk rows;
2. P-A1: scripts/time_eval.py;
3. P-A2 / P-A2s: scripts/engine_eval.py (expected-overlap feature, Decision 100 weights);
4. baselines: scripts/bench_eval.py --ce (v1 index in the scratch folder);
5. P-S: scripts/p_s_eval.py;
6. Holm over the pre-registered primaries {P-A2: recon_v1 - raw_mtime on time-cued queries,
   P-S: recon_v1 - ce_rerank on all queries}; P-A1 and P-A2s are reported with CIs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "p7"
CONFIG, CONFIG_SHA, RUNS = OUT / "frozen_config.json", OUT / "frozen_config.sha256", OUT / "test_runs.json"
SCRATCH = Path(r"C:\ContextFS-scratch\p7")
STAGE = Path(r"C:\ContextFS-scratch\p7stage")
PY = sys.executable
os.environ["HF_HOME"] = str(ROOT / "models" / "hf")  # inherited by every stage's subprocess
os.environ["HF_HUB_OFFLINE"] = os.environ["TRANSFORMERS_OFFLINE"] = "1"


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def persona_fingerprint(d: Path) -> str:
    """Hash of a persona's corpus and truth: path, content, NTFS birth and mtime, MotW stream."""
    h = hashlib.sha256()
    for p in sorted(x for s in ("corpus", "truth") for x in (d / s).rglob("*") if x.is_file()):
        st = p.stat()
        try:
            motw = (Path(str(p) + ":Zone.Identifier")).read_bytes()
        except OSError:
            motw = b""
        h.update(f"{p.relative_to(d).as_posix()}|{st.st_size}|{st.st_birthtime_ns}|{st.st_mtime_ns}|".encode())
        h.update(hashlib.sha256(p.read_bytes()).digest() + hashlib.sha256(motw).digest())
    return h.hexdigest()


def tracked_files() -> list[Path]:
    files = sorted((ROOT / "src" / "contextfs").rglob("*.py"))
    files += [ROOT / "scripts" / n for n in ("p7_run.py", "time_eval.py", "engine_eval.py", "bench_eval.py",
                                            "p_s_eval.py", "eval_estimators.py")]
    files += [ROOT / "contextfs.toml", ROOT / "data" / "splits" / "split_v1.json",
              ROOT / "data" / "splits" / "motw_fix_v1.json",
              ROOT / "models" / "onnx" / "minilm-l6-v2" / "model_fp32.onnx",
              ROOT / "models" / "onnx" / "minilm-l6-v2" / "tokenizer.json"]
    return [f for f in files if "__pycache__" not in f.parts]


def personas(split: str) -> list[Path]:
    fam = "-A-" if split == "dev" else "-B-"
    return sorted(d for d in (ROOT / "data" / "bench").iterdir() if fam in d.name and d.is_dir())


def freeze() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    import onnxruntime

    from contextfs.engine.rank import SearchConfig

    cfg = {
        "schema": "ContextFS v2-P7 frozen evaluation configuration",
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "onnxruntime": onnxruntime.__version__,
        "machine_timezone_note": "harnesses convert truth with the generator's fixed IST offset",
        "personas": {s: [d.name for d in personas(s)] for s in ("dev", "test")},
        "engine": {"device": "cpu", "threads": 4, "default_time_source": SearchConfig().time_source,
                   "time_feature": SearchConfig().time_feature, "weights": dict(SearchConfig().weights),
                   "weights_source": SearchConfig().weights_source},
        "time_sources": ["time_off", "raw_mtime", "rules_v0", "rules_v0_bulk", "recon_v1", "oracle"],
        "p_a1": "scripts/time_eval.py: coverage (persona CI), within-1-day, MdAE incl. abstentions, "
                "width p50/p90, interval score (alpha 0.2), existence P/R; per damage type and event",
        "p_a2": {"time_cued_cues": ["temporal", "mixed"], "metric": "nDCG@10", "primary_pair": "recon_v1 - raw_mtime"},
        "p_a2s": {"target_damage": ["content_copy", "git_clone"], "metric": "share of oracle headroom over raw_mtime"},
        "p_s": {"system": "recon_v1 (default config)", "baseline": "ce_rerank (scripts/bench_eval.py --ce)",
                "memory_cues": ["temporal", "activity", "mixed", "vague"],
                "content_cues": ["semantic", "entity", "format"], "primary": "all queries"},
        "holm_family": ["P-A2 recon_v1 - raw_mtime (time-cued)", "P-S recon_v1 - ce_rerank (all)"],
        "added_baseline": "rules_v0_bulk = rules_v0 with recon_v1's bulk-copy detector (review 2, finding 5)",
        "test_run_cap": 1,
        "scratch": str(SCRATCH),
        "stage": str(STAGE),
        "sessions_time_source": "rules_v0 (Decision 107; moving sessions is carried forward)",
        "hashes": {str(f.relative_to(ROOT)).replace("\\", "/"): sha_file(f) for f in tracked_files()},
        "persona_fingerprints": {d.name: persona_fingerprint(d) for s in ("dev", "test") for d in personas(s)},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(cfg, indent=1, sort_keys=True).encode("utf-8")
    CONFIG.write_bytes(blob)
    digest = hashlib.sha256(blob).hexdigest()
    CONFIG_SHA.write_text(digest + "  frozen_config.json\n", encoding="utf-8")
    print(f"frozen: {len(cfg['hashes'])} files hashed; config sha256 {digest}")
    return 0


def verify() -> tuple[dict, str]:
    blob = CONFIG.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if CONFIG_SHA.read_text(encoding="utf-8").split()[0] != digest:
        raise SystemExit("refusing: frozen_config.json does not match its recorded sha256")
    cfg = json.loads(blob)
    bad = [f for f, h in cfg["hashes"].items() if not (ROOT / f).exists() or sha_file(ROOT / f) != h]
    if bad:
        raise SystemExit(f"refusing: {len(bad)} frozen files changed since the freeze, e.g. {bad[:5]}")
    bad = [n for n, h in cfg["persona_fingerprints"].items() if persona_fingerprint(ROOT / "data" / "bench" / n) != h]
    if bad:
        raise SystemExit(f"refusing: {len(bad)} persona corpora differ from the freeze, e.g. {bad[:5]}")
    print(f"verified at {ROOT}: {len(cfg['hashes'])} files and {len(cfg['persona_fingerprints'])} personas "
          f"match config {digest[:16]}")
    return cfg, digest


def check_stage() -> None:
    inside = [ROOT, Path(sys.executable).resolve(), Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()]
    off = [str(p) for p in inside if not p.resolve().is_relative_to(STAGE)]
    off += [p for p in sys.path if p and Path(p).resolve().drive.upper() != STAGE.drive.upper()]
    if off:
        raise SystemExit(f"refusing: the run must execute entirely from {STAGE}; outside it: {off[:5]}")


def stage(name: str, marker: Path, cmd: list[str]) -> None:
    if marker.exists():
        print(f"[skip] {name} (done)")
        return
    print(f"[run ] {name}: {' '.join(str(c) for c in cmd[1:4])} ...", flush=True)
    t = time.perf_counter()
    subprocess.run(cmd, cwd=str(ROOT), check=True)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"stage": name, "seconds": round(time.perf_counter() - t, 1),
                                  "finished": datetime.now(timezone.utc).isoformat(timespec="seconds")}),
                      encoding="utf-8")


def runs() -> dict:
    return json.loads(RUNS.read_text(encoding="utf-8")) if RUNS.exists() else {"runs": []}


def begin(digest: str) -> int:
    r = runs()
    if any(x["status"] == "finished" for x in r["runs"]):
        raise SystemExit("refusing: the single test run has already finished")
    if any(x["status"] == "started" for x in r["runs"]):
        print("a test run is already recorded as started; use run to resume it")
        return 0
    r["runs"].append({"run": 1, "config_sha256": digest, "status": "started",
                      "started": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    RUNS.write_text(json.dumps(r, indent=1), encoding="utf-8")
    print("test run 1 recorded as started")
    return 0


def run(split: str, cfg: dict, digest: str, skip_bench: bool) -> int:
    if split == "test":
        r = runs()
        started = [x for x in r["runs"] if x["status"] == "started"]
        if any(x["status"] == "finished" for x in r["runs"]):
            raise SystemExit("refusing: the single test run has already finished")
        if not started or started[0]["config_sha256"] != digest:
            raise SystemExit("refusing: no started test run for this configuration (run 'begin' and commit it)")
    tag = "TEST-p7" if split == "test" else "dev-p7"
    out = OUT / split
    v2, v1 = SCRATCH / split / "v2", SCRATCH / split / "v1"
    dirs = [ROOT / "data" / "bench" / n for n in cfg["personas"][split]]
    marks = out / "stages"
    # 1. indexes, times, evaluation-only rows
    if not (marks / "indexes.json").exists():
        sys.path.insert(0, str(ROOT / "src"))
        sys.path.insert(0, str(ROOT / "scripts"))
        from contextfs.engine import Engine
        from contextfs.engine.indexer import _folder_of, recompute_times, refresh_bulk_events
        from contextfs.engine.schema import connect
        from eval_estimators import write_eval_rows

        t = time.perf_counter()
        for d in dirs:
            idx = v2 / d.name
            if not (idx / "contextfs.db").exists() or not (idx / ".complete").exists():
                with Engine.open(idx, mode="write", device="cpu", threads=4) as eng:
                    rep = eng.update([d / "corpus"])
                print(f"  indexed {d.name}: {rep.new} files, {rep.errors} errors", flush=True)
            conn = connect(idx / "contextfs.db")
            refresh_bulk_events(conn)
            recompute_times(conn, {(r[0], _folder_of(r[1])) for r in conn.execute(
                "SELECT root_id, rel_path FROM files WHERE status='present'")})
            conn.close()
            write_eval_rows(idx)
            (idx / ".complete").write_text("ok", encoding="utf-8")
        marks.mkdir(parents=True, exist_ok=True)
        (marks / "indexes.json").write_text(json.dumps({"seconds": round(time.perf_counter() - t, 1)}),
                                           encoding="utf-8")
    stage("P-A1", marks / "p_a1.json",
          [PY, "scripts/time_eval.py", *map(str, dirs), "--tag", tag, "--index-root", str(v2), "--out", str(out)])
    stage("P-A2", marks / "p_a2.json",
          [PY, "scripts/engine_eval.py", *map(str, dirs), "--tag", tag, "--index-root", str(v2),
           "--time-feature", "expected", "--out", str(out)])
    bench_pq = out / f"{tag}-bench" / "per_query.jsonl"
    if not skip_bench:
        stage("baselines", marks / "bench.json",
              [PY, "scripts/bench_eval.py", *map(str, dirs), "--tag", f"{tag}-bench", "--ce",
               "--index-root", str(v1), "--out", str(out)])
    else:
        bench_pq = ROOT / "data" / "bench_results" / "dev-v1-llm" / "per_query.jsonl"
    stage("P-S", marks / "p_s.json",
          [PY, "scripts/p_s_eval.py", str(out / tag / "per_query.jsonl"), str(bench_pq), "--out", str(out / "p_s")])
    # Holm over the two pre-registered primaries
    eng = json.loads((out / tag / "summary.json").read_text(encoding="utf-8"))
    ps = json.loads((out / "p_s" / "p_s.json").read_text(encoding="utf-8"))
    sys.path.insert(0, str(ROOT / "src"))
    from contextfs.evalkit import holm

    fam = {"P-A2 recon_v1 - raw_mtime (time-cued)": eng["p_a2"]["recon_v1 - raw_mtime"]["p"],
           "P-S recon_v1 - ce_rerank (all)": ps["all"]["p"]}
    adj = holm(fam)
    result = {"split": split, "config_sha256": digest, "tag": tag,
              "holm": {k: {"p": p, "p_holm": round(adj[k], 5)} for k, p in fam.items()},
              "finished": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (out / "RESULT.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    if split == "test":
        r = runs()
        for x in r["runs"]:
            if x["status"] == "started":
                x["status"], x["finished"] = "finished", result["finished"]
        RUNS.write_text(json.dumps(r, indent=1), encoding="utf-8")
    print(json.dumps(result, indent=1))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("freeze", "verify", "begin", "run"))
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--skip-bench", action="store_true", help="dev only: reuse Phase 31's dev baselines")
    args = parser.parse_args()
    if args.action == "freeze":
        return freeze()
    if args.action == "run":
        check_stage()
    cfg, digest = verify()
    if args.action == "verify":
        return 0
    if args.action == "begin":
        return begin(digest)
    if args.split == "test" and args.skip_bench:
        raise SystemExit("refusing: the test run always runs the baselines")
    return run(args.split, cfg, digest, args.skip_bench)


if __name__ == "__main__":
    raise SystemExit(main())
