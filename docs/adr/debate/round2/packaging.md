# Round 2: Windows / Packaging Engineer

## 1. Revised proposal

**Changes since Round 1:**
- Ship `onnxruntime` 1.31.1 with the CPU execution provider only.
- Evaluation runs the shipped artifact.
- Content reads use full share mode: read into memory, then close the handle.
- The watcher, CUDA and auto-update are cut.
- Clean-machine tests use a VirtualBox VM and the old laptop.

**R1. Freezing and installer (unchanged).**
- PyInstaller 6.22 one-dir, no UPX. `ContextFS.exe` and `contextfs.exe` share one `_internal`.
- Inno Setup 7.1 installs per user (`PrivilegesRequired=lowest`) and creates Start-menu and desktop shortcuts.
- Uninstall removes only `{app}`.
- Correction (V2): the Inno licence permits commercial use; commercial users are only *asked* to pay.

**R2. Runtime.**
- `onnxruntime` 1.31.1 with the CPU EP, pinned in both the lockfile and the frozen build.
- The model file is pinned by SHA-256, and thread counts are fixed.
- No torch, transformers or CUDA ship. Torch is used only at build time, to export ONNX.
- I ask for brute-force numpy `memmap` vectors. Dropping LanceDB and pyarrow saves about 230 MB and removes the hardest native library to freeze.
- Budget: about 0.55 GB installed and an installer of at most 300 MB.

**R3. Topology.**
- The UI process also runs queries, with the encoder on CPU.
- One indexer child is the sole writer and runs in a Job Object (`KILL_ON_JOB_CLOSE`).
- GPU work (LATER) runs only in the child. A TDR or device-removal crash then kills the child, not the window; the UI sets a sticky `gpu_disabled` flag and restarts the child on CPU.

**R4. `contextfs.fsio`, the only module that touches user files.**
- **Open:** `CreateFileW(GENERIC_READ, SHARE_READ|WRITE|DELETE, OPEN_EXISTING, SEQUENTIAL_SCAN)` via ctypes, then `msvcrt.open_osfhandle`, on the `\\?\` path. CPython's `open()` cannot request share-delete.
- **Read:** call `GetFileInformationByHandle`, read the whole file (64 MB cap), re-query the file information, then close.
  - The handle is held for milliseconds.
  - Parsers receive `BytesIO`, and xxh3 hashes the same bytes.
- **Handle values beat enumeration.** This fixes the stale `DirEntry` size/mtime for files open for writing, and `st_ino`=0. If the two queries differ, the file is requeued.
- **Error rows:** WinError 32/33 gives `locked`; denied access gives `access_denied`; placeholder files are never opened.
- **Never request `FILE_WRITE_ATTRIBUTES`.**

**R5. Walker (Round 1, kept).**
- Use `\\?\` paths.
- Never descend into junctions or symlinks. The loop guard calls `os.stat` per directory.
- Never open placeholder files.
- Exclusions are anchored to known folders.
- New: `GetVolumeInformationW` records the file-system type (`fs_type`) per root.

**R6. Single instance.**
- The writer lock (`msvcrt.locking` on `index\writer.lock`) is the authority. It also covers a shared `index_dir` across logon sessions.
- A `Local\` mutex plus `QLocalServer` only bring the existing window forward.
- If the CLI holds the lock, the GUI shows "Index busy", serves read-only, and retries every 30 s.

## 2. Responses to the skeptic

| # | Response |
|---|---|
| 5 Saves broken | **Accepted (R4).** Test: a writer repeats Word's pattern (write temp, `ReplaceFileW`) 1,000× while the indexer re-reads the file; pass means 0 sharing violations. Oplock back-off is LATER. Microsoft documents oplocks for exactly this: an indexer is notified and closes its handle. |
| 6 Placeholders and stale metadata | Stale metadata: handled by the handle re-stat. Missing in-file evidence for placeholders is a fact, so we report per-source evidence coverage. CI marks fixture files `FILE_ATTRIBUTE_OFFLINE`, and an audit hook asserts they are never opened. One manual ProcMon check on real OneDrive. |
| 10c/d/g Results not identical | **Accepted.** One ORT package and EP, one quantised model file, exact search. Paper numbers come from the frozen `contextfs.exe eval`. |
| 11 Topology and TDR | **Accepted (R3).** |
| 14 Watcher | **Accepted.** Cut; launch scan plus Refresh. |
| 15 Budget | The cap of ONNX-only and ≤150 MB stands, so the gte reranker is cut from the app. |
| 16 Signing | **Accepted.** The plan is unsigned builds with SHA-256 and a WDSI submission. SignPath is optional. Rehearse on the viva machine a week before. |
| 16 WinML | **Corrected:** execution providers arrive via Windows Update, and the real cost is the App SDK. Still cut. |
| 16 Mutex | **Accepted:** the file lock is the authority. |
| 16 `os.startfile` with `\\?\` | Strip the prefix. For paths over 260 characters, fall back to opening the parent folder or "Copy path". |
| 17 Scope | See §5. |

## 3. The skeptic's ten questions

1. **Reconciler.** The Architect or IR owns the algorithm. **I own the ground-truth transfer-signature table.** It extends M1 by hand to Explorer copy/move, 7-Zip, `git clone`, a OneDrive round trip, FAT32/exFAT USB, a browser download and Save As. It is captured before the reconciler is written, and the generator may only use these signatures.
2. **Evaluation.** `onnxruntime` 1.31.1 CPU EP; the shipped model (checked by hash); fixed threads; exact search; feedback off; run through the frozen CLI. A CI test asserts the venv and frozen builds produce the same rankings.
3. **Topology.** UI and queries in one process, plus one indexer child.
4. **Vector store.** A numpy memmap with brute-force search. Perf measures it at 10⁵ and 10⁶ vectors.
5–6. **IR and UX questions.** No packaging constraint.
7. **Timestamps.** Each volume records its `fs_type`:
   - NTFS stores UTC at 100 ns.
   - FAT32 stores local time, with 2 s mtime precision.
   - exFAT's UTC offset needs a test.
   - FAT-family, zip, offset-less EXIF and PDF times are stored as naive local with `tz_known=0` and compared as intervals.
8. **Access times.** No freeze: `SetFileTime` needs `FILE_WRITE_ATTRIBUTES`, i.e. write access to user files. Instead:
   - Record atime from enumeration *before* our read, and record our read time.
   - On rescan, an atime inside [read, read + 1 h] is self-caused, because NTFS delays atime updates up to 1 h. Keep the previous value.
   - Read `NtfsDisableLastAccessUpdate`.
   - atime is stored as weak evidence and not ranked in the MVP.
9. **Disk per 10⁴ files (estimate, roughly 25% text).** Vectors about 8 MB int8, FTS5 plus snippets 40–70 MB, catalog about 10 MB: **about 60–90 MB total**. Guards:
   - refuse to index with less than max(2 GB, 2× index) free;
   - pause below 1 GB;
   - logs capped at 25 MB;
   - `SQLITE_FULL` leads to a clean stop.
10. **Cut first:** DirectML, oplocks, SignPath, network-root tuning, GUI "Move index", the CI installer job.

## 4. Contradictions resolved

- **ORT package.** I had `onnxruntime-directml` 1.24.4; IR and Perf had `onnxruntime` 1.31.1. They cannot coexist and use different kernels. **Ship `onnxruntime` 1.31.1, CPU EP only.**
  - DirectML is reconsidered in v2-P8 only if it passes three gates:
    - cosine ≥ 0.999 against CPU vectors;
    - unchanged top-10 rankings when DML-built vectors are queried on CPU;
    - at least 2× faster bulk embedding on an iGPU.
  - If adopted, the app *and* the evaluation both switch to that package (its CPU EP serves queries).
  - Perf's CUDA-EP option is cut: CUDA 13 plus cuDNN weighs gigabytes.
- **Evaluation runtime vs app runtime.** Identical (Q2).
- **Process model.** I side with Perf, UX and the skeptic: UI and queries in one process, plus an indexer child. That is one fewer RPC surface than the Architect's engine host. Parsers still run in the child, so crash isolation is preserved.
- **Watcher.** Cut (Architect and UX had kept it).
- **Settings file.** One file. I defer to the Architect's `settings.json`. The default is `%LOCALAPPDATA%\ContextFS`; an index inside a root or on a network share is refused.
- **Clean-machine testing on Windows 11 Home.** Hyper-V and Windows Sandbox both need Pro, Enterprise or Education. The plan:
  - **VM.** VirtualBox 7.2.22 (GPLv3 base; vTPM and Secure Boot since 7.0) running the free 90-day Windows 11 Enterprise evaluation ISO. 4 vCPU, 6 GB RAM, a 64 GB disk on D:, and a "clean" snapshot.
  - **Per release:** restore the snapshot, disconnect the network cable, install as a standard user, run `selftest` with the no-network audit hook, uninstall, and verify the index survives.
  - **Speed:** with VBS or Memory Integrity on, VirtualBox runs on the Windows hypervisor and is slower. That is acceptable.
  - **Second machine:** the old 3700U laptop (no discrete GPU), on a fresh standard user, for real first-index timings.
  - The downloads are left to the student. None were made during this debate.

## 5. Scope

| Element | Scope |
|---|---|
| PyInstaller one-dir, `selftest`, frozen-vs-venv parity | **MVP** |
| Inno per-user installer, shortcuts, upgrade in place, uninstall keeps index | **MVP** |
| `onnxruntime` CPU, bundled int8 model, offline audit hook | **MVP** |
| `%LOCALAPPDATA%` layout, env override, refuse index inside a root | **MVP** |
| GUI "Move index" | LATER (P5) |
| `fsio` share-delete open, read-then-close, re-stat, error rows | **MVP** |
| Oplock back-off | LATER (P5) |
| Walker: `\\?\`, no junction descent, placeholder skip, path-anchored exclusions | **MVP** |
| Missing root = offline, never tombstoned | **MVP** |
| Volume-GUID root matching, network-root tuning | LATER (P5) |
| `fs_type`/`tz_known`, atime as weak evidence | **MVP** |
| Transfer-signature table | **MVP** (feeds P3) |
| Free-space and `SQLITE_FULL` guards | **MVP** |
| Writer lock, mutex and `QLocalServer` | **MVP** |
| DirectML behind the three gates | LATER (P8) |
| CUDA-torch edition, WinML, `onnxruntime-gpu` | **CUT** |
| Watcher; auto-update/Velopack; MSIX/Store | **CUT** |
| Unsigned build with SHA-256 and WDSI; viva rehearsal | **MVP** |
| SignPath | LATER, off the critical path |
| VirtualBox VM and old-laptop tests | **MVP** |
| GitHub Actions install job | LATER (P5) |

## 6. Residual risks

- Freezing spaCy is still a single point of failure. If parity fails twice, fall back to the embedded-runtime layout.
- The 64 MB in-memory reads cost at most about 130 MB across 2 workers. Content above the cap is metadata-only until oplock streaming exists.

## Sources

- https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/get-started/install-hyper-v (no Hyper-V on Home)
- https://www.virtualbox.org/wiki/Downloads (7.2.22, GPLv3)
- https://pureinfotech.com/virtualbox-7-tpm-secure-boot-windows-11/
- https://www.microsoft.com/evalcenter/evaluate-windows-11-enterprise
- https://www.it-connect.tech/windows-11-26h2-how-to-download-the-evaluation-iso-image/ (90 days)
- https://learn.microsoft.com/en-us/windows/win32/fileio/opportunistic-locks (indexer use of oplocks)
- https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfiletime (`FILE_WRITE_ATTRIBUTES`; atime delay)
- https://github.com/golang/go/issues/32088 and https://pypi.org/project/winnan/ (CPython `open()` lacks share-delete)
- https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html
- https://pypi.org/pypi/onnxruntime/1.31.1/json
- https://pypi.org/pypi/onnxruntime-directml/json
- https://jrsoftware.org/isorder.php
- https://learn.microsoft.com/en-us/windows/win32/fileio/file-attribute-constants
- Round 1 sources carry over.
