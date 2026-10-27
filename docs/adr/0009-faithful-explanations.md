# ADR-0009: Faithful explanations — signed contributions against frozen absent values, evidence required

- **Status:** Accepted · **Date:** 2026-10-27 · **Phase:** v2-P2
- **Debate record:** IR R2 §1.5, UX R2 §1.5, skeptic R1 #9, R2 N12; v1 audit finding
  (`matched_topic` cited query words absent from the file)

## Decision
1. **Contribution** of feature group g: cᵢ = s(x) − s(x with g set to aᵢ).
   - For the linear scorer this is exactly wᵢ(xᵢ − aᵢ), and s(x) = s(a) + Σcᵢ.
   - The **absent values a are frozen in the scorer JSON before any test run**: evidence features
     are absent at 0; similarity features are absent at the query's pool median.
2. **Every reason needs a `doc_ref`:** a chunk span, a metadata field, or a `time_evidence` row.
   **No reason may echo the query.** v1's `matched_topic` behaviour is removed.
3. **Why-line gate:**
   - name only groups whose removal costs **at least one rank**, recomputed on the same pool;
   - otherwise show "Ranked mostly on wording; no single piece of evidence decides it";
   - negatives appear as "counted against".
4. **How a file entered the pool** (BM25, dense, or expansion such as "worked on together with X")
   is shown separately. It is a gate, not an additive term.
5. **Chip removal is a re-search, not a counterfactual** (skeptic N12). Removing a chip can change
   the pool. It is labelled as a re-search and tested separately from the fixed-pool counterfactual.
6. **Metrics (paper):**
   - counterfactual rank drop (Comprehensiveness@10);
   - uncited score mass;
   - claim verification (every cited span exists in the file, and every cited time row exists):
     **must be 100%**;
   - the check Σcᵢ = s(x) − s(a).
   - v1's "explanation coverage" is retired, because it was true by construction.

## Consequences
Explanations get shorter and true. The flagship v1 example ("topic match: … exam" for a file with no
exam vocabulary) becomes impossible.

## Objections
- **UX (R1):** "non-zero contribution" display. *Replaced* by signed, reference-based contributions.

## How we will know it was wrong
Claim verification < 100% on any dev query, or users in the study misread the signed bars.
