# ADR-0016: Operations: logging, index privacy, licences, upgrades, language scope

- **Status:** Accepted · **Date:** 2026-10-27 · **Phase:** v2-P2 (from `review/fresh_review.md` §3.1–3.3, 3.6, 3.7)

## Decision
1. **Logging and diagnostics**
   - Rotating logs in `%LOCALAPPDATA%\ContextFS\logs`, capped at 25 MB.
   - Queries, paths and names are **redacted by default** (replaced by hashes). A Settings toggle
     enables verbose local logging.
   - `faulthandler` is enabled in the app, the indexer child and the workers. A Windows minidump is
     written for a child crash.
   - "Export diagnostics" builds a zip the user **reviews on screen** before saving. Nothing is
     ever sent anywhere.
2. **Privacy of the index.** The index is a plaintext copy of document text and author names.
   - On creation:
     - set an **owner-only ACL** on the index directory;
     - set `FILE_ATTRIBUTE_NOT_CONTENT_INDEXED`;
     - refuse locations inside OneDrive-synced folders (no cloud backup of the index).
   - EFS-encrypted files: detect `FILE_ATTRIBUTE_ENCRYPTED` and index them **metadata-only** by
     default, with an opt-in.
   - Roots on removable or BitLocker volumes: warn that their text will be stored in an unencrypted
     index.
   - Temp copies (ADR-0011 §4) are deleted after parsing and swept at startup.
   - "Delete my index" in Settings and at uninstall.
3. **Licences**
   - `THIRD-PARTY-NOTICES` ships in the bundle: PySide6/Qt LGPL-3 (dynamic linking, with notice
     and source offer), ONNX Runtime MIT, model licences (MiniLM Apache-2.0, granite Apache-2.0),
     SQLite public domain, and the rest.
   - A CI licence gate rejects AGPL or non-commercial packages in the **app** bundle (for example,
     PyMuPDF AGPL).
   - **The MiniLM cross-encoder was trained on MS MARCO, whose terms are non-commercial**, so it
     stays **evaluation-only** and is never shipped.
   - The query-generation LLM's licence and the released benchmark generator's licence (MIT) are
     recorded.
4. **Upgrades in the field.** A change in `embedder_fp`, `estimator_version` or the schema is
   detected at launch.
   - The indexer child re-embeds or re-estimates in the background, writing new segments and rows
     alongside the old ones.
   - The app keeps searching the old generation and swaps when the rebuild completes (ADR-0012
     Rev. 1).
5. **Language scope.** MiniLM is English-only. Non-English text (e.g. Hindi, Tamil) gets lexical
   BM25 only, and dense similarity there is unreliable. The README and the paper state this.
   Multilingual support is the granite evaluation row (ADR-0003), not a claim.

## Revision 1 — portable app (2026-10-27, log.md Decision 95)
There is no uninstaller (ADR-0004 Rev. 2). "Delete my index" lives in Settings only, and deleting
the app folder removes the program. Removing the app's settings and index is a Settings action, and
the README documents it.
