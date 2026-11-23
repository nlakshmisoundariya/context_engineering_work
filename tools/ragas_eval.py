"""RAGAS baseline helper: faithfulness and answer relevancy (EVALUATION_SPEC §11 — primary metrics only).

    from tools.ragas_eval import ragas_scores
    scores = ragas_scores([{"question": q, "answer": a, "contexts": [chunk texts...]}, ...])

RAGAS talks to the model through adapters around the course LLM client and the local embedding model, so it
uses the same provider, key and model as NovaAssist. Scores are cached by (sample, model) in
``runs/ragas_cache.json``. RAGAS scores are tracked against baselines; they never decide PASS/FAIL.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path


def _adapters():
    """Build the adapter classes lazily so importing this module never requires ragas."""
    from langchain_core.outputs import Generation, LLMResult
    from ragas.embeddings.base import BaseRagasEmbeddings
    from ragas.llms.base import BaseRagasLLM

    class LLMAdapter(BaseRagasLLM):
        """RAGAS -> novaassist LLM client. Temperature requests are ignored (the course model rejects them)."""

        def __init__(self, client, max_completion_tokens: int = 4096):
            super().__init__()
            self.client, self.max_completion_tokens = client, max_completion_tokens

        def generate_text(self, prompt, n=1, temperature=None, stop=None, callbacks=None):
            text = prompt.to_string() if hasattr(prompt, "to_string") else str(prompt)
            reply = self.client.complete([{"role": "user", "content": text}],
                                         max_completion_tokens=self.max_completion_tokens)
            return LLMResult(generations=[[Generation(text=reply.text) for _ in range(n)]])

        async def agenerate_text(self, prompt, n=1, temperature=None, stop=None, callbacks=None):
            return await asyncio.to_thread(self.generate_text, prompt, n, temperature, stop, callbacks)

        def is_finished(self, response) -> bool:
            return True

    class EmbeddingsAdapter(BaseRagasEmbeddings):
        def __init__(self, embedder):
            super().__init__()
            self.embedder = embedder

        def embed_documents(self, texts):
            return self.embedder.embed(list(texts)).tolist()

        def embed_query(self, text):
            return self.embedder.embed([text])[0].tolist()

        async def aembed_documents(self, texts):
            return self.embed_documents(texts)

        async def aembed_query(self, text):
            return self.embed_query(text)

    return LLMAdapter, EmbeddingsAdapter


def _sample_key(sample: dict, model: str) -> str:
    raw = json.dumps([model, sample["question"], sample["answer"], sample["contexts"]], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ragas_scores(samples: list[dict], llm=None, embedder=None, cache_path: str | Path = "runs/ragas_cache.json") -> dict:
    """Faithfulness and answer relevancy per sample plus means. ``samples``: question, answer, contexts[]."""
    from ragas import EvaluationDataset, SingleTurnSample, evaluate
    from ragas.metrics import answer_relevancy, faithfulness

    from novaassist.llm_client import get_client

    from .embeddings import Embedder

    llm = llm or get_client()
    embedder = embedder or Embedder()
    LLMAdapter, EmbeddingsAdapter = _adapters()
    cache_path = Path(cache_path)
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    keys = [_sample_key(s, llm.model) for s in samples]
    todo = [(k, s) for k, s in zip(keys, samples) if k not in cache]
    if todo:
        dataset = EvaluationDataset([SingleTurnSample(user_input=s["question"], response=s["answer"],
                                                      retrieved_contexts=list(s["contexts"])) for _, s in todo])
        result = evaluate(dataset, metrics=[faithfulness, answer_relevancy], llm=LLMAdapter(llm),
                          embeddings=EmbeddingsAdapter(embedder), show_progress=False)
        for (k, _), row in zip(todo, result.to_pandas().to_dict("records")):
            cache[k] = {"faithfulness": row.get("faithfulness"), "answer_relevancy": row.get("answer_relevancy")}
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    per_sample = [cache[k] for k in keys]

    def mean(name):
        values = [r[name] for r in per_sample if r.get(name) is not None]
        return round(sum(values) / len(values), 4) if values else None

    return {"per_sample": per_sample, "faithfulness": mean("faithfulness"), "answer_relevancy": mean("answer_relevancy"),
            "model": llm.model}
