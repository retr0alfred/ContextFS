"""Text for each planned file: a deterministic template provider in two families (ADR-0015 §2).

Family "A" is used for dev personas and family "B" for test personas, and they share **no**
sentence templates. So nothing tuned on dev phrasing can match test phrasing verbatim.

This provider is the development and fallback path. A local-LLM provider with the same interface
(``payload(plan, persona, rng) -> format payload``) can replace it. That is a GPU workload that
needs the user's go-ahead (ADR-0015 §6).

Every labelled date mention is rendered next to wording that fits its label: commitment words for
meaningful dates, record or reference words for incidental ones. The words come from the family's
own vocabulary.
"""

from __future__ import annotations

import random

from contextfs.benchgen.model import FilePlan, Persona

__all__ = ["TemplateProvider"]

_FAMILY = {
    "A": {
        "intro": [
            "These notes cover {topic} for {title}.",
            "Summary of {topic}: what matters for {title}.",
            "Working notes on {topic}.",
        ],
        "body": [
            "The key idea in {kw} is how it connects to {kw2}.",
            "Remember that {kw} depends on {kw2}; revisit the worked example.",
            "{kw} and {kw2} came up repeatedly, so they deserve a second look.",
            "A short checklist: {kw}, {kw2}, then practice problems.",
        ],
        "meaningful": [
            "Deadline: {d}.",
            "Due on {d} — do not miss it.",
            "Exam scheduled for {d}.",
            "Meeting fixed for {d}.",
            "Submit by {d}.",
        ],
        "incidental": [
            "(published {d})",
            "Recorded on {d}.",
            "Printed {d}.",
            "Reference dated {d}.",
        ],
        "people": ["Discussed with {who}.", "From {who}.", "{who} asked about this."],
        "code": "# {title}\n# {topic}: {kw}, {kw2}\n\ndef main():\n    data = load('{kw}')\n    return analyse(data, method='{kw2}')\n",
    },
    "B": {
        "intro": [
            "{title} — an overview of {topic}.",
            "Here is what I need to know about {topic} for {title}.",
            "Gathering everything on {topic} in one place.",
        ],
        "body": [
            "{kw2} only makes sense once {kw} is clear.",
            "Things I keep forgetting: {kw}, and why {kw2} matters.",
            "Comparing {kw} with {kw2} helped more than the slides did.",
            "Next steps: go through {kw} again, then try {kw2}.",
        ],
        "meaningful": [
            "Last date to hand in: {d}.",
            "{d} is the cut-off.",
            "The test is on {d}.",
            "We meet on {d}.",
            "Final version goes in on {d}.",
        ],
        "incidental": [
            "[{d}]",
            "Logged {d}.",
            "Copy made {d}.",
            "Source year {d}.",
        ],
        "people": ["Talked to {who} about it.", "Shared by {who}.", "cc {who}"],
        "code": '"""{title}."""\n\n\ndef run(path):\n    rows = read(path)  # {kw}\n    return fit(rows, strategy="{kw2}")  # {topic}\n',
    },
}


class TemplateProvider:
    """Deterministic content in a given template family."""

    name = "template-v1"

    def __init__(self, family: str) -> None:
        """Bind to a template family ("A" dev, "B" test)."""
        if family not in _FAMILY:
            raise ValueError(f"unknown template family {family!r}")
        self.family = family
        self.t = _FAMILY[family]

    def _sentences(self, plan: FilePlan, rng: random.Random, n: int) -> list[str]:
        kws = plan.keywords or [plan.topic or "the topic", "the basics"]
        out = []
        for _ in range(n):
            kw, kw2 = rng.sample(kws, 2) if len(kws) >= 2 else (kws[0], kws[0])
            out.append(
                rng.choice(self.t["body"]).format(
                    kw=kw, kw2=kw2, topic=plan.topic, title=plan.title
                )
            )
        return out

    def _dates(self, plan: FilePlan, rng: random.Random) -> list[str]:
        out = []
        for m in plan.dates:
            pool = self.t["meaningful"] if m.meaningful else self.t["incidental"]
            out.append(rng.choice(pool).format(d=m.surface))
        return out

    def payload(self, plan: FilePlan, persona: Persona, rng: random.Random):
        """Return the format-specific payload for ``plan`` (see datagen.writers)."""
        intro = rng.choice(self.t["intro"]).format(topic=plan.topic or "this", title=plan.title)
        body = self._sentences(plan, rng, rng.randint(3, 7))
        if plan.people:
            body.insert(
                rng.randrange(len(body) + 1),
                rng.choice(self.t["people"]).format(who=", ".join(plan.people)),
            )
        dates = self._dates(plan, rng)
        kws = plan.keywords or [plan.topic or "notes", "details"]
        kw, kw2 = (kws[0], kws[1 % len(kws)])
        k = plan.kind
        if k in {"txt", "md"}:
            head = f"# {plan.title}\n\n" if k == "md" else f"{plan.title}\n\n"
            return head + intro + "\n\n" + "\n".join(body + dates) + "\n"
        if k == "code":
            return self.t["code"].format(title=plan.title, topic=plan.topic, kw=kw, kw2=kw2)
        if k == "pdf":
            sections = [(plan.title, [intro, *body[:3]])]
            if len(body) > 3 or dates:
                sections.append(("Details", [*body[3:], *dates] or [intro]))
            return sections
        if k == "docx":
            return [
                ("h1", plan.title),
                ("p", intro),
                *[("p", s) for s in body],
                *[("bullet", d) for d in dates],
            ]
        if k == "pptx":
            slides = [(plan.title, [intro])]
            slides += [(f"{kw.title()}", body[:3]), ("Next steps", body[3:] or [intro])]
            if dates:
                slides.append(("Key dates", dates))
            return slides
        if k == "xlsx":
            rows = [["Item", "Detail", "Date"]]
            for i, m in enumerate(plan.dates):
                rows.append([f"{plan.title} #{i + 1}", m.why, m.surface])
            for s in body[:3]:
                rows.append([kw, s, ""])
            return [("Sheet1", rows)]
        if k in {"jpg", "zip"}:
            return None  # binary payloads are made by the materialiser
        return intro
