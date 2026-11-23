"""Workspace health check (also the participant access check).

    python scripts/preflight.py            # all checks
    python scripts/preflight.py --quick    # skip the model loads and the LLM call

Prints PASS / FAIL / SKIP per check with a one-line fix, writes preflight_report.txt next to the
current folder, and exits non-zero if anything FAILs. Secrets are never printed.
Participants cannot fix the servers, so most fixes say "tell the trainer".
"""
from __future__ import annotations

import argparse
import importlib
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

KIT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT_ROOT))

TRAINER = "tell the trainer"
REQUIRED_PYTHON = (3, 12)
OCR_IMAGE = Path(__file__).resolve().parent / "assets" / "ocr_check.png"

# requirement name -> import name
IMPORT_NAMES = {
    "openai": "openai", "tiktoken": "tiktoken", "torch": "torch", "sentence-transformers": "sentence_transformers",
    "transformers": "transformers", "faiss-cpu": "faiss", "rank-bm25": "rank_bm25", "qdrant-client": "qdrant_client",
    "numpy": "numpy", "pandas": "pandas", "ragas": "ragas", "langchain-community": "langchain_community", "mcp": "mcp", "neo4j": "neo4j", "pymupdf": "pymupdf",
    "pypdf": "pypdf", "pytesseract": "pytesseract", "pillow": "PIL", "pyyaml": "yaml", "python-dotenv": "dotenv",
    "pydantic": "pydantic", "jsonschema": "jsonschema", "matplotlib": "matplotlib", "jupyterlab": "jupyterlab", "ipykernel": "ipykernel",
    "pytest": "pytest",
}


@dataclass
class Result:
    name: str
    status: str  # PASS | FAIL | SKIP | INFO
    detail: str = ""
    fix: str = ""


class CheckFailed(Exception):
    """Raised by a check with the detail and the fix hint."""

    def __init__(self, detail: str, fix: str = TRAINER):
        super().__init__(detail)
        self.detail, self.fix = detail, fix


class Skipped(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


# ---------------------------------------------------------------- checks (each returns a detail string)

def check_python() -> str:
    v = sys.version_info
    if (v.major, v.minor) != REQUIRED_PYTHON:
        raise CheckFailed(f"Python {v.major}.{v.minor}.{v.micro}; the course environment is {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}",
                          f"select the course kernel/environment, or {TRAINER}")
    return f"Python {platform.python_version()} ({sys.executable})"


def requirement_names(path: Path = KIT_ROOT / "requirements.txt") -> list[str]:
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        names.append(line.split("==")[0].split("[")[0].strip().lower())
    return names


def check_imports() -> str:
    missing = []
    for name in requirement_names():
        module = IMPORT_NAMES.get(name, name.replace("-", "_"))
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - any import failure is a FAIL
            missing.append(f"{name} ({type(exc).__name__})")
    if missing:
        raise CheckFailed("cannot import: " + ", ".join(missing), f"the shared environment is incomplete - {TRAINER}")
    return f"{len(requirement_names())} packages importable"


def tesseract_cmd() -> str | None:
    return os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")


def check_tesseract() -> str:
    cmd = tesseract_cmd()
    if not cmd:
        raise CheckFailed("tesseract not found on PATH and TESSERACT_CMD unset", f"OCR is installed on the server - {TRAINER}")
    import pytesseract
    from PIL import Image

    pytesseract.pytesseract.tesseract_cmd = cmd
    text = pytesseract.image_to_string(Image.open(OCR_IMAGE)).upper()
    if "NOVATECH" not in text:
        raise CheckFailed(f"OCR of the bundled test image returned {text.strip()!r}", TRAINER)
    return f"{cmd}: OCR read the test image"


def check_embedding() -> str:
    from tools.local_models import embedding_model_name, load_embedder

    start = time.perf_counter()
    vec = load_embedder().encode(["NovaSync sync failure after upgrade"], normalize_embeddings=True)
    return f"{embedding_model_name()} dim={vec.shape[1]} ({time.perf_counter() - start:.1f}s)"


def check_reranker() -> str:
    from tools.local_models import load_reranker, reranker_model_name

    start = time.perf_counter()
    score = load_reranker().predict([("refund window", "Refunds are accepted within 14 days.")])
    return f"{reranker_model_name()} score={float(score[0]):.2f} ({time.perf_counter() - start:.1f}s)"


def check_faiss() -> str:
    import faiss
    import numpy as np

    vecs = np.eye(3, dtype="float32")
    index = faiss.IndexFlatIP(3)
    index.add(vecs)
    _, ids = index.search(vecs[1:2], 1)
    if int(ids[0][0]) != 1:
        raise CheckFailed("FAISS returned the wrong nearest neighbour", TRAINER)
    return "IndexFlatIP over 3 vectors OK"


def check_qdrant() -> str:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    with tempfile.TemporaryDirectory() as tmp:
        client = QdrantClient(path=tmp)
        try:
            client.create_collection("preflight", vectors_config=VectorParams(size=3, distance=Distance.COSINE))
            client.upsert("preflight", points=[PointStruct(id=i, vector=v, payload={"n": i})
                                               for i, v in enumerate([[1, 0, 0], [0, 1, 0], [0, 0, 1]])])
            hits = client.query_points("preflight", query=[0, 1, 0], limit=1).points
        finally:
            client.close()
    if not hits or hits[0].id != 1:
        raise CheckFailed("Qdrant local mode returned the wrong point", TRAINER)
    return "local mode: collection created, search OK"


def check_llm() -> str:
    from novaassist.config import ConfigError, llm_settings
    from novaassist.llm_client import get_client

    try:
        settings = llm_settings()
    except ConfigError as exc:
        raise CheckFailed(str(exc), TRAINER) from exc
    if settings.provider == "fake":
        raise Skipped("LLM_PROVIDER=fake (offline client); no real call made")
    start = time.perf_counter()
    reply = get_client(settings).complete([{"role": "user", "content": "Reply with the single word OK."}],
                                         max_completion_tokens=512)
    latency = time.perf_counter() - start
    return f"model={reply.model} latency={latency:.1f}s reply={reply.text.strip()[:20]!r}"


def check_neo4j() -> str:
    uri = os.environ.get("NEO4J_URI", "").strip()
    if not uri:
        raise Skipped("NEO4J_URI not set (the shared graph is needed from S11)")
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(uri, auth=(os.environ.get("NEO4J_USER", ""), os.environ.get("NEO4J_PASSWORD", "")))
    try:
        value = driver.execute_query("RETURN 1 AS ok").records[0]["ok"]
    finally:
        driver.close()
    if value != 1:
        raise CheckFailed("Neo4j answered but RETURN 1 did not return 1", TRAINER)
    return f"{uri} answered RETURN 1"


def check_git() -> str:
    git = shutil.which("git")
    if not git:
        raise CheckFailed("git not found", TRAINER)
    out = subprocess.run([git, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
    return out


def check_resources() -> str:
    usage = shutil.disk_usage(Path.home())
    ram = "unknown"
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        fields = dict(line.split(":", 1) for line in meminfo.read_text().splitlines() if ":" in line)
        ram = f"{int(fields['MemAvailable'].split()[0]) / 1024 / 1024:.1f} GB available"
    detail = f"disk free {usage.free / 1e9:.1f} GB of {usage.total / 1e9:.1f} GB; RAM {ram}; CPUs {os.cpu_count()}"
    if usage.free < 1e9:
        raise CheckFailed(detail + " (less than 1 GB free)", f"delete large files in your workspace, or {TRAINER}")
    return detail


def check_openai_reachable(host: str = "api.openai.com", port: int = 443) -> str:
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=5):
            pass
    except OSError as exc:
        raise CheckFailed(f"cannot reach {host}:{port} ({exc})", f"outbound access is a server setting - {TRAINER}") from exc
    return f"{host}:{port} reachable ({(time.perf_counter() - start) * 1000:.0f} ms)"


CHECKS: list[tuple[str, Callable[[], str], bool]] = [
    # (name, function, heavy) - heavy checks are skipped with --quick
    ("python version", check_python, False),
    ("packages importable", check_imports, False),
    ("tesseract OCR", check_tesseract, False),
    ("embedding model", check_embedding, True),
    ("reranker model", check_reranker, True),
    ("FAISS index", check_faiss, False),
    ("Qdrant local mode", check_qdrant, False),
    ("LLM call", check_llm, True),
    ("shared Neo4j", check_neo4j, False),
    ("git", check_git, False),
    ("disk and memory", check_resources, False),
    ("api.openai.com reachable", check_openai_reachable, False),
]


def redact(text: str) -> str:
    """Remove any configured secret from text before it is printed or written."""
    for var in ("LLM_API_KEY", "OPENAI_API_KEY", "NEO4J_PASSWORD"):
        secret = os.environ.get(var, "")
        if len(secret) >= 4:
            text = text.replace(secret, "***")
    return text


def run_checks(checks=None, quick: bool = False) -> list[Result]:
    results = []
    for name, fn, heavy in checks if checks is not None else CHECKS:
        if quick and heavy:
            results.append(Result(name, "SKIP", "skipped (--quick)"))
            continue
        try:
            results.append(Result(name, "PASS", fn()))
        except Skipped as exc:
            results.append(Result(name, "SKIP", exc.detail))
        except CheckFailed as exc:
            results.append(Result(name, "FAIL", exc.detail, exc.fix))
        except Exception as exc:  # noqa: BLE001 - an unexpected error is a FAIL with its message
            results.append(Result(name, "FAIL", f"{type(exc).__name__}: {exc}", TRAINER))
    return results


def format_report(results: list[Result]) -> str:
    lines = ["NovaAssist workspace health check", "=" * 34]
    for r in results:
        line = f"[{r.status}] {r.name}: {r.detail}"
        if r.status == "FAIL" and r.fix:
            line += f"\n       fix: {r.fix}"
        lines.append(line)
    fails = sum(r.status == "FAIL" for r in results)
    lines.append("-" * 34)
    lines.append(f"{len(results) - fails} ok/skipped, {fails} failed - " + ("READY" if not fails else "NOT READY"))
    return redact("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NovaAssist workspace health check")
    parser.add_argument("--quick", action="store_true", help="skip model loads and the LLM call")
    parser.add_argument("--report", type=Path, default=Path("preflight_report.txt"))
    args = parser.parse_args(argv)
    try:
        from novaassist.config import load_dotenv_once

        load_dotenv_once()
    except Exception:  # noqa: BLE001 - the imports check reports a broken environment
        pass
    results = run_checks(quick=args.quick)
    report = format_report(results)
    print(report)
    args.report.write_text(report + "\n", encoding="utf-8")
    print(f"report written to {args.report.resolve()}")
    return 1 if any(r.status == "FAIL" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
