"""Generate one benchmark persona: corpus + sealed truth (ADR-0015).

Usage:
    python scripts/generate_benchmark.py --archetype student --seed 1 --family A
    python scripts/generate_benchmark.py --archetype researcher --seed 7 --scale 3 --damage mixed

Output: ``data/bench/<persona_id>/corpus`` (scan this) and ``data/bench/<persona_id>/truth``
(sealed: only the scorer and the audits read it, never the engine).
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contextfs.benchgen.content import TemplateProvider  # noqa: E402
from contextfs.benchgen.damage import PROFILES, apply_damage  # noqa: E402
from contextfs.benchgen.materialise import materialise  # noqa: E402
from contextfs.benchgen.model import Persona  # noqa: E402
from contextfs.benchgen.simulate import (
    ARCHETYPES,
    CITIES,
    FIRST_NAMES,
    LAST_NAMES,
    simulate,
)  # noqa: E402

#: The persona's "now". Fixed, so generation never depends on the wall clock.
OBSERVED_AT = datetime(2026, 9, 30, 21, 0, 0)
GENERATOR_VERSION = "benchgen-0.1"


def make_persona(archetype: str, seed: int, family: str, years: float = 2.0) -> Persona:
    rng = random.Random(seed)
    org = {
        "student": "College of Engineering",
        "professional": "Consultancy",
        "researcher": "IR Lab",
    }[archetype]
    return Persona(
        persona_id=f"{archetype}-s{seed}-{family}",
        archetype=archetype,
        name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
        seed=seed,
        family=family,
        start=OBSERVED_AT - timedelta(days=int(365 * years)),
        end=OBSERVED_AT,
        home_city=rng.choice(CITIES),
        org=org,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archetype", choices=ARCHETYPES, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--family", choices=["A", "B"], required=True, help="A = dev, B = test")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--damage", choices=sorted(PROFILES), default="none")
    parser.add_argument("--migration-days-before-end", type=int, default=120)
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "data" / "bench")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    persona = make_persona(args.archetype, args.seed, args.family)
    suffix = f"-x{args.scale:g}-{args.damage}"
    out_dir = args.out / (persona.persona_id + suffix)
    if out_dir.exists():
        if not args.force:
            print(f"{out_dir} exists (use --force)")
            return 1
        shutil.rmtree(out_dir)

    activities, plans = simulate(persona, args.scale)
    corpus = materialise(persona, plans, TemplateProvider(persona.family), out_dir)
    migration = persona.end - timedelta(days=args.migration_days_before_end)
    counts = apply_damage(corpus, out_dir / "truth", plans, args.damage, migration, persona.seed)

    meta = {
        "generator": GENERATOR_VERSION,
        "content_provider": TemplateProvider.name,
        "persona": {k: str(v) for k, v in vars(persona).items()},
        "scale": args.scale,
        "damage_profile": args.damage,
        "migration": migration.isoformat(),
        "files": len(plans),
        "activities": len(activities),
        "by_kind": dict(Counter(p.kind for p in plans)),
        "by_origin": dict(Counter(p.origin for p in plans)),
        "damage_counts": counts,
    }
    (out_dir / "truth" / "persona.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (out_dir / "truth" / "activities.jsonl").write_text(
        "".join(json.dumps(vars(a), default=str) + "\n" for a in activities), encoding="utf-8"
    )
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
