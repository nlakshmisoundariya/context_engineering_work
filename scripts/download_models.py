"""Verify (and, at server build time only, download) the two local models.

Default: verify only. Load each model from the shared cache and run one embedding and one pair score.
``--download`` fetches the models into the cache first. It refuses while HF_HUB_OFFLINE=1, so it only
runs deliberately at server build time (Thread C). Participants never download.

Usage:
    python scripts/download_models.py                               # verify
    HF_HUB_OFFLINE=0 python scripts/download_models.py --download   # server build only
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.local_models import (cache_folder, embedding_model_name, load_embedder,  # noqa: E402
                                load_reranker, reranker_model_name)


def verify() -> int:
    """Return the number of models that failed to load or run."""
    failures = 0
    try:
        start = time.perf_counter()
        vec = load_embedder().encode(["NovaSync 2.3 sync failure after upgrade"], normalize_embeddings=True)
        print(f"PASS embedding  {embedding_model_name()}  dim={vec.shape[1]}  {time.perf_counter() - start:.1f}s")
    except Exception as exc:
        failures += 1
        print(f"FAIL embedding  {embedding_model_name()}: {exc}")
    try:
        start = time.perf_counter()
        score = load_reranker().predict([("refund policy", "Refund requests are accepted within 14 days.")])
        print(f"PASS reranker   {reranker_model_name()}  score={float(score[0]):.3f}  {time.perf_counter() - start:.1f}s")
    except Exception as exc:
        failures += 1
        print(f"FAIL reranker   {reranker_model_name()}: {exc}")
    return failures


def download() -> None:
    if os.environ.get("HF_HUB_OFFLINE", "") == "1":
        sys.exit("HF_HUB_OFFLINE=1: refusing to download. Downloads happen only at server build time.")
    from huggingface_hub import snapshot_download

    for name in (embedding_model_name(), reranker_model_name()):
        print(f"downloaded {name} -> {snapshot_download(name, cache_dir=cache_folder())}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the local embedding and reranker models.")
    parser.add_argument("--download", action="store_true", help="download into the cache first (server build only)")
    args = parser.parse_args(argv)
    print(f"cache: {cache_folder() or 'Hugging Face default under HF_HOME=' + str(os.environ.get('HF_HOME'))}")
    if args.download:
        download()
    return 1 if verify() else 0


if __name__ == "__main__":
    raise SystemExit(main())
