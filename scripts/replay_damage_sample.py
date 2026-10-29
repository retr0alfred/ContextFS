r"""Simulator-fidelity replay (ADR-0015 §4; log.md Phase 35): do simulated damages match real tools?

    python scripts/replay_damage_sample.py data/bench/*-A-* --work C:\ContextFS-scratch\replay

For a seeded 5% sample (at least 10, or all if fewer) of every damage behaviour in the given dev
personas:
1. regenerate the persona *undamaged* from the same seed (the generator is deterministic) and
   check the sampled file's bytes equal the damaged copy's, which proves the pair;
2. push the undamaged original through the real tool the behaviour models (T v1's own drivers in
   ``measure_transfer_table.py``): ``copyfile`` -> Copy-Item, ``content_copy`` -> shutil.copyfile,
   ``robocopy`` -> robocopy, ``git_clone`` -> git clone;
3. classify, for the real result and for the simulated damage alike, whether each field was kept
   or reset: NTFS birth, NTFS mtime, in-file times, Mark-of-the-Web.
Agreement is reported per behaviour and field. mtime is not compared for files edited after the
migration (the simulator then keeps the later edit, as a real later save would); that count is
reported. Dev personas only; the test split is not read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import measure_transfer_table as T  # noqa: E402
from contextfs.datagen.filetimes import get_file_times, read_infile_times  # noqa: E402

TOOL = {"copyfile": T.tool_copy_item, "content_copy": T.tool_shutil_copyfile,
        "robocopy": T.tool_robocopy_default, "git_clone": T.tool_git_clone}
TOL = 2.0  # seconds
GENERATOR_TZ = timezone(timedelta(hours=5, minutes=30))


def same(a: datetime, b: datetime) -> bool:
    return abs((a - b).total_seconds()) <= TOL


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def regenerate(persona: Path, out_root: Path) -> Path:
    arch, seed_part = persona.name.split("-")[0], persona.name.split("-")[1]
    scale = persona.name.split("-x")[1].split("-")[0]
    name = f"{arch}-{seed_part}-A-x{scale}-none"
    out = out_root / name
    if not (out / "truth" / "persona.json").exists():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_benchmark.py"), "--archetype", arch,
                        "--seed", seed_part[1:], "--family", "A", "--scale", scale, "--damage", "none",
                        "--out", str(out_root), "--force"], check=True, capture_output=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("persona_dirs", type=Path, nargs="+")
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--share", type=float, default=0.05)
    parser.add_argument("--min-n", type=int, default=10)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "transfer_table")
    args = parser.parse_args()
    if any("-B-" in d.name for d in args.persona_dirs):
        print("refusing: the replay uses dev personas only")
        return 2
    pool: dict[str, list[tuple[Path, str, datetime]]] = {}
    for d in sorted(args.persona_dirs):
        mig = datetime.fromisoformat(json.loads((d / "truth" / "damage.json").read_text(encoding="utf-8"))["migration"])
        mig = mig.replace(tzinfo=GENERATOR_TZ)  # the generator writes naive IST wall-clock times
        for r in map(json.loads, (d / "truth" / "truth_manifest.jsonl").read_text(encoding="utf-8").splitlines()):
            if r["damage"] in TOOL:
                pool.setdefault(r["damage"], []).append((d, r["rel_path"], mig))
    rng = random.Random(20261029)
    sample = []
    for behaviour, items in sorted(pool.items()):
        k = min(len(items), max(args.min_n, round(args.share * len(items))))
        sample += [(behaviour, *x) for x in rng.sample(items, k)]
    rows, regenerated = [], {}
    for behaviour, d, rel, mig in sample:
        if d.name not in regenerated:
            regenerated[d.name] = regenerate(d, args.work / "orig")
        orig = regenerated[d.name] / "corpus" / rel
        damaged = d / "corpus" / rel
        if not orig.exists() or sha(orig) != sha(damaged):
            rows.append({"behaviour": behaviour, "file": rel, "error": "pair mismatch"})
            continue
        dst_dir = args.work / "dst" / behaviour / hashlib.md5(f"{d.name}/{rel}".encode()).hexdigest()[:12]
        dst_dir.mkdir(parents=True, exist_ok=True)
        start = datetime.now(timezone.utc)
        time.sleep(1.1)
        try:
            dst = TOOL[behaviour](orig, dst_dir)
        except Exception as exc:  # noqa: BLE001 - recorded, not hidden
            rows.append({"behaviour": behaviour, "file": rel, "error": repr(exc)[:200]})
            continue
        fo, fd, fr = get_file_times(orig), get_file_times(damaged), get_file_times(dst)
        io, idmg, ir = read_infile_times(orig), read_infile_times(damaged), read_infile_times(dst)
        edited_after = fo.modified >= mig  # the plan's last edit came after the migration
        real = {"birth": "kept" if same(fr.created, fo.created) else "reset",
                "mtime": "kept" if same(fr.modified, fo.modified) else "reset",
                "infile": "kept" if ir == io else "changed",
                "motw": T._has_motw(dst)}
        sim = {"birth": "kept" if same(fd.created, fo.created) else "reset",
               "mtime": "kept" if same(fd.modified, fo.modified) else "reset",
               "infile": "kept" if idmg == io else "changed",
               "motw": T._has_motw(damaged)}
        rows.append({"behaviour": behaviour, "file_sha": hashlib.sha256(rel.encode()).hexdigest()[:12],
                     "edited_after_migration": bool(edited_after), "real": real, "sim": sim,
                     "real_started": start.isoformat()})
    summary: dict[str, dict] = {}
    for behaviour in sorted({r["behaviour"] for r in rows}):
        rs = [r for r in rows if r["behaviour"] == behaviour and "real" in r]
        s = {"n": len(rs), "errors": sum(1 for r in rows if r["behaviour"] == behaviour and "error" in r),
             "mtime_not_compared_edited_after_migration": sum(r["edited_after_migration"] for r in rs)}
        for field in ("birth", "mtime", "infile", "motw"):
            cmp = [r for r in rs if not (field == "mtime" and r["edited_after_migration"])]
            s[field] = {"agree": round(sum(r["real"][field] == r["sim"][field] for r in cmp) / len(cmp), 4) if cmp else None,
                        "n": len(cmp),
                        "real": sorted({str(r["real"][field]) for r in cmp}),
                        "sim": sorted({str(r["sim"][field]) for r in cmp})}
        summary[behaviour] = s
    result = {"schema": "simulator-fidelity replay v1 (ADR-0015 §4)", "measured_at": datetime.now(timezone.utc).isoformat(
        timespec="seconds"), "sample_share": args.share, "min_n": args.min_n, "seed": 20261029,
        "personas": sorted({r[1].name for r in sample}), "summary": summary, "rows": rows}
    blob = json.dumps(result, indent=1, sort_keys=True, default=str).encode("utf-8")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "replay_v1.json").write_bytes(blob)
    (args.out / "replay_v1.sha256").write_text(hashlib.sha256(blob).hexdigest() + "  replay_v1.json\n", encoding="utf-8")
    lines = [f"# Simulator-fidelity replay v1 ({result['measured_at']})", "",
             "Agreement between the simulated damage and the real tool, per field "
             "(kept vs reset; MotW present vs absent).", "",
             "| behaviour (tool) | n | birth | mtime | in-file | MotW | errors |", "|---|---|---|---|---|---|---|"]
    names = {"copyfile": "Copy-Item", "content_copy": "shutil.copyfile", "robocopy": "robocopy", "git_clone": "git clone"}
    for b, s in summary.items():
        cells = [f"{s[f]['agree']:.2f} (real {'/'.join(s[f]['real'])}, sim {'/'.join(s[f]['sim'])})"
                 if s[f]["agree"] is not None else "n/a" for f in ("birth", "mtime", "infile", "motw")]
        lines.append(f"| {b} ({names[b]}) | {s['n']} | " + " | ".join(cells) + f" | {s['errors']} |")
    (args.out / "replay_v1.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
