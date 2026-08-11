"""Deterministic synthetic corpus generation for ContextFS evaluation.

This subpackage exists so the evaluation corpus is *reproducible and
inspectable*, not a folder of files someone once made. Everything a reviewer
needs in order to regenerate the benchmark - the file contents, the folder
layout, the modification times, the session groupings, the labelled dates, and
the query set - is declared in :mod:`contextfs.datagen.corpus_spec` and
materialised by :mod:`contextfs.datagen.writers`.

It lives inside the package (rather than only in ``scripts/``) so that tests can
import the specification and assert properties of it directly.

**No file on the user's machine is ever read.** The corpus is authored from
scratch by this code.
"""

from contextfs.datagen.corpus_spec import (
    CORPUS_FILES,
    QUERIES,
    SESSIONS,
    DateLabel,
    FileSpec,
    QuerySpec,
    SessionSpec,
)

__all__ = [
    "CORPUS_FILES",
    "QUERIES",
    "SESSIONS",
    "DateLabel",
    "FileSpec",
    "QuerySpec",
    "SessionSpec",
]
