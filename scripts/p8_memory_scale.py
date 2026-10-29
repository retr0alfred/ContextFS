r"""Indexer peak memory at scale, through the packaged build (log.md Phase 36; the 8 GB risk, Decision 105).

    python scripts/p8_memory_scale.py --dist C:\ContextFS-scratch\dist_copy --work C:\ContextFS-scratch\p8\mem
        [--scale 100000] [--docs 300] [--gpu off|auto] [--limit-mb 12000]

The corpus is the hardening suite's C15 recipe (same generator, seed 15), at any scale. The packaged
indexer child runs in a Job Object whose memory limit is raised for the measurement only
(``--limit-mb``), so the true peak is seen instead of a limit kill. Every 5 s the child's private
memory is sampled against its progress (files processed), which shows *where* memory grows. Then:
a no-op launch update, 60 searches through ``ContextFS-console.exe --find``, and the index size.
Output: ``data/p8/memory_<scale>_<gpu>.json``.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import random
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "hardening"))
import run_suite as S  # noqa: E402

from contextfs.app.winjob import ChildJob  # noqa: E402


class _PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


def private_mb(pid: int) -> tuple[int, int]:
    """(private bytes, working set) of a process, in MB; (0, 0) if it has gone."""
    k32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
    k32.OpenProcess.restype = wintypes.HANDLE
    h = k32.OpenProcess(0x1000 | 0x0010, False, pid)  # QUERY_LIMITED_INFORMATION | VM_READ
    if not h:
        return 0, 0
    try:
        c = _PMC()
        c.cb = ctypes.sizeof(c)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
            return 0, 0
        return round(c.PrivateUsage / 2**20), round(c.WorkingSetSize / 2**20)
    finally:
        k32.CloseHandle(h)


def run_indexer(exe: Path, index: Path, corpus: Path, gpu: str, limit_mb: int, cwd: Path) -> dict:
    log = index.parent / f"{index.name}.child.log"
    proc = subprocess.Popen([str(exe), "--indexer", "--gpu", gpu, str(index), str(corpus)],
                            stdout=subprocess.PIPE, stderr=open(log, "wb"), cwd=str(cwd))  # noqa: SIM115
    job = ChildJob(memory_limit=limit_mb * 2**20)
    job.assign(proc.pid)
    msgs: list[dict] = []
    t0 = time.perf_counter()

    def read() -> None:
        for line in proc.stdout:
            with contextlib.suppress(ValueError):
                msgs.append(json.loads(line))

    threading.Thread(target=read, daemon=True).start()
    series = []
    while proc.poll() is None:
        prog = next((m for m in reversed(msgs) if m.get("type") == "progress"), {})
        priv, ws = private_mb(proc.pid)
        series.append({"s": round(time.perf_counter() - t0, 1), "processed": prog.get("processed"),
                       "seen": prog.get("seen"), "private_mb": priv, "working_set_mb": ws})
        time.sleep(5)
    time.sleep(0.3)
    seconds = round(time.perf_counter() - t0, 1)
    peak = round(job.peak_memory() / 2**20)
    job.close()
    done = [m for m in msgs if m.get("type") == "done"]
    return {"exit": proc.returncode, "seconds": seconds, "peak_private_mb": peak,
            "report": done[-1]["report"] if done else {}, "device": done[-1].get("device") if done else None,
            "series": series, "stderr_tail": log.read_text(encoding="utf-8", errors="replace")[-1500:]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dist", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--bench", type=Path, default=ROOT / "data" / "bench", help="where the dev personas' documents are")
    ap.add_argument("--scale", type=int, default=100_000)
    ap.add_argument("--docs", type=int, default=300)
    ap.add_argument("--gpu", choices=("off", "auto"), default="off")
    ap.add_argument("--limit-mb", type=int, default=12_000)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "p8")
    args = ap.parse_args()
    S.BENCH = args.bench
    corpus = args.work / f"corpus-{args.scale}"
    if not (args.work / f"corpus-{args.scale}.complete").exists():
        t = time.perf_counter()
        S._generate(corpus, args.scale, args.docs, 15)
        (args.work / f"corpus-{args.scale}.complete").write_text("", encoding="utf-8")
        print(f"corpus generated in {time.perf_counter() - t:.0f} s", flush=True)
    files = sum(1 for p in corpus.rglob("*") if p.is_file())
    index = args.work / f"index-{args.scale}-{args.gpu}"
    if index.exists():
        import shutil

        shutil.rmtree(index)
    exe, con = args.dist / "ContextFS.exe", args.dist / "ContextFS-console.exe"
    print(f"indexing {files} files on {args.gpu} ...", flush=True)
    full = run_indexer(exe, index, corpus, args.gpu, args.limit_mb, args.work)
    print(f"full: exit {full['exit']} in {full['seconds']} s, peak {full['peak_private_mb']} MB", flush=True)
    noop = run_indexer(exe, index, corpus, args.gpu, args.limit_mb, args.work)
    rng = random.Random(3)
    qfile = args.work / "queries.txt"
    qfile.write_text("\n".join(" ".join(rng.choice(S.WORDS) for _ in range(rng.randrange(2, 6))) for _ in range(60)),
                     encoding="utf-8")
    out = subprocess.run([str(con), "--find", str(index), "--queries", str(qfile)], capture_output=True, text=True,
                         encoding="utf-8", timeout=1800, cwd=str(args.work))
    ms = [q["ms"] for q in json.loads(out.stdout.strip().splitlines()[-1])["queries"]] if out.returncode == 0 else []
    result = {
        "schema": "p8 memory at scale v1", "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "exe_sha256_16": __import__("hashlib").sha256(exe.read_bytes()).hexdigest()[:16],
        "files": files, "scale": args.scale, "docs": args.docs, "gpu": args.gpu, "limit_mb_for_measurement": args.limit_mb,
        "full": full, "noop": {k: noop[k] for k in ("exit", "seconds", "peak_private_mb", "report")},
        "search_ms": {"n": len(ms), "p50": float(np.percentile(ms, 50)) if ms else None,
                      "p95": float(np.percentile(ms, 95)) if ms else None, "find_exit": out.returncode},
        "index_mb": round(sum(f.stat().st_size for f in index.rglob("*") if f.is_file()) / 2**20),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"memory_{args.scale}_{args.gpu}.json"
    path.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("full",)}, indent=1))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
