"""Rewrite a persona's queries with a local LLM, from the degraded memory view only (ADR-0015 §6).

The template generator (``contextfs.benchgen.queries``) picks targets, memory views and graded
qrels. This script asks a **local** LLM (llama.cpp ``llama-server`` on 127.0.0.1, model logged) to
write the query a person would type, given **only** the memory view: coarse time, activity,
paraphrased topic, kind of file, and a person or organisation. Never the filename, the exact date
or the file's text.

Targets, qrels and cue types are unchanged; only ``text`` is replaced. The output goes to
``truth/queries_llm.jsonl``. ~15% of queries then get one typo, by the same seeded rule as the
templates.

Usage:
    python scripts/llm_rewrite_queries.py data/bench/<persona> --url http://127.0.0.1:8080
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from contextfs.benchgen.queries import _typo  # noqa: E402

SYSTEM = (
    "You imitate a person typing into a desktop file-search box, trying to find an old file from "
    "memory. You only know the memory hints you are given. Do not invent file names, exact dates, "
    "numbers or technical details that are not in the hints. People type short, informal, sometimes "
    "incomplete queries (3 to 12 words), often lowercase, often without punctuation. Reply with the "
    "query text only."
)

CUE_STYLE = {
    "temporal": "Lead with when it was. Mention the kind of file.",
    "activity": "Describe what you were doing at the time (the activity), not the content.",
    "entity": "Mention the person or organisation it relates to.",
    "semantic": "Describe what the file is about, in your own words.",
    "format": "Mention the kind of file and roughly what it was for.",
    "mixed": "Combine when it was with what it was about.",
    "vague": "Be vague and half-remembered; you are not sure of the details.",
}


def build_prompt(view: dict, cue: str) -> str:
    hints = [f"- kind of file: {view.get('kind', 'file')}"]
    if view.get("when") and cue in {"temporal", "mixed", "vague"}:
        hints.append(f"- roughly when: {view['when']}")
    if view.get("activity") and cue in {"activity", "format", "mixed", "temporal"}:
        hints.append(f"- what you were doing then: {view['activity']}")
    if view.get("topic") and cue in {"semantic", "format", "mixed", "vague", "activity"}:
        hints.append(f"- what it was about: {view['topic']}")
    if view.get("entity") and cue == "entity":
        hints.append(f"- person or organisation involved: {view['entity']}")
    return "Memory hints:\n" + "\n".join(hints) + f"\n\nStyle: {CUE_STYLE[cue]}"


def _api_key() -> str:
    """The local server's key (git-ignored file read by llama-server --api-key-file)."""
    return (
        (PROJECT_ROOT / ".tools" / "llamacpp" / "api_key.txt").read_text(encoding="utf-8").strip()
    )


def ask(url: str, prompt: str, seed: int) -> str:
    body = {
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        "temperature": 0.8,
        "top_p": 0.95,
        "seed": seed,
        "max_tokens": 48,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {_api_key()}"},
    )
    with urllib.request.urlopen(
        req, timeout=120
    ) as resp:  # noqa: S310 - loopback only, checked below
        text = json.loads(resp.read())["choices"][0]["message"]["content"]
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip().strip('"').strip()
    return text.splitlines()[0].strip() if text else ""


_TIME_WORDS = re.compile(
    r"\b(19|20)\d{2}\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I
)


def invents_time(text: str, prompt: str) -> bool:
    """True if the query names a year or month that is not in the memory hints."""
    hinted = {m.group(0).lower()[:3] for m in _TIME_WORDS.finditer(prompt)}
    return any(m.group(0).lower()[:3] not in hinted for m in _TIME_WORDS.finditer(text))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("persona_dir", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument(
        "--quiet", action="store_true", help="print counts only (use for test personas)"
    )
    parser.add_argument("--model-name", default="Qwen3-8B-Q4_K_M (llama.cpp b11606, CUDA 13.4)")
    args = parser.parse_args()
    if not re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?/?$", args.url):
        print("refusing: the LLM endpoint must be loopback (local-first)")
        return 2

    src = args.persona_dir / "truth" / "queries.jsonl"
    out = args.persona_dir / "truth" / "queries_llm.jsonl"
    queries = [json.loads(line) for line in src.read_text(encoding="utf-8").splitlines()]
    rng = random.Random(len(queries) * 7 + sum(ord(c) for c in args.persona_dir.name))
    rewritten = []
    for i, q in enumerate(queries):
        seed = 1000 + i  # deterministic per position; llama.cpp honours the request seed
        text = ""
        prompt = build_prompt(q["memory_view"], q["cue"])
        for attempt in range(4):
            text = ask(args.url, prompt, seed + attempt * 7919)
            if 2 <= len(text.split()) <= 20 and not invents_time(text, prompt):
                break
        typo = rng.random() < 0.15
        if typo:
            text = _typo(text, rng)
        rewritten.append(
            q | {"text": text, "typo": typo, "writer": args.model_name, "template_text": q["text"]}
        )
        if not args.quiet:
            print(f"{q['cue']:<9} {text}")
    out.write_text("".join(json.dumps(q) + "\n" for q in rewritten), encoding="utf-8")
    print(f"wrote {out} ({len(rewritten)} queries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
