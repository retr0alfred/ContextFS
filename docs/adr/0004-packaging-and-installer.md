# ADR-0004: Packaging — portable PyInstaller one-dir app (no installer; Revision 2), signing when eligible

- **Status:** Accepted · **Date:** 2026-10-27 · **Phase:** v2-P2
- **Debate record:** packaging R1/R2; skeptic R1 (V-claims), R2 (N8, N14, N17)

## Context
Users install by double-clicking: no Python, no admin rights, offline after install. Dev machine is
Windows 11 **Home** (no Hyper-V, no Windows Sandbox). The student cannot get Azure Artifact Signing
(US/Canada individuals only).

## Options considered
| Option | For | Against |
|---|---|---|
| **PyInstaller one-dir** | Mature hooks for Qt, numpy and ORT; fast start | AV false positives are possible |
| PyInstaller one-file | Single exe | Unpacks to %TEMP% on every launch; slow; worse AV behaviour |
| Nuitka | Compiled; sometimes faster | Long builds; spaCy/ORT edge cases; harder to debug |
| Embedded Python + launcher | Transparent | DIY; fragile |
| MSIX / WiX | Store-ready / enterprise | Signing required; admin or complexity |
| **Inno Setup, per-user** | No UAC; shortcuts; clean uninstall | Unsigned → SmartScreen warning |

## Decision
1. **Bundle layout:**
   - PyInstaller one-dir, no UPX;
   - `ContextFS.exe` (GUI) and `contextfs.exe` (CLI) share one `_internal`;
   - a bundled MiniLM int8 ONNX model, so the first run works offline;
   - no torch, CUDA, transformers, LanceDB, pyarrow or NetworkX in the bundle.
   - **Size budget:** ≤ 0.6 GB installed, ≤ 300 MB installer.
2. **Installer:** Inno Setup, `PrivilegesRequired=lowest`.
   - Installs to `%LOCALAPPDATA%\Programs\ContextFS`, with Start-menu and desktop shortcuts.
   - Upgrade in place.
   - **Uninstall removes only the program folder.** The index and settings in
     `%LOCALAPPDATA%\ContextFS` are kept unless the user ticks "also delete my index".
3. **No network at runtime.** An audit hook in `selftest` and in CI fails on any socket use. The
   models are bundled, so there is no download step.
4. **Signing:** unsigned builds. Publish SHA-256 hashes, submit to Microsoft WDSI for false-positive
   review, and rehearse installing on the viva machine a week before. SignPath Foundation is LATER,
   off the critical path.
5. **Clean-machine testing** (Home edition): VirtualBox with the Windows 11 Enterprise evaluation
   ISO.
   - Use a "clean" snapshot on D:, with the network disconnected.
   - Install as a standard user, run `selftest`, run the hardening suite, uninstall.
   - Plus the **old 3700U laptop** on a fresh standard user, for real timings.
   - VM timings are never reported as performance numbers. The evaluation copy needs a Microsoft
     sign-in and expires after 90 days (skeptic N14).
6. **The freeze is spiked first (skeptic N17):** P4 week 1 freezes PySide6 + ORT + spaCy + numpy and
   proves `selftest` passes. If spaCy fails to freeze twice, NER leaves the app (D8) and the
   ablation records it.
7. **Dependency pins:** releases at least two weeks old (N8), recorded in the lockfile; the frozen
   build vs venv parity test runs in CI.

## Consequences
- An unsigned installer shows a SmartScreen "unknown publisher" prompt. This is documented in the
  install guide.
- Updates are reinstalls. There is no auto-updater (cut).

## Objections
- **Skeptic:** the evaluation VM expires and needs a Microsoft account. *Accepted*; the old laptop is
  the backup clean machine.
- **Packaging (R1):** DirectML package as default. *Moved behind the ADR-0003 gates.*

## How we will know it was wrong
Freeze parity fails twice; the installer is flagged by Defender after WDSI; or it is > 0.6 GB
installed.

## Revision 1 — after the fresh review (2026-10-27): code signing moves into the MVP
Supersedes §4. Source: `review/fresh_review.md` B3.
- **Smart App Control blocks unsigned apps.** It is on by default on clean installs of
  Windows 11 22H2 and later. When its cloud check cannot vouch for a binary, it blocks unsigned
  executables with no "run anyway" option. A clean machine is exactly the brief's acceptance test,
  so **an unsigned build can fail the requirement.**
- **Decision:** every PE file in the bundle and the installer is Authenticode-signed before the
  v2-P4 clean-machine test.
- **Options, all needing the user** (cost and an identity tied to the certificate):
  1. SignPath Foundation (free for OSS; application review, timeline uncertain);
  2. a Certum open-source code-signing certificate (low annual cost; token or cloud signing; confirm
     Authenticode support before buying);
  3. the institution's certificate, if the department has one.
  - **This is a user decision, raised at the Phase 2 stop.**
- **Test:** a fresh VM install with Smart App Control **on** (it can only be turned on during a
  clean install), plus SmartScreen, plus the old laptop.
- **Fallback if no certificate in time:** document that SAC must be off (it is off on most upgraded
  machines). Record this as a deviation the user approves.

## Revision 2 — user decision before Phase 3 (2026-10-27, log.md Decision 95)
Supersedes §2, §5 and Revision 1 where they conflict.

**No installer.** The deliverable is a **portable app**: a folder (PyInstaller one-dir) containing
`ContextFS.exe`, run by double-clicking. It looks and behaves like a normal app once open.
- There is no Inno Setup, no Add/Remove Programs entry, no Start-menu or registry integration, and
  no uninstaller.
- To remove it, delete the folder. The index is deleted from Settings → "Delete my index".
- **One-dir, not one-file.** A one-file exe unpacks about 0.5 GB to `%TEMP%` on **every** launch,
  which adds seconds to startup and leaves junk behind after a crash. The one-dir folder is the
  portable form. A one-file build can be produced later if wanted; the trade-off is recorded here.
- The index and settings still default to `%LOCALAPPDATA%\ContextFS`, configurable. On the author's
  machine they are set to D:.

**Acceptance bar** (the demo runs on the author's own laptop):
- **MVP check (light):**
  - (a) Run the built folder **as Alfred** from a fresh location, in a scrubbed environment: no
    Python on PATH, no `PYTHON*`/`HF_*`/`PIP_*` variables, and an empty data directory to force
    first run. Run `ContextFS.exe --selftest` headless with the no-network audit hook. Verify the
    process loads nothing from `.venv` or `.tools`.
  - (b) **User at the keyboard:** copy the folder to a second, standard Windows account on the same
    laptop (or the old laptop), double-click it, and walk through the first-run checklist.
- **Stretch goal, time-boxed to 1 day, after the research work:** the VirtualBox / evaluation-ISO /
  unattended-install pipeline. It is no longer blocking. It can be resumed calmly after submission
  if the app is distributed more widely.

**Code signing:**
- **SignPath Foundation: prepared, not yet submitted.** Facts as of 2026-10-27:
  - Its terms require a project "already released in the form that should be signed", binaries
    "built from source code in a verifiable way" (CI), a published "Code signing policy" page, and
    MFA for all team members.
  - Its application form sits behind reCAPTCHA, which Claude is not permitted to complete.
  - Therefore: after the first CI-built release of the portable app (v2-P4), the user submits the
    prepared application (`docs/release/signpath_application.md`) with the email the user chose.
  - Not on the critical path.
- **Working path:** unsigned build. The documented fallback "Smart App Control must be off" applies
  **only to disposable test environments and other people's machines**. The author will not
  disable SAC on their daily machine.
- **Measured risk on the author's machine:** SAC is in **Evaluation mode**
  (`VerifiedAndReputablePolicyState = 2`).
  - In this mode it does not block, but Windows may switch it to **On** automatically.
  - If that happens before the demo, an unsigned `ContextFS.exe` could be blocked on the demo
    laptop itself, and SAC cannot be re-enabled once turned off.
  - Mitigations, in order:
    1. Re-check the SAC state before every demo build (one PowerShell line, part of `selftest`).
    2. Get the build signed if SignPath approves in time.
    3. As a last resort, the dev-mode path: run from source through `start.bat`. This depends on
       how SAC treats the unsigned portable Python, which must be re-checked if SAC turns On.
  - **Recorded as an open risk.**
