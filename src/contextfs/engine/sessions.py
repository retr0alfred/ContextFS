"""Work sessions inside time-gap blocks (ADR-0007), on *estimated* last-edit time.

1. Files whose ``rules_v0`` ``last_user_edit`` interval is known to within the gap ``G`` are
   sorted by its point. Files with wider intervals are excluded (bulk-copied, time unknown).
2. Cutting wherever consecutive files are more than ``G`` apart gives independent blocks. Blocks
   above ``B_MAX`` are cut at their largest internal gaps.
3. Within a block, average-linkage agglomeration on affinity:
   0.4·time + 0.3·cosine (doc vectors) + 0.3·folder (no entity term in the MVP; its weight is
   redistributed). Merging stops at ``THRESHOLD``.

``G`` and ``THRESHOLD`` are v1's values (240 h, 0.18), set on the smoke fixture. ADR-0007 §5
requires refitting them on the **dev** split; they are labelled provisional until then.
"""

from __future__ import annotations

import numpy as np

__all__ = ["rebuild_sessions", "G_HOURS", "THRESHOLD", "B_MAX"]

G_HOURS = 240.0  # provisional (dev refit pending)
THRESHOLD = 0.18  # provisional (dev refit pending)
B_MAX = 500  # MVP cap (ADR-0007 says 2,000; raise once the vectorised linkage is benchmarked)


def _folder_affinity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    pa, pb = a.split("/"), b.split("/")
    shared = 0
    for x, y in zip(pa, pb, strict=False):
        if x != y:
            break
        shared += 1
    return shared / (max(len(pa), len(pb)) + 1) if shared else 0.0


def _cluster(times: np.ndarray, vecs: np.ndarray, folders: list[str]) -> list[int]:
    k = len(times)
    if k == 1:
        return [0]
    gap_h = np.abs(times[:, None] - times[None, :]) / 3600.0
    aff = 0.4 * np.clip(1 - gap_h / G_HOURS, 0, 1) + 0.3 * np.clip(vecs @ vecs.T, 0, 1)
    fold = np.array([[_folder_affinity(a, b) for b in folders] for a in folders], dtype=np.float32)
    aff = aff + 0.3 * fold
    np.fill_diagonal(aff, 0.0)
    sums = aff.astype(np.float64).copy()
    sizes = np.ones(k)
    alive = np.ones(k, dtype=bool)
    label = list(range(k))
    while alive.sum() > 1:
        avg = sums / np.outer(sizes, sizes)
        avg[~alive, :] = -1
        avg[:, ~alive] = -1
        np.fill_diagonal(avg, -1)
        i, j = np.unravel_index(np.argmax(avg), avg.shape)
        if avg[i, j] < THRESHOLD:
            break
        sums[i, :] += sums[j, :]
        sums[:, i] = sums[i, :]
        sums[i, i] = 0
        sizes[i] += sizes[j]
        alive[j] = False
        label = [i if lab == j else lab for lab in label]
    return label


def rebuild_sessions(conn) -> int:
    """Recompute all sessions (block partition is a sort; MVP rebuilds every block)."""
    from contextfs.engine.vectors import DIM, VectorFiles

    rows = conn.execute(
        "SELECT f.file_id, f.rel_path, t.point_utc, t.lo_utc, t.hi_utc FROM files f "
        "JOIN file_time t ON t.file_id=f.file_id AND t.estimator='rules_v0' AND t.event='last_user_edit' "
        # rel_path breaks timestamp ties: storage order differs between a resumed and a clean
        # build, and block cuts and clustering follow input order (Phase 33 suite C07)
        "WHERE f.status='present' AND f.access='ok' ORDER BY t.point_utc, f.rel_path"
    ).fetchall()
    rows = [r for r in rows if (r["hi_utc"] - r["lo_utc"]) <= G_HOURS * 3600]
    conn.execute("DELETE FROM sessions")
    if not rows:
        conn.commit()
        return 0
    from pathlib import Path

    index_dir = Path(conn.execute("PRAGMA database_list").fetchone()["file"]).parent
    mat, item_ids, _ = VectorFiles(index_dir / "vectors").load(conn, "doc")
    vec_of = {int(f): mat[i] for i, f in enumerate(item_ids)}
    zero = np.zeros(DIM, np.float32)
    blocks, cur = [], [rows[0]]
    for prev, r in zip(rows, rows[1:], strict=False):
        if (r["point_utc"] - prev["point_utc"]) / 3600.0 > G_HOURS or len(cur) >= B_MAX:
            blocks.append(cur)
            cur = []
        cur.append(r)
    blocks.append(cur)
    out, n_sessions = [], 0
    for b_index, block in enumerate(blocks):
        times = np.array([r["point_utc"] for r in block], dtype=np.float64)
        vecs = np.stack([vec_of.get(r["file_id"], zero) for r in block])
        folders = [r["rel_path"].rsplit("/", 1)[0] if "/" in r["rel_path"] else "" for r in block]
        labels = _cluster(times, vecs, folders)
        groups: dict[int, list[int]] = {}
        for r, lab in zip(block, labels, strict=True):
            groups.setdefault(lab, []).append(r["file_id"])
        for lab, members in groups.items():
            if len(members) < 2:
                continue  # singletons are not sessions
            n_sessions += 1
            out += [(fid, f"s{b_index}-{lab}", f"b{b_index}") for fid in members]
    conn.executemany("INSERT INTO sessions(file_id, session_id, block_id) VALUES (?,?,?)", out)
    conn.commit()
    return n_sessions
