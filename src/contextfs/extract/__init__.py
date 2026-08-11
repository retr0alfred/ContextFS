"""Layer 2 - content extraction.

Dispatches a file to the right extractor by extension, applies the configured
character limit, and reports outcomes. Nothing here raises on a bad file: every
result is an :class:`~contextfs.extract.base.ExtractedDocument`, successful or
not, so an index build over a corpus containing one corrupt document still
completes and still tells the user which document was corrupt.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contextfs.config import ContextFSConfig
from contextfs.extract.base import ExtractedBlock, ExtractedDocument, truncate_to
from contextfs.extract.extractors import (
    extract_code,
    extract_csv,
    extract_docx,
    extract_image,
    extract_pdf,
    extract_pptx,
    extract_text,
    extract_xlsx,
)

__all__ = [
    "ExtractedBlock",
    "ExtractedDocument",
    "Extractor",
    "EXTRACTORS",
    "ExtractionReport",
    "extractor_for",
    "extract_file",
    "extract_many",
    "supported_extensions",
]

#: An extractor takes ``(absolute_path, relative_path)`` and returns a document.
Extractor = Callable[[Path, str], ExtractedDocument]

#: Extension to extractor. The single source of truth for format support;
#: ``docs/architecture.md`` and the README are generated understandings of it.
EXTRACTORS: dict[str, Extractor] = {
    # documents
    ".pdf": extract_pdf,
    ".docx": extract_docx,
    ".pptx": extract_pptx,
    ".xlsx": extract_xlsx,
    # plain text
    ".txt": extract_text,
    ".md": extract_text,
    ".markdown": extract_text,
    # structured text
    ".csv": extract_csv,
    # source code
    **dict.fromkeys(
        (
            ".py",
            ".js",
            ".ts",
            ".java",
            ".c",
            ".cpp",
            ".h",
            ".cs",
            ".go",
            ".rs",
            ".rb",
            ".sh",
            ".sql",
            ".html",
            ".css",
            ".json",
            ".yaml",
            ".yml",
        ),
        extract_code,
    ),
    # images: metadata only, no OCR
    **dict.fromkeys((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff"), extract_image),
}


def supported_extensions() -> list[str]:
    """Return every extension with a registered extractor, sorted."""
    return sorted(EXTRACTORS)


def extractor_for(path: Path | str) -> Extractor | None:
    """Return the extractor for a path's extension, or ``None`` if unsupported."""
    return EXTRACTORS.get(Path(path).suffix.lower())


def extract_file(
    path: Path,
    rel_path: str | None = None,
    *,
    config: ContextFSConfig | None = None,
    max_chars: int | None = None,
) -> ExtractedDocument:
    """Extract one file, applying the configured character limit.

    Args:
        path: Absolute path to the file.
        rel_path: Corpus-relative path. Defaults to the file name.
        config: Used for ``extraction.max_chars_per_document`` if ``max_chars``
            is not given directly.
        max_chars: Explicit character limit; 0 means unlimited.

    Returns:
        An :class:`ExtractedDocument`. ``ok`` is False for unsupported formats
        and for genuine failures; the two are distinguished by ``extractor``,
        which is ``"unsupported"`` in the former case.
    """
    path = Path(path)
    rel = rel_path if rel_path is not None else path.name

    extractor = extractor_for(path)
    if extractor is None:
        doc = ExtractedDocument(
            path=str(path),
            rel_path=rel,
            ext=path.suffix.lower(),
            extractor="unsupported",
            ok=False,
            error=f"no extractor registered for {path.suffix.lower() or '(no extension)'}",
        )
        return doc

    if not path.is_file():
        return ExtractedDocument.failed(path, rel, extractor.__name__, "file does not exist")

    started = time.perf_counter()
    try:
        doc = extractor(path, rel)
    except Exception as exc:  # noqa: BLE001 - the contract is "never raise"
        doc = ExtractedDocument.failed(
            path, rel, extractor.__name__, f"unhandled {type(exc).__name__}: {exc}"
        )
    doc.meta["extract_ms"] = round((time.perf_counter() - started) * 1000, 2)

    limit = (
        max_chars
        if max_chars is not None
        else (config.extraction.max_chars_per_document if config else 0)
    )
    if limit and doc.ok:
        doc.blocks, doc.truncated = truncate_to(doc.blocks, limit)
        if doc.truncated:
            doc.warnings.append(f"truncated to {limit} characters")

    return doc


@dataclass
class ExtractionReport:
    """Aggregate outcome of extracting a batch of files.

    Exists so that extraction success rate is a *reported number* rather than
    something a caller has to reconstruct - the Phase 5 verification requires it,
    and silently dropping failures is the specific failure mode being guarded
    against.
    """

    documents: list[ExtractedDocument] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def total(self) -> int:
        """Number of files attempted."""
        return len(self.documents)

    @property
    def succeeded(self) -> list[ExtractedDocument]:
        """Documents extracted successfully."""
        return [d for d in self.documents if d.ok]

    @property
    def failed(self) -> list[ExtractedDocument]:
        """Documents that could not be extracted."""
        return [d for d in self.documents if not d.ok]

    @property
    def unsupported(self) -> list[ExtractedDocument]:
        """Files with no registered extractor (a subset of :attr:`failed`)."""
        return [d for d in self.documents if d.extractor == "unsupported"]

    @property
    def genuine_failures(self) -> list[ExtractedDocument]:
        """Failures that are not merely unsupported formats."""
        return [d for d in self.documents if not d.ok and d.extractor != "unsupported"]

    @property
    def empty(self) -> list[ExtractedDocument]:
        """Documents that extracted cleanly but contained no text."""
        return [d for d in self.documents if d.is_empty]

    @property
    def with_warnings(self) -> list[ExtractedDocument]:
        """Documents that produced non-fatal warnings."""
        return [d for d in self.documents if d.warnings]

    @property
    def success_rate(self) -> float:
        """Fraction of attempted files extracted successfully."""
        return len(self.succeeded) / self.total if self.total else 0.0

    @property
    def total_chars(self) -> int:
        """Characters extracted across all successful documents."""
        return sum(d.char_count for d in self.succeeded)

    def by_extension(self) -> dict[str, tuple[int, int]]:
        """Return ``{ext: (succeeded, attempted)}``."""
        out: dict[str, list[int]] = {}
        for doc in self.documents:
            entry = out.setdefault(doc.ext, [0, 0])
            entry[1] += 1
            if doc.ok:
                entry[0] += 1
        return {ext: (ok, total) for ext, (ok, total) in sorted(out.items())}

    def summary(self) -> dict[str, Any]:
        """A flat, printable summary of the batch."""
        return {
            "attempted": self.total,
            "succeeded": len(self.succeeded),
            "failed": len(self.failed),
            "unsupported": len(self.unsupported),
            "genuine_failures": len(self.genuine_failures),
            "empty": len(self.empty),
            "with_warnings": len(self.with_warnings),
            "success_rate": round(self.success_rate, 4),
            "total_chars": self.total_chars,
            "tabular_documents": sum(1 for d in self.succeeded if d.has_tabular_content),
            "duration_ms": round(self.duration_ms, 2),
        }


def extract_many(
    items: Iterable[tuple[Path, str]],
    *,
    config: ContextFSConfig | None = None,
    on_progress: Callable[[int, ExtractedDocument], None] | None = None,
) -> ExtractionReport:
    """Extract a batch of ``(absolute_path, relative_path)`` pairs.

    Args:
        items: Files to extract.
        config: Supplies the character limit.
        on_progress: Called with ``(index, document)`` after each file.

    Returns:
        An :class:`ExtractionReport` covering the whole batch.
    """
    started = time.perf_counter()
    report = ExtractionReport()
    for index, (path, rel) in enumerate(items):
        doc = extract_file(path, rel, config=config)
        report.documents.append(doc)
        if on_progress:
            on_progress(index, doc)
    report.duration_ms = (time.perf_counter() - started) * 1000
    return report
