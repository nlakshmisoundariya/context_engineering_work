"""M3 vector-database comparison step: the same chunk embeddings in Qdrant local mode (in-process, on disk).

    from tools.vectordb_compare import load_into_qdrant, search
    client = load_into_qdrant(chunks, vectors)                 # persisted under indexes/qdrant/
    hits = search(client, embedder.embed([q])[0], k=8)

Qdrant is used only for this comparison; FAISS stays the NovaAssist index. Each point's payload carries the
chunk's document metadata exactly as stated (absent fields stay absent), so filters behave like the corpus.

Your part (M3 lab): ``filtered_search`` (filter at query time) and the comparison with FAISS + post-filtering.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .chunking import Chunk
from .retrieval import Hit, rank_hits

COLLECTION = "novatech_chunks"
PAYLOAD_FIELDS = ("doc_id", "doc_type", "product", "product_version", "version", "status", "effective_date",
                  "audience", "entitlement_tier")


def load_into_qdrant(chunks: list[Chunk], vectors: np.ndarray, path: str | Path = "indexes/qdrant",
                     collection: str = COLLECTION):
    """(Re)create the collection and upsert every chunk with its vector and metadata payload."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    client = QdrantClient(path=str(path))
    if client.collection_exists(collection):
        client.delete_collection(collection)
    client.create_collection(collection, vectors_config=VectorParams(size=int(vectors.shape[1]), distance=Distance.COSINE))
    points = []
    for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
        payload = {k: chunk.metadata[k] for k in PAYLOAD_FIELDS if k in chunk.metadata}
        payload.update({"chunk_id": chunk.chunk_id, "doc_id": chunk.doc_id, "section_path": chunk.section_path})
        points.append(PointStruct(id=i, vector=[float(x) for x in vec], payload=payload))
    for start in range(0, len(points), 256):
        client.upsert(collection, points=points[start:start + 256])
    return client


def search(client, query_vector: np.ndarray, k: int = 8, collection: str = COLLECTION, query_filter=None) -> list[Hit]:
    """Top-k by cosine, with the same deterministic tie-breaking as the FAISS helper."""
    response = client.query_points(collection, query=[float(x) for x in query_vector], limit=k + 20,
                                   query_filter=query_filter, with_payload=True)
    return rank_hits([(p.payload["chunk_id"], p.score) for p in response.points], k, "qdrant")


def post_filter(hits: list[Hit], chunks_by_id: dict[str, Chunk], **conditions) -> list[Hit]:
    """What FAISS needs instead of a query-time filter: drop hits whose metadata does not match, after search."""
    kept = [h for h in hits if all(chunks_by_id[h.chunk_id].metadata.get(k) == v for k, v in conditions.items())]
    return [Hit(h.chunk_id, h.score, i, h.retriever) for i, h in enumerate(kept, start=1)]


def filtered_search(client, query_vector: np.ndarray, k: int = 8, collection: str = COLLECTION, **conditions) -> list[Hit]:
    """M3 lab: search with a Qdrant payload filter (e.g. product="NovaSync", product_version="2.3")."""
    raise NotImplementedError("M3 lab: build a qdrant_client.models.Filter from the conditions and call search()")
