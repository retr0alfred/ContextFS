"""Summarisation for semantic-tree nodes (Layer 5 support).

Two backends, with a hard rule: **ContextFS must be fully functional without a
local LLM.** Ollama is optional; the extractive summariser is not.

That ordering is deliberate and is more than caution. On the development machine
Ollama is not installed at all, which means the extractive path is the
*default-tested* path rather than an untested fallback branch that only runs on
someone else's laptop. A fallback nobody exercises is not a fallback.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

__all__ = ["Summary", "extractive_summary", "OllamaSummarizer", "Summarizer"]

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n{2,}")
_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "if",
    "then",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "at",
    "by",
    "from",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "it",
    "its",
    "this",
    "that",
    "these",
    "those",
    "as",
    "not",
    "no",
    "so",
    "we",
    "i",
    "you",
    "they",
    "he",
    "she",
    "which",
    "what",
    "when",
    "where",
    "will",
    "would",
    "can",
    "could",
    "should",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "there",
    "their",
    "our",
    "your",
    "than",
    "into",
    "about",
}


@dataclass
class Summary:
    """A generated summary and the provenance needed to trust it."""

    text: str
    backend: str
    sentence_count: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        """Whether a usable summary was produced."""
        return bool(self.text.strip())


def _sentences(text: str) -> list[str]:
    """Split text into candidate sentences, dropping fragments."""
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(text) if part and part.strip()]
    return [part for part in parts if len(part.split()) >= 4]


#: Hard ceiling on a summary's length, in characters.
#:
#: Found necessary by a failing test: for a spreadsheet, "sentences" are table
#: rows with no terminal punctuation, so sentence-count limiting did not bound
#: anything and a "summary" came out at 856 characters for an 833-character
#: document - longer than its own source. A summary that is not shorter than
#: what it summarises is not a summary, and folder nodes built from such
#: summaries would grow without limit up the tree.
MAX_SUMMARY_CHARS = 400


def _clip(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    """Trim to ``limit`` characters at a word boundary, appending an ellipsis."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:|-")
    return cut + " ..."


def extractive_summary(
    text: str, *, max_sentences: int = 3, title: str = "", max_chars: int = MAX_SUMMARY_CHARS
) -> Summary:
    """Summarise by selecting the most representative sentences.

    A frequency-centrality method: score each sentence by the mean corpus
    frequency of its content words, with two adjustments that matter for
    personal documents:

    * a mild length normalisation, so a single long sentence stuffed with common
      words cannot dominate;
    * a bonus for sentences appearing early, because personal documents
      (meeting notes, briefs, READMEs) overwhelmingly state their purpose first.

    Selected sentences are returned **in document order**, not score order, so
    the summary reads as prose rather than as a ranked list.

    Args:
        text: Document or aggregated child text.
        max_sentences: How many sentences to keep.
        title: Optional heading prepended for context.
        max_chars: Hard ceiling on the summary's length. See
            :data:`MAX_SUMMARY_CHARS` for why a sentence count is not enough.

    Returns:
        A :class:`Summary` with ``backend="extractive"``.
    """
    sentences = _sentences(text)
    if not sentences:
        cleaned = _clip(" ".join(text.split()), max_chars)
        return Summary(text=cleaned, backend="extractive", sentence_count=1 if cleaned else 0)

    if len(sentences) <= max_sentences:
        chosen = sentences
    else:
        frequencies: Counter[str] = Counter()
        for sentence in sentences:
            for word in re.findall(r"[a-z][a-z'-]+", sentence.lower()):
                if word not in _STOPWORDS and len(word) > 2:
                    frequencies[word] += 1
        if not frequencies:
            chosen = sentences[:max_sentences]
        else:
            peak = max(frequencies.values())
            scored: list[tuple[float, int, str]] = []
            for position, sentence in enumerate(sentences):
                words = [
                    word
                    for word in re.findall(r"[a-z][a-z'-]+", sentence.lower())
                    if word not in _STOPWORDS and len(word) > 2
                ]
                if not words:
                    continue
                base = sum(frequencies[word] / peak for word in words) / (len(words) ** 0.5)
                position_bonus = 1.0 + (0.25 if position < 3 else 0.0)
                scored.append((base * position_bonus, position, sentence))
            scored.sort(key=lambda item: -item[0])
            picked = sorted(scored[:max_sentences], key=lambda item: item[1])
            chosen = [item[2] for item in picked] or sentences[:max_sentences]

    body = _clip(" ".join(" ".join(sentence.split()) for sentence in chosen), max_chars)
    if title:
        body = f"{title}: {body}"
    return Summary(text=body.strip(), backend="extractive", sentence_count=len(chosen))


class OllamaSummarizer:
    """Optional local-LLM summariser, strictly loopback-only.

    Never required. If the endpoint is unreachable the caller falls back to
    :func:`extractive_summary`, which is the tested default path.
    """

    def __init__(self, model: str, endpoint: str, timeout: int = 120) -> None:
        """Configure the client. No connection is made until :meth:`summarize`."""
        if not any(host in endpoint for host in ("127.0.0.1", "localhost", "::1")):
            raise ValueError(
                f"refusing non-loopback summarisation endpoint {endpoint!r}: "
                "ContextFS performs no remote inference"
            )
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    def available(self) -> bool:
        """Whether a local Ollama server is reachable right now."""
        import httpx

        try:
            response = httpx.get(f"{self.endpoint}/api/tags", timeout=2.0)
            return response.status_code == 200
        except Exception:  # noqa: BLE001 - any failure means "not available"
            return False

    def summarize(self, text: str, *, max_sentences: int = 3, title: str = "") -> Summary:
        """Summarise via a local Ollama model, or return a failed Summary."""
        import httpx

        prompt = (
            "Summarise the following personal-file content in at most "
            f"{max_sentences} sentences. State what it is about and what it is "
            "for. Do not add facts. Reply with the summary only.\n\n"
            f"{'Title: ' + title if title else ''}\n\n{text[:6000]}"
        )
        try:
            response = httpx.post(
                f"{self.endpoint}/api/generate",
                json={"model": self.model, "prompt": prompt, "stream": False},
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = (response.json().get("response") or "").strip()
        except Exception as exc:  # noqa: BLE001 - any failure means fall back
            return Summary(text="", backend="ollama", error=f"{type(exc).__name__}: {exc}")
        return Summary(text=body, backend="ollama", sentence_count=len(_sentences(body)))


class Summarizer:
    """Chooses the best available backend, preferring the local LLM if present."""

    def __init__(self, config) -> None:
        """Build a summariser from the resolved ContextFS configuration."""
        self.config = config
        self.max_sentences = 3
        self._ollama: OllamaSummarizer | None = None
        self._checked = False
        self.fallbacks = 0

    @property
    def ollama(self) -> OllamaSummarizer | None:
        """The Ollama client, if enabled and reachable. Checked once."""
        if self._checked:
            return self._ollama
        self._checked = True
        settings = self.config.summarization
        if settings.enabled and settings.backend == "ollama":
            client = OllamaSummarizer(settings.model, settings.endpoint, settings.timeout_seconds)
            self._ollama = client if client.available() else None
        return self._ollama

    @property
    def backend_name(self) -> str:
        """Which backend will actually be used."""
        return "ollama" if self.ollama else "extractive"

    def summarize(self, text: str, title: str = "") -> Summary:
        """Summarise text, falling back to extraction if the LLM is unavailable."""
        if not text.strip():
            return Summary(text="", backend=self.backend_name)
        client = self.ollama
        if client is not None:
            result = client.summarize(text, max_sentences=self.max_sentences, title=title)
            if result.ok:
                return result
            self.fallbacks += 1
        return extractive_summary(text, max_sentences=self.max_sentences, title=title)
