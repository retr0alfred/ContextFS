r"""Transfer table T v1.1 addendum: does an existing Mark-of-the-Web survive each tool? (ADR-0006 E1).

T v1 (`measure_transfer_table.py`) recorded whether a ``Zone.Identifier`` stream *appeared* on
outputs of sources that had none, so it never measured *survival*. The benchmark's damage
simulator keeps the mark through every behaviour, and an estimator may use it as download evidence,
so survival must be measured, and frozen with a hash, before any rule relies on it (log.md Phase 34).

Every T v1 tool runs on the same fixtures as T v1, each given a browser-style mark first
(``[ZoneTransfer] ZoneId=3``). The output says, per tool and format, whether the destination still
carries the mark.

    python scripts/measure_motw_survival.py --work C:\ContextFS-scratch\motw [--out data/transfer_table]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
import measure_transfer_table as T  # noqa: E402

MARK = "[ZoneTransfer]\r\nZoneId=3\r\nReferrerUrl=https://example.org/\r\nHostUrl=https://example.org/f\r\n"


def add_mark(path: Path) -> None:
    with open(str(path) + ":Zone.Identifier", "w", encoding="utf-8", newline="") as fh:
        fh.write(MARK)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "transfer_table")
    args = parser.parse_args()
    if args.work.exists():
        shutil.rmtree(args.work, ignore_errors=True)
    fixtures = T.make_fixtures(args.work / "src")
    for p in fixtures.values():
        add_mark(p)
        assert T._has_motw(p), p
    rows = []
    for tool_name, tool in T.TOOLS.items():
        for fmt, src in fixtures.items():
            dst_dir = args.work / "dst" / tool_name / fmt
            dst_dir.mkdir(parents=True)
            try:
                dst = tool(src, dst_dir)
            except Exception as exc:  # noqa: BLE001 - record and continue, as T v1 does
                rows.append({"tool": tool_name, "format": fmt, "error": repr(exc)[:200]})
                continue
            if dst is None:
                continue
            rows.append({"tool": tool_name, "format": fmt, "motw_survives": T._has_motw(dst)})
            if not T._has_motw(src):  # a tool that moved the source away: restore for the next one
                add_mark(src)
    result = {
        "schema": "T v1.1 addendum: Mark-of-the-Web survival per tool and format",
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "machine": f"{platform.platform()}, NTFS ({args.work.drive})",
        "source_mark": "Zone.Identifier ZoneId=3 on every fixture",
        "rows": rows,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(result, indent=1, sort_keys=True).encode("utf-8")
    (args.out / "T_v1_1_motw.json").write_bytes(blob)
    (args.out / "T_v1_1_motw.sha256").write_text(hashlib.sha256(blob).hexdigest() + "  T_v1_1_motw.json\n",
                                                 encoding="utf-8")
    by_tool: dict[str, list] = {}
    for r in rows:
        by_tool.setdefault(r["tool"], []).append(r)
    lines = [f"# T v1.1 addendum — Mark-of-the-Web survival (measured {result['measured_at']})", "",
             f"Machine: {result['machine']}. Each fixture carried `Zone.Identifier` (ZoneId=3) before the tool ran.",
             "", "| tool | MotW survives | formats |", "|---|---|---|"]
    for tool, rs in by_tool.items():
        ok = [r for r in rs if "motw_survives" in r]
        kept = sum(r["motw_survives"] for r in ok)
        verdict = "yes" if ok and kept == len(ok) else "no" if kept == 0 else f"partly ({kept}/{len(ok)})"
        errs = [r["format"] for r in rs if "error" in r]
        lines.append(f"| {tool} | {verdict} | {', '.join(r['format'] for r in ok)}"
                     + (f" (errors: {', '.join(errs)})" if errs else "") + " |")
    (args.out / "T_v1_1_motw.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
