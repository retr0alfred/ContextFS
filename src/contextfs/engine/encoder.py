"""ONNX Runtime sentence encoder: mean pooling + L2 norm, CPU EP only (ADR-0003).

The execution provider is named explicitly. The default ORT wheel also registers an
``AzureExecutionProvider`` (a cloud backend), which a local-first app must never reach.
Thread counts are pinned so results do not depend on the machine's defaults (ADR-0010 §2).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

__all__ = ["OnnxEncoder"]


class OnnxEncoder:
    """Encode text with an exported transformer, CPU only, deterministic thread settings."""

    def __init__(
        self,
        model_dir: Path | str,
        model_file: str = "model_fp32.onnx",
        threads: int = 4,
        max_tokens: int = 256,
    ) -> None:
        """Load the ONNX graph and tokenizer from ``model_dir``."""
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.model_dir = Path(model_dir)
        self.model_file = model_file
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(
            str(self.model_dir / model_file), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=max_tokens)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.dimension = 384
        self.fingerprint = f"{self.model_dir.name}/{model_file}/cpu/t{threads}"

    def encode(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        """Return L2-normalised float32 embeddings, one row per text."""
        out = []
        for i in range(0, len(texts), batch_size):
            batch = self.tokenizer.encode_batch([t or " " for t in texts[i : i + batch_size]])
            ids = np.asarray([e.ids for e in batch], dtype=np.int64)
            mask = np.asarray([e.attention_mask for e in batch], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feeds["token_type_ids"] = np.zeros_like(ids)
            hidden = self.session.run(None, feeds)[0]
            m = mask[..., None].astype(np.float32)
            pooled = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            out.append((pooled / np.clip(norms, 1e-12, None)).astype(np.float32))
        return np.vstack(out) if out else np.zeros((0, self.dimension), dtype=np.float32)

    def encode_one(self, text: str) -> np.ndarray:
        """Embed a single query."""
        return self.encode([text])[0]
