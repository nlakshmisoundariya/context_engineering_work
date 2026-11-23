"""Agent orchestrator: the linear request lifecycle (CAPSTONE_ARCHITECTURE §4.3).

    receive input -> gather candidates from every Context Source -> WRITE -> SELECT -> COMPRESS -> ISOLATE
    -> render window -> call LLM -> (tool loop, from M6) -> return response -> write-backs -> finish trace

Every step emits raw trace events, so one request can be explained from its trace alone.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import pipeline
from .context_items import CHANNELS, ContextItem
from .context_source import Request, Session
from .economics import ccu_cost
from .llm_client import LLMClient
from .spec import ContextSpec
from .tokens import counter_name
from .trace import TraceWriter
from .window import render

CITATION = re.compile(r"\b(DOC-[A-Z]{2,3}-\d{3})(?:::(c\d{2,3}))?")


@dataclass
class Result:
    text: str
    request_id: str
    trace_path: Path
    window_tokens: int
    usage: dict
    window: dict = field(default_factory=dict)  # channel -> [{item_id, text, source, provenance}] actually sent
    tool_results: list = field(default_factory=list)  # [{tool, args, text}] as they re-entered the window
    isolation: dict | None = None  # the isolate event fields when a sub-agent ran


def dataset_version(data_root: Path | None = None) -> str | None:
    manifest = (data_root or Path(__file__).resolve().parents[1] / "data" / "novatech") / "MANIFEST.json"
    if manifest.is_file():
        return json.loads(manifest.read_text(encoding="utf-8")).get("dataset_version")
    return None


def citations(text: str) -> list[dict]:
    seen, out = set(), []
    for doc_id, chunk in CITATION.findall(text):
        key = (doc_id, chunk or None)
        if key not in seen:
            seen.add(key)
            out.append({"doc_id": doc_id, "chunk_id": f"{doc_id}::{chunk}" if chunk else None})
    return out


class Orchestrator:
    def __init__(self, spec: ContextSpec, sources: list, llm: LLMClient, session: Session,
                 tracer: TraceWriter | None = None, fault_flags: list[str] | None = None, write_backs: list | None = None,
                 tool_client=None, result_manager=None, isolator=None, faults=None):
        from .faults import FaultHarness

        # Fault Injection Harness (M8): from CE_FAULT_CONFIG unless given; active flags are traced on every request.
        self.faults = faults if faults is not None else FaultHarness.from_env()
        spec = self.faults.patch_spec(spec)
        self.spec, self.sources, self.llm, self.session = spec, sources, llm, session
        # Tool loop (M6): an MCPToolClient to run calls, and a result manager deciding what of each raw
        # result re-enters the window: fn(item, spec) -> (item, retention_decision). Default: raw pass-through.
        self.tool_client = tool_client
        self.result_manager = self.faults.wrap_result_manager(result_manager)
        # ISOLATE (M7): fn(request, session) -> SubAgentResult or None. Its digest joins the knowledge candidates.
        self.isolator = isolator
        self.tracer = tracer or TraceWriter(session.session_id)
        self.fault_flags = sorted(set((fault_flags or []) + self.faults.flags))
        # Extra write-backs run after the history append, e.g. memory extraction (M5) or state updates.
        # Each is called as fn(request, reply, session, tracer).
        self.write_backs = write_backs or []

    def handle(self, text: str) -> Result:
        t0 = time.perf_counter()
        stage_ms: dict[str, float] = {}
        request = Request(request_id=f"REQ-{uuid.uuid4().hex[:10]}", text=text,
                          timestamp=datetime.now().isoformat(timespec="seconds"))
        tr = self.tracer
        tr.start_request(request.request_id, scenario_id=None, spec_version=self.spec.version,
                         dataset_version=dataset_version(), fault_flags=self.fault_flags,
                         persona_id=self.session.persona_id, model=self.llm.model, provider=self.llm.provider,
                         token_counter=counter_name(), budget_total=self.spec.total_tokens,
                         response_reserve=self.spec.response_reserve)

        # 1. gather candidates from every Context Source
        t = time.perf_counter()
        candidates: dict[str, list] = {ch: [] for ch in CHANNELS}
        for source in self.sources:
            items = source.gather(request, self.session, self.spec)
            candidates[source.channel].extend(items)
            tr.emit("candidates", stage="gather", source=source.name, channel=source.channel,
                    items=[it.trace_view() for it in items])
        stage_ms["gather"] = (time.perf_counter() - t) * 1000
        candidates = self.faults.transform_candidates(candidates, request.timestamp)
        isolation = None
        if self.isolator is not None:
            from .isolation import digest_item, isolation_event

            t = time.perf_counter()
            result = self.isolator(request, self.session)
            if result is not None:
                isolation = isolation_event(result)
                item = digest_item(result)
                candidates["knowledge"].append(item)
                tr.emit("candidates", stage="isolate", source="subagent", channel="knowledge", items=[item.trace_view()])
            stage_ms["isolate_subagent"] = (time.perf_counter() - t) * 1000

        # 2. WRITE -> SELECT -> COMPRESS -> ISOLATE
        pool = {ch: list(items) for ch, items in candidates.items()}  # what SELECT chose from (fault seams F2/F5)
        for name, fn in (("write", lambda c: pipeline.write_stage(c, tr)),
                         ("select", lambda c: self.faults.transform_selected(pipeline.select_stage(c, self.spec, tr)[0], pool)),
                         ("compress", lambda c: pipeline.compress_stage(c, self.spec, tr)),
                         ("isolate", lambda c: pipeline.isolate_stage(c, self.spec, tr, isolation))):
            t = time.perf_counter()
            candidates = fn(candidates)
            stage_ms[name] = (time.perf_counter() - t) * 1000

        # 3. render the window
        messages, layout = render(candidates, self.spec)
        window_tokens = sum(ch["tokens"] for ch in layout.values())
        tr.emit("window", order=self.spec.assembly_order, channels=layout, total_tokens=window_tokens,
                budget_total=self.spec.total_tokens - self.spec.response_reserve)

        # 4. call the model, then run the tool loop while it asks for tools
        tool_defs = [it.metadata["tool"] for it in candidates.get("tool_definitions", []) if "tool" in it.metadata]
        t = time.perf_counter()
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self._tool_results = []
        reply = self._call_llm(messages, tool_defs, usage)
        max_iterations = self.spec.selection("tools").get("max_iterations", 3)
        iteration = 0
        while reply.tool_calls and self.tool_client is not None and iteration < max_iterations:
            iteration += 1
            messages.append({"role": "assistant", "content": reply.text,
                             "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in reply.tool_calls]})
            for call in reply.tool_calls:
                messages.append({"role": "tool", "tool_call_id": call.id, "content": self._run_tool(request, call, iteration)})
            reply = self._call_llm(messages, tool_defs, usage)
        stage_ms["llm_and_tools"] = (time.perf_counter() - t) * 1000
        tr.emit("response", text=reply.text, citations=citations(reply.text))

        # 5. write-backs: history append (memory extraction joins in M5)
        history = self.session.history
        if history is not None:
            user_turn = history.append("user", text, request.timestamp, request.request_id)
            bot_turn = history.append("assistant", reply.text, datetime.now().isoformat(timespec="seconds"),
                                      request.request_id)
            tr.emit("stage", stage="write", action="history_append", refs=[user_turn["turn_id"], bot_turn["turn_id"]])
        for write_back in self.write_backs:
            write_back(request, reply, self.session, tr)

        total_ms = (time.perf_counter() - t0) * 1000
        tr.emit("request_end", latency_ms=round(total_ms, 1),
                stage_latency_ms={k: round(v, 1) for k, v in stage_ms.items()},
                tokens_by_channel={ch: info["tokens"] for ch, info in layout.items()},
                window_tokens=window_tokens, prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                cost_ccu=ccu_cost(usage["prompt_tokens"], usage["completion_tokens"],
                                  (self.spec.data.get("economics") or {}).get("ccu_per_1k")))
        window = {ch: [{"item_id": it.item_id, "text": it.content, "source": it.source, "provenance": it.provenance}
                       for it in items] for ch, items in candidates.items() if items}
        return Result(text=reply.text, request_id=request.request_id, trace_path=tr.path, window_tokens=window_tokens,
                      usage=usage, window=window, tool_results=self._tool_results, isolation=isolation)

    def _call_llm(self, messages: list[dict], tool_defs: list[dict], usage: dict):
        reply = self.llm.complete(messages, tools=tool_defs or None)
        for k in usage:
            usage[k] += reply.usage.get(k, 0)
        self.tracer.emit("llm_call", model=reply.model, provider=reply.provider, prompt_tokens=reply.usage["prompt_tokens"],
                         completion_tokens=reply.usage["completion_tokens"], latency_ms=round(reply.latency_ms, 1),
                         finish_reason=reply.finish_reason, tool_calls=[c.__dict__ for c in reply.tool_calls])
        return reply

    def _run_tool(self, request: Request, call, iteration: int) -> str:
        """Call one tool, pass the raw result through the result manager, trace raw vs retained tokens."""
        from .tools_mcp import raw_result_manager

        start = time.perf_counter()
        out = self.tool_client.call(call.name, call.arguments)
        latency = (time.perf_counter() - start) * 1000
        raw = out["result"] if isinstance(out["result"], str) else json.dumps(out["result"], indent=2, ensure_ascii=False)
        item = ContextItem(item_id=f"{request.request_id}-{call.id}", channel="tool_results", content=raw,
                           source=f"mcp:{call.name}", provenance={"type": "tool", "ref": call.name, "args": call.arguments},
                           timestamp=request.timestamp, metadata={"ok": out["ok"], "iteration": iteration})
        kept, retention = (self.result_manager or raw_result_manager)(item, self.spec)
        self.tracer.emit("tool_call", tool=call.name, args=call.arguments, ok=out["ok"], latency_ms=round(latency, 1),
                         item_id=item.item_id, raw_tokens=item.token_count, retained_tokens=kept.token_count,
                         retention=retention, budget=self.spec.budget("tool_results"),
                         over_budget=kept.token_count > self.spec.budget("tool_results"))
        self._tool_results.append({"tool": call.name, "args": call.arguments, "text": kept.content})
        return kept.content
