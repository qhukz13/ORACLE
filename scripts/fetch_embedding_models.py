#!/usr/bin/env python
"""Fetch the ONNX embedding candidates OQ-02 compares.

    python scripts/fetch_embedding_models.py                 # all candidates
    python scripts/fetch_embedding_models.py e5-base

Models land in `D:/ORACLE/models/embeddings/<name>/onnx/`, beside the Ollama models on
the same drive — they are ~4 GB in total and do not belong on C:, which is at 82%.

Only the ONNX export and its tokenizer are fetched, never the PyTorch weights: the
runtime is ONNX Runtime on CPU (ADR-0014, TECH_STACK §4) and a `.safetensors` file we
will never load is a gigabyte of nothing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEST = Path("D:/ORACLE/models/embeddings")

TOKENIZER_FILES = [
    "onnx/tokenizer.json",
    "onnx/config.json",
    "onnx/tokenizer_config.json",
    "onnx/special_tokens_map.json",
]

JOBS: dict[str, tuple[str, list[str]]] = {
    "e5-small": ("intfloat/multilingual-e5-small", ["onnx/model.onnx", *TOKENIZER_FILES]),
    "e5-base": (
        "intfloat/multilingual-e5-base",
        # The int8 export is `_avx512_vnni`, and this machine is Haswell — no AVX-512,
        # no VNNI. Fetched anyway because "the quantised build is slower on a CPU that
        # cannot run its kernels" is a result worth having measured rather than assumed.
        ["onnx/model.onnx", "onnx/model_qint8_avx512_vnni.onnx", *TOKENIZER_FILES],
    ),
    "bge-m3": (
        "BAAI/bge-m3",
        # bge-m3's graph exceeds the 2 GB protobuf limit, so its weights live in a
        # sidecar `.onnx_data` that must sit next to the graph or the session will not
        # load. Fetching one without the other produces a confusing runtime error.
        ["onnx/model.onnx", "onnx/model.onnx_data", *TOKENIZER_FILES],
    ),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", default=[], help="candidates to fetch (default: all)")
    ap.add_argument("--dest", default=str(DEST))
    args = ap.parse_args()

    from huggingface_hub import hf_hub_download

    for name in args.names or list(JOBS):
        if name not in JOBS:
            print(f"unknown candidate: {name} (have {', '.join(JOBS)})")
            return 2
        repo, files = JOBS[name]
        for f in files:
            path = hf_hub_download(repo, f, local_dir=f"{args.dest}/{name}")
            print(f"{name}: {f} -> {path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
