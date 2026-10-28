"""Timestamp damage that follows the frozen transfer table T (ADR-0006 E2, ADR-0015 §4).

Damage is a *simulator*. It applies to the generator's own files exactly the effects that T v1
measured for real tools (`data/transfer_table/T_v1.json`). A 5% real-tool replay (ADR-0015 §4,
`scripts/replay_damage_sample.py`, to be added) checks that the simulation is faithful.

A damage *profile* models one event in the persona's history: "everything was moved to a new
laptop on date M". It applies one T behaviour to every file that existed before M:

| behaviour        | T rows it models                                            | birth | mtime |
|------------------|-------------------------------------------------------------|-------|-------|
| `copyfile`       | Copy-Item, cmd copy, xcopy, Explorer engine, copy2, Expand-Archive, tar | → M   | kept  |
| `robocopy`       | robocopy default, same-volume move                          | kept  | kept  |
| `content_copy`   | shutil.copy, copyfile, zipfile.extractall                   | → M   | → M   |
| `git_clone`      | git clone (code folders only)                               | → M   | → M   |

In-file times survive every behaviour, as T measured. The truth is never changed by damage. Only
the file system changes, and the manifest records which behaviour hit which file.

**Mark-of-the-Web (T v1.1, log.md Decision 106):** `content_copy` and `git_clone` remove the
``Zone.Identifier`` stream, as the measured tools (shutil.copy/copyfile, zip/tar extraction, git
clone) do. `copyfile` and `robocopy` keep it (their dominant tools keep it; Expand-Archive and tar,
which drop it, are a minority of `copyfile`: a stated limitation).
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

from contextfs.benchgen.materialise import MANIFEST_NAME, fs_times_for
from contextfs.benchgen.model import FilePlan
from contextfs.datagen.filetimes import set_file_times

__all__ = ["PROFILES", "apply_damage", "drop_motw", "DROPS_MOTW"]

#: Behaviours whose real tools remove the Mark-of-the-Web stream (T v1.1, measured).
DROPS_MOTW = {"content_copy", "git_clone"}


def drop_motw(path: Path) -> bool:
    """Remove ``path``'s Zone.Identifier stream, as a content-only copy does. True if one existed."""
    import os

    try:
        os.remove(str(path) + ":Zone.Identifier")
        return True
    except FileNotFoundError:
        return False

#: profile name → {behaviour: share of pre-migration files}
PROFILES: dict[str, dict[str, float]] = {
    "none": {},
    "copyfile_all": {"copyfile": 1.0},
    "content_copy_all": {"content_copy": 1.0},
    "robocopy_all": {"robocopy": 1.0},
    "mixed": {"copyfile": 0.5, "content_copy": 0.2, "robocopy": 0.3},
}
CODE_EXT = {".py", ".js", ".ts", ".java", ".c", ".cpp", ".go", ".rs", ".sql", ".sh"}


def apply_damage(
    corpus: Path,
    truth_dir: Path,
    plans: list[FilePlan],
    profile: str,
    migration: datetime,
    seed: int,
) -> dict[str, int]:
    """Apply ``profile`` at ``migration`` time. Returns counts per behaviour."""
    shares = PROFILES[profile]
    rng = random.Random(seed * 104729 + 3)
    counts: dict[str, int] = {}
    for plan in plans:
        created, modified = fs_times_for(plan)
        if created >= migration or not shares:
            plan.damage = "none"
            counts["none"] = counts.get("none", 0) + 1
            continue
        roll, behaviour = rng.random(), "robocopy"
        acc = 0.0
        for name, share in shares.items():
            acc += share
            if roll < acc:
                behaviour = name
                break
        path = corpus / plan.rel_path
        if Path(plan.rel_path).suffix in CODE_EXT and rng.random() < 0.5:
            behaviour = "git_clone"
        at = migration + timedelta(seconds=rng.randint(0, 3600))  # a migration takes a while
        if behaviour == "copyfile":
            set_file_times(path, created=at, modified=modified, accessed=at)
        elif behaviour in {"content_copy", "git_clone"}:
            # The copy resets both times to `at`; an edit made after the migration then moves
            # mtime forward again, so a later planned edit survives.
            set_file_times(path, created=at, modified=max(at, modified), accessed=max(at, modified))
            drop_motw(path)
        plan.damage = behaviour
        counts[behaviour] = counts.get(behaviour, 0) + 1

    with open(truth_dir / MANIFEST_NAME, "w", encoding="utf-8") as fh:
        for plan in plans:
            fh.write(json.dumps(plan.truth_record(), default=str, sort_keys=True) + "\n")
    (truth_dir / "damage.json").write_text(
        json.dumps(
            {"profile": profile, "migration": migration.isoformat(), "counts": counts}, indent=2
        ),
        encoding="utf-8",
    )
    return counts
