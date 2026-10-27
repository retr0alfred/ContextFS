# ADR-0012: Index safety — single writer, migrations, corruption recovery, disk guards

- **Status:** Accepted · **Date:** 2026-10-27 · **Phase:** v2-P2
- **Debate record:** architect R2 §Safety, packaging R2 R6, performance R2 #13, skeptic R1 #13, #16

## Decision
1. **Single instance:**
   - a `msvcrt.locking` lock on `index\writer.lock` is the **authority**. The OS releases it if the
     process dies, and it also covers a shared index across logon sessions;
   - a `Local\` mutex plus `QLocalServer` only bring an existing window forward;
   - if the CLI holds the lock, the GUI shows "Another ContextFS is updating the index. Search
     works." and retries every 30 s.
2. **Migrations:** one transaction per migration, ordered, tested from every older schema
   (including a v1 index). A `VACUUM INTO` backup is taken first, if free space is at least 2.2× the
   database. A read-only open never migrates; it reports "update needed".
3. **Corruption:** after a dirty shutdown, run `PRAGMA quick_check`. On failure, rebuild the index,
   keeping settings. `integrity_check` is available from Settings → Repair. Vector segments are
   validated against the SQLite manifest, and orphans are dropped.
4. **WAL and disk:**
   - readers never hold a transaction beyond one query;
   - checkpoint `PASSIVE` per batch, `TRUNCATE` at idle and on exit, forced above 256 MB, with
     `journal_size_limit` set;
   - refuse to start indexing below max(2 GB, 2× the estimated index size) free;
   - pause below 1 GB;
   - `SQLITE_FULL` → a clean pause and a banner;
   - logs capped at 25 MB.
5. **Location:** see ADR-0005 §6. Never inside a root; configurable.

## How we will know it was wrong
Any v2-P5 case fails: two instances writing; DB corrupted and not recovered; an old-schema upgrade
failing; or disk-full leaving a broken index.

## Revision 1 — after the fresh review (2026-10-27)
Supersedes §2–§3 where they conflict. Source: `review/fresh_review.md` M3, M2.
- **Recovery never blocks search.** After a real crash, the **child** runs `quick_check` while the
  app searches the last committed snapshot; a failure triggers a background rebuild.
- **Migrations run on a copy and swap.**
  - The child migrates a copy of the database (`VACUUM INTO`, then migrate), validates it, then
    atomically swaps the manifest. The app keeps searching the old generation meanwhile.
  - Refuse to migrate when free space is below 2.2× the database, and say why.
- **The append-only `observations` table** (ADR-0006 Rev. 1) is copied into every rebuild and
  backed up with settings.
