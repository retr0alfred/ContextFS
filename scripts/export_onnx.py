"""Export the embedding model to ONNX (fp32 + int8) and check parity (ADR-0003 §2).

Build-time only: uses torch + transformers from the dev venv on CPU. The app ships the ONNX files
and the tokenizer, never torch.

Parity gates (500 texts from the smoke fixture plus synthetic sentences):
- fp32 ONNX vs sentence-transformers: mean cosine ≥ 0.9999 (hard gate)
- int8 ONNX vs sentence-transformers: mean cosine and top-10 retrieval agreement, reported

Usage:
    python scripts/export_onnx.py [--model sentence-transformers/all-MiniLM-L6-v2]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
os.environ.setdefault("HF_HOME", str(PROJECT_ROOT / "models" / "hf"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np  # noqa: E402


def sample_texts(n: int = 500) -> list[str]:
    from contextfs.datagen.corpus_spec import CORPUS_FILES

    texts: list[str] = []
    for spec in CORPUS_FILES:
        content = spec.content
        if isinstance(content, str):
            texts += [p.strip() for p in content.split("\n") if len(p.strip()) > 20]
    base = [
        "the pdf I studied before my machine learning exam",
        "budget spreadsheet march",
        "notes about database normalization and BCNF",
        "photos from the goa trip",
    ]
    texts = base + texts
    return (texts * (n // max(1, len(texts)) + 1))[:n]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="sentence-transformers/all-MiniLM-L6-v2")
    parser.add_argument(
        "--out", type=Path, default=PROJECT_ROOT / "models" / "onnx" / "minilm-l6-v2"
    )
    args = parser.parse_args()

    import torch
    from huggingface_hub import snapshot_download
    from transformers import AutoModel, AutoTokenizer

    src = Path(snapshot_download(args.model))
    tok = AutoTokenizer.from_pretrained(src)
    model = AutoModel.from_pretrained(src).eval()
    args.out.mkdir(parents=True, exist_ok=True)

    class Wrapped(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask, token_type_ids):
            return self.m(
                input_ids=input_ids, attention_mask=attention_mask, token_type_ids=token_type_ids
            ).last_hidden_state

    dummy = tok(["hello world"], return_tensors="pt")
    fp32 = args.out / "model_fp32.onnx"
    torch.onnx.export(
        Wrapped(model),
        (dummy["input_ids"], dummy["attention_mask"], dummy["token_type_ids"]),
        str(fp32),
        input_names=["input_ids", "attention_mask", "token_type_ids"],
        output_names=["last_hidden_state"],
        opset_version=17,
        dynamo=False,
        dynamic_axes={
            k: {0: "batch", 1: "seq"}
            for k in ("input_ids", "attention_mask", "token_type_ids", "last_hidden_state")
        },
    )
    from onnxruntime.quantization import QuantType, quantize_dynamic

    int8 = args.out / "model_int8.onnx"
    quantize_dynamic(str(fp32), str(int8), weight_type=QuantType.QInt8)
    tok.save_pretrained(args.out)  # tokenizer.json etc.
    for name in ("README.md", "LICENSE"):
        if (src / name).exists():
            shutil.copy2(src / name, args.out / name)

    # -- parity -------------------------------------------------------------
    from sentence_transformers import SentenceTransformer

    from contextfs.engine.encoder import OnnxEncoder

    texts = sample_texts()
    ref = SentenceTransformer(str(src), device="cpu").encode(
        texts, normalize_embeddings=True, batch_size=32
    )
    report = {"model": args.model, "texts": len(texts)}
    for name, path in (("fp32", fp32), ("int8", int8)):
        enc = OnnxEncoder(args.out, model_file=path.name, threads=4)
        got = enc.encode(texts)
        cos = (got * ref).sum(axis=1)
        q = ref[:50]
        top_ref = np.argsort(-(q @ ref.T), axis=1)[:, :10]
        top_got = np.argsort(-(got[:50] @ got.T), axis=1)[:, :10]
        agree = float(
            np.mean([len(set(a) & set(b)) / 10 for a, b in zip(top_ref, top_got, strict=True)])
        )
        report[name] = {
            "mean_cosine": round(float(cos.mean()), 6),
            "min_cosine": round(float(cos.min()), 6),
            "top10_agreement": round(agree, 4),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
    (args.out / "export_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    ok = report["fp32"]["mean_cosine"] >= 0.9999
    print("fp32 parity gate (mean cosine >= 0.9999):", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
