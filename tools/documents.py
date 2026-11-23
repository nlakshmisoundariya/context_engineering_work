"""Document loading for the NovaTech corpus: Markdown + YAML front-matter, born-digital PDFs, scanned PDFs (OCR).

    from tools.documents import load_corpus
    docs = load_corpus("data/novatech/corpus")                 # Markdown + scanned (OCR) documents
    docs = load_corpus("data/novatech/corpus", ocr=False)       # skip OCR (the scanned document is then left out)
    docs = load_corpus("data/novatech/corpus", ocr_cache="indexes/ocr")   # reuse OCR text cached earlier

Metadata is passed through exactly as the document states it: a field that is absent stays absent (it is
*unknown*, which is different from null). Deciding what to do about missing or wrong metadata is your job.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Document:
    doc_id: str
    text: str  # body text (Markdown, PDF text or OCR text) without front-matter
    metadata: dict  # front-matter / sidecar fields exactly as stated
    source_path: str
    source_format: str  # markdown | pdf | ocr
    twins: list = field(default_factory=list)  # other files with the same doc_id (e.g. a PDF twin of a Markdown policy)


class DocumentError(ValueError):
    pass


def split_front_matter(raw: str) -> tuple[dict, str]:
    """Return (metadata, body) for a Markdown file that starts with a YAML front-matter block."""
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---\n", 4)
    if end < 0:
        raise DocumentError("front-matter block is not closed with '---'")
    meta = yaml.safe_load(raw[4:end]) or {}
    meta = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in meta.items()}
    return meta, raw[end + 5:].lstrip("\n")


def load_markdown(path: str | Path) -> Document:
    path = Path(path)
    meta, body = split_front_matter(path.read_text(encoding="utf-8"))
    doc_id = meta.get("doc_id") or path.stem
    return Document(doc_id, body, meta, path.as_posix(), "markdown")


def pdf_text(path: str | Path) -> str:
    """Text layer of a born-digital PDF (empty for a scanned, image-only PDF)."""
    import pymupdf

    with pymupdf.open(path) as doc:
        return "\n".join(page.get_text() for page in doc)


def tesseract_cmd() -> str | None:
    return os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")


def ocr_pdf(path: str | Path, dpi: int = 300, lang: str = "eng") -> str:
    """OCR every page of an image-only PDF with Tesseract. Pages are separated by form feeds."""
    import pymupdf
    import pytesseract
    from PIL import Image

    cmd = tesseract_cmd()
    if not cmd:
        raise DocumentError("Tesseract not found (PATH or TESSERACT_CMD). On the course servers it is installed; "
                            "tell the trainer if you see this there.")
    pytesseract.pytesseract.tesseract_cmd = cmd
    pages = []
    with pymupdf.open(path) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
            image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
            pages.append(pytesseract.image_to_string(image, lang=lang))
    return "\f".join(pages)


def ocr_cache_path(cache_dir: str | Path, doc_id: str) -> Path:
    """Where the OCR text of one scanned document is cached: <cache_dir>/<doc_id>.txt."""
    return Path(cache_dir) / f"{doc_id}.txt"


def load_scanned(pdf_path: str | Path, ocr_cache: str | Path | None = None, dpi: int = 300) -> Document:
    """A scanned PDF with its sidecar ``<doc_id>.meta.yaml``; the text comes from OCR.

    With ``ocr_cache``, cached OCR text is used when present; otherwise OCR runs and its text is cached.
    """
    pdf_path = Path(pdf_path)
    sidecar = pdf_path.with_suffix(".meta.yaml")
    meta = yaml.safe_load(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
    meta = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in (meta or {}).items()}
    doc_id = meta.get("doc_id") or pdf_path.stem
    cached = ocr_cache_path(ocr_cache, doc_id) if ocr_cache else None
    if cached and cached.is_file():
        text = cached.read_text(encoding="utf-8")
    else:
        text = ocr_pdf(pdf_path, dpi=dpi)
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(text, encoding="utf-8")
    return Document(doc_id, text, meta, pdf_path.as_posix(), "ocr")


def load_corpus(root: str | Path, ocr: bool = True, ocr_cache: str | Path | None = None) -> list[Document]:
    """Load every document once. A PDF twin of a Markdown document is recorded on it, not loaded twice.

    ``ocr_cache``: folder of cached OCR text (see ``load_scanned``); Tesseract runs only for uncached documents.
    """
    root = Path(root)
    if not root.is_dir():
        raise DocumentError(f"corpus folder not found: {root}")
    docs: dict[str, Document] = {}
    for md in sorted(root.rglob("*.md")):
        doc = load_markdown(md)
        docs[doc.doc_id] = doc
    for pdf in sorted(root.rglob("*.pdf")):
        if pdf.stem in docs:
            docs[pdf.stem].twins.append(pdf.as_posix())
        elif pdf.with_suffix(".meta.yaml").exists():
            if ocr:
                doc = load_scanned(pdf, ocr_cache)
                docs[doc.doc_id] = doc
        else:
            text = pdf_text(pdf)
            docs[pdf.stem] = Document(pdf.stem, text, {}, pdf.as_posix(), "pdf")
    return [docs[k] for k in sorted(docs)]
