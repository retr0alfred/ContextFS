r"""Hardening suite for the packaged app (v2-P5, log.md Phase 33). Runs against ``dist/ContextFS``.

    .venv\Scripts\python.exe scripts\hardening\run_suite.py [--only C01,C07] [--scale 30000] [--tag NAME]

Every case drives the *frozen* executables as a black box: indexing through the windowed
``ContextFS.exe --indexer`` (the real child), search and inspection through
``ContextFS-console.exe --find/--status``, and the GUI through ``CONTEXTFS_SELFDRIVE``. The suite
reads the SQLite index directly only to check invariants (integrity, per-file access, logical
equality). Work happens under ``.tools/exp/harden`` (D:). Results go to
``data/hardening/<tag>/report.json`` and ``report.md``.

The pass/fail bars come from the ADRs' "how we will know it was wrong" sections (ADR-0001,
0011, 0012, 0013, 0016); each case names its source.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import zipfile
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from contextfs.app.winjob import ChildJob  # noqa: E402

DIST = ROOT / "dist" / "ContextFS"
GUI_EXE, CON_EXE = DIST / "ContextFS.exe", DIST / "ContextFS-console.exe"
WORK = ROOT / ".tools" / "exp" / "harden"
BENCH = ROOT / "data" / "bench"
K32 = ctypes.WinDLL("kernel32", use_last_error=True)
K32.CreateFileW.restype = wintypes.HANDLE
INVALID = wintypes.HANDLE(-1).value

# --------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------


def lp(p: Path) -> str:
    """Long-path form for creating deep fixtures."""
    s = str(Path(p).absolute())
    return s if s.startswith("\\\\?\\") else "\\\\?\\" + s


def write(p: Path, data, mode="w") -> Path:
    os.makedirs(lp(p.parent), exist_ok=True)
    with open(lp(p), mode + ("b" if isinstance(data, bytes) else ""), **({} if isinstance(data, bytes) else {"encoding": "utf-8"})) as fh:
        fh.write(data)
    return p


TRASH = WORK / "_trash"


def fresh(name: str) -> Path:
    """An empty case folder.

    The old one is renamed into _trash (instant; deleting thousands of files case by case cost
    minutes on the project's USB hard disk) and emptied at the end of the run.
    """
    d = WORK / name
    if d.exists():
        subprocess.run(["icacls", str(d), "/reset", "/T", "/C", "/Q"], capture_output=True)  # undo deny ACEs
        TRASH.mkdir(parents=True, exist_ok=True)
        d.rename(TRASH / f"{name}-{time.time_ns()}")
    d.mkdir(parents=True)
    return d


def minimal_pdf(text: str) -> bytes:
    sys.path.insert(0, str(ROOT / "src"))
    from contextfs.app.selftest import _minimal_pdf

    return _minimal_pdf(text)


def make_docx(p: Path, text: str) -> None:
    import docx

    d = docx.Document()
    d.add_paragraph(text)
    os.makedirs(lp(p.parent), exist_ok=True)
    d.save(lp(p))


WORDS = ["normalization", "relational", "schema", "keys", "lecture", "notes", "exam", "semester", "budget", "invoice", "rent", "travel", "ticket", "hotel", "recipe", "saffron", "cricket", "league", "scores", "thesis", "draft", "chapter", "results", "figures", "python", "script", "dataset", "training", "model", "clustering", "kmeans", "regression", "report", "meeting", "minutes", "agenda", "project", "proposal", "deadline", "assignment", "lab", "quiz", "marks", "syllabus", "calendar", "internship", "offer", "letter", "photo", "trip", "hampi", "mysuru", "festival", "diwali", "pongal", "onam", "grades", "transcript", "certificate"]


def sentence(rng: random.Random, n: int = 30) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(n)) + "."


def snapshot(root: Path) -> dict:
    """Content hash, size, mtime and attributes of every file: the read-only guarantee."""
    out = {}
    for dirpath, dirnames, files in os.walk(lp(root)):
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))
                       and not _is_junction(os.path.join(dirpath, d))]
        for f in files:
            full = os.path.join(dirpath, f)
            st = os.stat(full)
            try:
                with open(full, "rb") as fh:
                    digest = hashlib.sha256(fh.read()).hexdigest()
            except OSError as exc:
                digest = f"unreadable:{exc.errno}"
            out[os.path.relpath(full, lp(root))] = (digest, st.st_size, st.st_mtime_ns, st.st_file_attributes)
    return out


def _is_junction(path: str) -> bool:
    try:
        return bool(os.lstat(path).st_file_attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except OSError:
        return False


class Child:
    """The packaged indexer child, in a Job Object (peak memory), with JSON-line capture."""

    def __init__(self, index: Path, roots: list[Path], gpu="off", env: dict | None = None) -> None:
        self.index = index
        self.log = index.parent / f"{index.name}.child.log"
        e = dict(os.environ, **(env or {}))
        e.pop("PYTHONPATH", None)
        self.proc = subprocess.Popen(
            [str(GUI_EXE), "--indexer", "--gpu", gpu, str(index), *map(str, roots)],
            stdout=subprocess.PIPE, stderr=open(self.log, "wb"), env=e, cwd=str(WORK))  # noqa: SIM115
        self.job = ChildJob()
        self.job.assign(self.proc.pid)
        self.msgs: list[dict] = []
        self.t0 = time.perf_counter()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            with contextlib.suppress(ValueError):
                self.msgs.append(json.loads(line))

    def wait(self, timeout=1800) -> int:
        try:
            code = self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            code = -999
        time.sleep(0.2)
        self.seconds = round(time.perf_counter() - self.t0, 1)
        self.peak_mb = round(self.job.peak_memory() / 2**20)
        self.job.close()
        return code

    def kill(self) -> None:
        self.proc.kill()
        self.proc.wait()

    def report(self) -> dict:
        done = [m for m in self.msgs if m.get("type") == "done"]
        return done[-1]["report"] if done else {}

    def kinds(self) -> list[str]:
        return [m.get("kind") or m.get("type") for m in self.msgs]


def run_child(index, roots, **kw) -> tuple[int, Child]:
    c = Child(index, roots, **kw)
    return c.wait(), c


def console(*args, timeout=600) -> dict:
    out = subprocess.run([str(CON_EXE), *map(str, args)], capture_output=True, text=True, encoding="utf-8",
                         timeout=timeout, cwd=str(WORK))
    if out.returncode != 0:
        raise RuntimeError(f"console {args[0]} exit {out.returncode}: {out.stderr[-500:]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def find(index, *queries) -> list[dict]:
    return console("--find", index, *queries)["queries"]


def db(index: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{(index / 'contextfs.db').as_posix()}?mode=ro", uri=True)


def access(index, rel) -> str | None:
    with db(index) as c:
        r = c.execute("SELECT access FROM files WHERE rel_path=?", (rel,)).fetchone()
    return r[0] if r else None


def lock_exclusive(p: Path):
    """Open with no sharing at all: any other open of this file fails while held."""
    h = K32.CreateFileW(lp(p), 0x80000000, 0, None, 3, 0x80, None)
    if h == INVALID:
        raise ctypes.WinError(ctypes.get_last_error())
    return h


def unlock(h) -> None:
    K32.CloseHandle(wintypes.HANDLE(h))


def set_offline(p: Path) -> None:
    attrs = K32.GetFileAttributesW(lp(p))
    if not K32.SetFileAttributesW(lp(p), attrs | 0x1000):  # FILE_ATTRIBUTE_OFFLINE
        raise ctypes.WinError(ctypes.get_last_error())


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def child_pids(parent: int) -> list[int]:
    """Direct children of ``parent`` (Toolhelp snapshot; WMI took over a minute per call here)."""
    K32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = K32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    out = []
    ok = K32.Process32FirstW(snap, ctypes.byref(entry))
    while ok:
        if entry.th32ParentProcessID == parent:
            out.append(int(entry.th32ProcessID))
        ok = K32.Process32NextW(snap, ctypes.byref(entry))
    K32.CloseHandle(wintypes.HANDLE(snap))
    return out


def pid_alive(pid: int) -> bool:
    h = K32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = wintypes.DWORD()
    K32.GetExitCodeProcess(h, ctypes.byref(code))
    K32.CloseHandle(h)
    return code.value == 259  # STILL_ACTIVE


def logical(index: Path) -> dict:
    """What an index means, independent of row ids and write order (ADR-0014 test 10)."""
    with db(index) as c:
        path = dict(c.execute("SELECT file_id, rel_path FROM files"))
        sessions: dict[str, set] = {}
        for fid, sid in c.execute("SELECT file_id, session_id FROM sessions"):
            sessions.setdefault(sid, set()).add(path[fid])
        vec_rows = c.execute("SELECT v.row, c.file_id, c.ord FROM vectors v JOIN chunks c ON c.chunk_id=v.item_id "
                             "WHERE v.kind='chunk' AND v.valid=1").fetchall()
        mat = np.fromfile(index / "vectors" / "chunk.f32", dtype=np.float32).reshape(-1, 384)
        return {
            "files": sorted(c.execute("SELECT rel_path, size, content_hash, status, access FROM files")),
            "chunks": sorted((path[f], o, t) for f, o, t in c.execute("SELECT file_id, ord, text FROM chunks")),
            "file_time": sorted((path[r[0]], *r[1:]) for r in c.execute(
                "SELECT file_id, estimator, event, lo_utc, hi_utc, point_utc, confidence FROM file_time")),
            "sessions": sorted(sorted(s) for s in sessions.values()),
            "vectors": {(path[f], o): mat[row] for row, f, o in vec_rows},
        }


def compare_logical(a: dict, b: dict) -> list[str]:
    diffs = []
    for k in ("files", "chunks", "file_time", "sessions"):
        if a[k] != b[k]:
            sa, sb = {tuple(map(str, x)) for x in a[k]}, {tuple(map(str, x)) for x in b[k]}
            diffs.append(f"{k}: {len(sa - sb)} only-incremental, {len(sb - sa)} only-clean; "
                         f"e.g. {sorted(sa - sb)[:1]} vs {sorted(sb - sa)[:1]}")
    if a["vectors"].keys() != b["vectors"].keys():
        diffs.append(f"vector keys differ ({len(a['vectors'])} vs {len(b['vectors'])})")
    else:
        worst = min((float(a["vectors"][k] @ b["vectors"][k]) for k in a["vectors"]), default=1.0)
        if worst < 0.9999:
            diffs.append(f"vectors: min cosine {worst:.6f}")
    return diffs


def bench_docs(limit: int) -> list[Path]:
    out = []
    for d in sorted(BENCH.glob("*-A-*")):  # dev personas only
        out += [p for p in (d / "corpus").rglob("*") if p.suffix.lower() in (".pdf", ".docx", ".pptx", ".xlsx")]
    random.Random(5).shuffle(out)
    return out[:limit]


def gen_corpus(root: Path, n: int, docs: int = 0, seed: int = 1) -> Path:
    rng = random.Random(seed)
    for i in range(n):
        folder = root / f"area{i % 40:02d}" / f"sub{(i // 40) % 25:02d}"
        ext = (".txt", ".md", ".csv")[i % 3]
        body = sentence(rng, 25 + rng.randrange(60)) if ext != ".csv" else "a,b\n" + f"{rng.random()},{i}\n"
        write(folder / f"note_{i:06d}_{rng.choice(WORDS)}{ext}", body)
    for j, src in enumerate(bench_docs(docs)):
        dst = root / "docs" / f"{j:04d}_{src.name}"
        os.makedirs(lp(dst.parent), exist_ok=True)
        shutil.copy2(src, lp(dst))
    return root


def selfdrive(home: Path, roots: list[Path], index: Path, queries: list[str], env: dict | None = None,
              max_seconds=900) -> dict:
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text(json.dumps({"roots": [str(r) for r in roots], "excluded_names": [],
                                                    "index_dir": str(index), "gpu": "off",
                                                    "first_run_done": True}), encoding="utf-8")
    cfg = home / "selfdrive.json"
    out = home / "selfdrive_report.json"
    out.unlink(missing_ok=True)
    cfg.write_text(json.dumps({"queries": queries, "out": str(out), "query_every_s": 1.5,
                               "max_seconds": max_seconds}), encoding="utf-8")
    e = dict(os.environ, CONTEXTFS_HOME=str(home), CONTEXTFS_SELFDRIVE=str(cfg), QT_QPA_PLATFORM="offscreen",
             **(env or {}))
    gui = subprocess.Popen([str(GUI_EXE)], env=e, cwd=str(WORK))
    try:
        gui.wait(max_seconds + 120)
    except subprocess.TimeoutExpired:
        gui.kill()
        return {"error": "GUI did not exit"}
    return json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"error": f"no report (exit {gui.returncode})"}


# --------------------------------------------------------------------------------------------
# cases
# --------------------------------------------------------------------------------------------

CASES = {}


def case(cid: str, source: str):
    def deco(fn):
        CASES[cid] = (fn, source)
        return fn
    return deco


@case("C01", "ADR-0011 §1-§4, ADR-0016: read-only, formats, names, long paths, malformed and oversized files")
def c01(args) -> dict:
    d = fresh("c01")
    corpus = d / "corpus"
    write(corpus / "College" / "dbms_notes.md", "BCNF and third normal form decomposition notes")
    make_docx(corpus / "College" / "offer_letter.docx", "Internship offer from Infosys in Mysuru")
    write(corpus / "College" / "kmeans.pdf", minimal_pdf("K-means clustering handout elbow method"))
    write(corpus / "தமிழ் குறிப்புகள்" / "பொங்கல்.txt", "pongal festival sugarcane jaggery notes")
    write(corpus / "हिंदी" / "दिवाली बजट 🎆.txt", "diwali budget sweets lamps")
    write(corpus / "Tom & Jerry (final) v2" / "مرحبا.md", "arabic greeting file about camels")
    deep = corpus.joinpath(*[f"level_{i:02d}_long_folder_name" for i in range(14)])
    write(deep / "deep_note.txt", "zeppelin hangar deep path marker")
    write(corpus / "Broken" / "empty.txt", "")
    write(corpus / "Broken" / "garbage.pdf", os.urandom(4096))
    write(corpus / "Broken" / "truncated.pdf", minimal_pdf("truncated document")[:120])
    write(corpus / "Broken" / "random.docx", os.urandom(2048))
    write(corpus / "Broken" / "random.xlsx", b"PK\x03\x04" + os.urandom(2000))
    with zipfile.ZipFile(lp(corpus / "Broken" / "bomb.docx"), "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", b"0" * (700 * 2**20))
    with open(lp(corpus / "Broken" / "huge_log.txt"), "wb") as fh:
        fh.write((b"log line about servers\n" * 50000) * 110)  # ~126 MB, over the 100 MB read cap
    longest = len(lp(deep / "deep_note.txt")) - 4  # characters, without the \?\ prefix
    before = snapshot(corpus)
    code, ch = run_child(d / "index", [corpus])
    after = snapshot(corpus)
    rep = ch.report()
    st = console("--status", d / "index")
    q = {r["query"]: [x["rel_path"] for x in r["results"][:3]] for r in find(
        d / "index", "pongal sugarcane jaggery", "diwali sweets lamps", "zeppelin hangar marker",
        "infosys internship offer", "kmeans elbow handout", "camels greeting")}
    checks = {
        "exit 0": code == 0,
        "user files unchanged (sha256, size, mtime, attributes) and nothing added": before == after,
        "integrity ok": st["integrity"] == "ok",
        "every file indexed (14)": rep.get("new") == 14,
        "Tamil name found by content": bool(q["pongal sugarcane jaggery"]) and "பொங்கல்" in q["pongal sugarcane jaggery"][0],
        "Devanagari+emoji name found": bool(q["diwali sweets lamps"]) and "दिवाली" in q["diwali sweets lamps"][0],
        "Arabic name under 'Tom & Jerry (final) v2' found": bool(q["camels greeting"]) and "مرحبا" in q["camels greeting"][0],
        f"path of {longest} chars found": "deep_note.txt" in q["zeppelin hangar marker"][0],
        "docx/pdf extracted": "offer_letter" in q["infosys internship offer"][0] and "kmeans" in q["kmeans elbow handout"][0],
        "zip bomb not unpacked": access(d / "index", "Broken/bomb.docx") == "too_large",
        "126 MB file not read": access(d / "index", "Broken/huge_log.txt") == "too_large",
        "malformed files survive as name-only rows": all(access(d / "index", f"Broken/{n}") is not None
                                                          for n in ("garbage.pdf", "truncated.pdf", "random.docx",
                                                                    "random.xlsx", "empty.txt")),
        "peak child memory < 1.5 GB": ch.peak_mb < 1536,
    }
    return {"checks": checks, "metrics": {"seconds": ch.seconds, "peak_mb": ch.peak_mb, "report": rep}}


@case("C02", "ADR-0011 §2 + Rev. 1: junction loops are never descended")
def c02(args) -> dict:
    d = fresh("c02")
    corpus = d / "corpus"
    for i in range(20):
        write(corpus / "a" / f"n{i}.txt", f"junction test note {i}")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(corpus / "a" / "loop"), str(corpus)], capture_output=True, check=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(corpus / "elsewhere"), str(ROOT / "docs")], capture_output=True, check=True)
    c = Child(d / "index", [corpus])
    code = c.wait(timeout=120)
    return {"checks": {"finishes within 120 s": code == 0, "exactly the 20 real files": c.report().get("new") == 20},
            "metrics": {"seconds": c.seconds}}


@case("C03", "ADR-0011 §3: offline/placeholder files are never opened")
def c03(args) -> dict:
    d = fresh("c03")
    corpus = d / "corpus"
    write(corpus / "normal.txt", "ordinary visible file")
    ph = write(corpus / "cloud_only.txt", "secretword quokka never read")
    set_offline(ph)
    h = lock_exclusive(ph)  # if the indexer opened it at all, it would see a sharing violation instead
    try:
        code, _ = run_child(d / "index", [corpus])
    finally:
        unlock(h)
    hits = find(d / "index", "secretword quokka")[0]["results"]
    with db(d / "index") as c:
        ev = c.execute("SELECT source FROM time_evidence t JOIN files f USING(file_id) "
                       "WHERE f.rel_path='cloud_only.txt'").fetchall()
    return {"checks": {"exit 0": code == 0,
                       "indexed as placeholder (not 'locked': it was never opened)": access(d / "index", "cloud_only.txt") == "placeholder",
                       "its content is not in the index": not any("quokka" in json.dumps(r) for r in hits),
                       "only file-system times recorded": {r[0] for r in ev} <= {"ntfs", "motw"}}}


@case("C04", "ADR-0011 §4: a locked file becomes an error row and is retried next scan")
def c04(args) -> dict:
    d = fresh("c04")
    corpus = d / "corpus"
    write(corpus / "open_in_excel.csv", "quarterly,figures\nwombat,42\n")
    write(corpus / "other.txt", "unrelated text")
    h = lock_exclusive(corpus / "open_in_excel.csv")
    try:
        code1, _ = run_child(d / "index", [corpus])
        first = access(d / "index", "open_in_excel.csv")
    finally:
        unlock(h)
    code2, _ = run_child(d / "index", [corpus])
    hit = find(d / "index", "quarterly figures wombat")[0]["results"]
    return {"checks": {"first scan: 'locked', no crash": code1 == 0 and first == "locked",
                       "next scan reads it": code2 == 0 and access(d / "index", "open_in_excel.csv") == "ok",
                       "then found by content": bool(hit) and hit[0]["rel_path"] == "open_in_excel.csv"}}


@case("C05", "ADR-0011 §2: an unreadable folder is an error, and never tombstones its files")
def c05(args) -> dict:
    d = fresh("c05")
    corpus = d / "corpus"
    for i in range(5):
        write(corpus / "private" / f"p{i}.txt", f"private diary entry {i}")
    write(corpus / "public.txt", "public note")
    code1, _ = run_child(d / "index", [corpus])
    deny = subprocess.run(["icacls", str(corpus / "private"), "/deny", "*S-1-1-0:(RX)"], capture_output=True, text=True)
    try:
        code2, ch = run_child(d / "index", [corpus])
    finally:
        subprocess.run(["icacls", str(corpus / "private"), "/remove:d", "*S-1-1-0"], capture_output=True)
    with db(d / "index") as c:
        present = c.execute("SELECT COUNT(*) FROM files WHERE rel_path LIKE 'private/%' AND status='present'").fetchone()[0]
        errs = c.execute("SELECT COUNT(*) FROM errors WHERE path LIKE '%private%'").fetchone()[0]
    return {"checks": {"deny applied": deny.returncode == 0, "both scans exit 0": code1 == code2 == 0,
                       "files under the unreadable folder kept": present == 5 and ch.report().get("deleted") == 0,
                       "the folder error is recorded": errs >= 1}}


@case("C06", "ADR-0011 §4 test: Word-style save loop (temp write + ReplaceFileW) x1000 during indexing")
def c06(args) -> dict:
    d = fresh("c06")
    corpus = d / "corpus"
    gen_corpus(corpus, 300)
    target = write(corpus / "thesis.docx", b"")
    make_docx(target, "thesis draft version 0")
    template = Path(lp(target)).read_bytes()
    K32.ReplaceFileW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                 ctypes.c_void_p, ctypes.c_void_p]
    failures, done = [], threading.Event()

    def writer() -> None:
        for i in range(1000):
            tmp = corpus / f"~WRL{i:04d}.tmp"
            try:
                with open(lp(tmp), "wb") as fh:
                    fh.write(template + str(i).encode())
                if not K32.ReplaceFileW(lp(target), lp(tmp), None, 0, None, None):
                    failures.append(ctypes.get_last_error())
                    os.unlink(lp(tmp))
            except OSError as exc:
                failures.append(exc.winerror or exc.errno)
        done.set()

    t = threading.Thread(target=writer)
    t.start()
    runs, codes = 0, []
    while not done.is_set():
        (corpus / "touch.txt").write_text(str(runs), encoding="utf-8")
        code, _ = run_child(d / "index", [corpus])
        codes.append(code)
        runs += 1
    t.join()
    return {"checks": {"zero writer failures (no sharing violations)": not failures,
                       "every indexer run exits 0": all(c == 0 for c in codes)},
            "metrics": {"writer_failures": failures[:10], "indexer_runs": runs}}


@case("C07", "ADR-0011 'no lost scan after a kill', ADR-0012, ADR-0014 test 10: kill x5, then incremental == clean")
def c07(args) -> dict:
    d = fresh("c07")
    corpus = gen_corpus(d / "corpus", 1500, docs=60)
    rng = random.Random(7)
    kills = []
    for _ in range(5):
        c = Child(d / "inc", [corpus])
        time.sleep(rng.uniform(3, 9))
        alive = c.proc.poll() is None
        c.kill()
        kills.append(alive)
    code, last = run_child(d / "inc", [corpus])
    integ = console("--status", d / "inc")["integrity"]
    code2, _ = run_child(d / "clean", [corpus])
    with db(d / "inc") as c:
        struck = c.execute("SELECT rel_path, count FROM strikes").fetchall()
    quarantined = [r for r in struck if r[1] >= 2]
    diffs = compare_logical(logical(d / "inc"), logical(d / "clean"))
    return {"checks": {"kills landed mid-scan": sum(kills) >= 4, "resume completes": code == 0 and code2 == 0,
                       "integrity ok after 5 kills": integ == "ok",
                       "incremental == clean (files, chunks, per-event times, sessions, vectors)": not diffs},
            "metrics": {"diffs": diffs, "strikes": struck, "quarantined_by_kills": quarantined,
                        "final_report": last.report()}}


@case("C08", "ADR-0012 §1: a second writer is refused; the first is unaffected")
def c08(args) -> dict:
    d = fresh("c08")
    corpus = gen_corpus(d / "corpus", 800)
    a = Child(d / "index", [corpus])
    while not any(m.get("type") == "started" for m in a.msgs) and a.proc.poll() is None:
        time.sleep(0.1)
    code_b, b = run_child(d / "index", [corpus])
    code_a = a.wait()
    return {"checks": {"second writer exits 4 with 'locked'": code_b == 4 and "locked" in b.kinds(),
                       "first writer completes": code_a == 0 and a.report().get("new") == 800}}


@case("C09", "ADR-0001 §2: the indexer child cannot outlive the window (Job Object)")
def c09(args) -> dict:
    d = fresh("c09")
    corpus = gen_corpus(d / "corpus", 3000, docs=40)
    home = d / "home"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"roots": [str(corpus)], "excluded_names": [],
                                                    "index_dir": str(d / "index"), "gpu": "off",
                                                    "first_run_done": True}), encoding="utf-8")
    gui = subprocess.Popen([str(GUI_EXE)], env=dict(os.environ, CONTEXTFS_HOME=str(home), QT_QPA_PLATFORM="offscreen"))
    kids = []
    for _ in range(60):
        time.sleep(0.5)
        kids = child_pids(gui.pid)
        if kids and (d / "index" / "inprogress.marker").exists():
            break
    time.sleep(2)
    gui.kill()  # a crash, not a clean close
    gui.wait()
    deadline = time.time() + 10
    while time.time() < deadline and any(pid_alive(k) for k in kids):
        time.sleep(0.25)
    orphans = [k for k in kids if pid_alive(k)]
    code, after = run_child(d / "index", [corpus])
    return {"checks": {"the child was running": bool(kids), "child dies with the window": not orphans,
                       "the index is writable afterwards (lock released) and resumes": code == 0},
            "metrics": {"child_pids": kids, "resume_report": after.report()}}


@case("C10", "ADR-0001 §2: a file that hangs a parser costs restarts, then is quarantined (GUI-driven)")
def c10(args) -> dict:
    d = fresh("c10")
    corpus = gen_corpus(d / "corpus", 200)
    write(corpus / "stuck_here.txt", "this file hangs the parser")
    rep = selfdrive(d / "home", [corpus], d / "index", ["notes"],
                    env={"CONTEXTFS_TEST_FAULT": "hang:stuck_here", "CONTEXTFS_FILE_TIMEOUT": "5"}, max_seconds=300)
    with db(d / "index") as c:
        ok_files = c.execute("SELECT COUNT(*) FROM files WHERE access='ok'").fetchone()[0]
    return {"checks": {"GUI finished, last child exit 0": rep.get("last_child_exit") == 0 and not rep.get("timed_out"),
                       "two automatic restarts": rep.get("restarts") == 2,
                       "the file is quarantined": access(d / "index", "stuck_here.txt") == "quarantined",
                       "every other file indexed": ok_files == 200},
            "metrics": {"selfdrive": rep}}


@case("C11", "ADR-0001 §2: a native parser crash (access violation) is quarantined after two strikes")
def c11(args) -> dict:
    d = fresh("c11")
    corpus = gen_corpus(d / "corpus", 100)
    write(corpus / "boom_here.txt", "this file crashes the parser")
    codes = []
    for _ in range(3):
        code, _ = run_child(d / "index", [corpus], env={"CONTEXTFS_TEST_FAULT": "crash:boom_here"})
        codes.append(code)
    return {"checks": {"crash, crash, then success": codes[0] != 0 and codes[1] != 0 and codes[2] == 0,
                       "quarantined": access(d / "index", "boom_here.txt") == "quarantined",
                       "integrity ok": console("--status", d / "index")["integrity"] == "ok"},
            "metrics": {"exit_codes": codes}}


@case("C12", "ADR-0012 §3: a damaged database is detected, set aside and rebuilt; search recovers")
def c12(args) -> dict:
    d = fresh("c12")
    corpus = gen_corpus(d / "corpus", 400)
    write(corpus / "marker.txt", "ocelot lighthouse marker")
    out = {}
    for variant in ("header", "page"):
        idx = d / f"index_{variant}"
        run_child(idx, [corpus])
        dbf = idx / "contextfs.db"
        data = bytearray(dbf.read_bytes())
        if variant == "header":
            data[:100] = os.urandom(100)
        else:
            mid = len(data) // 2
            data[mid:mid + 8192] = os.urandom(8192)
        dbf.write_bytes(bytes(data))
        try:
            console("--find", idx, "ocelot")
            search_before = "no error"
        except RuntimeError as exc:
            search_before = str(exc)[-120:]
        code, ch = run_child(idx, [corpus])
        hits = find(idx, "ocelot lighthouse marker")[0]["results"]
        out[variant] = {"exit 0": code == 0, "recovered message": "recovered" in ch.kinds(),
                        "damaged copy kept": len(list(idx.glob("damaged-*"))) == 1,
                        "search works after": bool(hits) and hits[0]["rel_path"] == "marker.txt",
                        "search_before": search_before}
    return {"checks": {f"{v}: {k}": val for v, r in out.items() for k, val in r.items() if k != "search_before"},
            "metrics": {v: r["search_before"] for v, r in out.items()}}


@case("C13", "ADR-0012 §3: truncated vector files are repaired; search never crashes")
def c13(args) -> dict:
    d = fresh("c13")
    corpus = gen_corpus(d / "corpus", 300)
    write(corpus / "marker.txt", "narwhal observatory marker")
    run_child(d / "index", [corpus])
    for kind in ("chunk", "doc"):
        f = d / "index" / "vectors" / f"{kind}.f32"
        f.write_bytes(f.read_bytes()[: (f.stat().st_size // 2) // 1536 * 1536 + 700])  # half, plus a torn row
    during = find(d / "index", "narwhal observatory")[0]
    code, ch = run_child(d / "index", [corpus])
    hits = find(d / "index", "narwhal observatory marker")[0]["results"]
    return {"checks": {"search on the damaged index does not crash": isinstance(during["results"], list),
                       "update repairs": code == 0 and ch.report().get("repaired_vectors", 0) > 100,
                       "found again": bool(hits) and hits[0]["rel_path"] == "marker.txt",
                       "integrity ok": console("--status", d / "index")["integrity"] == "ok"}}


@case("C14", "ADR-0012 §4: low disk refuses to start and leaves the index untouched")
def c14(args) -> dict:
    d = fresh("c14")
    corpus = gen_corpus(d / "corpus", 100)
    run_child(d / "index", [corpus])
    write(corpus / "new.txt", "added later")
    before = console("--status", d / "index")["files"]
    code, ch = run_child(d / "index", [corpus], env={"CONTEXTFS_MIN_FREE_MB": str(10**9)})
    after = console("--status", d / "index")["files"]
    return {"checks": {"exit 5 with disk_low": code == 5 and "disk_low" in ch.kinds(), "index untouched": before == after == 100}}


@case("C15", "ADR-0013/0003 scale: 10^4-scale index time, memory, search latency, no-op launch update")
def c15(args) -> dict:
    d = WORK / "c15"
    corpus = d / "corpus"
    if not (corpus / ".complete").exists():
        fresh("c15")
        gen_corpus(corpus, args.scale, docs=300, seed=15)
        write(corpus / ".complete", "")
    shutil.rmtree(d / "index", ignore_errors=True)
    net = []
    c = Child(d / "index", [corpus], gpu="auto")

    def sample_net() -> None:
        while c.proc.poll() is None:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  f"@(Get-NetTCPConnection -OwningProcess {c.proc.pid} -EA SilentlyContinue).Count + "
                                  f"@(Get-NetUDPEndpoint -OwningProcess {c.proc.pid} -EA SilentlyContinue).Count"],
                                 capture_output=True, text=True)
            net.append(out.stdout.strip())
            time.sleep(10)

    threading.Thread(target=sample_net, daemon=True).start()
    code = c.wait(timeout=7200)
    rep = c.report()
    t = time.perf_counter()
    code2, c2 = run_child(d / "index", [corpus], gpu="auto")
    noop_s = round(time.perf_counter() - t, 1)
    qfile = d / "queries.txt"
    qrng = random.Random(3)
    qfile.write_text("\n".join(" ".join(qrng.choice(WORDS) for _ in range(qrng.randrange(2, 6))) for _ in range(60)),
                     encoding="utf-8")
    res = console("--find", d / "index", "--queries", qfile)["queries"]
    ms = [r["ms"] for r in res]
    size_mb = round(sum(f.stat().st_size for f in (d / "index").rglob("*") if f.is_file()) / 2**20)
    files = sum(1 for _ in corpus.rglob("*") if _.is_file())
    return {"checks": {"full index completes": code == 0 and rep.get("new", 0) >= args.scale,
                       "no-op launch update completes": code2 == 0 and c2.report().get("new") == 0,
                       "search p95 < 300 ms": float(np.percentile(ms, 95)) < 300,
                       "peak child memory < 2 GB (job limit)": c.peak_mb < 2048,
                       "no network endpoints owned by the child": all(x in ("0", "") for x in net)},
            "metrics": {"files": files, "index_seconds": c.seconds, "files_per_s": round(files / max(c.seconds, 1), 1),
                        "peak_mb": c.peak_mb, "index_mb": size_mb, "noop_update_s": noop_s,
                        "search_p50_ms": float(np.percentile(ms, 50)), "search_p95_ms": float(np.percentile(ms, 95)),
                        "device": next((m.get("device") for m in c.msgs if m.get("type") == "done"), None),
                        "net_samples": net[:20]}}


@case("C16", "ADR-0001: no event-loop stall > 250 ms while indexing and searching (packaged GUI)")
def c16(args) -> dict:
    d = fresh("c16")
    corpus = gen_corpus(d / "corpus", 3000, docs=150, seed=16)
    rep = selfdrive(d / "home", [corpus], d / "index",
                    ["budget invoice", "the pdf about clustering", "lecture notes from march", "hotel ticket",
                     "thesis chapter results"], max_seconds=1200)
    return {"checks": {"GUI finished the update": rep.get("last_child_exit") == 0 and not rep.get("timed_out"),
                       "no stall > 250 ms": rep.get("stalls_over_250ms") == 0,
                       "search-to-results p95 < 500 ms during indexing": (rep.get("search_p95_ms") or 1e9) < 500},
            "metrics": {"selfdrive": rep}}


@case("C17", "Decision 95: the frozen self test passes")
def c17(args) -> dict:
    out = subprocess.run([str(CON_EXE), "--selftest"], capture_output=True, text=True, encoding="utf-8",
                         timeout=600, cwd=str(WORK))
    return {"checks": {"selftest exit 0": out.returncode == 0, "ALL PASS": "RESULT: ALL PASS" in out.stdout},
            "metrics": {"tail": out.stdout.splitlines()[-6:]}}


@case("C18", "Phase 33 real-data finding: a folder removed from Settings leaves search (packaged child)")
def c18(args) -> dict:
    d = fresh("c18")
    a, b = gen_corpus(d / "a", 50), d / "b"
    write(b / "removed_marker.txt", "axolotl removed folder marker")
    run_child(d / "index", [a, b])
    before = find(d / "index", "axolotl removed folder marker")[0]["results"]
    code, ch = run_child(d / "index", [a])
    after = find(d / "index", "axolotl removed folder marker")[0]["results"]
    return {"checks": {"found while listed": bool(before) and before[0]["rel_path"] == "removed_marker.txt",
                       "gone after removal": code == 0 and not any(r["rel_path"] == "removed_marker.txt" for r in after),
                       "reported": ch.report().get("removed_roots") == [str(b)]}}


@case("C19", "Phase 33 real-data finding: closing mid-scan never leaves files without dates")
def c19(args) -> dict:
    d = fresh("c19")
    corpus = gen_corpus(d / "corpus", 2500, docs=50)
    cancels = 0
    for delay in (4, 6, 8):
        c = Child(d / "index", [corpus])
        time.sleep(delay)
        (d / "index" / "cancel.request").write_text("x", encoding="utf-8")
        c.wait()
        cancels += bool(c.report().get("cancelled"))
    with db(d / "index") as c:
        undated = c.execute("SELECT COUNT(*) FROM files f WHERE status='present' AND NOT EXISTS "
                            "(SELECT 1 FROM file_time t WHERE t.file_id=f.file_id)").fetchone()[0]
        files = c.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    return {"checks": {"three cancelled scans": cancels == 3, "no indexed file lacks dates": undated == 0 and files > 0},
            "metrics": {"files_after_cancels": files}}


@case("C20", "QA H1 (Phase 33): a renamed or deleted folder leaves no ghost results")
def c20(args) -> dict:
    d = fresh("c20")
    corpus = d / "corpus"
    for i in range(10):
        write(corpus / "projA" / f"a{i}.txt", f"quasar telescope proposal part {i}")
    write(corpus / "keep" / "k.txt", "unrelated keeper")
    run_child(d / "index", [corpus])
    (corpus / "projA").rename(corpus / "projB")
    code1, c1 = run_child(d / "index", [corpus])
    after_rename = [r["rel_path"] for r in find(d / "index", "quasar telescope proposal")[0]["results"]]
    shutil.rmtree(lp(corpus / "projB"))
    code2, c2 = run_child(d / "index", [corpus])
    after_delete = [r["rel_path"] for r in find(d / "index", "quasar telescope proposal")[0]["results"]]
    return {"checks": {"rename: old paths gone, new paths found once": code1 == 0 and c1.report().get("deleted") == 10
                       and not any(p.startswith("projA/") for p in after_rename)
                       and sum(p.startswith("projB/") for p in after_rename) == 10,
                       "delete: the folder's text is no longer searchable": code2 == 0
                       and not any(p.startswith(("projA/", "projB/")) for p in after_delete)}}


@case("C21", "QA H2+H3 (Phase 33): 1601- and pre-1970-dated files never stop a scan or crash the window")
def c21(args) -> dict:
    d = fresh("c21")
    corpus = gen_corpus(d / "corpus", 40)
    old = write(corpus / "old" / "ancient_parchment.txt", "ancient parchment scroll")
    sixties = write(corpus / "old" / "sixties_letter.txt", "ancient parchment letter from the sixties")
    zero = -11644473600 * 10**9 + 10**9
    os.utime(lp(old), ns=(zero, zero))
    t1965 = -157766400 * 10**9
    os.utime(lp(sixties), ns=(t1965, t1965))
    code1, c1 = run_child(d / "index", [corpus])
    code2, c2 = run_child(d / "index", [corpus])
    rep = selfdrive(d / "home", [corpus], d / "index", ["ancient parchment"], max_seconds=120)
    return {"checks": {"scan completes with every file": code1 == 0 and c1.report().get("new") == 42,
                       "next scan sees them unchanged": code2 == 0 and c2.report().get("unchanged") == 42,
                       "the window shows them without crashing": not rep.get("error") and not rep.get("timed_out")
                       and any(n >= 2 for n in rep.get("result_counts", []))},
            "metrics": {"selfdrive": rep}}


@case("C22", "QA L1 (Phase 33): support commands answer in JSON, even for a missing index")
def c22(args) -> dict:
    d = fresh("c22")
    outs = {}
    for args_ in (("--status", d / "nope"), ("--find", d / "nope", "x"), ("--status",)):
        r = subprocess.run([str(CON_EXE), *map(str, args_)], capture_output=True, text=True, encoding="utf-8",
                           timeout=120, cwd=str(WORK))
        try:
            outs[" ".join(map(str, args_[:1]))] = ("error" in json.loads(r.stdout.strip().splitlines()[-1]), r.returncode)
        except (ValueError, IndexError):
            outs[" ".join(map(str, args_[:1]))] = (False, r.returncode)
    return {"checks": {"JSON error, exit 2, no traceback": all(ok and code == 2 for ok, code in outs.values())},
            "metrics": {"outputs": outs}}


# --------------------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", default="")
    parser.add_argument("--scale", type=int, default=30000)
    parser.add_argument("--tag", default=f"p5-{datetime.now():%Y%m%d}")
    args = parser.parse_args()
    if not GUI_EXE.exists():
        print("build first: python scripts/build_app.py")
        return 2
    WORK.mkdir(parents=True, exist_ok=True)
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    out_dir = ROOT / "data" / "hardening" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "report.json"
    results = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    exe_hash = hashlib.sha256(GUI_EXE.read_bytes()).hexdigest()[:16]
    for cid, (fn, source) in CASES.items():
        if only and cid not in only:
            continue
        t = time.perf_counter()
        print(f"{cid} … {source}", flush=True)
        try:
            r = fn(args)
        except Exception as exc:  # noqa: BLE001 - a crashing case is a failing case
            r = {"checks": {"case ran": False}, "metrics": {"exception": f"{type(exc).__name__}: {exc}"[:800]}}
        r.update(source=source, seconds=round(time.perf_counter() - t, 1), exe=exe_hash,
                 ok=all(r["checks"].values()), at=datetime.now().isoformat(timespec="seconds"))
        results[cid] = r
        for name, ok in r["checks"].items():
            print(f"   [{'PASS' if ok else 'FAIL'}] {name}")
        report_path.write_text(json.dumps(results, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    lines = [f"# Hardening report `{args.tag}`", "", f"Packaged build `ContextFS.exe` sha256[:16] = `{exe_hash}`", "",
             "| Case | Source | Result | Seconds |", "|---|---|---|---|"]
    for cid, r in sorted(results.items()):
        lines.append(f"| {cid} | {r['source']} | {'PASS' if r['ok'] else 'FAIL'} | {r['seconds']} |")
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    shutil.rmtree(lp(TRASH), ignore_errors=True)
    passed = sum(r["ok"] for r in results.values())
    print(f"\n{passed}/{len(results)} cases pass -> {report_path}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
