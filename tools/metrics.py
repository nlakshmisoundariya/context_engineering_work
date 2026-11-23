"""Retrieval metrics (EVALUATION_SPEC §5.2). Definitions are curriculum: read them, recompute them by hand.

All functions take ranked chunk IDs (best first) and sets of gold chunk IDs. K always comes from the caller
(read it from your Context Spec), never from a constant here.

- recall_at_k        |relevant ∩ top-K| / |relevant|
- precision_at_k     |relevant ∩ top-K| / K
- must_retrieve_hit  1.0 if every must-retrieve chunk is in top-K, else 0.0   (the primary teaching metric)
- reciprocal_rank    1 / rank of the first relevant chunk (0 if none); MRR = mean over needle/single-gold queries
- forbidden_rate     |forbidden ∩ top-K| / K   (superseded, wrong-product and distractor material)

``aggregate`` averages per query type, which is all participants see from the hidden gold labels.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import mean


def top_k(ranked: list[str], k: int) -> list[str]:
    if k <= 0:
        raise ValueError("k must be positive")
    return list(ranked[:k])


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        raise ValueError("recall is undefined without relevant chunks")
    return len(set(top_k(ranked, k)) & set(relevant)) / len(relevant)


def precision_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    return len(set(top_k(ranked, k)) & set(relevant)) / k


def must_retrieve_hit(ranked: list[str], must: set[str], k: int) -> float:
    return 1.0 if set(must) <= set(top_k(ranked, k)) else 0.0


def reciprocal_rank(ranked: list[str], relevant: set[str]) -> float:
    for i, cid in enumerate(ranked, start=1):
        if cid in relevant:
            return 1.0 / i
    return 0.0


def forbidden_rate(ranked: list[str], forbidden: set[str], k: int) -> float:
    return len(set(top_k(ranked, k)) & set(forbidden)) / k


def query_metrics(ranked: list[str], labels: dict, k: int) -> dict:
    """All metrics for one query. ``labels``: relevant_chunks, must_retrieve, forbidden_chunks, query_type."""
    relevant, must = set(labels["relevant_chunks"]), set(labels["must_retrieve"])
    out = {"recall@k": recall_at_k(ranked, relevant, k), "precision@k": precision_at_k(ranked, relevant, k),
           "must_retrieve_hit": must_retrieve_hit(ranked, must, k),
           "forbidden_rate": forbidden_rate(ranked, set(labels.get("forbidden_chunks", [])), k)}
    if labels.get("query_type") == "needle" or len(relevant) == 1:
        out["reciprocal_rank"] = reciprocal_rank(ranked, relevant)
    return out


def aggregate(per_query: dict[str, dict], query_types: dict[str, str]) -> dict:
    """Mean of each metric per query type and overall. MRR is the mean reciprocal rank where defined."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for qid, metrics in per_query.items():
        groups[query_types[qid]].append(metrics)
        groups["all"].append(metrics)
    out = {}
    for group, rows in sorted(groups.items()):
        names = sorted({n for r in rows for n in r})
        out[group] = {"queries": len(rows)}
        for name in names:
            values = [r[name] for r in rows if name in r]
            key = "MRR" if name == "reciprocal_rank" else name
            out[group][key] = round(mean(values), 4)
    return out
