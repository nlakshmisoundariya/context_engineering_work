"""The chunk store: one JSONL file of Chunk records, plus a Context Source view over it.

    from tools.chunk_store import save_chunks, load_chunks
    save_chunks(chunks, "indexes/chunks.jsonl")
    chunks = load_chunks("indexes/chunks.jsonl")

Format (one JSON object per line): chunk_id, doc_id, text, section_path, char_start, char_end, token_count,
strategy, metadata. Documented in docs/interfaces/chunk_store.md.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from novaassist.context_items import ContextItem

from .chunking import Chunk


def save_chunks(chunks: list[Chunk], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for c in chunks:
            fh.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    return path


def load_chunks(path: str | Path) -> list[Chunk]:
    return [Chunk(**json.loads(line)) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _timestamp(meta: dict) -> str:
    """The item timestamp: effective_date, else last_updated, else 1970-01-01. Values that are not ISO dates
    (for example "unknown" from a metadata override) are skipped; the metadata keeps them for your freshness rule."""
    for key in ("effective_date", "last_updated"):
        try:
            return date.fromisoformat(str(meta.get(key))).isoformat()
        except ValueError:
            continue
    return "1970-01-01"


def to_context_item(chunk: Chunk, relevance: float | None = None, source: str = "chunk_store") -> ContextItem:
    """A chunk as a knowledge-channel context item, with document provenance for citations and freshness."""
    meta = chunk.metadata
    return ContextItem(
        item_id=chunk.chunk_id, channel="knowledge", content=chunk.text, source=source,
        provenance={"type": "document", "ref": chunk.doc_id, "chunk_id": chunk.chunk_id,
                    "section_path": chunk.section_path, "version": meta.get("version"), "status": meta.get("status")},
        timestamp=_timestamp(meta),
        relevance=relevance, token_count=chunk.token_count, metadata=dict(meta))
