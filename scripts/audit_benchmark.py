"""Audit a generated persona against its sealed truth (skeptic N1; ADR-0014 test 1).

For a zero-damage persona (`--damage none`) every check must pass:

1. **FS times equal the plan:** created = arrived_here; modified = the planned modified time
   (last edit / download time / photo capture). Tolerance 2 s.
2. **The copy signature (birth > mtime) fires only where the simulation copied a file** (photos
   imported from a phone, origin "received") and on 0% of the other files.
3. **In-file times equal the truth:** Office created/modified, PDF created/modified, EXIF original,
   zip entry. Tolerance 2 s.
4. **Mark-of-the-Web** appears exactly on downloads.
5. **Every labelled date mention appears in the extracted text** (checked on text-bearing formats).

For damaged personas, check 1 is replaced by: the FS times match the damage behaviour recorded in
the manifest.

Exit code 0 only if everything passes.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contextfs.datagen.filetimes import get_file_times, read_infile_times  # noqa: E402

TOL = timedelta(seconds=2)


def _local(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _aware(local_naive: datetime) -> datetime:
    return local_naive.astimezone().astimezone(timezone.utc)


def _parse_infile(value: str) -> datetime:
    v = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(v)
    return dt.astimezone(timezone.utc) if dt.tzinfo else _aware(dt)  # floating → local


def audit(persona_dir: Path) -> int:
    corpus, truth = persona_dir / "corpus", persona_dir / "truth"
    records = [
        json.loads(line)
        for line in (truth / "truth_manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    fails: dict[str, list[str]] = {
        k: [] for k in ("fs", "copy_signature", "infile", "motw", "dates")
    }
    damage_meta = (
        json.loads((truth / "damage.json").read_text(encoding="utf-8"))
        if (truth / "damage.json").exists()
        else {}
    )
    migration = _aware(_local(damage_meta["migration"])) if "migration" in damage_meta else None
    window = timedelta(hours=1, seconds=2)  # damage.apply spreads a migration over an hour
    sig_fired = sig_expected = 0

    from contextfs.extract import extract_file

    for r in records:
        path = corpus / r["rel_path"]
        t = r["truth"]
        authored, arrived = _local(t["authored"]), _local(t["arrived_here"])
        edit = _local(t["last_user_edit"]) if t["last_user_edit"] else None
        fs = get_file_times(path)

        # 1. FS times vs plan (or vs the recorded damage)
        if r["origin"] == "received":
            want_c, want_m = arrived, authored
        elif r["origin"] == "downloaded":
            want_c, want_m = arrived, arrived
        else:
            want_c, want_m = arrived, edit or arrived
        dmg = r.get("damage", "none")
        if dmg == "none" or dmg == "robocopy":
            if abs(fs.created - _aware(want_c)) > TOL or abs(fs.modified - _aware(want_m)) > TOL:
                fails["fs"].append(
                    f"{r['rel_path']}: created {fs.created} vs {_aware(want_c)}; "
                    f"modified {fs.modified} vs {_aware(want_m)}"
                )
        elif dmg == "copyfile":  # T: birth -> copy time, mtime kept (or moved by a later edit)
            in_window = migration <= fs.created <= migration + window
            if not in_window or abs(fs.modified - _aware(want_m)) > TOL:
                fails["fs"].append(f"{r['rel_path']}: copyfile damage not as T says")
        elif dmg in {"content_copy", "git_clone"}:  # T: both -> copy time, unless edited later
            in_window = migration <= fs.created <= migration + window
            want_mod = max(fs.created, _aware(want_m))
            if not in_window or abs(fs.modified - want_mod) > TOL:
                fails["fs"].append(f"{r['rel_path']}: {dmg} damage not as T says")

        # 2. copy signature
        fired = fs.created > fs.modified + TOL
        if dmg in {"none", "robocopy"}:
            expected = r["origin"] == "received"
        elif dmg == "copyfile":
            expected = fs.created > _aware(want_m) + TOL  # unless edited after the copy
        else:  # content_copy / git_clone: both times reset together
            expected = False
        sig_fired += fired
        sig_expected += expected
        if fired != expected:
            fails["copy_signature"].append(f"{r['rel_path']}: fired={fired} expected={expected}")

        # 3. in-file times
        infile = read_infile_times(path)
        if r["origin"] == "authored":
            in_c, in_m = authored, edit or authored
        else:
            in_c, in_m = authored, authored
        checks = {
            "docx": {"ooxml:created": in_c, "ooxml:modified": in_m},
            "pptx": {"ooxml:created": in_c, "ooxml:modified": in_m},
            "xlsx": {"ooxml:created": in_c, "ooxml:modified": in_m},
            "pdf": {"pdf:created": in_c, "pdf:modified": in_m},
            "jpg": {"exif:original": authored},
            "zip": {"zip:max_entry": authored},
        }.get(r["kind"], {})
        for key, want in checks.items():
            got = infile.get(key)
            if got is None or abs(_parse_infile(got) - _aware(want)) > TOL:
                fails["infile"].append(f"{r['rel_path']}: {key}={got} want {want}")

        # 4. Mark-of-the-Web
        try:
            with open(str(path) + ":Zone.Identifier", encoding="utf-8"):
                has_motw = True
        except OSError:
            has_motw = False
        if has_motw != (r["origin"] == "downloaded"):
            fails["motw"].append(f"{r['rel_path']}: motw={has_motw}")

        # 5. labelled dates present in extracted text
        if r["dates"] and r["kind"] not in {"jpg", "zip"}:
            doc = extract_file(path)
            text = doc.text if doc and doc.ok else ""
            for m in r["dates"]:
                if m["surface"] not in text:
                    fails["dates"].append(f"{r['rel_path']}: missing '{m['surface']}'")

    n = len(records)
    print(f"persona: {persona_dir.name}  files: {n}")
    print(f"copy signature fired on {sig_fired}/{n} files; expected on {sig_expected}")
    ok = True
    for name, items in fails.items():
        status = "PASS" if not items else f"FAIL ({len(items)})"
        print(f"  {name:<15} {status}")
        for line in items[:5]:
            print(f"      {line}")
        ok &= not items
    print("RESULT:", "ALL PASS" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("persona_dir", type=Path)
    return audit(parser.parse_args().persona_dir)


if __name__ == "__main__":
    raise SystemExit(main())
