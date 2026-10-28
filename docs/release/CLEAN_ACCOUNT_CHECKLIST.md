# Portable build — acceptance checklist (Decision 95)

Build: `.venv\Scripts\python.exe scripts\build_app.py` → `dist\ContextFS\` (≈ 330 MB, 288 files).
Nothing is installed: the folder *is* the app. Settings, the index and logs live in
`%LOCALAPPDATA%\ContextFS\` of whoever runs it.

## Already verified by Claude (Phase 32, log.md)

| Check | How | Result |
|---|---|---|
| Frozen self test, scrubbed environment | `ContextFS-console.exe --selftest --gpu`, `env -i` with only Windows on PATH, run from an unrelated folder | ALL PASS |
| No code from the dev environment | the self test lists every loaded Python module and DLL | none from `.venv` / `.tools` / another Python (two DLLs injected by Nahimic audio and Defender, listed as notes) |
| Format extractors bundled | the self test indexes a .docx, .xlsx and .pdf it writes itself | all three found by content |
| Frozen GUI → frozen indexer child | `ContextFS.exe` launched offscreen with a saved settings file | 229 files indexed by the child, GUI kept running, empty error log |
| Windowed child pipes, cancel, resume | the windowed exe driven by QProcess, cancel after 4 s | cancelled cleanly (44 files), resume added the other 185, 0 errors |
| GPU choice and probe | DirectML adapter enumeration + 200-text probe | picks the RTX 5060, not adapter 0 (the Radeon 780M); probe cosine 1.000000 |

## Needs you at the keyboard

### A. On this laptop (the acceptance demo)
1. Double-click `dist\ContextFS\ContextFS.exe`.
   - If **SmartScreen** says "Windows protected your PC": More info → Run anyway (the build is
     unsigned; signing is off the critical path).
   - Smart App Control on this laptop is in **Evaluation** mode. If it ever blocks the app, tell me;
     do not turn it off on this machine (your instruction).
2. First run: pick one or two real folders (e.g. Documents\College), keep the default exclusions.
3. While it indexes, search right away (search works during indexing). Then try:
   - a content query ("notes on normalization"),
   - a type + time query ("the pdf I downloaded last month"),
   - click a result → check **Dates for this file** (written / arrived on this PC / last edited,
     plus the raw clues).
4. Close the window during an update, reopen → it should say it resumed and finish.
5. Settings → tick/untick the GPU box → the status bar says which device embedded new files.

### B. Light clean-account check (a second Windows account, or another PC)
1. Create a **standard** local account (Settings → Accounts → Other users → Add account →
   "I don't have this person's sign-in information" → "Add a user without a Microsoft account"),
   or use any other Windows 10/11 PC you are allowed to test on.
2. Copy the whole `dist\ContextFS` folder somewhere that account can read (e.g. `C:\Users\Public\`
   on that account's machine, or a USB stick).
3. Signed in as that account, open **Command Prompt** in the copied folder and run
   `ContextFS-console.exe --selftest`. Add `--gpu` only on a machine whose owner is fine with
   a short GPU test.
4. Double-click `ContextFS.exe` and repeat A.2–A.3 on a small folder.
5. Send me: the self-test output, any error dialog (screenshot), and
   `%LOCALAPPDATA%\ContextFS\logs\indexer.log` if it is not empty.

If Smart App Control is **On** on that other machine, the unsigned build will be blocked. The
documented fallback (turning SAC off) is only for a disposable test machine or another person's
machine with their consent, never your daily laptop.

## Not done (stretch, Decision 95)
- VM testing (1-day stretch goal).
- Code signing (SignPath application prepared in `signpath_application.md`, not submitted: the form
  needs a reCAPTCHA and the project needs a public release + licence first).
