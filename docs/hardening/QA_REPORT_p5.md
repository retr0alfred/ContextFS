# ContextFS Phase 5 hardening: independent QA report

- **Date:** 2026-10-28
- **Branch:** `v2`, HEAD `44ce2aa`
- **Diff reviewed:** `e693152..HEAD`, i.e. from "v2 Phase 4 complete" through the Phase 5 commits.
- **Build tested:** `dist\ContextFS\ContextFS.exe` and `ContextFS-console.exe`, always run with `--gpu off`.
- **Workspace:** all corpora, indexes and scripts are under `.tools\exp\qa\`. The scripts are `qalib.py`, `t1_edge.py`, `t2_incr.py`, `t3_faults.py`, `t4_readers.py`, `t5_explain.py` and `gui_drive.py`.
- **How to rerun:** `cd .tools\exp\qa` and then `..\..\..\.venv\Scripts\python.exe <script>`.

## Summary

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 4 |
| Medium | 6 |
| Low | 8 |

The read-only guarantee held on every corpus I checked: hashes, sizes, mtimes, attributes and an ADS were all unchanged, and no new files appeared. No network use was seen.

The most serious problems:
- Files in a renamed or deleted folder are never removed from the index (ghost results).
- A file dated 1601 (FILETIME 0) stops every scan permanently.
- A pre-1970 file date crashes the GUI with an access violation.
- Closing the app twice while one slow file is being parsed quarantines that file, with an untrue explanation.

## Findings

### H1 (High). Files in a renamed, moved or deleted folder are never tombstoned, so they stay in search forever

**Evidence:** `t2_incr.py`.
- Corpus: `projA\a0..a4.txt`, `projB\inner\b0..b4.txt` and `same.txt`.
- Scan 1, then `os.rename(projA, projA_renamed)`, then scan 2 with the packaged child.

```
scan2 (rename+recreate) exit 0 {'seen': 11, 'new': 5, 'changed': 1, 'unchanged': 5, 'deleted': 0, ...}
present: [('projA/a0.txt','present') ... ('projA/a4.txt','present'), ('projA_renamed/a0.txt','present') ...]
find "alphaword kiwis" -> ['projA/a1.txt', 'projA/a4.txt', 'projA/a3.txt']   # paths that no longer exist
```

**Code:** `src/contextfs/engine/indexer.py:295-301`. A file is tombstoned only if `parent in stats.listed_dirs`. A folder that no longer exists is never listed, so nothing under it is ever tombstoned. The rule cannot tell a vanished folder apart from an unreadable one.

**Why it matters:**
- Search returns paths that no longer exist, and a renamed folder shows every file twice.
- `--status` counts are inflated.
- Privacy (ADR-0016): the text of a folder the user deleted stays searchable indefinitely.
- Every folder rename or move leaks this way. It breaks the "incremental indexing" and "faithful results" principles.

**Fix:** a file is gone when its nearest ancestor directory that was listed successfully in this scan does not contain the next path component. That means the folder is absent from a listing that succeeded, as opposed to a listing that failed. Keep the C05 rule (an unreadable folder never tombstones) by also checking that the missing folder did not produce a walk error.

### H2 (High). A file with a pre-1677 timestamp (e.g. FILETIME 0 = 1601-01-01) aborts every scan, permanently

**Evidence:** `t1_edge.py`.
- `old1601.txt` with mtime `-11644387200 s` (1601-01-02), set via `os.utime`.

```
child exit 1
{'type': 'error', 'kind': 'OverflowError', 'message': 'Python int too large to convert to SQLite INTEGER'}
scans: [(1,'running',None),(2,'running',None)]      # each run dies at the same place
```

- Only 17 of 34 files were committed: everything after one batch was lost, every time.
- Moving that one file out made the next scan finish with `exit 0`.

**Code:**
- `indexer.py:366-386`: `mtime_ns`/`birth_ns` in ns overflow int64 for dates before 1677-09-21.
- The per-file `except Exception` at `indexer.py:265-274` retries `_index_file(force_access="parse_error")` with the same values. That raises again and escapes `run_update`.
- `finally: marker.close()` (`indexer.py:104-107`) then deletes the in-progress record. So the file never gets a strike and is never quarantined.

**Why it matters:**
- FILETIME 0 / "01/01/1601" dates are a real occurrence: broken archive extractors and copy tools produce them.
- One such file stops indexing of everything after it in walk order, on every launch.
- The GUI also auto-restarts the child 3 times (exit code 1 counts as a crash, `gui.py:345`), and each restart fails the same way.
- No deletes are applied and sessions are never rebuilt.

**Fix:**
- Clamp or store the times as seconds or a 64-bit-safe value. At minimum, validate `Entry` times in `fsio.walk`.
- Make the fallback path unable to raise: wrap the second `_index_file` call and, on failure, skip the file and record an error row.
- Write the strike before re-raising.

### H3 (High). A file dated before 1970 crashes the GUI (0xC0000005) whenever it appears in the results

**Evidence:** `gui_drive.py`.
- The packaged GUI, offscreen, driven by `CONTEXTFS_SELFDRIVE`, on the `t1` index containing `old1965.txt` (mtime 1965-01-01).

```
gui_drive.py t1 <t1\index> 40 <t1\corpus> -- "grandpa letter oldword" "hiddenword"
exit 3221225477            # access violation, no self-drive report
```

- Control: I moved `old1965.txt` out and rescanned (the file is tombstoned). The same run gives `exit 0` with `result_counts [25,25,25]`.
- I moved it back and the crash reproduced (`exit 3221225477`).

**Code:**
- `gui.py:57-61`, `_fmt`, calls `datetime.fromtimestamp(utc, timezone.utc).astimezone()`. On Windows this raises `OSError [Errno 22]` for negative timestamps (checked in the venv: `_fmt(-157766400, True)` raises `OSError`).
- It is called for every result row in `show_results` (`gui.py:411-413`) and in the details pane (`gui.py:440-448`).
- The stored `file_time` for that file has `point_utc = -157766400`.
- `rank.py:365-368` has the same pattern for time reasons.

**Why it matters:** one old file (scanned photos, old documents, files restored with original dates) makes the app crash on most searches.

**Fix:** format through an epoch-safe helper, e.g. `datetime(1970,1,1,tzinfo=utc) + timedelta(seconds=utc)` plus a fixed-offset conversion, or catch `OSError`/`OverflowError` and show the UTC date. Do the same in `rank._reason` and `timeev._parse_infile` (lines 53-54).

### H4 (High). Closing the app twice while one slow file is parsing quarantines it as "made the indexer crash or hang twice"

**Evidence:** `t3_faults.py close`.
- `CONTEXTFS_TEST_FAULT=hang:bigslowfile` with `CONTEXTFS_FILE_TIMEOUT=600` stands in for a parse that is merely slow (a large PDF on a USB disk). The watchdog never fires.
- Each run does what `closeEvent` does:
  1. write `cancel.request` once the marker names the file;
  2. wait 3 s;
  3. kill.
- I did this twice, then ran a normal scan:

```
close1: killed after 3 s (as GUI closeEvent does)
close2: killed after 3 s (as GUI closeEvent does)
after two closes: {'quarantined': 1, 'struck': 'bigslowfile.txt', ...}
[('bigslowfile.txt', 'quarantined', 'made the indexer crash or hang twice; not opened again until it changes')]
find "slowword important meeting notes" -> ['f4.txt', 'f1.txt']     # the file's content is gone from search
```

**Code:**
- Cancel is polled only between files (`indexer.py:239`, `fsio.walk`), so a close during a long parse always ends in a kill.
- The kill leaves `inprogress.marker`, and `run_update` turns it into a strike (`indexer.py:132-134`).
- `guard.py:136-139` says outright that a user close "looks the same" as a crash.

**Why it matters:**
- ADR-0001 Rev. 1 says closing mid-scan is a clean stop, not a crash, and the index is marked dirty only on a real crash.
- The file's content stays unsearchable until it is modified, and the explanation shown to the user is false.
- The first file parsed after launch is exactly the one most at risk, because users open the app, search and close it.

**Fix:**
- Before killing, the GUI should write a "closed by user" record, or the child should mark the record "cancel requested" when it sees `cancel.request`. A record left by a kill that followed a cancel request then does not count as a strike.
- Alternatively, only strike records left by a watchdog exit (exit 3) or a non-kill crash.

### M1 (Medium). Any `sqlite3.OperationalError` at open (locked, busy, I/O, full) is treated as corruption; the previous damaged copy is deleted first

**Evidence:** `t3_faults.py lock`.
- A second process holds the index with `PRAGMA locking_mode=EXCLUSIVE; BEGIN EXCLUSIVE` for 12 s.
- An older `damaged-20000101-000000\` folder was pre-seeded.
- I then ran the child:

```
exit 1 [{'type':'error','kind':'PermissionError','message':"[WinError 32] ... contextfs.db"}]
index dir now: ['contextfs.db', 'damaged-20261028-231910', 'tmp', 'vectors', 'writer.lock']
 damaged-20261028-231910 ['contextfs.db']          # the older damaged copy was rmtree'd
status: ... "integrity": "ok"                       # the index was healthy all along
```

**Code:** `engine/__init__.py:94-108` catches `sqlite3.DatabaseError`, whose subclasses include `OperationalError` "database is locked", "disk I/O error", "database or disk is full" and "unable to open". It then calls `_set_aside_damaged` (`__init__.py:110-122`), which first `rmtree`s every existing `damaged-*` folder.

**Why it matters:**
- Here the healthy DB survived only because Windows refused to rename a file another process had open. `shutil.move` fell back to copy+unlink and left a full copy behind.
- A transient error with no open handle would move a healthy index aside and start a full rebuild. Examples: SQLITE_FULL during an upgrade migration, or an I/O error on a USB index drive.
- The user's only previous damaged copy is destroyed before the move is even attempted.
- With the GUI's 3 auto-restarts, this repeats 3 times, and each attempt copies the whole DB.

**Fix:**
- Rebuild only on corruption codes: `SQLITE_CORRUPT`, `SQLITE_NOTADB`, or a `quick_check` result other than "ok". Check `exc.sqlite_errorname` (Python 3.11+).
- Treat busy, locked, I/O, full and cantopen as retryable errors.
- Delete old `damaged-*` folders only after the new move succeeded.

### M2 (Medium). A transient failure is recorded as a permanent `parse_error` and never retried

**Evidence:** `t3_faults.py tmp`.
- `icacls <index>\tmp /deny user:(WD,AD)`, then scan, then remove the deny, then scan again:

```
scan2 (temp dir unwritable): {'new': 3, 'metadata_only': {'parse_error': 3}, 'parse_errors': 3}
scan3 (temp dir writable again): {'new': 0, 'changed': 0, 'unchanged': 4}
[('later0.txt','parse_error','reading or parsing failed; indexed by name and dates only (see errors)'), ...]
errors: PermissionError: [Errno 13] Permission denied: '...\index\tmp\2.txt'
```

**Code:**
- `indexer.py:263-274`: every exception becomes `force_access="parse_error"`. That covers `PermissionError` or a full disk while writing the temp copy (`indexer.py:409-410`), `PermissionError` from `copy_path.unlink` (antivirus holding the temp file), `MemoryError` under the Job memory limit, and SQLite errors.
- `parse_error` is not in `RETRY_ACCESS` (`indexer.py:41`), so it is skipped while size and mtime are unchanged.

**Why it matters:** an environmental hiccup permanently removes files' content from search, and the explanation blames "reading or parsing".

**Fix:** keep `parse_error` for exceptions raised inside `extract_file`. Classify `OSError`/`MemoryError` from temp I/O as retryable (e.g. `access="io"`), or retry `parse_error` once on the next scan.

### M3 (Medium). Denied or locked files are re-indexed and re-embedded on every scan, so the vector files grow without bound

**Evidence:** `t3_faults.py denied`.
- 20 of 30 files carry `icacls /deny <user>:(R)`. I ran 4 scans with nothing changed:

```
scan1 vectors {'chunk.f32': 46080, 'doc.f32': 46080}
scan2 {'changed': 20, 'chunks_embedded': 20}  vectors 76800
scan3 {'changed': 20, 'chunks_embedded': 20}  vectors 107520
scan4 {'changed': 20, 'chunks_embedded': 20}  vectors 138240
```

**Code:**
- `RETRY_ACCESS` (`indexer.py:41`) is applied at `indexer.py:250`, so an unchanged denied file always goes through `_index_file`.
- That path rewrites the row, deletes and re-inserts its name chunk, and appends new chunk+doc vectors (`_store_document`, `indexer.py:475-511`). Old rows are only marked `valid=0`, and there is no compaction (`vectors.py` docstring).

**Why it matters:**
- About 3 KB per denied or locked file per launch, forever.
- Every launch reports "N changed" for files that did not change.
- Retries cost CPU on every launch, while the PST or database files that stay locked never succeed.

**Fix:** on a retry that fails with the same access kind and unchanged size and mtime, update only `access`/`detail`/`last_seen`. Do not call `_store_document` for metadata-only rows whose name and path are unchanged.

### M4 (Medium). A time reason says "matches" based on an "unknown" 1990-to-now interval, while showing a date outside the cue

**Evidence:**
- `t5_explain.py`, last line, on the `t1` index:
  ```
  readonly.txt: "written 28 Oct 2026 (unknown), matches 'last year'"
  ```
- The stored interval for that file (`t1_times_ro.py`):
  ```
  ('readonly.txt','authored', 631152000, 1793209366, 1793209366, 'unknown')
  ```
  That is lo = 1990-01-01, hi = point = today.

**Code:** `rank.py:373-375` (Phase 5). "matches" is chosen when the interval `[lo, hi]` overlaps the cue, but the date shown is `point_utc` (`rank.py:365-370`). For a wide or unknown interval, the point can be far outside the cue.

**Why it matters:** this is the same faithfulness bug Phase 33 set out to fix ("Jan 2021 matched Nov 2020"), in a different form. ADR-0009: every result is explained faithfully.

**Fix:**
- Say "matches" only if `point_utc` lies inside the cue, or if the interval lies inside it.
- Otherwise say "could be from '<cue>' (date uncertain: <lo>â€“<hi>)".
- Consider not letting `unknown` intervals score at all.

### M5 (Medium). Corruption recovery and migrations deviate from ADR-0012 Rev. 1 (code review)

**Code:**
- `engine/__init__.py:86-108` and `schema.py:104-128`. ADR-0012 Rev. 1 requires that migrations run on a copy (`VACUUM INTO`, migrate, validate, swap), with a check that free space is at least 2.2x the DB. The code migrates in place under `BEGIN IMMEDIATE`, and `check_free_space` runs only later, inside `run_update` (`indexer.py:96`).
- ADR-0012 Rev. 1 also requires that the append-only `observations` table is copied into every rebuild. `_set_aside_damaged` plus a fresh `connect`/`migrate` starts with no observations. All `first_seen` history, which the "arrived on this PC" estimates use, is lost on recovery.

**Why it matters:**
- ADR-0012's falsification list includes "an old-schema upgrade failing" and "disk-full leaving a broken index".
- Losing `first_seen` permanently degrades the time explanations after any recovery.

**Status:** unconfirmed black-box. I did not build an old-schema index.

**Fix:** salvage `observations` from the damaged DB (`.recover` or a best-effort SELECT) into the new one. Implement copy-and-swap migration with the 2.2x space check before migrating.

### M6 (Medium). Nested roots index the same files twice, and search shows duplicates

**Evidence:** `t2_incr.py`, run with roots `corpus` and `corpus\projB`.

```
roots: [(1,'...\t2\corpus'), (2,'...\t2\corpus\projB')]
files per root: [(1, 11), (2, 5)]
find "betaword lemurs number 3" -> ['inner/b3.txt', 'projB/inner/b3.txt', ...]   # same file twice
```

**Code:**
- `gui.py:115-118` (first run) dedups by exact text only. Settings has no nested-root check.
- `run_update` does not exclude a root that lies inside another root.

**Fix:** reject or merge nested roots in Settings and the first-run wizard, or pass inner roots as `self_exclude` for the outer walk.

### L1 (Low). `--status` and `--find` on a missing or empty index dir, or with no argument, crash with a PyInstaller traceback instead of JSON

**Evidence:** `t4_readers.py`.

```
--status <missing>  exit 1 | err: [PYI-...:ERROR] Failed to execute script '__main__' due to unhandled exception!
   FileNotFoundError: no index at ...\empty_index; run an update first
--status (no arg)   exit 1 (unhandled exception)
```

The good part: it did not create the missing directory.

**Code:** `app/__main__.py:33-70`.

**Fix:** print `{"error": ...}` and use a distinct exit code.

### L2 (Low). A drive-relative root such as `D:` indexes whatever the process's current directory is

**Evidence:** `t2_incr.py`. Root `"D:"` with cwd `corpus\projB` indexed `inner/b*.txt`. With cwd `corpus\projA_renamed` it indexed `a*.txt` into the same root `"D:"`, and the earlier files stayed `present`.

**Code:** `fsio.long_path` uses `os.path.abspath`, and `_root_key` treats `D:` and `D:\` as different roots.

**Impact:** the folder picker never produces this, but the CLI and a hand-edited `settings.json` can.

**Fix:** resolve roots to absolute paths (`Path(r).resolve()`) when they are saved and when they are passed to `update`.

### L3 (Low). Cancel is not honoured across automatic restarts, and cancel latency is unbounded within a file

**Code:**
- `gui.py:345-350` auto-restarts after exit 3 or 1 even if the user pressed Cancel.
- `start_update` (`gui.py:299`) then deletes `cancel.request`.
- If a file is slow, Cancel waits up to `FILE_TIMEOUT` (180 s). The watchdog then exits with code 3 and the GUI restarts indexing that the user cancelled.

**Status:** unconfirmed in the GUI; based on code review.

**Fix:** remember a user cancel in the window (`self.user_cancelled`) and skip the auto-restart.

### L4 (Low). The child deletes `cancel.request` in `finally` even when it exits with `WriterLocked`

**Code:** `indexer_child.py:134-135`. A second child refused by the writer lock deletes the cancel request meant for the running writer. Two GUI instances are enough to trigger it, and I found no single-instance guard.

**Status:** unconfirmed black-box.

**Fix:** unlink only after you own the writer lock.

### L5 (Low). Watchdog race: `current` and `started` are read without synchronisation

**Code:**
- `guard.py:206-209` sets `self.current` before `self.started`.
- `guard.py:248-253` reads both from another thread.
- After a long gap with no file in progress (a long walk of unchanged files, or a long embedding flush), the watchdog can pair the new record with the old `started`. It then calls `on_hang` on an innocent file, giving it a strike and a restart.

**Status:** the window is a few bytecodes, so this is theoretical and unconfirmed.

**Fix:** set `started` first, or store `(record, started)` as one tuple.

### L6 (Low). Auto-restart budget: with two bad files, indexing stalls until relaunch

**Code:** `gui.py:345-352`.
- One hanging file needs 2 restarts (strike 1, then strike 2 plus quarantine).
- `restarts` is reset only after an exit with code 0, so a second bad file exhausts the budget of 3. Indexing then stops for the rest of the session (each later F5 advances at most one strike), and the user is not told.

**Status:** code review only.

### L7 (Low). Test-only fault hooks ship in the production executable

**Code:** `indexer_child.py:54-77`, `CONTEXTFS_TEST_FAULT`, and `CONTEXTFS_MIN_FREE_MB`/`CONTEXTFS_FILE_TIMEOUT` in `guard.py` and `indexer_child.py`.
- A stray environment variable can make the shipped indexer hang or segfault on chosen files.
- `CONTEXTFS_FILE_TIMEOUT` is parsed at import with `float()`. A malformed value raises at import time, which could make the child unstartable. This last point is from code review and was not run.

**Fix:** gate the hooks behind a build flag, or ignore them when frozen unless `CONTEXTFS_SELFDRIVE`/a test marker is set.

### L8 (Low). Minor fidelity issues seen while testing

- A file named `trailingdot.` (created via `\\?\`) is stored as `rel_path='trailingdot'`. `os.path.relpath` strips the dot, so "open file" would point at a path that does not exist.
- A file deleted and then recreated with the same name but different content keeps the old file's `first_seen` (`same.txt`: 1793209600 before and after). An "arrived" estimate can therefore describe the earlier file.
- The `network blocked` claim in `__main__.py:10-26` covers Python sockets only. Native code (ORT, Qt) is not covered. That is fine for now, but the docstring overstates it as "a property of the process".
- `check_free_space` requires 2x the index size free just to start a no-op update (`guard.py:136-148`). For a large index on a nearly full disk, launch-time updates are refused permanently.

## Things verified as working

- **Read-only guarantee:**
  - `t1` corpus: sha256, size, mtime and attributes of every file and directory were identical before and after; there were no new entries; the `motw.txt:Zone.Identifier` ADS was unchanged; hidden (+H) and read-only (+R) attributes were preserved.
  - `t2` scan 1: the same checks held (the script asserts them).
  - `t4` corpus (250 files): the same checks held while a writer and 2 readers ran.
- **Index dir inside an indexed root:** it is excluded, both on the first scan and on a rescan.
- **Root spellings are deduped into one root:** case, a trailing `\`, forward slashes and `\.\` all end up as one root. The walk still runs once per spelling, which is wasteful but harmless.
- **File variety:**
  - UTF-16 `.txt` is indexed and searchable.
  - A random-binary `.txt` is indexed without error.
  - Long names (254 characters) and Unicode/Devanagari names work.
  - Empty files work.
  - Files with no extension are indexed as metadata only (`unsupported`).
  - Hidden and read-only files are indexed.
- **Future mtime (2035):** indexed with no error.
- **Pre-1970 month+year query** ("letter from november 1965"): degrades with the note `query parsing degraded: OSError` and returns results.
- **Delete + recreate the same name:** detected as changed; the new content is searchable and the old content is not.
- **Cancel already present before the child starts:** clean exit 0, `cancelled: true`, nothing indexed, and the request file is removed.
- **Two `--find` readers polling 6 times each while a 250-file writer ran:** 12/12 succeeded. The writer finished with exit 0 and `integrity: ok`.
- **The writer-lock path in M1:** the healthy DB was not lost in this run, and `integrity` stayed `ok`.
- **Process cleanup:** no leftover ContextFS processes after my runs.

## What I did not cover

- Deleting the index folder while the GUI runs. Also the "Delete my index" button. Both need interactive dialogs.
- GPU paths. Battery/AC pausing. Job Object memory-limit behaviour, beyond reading the code.
- Real OneDrive placeholders and junctions (suite C02/C03 cover them).
- Old-schema upgrade.
- Real disk-full.
- An index on a removable drive.
- Real ACL-denied folders (only denied files were tested).
- Large-scale performance.
- Rename detection by NTFS file id (move inheritance from ADR-0011 Rev. 1 m2). It does not appear to be implemented. The renamed files in H1 were simply indexed as new.
- Network: I relied on the code review of `block_network` plus the absence of failures. I did not run a packet capture.

## Cleanup notes

The `t1\corpus` folder contains `trailingdot.` and a 254-character filename. Delete it with `rmdir /s /q "\\?\D:\Projects & Code\Projects\ContextFS\.tools\exp\qa\t1"`. The ACL denies used in `t3d` and `t3t` were removed by the script.

