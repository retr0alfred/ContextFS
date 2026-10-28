# `contextfs.engine`: design (v2-P4, log.md Phase 32)

Implements ADR-0001 to ADR-0016. This page fixes the **contracts** that later phases build on,
especially direction A (reconciled file time). Phase 6 must be able to add a better time estimator
**without touching the schema, the scorer interface, the explanations or the harness**.

## 1. Modules

| Module | Role | ADR |
|---|---|---|
| `engine/__init__.py` | `Engine.open / update / search / first_stage / explain / time_evidence / status` | 0010 |
| `engine/fsio.py` | The **only** code that touches user files. Walker plus share-delete reads | 0011 |
| `engine/schema.py` | SQLite schema v1 of the v2 index, and its migrations | 0005, 0012 |
| `engine/timeev.py` | Captures time evidence (file system, in-file, Mark-of-the-Web, observations) | 0006 |
| `engine/estimate.py` | **Pluggable estimators** → `file_time` per event (v0 = rules) | 0006 |
| `engine/encoder.py` | ONNX Runtime CPU encoder (MiniLM int8), pinned threads | 0003 |
| `engine/vectors.py` | Exact f32 vector segments; document-level candidates, MaxP scoring | 0005 |
| `engine/indexer.py` | `update()`: walk → read → extract → evidence → chunk → FTS/embed → aggregates | 0001, 0007 |
| `engine/query.py` | `QueryFrame`: format P(kind), time intervals **with event hints**, entities | 0008 |
| `engine/rank.py` | First stage (BM25 ∪ dense), features, linear scorer, explanations | 0008, 0009 |

The engine never imports Qt, Typer, `benchgen` or truth files. An import-lint test enforces this.

## 2. The time model (direction A built in)

### 2.1 Three events, kept apart everywhere
`EVENTS = ("authored", "arrived_here", "last_user_edit")`.

| Event | Meaning | Typical query language |
|---|---|---|
| `authored` | when the content was first written, by anyone | "the paper from 2019", "the lecture notes from last year's course" |
| `arrived_here` | when the file first appeared on this machine | "the PDF I **downloaded** before the exam", "photos I **got** from the trip" |
| `last_user_edit` | when *this user* last changed it | "the report I **was working on** in March", "what I **edited** last week" |

### 2.2 Storage
- `time_evidence(file_id, source, kind, wall_s, utc_s, offset_min, tz_state, resolution_s,
  reliability, raw, captured_at)` holds every raw clue, never interpreted:
  - NTFS birth/mtime/atime;
  - Office `core.xml` created/modified/lastModifiedBy;
  - PDF Info;
  - EXIF original;
  - zip max entry time;
  - `Zone.Identifier`.
- `observations(file_id, kind, at_utc, detail)`, append-only:
  - `first_seen`, `superseded_mtime` (mtime moved but hash unchanged), `moved_from`;
  - survives rebuilds (ADR-0006 Rev. 1).
- `file_time(file_id, event, lo_utc, hi_utc, point_utc, confidence, method, evidence_json,
  estimator)` holds **one row per event per file, per estimator**. Several estimators can coexist
  (`raw_mtime`, `rules_v0`, later `prob_A`), so ablations and P-A2 compare them on the same index
  without re-indexing.

### 2.3 Estimators are plugins
```python
class Estimator(Protocol):
    name: str                       # "raw_mtime" | "rules_v0" | ... (Phase 6: "prob_A")
    def estimate(self, ev: FileEvidence, ctx: FolderContext) -> dict[str, EventTime]: ...
```
- `FileEvidence` contains only what `time_evidence` and `observations` hold. **No truth is
  reachable** (ADR-0006 E3, taint test).
- `FolderContext` gives sibling statistics (burst detection: how many files share a birth time
  within W seconds).
- `raw_mtime`: every event = [mtime ± 0], `confidence=sure`. This is the baseline that direction A
  must beat (P-A2).
- `rules_v0`: the frozen rule set from ADR-0006 §3 (uses T v1 + `data/calibration/undamaged_model_v1.md`).
- **Oracle** (P-A2 upper bound): *not* an estimator in the engine. The harness passes the sealed
  truth as an override map to `SearchConfig.time_override`, from outside. The engine never opens
  truth files.

### 2.4 Time in queries
- `QueryFrame.time = [TimeCue(core=(lo,hi), band_days, event_hint)]`.
- `event_hint` comes from the verbs: downloaded/got/saved/received → `arrived_here`;
  edited/worked on/wrote/updated → `last_user_edit`; "from <year>"/published → `authored`;
  none → `any`.
- Bands are soft: overlap decays linearly over `band_days` outside the core.

### 2.5 Time features (the scorer's contract)
For each candidate file and each `TimeCue`:
- `time_<event>` for each of the three events = soft overlap(cue, `file_time[event]`) ×
  confidence weight (sure 1.0, probably 0.7, unknown 0.3);
- `time_hinted` = the event matching the cue's hint, or the max over events when the hint is
  `any`. **This is the feature the scorer uses.** The per-event values stay available for
  analysis and explanations.

`SearchConfig.time_source` selects which estimator's `file_time` rows feed these features
(`raw_mtime` | `rules_v0` | `prob_A` | `override`). P-A2 is therefore exactly one config switch,
with everything else frozen.

## 3. Ranking contract

```text
P0 = BM25 top-200 docs ∪ dense top-200 docs  (exact; document-level candidates)
features x(d, q) = {bm25, dense_maxp, name, format, time_hinted, session, link}
score = w · (x − a)        (linear; w fitted on dev; a = frozen "absent" values)
contribution_i = w_i (x_i − a_i)      → explanations (ADR-0009)
```
- Every feature carries a `doc_ref`. The text features cite the argmax chunk span. Time cites
  `file_time.evidence_json` rows, which name the `time_evidence` sources used.
- A future reranker (cross-encoder) attaches as one more feature, never by replacing the
  contract.

## 4. Index layout (`%LOCALAPPDATA%\ContextFS\index` or `--index-dir`)
```
index/
  contextfs.db        SQLite (WAL): everything except vector bytes
  vectors/            seg-N.f32 + seg-N.ids (chunks), doc.f32 + doc.ids (document candidates)
  tmp/                transient parse copies (deleted after parsing; swept at start)
  writer.lock         msvcrt lock = single writer (ADR-0012)
```

## 5. What is deliberately NOT in the MVP (cut list, ADR-0014)
Vector compaction, SymSpell, P1 expansion, the counterfactual UI, DirectML, dirty-block
incremental sessions, multi-root UI polish. Each is a module-local addition later. **None of them
changes a contract above.**
