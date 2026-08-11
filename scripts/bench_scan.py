"""Measure scanner throughput on the current machine.

Reports the median of N repetitions for three cases: a cold full scan (every
file hashed), a warm incremental scan (nothing changed), and a scan after one
file is modified. The last of these is the Phase 18 incremental-update metric.

Every number this prints was produced by the run that printed it. Nothing here
is estimated.

Usage:
    python scripts/bench_scan.py --repeats 7
"""

from __future__ import annotations

import argparse
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contextfs.config import load_config  # noqa: E402
from contextfs.scanner import Scanner  # noqa: E402
from contextfs.store import Store  # noqa: E402


def summarise(name: str, samples: list[float], extra: str = "") -> None:
    """Print median / min / max for a set of timing samples."""
    print(
        f"  {name:<34} median {statistics.median(samples):7.1f} ms   "
        f"min {min(samples):7.1f}   max {max(samples):7.1f}   n={len(samples)}   {extra}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()

    cfg = load_config(args.config or (PROJECT_ROOT / "contextfs.toml"))
    root = cfg.paths.root
    if not root.is_dir():
        print(f"corpus not found at {root}; run scripts/generate_corpus.py first")
        return 1

    file_count = sum(1 for _ in Scanner(cfg).walk())
    total_bytes = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())

    print(f"machine    : {platform.processor() or platform.machine()}")
    print(f"python     : {platform.python_version()} on {platform.system()} {platform.release()}")
    print(f"corpus     : {file_count} files, {total_bytes / 1024:.0f} KiB, at {root}")
    print(f"repeats    : {args.repeats}\n")

    cold: list[float] = []
    warm: list[float] = []
    incremental: list[float] = []
    hashed_cold = hashed_warm = hashed_inc = 0

    victim = root / "Personal" / "Misc" / "movie_watchlist.txt"
    original = victim.read_bytes() if victim.is_file() else None

    try:
        for iteration in range(args.repeats):
            with tempfile.TemporaryDirectory(prefix="cfs_bench_") as tmp:
                db = Path(tmp) / "contextfs.db"

                with Store(db) as store:
                    scanner = Scanner(cfg)

                    start = time.perf_counter()
                    result = scanner.scan(store)
                    cold.append((time.perf_counter() - start) * 1000)
                    hashed_cold = result.files_hashed

                    start = time.perf_counter()
                    result = scanner.scan(store)
                    warm.append((time.perf_counter() - start) * 1000)
                    hashed_warm = result.files_hashed

                    if original is not None:
                        victim.write_bytes(original + f"\n# bench {iteration}\n".encode())
                        start = time.perf_counter()
                        result = scanner.scan(store)
                        incremental.append((time.perf_counter() - start) * 1000)
                        hashed_inc = result.files_hashed
                        victim.write_bytes(original)
    finally:
        if original is not None:
            victim.write_bytes(original)

    print("scan timings")
    summarise("cold (all files hashed)", cold, f"hashed {hashed_cold}/{file_count}")
    summarise("warm (no changes)", warm, f"hashed {hashed_warm}/{file_count}")
    if incremental:
        summarise("incremental (1 file changed)", incremental, f"hashed {hashed_inc}/{file_count}")

    cold_median = statistics.median(cold)
    print(
        f"\nthroughput (cold): {file_count / (cold_median / 1000):.0f} files/s, "
        f"{total_bytes / 1024 / (cold_median / 1000) / 1024:.1f} MiB/s"
    )
    if incremental:
        print(
            f"incremental speedup vs cold: " f"{cold_median / statistics.median(incremental):.1f}x"
        )
    print(
        "\nNOTE: this corpus is small (KiB, not GiB) and fits entirely in the OS page\n"
        "cache, so these numbers measure per-file overhead, not disk throughput.\n"
        "They must be re-measured on a realistically sized corpus before any\n"
        "scaling claim is made."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
