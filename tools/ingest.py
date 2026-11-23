"""Ingest the corpus into a chunk store with *your* chunkers (one per document type).

    python -m tools.ingest --corpus data/novatech/corpus --out indexes/chunks.jsonl \\
        --chunker policy=labs/m2/my_chunkers.py:HierarchicalChunker \\
        --chunker default=labs/m2/my_chunkers.py:FixedChunker \\
        --ocr-cache indexes/ocr --overrides labs/m2/metadata_overrides.yaml

``--chunker TYPE=module:Class`` picks the chunker for a doc_type (policy, manual, troubleshooting, kb, faq,
release_notes, advisory, distractor, scanned); ``default`` covers the rest. The module part is a Python file
path (``labs/m2/my_chunkers.py``) or an importable module name. Add ``--no-ocr`` to skip the scanned document
while you iterate; ``--ocr-cache`` reuses OCR text cached by ``labs/m2/ocr_start.sh`` instead of running
Tesseract again.

``--overrides`` applies your metadata decisions at ingestion; the corpus files are never changed. Format:

    overrides:
      - doc_id: DOC-XX-000
        set: {field: value}              # the value to use (the string "unknown" marks an unknown value)
        metadata_source: corrected       # corrected | inferred | unknown
        reason: "why, and the evidence"  # required
    quarantine:
      - doc_id: DOC-XX-000
        reason: "why this document stays out of the store"

Every chunk of an overridden document records ``metadata_source`` and ``correction_reason`` per field and the
stated value under ``stated_metadata``. Quarantined documents are left out and listed in
``<out>.quarantine.jsonl`` next to the store.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
import time
from pathlib import Path

import yaml

from .chunk_store import save_chunks
from .chunking import Chunk, chunk_stats
from .documents import Document, load_corpus

METADATA_SOURCES = {"corrected", "inferred", "unknown"}
_modules: dict[str, object] = {}


def _module(name: str):
    """Import a module by name, or load it from a .py file path (once per path)."""
    if name not in _modules:
        if name.endswith(".py"):
            path = Path(name).resolve()
            if not path.is_file():
                raise FileNotFoundError(f"chunker file not found: {name}")
            sys.path.insert(0, str(path.parent))  # so the file can import its neighbours
            spec = importlib.util.spec_from_file_location(path.stem, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _modules[name] = module
        else:
            sys.path.insert(0, str(Path.cwd()))
            _modules[name] = importlib.import_module(name)
    return _modules[name]


def load_chunker(spec: str):
    module_name, _, cls_name = spec.rpartition(":")  # the last colon: a Windows path has one too
    return getattr(_module(module_name), cls_name)()


def load_overrides(path: str | Path, doc_ids: set[str]) -> dict:
    """Read and validate an overrides file. Reports every problem at once."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    problems = []
    for i, o in enumerate(data.get("overrides") or []):
        where = f"overrides[{i}] ({o.get('doc_id')})"
        if o.get("doc_id") not in doc_ids:
            problems.append(f"{where}: unknown doc_id")
        if not isinstance(o.get("set"), dict) or not o["set"]:
            problems.append(f"{where}: 'set' must map at least one field to a value")
        if o.get("metadata_source") not in METADATA_SOURCES:
            problems.append(f"{where}: metadata_source must be one of {sorted(METADATA_SOURCES)}")
        if not str(o.get("reason") or "").strip():
            problems.append(f"{where}: a reason is required")
    for i, q in enumerate(data.get("quarantine") or []):
        if q.get("doc_id") not in doc_ids:
            problems.append(f"quarantine[{i}] ({q.get('doc_id')}): unknown doc_id")
        if not str(q.get("reason") or "").strip():
            problems.append(f"quarantine[{i}] ({q.get('doc_id')}): a reason is required")
    if problems:
        raise ValueError(f"{Path(path).name} has {len(problems)} problem(s):\n  - " + "\n  - ".join(problems))
    return {"overrides": data.get("overrides") or [], "quarantine": data.get("quarantine") or []}


def apply_overrides(docs: list[Document], overrides: dict) -> tuple[list[Document], list[dict]]:
    """Return (documents to chunk, quarantine records). Overridden metadata records its source and reason."""
    quarantined = {q["doc_id"]: q["reason"] for q in overrides["quarantine"]}
    by_id = {d.doc_id: d for d in docs}
    for o in overrides["overrides"]:
        doc = by_id.get(o["doc_id"])
        if doc is None:
            continue  # e.g. the scanned document with --no-ocr
        meta = doc.metadata
        for field, value in o["set"].items():
            meta.setdefault("stated_metadata", {})[field] = meta.get(field)  # None also when the field was absent
            meta.setdefault("metadata_source", {})[field] = o["metadata_source"]
            meta.setdefault("correction_reason", {})[field] = o["reason"]
            meta[field] = value
    kept = [d for d in docs if d.doc_id not in quarantined]
    return kept, [{"doc_id": k, "reason": v} for k, v in sorted(quarantined.items())]


def ingest(docs: list[Document], chunkers: dict) -> list[Chunk]:
    """Chunk every document with the chunker for its doc_type (or the ``default`` one)."""
    out: list[Chunk] = []
    for doc in docs:
        doc_type = doc.metadata.get("doc_type", "default")
        chunker = chunkers.get(doc_type) or chunkers.get("default")
        if chunker is None:
            raise ValueError(f"no chunker for doc_type {doc_type!r} and no default chunker")
        out.extend(chunker.chunk(doc))
    return out


def quarantine_path(store: str | Path) -> Path:
    store = Path(store)
    return store.with_name(store.stem + ".quarantine.jsonl")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a chunk store from the NovaTech corpus.")
    parser.add_argument("--corpus", type=Path, default=Path("data/novatech/corpus"))
    parser.add_argument("--out", type=Path, default=Path("indexes/chunks.jsonl"))
    parser.add_argument("--chunker", action="append", required=True, metavar="TYPE=module:Class")
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--ocr-cache", type=Path, default=None, help="folder of cached OCR text (indexes/ocr)")
    parser.add_argument("--overrides", type=Path, default=None, help="metadata overrides YAML")
    args = parser.parse_args(argv)
    chunkers = {}
    for item in args.chunker:
        doc_type, _, spec = item.partition("=")
        chunkers[doc_type] = load_chunker(spec)
    start = time.perf_counter()
    docs = load_corpus(args.corpus, ocr=not args.no_ocr, ocr_cache=args.ocr_cache)
    quarantined: list[dict] = []
    if args.overrides:
        try:
            overrides = load_overrides(args.overrides, {d.doc_id for d in docs} | _sidecar_ids(args.corpus))
        except ValueError as exc:
            print(exc)
            return 2
        docs, quarantined = apply_overrides(docs, overrides)
    chunks = ingest(docs, chunkers)
    save_chunks(chunks, args.out)
    qpath = quarantine_path(args.out)
    qpath.write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in quarantined), encoding="utf-8")
    print(f"{len(docs)} documents -> {chunk_stats(chunks)} in {time.perf_counter() - start:.1f}s -> {args.out}")
    if quarantined:
        print(f"{len(quarantined)} quarantined -> {qpath}")
    return 0


def _sidecar_ids(corpus: Path) -> set[str]:
    """Doc IDs of scanned documents, so overrides for them validate even with --no-ocr."""
    return {p.name.removesuffix(".meta.yaml") for p in Path(corpus).rglob("*.meta.yaml")}


if __name__ == "__main__":
    sys.exit(main())
