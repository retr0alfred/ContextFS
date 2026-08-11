"""ContextFS - a local-first, context-aware, time-intelligent file retrieval system.

ContextFS is a contextual memory layer that sits *above* an existing filesystem.
It never modifies the files it reads; all derived data (metadata, entities,
embeddings, graph, timeline, sessions) lives in ContextFS's own stores.

The package is organised along the layered architecture described in
``docs/architecture.md``::

    scanner    -> Layer 1  file discovery
    extract    -> Layer 2  content extraction
    entities   -> Layer 3  entity extraction
    embed      -> Layer 4  embedding generation
    tree       -> Layer 5  semantic tree
    graph      -> Layer 6  relationship graph
    temporal   -> Layer 7  temporal intelligence
    activity   -> Layer 8  activity sessions
    retrieval  -> Layer 9  hybrid retrieval + explanation
    cli        -> Layer 10 command line interface
    gui        -> Layer 11 desktop interface
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
