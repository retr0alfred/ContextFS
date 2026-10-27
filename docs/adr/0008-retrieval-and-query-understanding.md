# ADR-0008: Retrieval — shared lexical + dense first stage, ContextFS signals as features, open-vocabulary query understanding

- **Status:** Accepted · **Date:** 2026-10-27 · **Phase:** v2-P2
- **Debate record:** IR R1/R2; skeptic R1 #7–9, V9, V17; skeptic R2 N10, N12; log.md Decisions 90–91

## Context
v1 has no lexical channel, a dense-only "baseline", hand-tuned weights, and a closed vocabulary (16
format words, 21 activity phrases). The claim to defend: ContextFS beats **BM25 + dense +
cross-encoder rerank** on memory-cue queries without hurting content queries. On the smoke fixture
naive RRF (k=60) underperformed dense-only (Decision 91).

## Decision
1. **First stage, shared by every system:**
   - **Lexical:** SQLite FTS5 BM25 over chunk text (`unicode61 remove_diacritics 2`), plus a
     name/path table, plus a trigram index **for substrings only**. Trigram does not handle typos;
     measured: `gradeint` → 0 rows.
   - **Dense:** exact, document-level, up to 200 distinct documents (ADR-0005).
   - **P0 = union of the BM25 top-200 and the dense top-200** (≤ 400). No RRF, no cap. A test asserts
     that ranking P0 by one channel reproduces that channel's unpooled top-200 documents.
   - Baselines are also reported unpooled.
2. **Fusion is chosen on the dev split** from {dense-only, BM25-only, RRF k ∈ {10, 60}, z-score
   linear α}. Nothing is assumed.
3. **ContextFS signals are features over P0** (and over P1 = P0 ∪ session-mates of the top-20 ∪
   timeline hits, reported as its own row, and the first thing cut if behind):
   - BM25, dense, name match, format P(kind);
   - time overlap with the query's time interval (core and decay band), per ADR-0006 event;
   - session co-membership with high-scoring candidates;
   - link features, and `q_has_time × overlap`.
   - **≤ 12 features**, a **linear scorer** fitted by coordinate ascent on dev nDCG@10, stored as
     hashed JSON. LambdaMART is cut (C is engineering, Decision 92).
4. **Baselines** (v2-P3, same index and pools):
   - filename/path + mtime heuristic;
   - BM25;
   - dense (MiniLM, and granite as a row);
   - BM25 + dense fusion;
   - fusion + MiniLM cross-encoder rerank (eval-only);
   - optional local-LLM reranker (eval-only, LATER, P7).
   - The format-hint multiplier is its own ablation axis (Decision 90).
5. **Query understanding** (open vocabulary, local, never throws):
   - NFKC normalisation, 2,000-character cap;
   - **SymSpell** typo expansion over the index's own `fts5vocab` vocabulary (edit distance ≤ 2;
     the original term is kept);
   - an editable format lexicon giving a soft P(kind);
   - **time:** the existing regex resolver plus relational operators ("before/after/around X",
     "N weeks before X") plus **user-defined periods in Settings** ("sem 5 = Jul–Nov 2025"). Each
     resolves to a soft interval (core plus decay band).
   - Corpus-event anchors, festival tables and `dateparser` are LATER (direction B, only if time
     allows after A).
   - Every stage is guarded and degrades to plain BM25 + dense.
6. **The test queries are written blind:**
   - generated from persona and target descriptions by a **different LLM family** from anything
     the retriever uses, with no template lexicon;
   - real-participant queries (v2-P3 protocol) form a second test set;
   - the primary metric is pre-registered: blind-query nDCG@10, full vs the best baseline.
7. Dependency release pins must be at least two weeks old (SymSpell and ORT included, skeptic N8).

## Consequences
- The paper's comparison is fair by construction: same index, same pool, same first stage.
- **The regression anchor:** the new engine configured as v1 must reproduce `dense_flat` 0.503 on
  the smoke fixture, or the difference must be explained.

## Objections
- **IR (R1):** RRF pool builder, LambdaMART, gte reranker. *Withdrawn in R2* after skeptic attacks.
- **Skeptic (R2 #8):** the student is not blind. *Addressed* by LLM-family separation and real
  participants.

## How we will know it was wrong
P0 recall on dev is below 90% for any query kind; the linear scorer does not beat the best fusion
baseline on dev; or blind-query results contradict template-query results.

## Revision 1 — after the fresh review (2026-10-27)
Supersedes §1 and §6 where they conflict. Source: `review/fresh_review.md` B2, M5, m5.
- **BM25 is tunable** (M5).
  - FTS5's built-in `bm25()` hard-codes k1 = 1.2 and b = 0.75.
  - So FTS5 generates the top-1,000 lexical candidates, and the **lexical score** is re-computed in
    Python with tunable k1/b from `fts5vocab` statistics and per-document term frequencies.
  - BM25 is **document-level** (one FTS row per document's text); the chunk FTS table is used for
    evidence spans only.
  - k1, b and the fusion weights are tuned on dev **with the same search budget as the ContextFS
    scorer**. The tuning budget per system is reported.
- **Dense:** MaxP for every system (ADR-0005 Rev. 1).
- **Primaries:** three pre-registered primaries with Holm correction (ADR-0015 §7). The system
  primary P-S is no longer the only one.
- **Regression anchor (m5):** the v1 configuration must reproduce `dense_flat` MRR **0.5029 ±
  0.0005** on the smoke fixture. A miss is a blocker, not "explained". MRR is used for
  single-target queries and nDCG@10 for graded ones.
