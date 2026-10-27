# Facilitator measurements during the debate

## M1 — What copying does to NTFS timestamps (2026-10-27, dev machine, Windows 11 26200, D: NTFS)

Source file set to created 2025-03-14 08:00:00, modified 2025-03-14 10:00:00, then copied by six
methods (PowerShell script recorded in log.md Phase 30).

| Method | Created | Modified | `created > modified`? | What survives |
|---|---|---|---|---|
| original | 2025-03-14 08:00 | 2025-03-14 10:00 | no | — |
| `Copy-Item` (PowerShell) | **now** | 2025-03-14 10:00 | **yes** | modified kept; copy detectable |
| `cmd copy` | **now** | 2025-03-14 10:00 | **yes** | modified kept; copy detectable |
| `shutil.copy2` | **now** | 2025-03-14 10:00 | **yes** | modified kept; copy detectable |
| `Expand-Archive` (zip) | **now** | 2025-03-14 10:00 | **yes** | modified kept; detectable |
| `robocopy` (default `/COPY:DAT`) | 2025-03-14 08:00 | 2025-03-14 10:00 | no | both kept; **copy invisible** |
| `shutil.copy` (content only) | **now** | **now** | no | **original time destroyed**; FS times cannot recover it |

Consequences for the design:
1. `created > modified` is a usable "this file was copied/extracted" signal for CopyFile-style tools.
   It is **not** sufficient: robocopy leaves no trace, and content-only copies destroy the modified
   time entirely. Only in-file metadata (Office `core.xml`, PDF dates, EXIF, zip entry times) can
   recover the original time in the last case.
2. Explorer's own copy path was not tested directly (Explorer is interactive). It is believed to use
   the same CopyFile semantics as `Copy-Item`. To be confirmed by hand in v2-P3.
3. Cloud-sync clients and backup restores remain untested; the v2-P3 benchmark must simulate all
   three observed patterns (created-reset, preserved, both-reset).
