# Fresh review of ADR-0001 to ADR-0014

Independent reviewer; did not read `debate/`. Inputs: ADRs 0001–0014, log.md Phase 29 and Decisions
90–93, the v1 source, and the web sources cited inline. Date: 2026-10-27.

## 1. Verdict

**Approve the direction, with conditions. Fix B1–B3 before v2-P3 starts.**

The engineering ADRs (0001, 0005, 0010–0012) are careful and falsifiable. The research side is
weaker: benchmark D has no ADR, and contribution A is never tied to the pre-registered result. The
unsigned installer will not run on a fresh Windows 11 install with Smart App Control on.

## 2. Findings

**B1 — Blocker — Benchmark D has no ADR, but the "dev split" carries weight in ADRs 0006 §3, 0007 §5, 0008 §2–3 and 0014.**
- **Problem.** G, the burst parameters, the fusion choice and the scorer weights are all "fitted on
  dev". Nothing defines:
  - the split unit;
  - persona size;
  - how distractors are made;
  - the relevance labels;
  - whether the splits share templates.
- If they share templates, the test measures fit to the generator, not to users.
- The same author also writes two things that check each other: the generator's model of
  *undamaged* time behaviour (birth vs mtime, folder bursts), and the estimator rules that flag
  deviations from it (0006 §3: "birth ≈ mtime in a burst ⇒ suspect"). E1 freezes T, the damage
  table, but not this baseline model, so the result is circular.
- **Fix.** Write ADR-0015 (benchmark) that:
  - splits by persona *and* template family;
  - calibrates the undamaged time distributions on donor statistics and hashes them before any
    estimator rule exists;
  - defines graded relevance;
  - sizes the test set by a power calculation;
  - forbids the generator from calling the engine (§3.4).

**B2 — Blocker — The primary contribution is not the primary metric, and 0014 test 4 contradicts itself (0006 §4, 0008 §6, 0014).**
- **Problem.** The pre-registered primary is "blind-query nDCG@10, full vs best baseline". That
  measures C, which Decision 92 calls engineering.
  - No ADR pre-registers the extrinsic test of A: does reconciled time beat raw mtime for retrieval
    on damaged personas?
  - Test 4 says "no truth label is derivable from `time_evidence`". If that held, no estimator could
    succeed.
- **Fix.**
  - Pre-register two primaries:
    - (i) the E5 metrics per damage type;
    - (ii) nDCG@10 on time-cued queries, comparing `file_time` vs raw mtime vs an oracle time,
      with everything else frozen.
  - Add a baseline that uses the same time parser plus raw-mtime overlap. Without it, gains from the
    parser are credited to A.
  - Reword test 4 as "no sealed truth value flows into `time_evidence`", checked by a provenance
    taint test.

**B3 — Blocker — Unsigned builds fail the clean-machine requirement (0004 §4–5).**
- **Problem.** Smart App Control is on by default on clean installs of Windows 11 22H2 and later.
  When its cloud check cannot make a confident call, it blocks unsigned binaries, with no "run
  anyway" option.
  ([Microsoft](https://support.microsoft.com/en-us/topic/what-is-smart-app-control-285ea03d-fa88-4d56-882e-6698afdb7003),
  [Computerworld](https://www.computerworld.com/article/4043925/windows-11-smart-app-control-explained.html))
- A PyInstaller bundle is a new unsigned EXE plus dozens of unsigned PE files. Testing only for
  SmartScreen, offline in a VM, will not reveal this.
- **Fix.**
  - Put signing in the MVP. Options:
    - a Certum open-source certificate (reportedly about €25–70 a year and token-bound; confirm it
      supports Authenticode first,
      [Delphi-PRAXiS](https://en.delphipraxis.net/topic/15454-cheapest-codesign-149-anyone-used-this/));
    - SignPath Foundation.
  - Sign every PE file.
  - Test on a freshly installed VM with Smart App Control on.

**M1 — Major — The rule "never descend reparse points" can skip OneDrive (0011 §2).**
- **Problem.** OneDrive Files-On-Demand directories are cloud placeholders with
  `IO_REPARSE_TAG_CLOUD_*` tags, and users report the reparse attribute on directories
  ([MS-FSCC](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-fscc/c8e77b37-3909-4fe6-a4ea-2b9d423b1ee4),
  [report](https://github.com/anthropics/claude-code/issues/30928)).
- Desktop and Documents are often redirected into OneDrive, so most of a student's files could
  vanish from the walk.
- **Fix.**
  - Decide by **tag**: skip `MOUNT_POINT`, `SYMLINK` and unknown tags; descend into `CLOUD*` tags.
    Keep the file-id loop guard.
  - Add a fixture directory with a cloud tag.
  - Run a ProcMon check on a real OneDrive with unhydrated folders.

**M2 — Major — Evidence from scan history breaks reproducibility and is lost on recovery (0006 §1, 0010 §1, 0011 §5, 0012 §3).**
- **Problem.** `first_seen`, `superseded_mtime` and move inheritance depend on when and in what
  order scans ran, but `as_of` reaches only `search`, so `update` reads the wall clock. A one-shot
  benchmark scan gives every file the same `first_seen`, so eval and shipped systems see different
  evidence. "Rebuild" on corruption destroys the only copy of this history.
- **Fix.** Inject a clock into `update()`. Have the generator replay scan → damage → scan. Keep
  observations in a backed-up, append-only table. Exclude history from "incremental == clean".

**M3 — Major — Dirty shutdown will be the normal case (0001 §2, 0012 §2–3, 0013 §2).**
- **Problem.** `KILL_ON_JOB_CLOSE` kills the writer whenever the window closes mid-scan, so the next
  launch runs `quick_check`: a full read taking minutes on a multi-GB index, against a 2.5 s
  word-search target. After an upgrade the read-only app says "update needed", so search is down
  during migration and `VACUUM INTO`.
- **Fix.** On close, send Cancel, wait about 3 s, then kill; mark dirty only on a real crash. Run
  `quick_check` in the child while the app searches the old snapshot. Migrate a copy and swap it in.
  Refuse to migrate below 2.2× free space (currently unspecified).

**M4 — Major — The GPU gates contradict the int8 default and are validated on one device only (0003 §2, §4).**
- **Problem.**
  - The CPU default is int8 U8S8. DirectML generally needs an fp16 or fp32 graph, and int8 vs fp32
    cosine rarely reaches 0.999. Gate (a) is likely to fail by construction, and if it is relaxed,
    indexes and queries use different models.
  - The gates run on one RTX 5060 but ship to every vendor. Numeric drift on AMD or Intel GPUs is
    silent, so no fallback fires.
  - DirectML is in sustained engineering
    ([repo](https://github.com/microsoft/DirectML), [Learn](https://learn.microsoft.com/windows/ai/directml)).
- **Fix.**
  - Gate at matched precision, or ship fp32 and measure the CPU cost.
  - On first GPU use, embed a bundled 200-text probe and compare it with CPU reference vectors.
    Below cosine 0.999, use the CPU and log the reason.
  - Include the execution provider in `embedder_fp`.

**M5 — Major — Handicapped baselines, and no frozen build for them (0005 §3, 0008 §1/§4, 0010 §3).**
- **Problem.**
  - FTS5 `bm25()` hard-codes k1 = 1.2 and b = 0.75
    ([fts5_aux.c](https://sqlite.org/src/info/8edb983bc87898ef)). The ContextFS scorer is tuned on
    dev, but BM25 cannot be tuned.
  - Dense is pooled per document. Only the system gets chunk re-scoring, and MaxP is the standard.
  - The chunk-to-document BM25 aggregation is unspecified.
  - The cross-encoder and granite are not in the bundle, so "numbers from the frozen build" cannot
    cover the baselines.
- **Fix.**
  - Tune k1/b and fusion on dev with the scorer's budget (custom rank function or `fts5vocab`
    statistics).
  - Give the dense baseline MaxP over the same pool.
  - Export every baseline to ONNX in an eval bundle built from the same lockfile.
  - Report the tuning budget per system.

**M6 — Major — Index-build parity is untested (0010 §2/§4).**
- **Problem.** The CLI `update` and the GUI child, with 1 or 2 workers, must also build the same
  index, but worker commit order changes `file_id`s and move inheritance. Ties break on the
  *absolute* path, which differs between roots. Rounding to 1e-5 still flips at boundaries.
- **Fix.** Hash the logical content across builds. Break ties on the root-relative path. Treat
  scores within 1e-6 as tied.

**M7 — Major — No budget for 8 GB with no GPU (0001, 0003, 0005, 0011).**
- **Problem.** No RAM total covers the four processes: the UI holds Qt, ORT and about 1.5 GB of
  mapped vectors at 10⁶ documents, and the indexer and two workers may each hold spaCy `md`,
  100 MB buffers and pypdf parses. No throughput estimate exists: at an assumed 30–50 chunks/s on
  the 3700U, 10⁶ chunks take 6–9 h with the window open. The indexer has no priority or battery
  policy.
- **Fix.** Write a RAM budget enforced by Job Object memory limits. Cap chunks per document and
  index user folders first. Use `PROCESS_MODE_BACKGROUND_BEGIN`, fewer threads than cores, and a
  low-battery pause.

**M8 — Major — The default index location contradicts ADR-0005 §6.**
- **Problem.** The index is "refused if inside a scanned root", yet the default `%LOCALAPPDATA%` is
  inside C:, the most likely whole-drive choice. It survives only through the AppData exclusion,
  which the user can edit.
- **Fix.** Refuse only when the index is inside a scanned *non-excluded* subtree, and add a
  self-exclusion that cannot be removed.

**M9 — Major — "Faithful" overclaims what is checked (0009).**
- **Problem.** The dense score comes from a pooled vector, but the reason cites a chunk picked
  separately. Time reasons cite a `time_evidence` row, but the feature uses `file_time`. Claim
  verification checks only that the evidence *exists*.
- **Fix.** Make the dense feature max-chunk similarity and cite `file_time.evidence_used`. Add a
  causal check (neutralising the cited span lowers the contribution), or say "evidence-grounded".

**M10 — Major — Query generation can leak (0008 §6).**
- **Problem.** "A different LLM family from the retriever" is vacuous, because the retriever has no
  LLM. The real leak is an LLM that sees exact dates, titles and rare terms and writes queries no
  human could remember. The participant queries' corpus and judgements are undefined.
- **Fix.** Show the LLM a degraded "memory" view: coarse time, paraphrased topic, no filename.
  Report query-to-target lexical overlap against participant queries. Define the study (§3.5).

**M11 — Major — Feasibility for one student in a few months (0006 E2/E4, 0014).**
- **Problem.** OneDrive and USB transfers cannot be scripted at benchmark scale. E4 needs at least
  300 consented donor files. The MVP also carries compaction, migrations, chips, Issues and a GPU
  spike. The cut order triggers at "four weeks behind" a schedule no ADR states.
- **Fix.** Make T a simulator validated by real-tool replay on a sample (for example 5%). Add a dated
  plan. Decouple the paper path (generator, E1–E5, harness, baselines) from the app. Put compaction,
  SymSpell, relational operators and Settings periods first in the cut list.

**Minor findings**

- **m1 (0002/0013).** The 2 s window target is below the measured 2.18 s PySide6 import. "Never
  waits / never reorders" contradicts "waits up to 3 s … upgraded".
- **m2 (0011 §5).** Move inheritance needs a hash, which metadata-only and placeholder files never
  get. Walk order also makes it arbitrary.
- **m3 (0005 §2).** The unmap acknowledgement covers only the UI; CLI and eval readers also map
  segments. Keep a pending-delete list.
- **m4 (0006).**
  - Win32 converts FAT times with the *current* DST bias.
  - exFAT stores a UTC offset.
  - T must include NTFS tunnelling, which restores the creation time after a safe-save
    ([MAC times](https://en.wikipedia.org/wiki/MAC_times)); otherwise "birth > mtime ⇒ copy"
    misfires.
  - `Zone.Identifier` is free `arrived_here` evidence.
- **m5 (0008/0014).** "0.503 *or explained*" cannot be falsified. Use MRR for single-target queries.
  Pre-register a multiple-comparison correction and a cap on test runs.
- **m6 (0007 §4).** With session IDs carried over, compare partitions up to relabelling.

## 3. Missing entirely

1. **Logging and diagnostics.** Only "≤ 25 MB" is stated. Specify what is logged (redact queries and
   paths by default), `faulthandler` and minidumps for the child and workers, and a user-reviewed
   "Export diagnostics".
2. **Privacy of the index.** The index is a plaintext copy of all document text and
   `lastModifiedBy` names. Specify:
   - an owner-only ACL (D: roots often grant Authenticated Users);
   - `NOT_CONTENT_INDEXED` and exclusion from OneDrive backup;
   - an explicit EFS check (EFS decrypts transparently for the owner);
   - text from BitLocker USB drives landing in an unencrypted index;
   - temp-copy cleanup.
3. **Licences.** Specify the PySide6 LGPL-3 obligations, the model notices, MS MARCO's
   non-commercial terms behind the cross-encoder, the query-LLM and released-benchmark licences, and
   `THIRD-PARTY-NOTICES` with a CI licence gate (for example, blocking AGPL PyMuPDF).
4. **Generator ↔ engine contract.** E3 guards one direction only. The generator must never call the
   engine or baselines to choose targets or distractors. Release the generator and seed, not files:
   a zip loses birth times.
5. **The real-user study.** Specify consent and ethics approval, what is collected (queries,
   judgements, timings; never content or paths), on-device review before export, participant count,
   relevance judging on private drives, and that study data is never used for fitting.
6. **Upgrades in the field.** A new `embedder_fp` or `estimator_version` means a re-embed or
   re-estimate. Specify the flow.
7. **Non-English content.** MiniLM is English-only, so Hindi or Tamil text gets BM25 only. State
   this.
