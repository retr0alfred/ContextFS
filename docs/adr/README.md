# Architecture Decision Records (v2)

Produced by the v2-P2 design review (log.md Phase 30). Six roles debated in two rounds: systems
architect, IR/ML researcher, Windows/packaging engineer, performance engineer, UX lead and a
red-team skeptic. The full record is in `debate/` (brief, round 1, round 2, facilitator
measurements). A fresh reviewer that saw none of the debate then critiqued the result
(`review/`).

| ADR | Decision |
|---|---|
| [0001](0001-process-model.md) | UI+query process, one indexer child as sole writer, no watcher; search never waits |
| [0002](0002-ui-stack.md) | Keep PySide6-Essentials; cut 3D graph, Insights and the semantic tree from the app |
| [0003](0003-inference-runtime-and-gpu.md) | ONNX Runtime CPU in app and eval; MiniLM int8 default; GPU for bulk indexing only behind DirectML parity gates (**deviation to acknowledge**) |
| [0004](0004-packaging-and-installer.md) | PyInstaller one-dir + per-user Inno Setup; unsigned with hashes; VirtualBox + old-laptop clean tests |
| [0005](0005-storage.md) | SQLite single commit point; exact f32 document-level vectors; no stored semantic graph; drop LanceDB/NetworkX |
| [0006](0006-time-evidence-and-reconciled-time.md) | Multi-source time evidence; per-event reconciled time; non-circular evaluation protocol E1–E5 |
| [0007](0007-sessions-at-scale.md) | Exact clustering inside time-gap blocks (cap 2,000); incremental by block hash; G fitted on dev |
| [0008](0008-retrieval-and-query-understanding.md) | Shared FTS5 + exact dense first stage; ContextFS signals as features; dev-fitted linear scorer; SymSpell; relational time |
| [0009](0009-faithful-explanations.md) | Signed contributions vs frozen absent values; every reason cites document evidence |
| [0010](0010-engine-api-and-parity.md) | One `contextfs.engine`; deterministic; GUI = CLI = frozen eval, tested |
| [0011](0011-filesystem-access.md) | `fsio`: `\\?\`, no junction descent, placeholders never opened, share-delete reads, resumable scans |
| [0012](0012-index-safety.md) | Writer lock, migrations, quick_check recovery, WAL and disk guards |
| [0013](0013-first-run-and-search-ux.md) | Three-screen first run; never-wait search; dates panel; chips; Issues; Research mode |
| [0014](0014-scope-and-cut-order.md) | MVP / LATER / CUT, dated 12-week plan, cut order, tests required before any paper number |
| [0015](0015-benchmark-and-study.md) | Procedural benchmark (D): splits, frozen undamaged-time model, damage simulator + real-tool replay, three pre-registered primaries, real-user study |
| [0016](0016-operations-privacy-licences.md) | Logging, index privacy, licences, field upgrades, language scope |

Most ADRs carry a **Revision 1** section added after the fresh review; it supersedes conflicting
clauses above it. `review/response.md` maps every review finding to its resolution.

**Open user decisions (Phase 2 stop):** code signing (ADR-0004 Rev. 1) and the GPU deviation
(ADR-0003).
