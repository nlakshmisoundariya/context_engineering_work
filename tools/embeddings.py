"""Embedding utility: the local model in batches, a per-text disk cache, and precomputed-embedding files.

    from tools.embeddings import Embedder
    emb = Embedder()                                  # BAAI/bge-small-en-v1.5 on CPU, cache in indexes/emb_cache/
    vectors = emb.embed([c.text for c in chunks])     # L2-normalised float32, shape (n, 384)

Precomputed embeddings (NOVATECH_DATA_SPEC GEN-10): if embedding the whole chunk store is too slow, load a
file produced earlier for exactly the same chunks and model. The file records the model name, the corpus
checksum and the chunk IDs, and ``load_precomputed`` refuses a file that does not match.

``HashingEmbedder`` is a tiny deterministic stand-in for tests and offline dry runs (no model, poor quality).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import numpy as np

from .local_models import embedding_model_name, load_embedder


def _key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\x00{text}".encode("utf-8")).hexdigest()


class Embedder:
    def __init__(self, model_name: str | None = None, cache_dir: str | Path | None = "indexes/emb_cache",
                 batch_size: int = 64):
        self.model_name = model_name or embedding_model_name()
        self.batch_size = batch_size
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._model = None
        self._cache: dict[str, np.ndarray] = {}
        if self.cache_dir and (self.cache_dir / "cache.npz").exists():
            data = np.load(self.cache_dir / "cache.npz")
            self._cache = {k: data[k] for k in data.files}

    def _encode(self, texts: list[str]) -> np.ndarray:
        if self._model is None:
            self._model = load_embedder(self.model_name)
        return np.asarray(self._model.encode(texts, batch_size=self.batch_size, normalize_embeddings=True,
                                             show_progress_bar=False), dtype="float32")

    def embed(self, texts: list[str]) -> np.ndarray:
        keys = [_key(self.model_name, t) for t in texts]
        todo = sorted({k: t for k, t in zip(keys, texts) if k not in self._cache}.items())
        if todo:
            vecs = self._encode([t for _, t in todo])
            self._cache.update({k: v for (k, _), v in zip(todo, vecs)})
            self.save_cache()
        return np.stack([self._cache[k] for k in keys]) if keys else np.zeros((0, 0), dtype="float32")

    def save_cache(self) -> None:
        """Write the cache atomically (temp file + rename): two of your processes may embed at the same time."""
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_dir / f"cache.{os.getpid()}.tmp"
            with tmp.open("wb") as fh:
                np.savez(fh, **self._cache)
            os.replace(tmp, self.cache_dir / "cache.npz")


class HashingEmbedder:
    """Deterministic bag-of-words hashing vectors. For tests and offline dry runs only."""

    model_name = "hashing-256"

    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype="float32")
        for i, text in enumerate(texts):
            for word in re.findall(r"\w+", text.lower()):
                out[i, int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


class PrecomputedMismatch(ValueError):
    pass


def save_precomputed(path: str | Path, chunk_ids: list[str], vectors: np.ndarray, model_name: str,
                     corpus_checksum: str | None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"model": model_name, "corpus_checksum": corpus_checksum, "chunk_ids": list(chunk_ids)}
    np.savez(path, vectors=np.asarray(vectors, dtype="float32"), meta=np.array(json.dumps(meta)))
    return path


def load_precomputed(path: str | Path, chunk_ids: list[str], model_name: str, corpus_checksum: str | None) -> np.ndarray:
    """Vectors for exactly these chunk IDs, or PrecomputedMismatch explaining why the file cannot be used."""
    data = np.load(path)
    meta = json.loads(str(data["meta"]))
    if meta["model"] != model_name:
        raise PrecomputedMismatch(f"file was embedded with {meta['model']}, you are using {model_name}")
    if meta["corpus_checksum"] != corpus_checksum:
        raise PrecomputedMismatch("file was built for a different corpus (checksum differs); re-embed")
    index = {cid: i for i, cid in enumerate(meta["chunk_ids"])}
    missing = [cid for cid in chunk_ids if cid not in index]
    if missing:
        raise PrecomputedMismatch(f"{len(missing)} chunks are not in the file (e.g. {missing[:3]}): "
                                  "your chunking differs from the one it was built for")
    return data["vectors"][[index[cid] for cid in chunk_ids]]


def corpus_checksum(data_root: str | Path = "data/novatech") -> str | None:
    manifest = Path(data_root) / "MANIFEST.json"
    return json.loads(manifest.read_text(encoding="utf-8")).get("corpus_checksum") if manifest.exists() else None
