"""Score a retrieval run against gold labels, whatever chunking produced it.

Gold labels are chunk IDs under the course's reference chunking. Your chunks have different IDs, so a hit is
mapped to the gold chunks it *covers*: same document, and at least half of the gold chunk's words appear in
your chunk (or your chunk is mostly inside the gold chunk). Metrics then follow EVALUATION_SPEC §5.2 exactly.

Run file (one JSON object per query, written with ``write_run``):
    {"query_id": "GQ-07", "retriever": "faiss", "k": 8,
     "hits": [{"chunk_id": "...", "doc_id": "...", "score": 0.83, "rank": 1, "text": "..."}, ...]}

Participants never see labels: the trainer's scoring command prints per-query-type aggregates only.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from .metrics import aggregate

WORD = re.compile(r"\w+")
GOLD_CONTAINMENT = 0.5  # share of a gold chunk's words that a hit must contain
HIT_CONTAINMENT = 0.8  # or: share of the hit's words that lie inside the gold chunk


def _bag(text: str) -> Counter:
    return Counter(w.lower() for w in WORD.findall(text))


def _contained(part: Counter, whole: Counter) -> float:
    total = sum(part.values())
    return sum(min(n, whole[w]) for w, n in part.items()) / total if total else 0.0


def covered_gold(hit: dict, gold_chunks: dict[str, dict]) -> set[str]:
    """Gold chunk IDs this hit covers. ``gold_chunks``: {chunk_id: {"doc_id", "text"}}."""
    bag = _bag(hit["text"])
    out = set()
    for gid, g in gold_chunks.items():
        if g["doc_id"] != hit["doc_id"]:
            continue
        gbag = g.setdefault("_bag", _bag(g["text"]))
        if _contained(gbag, bag) >= GOLD_CONTAINMENT or _contained(bag, gbag) >= HIT_CONTAINMENT:
            out.add(gid)
    return out


def score_hits(covers: list[set[str]], labels: dict, k: int) -> dict:
    """Metrics over hits mapped to gold IDs (``covers[i]`` = gold chunks covered by the hit at rank i+1)."""
    relevant, must = set(labels["relevant_chunks"]), set(labels["must_retrieve"])
    forbidden = set(labels.get("forbidden_chunks", []))
    top = covers[:k]
    got = set().union(*top) if top else set()
    out = {"recall@k": len(got & relevant) / len(relevant),
           "precision@k": sum(1 for c in top if c & relevant) / k,
           "must_retrieve_hit": 1.0 if must <= got else 0.0,
           "forbidden_rate": sum(1 for c in top if c & forbidden) / k}
    if labels.get("query_type") == "needle" or len(relevant) == 1:
        out["reciprocal_rank"] = next((1.0 / i for i, c in enumerate(covers, 1) if c & relevant), 0.0)
    return out


def score_run(run_rows: list[dict], labels: list[dict], gold_chunks: dict[str, dict], k: int | None = None) -> dict:
    """Per-query metrics and per-type aggregates for one run."""
    by_id = {q["query_id"]: q for q in labels}
    per_query, types = {}, {}
    for row in run_rows:
        q = by_id.get(row["query_id"])
        if not q:
            continue
        kk = k or row.get("k") or len(row["hits"])
        covers = [covered_gold(h, gold_chunks) for h in sorted(row["hits"], key=lambda h: h["rank"])]
        per_query[row["query_id"]] = score_hits(covers, q, kk)
        types[row["query_id"]] = q["query_type"]
    missing = sorted(set(by_id) - set(per_query))
    return {"per_query": per_query, "aggregate": aggregate(per_query, types), "missing_queries": missing}


def write_run(path: str | Path, results: dict[str, list], chunks_by_id: dict, retriever: str, k: int) -> Path:
    """``results``: {query_id: [Hit, ...]} from a retriever's search()."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for qid in sorted(results):
            hits = [{"chunk_id": h.chunk_id, "doc_id": chunks_by_id[h.chunk_id].doc_id, "score": h.score,
                     "rank": h.rank, "text": chunks_by_id[h.chunk_id].text} for h in results[qid]]
            fh.write(json.dumps({"query_id": qid, "retriever": retriever, "k": k, "hits": hits}, ensure_ascii=False) + "\n")
    return path


def read_run(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def load_queries(path: str | Path = "data/novatech/eval/queries.jsonl") -> list[dict]:
    """The participant-safe query set: query_id, query, query_type."""
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def format_aggregate(agg: dict) -> str:
    metrics = ["recall@k", "precision@k", "must_retrieve_hit", "forbidden_rate", "MRR"]
    lines = [f"{'query type':<22}{'n':>4}" + "".join(f"{m:>18}" for m in metrics)]
    for group, row in agg.items():
        lines.append(f"{group:<22}{row['queries']:>4}" + "".join(f"{row.get(m, ''):>18}" for m in metrics))
    return "\n".join(lines)
