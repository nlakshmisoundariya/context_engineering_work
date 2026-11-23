"""Baseline retrievers for Module 3: an exact FAISS dense index and a BM25 sparse index, measured separately.

    from tools.retrieval import DenseIndex, BM25Index
    dense = DenseIndex.build(chunks, embedder)
    sparse = BM25Index.build(chunks)
    hits = dense.search("refund window for NovaEdge", k=8)      # list[Hit], best first

Rankings are deterministic: scores are rounded to 6 decimals and ties are broken by chunk ID
(EVALUATION_SPEC §19). Hybrid combination is deliberately NOT here: it is Module 4 work.
``RetrieverSource`` wraps any retriever as a knowledge-channel Context Source.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from .chunk_store import to_context_item
from .chunking import Chunk

TOKEN = re.compile(r"[a-z0-9][a-z0-9_.\-]*")


@dataclass
class Hit:
    chunk_id: str
    score: float
    rank: int
    retriever: str
    evidence: dict | None = None  # optional: how a fused hit was formed, e.g. each retriever's rank and score


def rank_hits(scored: list[tuple[str, float]], k: int, retriever: str) -> list[Hit]:
    """Sort by score (desc, 6 decimals) then chunk ID (asc); keep k; number ranks from 1."""
    ordered = sorted(((cid, round(float(s), 6)) for cid, s in scored), key=lambda x: (-x[1], x[0]))[:k]
    return [Hit(cid, s, i, retriever) for i, (cid, s) in enumerate(ordered, start=1)]


def bm25_tokens(text: str) -> list[str]:
    """Lowercase tokens that keep error codes and config keys whole (SYNC-E431, flow.webhooks.max_retries)."""
    return [t.strip(".-") for t in TOKEN.findall(text.lower()) if t.strip(".-")]


class DenseIndex:
    name = "faiss"

    def __init__(self, index, chunk_ids: list[str], embedder):
        self.index, self.chunk_ids, self.embedder = index, chunk_ids, embedder

    @classmethod
    def build(cls, chunks: list[Chunk], embedder, vectors: np.ndarray | None = None) -> "DenseIndex":
        """Exact inner-product index over L2-normalised vectors (= cosine). Pass precomputed vectors if you have them."""
        import faiss

        vecs = np.asarray(vectors if vectors is not None else embedder.embed([c.text for c in chunks]), dtype="float32")
        index = faiss.IndexFlatIP(vecs.shape[1])
        index.add(vecs)
        return cls(index, [c.chunk_id for c in chunks], embedder)

    def search(self, query: str, k: int = 8, query_vector: np.ndarray | None = None) -> list[Hit]:
        qv = np.asarray(query_vector if query_vector is not None else self.embedder.embed([query])[0], dtype="float32")
        depth = min(len(self.chunk_ids), k + 20)  # over-fetch so tie-breaking at the cut is stable
        scores, ids = self.index.search(qv.reshape(1, -1), depth)
        return rank_hits([(self.chunk_ids[i], s) for s, i in zip(scores[0], ids[0]) if i >= 0], k, self.name)


class BM25Index:
    name = "bm25"

    def __init__(self, bm25, chunk_ids: list[str]):
        self.bm25, self.chunk_ids = bm25, chunk_ids

    @classmethod
    def build(cls, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75) -> "BM25Index":
        from rank_bm25 import BM25Okapi

        return cls(BM25Okapi([bm25_tokens(c.text) for c in chunks], k1=k1, b=b), [c.chunk_id for c in chunks])

    def search(self, query: str, k: int = 8) -> list[Hit]:
        scores = self.bm25.get_scores(bm25_tokens(query))
        return rank_hits(list(zip(self.chunk_ids, scores)), k, self.name)


class RetrieverSource:
    """A retriever as a knowledge Context Source: top_k from the Context Spec, relevance = retrieval score."""

    channel = "knowledge"

    def __init__(self, retriever, chunks: list[Chunk], name: str | None = None):
        self.retriever = retriever
        self.by_id = {c.chunk_id: c for c in chunks}
        self.name = name or f"{retriever.name}_retriever"

    def gather(self, request, session, spec) -> list:
        k = (spec.selection("knowledge").get("top_k") if spec else None) or 8
        items = []
        for hit in self.retriever.search(request.text, k=k):
            item = to_context_item(self.by_id[hit.chunk_id], relevance=hit.score, source=self.name)
            item.metadata["retrieval"] = {"retriever": hit.retriever, "rank": hit.rank, "score": hit.score,
                                          "query": request.text}
            items.append(item)
        return items
