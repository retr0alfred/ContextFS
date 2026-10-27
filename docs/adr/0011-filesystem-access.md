# ADR-0011: Filesystem access — one `fsio` module, safe whole-drive walking, never hydrate or block

- **Status:** Accepted · **Date:** 2026-10-27 · **Phase:** v2-P2
- **Debate record:** packaging R1/R2 (R4, R5), architect R2, performance R1/R2, skeptic R1 #5–6,
  R2 N11, D5, D7

## Context
v1 has these gaps:
- `os.walk` has no `onerror`, so permission-denied directories vanish silently.
- Top-down `os.walk` **descends into junctions even with `followlinks=False`** (packaging R1, from
  CPython `Lib/os.py`).
- Hashing reads cloud-only placeholders and **triggers a download**.
- CPython's `open()` cannot request share-delete, so the indexer can break Word's
  rename-replace save.
- `LongPathsEnabled` is 0 on the dev machine.
- Nothing is committed until the walk ends.

## Decision
1. **`contextfs.fsio` is the only module that touches user files** (an import-lint test enforces
   this). It never requests write access or `FILE_WRITE_ATTRIBUTES`.
2. **Walker:**
   - `os.scandir` on `\\?\` paths, with an explicit stack;
   - reparse points (junctions and symlinks) are **never descended**, and a per-directory
     (volume, file-id) loop guard backs this up;
   - exclusions are anchored to known folders (Windows, Program Files, AppData, `$Recycle.Bin`,
     System Volume Information), plus name rules (`.git`, `node_modules`, venvs, `__pycache__`,
     caches). They are editable, and shown with file counts in the first-run flow;
   - every directory error becomes an error row with a reason, so nothing is silent;
   - **only a successfully listed directory may tombstone its children.**
3. **Placeholders:** files with `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS`, `RECALL_ON_OPEN` or
   `OFFLINE` are **never opened**. They are indexed by name, path and file-system times only, and
   counted in coverage. CI marks fixture files `OFFLINE`, and an audit hook asserts they are never
   opened. A manual ProcMon check on real OneDrive is part of v2-P5.
4. **Reads:**
   - `CreateFileW(GENERIC_READ, FILE_SHARE_READ|WRITE|DELETE, OPEN_EXISTING, SEQUENTIAL_SCAN)`;
   - read into memory up to **100 MB**, xxh3 over the same bytes, re-query the handle info, then
     **close before parsing** (milliseconds held);
   - above 100 MB, stream-copy to a temp file in the index directory under the same sharing, then
     parse (skeptic D5);
   - files that changed mid-read are requeued; locked or denied files become error rows and are
     retried next scan.
   - **Test:** a writer repeats Word's temp-write + `ReplaceFileW` 1,000× while the indexer reads;
     it must hit zero sharing violations.
5. **Change detection:** size + mtime from enumeration, then the handle. Hashing happens only when a
   file is read for extraction anyway. A new path matching a tombstone's (size, hash) **inherits its
   `file_id`**, so moves keep their history.
6. **Roots:** multiple roots. Roots are identified by volume serial (volume GUID is LATER). A
   missing root is marked **offline, never tombstoned**; its files stay searchable with "Open"
   disabled. Each volume records its `fs_type`.
7. **Resumable scans** (D7): a persisted per-directory queue, with commits every 500 files or 5 s.
   Cancel, a crash or sleep resumes on the next launch.
8. **Opening results:** strip `\\?\` before `os.startfile`. For paths over 260 characters, fall
   back to opening the parent folder or "Copy path".

## Consequences
Whole-drive scans become safe and observable. Encrypted (EFS) and password-protected files become
error or metadata-only rows with a reason.

## How we will know it was wrong
Any v2-P5 hardening case fails: hydration observed, a junction loop, a sharing violation, a silent
skip, or a lost scan after a kill.

## Revision 1 — after the fresh review (2026-10-27)
Supersedes §2 and §5 where they conflict. Source: `review/fresh_review.md` M1, m2.
- **Reparse points are decided by tag, not by the attribute (M1).**
  - Skip `IO_REPARSE_TAG_MOUNT_POINT` (junctions), `IO_REPARSE_TAG_SYMLINK` and unknown tags.
  - **Descend into `IO_REPARSE_TAG_CLOUD*` directories** (OneDrive Files-On-Demand), whose files
    are still never opened while they are placeholders.
  - The (volume, file-id) loop guard stays.
  - Test: a fixture directory with a cloud tag, plus a ProcMon check on a real OneDrive with
    unhydrated folders (v2-P5).
- **Move inheritance (m2)** applies only to files that have a content hash. Metadata-only and
  placeholder files never inherit. When several tombstones match, the lowest `file_id` wins.
