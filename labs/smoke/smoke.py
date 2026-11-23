"""Smoke lab helpers: tiny FAISS similarity search and one LLM call.

The notebook calls these functions with the real local embedding model and LLM client;
the tests call them with a fake embedder and the fake LLM client.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

HERE = Path(__file__).resolve().parent
QUERIES = [
    "Which product works at the edge of the network?",
    "How do I avoid schedule problems when daylight saving time changes?",
    "Where should passwords for connectors be kept?",
]


def load_sentences(path: Path = HERE / "sentences.txt") -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_index(vectors: np.ndarray):
    """Exact inner-product FAISS index (vectors are L2-normalised, so this is cosine similarity)."""
    import faiss

    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors.astype("float32"))
    return index


def search(index, sentences: list[str], query_vector: np.ndarray, k: int = 3) -> list[tuple[float, str]]:
    """Top-k (score, sentence); ties broken by sentence position so results are stable."""
    scores, ids = index.search(query_vector.reshape(1, -1).astype("float32"), k)
    hits = [(round(float(s), 6), int(i)) for s, i in zip(scores[0], ids[0]) if i >= 0]
    hits.sort(key=lambda h: (-h[0], h[1]))
    return [(score, sentences[i]) for score, i in hits]


def run_smoke(embed: Callable[[list[str]], np.ndarray], llm, k: int = 3) -> dict:
    """Embed the sentences, build the index, answer the three queries, make one LLM call."""
    sentences = load_sentences()
    index = build_index(embed(sentences))
    results = {q: search(index, sentences, embed([q])[0], k) for q in QUERIES}
    reply = llm.complete([{"role": "user", "content": "In one sentence, what is a context window?"}],
                         max_completion_tokens=1024)
    return {"results": results, "llm_reply": reply.text, "llm_model": reply.model, "usage": reply.usage}
