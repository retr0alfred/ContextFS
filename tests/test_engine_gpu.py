"""GPU for bulk indexing: adapter choice, the parity probe, and the CPU fallback (ADR-0003).

These tests use fake adapters and encoders, so they never touch a GPU. The one real-GPU check is
opt-in (``CONTEXTFS_TEST_GPU=1``), because a GPU workload needs the user's say-so (CLAUDE.md).
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from contextfs.engine import Engine, default_model_dir, gpu

HYBRID = [
    {"device_id": 0, "name": "AMD Radeon 780M Graphics", "vendor": "0x1002", "dedicated_mb": 512, "software": False},
    {"device_id": 1, "name": "NVIDIA GeForce RTX 5060 Laptop GPU", "vendor": "0x10de", "dedicated_mb": 7899,
     "software": False},
    {"device_id": 2, "name": "Microsoft Basic Render Driver", "vendor": "0x1414", "dedicated_mb": 0, "software": True},
]


def test_hybrid_laptop_picks_the_discrete_gpu_not_adapter_zero():
    assert gpu.choose_adapter(HYBRID)["device_id"] == 1


def test_integrated_or_software_only_means_cpu():
    assert gpu.choose_adapter([HYBRID[0], HYBRID[2]]) is None
    assert gpu.choose_adapter([]) is None


class _FakeEncoder:
    model_file = "model_fp32.onnx"

    def __init__(self, *args, noise: float = 0.0, fail: bool = False, **kwargs):
        if fail:
            raise RuntimeError("DirectML device lost")
        self.noise = noise
        self.fingerprint = "fake/gpu"

    def encode(self, texts):
        rng = np.random.default_rng(len(texts))
        base = rng.standard_normal((len(texts), 8)).astype(np.float32)
        if self.noise:
            base += self.noise * np.random.default_rng(99).standard_normal(base.shape).astype(np.float32)
        return base / np.linalg.norm(base, axis=1, keepdims=True)


@pytest.fixture()
def discrete(monkeypatch):
    import onnxruntime

    monkeypatch.setattr(gpu, "adapters", lambda: HYBRID)
    monkeypatch.setattr(onnxruntime, "get_available_providers", lambda: ["DmlExecutionProvider", "CPUExecutionProvider"])


def _use_gpu_encoder(monkeypatch, **kwargs):
    import contextfs.engine.encoder as encoder

    monkeypatch.setattr(encoder, "OnnxEncoder", lambda *a, **k: _FakeEncoder(**kwargs))


def test_probe_pass_uses_the_gpu(discrete, monkeypatch):
    _use_gpu_encoder(monkeypatch)
    enc, note = gpu.gpu_encoder("unused", _FakeEncoder())
    assert enc is not None and note.startswith("GPU: NVIDIA GeForce RTX 5060")


def test_probe_mismatch_falls_back_to_cpu_with_the_reason(discrete, monkeypatch):
    _use_gpu_encoder(monkeypatch, noise=0.5)
    enc, note = gpu.gpu_encoder("unused", _FakeEncoder())
    assert enc is None and "differ from CPU" in note


def test_gpu_error_falls_back_to_cpu_and_never_raises(discrete, monkeypatch):
    _use_gpu_encoder(monkeypatch, fail=True)
    enc, note = gpu.gpu_encoder("unused", _FakeEncoder())
    assert enc is None and "DirectML device lost" in note


def test_no_discrete_gpu_falls_back_to_cpu(monkeypatch):
    monkeypatch.setattr(gpu, "adapters", lambda: [HYBRID[0]])
    enc, note = gpu.gpu_encoder("unused", _FakeEncoder())
    assert enc is None and note.startswith("CPU")


def test_probe_texts_are_fixed_and_distinct():
    texts = gpu.probe_texts()
    assert len(texts) == len(set(texts)) == 200 and texts == gpu.probe_texts()


@pytest.mark.skipif(os.environ.get("CONTEXTFS_TEST_GPU") != "1", reason="real GPU work is opt-in")
@pytest.mark.skipif(not (default_model_dir() / "model_fp32.onnx").exists(), reason="ONNX model not exported")
def test_real_gpu_index_matches_cpu_index(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for i, t in enumerate(gpu.probe_texts(40)):
        (corpus / f"note_{i:02d}.txt").write_text(t, encoding="utf-8")
    with Engine.open(tmp_path / "cpu", mode="write") as a, Engine.open(tmp_path / "gpu", mode="write", device="auto") as b:
        a.update([corpus])
        b.update([corpus])
        assert b.device_note.startswith("GPU"), b.device_note
    with Engine.open(tmp_path / "cpu") as a, Engine.open(tmp_path / "gpu") as b:
        for q in ("lab report", "biryani recipe", "deadlines for the fifth semester"):
            assert a.search(q).paths == b.search(q).paths
