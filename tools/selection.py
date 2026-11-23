"""Module 4 selection stack: query rewriting/expansion, HyDE, hybrid fusion, MMR, reranking — each toggleable.

    from tools.selection import SelectionStack, StackConfig
    stack = SelectionStack(chunks, dense=dense_index, sparse=bm25_index, embedder=emb, llm=llm, reranker=ce)
    result = stack.run("refund window NovaEdge", StackConfig(retriever="hybrid", mmr=True, rerank=True))
    result.hits          # final ranking (list[Hit])
    result.stages        # per-stage rankings, for the trace and the ablation report

Stage order (CAPSTONE_ARCHITECTURE §8 K3): rewrite -> retrieve (dense | HyDE-dense | sparse | hybrid)
-> MMR -> rerank -> cut to top_k. Every element is off unless the config turns it on.

Instructor-provided: rewrite, HyDE, MMR, rerank. **Your part (M4 lab): ``fuse``** — the hybrid combination of
the dense and sparse rankings — plus one technique of your choice in depth. LLM outputs (rewrites, HyDE
passages) are cached in ``runs/selection_cache.json`` so ablation runs are repeatable and cheap.

Technique hooks (M4 "one technique in depth"; every default reproduces the stack exactly as before):
rewrite_prompt / hyde_prompt (your prompt, with {q} for the question), rewrite_keep_original (search with the
original question plus the rewrite), hyde_query_weight (mix the question's embedding into the HyDE vector),
rerank_blend (blend the normalised first-stage score into the rerank score), rerank_text_tokens (truncate chunk
text sent to the cross-encoder). Set them in the knowledge ``selection`` block of your Context Spec.
``SelectionSource`` offers the stack as the knowledge Context Source; its items carry the retrieval evidence.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import numpy as np

from novaassist.tokens import count_tokens

from .chunk_store import to_context_item
from .chunking import Chunk
from .retrieval import Hit, rank_hits


@dataclass
class StackConfig:
    retriever: str = "dense"  # dense | sparse | hybrid
    rewrite: bool = False  # LLM query rewriting/expansion before retrieval
    hyde: bool = False  # dense path uses the embedding of a hypothetical answer
    mmr: bool = False
    mmr_lambda: float = 0.7
    rerank: bool = False
    candidate_k: int = 30  # depth retrieved before MMR/rerank
    top_k: int = 8
    # technique hooks (M4): the defaults leave the stack's behaviour unchanged
    rewrite_prompt: str | None = None  # None = the kit prompt; yours must contain {q}
    rewrite_keep_original: bool = False  # True: search with the original question + the rewrite + expansions
    hyde_prompt: str | None = None  # None = the kit prompt; yours must contain {q}
    hyde_query_weight: float = 0.0  # 0 = the HyDE passage alone; w mixes in the question's embedding
    rerank_blend: float = 0.0  # 0 = the cross-encoder score alone; b blends in the normalised first-stage score
    rerank_text_tokens: int | None = None  # None = whole chunk text to the cross-encoder; n = first n tokens

    @classmethod
    def from_spec(cls, spec) -> "StackConfig":
        """Read the knowledge component's ``selection`` block of a Context Spec. A prompt may be given inline or
        as a file path relative to the spec (e.g. ``rewrite_prompt: prompts/rewrite.txt``)."""
        sel = spec.selection("knowledge")
        known = {k: v for k, v in sel.items() if k in cls.__dataclass_fields__}
        for key in ("rewrite_prompt", "hyde_prompt"):
            value = known.get(key)
            if isinstance(value, str) and "{q}" not in value and spec.resolve(value).is_file():
                known[key] = spec.resolve(value).read_text(encoding="utf-8")
        return cls(**known)


def non_default(config: StackConfig) -> dict:
    """The fields that differ from the defaults (prompts shown as a short fingerprint), for traces and records."""
    out = {}
    for f in fields(config):
        value = getattr(config, f.name)
        if value != f.default:
            out[f.name] = f"custom:{_fingerprint(value)}" if f.name.endswith("_prompt") else value
    return out


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


@dataclass
class StackResult:
    query: str
    retrieval_query: str
    hits: list[Hit]
    stages: dict = field(default_factory=dict)  # stage name -> ranked chunk IDs
    llm_usage: dict = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0})
    latency_ms: float = 0.0


def fuse(dense: list[Hit], sparse: list[Hit], k: int) -> list[Hit]:
    """M4 lab: combine the dense and sparse rankings into one ranking (hybrid retrieval)."""
    raise NotImplementedError("M4 lab: implement hybrid fusion of the dense and sparse rankings")


REWRITE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["rewritten", "expansions"],
                  "properties": {"rewritten": {"type": "string"}, "expansions": {"type": "array", "items": {"type": "string"}}}}
REWRITE_PROMPT = ("Rewrite this support-staff question as a search query for NovaTech product documentation. "
                  "Keep product names, versions, error codes and configuration keys exactly. Add up to three short "
                  "expansion phrases (synonyms or the technical terms a document would use).\nQuestion: {q}")
HYDE_PROMPT = ("Write a short passage (60-100 words) that could appear in NovaTech product documentation and would "
               "answer this question. Do not mention that it is hypothetical.\nQuestion: {q}")


class LLMCache:
    """JSON cache for LLM-generated selection inputs, keyed by (kind, model, query)."""

    def __init__(self, path: str | Path = "runs/selection_cache.json"):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def get(self, key: str):
        return self.data.get(key)

    def put(self, key: str, value) -> None:
        self.data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")


def mmr(query_vec: np.ndarray, cand_ids: list[str], cand_vecs: np.ndarray, k: int, lam: float) -> list[tuple[str, float]]:
    """Maximal marginal relevance: lam * sim(query, d) - (1 - lam) * max sim(d, already selected)."""
    rel = cand_vecs @ query_vec
    chosen: list[int] = []
    out = []
    remaining = list(range(len(cand_ids)))
    while remaining and len(chosen) < k:
        def score(i):
            redundancy = max((float(cand_vecs[i] @ cand_vecs[j]) for j in chosen), default=0.0)
            return lam * float(rel[i]) - (1 - lam) * redundancy
        best = max(remaining, key=lambda i: (round(score(i), 6), -remaining.index(i)))
        out.append((cand_ids[best], score(best)))
        chosen.append(best)
        remaining.remove(best)
    return out


class SelectionStack:
    def __init__(self, chunks: list[Chunk], dense=None, sparse=None, embedder=None, llm=None, reranker=None,
                 fusion=None, cache: LLMCache | None = None):
        self.by_id = {c.chunk_id: c for c in chunks}
        self.dense, self.sparse, self.embedder, self.llm = dense, sparse, embedder, llm
        self.reranker = reranker  # object with .predict([(query, text), ...]) -> scores (e.g. a CrossEncoder)
        self.fusion = fusion or fuse
        self.cache = cache or LLMCache()
        self._vec_cache: dict[str, np.ndarray] = {}

    # ----------------------------------------------------------------- LLM-backed inputs (cached)
    def _llm_call(self, kind: str, prompt: str, query: str, schema=None, usage=None, default_prompt: bool = True):
        # a custom prompt gets its own cache entries; the kit prompts keep the original keys
        model = getattr(self.llm, 'model', '?')
        key = f"{kind}|{model}|{query}" if default_prompt else f"{kind}|{model}|{_fingerprint(prompt)}|{query}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        if self.llm is None:
            raise ValueError(f"{kind} needs an LLM client")
        reply = self.llm.complete([{"role": "user", "content": prompt.format(q=query)}], json_schema=schema,
                                  schema_name=kind)
        for k in ("prompt_tokens", "completion_tokens"):
            usage[k] += reply.usage.get(k, 0)
        value = reply.parsed if schema else reply.text
        self.cache.put(key, value)
        return value

    def rewrite(self, query: str, usage: dict, config: StackConfig | None = None) -> str:
        config = config or StackConfig()
        prompt = config.rewrite_prompt or REWRITE_PROMPT
        out = self._llm_call("rewrite", prompt, query, REWRITE_SCHEMA, usage, default_prompt=prompt == REWRITE_PROMPT)
        parts = [out["rewritten"]] + list(out.get("expansions", []))
        return " ".join(([query] if config.rewrite_keep_original else []) + parts)

    def hyde_vector(self, query: str, usage: dict, config: StackConfig | None = None) -> np.ndarray:
        config = config or StackConfig()
        prompt = config.hyde_prompt or HYDE_PROMPT
        passage = self._llm_call("hyde", prompt, query, None, usage, default_prompt=prompt == HYDE_PROMPT)
        vec = self.embedder.embed([passage])[0]
        w = config.hyde_query_weight
        if w:
            vec = (1 - w) * vec + w * self.embedder.embed([query])[0]
            vec = vec / max(float(np.linalg.norm(vec)), 1e-9)
        return vec

    def _rerank_text(self, chunk_id: str, max_tokens: int | None) -> str:
        """The chunk text for the cross-encoder, cut to the first max_tokens tokens (word boundary) if set."""
        text = self.by_id[chunk_id].text
        if not max_tokens or count_tokens(text) <= max_tokens:
            return text
        words = text.split()
        lo, hi = 0, len(words)
        while lo < hi:  # the longest word prefix within max_tokens
            mid = (lo + hi + 1) // 2
            lo, hi = (mid, hi) if count_tokens(" ".join(words[:mid])) <= max_tokens else (lo, mid - 1)
        return " ".join(words[:lo])

    def _vectors(self, chunk_ids: list[str]) -> np.ndarray:
        todo = [cid for cid in chunk_ids if cid not in self._vec_cache]
        if todo:
            for cid, vec in zip(todo, self.embedder.embed([self.by_id[c].text for c in todo])):
                self._vec_cache[cid] = vec
        return np.stack([self._vec_cache[c] for c in chunk_ids])

    # ----------------------------------------------------------------- the stack
    def run(self, query: str, config: StackConfig) -> StackResult:
        start = time.perf_counter()
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        stages: dict[str, list[str]] = {}
        q = self.rewrite(query, usage, config) if config.rewrite else query
        if config.rewrite:
            stages["rewrite"] = [q]
        depth = max(config.candidate_k, config.top_k)

        def dense_hits():
            if config.hyde:
                return self.dense.search(q, k=depth, query_vector=self.hyde_vector(query, usage, config))
            return self.dense.search(q, k=depth)

        if config.retriever == "dense":
            hits = dense_hits()
        elif config.retriever == "sparse":
            hits = self.sparse.search(q, k=depth)
        elif config.retriever == "hybrid":
            d, s = dense_hits(), self.sparse.search(q, k=depth)
            stages["dense"], stages["sparse"] = [h.chunk_id for h in d], [h.chunk_id for h in s]
            hits = self.fusion(d, s, depth)
        else:
            raise ValueError(f"unknown retriever {config.retriever!r}")
        stages["retrieve"] = [h.chunk_id for h in hits]
        evidence = {h.chunk_id: h.evidence for h in hits}  # kept through the later stages
        if config.mmr:
            ids = [h.chunk_id for h in hits]
            qv = self.hyde_vector(query, usage, config) if config.hyde else self.embedder.embed([q])[0]
            hits = rank_hits(mmr(qv, ids, self._vectors(ids), len(ids), config.mmr_lambda), len(ids), "mmr")
            stages["mmr"] = [h.chunk_id for h in hits]
        if config.rerank:
            if self.reranker is None:
                raise ValueError("rerank is on but no reranker was given")
            scores = self.reranker.predict([(query, self._rerank_text(h.chunk_id, config.rerank_text_tokens))
                                            for h in hits])
            if config.rerank_blend:
                scores = _blend(scores, [h.score for h in hits], config.rerank_blend)
            hits = rank_hits([(h.chunk_id, s) for h, s in zip(hits, scores)], len(hits), "rerank")
            stages["rerank"] = [h.chunk_id for h in hits]
        final = [Hit(h.chunk_id, h.score, i, h.retriever, evidence.get(h.chunk_id))
                 for i, h in enumerate(hits[:config.top_k], start=1)]
        return StackResult(query, q, final, stages, usage, (time.perf_counter() - start) * 1000)


def _minmax(values) -> list[float]:
    values = [float(v) for v in values]
    lo, hi = min(values, default=0.0), max(values, default=0.0)
    return [(v - lo) / (hi - lo) if hi > lo else 1.0 for v in values]


def _blend(rerank_scores, first_stage_scores, weight: float) -> list[float]:
    """(1 - weight) * normalised rerank score + weight * normalised first-stage score."""
    return [(1 - weight) * r + weight * f for r, f in zip(_minmax(rerank_scores), _minmax(first_stage_scores))]


def config_dict(config: StackConfig) -> dict:
    return asdict(config)


class SelectionSource:
    """The selection stack as the knowledge Context Source (one stack run per request).

    Items carry the retrieval evidence in ``metadata["retrieval"]`` (traced): retriever, rank, score, the query
    and the query actually searched, the fusion evidence of the hit, and the configuration fields that differ
    from the defaults. ``render(chunk) -> str`` optionally replaces the item text (e.g. a citation header).
    """

    channel = "knowledge"

    def __init__(self, stack: SelectionStack, config: StackConfig, name: str = "selection_stack", render=None):
        self.stack, self.config, self.name, self.render = stack, config, name, render
        self.last_result: StackResult | None = None

    def gather(self, request, session, spec) -> list:
        result = self.stack.run(request.text, self.config)
        self.last_result = result
        settings = non_default(self.config)
        items = []
        for hit in result.hits:
            chunk = self.stack.by_id[hit.chunk_id]
            item = to_context_item(chunk, relevance=hit.score, source=self.name)
            if self.render is not None:
                item.content = self.render(chunk)
                item.token_count = count_tokens(item.content)
            item.metadata["retrieval"] = {"retriever": hit.retriever, "rank": hit.rank, "score": hit.score,
                                          "query": request.text, "retrieval_query": result.retrieval_query,
                                          "evidence": hit.evidence, "config": settings}
            items.append(item)
        return items
