# ADR-0004: Packaging — PyInstaller one-dir, per-user Inno Setup installer, unsigned with published hashes

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
