"""ISOLATE (CAPSTONE_ARCHITECTURE §7): one sub-agent boundary with an explicit contract.

Inbound: a ``Brief`` (the task + only the state fields the contract allows). Outbound: a structured digest
(findings with citations, overall confidence, open questions) within a size cap. The sub-agent works in its own
window and writes its own trace (``sub_trace_id``), so the parent window never sees its raw chunks or tool results.

    sub = SubAgent(spec, sources=[knowledge_source], llm=llm, parent_session_id=session.session_id)
    brief = make_brief("Which Premium customers are exposed to SA-2026-04?", session.state, allowed=["active_customer"])
    result = sub.run(brief)                      # result.digest, result.sub_trace_id, result.problems
    item = digest_item(result)                   # a knowledge-channel item for the parent window

Your design (M7): *when* a request or sub-task is isolated, what crosses inward, the digest size cap, and how the
parent uses the digest. Declare the boundary in the Context Spec (components.<name>.isolation).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime

from . import pipeline
from .context_items import CHANNELS, ContextItem
from .context_source import Request, Session
from .tokens import count_tokens
from .trace import TraceWriter
from .window import render

DIGEST_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["findings", "confidence", "open_questions"],
    "properties": {
        "findings": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["statement", "citations"],
            "properties": {"statement": {"type": "string"}, "citations": {"type": "array", "items": {"type": "string"}}}}},
        "confidence": {"type": "number"},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
}
DIGEST_INSTRUCTION = ("You are a research sub-agent. Answer the task using only the supplied context. Return JSON: findings "
                      "(each a statement with the IDs of the documents, chunks or records that support it), an overall "
                      "confidence between 0 and 1, and open questions. Every finding needs at least one citation.")


@dataclass
class Brief:
    task: str
    state: dict = field(default_factory=dict)
    brief_id: str = field(default_factory=lambda: f"BRIEF-{uuid.uuid4().hex[:8]}")


def make_brief(task: str, state: dict, allowed: list[str]) -> Brief:
    """Only the declared state fields cross the boundary inward."""
    return Brief(task=task, state={k: v for k, v in (state or {}).items() if k in allowed})


def validate_digest(digest, max_tokens: int) -> list[str]:
    """``digest_conforms``: schema, citations on every finding, confidence range, size cap."""
    problems = []
    if not isinstance(digest, dict):
        return ["digest is not a JSON object"]
    for key in ("findings", "confidence", "open_questions"):
        if key not in digest:
            problems.append(f"missing {key}")
    for i, f in enumerate(digest.get("findings") or []):
        if not isinstance(f, dict) or not f.get("statement"):
            problems.append(f"finding {i} has no statement")
        elif not f.get("citations"):
            problems.append(f"finding {i} has no citations")
    conf = digest.get("confidence")
    if not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        problems.append("confidence must be a number between 0 and 1")
    size = count_tokens(json.dumps(digest, ensure_ascii=False))
    if size > max_tokens:
        problems.append(f"digest is {size} tokens, over the {max_tokens}-token cap")
    return problems


@dataclass
class SubAgentResult:
    brief: Brief
    digest: dict
    sub_trace_id: str
    trace_path: str
    working_item_ids: list
    problems: list
    usage: dict


class SubAgent:
    """Runs one task in its own context window with its own sources and trace."""

    def __init__(self, spec, sources: list, llm, parent_session_id: str, boundary: str = "research",
                 digest_max_tokens: int = 600, trace_dir=None):
        self.spec, self.sources, self.llm = spec, sources, llm
        self.parent_session_id, self.boundary, self.digest_max_tokens = parent_session_id, boundary, digest_max_tokens
        self.trace_dir = trace_dir
        self._n = 0

    def run(self, brief: Brief) -> SubAgentResult:
        self._n += 1
        sub_id = f"{self.parent_session_id}-sub{self._n:02d}"
        tracer = TraceWriter(sub_id, self.trace_dir)
        state_text = "; ".join(f"{k}: {v}" for k, v in sorted(brief.state.items()))
        request = Request(f"REQ-{uuid.uuid4().hex[:10]}", brief.task + (f"\nKnown state: {state_text}" if state_text else ""),
                          datetime.now().isoformat(timespec="seconds"))
        tracer.start_request(request.request_id, boundary=self.boundary, brief_id=brief.brief_id,
                             parent_session_id=self.parent_session_id, spec_version=self.spec.version)
        session = Session(sub_id, state=dict(brief.state))
        candidates = {ch: [] for ch in CHANNELS}
        for source in self.sources:
            items = source.gather(request, session, self.spec)
            candidates[source.channel].extend(items)
            tracer.emit("candidates", stage="gather", source=source.name, channel=source.channel,
                        items=[it.trace_view() for it in items])
        selected, _ = pipeline.select_stage(candidates, self.spec, tracer)
        working = [it.item_id for items in selected.values() for it in items]
        messages, layout = render(selected, self.spec)
        messages.insert(0, {"role": "system", "content": DIGEST_INSTRUCTION})
        tracer.emit("window", order=self.spec.assembly_order, channels=layout,
                    total_tokens=sum(ch["tokens"] for ch in layout.values()))
        reply = self.llm.complete(messages, json_schema=DIGEST_SCHEMA, schema_name="digest")
        digest = reply.parsed if isinstance(reply.parsed, dict) else {}
        problems = validate_digest(digest, self.digest_max_tokens)
        tracer.emit("llm_call", model=reply.model, provider=reply.provider, prompt_tokens=reply.usage["prompt_tokens"],
                    completion_tokens=reply.usage["completion_tokens"], latency_ms=round(reply.latency_ms, 1))
        tracer.emit("digest", digest=digest, conforms=not problems, problems=problems,
                    tokens=count_tokens(json.dumps(digest, ensure_ascii=False)))
        return SubAgentResult(brief, digest, sub_id, str(tracer.path), working, problems, reply.usage)


def digest_item(result: SubAgentResult) -> ContextItem:
    """The digest as a knowledge-channel item for the parent window (provenance: the sub-agent's trace)."""
    lines = [f"Research digest ({result.sub_trace_id}, confidence {result.digest.get('confidence')}):"]
    for f in result.digest.get("findings", []):
        lines.append(f"- {f['statement']} [{', '.join(f.get('citations', []))}]")
    for q in result.digest.get("open_questions", []):
        lines.append(f"- Open question: {q}")
    return ContextItem(item_id=f"digest:{result.sub_trace_id}", channel="knowledge", content="\n".join(lines),
                       source="subagent", provenance={"type": "subagent", "ref": result.sub_trace_id,
                                                      "brief_id": result.brief.brief_id},
                       timestamp=datetime.now().isoformat(timespec="seconds"),
                       relevance=result.digest.get("confidence"),
                       metadata={"citations": sorted({c for f in result.digest.get("findings", []) for c in f.get("citations", [])}),
                                 "conforms": not result.problems})


def boundary_leakage(parent_item_ids: list[str], result: SubAgentResult) -> list[str]:
    """Sub-agent working items that appear in the parent window (should be empty: only the digest crosses)."""
    return sorted(set(parent_item_ids) & set(result.working_item_ids))


def isolation_event(result: SubAgentResult) -> dict:
    """Fields for the parent's ``isolate`` trace event (EVALUATION_SPEC §12 isolation block)."""
    return {"boundary": "research", "invoked": True, "inbound_brief_ref": result.brief.brief_id,
            "digest_ref": f"digest:{result.sub_trace_id}", "sub_trace_id": result.sub_trace_id,
            "digest_conforms": not result.problems, "brief": asdict(result.brief)}
