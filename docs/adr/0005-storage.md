# ADR-0005: Storage — SQLite is the single commit point; exact f32 vector segments; no stored semantic graph

- **Status:** Accepted · **Date:** 2026-10-27 · **Phase:** v2-P2
- **Debate record:** architect R1/R2, performance R1/R2, IR R2, skeptic R2 (N6, N9, N10, N13, D2, D6)

## Context
v1 used three stores: SQLite, LanceDB (brute force, no ANN), and a NetworkX graph saved as one JSON
file and rebuilt every scan. That does not scale, does not commit atomically, and differs between
eval and app if an ANN is added.

Measured on the dev machine (exact numpy top-200, 384-d f32):

| Vectors | p50 latency | RAM |
|---|---|---|
| 10⁵ | 6.3–7.4 ms | 146 MB |
| 3×10⁵ | 19.2 ms | — |
| 10⁶ | 84 ms | 1.46 GB |

f16 storage was about 10× slower (performance R2, skeptic R2).

## Decision
1. **SQLite (WAL) holds everything except vector bytes** and is the only commit point. It stores:
   - the catalog, `stage_state` memoisation, chunks and text;
   - the FTS5 tables (ADR-0008);
   - entities, date mentions and classified dates;
   - `time_evidence` and `file_time` (ADR-0006);
   - sessions, settings and the schema version.
2. **Vectors are exact f32, stored outside SQLite and registered in it.**
   - Storage: one growing *tail* segment plus sealed segments (`seg-N.f32` + `.ids`, memory-mapped).
     A segment is fsync'd before it is registered in SQLite, and readers map only the row count in
     their snapshot.
   - Deletes are masks.
   - Compaction runs at idle and swaps the manifest. Old files are deleted only after the UI process
     acknowledges it has unmapped them; Windows cannot delete mapped files (skeptic N13).
3. **Dense first stage is document-level** (skeptic D2):
   - one pooled vector per document is searched exactly;
   - chunk vectors are kept and used only to re-score candidates and pick the evidence chunk;
   - dense retrieves until **200 distinct documents** (skeptic N10).
   - This bounds resident RAM to roughly 190 MB per 1.25×10⁵ text documents. Scale claims are
     limited to what Phase 7 measures.
4. **Graph: nothing semantic is stored.**
   - Neighbours of the top seeds come from one batched exact matmul at query time.
   - Entity, folder, session and timeline links are SQL lookups.
   - Near-duplicate edges are **cut from the MVP** (skeptic D6) and come back LATER with
     version-lineage.
5. **Dropped:** LanceDB, pyarrow, the NetworkX JSON graph, and the semantic tree.
6. **The index location** is `%LOCALAPPDATA%\ContextFS\index` by default, and is configurable (env
   var and Settings). It is **refused if inside a scanned root or on a network share**. The dev
   machine keeps it on D:.

## Consequences
- Eval, CLI and app run the same exact search. There is no ANN-recall confound.
- Above about 3×10⁵ resident chunk vectors on 8 GB, chunk re-scoring pages from disk. ANN behind a
  `VectorIndex` interface is LATER, as its own evaluation row.
- The 3D graph export (CLI, LATER) builds its graph on demand from SQLite.

## Objections
- **Performance (R1):** usearch HNSW. *Conceded in R2* (parity, measured exact latency is enough).
- **Architect (R1):** LanceDB. *Conceded in R2.*
- **Performance (R1):** stored kNN edges. *Conceded*; revisit if p95 expansion exceeds 150 ms.
- **IR:** chunk-level first stage. *Overruled* (D2) for the 8 GB bound. Chunks are used for
  re-scoring and evidence.

## How we will know it was wrong
Dense-search p95 > 250 ms at 10⁵ files (M-Q1); compaction leaks files; or document-level recall on
dev is materially below chunk-level recall (P3 pool-recall report).
