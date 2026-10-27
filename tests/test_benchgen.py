"""The benchmark generator must produce internally consistent times (ADR-0014 test 1, skeptic N1)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="NTFS birth-time semantics")


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], cwd=PROJECT_ROOT, capture_output=True, text=True)


@pytest.mark.parametrize(
    ("archetype", "seed", "damage"),
    [("student", 101, "none"), ("researcher", 102, "none"), ("professional", 103, "mixed")],
)
def test_generated_persona_passes_its_audit(tmp_path, archetype, seed, damage):
    gen = _run(
        "scripts/generate_benchmark.py",
        "--archetype",
        archetype,
        "--seed",
        str(seed),
        "--family",
        "A",
        "--scale",
        "0.5",
        "--damage",
        damage,
        "--out",
        str(tmp_path),
    )
    assert gen.returncode == 0, gen.stderr[-2000:]
    persona_dir = next(tmp_path.iterdir())
    audit = _run("scripts/audit_benchmark.py", str(persona_dir))
    assert audit.returncode == 0, audit.stdout[-3000:]
    assert "ALL PASS" in audit.stdout


def test_generation_is_deterministic(tmp_path):
    for sub in ("a", "b"):
        gen = _run(
            "scripts/generate_benchmark.py",
            "--archetype",
            "student",
            "--seed",
            "7",
            "--family",
            "A",
            "--scale",
            "0.5",
            "--out",
            str(tmp_path / sub),
        )
        assert gen.returncode == 0, gen.stderr[-2000:]
    (a,) = (tmp_path / "a").iterdir()
    (b,) = (tmp_path / "b").iterdir()
    ta = (a / "truth" / "truth_manifest.jsonl").read_text(encoding="utf-8")
    tb = (b / "truth" / "truth_manifest.jsonl").read_text(encoding="utf-8")
    assert ta == tb


def test_truth_lives_outside_the_corpus(tmp_path):
    gen = _run(
        "scripts/generate_benchmark.py",
        "--archetype",
        "student",
        "--seed",
        "9",
        "--family",
        "B",
        "--scale",
        "0.5",
        "--out",
        str(tmp_path),
    )
    assert gen.returncode == 0, gen.stderr[-2000:]
    (persona,) = tmp_path.iterdir()
    corpus_files = {p.name for p in (persona / "corpus").rglob("*") if p.is_file()}
    assert "truth_manifest.jsonl" not in corpus_files
    assert not any(
        "truth" in part
        for p in (persona / "corpus").rglob("*")
        for part in p.relative_to(persona / "corpus").parts
    )
