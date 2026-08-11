"""Common schema for content extraction (Layer 2).

Two design commitments shape everything here.

**Errors are captured, never thrown.** One corrupt PDF must not abort an index
build over thousands of files. Every extractor returns an
:class:`ExtractedDocument` whose ``ok`` flag and ``error`` string record what
happened. Nothing is dropped silently: a failure is a row in the database and a
line in the extraction report.

**Structure survives extraction.** Flattening a spreadsheet to a paragraph would
destroy the single most useful signal the temporal layer has - that a date
appeared *inside a table*. So extraction produces a list of
:class:`ExtractedBlock` objects, each tagged with its origin (page, sheet, slide,
section) and, critically, whether it is tabular. Phase 10's structured-context
signal reads that flag directly. This is why extraction is not simply
"get the text out".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "ExtractedBlock",
    "ExtractedDocument",
    "BlockKind",
    "truncate_to",
]

#: Origin of a block within its source document.
BlockKind = str  # one of: page, sheet, slide, section, paragraph, notes, sheet_meta


@dataclass
class ExtractedBlock:
    """One structurally meaningful unit of a document.

    Attributes:
        index: Zero-based position within the document.
        kind: Where it came from - ``page``, ``sheet``, ``slide``, ``section``,
            ``paragraph``, or ``notes``.
        label: Human-readable identifier, e.g. ``"page 3"`` or ``"sheet: Timetable"``.
        text: The block's plain text.
        is_tabular: True when the text originated from a genuine tabular
            structure (spreadsheet rows, a DOCX table). **Read directly by the
            Phase 10 structured-context signal** - a date inside a timetable is
            far more likely to be a commitment than one in running prose.
        is_heading: True for headings and slide titles; used by the extractive
            summariser in Phase 8.
        row_count: For tabular blocks, the number of rows represented.
    """

    index: int
    kind: BlockKind
    label: str
    text: str
    is_tabular: bool = False
    is_heading: bool = False
    row_count: int = 0

    @property
    def char_count(self) -> int:
        """Length of this block's text."""
        return len(self.text)


@dataclass
class ExtractedDocument:
    """Normalised output of any extractor.

    Attributes:
        path: Absolute path of the source file.
        rel_path: Path relative to the scan root, matching ``files.path``.
        ext: Lowercased file extension.
        extractor: Name of the extractor that produced this.
        ok: Whether extraction succeeded. False still yields a usable object.
        error: Failure reason when ``ok`` is False; empty otherwise.
        warnings: Non-fatal problems (e.g. "3 of 12 pages yielded no text").
        blocks: Structural units, in document order.
        meta: Format-specific metadata (page count, sheet names, author, ...).
        truncated: Whether the text was cut at the configured character limit.
    """

    path: str
    rel_path: str
    ext: str
    extractor: str
    ok: bool = True
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    blocks: list[ExtractedBlock] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    truncated: bool = False

    # -- derived views -----------------------------------------------------

    @property
    def text(self) -> str:
        """All block text joined in document order."""
        return "\n\n".join(block.text for block in self.blocks if block.text)

    @property
    def char_count(self) -> int:
        """Total extracted characters."""
        return sum(block.char_count for block in self.blocks)

    @property
    def word_count(self) -> int:
        """Approximate word count over the extracted text."""
        return len(self.text.split())

    @property
    def block_count(self) -> int:
        """Number of structural blocks."""
        return len(self.blocks)

    @property
    def has_tabular_content(self) -> bool:
        """Whether any block came from a table or spreadsheet."""
        return any(block.is_tabular for block in self.blocks)

    @property
    def is_empty(self) -> bool:
        """True when extraction succeeded but produced no usable text."""
        return self.ok and self.char_count == 0

    def tabular_spans(self) -> list[tuple[int, int]]:
        """Return ``(start, end)`` character offsets of tabular regions.

        Offsets index into :attr:`text`. Phase 10 uses these to decide whether a
        date mention at a given position sits inside structured content, without
        needing to re-parse the source file.
        """
        spans: list[tuple[int, int]] = []
        cursor = 0
        for block in self.blocks:
            if not block.text:
                continue
            start = cursor
            end = start + len(block.text)
            if block.is_tabular:
                spans.append((start, end))
            cursor = end + 2  # the "\n\n" join separator
        return spans

    def block_at(self, offset: int) -> ExtractedBlock | None:
        """Return the block containing a character offset into :attr:`text`."""
        cursor = 0
        for block in self.blocks:
            if not block.text:
                continue
            end = cursor + len(block.text)
            if cursor <= offset < end:
                return block
            cursor = end + 2
        return None

    def summary(self) -> dict[str, Any]:
        """A flat, printable description of this extraction."""
        return {
            "path": self.rel_path,
            "ext": self.ext,
            "extractor": self.extractor,
            "ok": self.ok,
            "blocks": self.block_count,
            "chars": self.char_count,
            "words": self.word_count,
            "tabular": self.has_tabular_content,
            "truncated": self.truncated,
            "error": self.error,
            "warnings": len(self.warnings),
        }

    @classmethod
    def failed(cls, path: Path, rel_path: str, extractor: str, error: str) -> ExtractedDocument:
        """Build a failure result. Used instead of raising."""
        return cls(
            path=str(path),
            rel_path=rel_path,
            ext=path.suffix.lower(),
            extractor=extractor,
            ok=False,
            error=error,
        )


def truncate_to(blocks: list[ExtractedBlock], limit: int) -> tuple[list[ExtractedBlock], bool]:
    """Trim a block list so its total character count stays under ``limit``.

    Truncation is at block boundaries wherever possible, so a partially included
    document still consists of whole pages or sheets rather than a sentence cut
    mid-word. A limit of 0 or less means unlimited.

    Args:
        blocks: Blocks in document order.
        limit: Maximum total characters, or 0 for no limit.

    Returns:
        ``(kept_blocks, was_truncated)``.
    """
    if limit <= 0:
        return blocks, False

    kept: list[ExtractedBlock] = []
    total = 0
    for block in blocks:
        if total + block.char_count <= limit:
            kept.append(block)
            total += block.char_count
            continue
        remaining = limit - total
        if remaining > 200:  # a fragment this small is not worth keeping
            kept.append(
                ExtractedBlock(
                    index=block.index,
                    kind=block.kind,
                    label=block.label + " (truncated)",
                    text=block.text[:remaining],
                    is_tabular=block.is_tabular,
                    is_heading=block.is_heading,
                    row_count=block.row_count,
                )
            )
        return kept, True
    return kept, False
