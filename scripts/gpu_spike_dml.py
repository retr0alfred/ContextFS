r"""DirectML GPU spike for bulk indexing (ADR-0003 §4 + Revision 1). Dev personas only.

Gates (fp32 on DirectML vs fp32 on the CPU EP, matched precision):
  (a) cosine >= 0.999 vs CPU-EP vectors on 2,000 chunks (every chunk, not just the mean)
  (b) top-10 identical on >= 99% of dev queries when a DML-built index is queried on the CPU EP
  (c) >= 2x bulk embedding speed vs the CPU EP (4 threads) on this machine

``onnxruntime-directml`` and ``onnxruntime`` cannot share a venv, so steps run in two venvs:

    .tools\venv-dml\Scripts\python.exe scripts\gpu_spike_dml.py adapters
    .tools\venv-dml\Scripts\python.exe scripts\gpu_spike_dml.py embed --device-id N   # gates a, c; builds index_v2_dml
    .venv\Scripts\python.exe scripts\gpu_spike_dml.py compare                          # gate b (CPU EP queries)

``adapters`` touches no GPU. ``embed`` is a real GPU workload: run it only after the user has
confirmed the adapter (CLAUDE.md GPU rule).
"""

from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "models" / "onnx" / "minilm-l6-v2"
BENCH = ROOT / "data" / "bench"
OUT = ROOT / "data" / "gpu_spike"
DIM = 384


def dev_personas() -> list[Path]:
    dirs = sorted(d for d in BENCH.iterdir() if "-A-" in d.name and (d / "index_v2" / "contextfs.db").exists())
    assert all("-B-" not in d.name for d in dirs), "test personas must never be used"
    return dirs


# -- adapters (no GPU work) --------------------------------------------------------------------


class _LUID(ctypes.Structure):
    _fields_ = [("Low", ctypes.c_uint32), ("High", ctypes.c_int32)]


class _DESC1(ctypes.Structure):
    _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint), ("DeviceId", ctypes.c_uint),
                ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint), ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                ("AdapterLuid", _LUID), ("Flags", ctypes.c_uint)]


def _vcall(obj, index, restype, *args):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    argtypes = [ctypes.c_void_p if type(a).__name__ == "CArgObject" else type(a) for a in args]  # byref(...)
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])
    return fn(obj, *args)


def adapters() -> list[dict]:
    """DXGI adapters in EnumAdapters1 order (the order DirectML's device_id indexes)."""
    import uuid

    # IID_IDXGIFactory1, in the in-memory (little-endian) GUID layout
    iid = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID("770aae78-f26f-4dba-a829-253c83d1b387").bytes_le)
    factory = ctypes.c_void_p()
    hr = ctypes.windll.dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory))
    if hr != 0:
        raise OSError(f"CreateDXGIFactory1 failed: {hr:#x}")
    out, i = [], 0
    while True:
        adapter = ctypes.c_void_p()
        if _vcall(factory, 12, ctypes.c_long, ctypes.c_uint(i), ctypes.byref(adapter)) != 0:  # EnumAdapters1
            break
        desc = _DESC1()
        _vcall(adapter, 10, ctypes.c_long, ctypes.byref(desc))  # GetDesc1
        out.append({"device_id": i, "name": desc.Description, "vendor": f"{desc.VendorId:#06x}",
                    "dedicated_vram_mb": desc.DedicatedVideoMemory // 2**20, "software": bool(desc.Flags & 2)})
        _vcall(adapter, 2, ctypes.c_ulong)  # Release
        i += 1
    _vcall(factory, 2, ctypes.c_ulong)
    return out


# -- embedding -----------------------------------------------------------------------------------


def _encoder_class():
    spec = importlib.util.spec_from_file_location("encoder", ROOT / "src" / "contextfs" / "engine" / "encoder.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.OnnxEncoder


def make_encoders(device_id: int):
    import onnxruntime as ort

    enc_cls = _encoder_class()
    cpu = enc_cls(MODEL, threads=4)
    dml = enc_cls(MODEL, threads=4)
    opts = ort.SessionOptions()
    opts.enable_mem_pattern = False  # required by the DirectML EP
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    dml.session = ort.InferenceSession(str(MODEL / "model_fp32.onnx"), sess_options=opts,
                                       providers=[("DmlExecutionProvider", {"device_id": device_id})])
    assert dml.session.get_providers()[0] == "DmlExecutionProvider", dml.session.get_providers()
    return cpu, dml


def chunk_texts(db: Path) -> list[tuple[int, int, int, str]]:
    """(row, chunk_id, file_id, embedded text) for the valid chunk vectors of one index, in row order.

    The embedded text comes from the indexer's own rule (name words prefix the first chunk), so a
    rebuilt vector is exactly what a DirectML indexer would have written.
    """
    sys.path.insert(0, str(ROOT / "src"))
    from contextfs.engine.indexer import _name_words, embedding_text

    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = c.execute("SELECT v.row, v.item_id, v.file_id, ch.text, ch.ord, ch.char_end, f.rel_path "
                     "FROM vectors v JOIN chunks ch ON ch.chunk_id=v.item_id JOIN files f ON f.file_id=v.file_id "
                     "WHERE v.kind='chunk' AND v.valid=1 ORDER BY v.row").fetchall()
    c.close()
    return [(row, cid, fid, embedding_text(rel, ord_, text, name_only=(end == 0 and text == _name_words(rel))))
            for row, cid, fid, text, ord_, end, rel in rows]


def vram_in_use() -> str:
    try:
        return subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader"],
                              capture_output=True, text=True, timeout=20).stdout.strip() or "(no compute apps listed)"
    except OSError as exc:
        return f"nvidia-smi unavailable: {exc}"


def embed(device_id: int, n_chunks: int) -> int:
    import onnxruntime as ort

    ad = {a["device_id"]: a for a in adapters()}[device_id]
    print(f"onnxruntime {ort.__version__}; DirectML device_id {device_id} = {ad['name']} "
          f"({ad['dedicated_vram_mb']} MB dedicated)")
    cpu, dml = make_encoders(device_id)
    personas = dev_personas()
    pool = [t for d in personas for (_, _, _, t) in chunk_texts(d / "index_v2" / "contextfs.db")]
    rng = np.random.default_rng(7)
    texts = [pool[i] for i in sorted(rng.choice(len(pool), size=min(n_chunks, len(pool)), replace=False))]
    dml.encode(texts[:64])  # warm-up (graph compile / allocation), excluded from timing
    print("VRAM during warm-up:", vram_in_use())
    t = time.perf_counter()
    v_cpu = cpu.encode(texts)
    cpu_s = time.perf_counter() - t
    t = time.perf_counter()
    v_dml = dml.encode(texts)
    dml_s = time.perf_counter() - t
    cos = (v_cpu * v_dml).sum(axis=1)
    res = {"date": datetime.now().date().isoformat(), "ort": ort.__version__, "adapter": ad,
           "chunks": len(texts), "cos_min": float(cos.min()), "cos_mean": float(cos.mean()),
           "cos_p01": float(np.quantile(cos, 0.01)), "cpu_chunks_per_s": len(texts) / cpu_s,
           "dml_chunks_per_s": len(texts) / dml_s, "speedup": cpu_s / dml_s}
    res["gate_a"] = res["cos_min"] >= 0.999
    res["gate_c"] = res["speedup"] >= 2.0
    print(json.dumps(res, indent=2))
    # Gate (b) input: the same dev indexes with every vector recomputed on DirectML.
    for d in personas:
        src, dst = d / "index_v2", d / "index_v2_dml"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("writer.lock", "tmp"))
        rows = chunk_texts(dst / "contextfs.db")
        vec = dml.encode([t for *_, t in rows])
        chunk_mat = np.fromfile(dst / "vectors" / "chunk.f32", dtype=np.float32).reshape(-1, DIM)
        by_file: dict[int, list[int]] = {}
        for i, (row, _, fid, _) in enumerate(rows):
            chunk_mat[row] = vec[i]
            by_file.setdefault(fid, []).append(i)
        chunk_mat.tofile(dst / "vectors" / "chunk.f32")
        doc_mat = np.fromfile(dst / "vectors" / "doc.f32", dtype=np.float32).reshape(-1, DIM)
        c = sqlite3.connect(dst / "contextfs.db")
        for row, fid in c.execute("SELECT row, file_id FROM vectors WHERE kind='doc' AND valid=1"):
            m = vec[by_file[fid]].mean(axis=0)
            doc_mat[row] = m / max(np.linalg.norm(m), 1e-12)
        c.close()
        doc_mat.tofile(dst / "vectors" / "doc.f32")
    print(f"rebuilt vectors on DirectML for {len(personas)} dev personas (index_v2_dml)")
    print("VRAM after:", vram_in_use())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "gates_a_c.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    return 0


# -- gate (b): queries on the CPU EP over the CPU-built vs the DML-built index ---------------------


def compare() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from contextfs.engine import Engine
    from contextfs.engine.rank import SearchConfig

    same = total = 0
    for d in dev_personas():
        meta = json.loads((d / "truth" / "persona.json").read_text(encoding="utf-8"))
        as_of = datetime.fromisoformat(meta["persona"]["end"]).astimezone()
        queries = [json.loads(x)["text"] for x in
                   (d / "truth" / "queries_llm.jsonl").read_text(encoding="utf-8").splitlines()]
        with Engine.open(d / "index_v2") as a, Engine.open(d / "index_v2_dml") as b:
            for q in queries:
                total += 1
                same += a.search(q, SearchConfig(), as_of=as_of).paths[:10] == \
                    b.search(q, SearchConfig(), as_of=as_of).paths[:10]
    res = {"queries": total, "top10_identical": same, "share": same / total, "gate_b": same / total >= 0.99}
    print(json.dumps(res, indent=2))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "gate_b.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("adapters")
    e = sub.add_parser("embed")
    e.add_argument("--device-id", type=int, required=True)
    e.add_argument("--chunks", type=int, default=2000)
    sub.add_parser("compare")
    args = parser.parse_args()
    if args.cmd == "adapters":
        print(json.dumps(adapters(), indent=2))
        return 0
    if args.cmd == "embed":
        return embed(args.device_id, args.chunks)
    return compare()


if __name__ == "__main__":
    raise SystemExit(main())
