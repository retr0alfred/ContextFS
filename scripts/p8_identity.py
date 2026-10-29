r"""Phase 8 zero-tolerance check: is the app's index and its top-10 bit-identical to the Phase 7 build?

    # reference: the frozen Phase 7 code, indexing exactly as p7_run did (Engine, CPU, 4 threads)
    python scripts/p8_identity.py build --code-root C:\ContextFS-scratch\p7stage --mode p7 --bench DIR --work DIR --out ref.json
    # candidate: new code, through the app's own path (default Settings -> child_args -> indexer_child)
    python scripts/p8_identity.py build --code-root DIR --mode app --bench DIR --work DIR --out new.json
    # the packaged build: ContextFS.exe --indexer --gpu off, then ContextFS-console.exe --find
    python scripts/p8_identity.py build --code-root DIR --mode exe --dist DIST --bench DIR --work DIR --out exe.json
    python scripts/p8_identity.py compare ref.json new.json

Per dev persona it records a hash of the *logical* index (files, chunks, every file_time row,
sessions, and the raw bytes of every document vector, keyed by path) and, for every query, the
top-10 paths and scores under the app's search configuration (default SearchConfig, top_k 25, as of
the persona's end in the generator's zone). ``compare`` passes only if every hash and every top-10
(paths and scores, exactly) is equal. Dev personas only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

GENERATOR_TZ = timezone(timedelta(hours=5, minutes=30))


def logical(index: Path) -> dict[str, str]:
    import numpy as np

    from contextfs.engine.vectors import VectorFiles

    c = sqlite3.connect(f"file:{(index / 'contextfs.db').as_posix()}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row  # VectorFiles.load reads rows by name
    path = {r[0]: r[1] for r in c.execute("SELECT file_id, rel_path FROM files")}
    parts = {
        "files": sorted(tuple(r) for r in c.execute("SELECT rel_path, size, content_hash, status FROM files")),
        "chunks": sorted((path[r[0]], r[1], r[2]) for r in c.execute("SELECT file_id, ord, text FROM chunks")),
        "file_time": sorted((path[r[0]], *r[1:]) for r in c.execute(
            "SELECT file_id, estimator, event, lo_utc, hi_utc, point_utc, confidence, method FROM file_time")),
    }
    sessions: dict = {}
    for r in c.execute("SELECT file_id, session_id FROM sessions"):
        sessions.setdefault(r[1], []).append(path[r[0]])
    parts["sessions"] = sorted(sorted(s) for s in sessions.values())
    mat, _, fids = VectorFiles(index / "vectors").load(c, "doc")
    c.close()
    out = {k: hashlib.sha256(json.dumps(v, default=str).encode()).hexdigest() for k, v in parts.items()}
    h = hashlib.sha256()
    for p, row in sorted(zip((path[int(f)] for f in fids), mat, strict=True)):
        h.update(p.encode() + b"\0" + np.ascontiguousarray(row, dtype=np.float32).tobytes())
    out["vectors"] = h.hexdigest()
    out["n_vectors"] = str(len(fids))
    out["logical"] = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()
    return out


def build_p7(index: Path, corpus: Path) -> None:
    from contextfs.engine import Engine

    with Engine.open(index, mode="write", device="cpu", threads=4) as eng:  # as scripts/p7_run.py stage 1
        eng.update([corpus])


def build_app(index: Path, corpus: Path, home: Path) -> str:
    """Exactly the app's path: default Settings -> the GUI's child arguments -> the indexer child."""
    os.environ["CONTEXTFS_HOME"] = str(home)
    from contextfs.app import indexer_child
    from contextfs.app.gui import child_args
    from contextfs.app.settings import Settings

    s = Settings.load()  # a fresh home: defaults
    s.roots = [str(corpus)]
    args = child_args(s, index)
    code = indexer_child.main(args[1:])  # drop "--indexer", as __main__ does
    if code != 0:
        raise SystemExit(f"indexer child exit {code} on {corpus}")
    return " ".join(args[:3])


def build_exe(dist: Path, index: Path, corpus: Path, queries: list[dict], as_of: datetime) -> dict:
    """Index and query through the packaged build.

    ``ContextFS.exe --indexer --gpu off`` is what the GUI sends by default; queries go through
    ``ContextFS-console.exe --find``, which reports scores rounded to 6 places.
    """
    import subprocess

    rc = subprocess.run([str(dist / "ContextFS.exe"), "--indexer", "--gpu", "off", str(index), str(corpus)],
                        capture_output=True, timeout=3600).returncode
    if rc != 0:
        raise SystemExit(f"packaged indexer exit {rc} on {corpus}")
    qfile = index.parent / f"{index.name}.queries.txt"
    qfile.write_text("\n".join(q["text"] for q in queries), encoding="utf-8")
    out = subprocess.run([str(dist / "ContextFS-console.exe"), "--find", str(index), "--as-of", as_of.isoformat(),
                          "--queries", str(qfile)], capture_output=True, timeout=3600)
    got = json.loads(out.stdout.decode("utf-8").strip().splitlines()[-1])["queries"]
    assert [g["query"] for g in got] == [q["text"] for q in queries]
    return {q["query_id"]: [[r["rel_path"], repr(r["score"])] for r in g["results"][:10]]
            for q, g in zip(queries, got, strict=True)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--code-root", type=Path, required=True)
    b.add_argument("--mode", choices=("p7", "app", "exe"), required=True)
    b.add_argument("--dist", type=Path, help="mode exe: the packaged build's folder")
    b.add_argument("--bench", type=Path, required=True)
    b.add_argument("--work", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("compare")
    c.add_argument("a", type=Path)
    c.add_argument("b", type=Path)
    args = ap.parse_args()
    if args.cmd == "compare":
        return compare(args.a, args.b)

    sys.path.insert(0, str(args.code_root / "src"))
    import contextfs

    if not Path(contextfs.__file__).resolve().is_relative_to(args.code_root.resolve()):
        raise SystemExit(f"refusing: contextfs imported from {contextfs.__file__}, not {args.code_root}")
    from contextfs.engine import Engine
    from contextfs.engine.rank import SearchConfig

    personas = sorted(d for d in args.bench.iterdir() if d.is_dir() and "-A-" in d.name)
    result = {"mode": args.mode, "code_root": str(args.code_root), "contextfs": contextfs.__file__,
              "personas": {}, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    t0 = time.perf_counter()
    for d in personas:
        index = args.work / args.mode / d.name
        if index.exists():
            import shutil

            shutil.rmtree(index)
        child = None
        as_of = datetime.fromisoformat(json.loads((d / "truth" / "persona.json").read_text(encoding="utf-8"))
                                       ["persona"]["end"]).replace(tzinfo=GENERATOR_TZ)
        queries = [json.loads(x) for x in (d / "truth" / "queries_llm.jsonl").read_text(encoding="utf-8").splitlines()]
        if args.mode == "exe":
            child = "ContextFS.exe --indexer --gpu off"
            tops = build_exe(args.dist, index, d / "corpus", queries, as_of)
        else:
            if args.mode == "p7":
                build_p7(index, d / "corpus")
            else:
                child = build_app(index, d / "corpus", args.work / "home")
            tops = {}
            with Engine.open(index) as eng:
                for q in queries:
                    resp = eng.search(q["text"], SearchConfig(top_k=25), as_of=as_of)  # the GUI's SearchJob config
                    tops[q["query_id"]] = [[r.rel_path, repr(r.score)] for r in resp.results[:10]]
        result["personas"][d.name] = {"index": logical(index), "child_args": child, "top10": tops}
        print(f"{d.name}: {result['personas'][d.name]['index']['logical'][:16]} ({len(tops)} queries)", flush=True)
    result["seconds"] = round(time.perf_counter() - t0, 1)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return 0


def compare(a_path: Path, b_path: Path) -> int:
    a, b = (json.loads(p.read_text(encoding="utf-8")) for p in (a_path, b_path))
    rounded = "exe" in (a["mode"], b["mode"])  # the console reports scores to 6 places

    def norm(top: list) -> list:
        return [[p, repr(round(float(s), 6))] for p, s in top] if rounded else top

    bad, n_q = [], 0
    for name in sorted(set(a["personas"]) | set(b["personas"])):
        pa, pb = a["personas"].get(name), b["personas"].get(name)
        if pa is None or pb is None:
            bad.append(f"{name}: missing on one side")
            continue
        for k in pa["index"]:
            if pa["index"][k] != pb["index"].get(k):
                bad.append(f"{name}: index part '{k}' differs")
        for qid, top in pa["top10"].items():
            n_q += 1
            if pb["top10"].get(qid) is None or norm(pb["top10"][qid]) != norm(top):
                bad.append(f"{name}: top-10 differs for {qid}")
    print(f"personas {len(a['personas'])} vs {len(b['personas'])}; queries compared {n_q}; differences {len(bad)}")
    for x in bad[:20]:
        print("  " + x)
    print("IDENTICAL" if not bad else "NOT IDENTICAL")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
