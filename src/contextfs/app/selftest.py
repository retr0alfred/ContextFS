"""Offline end-to-end self test of the app (ADR-0004 §3, Decision 95's light acceptance check).

1. Installs a Python audit hook that **fails on any network use** (socket connect / DNS).
2. Reports the runtime: frozen or not, ONNX Runtime version and providers, model files, and the
   Smart App Control state (Evaluation mode is an open risk, Decision 95).
3. Builds a throwaway corpus and index in a temp folder, updates, searches, re-updates
   (expects a no-op), and checks the expected results.

Exit code 0 only if every check passes.
"""

from __future__ import annotations

import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

__all__ = ["run_selftest", "smart_app_control_state"]


def _block_network() -> None:
    def hook(event, args):
        if event in ("socket.connect", "socket.getaddrinfo", "socket.bind"):
            raise RuntimeError(f"network use blocked by selftest: {event} {args!r}"[:200])

    sys.addaudithook(hook)


def smart_app_control_state() -> str:
    """'off' | 'on' | 'evaluation' | 'unknown' (read-only registry query)."""
    if sys.platform != "win32":
        return "n/a"
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\CI\Policy") as k:
            v, _ = winreg.QueryValueEx(k, "VerifiedAndReputablePolicyState")
        return {0: "off", 1: "on", 2: "evaluation"}.get(int(v), f"unknown({v})")
    except OSError:
        return "unknown"


def run_selftest(console=None) -> int:
    """Run all checks; print a report; return a process exit code."""
    say = console.print if console else print
    _block_network()
    failures = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        say(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    import onnxruntime as ort

    from contextfs.engine import Engine, default_model_dir

    say("ContextFS self test")
    say(f"  frozen app: {bool(getattr(sys, 'frozen', False))}  python {sys.version.split()[0]}")
    say(f"  onnxruntime {ort.__version__}; app uses CPUExecutionProvider only")
    sac = smart_app_control_state()
    say(f"  Smart App Control: {sac}" + ("  (OPEN RISK: may switch to On and block unsigned builds)"
                                         if sac == "evaluation" else ""))
    model = default_model_dir()
    check("model files present", (model / "model_fp32.onnx").exists() and (model / "tokenizer.json").exists(),
          str(model))
    as_of = datetime(2026, 9, 30, 21).astimezone()
    with tempfile.TemporaryDirectory(prefix="contextfs-selftest-") as tmp:
        root = Path(tmp) / "files"
        (root / "College").mkdir(parents=True)
        (root / "College" / "dbms_normalization.md").write_text(
            "# BCNF and 3NF\nDecomposition examples. Submission deadline 24 November 2025.", encoding="utf-8")
        (root / "Personal").mkdir()
        (root / "Personal" / "biryani_recipe.txt").write_text("Biryani with saffron and mint.", encoding="utf-8")
        (root / "Personal" / "trip_budget.csv").write_text("item,cost\ntrain,900\nhotel,2400\n", encoding="utf-8")
        t = time.perf_counter()
        with Engine.open(Path(tmp) / "index", mode="write") as eng:
            rep = eng.update([root])
            check("first update indexes 3 files", rep.new == 3 and rep.errors == 0, f"{rep.seconds}s")
            rep2 = eng.update([root])
            check("second update is a no-op", rep2.new == rep2.changed == rep2.deleted == 0)
        with Engine.open(Path(tmp) / "index") as eng:
            r = eng.search("notes on BCNF decomposition", as_of=as_of)
            check("content query finds the right file", bool(r.results) and r.results[0].rel_path.endswith(
                "dbms_normalization.md"))
            check("every result explains itself", all(x.reasons for x in r.results))
            check("gibberish returns a clear note", not eng.search("qzxv wplk", as_of=as_of).results)
        check("end-to-end time", True, f"{time.perf_counter() - t:.1f}s")
    say("RESULT: " + ("ALL PASS" if not failures else f"FAILED ({', '.join(failures)})"))
    return 0 if not failures else 1
