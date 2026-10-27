# SignPath Foundation application — prepared, submit when eligible

Status: **not yet submitted** (log.md Decision 95). Submitted by the user at https://signpath.org/apply,
with the email the user chose (kept out of this public file). The form is behind reCAPTCHA.

## Eligibility checklist (from https://signpath.org/terms, read 2026-10-27)

| Requirement | State | To do |
|---|---|---|
| OSI-approved licence for all components; no commercial dual licensing | `pyproject.toml` says MIT, but **the repo has no LICENSE file** | Add `LICENSE` (MIT) + THIRD-PARTY-NOTICES (ADR-0016 §3) |
| Actively maintained, "already released in the form that should be signed" | No Windows release yet | First portable release in v2-P4 |
| "Built from source code in a verifiable way" (automated CI from the repo) | No CI | GitHub Actions workflow building the PyInstaller one-dir from the lockfile |
| Manual approval of every release before signing | — | Approver = repository owner |
| "Code signing policy" page on the project home page, with the SignPath credit line, team roles (Authors / Reviewers / Approvers), privacy statement | Missing | Add a section to README (or `docs/CODE_SIGNING_POLICY.md` linked from the README) |
| MFA for all team members on SignPath and the repository | Unknown | Enable GitHub 2FA for every committer |
| Product name and version metadata set | — | PyInstaller version resource |
| Privacy: no data sent anywhere; uninstallation provided | ContextFS sends nothing (local-first); portable app removed by deleting its folder | State it in the policy page |

## Draft answers

- **Project name:** ContextFS
- **Repository:** https://github.com/retr0alfred/ContextFS
- **Licence:** MIT
- **Description:** A local-first Windows desktop application that re-finds personal files from
  memory-style natural-language queries ("the PDF I studied before my ML exam"). It indexes the
  folders the user chooses, read-only and fully offline, and explains every result. It is a
  final-year B.E. Computer Science research project and the basis of an IEEE paper.
- **What will be signed:** `ContextFS.exe`, `contextfs.exe` and the bundled PE files of the
  portable PyInstaller build, produced by GitHub Actions from tagged releases.
- **Team:** Alfred Mathew (Author, Approver), Abu Ibrahim Mothi (Author, Reviewer).
- **Privacy:** no telemetry, no network access at runtime. Indexes are stored locally.
