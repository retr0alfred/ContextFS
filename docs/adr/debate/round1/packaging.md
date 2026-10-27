# Round 1: Windows / Packaging Engineer

## 1. Position summary

- **Build and installer.** PyInstaller one-dir, no UPX, in a per-user Inno Setup 7.1 installer: no UAC, no MSIX, no one-file. Uninstall removes only the program folder; the index stays by default; user files are never touched.
- **CUDA and torch.** No CUDA and, if the ML role agrees, no torch in the bundle. ONNX Runtime + DirectML EP gives CPU and any-vendor GPU in one ~25 MB package. CUDA torch is dev/eval only. Nothing downloads at runtime.
- **Folders.** Program and models: `%LOCALAPPDATA%\Programs\ContextFS`. Settings, index, logs: `%LOCALAPPDATA%\ContextFS`. The index is movable, never into a root.
- **Scanning.** A `scandir` walker with `\\?\` paths classifies placeholders, links, locked, denied and offline items from enumeration attributes alone, without opening them. Nothing is silently dropped.
- **Updates and signing.** No network calls, so no auto-updater: run the newer installer over the old one. Sign via SignPath Foundation (free OSS) if accepted, else ship unsigned with hashes.

## 2. Proposals

### P1. Freezing tool
**Decision.**
- PyInstaller 6.22 (2026-08-08) with hooks-contrib 2026.9, one-dir.
- One `COLLECT` holds both `ContextFS.exe` (windowed) and `contextfs.exe` (CLI), sharing `_internal`.
- Explicit hooks `collect_all` spaCy, thinc, the model package with its metadata, and lance/lancedb.
- Weights go in `{app}\models`. The engine already forces `HF_HUB_OFFLINE`.

**Alternatives.**
- One-file re-extracts to temp each launch: slower, packer-like to AV.
- Nuitka 4.0: minutes-long compiles, same onefile AV issue, AGPL community edition.
- Briefcase: needs WiX and .NET 3.5.
- Embedded python-build-standalone plus launcher (as `start.bat` today): no freezing magic, but shows as "Python".

**Trade-off.** Static analysis misses dynamic registries (spaCy, lance).
**What would change my mind.** If the build needs more than 5 custom hooks or fails parity, switch to the embedded-runtime layout.
**Measurement.** A hidden `contextfs.exe selftest` on a clean machine indexes a bundled 40-file corpus in temp and runs 10 golden queries. The ranked IDs must match the dev venv exactly.

### P2. Installer, size, startup
**Decision.** Inno Setup 7.1, free for non-commercial use.
- `PrivilegesRequired=lowest`, `SetupArchitecture=x64`.
- `DefaultDirName={autopf}\ContextFS`, which becomes `{userpf}` per user; uninstall info in HKCU.
- Start-menu entry in `{autoprograms}`; optional desktop icon in `{autodesktop}`.
- A fixed `AppId` so new versions upgrade in place; `CloseApplications=yes`.
- Uninstall deletes only `{app}`. A checkbox "also delete index" defaults to No.

**Alternatives.**
- MSIX redirects AppData into the container and deletes it on uninstall. Outside the Store it also needs a trusted signature.
- Per-user WiX/MSI is clumsy.

**What would change my mind.** Publishing via the Microsoft Store, which gives free re-signing and no SmartScreen prompt.

**Budget (estimates).** Without torch/transformers, the install is about 0.85 GB (Qt 0.2, lancedb+pyarrow 0.23, spaCy 0.14, models 0.1, ORT 0.05, Python/numpy/scipy 0.15).
- **Caps:** installer ≤ 450 MB, installed ≤ 1.1 GB. CPU torch would add about 0.5 GB (the PyPI Windows wheel alone is 124 MB).
- **Startup:** window ≤ 2 s cold, before any ML import; search on the existing index ≤ 6 s on the 8 GB target.
- **Measurement:** `-X importtime` on the frozen build; as a standard user, install → uninstall → reinstall keeps the index.

### P3. GPU and CUDA delivery
**Decision.**
- One build using `onnxruntime-directml` 1.24.4 (about 24 MB, CPU EP plus DML EP). The "GPU on/off" setting picks the EP list.
- Any DML failure falls back to CPU and is logged.
- The index records `embed_backend` and the model hash. Eval and parity tests pin CPU.

**Alternatives.**
- **(a) Separate CUDA-torch installer.** +2.4 GB, NVIDIA-only, Blackwell needs cu128+. Dev/eval only.
- **(b) Runtime CUDA download.** Rejected: breaks offline and no-network; pip into a frozen app.
- **(c) `onnxruntime-gpu`.** Requires pip CUDA 13 and cuDNN 9, so the same size problem as (a).
- **(d) Windows ML.** Rejected: it downloads EPs from the Store.

**Trade-offs.**
- DirectML is in sustained engineering and lags mainline ORT (1.24 vs 1.31).
- `onnxruntime` and `onnxruntime-directml` conflict in one environment.
- DML vectors differ slightly from CPU vectors.

**What would change my mind.**
- DML-vs-CPU cosine below 0.999.
- A chosen model that will not export to ONNX.
- Less than 2× bulk speedup on an integrated GPU.

**Measurement.** Embed 10k chunks on CPU, DML on an iGPU, and DML on the RTX. Compare throughput, cosine to CPU, and top-10 overlap.

### P4. Paths and permissions
**Decision.**
- Data folder `%LOCALAPPDATA%\ContextFS\` holds `settings.toml`, `index\<profile>\`, `models\` (user-added), `logs\` (5×5 MB, rotating) and `crash\`.
- `index_dir` is configurable; `CONTEXTFS_DATA_DIR` overrides it (already exists).
- Moving the index: copy → verify → switch → delete.
- An index inside a root is rejected, and the data folder is always excluded from scans.
- Roaming AppData is never used.
- Bundled models stay read-only in `{app}`.

**Spaces and `&` in the dev path.**
- Never `shell=True`; always list args.
- `os.startfile` for opening files; `explorer /select,` as a list argument.
- Every Inno path is quoted.
- CI builds from `Dév & Test\…` with a path over 260 characters.

### P5. Whole-drive realities (scanner I/O)
**Decision.** Replace `os.walk` at `scanner.py:202` with an iterative `os.scandir` walker:

1. **Long paths.** Always use `\\?\D:\…` or `\\?\UNC\…` paths, and display them without the prefix. `LongPathsEnabled` is **0** on the dev machine. Lifting MAX_PATH normally needs both that admin-only registry value and a manifest flag, while the prefix works regardless.
2. **No opens during classification.** Use `entry.stat(follow_symlinks=False)`: on Windows it makes no syscall and returns `st_file_attributes`, `st_reparse_tag` and `st_birthtime`.
3. **Cloud placeholders.** `RECALL_ON_DATA_ACCESS` (0x400000), `RECALL_ON_OPEN` (0x40000) or `OFFLINE` (0x1000) mean **cloud-only**.
   - Store the name, size and FS times from enumeration only.
   - Never `os.stat`, open, hash or extract it (today's `_describe` calls `path.stat()`, an open).
   - Results explain "content not indexed: online-only". Once hydrated, the next scan extracts it.
   - Cloud reparse tags are hidden from normal processes, so use attribute bits.
4. **Links and loops.**
   - In 3.12, top-down `os.walk` checks `os.path.islink`, which is False for junctions. So **`followlinks=False` still descends junctions**; I read this in the bundled `Lib/os.py`.
   - New rule: never descend `is_symlink()`, `is_junction()`, or other non-cloud reparse directories. Record them as `link` rows.
   - Loop guard: a visited `(st_dev, st_ino)` set, one `os.stat` per directory.
5. **Errors become rows.**
   - Denied directory → `access_denied`.
   - WinError 32 or 33 → `locked`. Keep the old entry and retry next scan. No VSS, since it needs admin.
   - Children of a folder that becomes unreadable are marked stale, never tombstoned.
6. **Changed mid-read.** Re-stat after extraction. If anything differs, discard the result and requeue.
7. **Encrypted and huge files.**
   - EFS files read normally; if access is denied → `encrypted_unreadable`.
   - Password-protected documents → metadata-only.
   - Extraction cap: 100 MB, editable.
   - Above the hash cap, fingerprint = size + xxh3 of the first and last 4 MB + file ID.
   - Sparse files → metadata-only.
8. **Removable and network roots.**
   - Key each root by volume GUID and serial, so a changed drive letter still matches.
   - Use `GetDriveTypeW` to identify drive types.
   - An absent root is `offline`: its files stay searchable, flagged "not connected", never tombstoned.
   - Network roots get lower concurrency.
9. **Exclusions.**
   - Anchor them to `SHGetKnownFolderPath` paths: Windows, Program Files ×2, ProgramData, LocalAppData, `$Recycle.Bin`, System Volume Information.
   - Name rules only for unambiguous names (`.git`, `node_modules`, `__pycache__`) plus the `pyvenv.cfg` marker.
   - Today's any-depth rule wrongly drops user folders named `build` or `env`.

**Measurements.**
- **OneDrive.** 20 online-only files, then a full scan. Expect 0 downloads, no `ReadFile` in Process Monitor, and cloud icons intact.
- **Fixture tree.** A junction loop, a >260-character path, a file opened with `share=0`, a denied folder and an unplugged USB root. The scan finishes and reports each case.

### P6. Single instance
**Decision.**
- A named mutex `Local\ContextFS-<hash(data_dir)>`. The kernel releases it on crash.
- A `QLocalServer` forwards "activate / run query" to the running instance.
- An engine writer lock via `msvcrt.locking` on `writer.lock`, released by the OS on death. The CLI may read (WAL) while the GUI writes.

**Rejected.** `QLockFile` (PID heuristics, 30 s stale time).
**Measurement.** Kill the process mid-index, then relaunch: it must start with no manual cleanup.

### P7. Updates and signing
**Updates.**
- No in-app update check.
- The About page links to the releases page.
- After upgrade: schema migration; a changed model hash triggers background re-embedding while old vectors serve.
- Velopack rejected: it calls home, and its build tool needs .NET.

**Signing.** Artifact Signing is available to individuals only in the USA or Canada, and OV certificates cost $150–300 a year. Plan:
- Apply to SignPath Foundation: MIT licence, public repo, GitHub Actions build.
- Otherwise ship unsigned with SHA-256 hashes.
- Submit each release to WDSI.
- Document SmartScreen's "Run anyway" in the README.

### P8. Clean-machine testing
**Windows Sandbox is not available on Windows 11 Home**, which the dev machine runs. Tiers:
1. A new standard local user: no admin, no Python on PATH.
2. GitHub Actions Windows: `/VERYSILENT /CURRENTUSER`, then `selftest` with PATH stripped.
3. A teammate's Pro laptop: `.wsb` with `Networking=Disable`, read-only `dist\`, `LogonCommand` self-test (proves offline).
4. An 8 GB VM if available.

The self-test installs a `sys.addaudithook` that fails on any `socket.connect`.

## 3. Positions on other questions

- **UI stack.** Keep PySide6-Essentials. At 0.2 GB it is not the size problem; torch and CUDA are.
- **Process model.** UI plus an indexer child in a Job Object (`KILL_ON_JOB_CLOSE`): isolation and cancellation, still "no background service". Watch local NTFS roots only while open; network/removable roots get a launch scan.
- **Search during update.** Use the existing index, with progress shown.
- **Index safety.** Batch commits with a scan-generation ID so scans can resume. Run a SQLite online backup before migrations.
- **Models.** The IR role decides, with my constraint: ONNX-exportable, ≤ 150 MB total.

## 4. Risks in my proposal

- **No-torch plan** depends on clean ONNX export and tokenizer parity (`tokenizers`, not `transformers`).
- **DirectML** in maintenance mode may break on future drivers. The CPU fallback bounds the damage.
- **Hydration-free enumeration** is my reading of the attribute docs, not a per-provider guarantee. Non-Cloud-Files providers (possibly Google Drive's virtual drive) need testing.
- **`\\?\` paths** may break extractors that normalise paths, so each extractor needs a long-path test.
- **Unsigned builds** risk SmartScreen/Defender at the demo; apply to SignPath now.
- **Configurable index** causes "where is my index?" confusion. Settings must show the path and an "Open folder" button.

## 5. Sources

- https://pyinstaller.org/en/stable/CHANGES.html
- https://pyinstaller.org/en/stable/operating-mode.html
- https://pypi.org/pypi/pyinstaller-hooks-contrib/json
- https://nuitka.net/posts/nuitka-release-40.html
- https://pydevtools.com/handbook/reference/nuitka.md
- https://briefcase.beeware.org/en/stable/reference/platforms/windows/
- https://jrsoftware.org/isdl.php (7.1.0, 2026-08-12)
- https://jrsoftware.org/files/is7-whatsnew.htm
- https://jrsoftware.org/isorder.php
- https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm
- https://jrsoftware.org/ishelp/topic_admininstallmode.htm
- https://jrsoftware.org/ishelp/topic_consts.htm
- https://www.advancedinstaller.com/application-packaging-training/msix-packaging/ebook/uninstall-and-cleanup-msi-vs-msix.html
- https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options
- https://signpath.org/terms
- https://www.microsoft.com/wdsi/filesubmission
- https://discuss.python.org/t/pyinstaller-false-positive/43171
- https://pypi.org/pypi/torch/2.14.1/json
- https://discuss.pytorch.org/t/pytorch-support-for-sm120/216099
- https://pypi.org/pypi/onnxruntime/1.31.1/json
- https://pypi.org/pypi/onnxruntime-gpu/json
- https://pypi.org/pypi/onnxruntime-directml/json
- https://app.semanticdiff.com/gh/microsoft/onnxruntime/commit/d6123fb9a4805b31ab060edc764b7a070abcc56c
- https://learn.microsoft.com/windows/ai/new-windows-ml/overview
- https://learn.microsoft.com/en-us/windows/win32/fileio/file-attribute-constants
- https://learn.microsoft.com/en-us/windows/win32/api/cfapi/ne-cfapi-cf_placeholder_state
- https://learn.microsoft.com/en-us/windows/win32/cfapi/build-a-cloud-file-sync-engine
- https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation
- https://docs.python.org/3.12/library/os.html
- https://doc.qt.io/qt-6/qlockfile.html
- https://pypi.org/pypi/PySide6-Essentials/json
- https://docs.velopack.io/getting-started/python
- https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-install
- https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/
- https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file
