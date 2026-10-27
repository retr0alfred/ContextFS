# ADR-0003: Inference runtime — ONNX Runtime in the app, MiniLM int8 default, GPU only for bulk indexing behind parity gates

- **Status:** Accepted (deviation acknowledged by the user 2026-10-27, Decision 95)
- **Date:** 2026-10-27 · **Phase:** v2-P2
- **Debate record:** IR R1/R2, packaging R1/R2, performance R1/R2, skeptic R1 (claims V1–V17),
  skeptic R2 (N6, N7, N8, N16, D3, D9)

## Context
- torch 2.14.1+cu130 is 2.9 GB installed, 2,425 MB of it CUDA libraries (BRIEF §7).
- The user's brief requires that the shipped app runs with no GPU, detects a GPU and uses it, and
  falls back to CPU cleanly. It also asks for a model choice in Settings.
- The debate showed that results are reproducible across the app, the CLI and evaluation only if
  all three run the **same runtime, model file, precision, execution provider and index**.

## Options considered
| Option | For | Against |
|---|---|---|
| torch (CPU and CUDA builds) in the app | Same as dev | GBs; CUDA build per vendor; slow import |
| **`onnxruntime` CPU EP** | ~25 MB package; fast start; one wheel for app and eval | No GPU |
| `onnxruntime-directml` (CPU + DirectML EPs, one wheel) | Any-vendor GPU, with a CPU EP in the same wheel | Lags the CPU package (1.24.4 vs 1.31.1); DML vectors differ in float noise; DirectML is in maintenance |
| `onnxruntime-gpu` (CUDA EP) | Fast on NVIDIA | Needs CUDA + cuDNN (GBs); NVIDIA-only |
| Windows ML | OS-provided EPs | Requires the Windows App SDK; immature for this use |

## Decision
1. **The app and evaluation use ONNX Runtime only.**
   - torch is a **dev and build-time** dependency: ONNX export, the sentence-transformers export
     parity check, and optional dev-only experiments.
   - One pinned ORT wheel, a release at least two weeks old (skeptic N8), is used by the venv, the
     frozen build and evaluation.
   - ORT intra-op threads, inter-op threads and BLAS threads are pinned inside `Engine.open`
     (skeptic N6).
2. **Default embedder: `all-MiniLM-L6-v2`, int8 ONNX** (skeptic D3). It matches v1 (the regression
   anchor), embeds about 2.5–3× faster than granite-97m, and multilinguality is not a paper claim.
   - `granite-embedding-97m-multilingual-r2` is an **evaluation row**.
   - It becomes the default only if the P3 week-1 gate passes: ≥ 100 chunks/s on 4 threads **and**
     dev nDCG@10 ≥ MiniLM's.
   - **Export parity:** ONNX vs sentence-transformers cosine ≥ 0.9999 on 500 texts.
3. **Queries always run on the CPU EP** in the app process. A query is one short text, so a GPU buys
   nothing.
4. **GPU for bulk indexing in the indexer child (requirement retained).**
   - The requirement stays: detect a GPU and use it for bulk embedding, with Settings
     "GPU: Auto / Off", and fall back to CPU on any failure.
   - **v2-P4 week 1 runs a GPU spike** with `onnxruntime-directml` as the single wheel. It must
     pass three gates (packaging R2):
     - (a) cosine ≥ 0.999 vs CPU-EP vectors on 2,000 chunks;
     - (b) top-10 identical on ≥ 99% of dev queries when a DML-built index is queried on the CPU EP;
     - (c) ≥ 2× bulk embedding speed vs the CPU EP on the dev GPU.
   - **If all three pass:** that wheel becomes the single wheel for app **and** evaluation. Indexes
     are built with it, and the evaluation reports on the GPU-built index plus a CPU-built control
     row.
   - **If any gate fails:** the app ships CPU-only and the GPU requirement becomes a logged
     deviation (see below).
   - The CUDA EP, `onnxruntime-gpu` and Windows ML are **cut** (size, vendor lock-in).
5. **Model choice in Settings:** MVP ships one embedder (MiniLM). The setting exists as a
   read-only display. A second shipped model is LATER, and switching models requires a full
   re-embed.
6. **No rerankers or LLM in the app.** The MiniLM cross-encoder and any local-LLM reranker are
   **evaluation baselines only** (ADR-0008).
7. **Cross-CPU note (skeptic N7):** int8 U8S8 results can differ between VNNI and AVX2 CPUs.
   Evaluation states its CPU, and Phase 7 reports top-10 overlap with the AVX2 laptop.

## Deviation from the user's brief — requires acknowledgement at the Phase 2 stop
- The brief asks for "detect GPU and use it". The debate (all five roles plus the skeptic)
  recommended deferring or cutting the GPU. **The facilitator did not accept a silent cut.** The GPU
  stays a requirement, delivered only through the gated DirectML spike.
- If the gates fail, the shipped app is CPU-only, and the user must approve that before v2-P4 closes.
- Also deferred: user-selectable models (one model ships in the MVP).

## Consequences
- The installer drops about 2.9 GB of torch and CUDA. Packaging estimates about 0.55 GB installed
  (ADR-0004).
- **The first real GPU workload of the project** will be the P4 DirectML spike or the P3 local-LLM
  query generation, whichever comes first. **It must be flagged to the user before it runs**
  (standing rule, log.md Phase 29).

## Objections
- **Skeptic (D9):** accept GPU and model choice as LATER/CUT. *Partly overruled:* the user's
  requirement is kept behind gates.
- **IR (R1):** granite as the default. *Overruled for the MVP* (D3); it stays a gated evaluation row.
- **Packaging (R1):** DirectML as the default EP. *Moved behind the gates* after the parity attacks.
- **Performance (R1):** CUDA EP option. *Cut* for size.

## How we will know it was wrong
- The DirectML gates fail on the dev GPU.
- Export parity is below 0.9999.
- Cross-CPU top-10 overlap is below 95% (the reported number would then not describe users'
  machines).

## Revision 1 — after the fresh review (2026-10-27)
Supersedes §2 and §4 where they conflict. Source: `review/fresh_review.md` M4, M5.
- **One precision per index.**
  - The embedder model file (including its precision) is part of `embedder_fp`, and queries always
    use **the same model file** as the index.
  - The GPU spike therefore gates the **fp32** model on DirectML vs the **fp32** model on the CPU EP
    (matched precision).
  - If a GPU-built index is adopted, that index and its queries use fp32. The CPU query cost of fp32
    is measured and must stay under 50 ms per query.
  - The int8 model is the CPU-only default. Switching precision means a re-embed (ADR-0016 §4).
- **The parity check runs on the user's device, not only on the dev GPU.**
  - On first GPU use, the child embeds a bundled 200-text probe on the GPU and on the CPU EP.
  - If mean cosine is below 0.999, it falls back to the CPU with a logged reason, and the probe
    result is stored in `embedder_fp`.
  - This covers AMD and Intel GPUs that were never tested in development.
- DirectML is in sustained-engineering (maintenance) mode. Recorded as a risk; it does not change
  the decision, because the gate and the probe make failure safe.
- **Evaluation bundle (M5).** Baselines that are not shipped (MiniLM cross-encoder, granite) are
  exported to ONNX into an **eval bundle** built from the same lockfile and ORT wheel as the app.
  Paper numbers for every system come from that frozen eval bundle.

## Status update — 2026-10-27 (log.md Decision 95)
The user **acknowledged** this ADR's deviation:
- GPU ships only if GPU-built results match CPU-built ones (the gates in Revision 1, plus the
  on-device probe);
- otherwise CPU-only, with the user's sign-off at that point;
- one embedding model ships at first.
Status: **Accepted (deviation acknowledged).**
