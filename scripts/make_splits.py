"""Generate the dev and test splits, and freeze the test split (ADR-0015 §2).

Split v1: 20 dev personas (seeds 1–20, template family A) and 20 test personas (seeds 1001–1020,
family B). Archetypes and damage profiles rotate deterministically, so both splits have the same
mix:

    archetype = [student, professional, researcher][seed % 3]
    damage    = [none, mixed, copyfile_all, content_copy_all][seed % 4]

``generate`` builds the personas. ``freeze`` writes ``data/splits/split_v1.json`` with sha256 hashes
of every persona's truth manifest and query files. After that commit the test split is frozen:
``scripts/bench_eval.py`` refuses test personas unless the run is tagged ``TEST-*``, and any
change to a test persona changes its hash.

Usage:
    python scripts/make_splits.py generate --split dev
    python scripts/make_splits.py generate --split test
    python scripts/make_splits.py freeze
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCH = PROJECT_ROOT / "data" / "bench"
SPLITS = PROJECT_ROOT / "data" / "splits"
ARCHETYPES = ["student", "professional", "researcher"]
DAMAGE = ["none", "mixed", "copyfile_all", "content_copy_all"]
#: Professionals and researchers have fewer, longer activities; scale them up so every persona
#: yields enough distinct query targets (see log.md Phase 31, entry 2).
SCALE = {"student": 1.0, "professional": 2.5, "researcher": 2.5}
SPEC = {"dev": (range(1, 21), "A"), "test": (range(1001, 1021), "B")}


def personas(split: str) -> list[tuple[str, int, str, str]]:
    seeds, family = SPEC[split]
    return [(ARCHETYPES[s % 3], s, family, DAMAGE[s % 4]) for s in seeds]


def dir_for(archetype: str, seed: int, family: str, damage: str) -> Path:
    return BENCH / f"{archetype}-s{seed}-{family}-x{SCALE[archetype]:g}-{damage}"


def generate(split: str) -> int:
    for archetype, seed, family, damage in personas(split):
        out = dir_for(archetype, seed, family, damage)
        if out.exists():
            continue
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/generate_benchmark.py",
                "--archetype",
                archetype,
                "--seed",
                str(seed),
                "--family",
                family,
                "--damage",
                damage,
                "--scale",
                str(SCALE[archetype]),
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print(f"FAILED {out.name}\n{proc.stderr[-1500:]}")
            return 1
        # Report counts only; never print a test persona's queries or contents.
        print(f"generated {out.name}")
    return 0


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def freeze() -> int:
    record = {
        "split_version": "v1",
        "frozen_at": datetime.now().isoformat(timespec="seconds"),
        "spec": {
            k: {"seeds": [v[0].start, v[0].stop - 1], "family": v[1]} for k, v in SPEC.items()
        },
        "personas": {},
    }
    for split in SPEC:
        for p in personas(split):
            d = dir_for(*p)
            truth = d / "truth"
            entry = {
                "split": split,
                "truth_manifest_sha256": _sha(truth / "truth_manifest.jsonl"),
                "queries_template_sha256": _sha(truth / "queries.jsonl"),
                "queries_llm_sha256": _sha(truth / "queries_llm.jsonl"),
            }
            if None in (entry["truth_manifest_sha256"], entry["queries_llm_sha256"]):
                print(f"cannot freeze: {d.name} is missing its manifest or LLM queries")
                return 1
            record["personas"][d.name] = entry
    SPLITS.mkdir(parents=True, exist_ok=True)
    text = json.dumps(record, indent=2, sort_keys=True) + "\n"
    (SPLITS / "split_v1.json").write_text(text, encoding="utf-8")
    print(
        f"froze {len(record['personas'])} personas; split_v1.json sha256 "
        f"{hashlib.sha256(text.encode()).hexdigest()}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--split", choices=list(SPEC), required=True)
    sub.add_parser("freeze")
    args = parser.parse_args()
    return generate(args.split) if args.cmd == "generate" else freeze()


if __name__ == "__main__":
    raise SystemExit(main())
