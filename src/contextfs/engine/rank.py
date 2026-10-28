"""First stage, features, linear scorer and explanations (ADR-0008, ADR-0009, DESIGN.md §3).

    P0 = FTS5 BM25 top-200 docs ∪ dense top-200 docs (exact, document-level candidates)
    x  = {bm25, dense, name, format, time, session}            (per candidate)
    s  = Σ w_i (x_i − a_i)        a = frozen "absent" values;  contribution_i = w_i (x_i − a_i)

The time feature uses ``SearchConfig.time_source`` (``raw_mtime`` | ``rules_v0`` | later
``prob_A`` | ``override``), so P-A2 compares time estimators with one switch.

Default weights are hand-set and labelled ``provisional``. They are to be fitted on the **dev**
split only (``scripts/fit_scorer.py``) and stored as a hashed JSON.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import numpy as np

from contextfs.engine.query import parse

__all__ = ["SearchConfig", "Result", "Response", "search", "FEATURES"]

FEATURES = ("bm25", "dense", "name", "format", "time", "session")
POOL = 200
DENSE_FLOOR = 0.25  # provisional; to be checked on dev (no-answer queries)
CONFIDENCE_WEIGHT = {"sure": 1.0, "probably": 0.7, "unknown": 0.3}


@dataclass(frozen=True)
class SearchConfig:
    """Everything that changes a ranking. Hashed into every response (ADR-0010)."""

    weights: tuple[tuple[str, float], ...] = (
        ("bm25", 0.35),
        ("dense", 0.45),
        ("name", 0.10),
        ("format", 0.25),
        ("time", 0.40),
        ("session", 0.05),
    )
    weights_source: str = "provisional-hand-set"
    time_source: str = "rules_v0"
    signals: tuple[str, ...] = FEATURES
    overrides: tuple[tuple[str, str], ...] = ()
    top_k: int = 10
    time_override: tuple[
        tuple[int, int, int], ...
    ] = ()  # (file_id, lo, hi): oracle, from the harness

    def weight(self, name: str) -> float:
        """Weight of a feature, 0 if masked out."""
        return dict(self.weights).get(name, 0.0) if name in self.signals else 0.0

    def digest(self) -> str:
        """Stable hash of the config."""
        payload = json.dumps(asdict(self), sort_keys=True, default=str).encode()
        return hashlib.sha256(payload).hexdigest()[:16]


@dataclass
class Result:
    """One ranked file with its evidence-grounded explanation."""

    file_id: int
    rel_path: str
    root: str
    rank: int
    score: float
    features: dict[str, float]
    contributions: dict[str, float]
    reasons: list[str]
    doc_refs: dict[str, object]
    snippet: str
    entered_via: str

    @property
    def path(self) -> str:
        """Absolute path on disk."""
        return f"{self.root.rstrip(chr(92) + '/')}\\{self.rel_path.replace('/', chr(92))}"


@dataclass
class Response:
    """Ranked results, how the query was read, and the provenance of the ranking."""

    query: str
    results: list[Result]
    frame: dict
    signals_used: list[str]
    config_hash: str
    generation: str | None
    pool_size: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def paths(self) -> list[str]:
        """Ranked root-relative paths."""
        return [r.rel_path for r in self.results]


def _fts_query(words: list[str]) -> str:
    terms = [w for w in dict.fromkeys(words) if len(w) >= 2][:32]
    return " OR ".join(f'"{w}"' for w in terms)


def _absent(feature: str, values: np.ndarray) -> float:
    """Frozen absent values (ADR-0009): evidence features 0; similarity features the pool median."""
    if feature in ("bm25", "dense") and len(values):
        return float(np.median(values))
    return 0.5 if feature == "format" else 0.0


def search(
    engine, query: str, config: SearchConfig | None = None, as_of: datetime | None = None
) -> Response:
    """Rank the index for ``query``. Never raises on query content."""
    cfg = config or SearchConfig()
    conn = engine.conn
    as_of = as_of or datetime.now(timezone.utc).astimezone()
    frame = parse(query, as_of, dict(cfg.overrides))
    gen_row = conn.execute("SELECT value FROM meta WHERE key='index_generation'").fetchone()
    resp = Response(
        query=frame.text,
        results=[],
        frame=_frame_dict(frame),
        signals_used=[],
        config_hash=cfg.digest(),
        generation=gen_row[0] if gen_row else None,
        notes=frame.notes,
    )
    if not frame.text.strip():
        resp.notes.append("empty query")
        return resp

    # -- first stage --------------------------------------------------------------------------
    bm: dict[int, float] = {}
    fq = _fts_query(frame.words)
    if fq:
        for r in conn.execute(
            "SELECT rowid, bm25(docs_fts) AS s FROM docs_fts WHERE docs_fts MATCH ? ORDER BY s LIMIT ?",
            (fq, POOL),
        ):
            bm[r["rowid"]] = -float(r["s"])  # FTS5 bm25() is negative-is-better
    dense_doc: dict[int, float] = {}
    from contextfs.engine.vectors import VectorFiles

    vf = VectorFiles(engine.index_dir / "vectors")
    qv = engine.encoder.encode_one(frame.text)
    dmat, _, dfiles = vf.load(conn, "doc")
    if len(dmat):
        sims = dmat @ qv
        top = np.argsort(-sims)[:POOL]
        dense_doc = {int(dfiles[i]): float(sims[i]) for i in top}
    # "Nothing found" floor: no word matched and nothing is even moderately similar in meaning.
    if not bm and (not dense_doc or max(dense_doc.values()) < DENSE_FLOOR):
        resp.notes.append("nothing found")
        return resp
    pool = list(dict.fromkeys(list(bm) + list(dense_doc)))
    present = (
        {
            r["file_id"]: r
            for r in conn.execute(
                f"SELECT f.file_id, f.rel_path, f.ext, r.path AS root FROM files f JOIN roots r ON r.root_id=f.root_id "
                f"WHERE f.status IN ('present','offline') AND f.file_id IN ({','.join('?' * len(pool))})",
                pool,
            )
        }
        if pool
        else {}
    )
    pool = [f for f in pool if f in present]
    resp.pool_size = len(pool)
    if not pool:
        resp.notes.append("nothing found")
        return resp

    # -- features -------------------------------------------------------------------------------
    marks = ",".join("?" * len(pool))
    cmat, cids, cfiles = vf.load(conn, "chunk")
    best_chunk: dict[int, tuple[float, int]] = {}
    if len(cmat):
        sel = np.isin(cfiles, np.asarray(pool))
        if sel.any():
            s = cmat[sel] @ qv
            for sim, cid, fid in zip(
                s.tolist(), cids[sel].tolist(), cfiles[sel].tolist(), strict=True
            ):
                if fid not in best_chunk or sim > best_chunk[fid][0]:
                    best_chunk[fid] = (sim, cid)
    x = {f: np.zeros(len(pool)) for f in FEATURES}
    bm_vals = np.array([bm.get(f, 0.0) for f in pool])
    if bm_vals.std() > 0:
        x["bm25"] = (bm_vals - bm_vals.mean()) / bm_vals.std()
    x["dense"] = np.array([best_chunk.get(f, (dense_doc.get(f, 0.0), 0))[0] for f in pool])
    content_words = [w for w in frame.words if len(w) >= 3]
    for i, f in enumerate(pool):
        name_words = set(re.split(r"[\\/_\-.\s()]+", present[f]["rel_path"].lower()))
        x["name"][i] = (
            (sum(w in name_words for w in content_words) / len(content_words))
            if content_words
            else 0.0
        )
        if frame.kinds:
            x["format"][i] = 1.0 if (present[f]["ext"] or "") in frame.kinds else 0.0
        else:
            x["format"][i] = 0.5
    time_refs: dict[int, dict] = {}
    if frame.time:
        times = _file_times(conn, pool, cfg)
        for i, f in enumerate(pool):
            best, ref = 0.0, None
            for cue in frame.time:
                events = (
                    ("authored", "arrived_here", "last_user_edit")
                    if cue.event_hint == "any"
                    else (cue.event_hint,)
                )
                for ev in events:
                    t = times.get((f, ev))
                    if not t:
                        continue
                    v = cue.overlap(t["lo_utc"], t["hi_utc"]) * CONFIDENCE_WEIGHT[t["confidence"]]
                    if v > best:
                        best, ref = v, {"event": ev, "cue": cue.surface, **t}
            x["time"][i] = best
            if ref:
                time_refs[f] = ref
    sess = {
        r["file_id"]: r["session_id"]
        for r in conn.execute(
            f"SELECT file_id, session_id FROM sessions WHERE file_id IN ({marks})", pool
        )
    }
    first = np.argsort(-(x["bm25"] * 0.4 + x["dense"] * 0.6))[:5]
    seed_sessions = {sess.get(pool[j]) for j in first} - {None}
    for i, f in enumerate(pool):
        x["session"][i] = 1.0 if sess.get(f) in seed_sessions and i not in set(first[:1]) else 0.0

    # -- score + contributions ------------------------------------------------------------------
    used = [
        f
        for f in FEATURES
        if cfg.weight(f)
        and not (f == "time" and not frame.time)
        and not (f == "format" and not frame.kinds)
    ]
    resp.signals_used = used
    absent = {f: _absent(f, x[f]) for f in used}
    contrib = {f: cfg.weight(f) * (x[f] - absent[f]) for f in used}
    score = sum(contrib.values()) if used else np.zeros(len(pool))
    order = sorted(
        range(len(pool)), key=lambda i: (-round(float(score[i]), 6), present[pool[i]]["rel_path"])
    )
    rank_of = {i: r for r, i in enumerate(order)}

    # Why-line gate (ADR-0009 §3): a group is a reason only if removing it costs ≥ 1 rank.
    def rank_without(i: int, feat: str) -> int:
        alt = score - contrib[feat]
        return int((alt > alt[i] + 1e-9).sum())

    chunk_text = {}
    top = order[: cfg.top_k]
    if top:
        want = [best_chunk[pool[i]][1] for i in top if pool[i] in best_chunk]
        if want:
            chunk_text = {
                r["chunk_id"]: r["text"]
                for r in conn.execute(
                    f"SELECT chunk_id, text FROM chunks WHERE chunk_id IN ({','.join('?' * len(want))})",
                    want,
                )
            }
    for r_i, i in enumerate(top):
        f = pool[i]
        c = {k: round(float(contrib[k][i]), 4) for k in used}
        reasons, refs = [], {}
        for k in sorted(used, key=lambda k: -abs(c[k])):
            if c[k] > 0 and rank_without(i, k) > rank_of[i]:
                reasons.append(_reason(k, frame, time_refs.get(f), sess.get(f)))
            elif c[k] < 0 and abs(c[k]) > 0.05:
                reasons.append(
                    "counted against: " + _reason(k, frame, time_refs.get(f), sess.get(f))
                )
        if not any(not r.startswith("counted against") for r in reasons):
            reasons.insert(0, "ranked mostly on wording; no single piece of evidence decides it")
        if f in best_chunk:
            refs["dense"] = {"chunk_id": best_chunk[f][1]}
        if f in time_refs:
            refs["time"] = {
                k: time_refs[f][k]
                for k in ("event", "confidence", "method", "evidence_json", "cue")
            }
        if "bm25" in used:
            text = chunk_text.get(best_chunk.get(f, (0, -1))[1], "").lower()
            refs["bm25"] = {"terms_in_evidence": [w for w in content_words if w in text]}
        snippet = _snippet(chunk_text.get(best_chunk.get(f, (0, -1))[1], ""), content_words)
        via = (
            "word match"
            if f in bm and f not in dense_doc
            else "meaning match" if f not in bm else "word + meaning match"
        )
        resp.results.append(
            Result(
                file_id=f,
                rel_path=present[f]["rel_path"],
                root=present[f]["root"],
                rank=r_i + 1,
                score=round(float(score[i]), 6),
                features={k: round(float(x[k][i]), 4) for k in FEATURES},
                contributions=c,
                reasons=reasons,
                doc_refs=refs,
                snippet=snippet,
                entered_via=via,
            )
        )
    return resp


def _file_times(conn, pool, cfg) -> dict[tuple[int, str], dict]:
    if cfg.time_source == "override":
        return {
            (fid, ev): {
                "lo_utc": lo,
                "hi_utc": hi,
                "point_utc": (lo + hi) // 2,
                "confidence": "sure",
                "method": "oracle override",
                "evidence_json": "[]",
            }
            for fid, lo, hi in cfg.time_override
            for ev in ("authored", "arrived_here", "last_user_edit")
        }
    rows = conn.execute(
        f"SELECT file_id, event, lo_utc, hi_utc, point_utc, confidence, method, evidence_json FROM file_time "
        f"WHERE estimator=? AND file_id IN ({','.join('?' * len(pool))})",
        (cfg.time_source, *pool),
    )
    return {(r["file_id"], r["event"]): dict(r) for r in rows}


def _reason(feature: str, frame, time_ref, session_id) -> str:
    if feature == "time" and time_ref:
        label = {
            "authored": "written",
            "arrived_here": "arrived on this PC",
            "last_user_edit": "last edited",
        }
        when = (
            datetime.fromtimestamp(time_ref["point_utc"], timezone.utc)
            .astimezone()
            .strftime("%d %b %Y")
        )
        return f"{label[time_ref['event']]} {when} ({time_ref['confidence']}), matches '{time_ref['cue']}'"
    if feature == "format":
        return f"is a {frame.kind_word} file"
    if feature == "session":
        return "worked on together with other strong matches"
    if feature == "name":
        return "file name matches your words"
    if feature == "bm25":
        return "contains your words"
    return "content means something similar"


def _snippet(text: str, words: list[str], width: int = 220) -> str:
    if not text:
        return ""
    low = text.lower()
    pos = min((low.find(w) for w in words if low.find(w) >= 0), default=0)
    start = max(0, pos - width // 3)
    out = " ".join(text[start : start + width].split())
    return ("…" if start else "") + out + ("…" if start + width < len(text) else "")


def _frame_dict(frame) -> dict:
    return {
        "text": frame.text,
        "kinds": list(frame.kinds),
        "kind_word": frame.kind_word,
        "time": [
            {
                "lo": c.lo.isoformat(),
                "hi": c.hi.isoformat(),
                "band_days": round(c.band_days, 1),
                "event_hint": c.event_hint,
                "surface": c.surface,
            }
            for c in frame.time
        ],
        "notes": frame.notes,
    }


_ = math  # math kept for future calibrated transforms
