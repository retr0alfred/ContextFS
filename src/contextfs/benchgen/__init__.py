"""Procedurally generated, multi-persona benchmark for memory-cue re-finding (ADR-0015).

Pipeline: ``simulate`` (persona → timeline → file plans with true per-event times) →
``content`` (text for each plan, by template family) → ``materialise`` (real files, consistent
in-file metadata, all three NTFS times, sealed truth manifest outside the corpus) → ``damage``
(timestamp damage that follows the measured transfer table T) → ``queries``.

Contract (ADR-0015 §10): nothing here imports ``contextfs.engine`` or any retriever. The
generator never asks a retrieval system which targets or distractors to pick.
"""

from contextfs.benchgen.model import Activity, FilePlan, Persona

__all__ = ["Activity", "FilePlan", "Persona"]
