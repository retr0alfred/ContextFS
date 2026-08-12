# Migrating ContextFS to real personal files

**Status: a plan, not an executed step.** Nothing in this document has been run.
Every result ContextFS reports comes from the authored synthetic corpus, and
this document exists to say precisely what would have to happen before any
claim could be made about real data, and which of those steps are genuinely
hard.

That distinction matters more than the plan itself. A synthetic corpus with
hand-written ground truth is what makes the reported numbers *checkable*; it is
also what makes them *unrepresentative*. Both are true, and the second is the
reason this phase exists.

---

## 1. Why the system was built on synthetic data first

The research question — does context-aware retrieval beat semantic-only
retrieval for memory-based re-finding? — needs three things that real personal
files cannot supply:

| Requirement | Synthetic corpus | Real files |
|---|---|---|
| Ground-truth relevance for each query | Authored alongside the files | Requires the owner to label, from memory |
| Ground-truth activity sessions | Designed in, with an adversarial case and a negative control | Unknowable; the real sessions happened years ago |
| Meaningful vs incidental date labels | 88 hand-labelled (file, date) pairs | Would need per-date human judgement |
| Publishable / inspectable by an examiner | Yes, committed to the repo | No — privacy |

The last row is decisive for a final-year project. An examiner can regenerate
the corpus, re-run every evaluation script, and get the same numbers. That is
not possible with a private Documents folder, and a result nobody can reproduce
is not much of a result.

---

## 2. What "migrating" actually means

There is no data migration in the database sense. ContextFS never modifies
scanned files and stores everything it derives separately, so pointing it at
real files is a **configuration change**, not a conversion:

```toml
# contextfs.local.toml  (git-ignored)
[paths]
root = "C:/Users/<you>/Documents"
data_dir = "C:/Users/<you>/.contextfs"
```

```bash
contextfs --config contextfs.local.toml scan
```

The work is not in getting it to run. The work is in knowing whether the output
is any good, and that is where the hard steps are.

---

## 3. The plan

### Step 1 — Dry run first (easy)

```bash
contextfs --config contextfs.local.toml scan --dry-run
```

Reports what would be indexed without creating an index. Two things to check
before going further:

- **File count.** The synthetic corpus is 40 files. A Documents folder is
  commonly 10³–10⁵. See §4 for what breaks at which order of magnitude.
- **Exclusions.** Confirm that `node_modules`, `.git`, virtual environments,
  and application caches are excluded. Indexing a dependency tree wastes hours
  and pollutes every session cluster with machine-generated timestamps.

### Step 2 — Start with one subtree, not the whole disk (easy)

Point `root` at a single project or course folder — something on the order of
50–500 files where you personally remember what happened. This keeps the first
scan minutes rather than hours, and more importantly it keeps you able to judge
the output, which is the entire point of the exercise.

### Step 3 — Sanity-check the layers that are checkable without labels (easy)

Some outputs can be judged by eye without any ground truth:

```bash
contextfs duplicates    # do you recognise these as duplicates?
contextfs projects      # is the lifecycle stage right for folders you know?
contextfs tags <file>   # are these the words you would have tagged it with?
```

If `projects` says a folder you finished last year is "active", something is
wrong with mtimes — commonly a cloud-sync client or a bulk copy that rewrote
every timestamp. That failure mode is silent on synthetic data and common on
real data, so check it early. §5 covers it.

### Step 4 — Build a small real query set (**hard — this is the bottleneck**)

This is the step that cannot be automated, and it is the reason "just point it
at real files" is not a plan.

The protocol that would make the result meaningful:

1. **Write the queries before looking at the index.** Recall 15–25 files from
   memory the way you would actually search: *"the spreadsheet I made the week
   of the fee deadline"*, not *"budget.xlsx"*. Record the query text and what
   you believe the answer is, in a file, before running anything.
2. **Do not adjust a query after seeing results.** Rewriting a query that failed
   is how an evaluation quietly turns into a demo.
3. **Record relevance honestly, including "I was wrong about which file it
   was".** That is a real outcome, and dropping it biases everything upward.
4. **Have a second person do it too**, on their own files, if at all possible.
   n=1 measures one memory, not a method.

Format compatible with the existing harness (`data/synthetic/ground_truth.json`,
`QueryOutcome` in `evaluation.py`): each entry needs `query`, `relevant` (list
of paths), and `kind` (semantic / temporal / activity / entity / hybrid) so the
per-kind breakdown still works.

**Why this is hard, stated plainly:** it takes a person several hours, it cannot
be checked by anyone else, and the labeller is the same person who wrote the
system. Blinding is impossible. The most that can be claimed from it is *"the
system behaved this way on one person's files"* — a case study, not a result.

### Step 5 — Re-run the evaluation harness (easy, once Step 4 exists)

```bash
python scripts/evaluate.py --ground-truth path/to/real_ground_truth.json
```

The ablation table, per-kind MRR, and explanation coverage all work unchanged;
nothing in the harness assumes the synthetic corpus. Report real-data numbers
**separately** from synthetic ones, never pooled — they measure different things
under different labelling regimes.

### Step 6 — What cannot be evaluated on real data at all (**hard, partly impossible**)

Three of the project's measured results have no path to real-data validation:

| Result | Why it cannot transfer |
|---|---|
| Session accuracy (F1 1.000) | Requires ground-truth session boundaries. The user does not remember which files they had open together in one sitting two years ago, and no record of it exists. |
| Meaningful-date F1 (0.986) | Requires per-date human judgement over hundreds of extracted dates. Feasible in principle, brutal in practice: a 500-file subtree yields on the order of 10³ (file, date) pairs. |
| Entity extraction F1 (0.595) | Requires hand-labelled entity spans. Same problem, same scale. |

For sessions specifically, the honest fallback is a **weaker proxy**: ask the
user to confirm or reject each reconstructed session as "yes, that was one piece
of work" / "no, these don't belong together". That yields precision-like
feedback but **no recall** — you cannot ask someone to enumerate sessions they
have forgotten. Any number from it must be labelled as what it is.

---

## 4. What breaks at scale, and roughly where

The system has been measured at 40 files. The table below is **projected from
the known complexity of each stage, not measured**, and is stated as such:

| Stage | Complexity | Expected first pain point |
|---|---|---|
| Scan | O(n), xxh3 hashing | Fine to ~10⁶; disk-bound |
| Extraction | O(n), per-file | Fine; embarrassingly parallel if needed |
| Embedding | O(chunks) | ~35 files/s measured on the target CPU → 10⁴ files ≈ 5 min, 10⁵ ≈ 50 min. Tolerable once, painful to redo |
| Vector search | LanceDB ANN | Fine well past 10⁶ |
| **Session clustering** | **O(n²)** pairwise similarity | **The first real wall. ~10⁴ files is where this becomes minutes; ~10⁵ is not viable** |
| Date cross-file recurrence | O(distinct dates) | Fine |
| Graph build | O(n · k) after ANN pre-filter | Fine to ~10⁵ |
| Incremental update | ~450 ms floor (structural rebuild) | The floor grows with n, dominated by session clustering |

**The known architectural limit** (already recorded in log.md, Phase 18):
session clustering and the structural rebuild are global by design, so
incremental update has a floor that does not shrink with fewer changes. Fixing
it for real-scale data means blocking sessions by time window and reconciling
globally on a schedule — a design change, not a tuning change, and it is not
implemented.

---

## 5. Real-data failure modes the synthetic corpus cannot exhibit

Listed so that a disappointing run gets diagnosed rather than mistaken for a
null result:

1. **Destroyed mtimes.** Cloud sync (OneDrive/Dropbox/Drive), bulk copies,
   restores from backup, and unzipping all rewrite modification times. The
   activity layer is built entirely on mtime, so this does not degrade it — it
   *silences* it. Detection: if `projects` reports implausible recency for
   folders you know are old, mtimes are not trustworthy and every activity
   number from that corpus is meaningless. There is no repair.
2. **Scanned and image-only PDFs.** The corpus's PDFs all have a text layer.
   Real ones frequently do not; extraction returns near-empty text and the file
   becomes invisible to every layer. No OCR is implemented — deliberately: it is
   a large dependency and not a research contribution.
3. **Format diversity.** `.eml`, `.one`, `.psd`, CAD, archives, proprietary
   formats. These are skipped, which is correct, but the skip rate on a real
   Downloads folder could be substantial and must be reported alongside any
   real-data result.
4. **Language mixing.** `en_core_web_md` and `all-MiniLM-L6-v2` are English
   models. Non-English documents embed and NER poorly, silently.
5. **Machine-generated files.** Build outputs, logs and caches have dense,
   clustered mtimes that will dominate session clustering with sessions no human
   ever had. Exclusions are the only defence and they are manual.
6. **Genuine duplicates at scale.** Real folders contain hundreds of copies
   (`report (1).docx`). The duplicate layer will find them, and connected
   components could grow large enough that the graph becomes dense in ways the
   40-file corpus never tests.

---

## 6. Minimum viable real-data validation

If time allows only one thing, do this. It is roughly a day's work and it is
defensible:

> **Take one folder of 100–300 files you personally worked in over at least six
> months. Write 15 recall-style queries and their answers from memory, in
> advance, in writing. Index it. Run `scripts/evaluate.py` on those 15 queries,
> reporting the hybrid system against the semantic baseline on the same index.
> Report per-kind MRR and Recall@10, the number of files skipped as unsupported,
> and whether mtimes were intact.**

What that can honestly claim: *"on one user's real folder, under self-labelled
ground truth, the context-aware system scored X against a semantic baseline's
Y."* A case study, explicitly n=1, with the labelling conflict of interest
stated.

What it cannot claim: that the effect generalises, that the session or date
numbers hold, or that it would survive blinded labelling.

Report it that way. A modest claim that survives scrutiny is worth more than a
strong one that does not, and an examiner who finds the caveat themselves will
trust nothing else in the report.
