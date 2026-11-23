"""Chunking scaffolding for Module 2. You implement the three strategies; the kit gives you the plumbing.

Provided:
- ``sections(text)``           heading tree -> sections with section paths and character offsets
- ``paragraphs(text)`` / ``sentences(text)``   splitting helpers that keep character offsets
- ``tokens(text)``             the kit token counter (same one the Context Spec budgets use)
- ``make_chunks(doc, pieces, strategy)``      turns your pieces into Chunk records: stable IDs
                                              ``DOC-x-###::cNN``, document metadata + section path propagated
- ``side_by_side(doc, {name: chunks})``       printable comparison of strategies for one document
- ``chunk_stats(chunks)``      counts and token distribution

Extension points (``# M2 lab``): ``FixedChunker``, ``SemanticChunker``, ``HierarchicalChunker``.
A chunker takes a ``Document`` and returns ``list[Chunk]``; build each piece as (start, end, section_path)
character offsets into ``doc.text`` and let ``make_chunks`` do the rest.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from novaassist.tokens import count_tokens

from .documents import Document

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def tokens(text: str) -> int:
    return count_tokens(text)


@dataclass
class Section:
    path: list[str]  # heading titles from the top level down to this section
    level: int  # heading level (0 = text before the first heading)
    start: int  # character offsets into the document text: text[start:end]
    end: int

    def text(self, doc_text: str) -> str:
        return doc_text[self.start:self.end]


@dataclass
class Chunk:
    chunk_id: str  # DOC-x-###::cNN
    doc_id: str
    text: str
    section_path: list[str]
    char_start: int  # offsets into Document.text, so any chunking can be compared span-wise
    char_end: int
    token_count: int
    strategy: str
    metadata: dict = field(default_factory=dict)  # document metadata exactly as stated + section_path + chunk_index


def sections(text: str) -> list[Section]:
    """Split Markdown into sections at headings (headings inside ``` code fences are ignored)."""
    out: list[Section] = []
    path: list[tuple[int, str]] = []
    in_fence = False
    offset = 0
    current_start, current_level, current_path = 0, 0, []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else HEADING.match(line.rstrip("\n"))
        if m:
            if offset > current_start and text[current_start:offset].strip():
                out.append(Section(current_path, current_level, current_start, offset))
            level, title = len(m.group(1)), m.group(2)
            path = [p for p in path if p[0] < level] + [(level, title)]
            current_start, current_level, current_path = offset, level, [t for _, t in path]
        offset += len(line)
    if text[current_start:].strip():
        out.append(Section(current_path, current_level, current_start, len(text)))
    return out


def _spans(text: str, pattern: str, base: int = 0) -> list[tuple[int, int]]:
    spans, pos = [], 0
    for m in re.finditer(pattern, text):
        if text[pos:m.start()].strip():
            spans.append((base + pos, base + m.start()))
        pos = m.end()
    if text[pos:].strip():
        spans.append((base + pos, base + len(text)))
    return spans


def paragraphs(text: str, base: int = 0) -> list[tuple[int, int]]:
    """Character spans of blank-line-separated paragraphs (offsets shifted by ``base``)."""
    return _spans(text, r"\n\s*\n", base)


def sentences(text: str, base: int = 0) -> list[tuple[int, int]]:
    """Character spans of sentences (simple rule: ., ! or ? followed by whitespace, or a line break)."""
    return _spans(text, r"(?<=[.!?])\s+|\n+", base)


def chunk_id(doc_id: str, index: int, total: int) -> str:
    width = 3 if total > 99 else 2
    return f"{doc_id}::c{index:0{width}d}"


def make_chunks(doc: Document, pieces: list[tuple[int, int, list[str]]], strategy: str) -> list[Chunk]:
    """Turn (start, end, section_path) pieces of ``doc.text`` into Chunk records with propagated metadata."""
    pieces = [(s, e, p) for s, e, p in pieces if doc.text[s:e].strip()]
    chunks = []
    for i, (start, end, path) in enumerate(pieces, start=1):
        text = doc.text[start:end].strip()
        meta = dict(doc.metadata)
        meta.update({"section_path": list(path), "chunk_index": i})
        chunks.append(Chunk(chunk_id(doc.doc_id, i, len(pieces)), doc.doc_id, text, list(path), start, end,
                            tokens(text), strategy, meta))
    return chunks


def section_path_at(doc: Document, offset: int) -> list[str]:
    """Section path that contains a character offset (useful for fixed-size windows)."""
    path: list[str] = []
    for sec in sections(doc.text):
        if sec.start <= offset:
            path = sec.path
    return path


def chunk_stats(chunks: list[Chunk]) -> dict:
    sizes = [c.token_count for c in chunks]
    if not sizes:
        return {"chunks": 0}
    return {"chunks": len(sizes), "tokens_total": sum(sizes), "tokens_min": min(sizes), "tokens_median": statistics.median(sizes),
            "tokens_max": max(sizes)}


def side_by_side(doc: Document, results: dict[str, list[Chunk]], width: int = 70) -> str:
    """Printable comparison: one block per strategy, one line per chunk (ID, tokens, section path, start of text)."""
    lines = [f"{doc.doc_id}: {doc.metadata.get('title', '')}"]
    for name, chunks in results.items():
        lines.append(f"\n== {name}: {chunk_stats(chunks)}")
        for c in chunks:
            preview = " ".join(c.text.split())[:width]
            lines.append(f"  {c.chunk_id:<20} {c.token_count:>5} tok  {' > '.join(c.section_path)[:40]:<40}  {preview}")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ extension points

class FixedChunker:
    """M2 lab: fixed-size chunks of about ``size`` tokens with ``overlap`` tokens of overlap."""

    name = "fixed"

    def __init__(self, size: int = 200, overlap: int = 40):
        self.size, self.overlap = size, overlap

    def chunk(self, doc: Document) -> list[Chunk]:
        # M2 lab: build (start, end, section_path) pieces over doc.text, then:
        # return make_chunks(doc, pieces, self.name)
        raise NotImplementedError("M2 lab: implement FixedChunker.chunk")


class SemanticChunker:
    """M2 lab: group consecutive sentences while they stay on one topic (e.g. embedding similarity)."""

    name = "semantic"

    def __init__(self, embed=None, threshold: float = 0.6, max_tokens: int = 300):
        self.embed, self.threshold, self.max_tokens = embed, threshold, max_tokens

    def chunk(self, doc: Document) -> list[Chunk]:
        raise NotImplementedError("M2 lab: implement SemanticChunker.chunk")


class HierarchicalChunker:
    """M2 lab: follow the heading tree; split oversized sections; carry the section path."""

    name = "hierarchical"

    def __init__(self, max_tokens: int = 300):
        self.max_tokens = max_tokens

    def chunk(self, doc: Document) -> list[Chunk]:
        raise NotImplementedError("M2 lab: implement HierarchicalChunker.chunk")
