"""Load the two local models (embedding, cross-encoder reranker) from the shared cache.

Models are pre-downloaded on the servers. The cache is ``MODEL_CACHE_DIR`` if set, otherwise the
standard Hugging Face cache under ``HF_HOME``. With ``HF_HUB_OFFLINE=1`` nothing is downloaded.
"""
from __future__ import annotations

import os

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


def embedding_model_name() -> str:
    return os.environ.get("EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL


def reranker_model_name() -> str:
    return os.environ.get("RERANKER_MODEL") or DEFAULT_RERANKER_MODEL


def cache_folder() -> str | None:
    """Explicit cache folder, or None to use the Hugging Face default under HF_HOME."""
    return os.environ.get("MODEL_CACHE_DIR") or None


def load_embedder(name: str | None = None):
    """Return a sentence-transformers model on CPU."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(name or embedding_model_name(), device="cpu", cache_folder=cache_folder())


def load_reranker(name: str | None = None):
    """Return a sentence-transformers CrossEncoder on CPU."""
    from sentence_transformers import CrossEncoder

    return CrossEncoder(name or reranker_model_name(), device="cpu", cache_folder=cache_folder())
