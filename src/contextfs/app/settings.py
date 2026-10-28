r"""User settings shared by the GUI and the CLI (ADR-0013, ADR-0005 §6).

Stored as JSON in ``%LOCALAPPDATA%\ContextFS\settings.json`` (or ``CONTEXTFS_HOME``). The first
run writes it once; Settings can change roots, exclusions, index location and GPU use at any
time. The index location is refused inside a scanned, non-excluded root, on a network share, or
in a OneDrive-synced folder (ADR-0005 Rev. 1, ADR-0016).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = ["Settings", "home_dir", "validate_index_dir"]


def home_dir() -> Path:
    """Where settings, logs and (by default) the index live."""
    env = os.environ.get("CONTEXTFS_HOME")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "ContextFS"


@dataclass
class Settings:
    """Everything the user chooses. Persisted as JSON."""

    roots: list[str] = field(default_factory=list)
    excluded_names: list[str] = field(default_factory=list)  # extra names, on top of defaults
    index_dir: str = ""
    gpu: str = "auto"  # auto | off
    first_run_done: bool = False

    @classmethod
    def load(cls) -> Settings:
        """Read settings, or defaults if none exist yet."""
        path = home_dir() / "settings.json"
        if not path.exists():
            return cls(index_dir=str(home_dir() / "index"))
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        s = cls(**known)
        s.index_dir = s.index_dir or str(home_dir() / "index")
        return s

    def save(self) -> None:
        """Write settings atomically."""
        home_dir().mkdir(parents=True, exist_ok=True)
        tmp = home_dir() / "settings.json.tmp"
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(tmp, home_dir() / "settings.json")


def validate_index_dir(index_dir: str | Path, roots: list[str]) -> str | None:
    """Return a reason the location is unacceptable, or None if it is fine."""
    p = Path(index_dir).resolve()
    s = str(p)
    if s.startswith("\\\\"):
        return "the index cannot live on a network share"
    onedrive = [os.environ.get(k) for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")]
    if any(od and s.lower().startswith(os.path.abspath(od).lower()) for od in onedrive):
        return "the index cannot live inside a OneDrive folder (it would be uploaded)"
    for root in roots:
        r = str(Path(root).resolve()).lower()
        if s.lower().startswith(r.rstrip("\\/") + os.sep) or s.lower() == r:
            # Allowed only when it sits inside an excluded system location (e.g. AppData on C:\).
            from contextfs.engine.fsio import default_anchored_exclusions

            if not any(s.lower().startswith(str(a).lower()) for a in default_anchored_exclusions()):
                return f"the index cannot live inside a folder being searched ({root})"
    return None
