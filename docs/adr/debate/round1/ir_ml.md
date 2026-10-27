# Round 1 · IR/ML Researcher

## 1. Position summary

- **One shared first stage.** SQLite FTS5 BM25 (a content table plus a trigram name/path table) and dense chunk retrieval are fused by RRF into a candidate pool **P0**. Every baseline and ContextFS ranks the same P0. ContextFS expansion produces **P1 = P0 ∪ context candidates**, which we report as a separate row.
- **ContextFS becomes features.** Graph, sessions, timeline, multi-source time evidence and format become per-(query, doc) features with a pluggable scorer. The shipped default is linear, with weights fitted on dev. LambdaMART is a research row.
- **ONNX Runtime with no torch.** The default embedder is `granite-embedding-97m-multilingual-r2`. No LLM ships. The cross-encoder is an opt-in feature on CPU.
- **Query understanding is grounded in the corpus.** Entities, typos and event anchors resolve against the user's own vocabulary and timeline. Time is a soft, weighted interval set. Every stage degrades and never throws.
- **Faithful explanations; locked evaluation.** Reasons cite document-side evidence, scored by counterfactual rank drop. The harness drives `engine.search` via a frozen `SearchConfig`.

## 2. Proposals

### 2.1 First stage, fusion, pool

**Decision.**
- **Lexical.** FTS5 inside `store.db`:
  - `fts_content`: title/headings/body, tokenised with `unicode61 remove_diacritics 2`, with per-column `bm25()` weights.
  - `fts_name`: filename, path parts and corpus entity names, using the `trigram` tokenizer for substring and typo matching.

  `snippet()`/`highlight()` give snippets. Updates share the file row's transaction, so there is no second index to drift.
- **Dense.** Chunk vectors stored int8/PQ on disk; the ANN choice belongs to the systems role. A document scores its best chunk, and that chunk is kept as evidence. Requirement: ANN recall@200 ≥ 0.95 against exact search.
- **Fusion.** RRF with k = 60 over BM25 top-200 and dense top-200. P0 is the union, capped at 300.
- **Expansion.** Session-mates and 1-hop graph neighbours of the top-20 P0 items, plus timeline hits, adding at most 200 candidates. Each is flagged `origin=expansion`.

**Alternatives.** LanceDB FTS (a second store, scoring undocumented); `bm25s` (rebuild-oriented); linear blending (scale-dependent); dense-only (misses names).

**Trade-offs.** No stemming keeps FTS multilingual. RRF drops magnitudes; the scorer gets raw scores as features.

**What would change my mind.** FTS5 query p95 above 150 ms at 10⁶ files.

**Measurement.** Pool recall of P0 and P1 at 10², 10⁴ and 10⁵ files, with BM25-only, dense-only and RRF ablations.

### 2.2 Features and scorer

**Features.** Each group is switched off by a mask at scoring time.
- **First stage:** BM25 score/rank, name-trigram score, dense max-chunk cosine/rank, RRF.
- **Format:** P(kind | query) × document kind.
- **Time:** overlap of the query interval distribution with the *reconciled* time, with each source (fs times, `docProps`, PDF Info/XMP, EXIF, zip) and with meaningful content dates. Also: rewritten-fs-time confidence, recency.
- **Session:** shares a session with top-k P0 items; coverage; session span overlaps the query time.
- **Graph:** maximum edge weight to top-k P0 items, by edge type.
- **Reranker:** optional cross-encoder score.
- **Interactions:** for example `q_has_time × time_overlap`. These replace the hand-coded `_adaptive_weights` gating.

**Decision.** The shipped scorer is **linear**, fitted by coordinate ascent on nDCG@10 over the dev split and stored as JSON. Its contributions are exact. **LambdaMART** (LightGBM `lambdarank`, text model, additive `pred_contrib` attributions) is evaluated as `contextfs-ltr`.

**Alternatives.** Keep the hand weights (tuned on the 17-query set, so not test-clean); LambdaMART by default.

**Trade-offs.** Linear needs no dependency and is auditable, but is weaker on interactions.

**What would change my mind.** LTR beats linear by ≥ 0.02 nDCG@10 on the test personas, with a paired-bootstrap CI excluding 0, and no faithfulness loss.

**Measurement.** Per-kind nDCG@10, MRR and R@100.

### 2.3 Models and memory

| Profile | Embedder | Reranker | LLM |
|---|---|---|---|
| CPU default (8 GB) | granite-97m-multilingual-r2, int8 ONNX, 384-d | off; opt-in `ms-marco-MiniLM-L6-v2` (22.7M, Apache-2.0, English), top-30 | none |
| GPU / 16 GB | opt-in granite-311m-multilingual-r2 (Matryoshka) | `gte-multilingual-reranker-base` (306M, Apache-2.0, 70+ languages), top-50 | none |
| Eval-only | MiniLM-L6 ("current"), granite-311m ("stronger") | both | Qwen3-0.6B/1.7B GGUF (Apache-2.0) reranker baseline |

**Why granite-97m.** Apache-2.0; best under 100M on MTEB multilingual retrieval (60.3); 52 retrieval languages including Hindi, Bengali, Marathi, Telugu and Urdu; 32k context (no 256-token cap); 384-d like today; about 98 MB quantised ONNX.

**Runtime and memory.**
- Runtime is ONNX Runtime CPU (a 14.9 MB wheel). torch (2.9 GB) leaves the shipped app.
- GPU through an ONNX Runtime EP or WinML; DirectML is in sustained engineering.
- 8 GB budget: embedder plus ORT at about 250 MB, an optional cross-encoder at about 100 MB, and the FTS and vectors mmapped. The query path holds **no corpus-sized arrays**; today's per-query `document_vectors()` load goes.
- The index records its embedder id. Changing model triggers a background re-embed.

**Alternatives.** EmbeddingGemma (308M; slower); `potion-multilingual-128M` static embeddings (MIT, very fast; kept as fallback tier); bge-reranker-v2-m3 (568M; too heavy for CPU); a shipped LLM (large, non-deterministic, no document-side faithfulness).

**What would change my mind.**
- granite-97m below about 40 chunks/s on 4 cores → potion first pass plus a transformer back-fill.
- CPU cross-encoder at p95 under 120 ms and at least +0.02 nDCG → on by default.

**Measurement.** On the target CPU: chunks/s, p50/p95 latency and peak RSS against nDCG, for all three embedders.

### 2.4 Open-vocabulary query understanding

`parse(text, as_of, snapshot) -> QueryFrame`. Each stage runs inside a guard with a time budget. A failed stage records "could not interpret X" and is skipped. The full raw text always reaches both channels.

1. **Normalise.** NFKC, casefold, strip control characters, cap at 2,000 characters.
2. **Typos.** The trigram `fts_name` table, plus SymSpell (`symspellpy`, MIT) over the **corpus's own vocabulary** (from `fts5vocab`) as OR-expansions that keep the original term. There is no global corrector, because one would rewrite names.
3. **Entities.** Query n-grams are matched against the corpus entity dictionary via `fts_name`. This works in any script. spaCy query NER is dropped from the query path.
4. **Format.** An editable, multilingual lexicon data file, plus the query's cosine to per-kind prototype descriptions. The output is a soft P(kind), which replaces the hard 0.70 multiplier.
5. **Time.** Weighted intervals with fuzzy edges:
   - `dateparser.search_dates` (BSD-3, 200+ locales) with languages pinned from settings, cross-checked by the existing regex resolver, since dateparser's search mode is documented as limited.
   - Relational grammar: "around X" means X ± w; "before/after X" means a half-open interval with decay; "N weeks before X" shifts the anchor.
   - Anchors, in order:
     - a bundled festival table (Diwali is lunisolar, so it needs per-year data; `holidays` is MIT, or a shipped CSV);
     - user-defined periods in Settings ("sem 5 = Jul–Nov 2025");
     - **corpus events**: BM25 over timeline date-context surfaces ("ML exam"). "Last sem" is inferred from term-keyword date clusters.
   - The year is chosen by evidence, as `resolve_best` does today.
   - An unresolved anchor adds no feature and never empties results.
6. **Activity.** "Activity" queries are anchored-time queries. The 21-phrase list becomes grammar relations plus `anchor_strength`, which weights the session features.

**Alternatives.** Local LLM parser (latency, non-determinism, size); GLiNER (multi models about 0.5–1 GB; index-time option at most); longer closed lists.

**What would change my mind.** If corpus anchors resolve under 70% of the benchmark's anchored queries, test a 0.6B LLM anchor extractor, eval-only.

**Measurement.**
- Time tagging P/R and interval IoU against gold intervals, including Hinglish and Hindi templates.
- Fuzz test over 10⁵ random Unicode, empty and 100 kB queries: zero exceptions and p99 parse time under 30 ms.

### 2.5 Explanation faithfulness

**Decision.**
- Every reason is an `Evidence{feature, contribution, doc_ref, query_ref}`.
- `doc_ref` must be a chunk span (file, block, character offsets) or a metadata field with its source, for example `docProps/core.xml:created=2025-09-03 (fs mtime rewritten by copy)`.
- Reasons with no document reference are banned. Today's `matched_topic=[query]` only echoes the query and would fail.
- Only contributions above ε are shown, ordered by contribution.
- Expansion candidates also show **how they entered the pool**.

**Metrics.** All computed offline over cached features.
- **Counterfactual rank drop:** set the cited features to "absent", re-score within the same pool, and record Δrank. Comprehensiveness@10 is the share of results that drop at least one rank, in the style of ERASER.
- **Sufficiency:** rank using the cited features only.
- **Uncited mass:** the share of score from features that are not shown.
- **Claim verification:** each cited span or date is re-read from the index.

Targets: 100% claim verification and uncited mass under 10%. "Absent" values are frozen before any results are seen.

### 2.6 Evaluation hooks

- **One path.** `engine.search(query, config: SearchConfig, as_of, trace)`.
  - `SearchConfig` is frozen and hashed: channels, RRF k, pool caps, feature mask, scorer file, reranker, embedder id.
  - Each baseline is a named config. Path+mtime is BM25 on `fts_name` plus recency.
  - The GUI and CLI use the default config. A test asserts byte-identical rankings across GUI, CLI and harness.
- **Ablation without rebuilding.** `trace=True` returns the pool and the feature matrix, cached as Parquet keyed by (corpus hash, query, first-stage hash). Ablations re-score offline. A test asserts offline re-scoring equals online.
- **Determinism.** `as_of` is injected (no `date.today()` in the engine). Ties break by `(score desc, file_id asc)`. ORT threads are fixed. Evaluation uses exact search or a seeded ANN build.
- **Never tune on test.**
  - Splits are **by persona seed**, with generator templates also held out.
  - `fit()` and sweeps reject test seeds.
  - Test runs require `--final` and are logged (git SHA, config hash). The number of test runs is reported.
  - The legacy 17 queries and today's hand weights become dev-only.
- **Statistics.** Paired bootstrap CIs per query kind; pool recall beside every metric.

## 3. Positions on other questions

- **Process:** background indexer; search never waits, runs on the last committed WAL snapshot, shows "updating (n left)". An app-lifetime watcher is consistent with "no background service".
- **Packaging:** no torch. Bundle the int8 embedder for offline first run. GPU support is a runtime EP choice, not a CUDA bundle.
- **Scale:** sublinear query path; time-window session blocking.

## 4. Risks in my proposal

- granite-97m has only H100 throughput figures; the potion fallback costs quality.
- A scorer fitted on synthetic personas may learn generator artefacts; mitigate with template hold-out and a real-query sanity set.
- Anchors can confidently pick the wrong interval; soft time and shown interpretations limit damage.
- Unstemmed FTS may cost English recall.
- The counterfactual metric depends on the definition of "absent".
- Corpus SymSpell can expand toward a frequent wrong term.

## 5. Sources

- FTS5: https://www.sqlite.org/fts5.html
- RRF k=60: https://docs.lancedb.com/reranking/rrf
- bm25s: https://arxiv.org/html/2407.03618v1
- Granite R2: https://huggingface.co/blog/ibm-granite/granite-embedding-multilingual-r2 and https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2
- EmbeddingGemma: https://ai.google.dev/gemma/docs/embeddinggemma
- Model2Vec/potion: https://huggingface.co/minishlab/potion-multilingual-128m
- ms-marco-MiniLM-L6-v2: https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2
- gte-multilingual-reranker-base: https://huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base
- bge-reranker-v2-m3: https://www.promptlayer.com/models/bge-reranker-v2-m3
- onnxruntime 1.31.1: https://pypi.org/project/onnxruntime/
- DirectML/WinML: https://learn.microsoft.com/lt-lt/windows/ai/directml/dml-min-app
- Qwen3-0.6B GGUF: https://huggingface.co/bartowski/Qwen_Qwen3-0.6B-GGUF
- GLiNER: https://pypi.org/project/gliner and https://huggingface.co/jugaadsrl/gliner2.5-multi-v1-onnx
- dateparser: https://github.com/scrapinghub/dateparser
- holidays: https://python-holidays.readthedocs.io/
- symspellpy: https://pypi.org/project/symspellpy/
- LightGBM: https://lightgbm.readthedocs.io/en/v4.4.0/Parameters.html and https://lightgbm.readthedocs.io/en/latest/pythonapi/lightgbm.Booster.html
- ERASER: https://preview.aclanthology.org/author-url/2020.acl-main.408/
