"""Memory-style queries with graded relevance, generated from the sealed truth (ADR-0015 §5–6).

Each query is written from a **degraded memory view** of its target: a coarse time ("in March",
"before the exam"), the activity, a paraphrased topic, the kind of file. Never the filename, the
exact date or the file's rare terms. This module is the deterministic *template* writer, used for
dev, smoke tests and as a fallback. The test split's queries are to be written by a local LLM from
the same memory view (a GPU workload that needs the user's go-ahead).

Relevance (graded, from the simulation, assigned before any system runs):
- 3: the target;
- 2: a byte-identical duplicate of the target ("x (1).pdf"), or the other half of a draft/final pair;
- 1: other files of the target's activity;
- 0: everything else.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime

__all__ = ["Query", "generate_queries", "CUE_TYPES"]

CUE_TYPES = ("temporal", "activity", "entity", "semantic", "format", "mixed", "vague")

KIND_WORDS = {
    "pdf": ["pdf", "PDF", "document"],
    "docx": ["word doc", "document", "report"],
    "pptx": ["slides", "presentation", "deck"],
    "xlsx": ["spreadsheet", "sheet", "excel file"],
    "md": ["notes", "note"],
    "txt": ["text file", "notes"],
    "code": ["code", "script"],
    "jpg": ["photo", "picture"],
    "zip": ["zip", "archive"],
}

# {verb}: past-tense verb fitting the file's origin; {when}: a noun phrase ("February",
# "mid-March 2025") that templates put after "in"/"around"/"from".
_T = {
    "A": {
        "temporal": [
            "the {kind} I {verb} in {when}",
            "{kind} I {verb} around {when}",
            "which {kind} did I {verb_base} in {when}",
        ],
        "activity": [
            "the {kind} I used during {activity}",
            "{kind} from when I was busy with {activity}",
            "files I had open for {activity}",
        ],
        "entity": [
            "the {kind} about {entity}",
            "{kind} that mentions {entity}",
            "anything for {entity}",
        ],
        "semantic": ["my {kind} on {topic}", "{kind} explaining {topic}", "notes about {topic}"],
        "format": ["that {kind} with the {topic} stuff", "the {kind} for {activity}"],
        "mixed": ["the {kind} on {topic} from {when}", "{kind} for {activity} around {when}"],
        "vague": [
            "that thing about {topic}",
            "something on {topic} I saw a while back",
            "the file with {topic} in it",
        ],
    },
    "B": {
        "temporal": [
            "{when}, I {verb} a {kind}, which one",
            "the {kind} I {verb} back in {when}",
            "stuff I {verb} in {when}",
        ],
        "activity": [
            "{kind} I needed for {activity}",
            "what did I use for {activity}, the {kind}",
            "the {activity} {kind}",
        ],
        "entity": [
            "{entity} {kind}",
            "where did {entity} come up, the {kind}",
            "{kind} related to {entity}",
        ],
        "semantic": ["{topic} {kind}", "explanation of {topic}, a {kind}", "{topic} write-up"],
        "format": ["it was a {kind}, something about {topic}", "a {kind} for {activity}"],
        "mixed": ["{kind} on {topic}, {when}", "{activity} {kind} from {when}"],
        "vague": ["{topic}... i think", "something {topic} related", "that {topic} file"],
    },
}

#: Cues that make sense for a file with no text (photos, archives).
_TEXTLESS_CUES = {"temporal", "activity", "entity"}
_VERBS = {
    "authored": (["worked on", "edited", "wrote"], "work on"),
    "downloaded": (["downloaded", "saved", "got"], "download"),
    "received": (["took", "saved"], "save"),
}


@dataclass
class Query:
    """One benchmark query with graded judgements."""

    query_id: str
    persona_id: str
    cue: str
    text: str
    target: str
    qrels: dict[str, int] = field(default_factory=dict)
    memory_view: dict[str, str] = field(default_factory=dict)
    typo: bool = False
    as_of: str = ""


def _coarse_when(t: datetime, as_of: datetime, rng: random.Random) -> str:
    """A coarse, preposition-free time phrase, as people remember it."""
    part = "early" if t.day <= 10 else "late" if t.day >= 21 else "mid"
    month = t.strftime("%B")
    options = [month, f"{part} {month}", f"{month} {t.year}", f"{part} {month} {t.year}"]
    if as_of.year - t.year >= 1 and rng.random() < 0.3:
        options.append(str(t.year))
    return rng.choice(options)


def _typo(text: str, rng: random.Random) -> str:
    words = [w for w in text.split() if len(w) >= 5 and w.isalpha()]
    if not words:
        return text
    w = rng.choice(words)
    i = rng.randrange(1, len(w) - 2)
    bad = w[:i] + w[i + 1] + w[i] + w[i + 2 :]  # transpose two letters
    return text.replace(w, bad, 1)


def _activity_phrase(act: dict, rng: random.Random) -> str:
    kind = act["kind"]
    title = act["title"]
    if kind == "exam_prep":
        return f"my {act['topic']} exam"
    if kind == "hackathon":
        return rng.choice([f"the {act['topic']} hackathon", "that hackathon weekend"])
    if kind == "job_search":
        return "my internship applications"
    if kind == "trip":
        return f"the {act['place'] or act['topic']} trip"
    if kind == "capstone":
        return "my final year project"
    if kind == "paper":
        return f"the {title.split(' paper')[0]} paper"
    if kind == "client_project":
        return f"the {title} project"
    return title


def generate_queries(
    persona: dict, activities: list[dict], records: list[dict], n: int, family: str, seed: int
) -> list[Query]:
    """Generate ``n`` queries spread across cue types."""
    rng = random.Random(seed * 31 + 17)
    as_of = datetime.fromisoformat(str(persona["end"]))
    acts = {a["activity_id"]: a for a in activities}
    by_act: dict[str, list[dict]] = {}
    for r in records:
        if r["activity_id"]:
            by_act.setdefault(r["activity_id"], []).append(r)
    candidates = [
        r for r in records if not r["noise"] and r["duplicate_of"] is None and r["activity_id"]
    ]
    rng.shuffle(candidates)
    templates = _T[family]
    queries: list[Query] = []
    used: set[str] = set()
    cues = [CUE_TYPES[i % len(CUE_TYPES)] for i in range(n)]
    for cue in cues:
        for r in candidates:
            if r["rel_path"] in used:
                continue
            act = acts.get(r["activity_id"]) or {}
            entity = (
                next(iter(r["keywords"]), "")
                if act.get("kind") in {"job_search", "client_project"}
                else ""
            )
            if act.get("people"):
                entity = entity or act["people"][0]
            if cue == "entity" and not entity:
                continue
            if r["kind"] in {"jpg", "zip"} and cue not in _TEXTLESS_CUES:
                continue
            t = r["truth"]
            when_t = datetime.fromisoformat(t["last_user_edit"] or t["arrived_here"])
            verbs, verb_base = _VERBS.get(r["origin"], _VERBS["downloaded"])
            view = {
                "verb": rng.choice(verbs),
                "verb_base": verb_base,
                "kind": rng.choice(KIND_WORDS.get(r["kind"], ["file"])),
                "when": _coarse_when(when_t, as_of, rng),
                "activity": _activity_phrase(act, rng) if act else "",
                "topic": r["topic"] or (r["keywords"][0] if r["keywords"] else "it"),
                "entity": entity,
            }
            text = rng.choice(templates[cue]).format(**view)
            typo = rng.random() < 0.15
            if typo:
                text = _typo(text, rng)
            qrels = {r["rel_path"]: 3}
            for other in by_act.get(r["activity_id"], []):
                if other["rel_path"] != r["rel_path"]:
                    same_doc = other.get("duplicate_of") == r["rel_path"] or (
                        other["kind"] == r["kind"]
                        and other["title"].split(" (")[0] == r["title"].split(" (")[0]
                    )
                    qrels[other["rel_path"]] = 2 if same_doc else 1
            used.add(r["rel_path"])
            queries.append(
                Query(
                    query_id=f"{persona['persona_id']}:q{len(queries) + 1:03d}",
                    persona_id=persona["persona_id"],
                    cue=cue,
                    text=text,
                    target=r["rel_path"],
                    qrels=qrels,
                    memory_view=view,
                    typo=typo,
                    as_of=as_of.isoformat(),
                )
            )
            break
    return queries
