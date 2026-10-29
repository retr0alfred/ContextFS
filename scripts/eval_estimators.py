r"""Write evaluation-only estimators' file_time rows into an index (log.md Phase 35).

    python scripts/eval_estimators.py INDEX_DIR [INDEX_DIR ...]

The shipped engine computes raw_mtime, rules_v0 and recon_v1. Baselines that exist only for the
evaluation (``rules_v0_bulk``: rules_v0 with recon_v1's bulk-copy detector, the fairer comparison
asked for by the second independent review) are written here, through the engine's own
``estimate_folder`` and the index's stored bulk-copy events, under their own estimator name, so
``SearchConfig.time_source`` and ``scripts/time_eval.py`` can use them like any other source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def write_eval_rows(index: Path) -> int:
    from contextfs.engine.estimate import RulesV0Bulk
    from contextfs.engine.indexer import _chunks, _folder_of, estimate_folder, refresh_bulk_events
    from contextfs.engine.schema import connect

    est = RulesV0Bulk()
    conn = connect(index / "contextfs.db")
    refresh_bulk_events(conn)
    conn.execute("DELETE FROM file_time WHERE estimator=?", (est.name,))
    n = 0
    for (root_id,) in conn.execute("SELECT root_id FROM roots WHERE state!='removed'").fetchall():
        row = conn.execute("SELECT value FROM meta WHERE key=?", (f"bulk_events:{root_id}",)).fetchone()
        bulk = [tuple(e) for e in json.loads(row[0] if row else "[]")]
        folders = {_folder_of(r[0]) for r in conn.execute(
            "SELECT rel_path FROM files WHERE root_id=? AND status IN ('present','offline')", (root_id,))}
        for folder in folders:
            _, rows = estimate_folder(conn, root_id, folder, bulk, estimators=[est])
            for part in _chunks(rows, 500):
                conn.executemany(
                    "INSERT INTO file_time(file_id, estimator, event, lo_utc, hi_utc, point_utc, confidence, method,"
                    " evidence_json) VALUES (?,?,?,?,?,?,?,?,?)", part)
            n += len(rows)
    conn.commit()
    conn.close()
    return n


def main() -> int:
    for arg in sys.argv[1:]:
        print(f"{arg}: {write_eval_rows(Path(arg))} rules_v0_bulk rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
