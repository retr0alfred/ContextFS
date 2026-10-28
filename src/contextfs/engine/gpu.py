"""GPU for bulk indexing: adapter choice and the on-device parity probe (ADR-0003 §4, Rev. 1).

Queries always run on the CPU EP. Only the indexer may embed on a GPU, and only if:

1. Settings allow it (``GPU: Auto``);
2. a **discrete** adapter exists (DXGI, not software, >= 2 GB dedicated memory). On hybrid
   laptops DirectML's adapter 0 is usually the integrated GPU (log.md Phase 32: the Radeon 780M
   here), so the adapter is chosen explicitly, never left at the default;
3. the probe passes: 200 fixed texts embedded on that GPU and on the CPU EP, mean cosine
   >= 0.999 (and every one >= 0.99).

Any failure, including an exception, falls back to the CPU with a stated reason. Nothing here
raises.
"""

from __future__ import annotations

import ctypes
import sys

import numpy as np

__all__ = ["adapters", "choose_adapter", "probe_texts", "gpu_encoder", "MIN_DEDICATED_MB"]

MIN_DEDICATED_MB = 2048
PROBE_MEAN, PROBE_MIN = 0.999, 0.99


class _LUID(ctypes.Structure):
    _fields_ = [("Low", ctypes.c_uint32), ("High", ctypes.c_int32)]


class _DESC1(ctypes.Structure):
    _fields_ = [
        ("Description", ctypes.c_wchar * 128),
        ("VendorId", ctypes.c_uint),
        ("DeviceId", ctypes.c_uint),
        ("SubSysId", ctypes.c_uint),
        ("Revision", ctypes.c_uint),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuid", _LUID),
        ("Flags", ctypes.c_uint),
    ]


def _vcall(obj, index, restype, *args):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    argtypes = [ctypes.c_void_p if type(a).__name__ == "CArgObject" else type(a) for a in args]
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])
    return fn(obj, *args)


def adapters() -> list[dict]:
    """DXGI adapters in ``EnumAdapters1`` order, which is the order DirectML's ``device_id`` uses.

    Read-only: no GPU work. Returns [] off Windows or on any failure.
    """
    if sys.platform != "win32":
        return []
    import uuid

    try:
        iid = (ctypes.c_byte * 16).from_buffer_copy(
            uuid.UUID("770aae78-f26f-4dba-a829-253c83d1b387").bytes_le  # IID_IDXGIFactory1
        )
        factory = ctypes.c_void_p()
        if ctypes.windll.dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory)) != 0:
            return []
        out, i = [], 0
        while True:
            adapter = ctypes.c_void_p()
            if _vcall(factory, 12, ctypes.c_long, ctypes.c_uint(i), ctypes.byref(adapter)) != 0:
                break  # EnumAdapters1 returns DXGI_ERROR_NOT_FOUND past the last adapter
            desc = _DESC1()
            _vcall(adapter, 10, ctypes.c_long, ctypes.byref(desc))  # GetDesc1
            out.append(
                {
                    "device_id": i,
                    "name": desc.Description,
                    "vendor": f"{desc.VendorId:#06x}",
                    "dedicated_mb": desc.DedicatedVideoMemory // 2**20,
                    "software": bool(desc.Flags & 2),  # DXGI_ADAPTER_FLAG_SOFTWARE
                }
            )
            _vcall(adapter, 2, ctypes.c_ulong)  # Release
            i += 1
        _vcall(factory, 2, ctypes.c_ulong)
        return out
    except Exception:  # noqa: BLE001 - no GPU information is a CPU fallback, not an error
        return []


def choose_adapter(found: list[dict]) -> dict | None:
    """The discrete adapter with the most dedicated memory, or None."""
    ok = [a for a in found if not a["software"] and a["dedicated_mb"] >= MIN_DEDICATED_MB]
    return max(ok, key=lambda a: (a["dedicated_mb"], -a["device_id"])) if ok else None


def probe_texts(n: int = 200) -> list[str]:
    """Fixed, varied probe texts (built in code, so nothing extra is bundled)."""
    subjects = ["The lab report", "My tax receipt", "Notes on BCNF", "A photo caption", "The invoice",
                "Meeting minutes", "A recipe for biryani", "Cricket scores", "The thesis draft",
                "Train ticket to Mysuru"]
    middles = ["was written in", "mentions", "summarises", "was downloaded during", "lists"]
    ends = ["March 2024 with three tables.", "the clustering lecture and k-means.",
            "rent, groceries and a hotel booking.", "deadlines for the fifth semester.",
            "a long paragraph about normalization and keys in relational databases."]
    out = []
    for i in range(n):
        out.append(f"{subjects[i % 10]} {middles[(i // 10) % 5]} {ends[(i // 50) % 5]} #{i}")
    return out


def gpu_encoder(model_dir, cpu_encoder, threads: int = 4) -> tuple[object | None, str]:
    """(DirectML encoder or None, human-readable note). Never raises."""
    try:
        import onnxruntime as ort

        if "DmlExecutionProvider" not in ort.get_available_providers():
            return None, "CPU: this build has no DirectML support"
        found = adapters()
        pick = choose_adapter(found)
        if pick is None:
            names = ", ".join(a["name"] for a in found if not a["software"]) or "none"
            return None, f"CPU: no discrete GPU found (adapters: {names})"
        from contextfs.engine.encoder import OnnxEncoder

        enc = OnnxEncoder(model_dir, model_file=cpu_encoder.model_file, threads=threads,
                          dml_device_id=pick["device_id"])
        texts = probe_texts()
        cos = (enc.encode(texts) * cpu_encoder.encode(texts)).sum(axis=1)
        if float(cos.mean()) < PROBE_MEAN or float(cos.min()) < PROBE_MIN:
            return None, (f"CPU: GPU results differ from CPU on {pick['name']} "
                          f"(probe mean cosine {cos.mean():.5f}, min {cos.min():.5f})")
        return enc, f"GPU: {pick['name']} (probe mean cosine {float(np.mean(cos)):.6f})"
    except Exception as exc:  # noqa: BLE001 - any GPU problem means the CPU, with the reason
        return None, f"CPU: GPU unavailable ({type(exc).__name__}: {str(exc)[:160]})"
