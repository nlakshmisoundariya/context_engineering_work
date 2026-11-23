"""Module 9 trace-record assembly support.

NovaAssist has emitted raw events since S1 (docs: trace_events.md). In M9 you assemble them into one record per
request that matches ``data/novatech/eval/schemas/trace_record.schema.json`` (EVALUATION_SPEC §12).

Provided: ``load_events``, ``group_by_request``, ``validate_record`` (schema check with readable messages),
``FIELD_MAP`` (which raw events feed which record block). **Your part:** ``assemble_record``.

    from tools.traces import load_events, group_by_request, validate_record
    for request_id, events in group_by_request(load_events("traces/sess-....jsonl")).items():
        record = assemble_record(events)
        problems = validate_record(record)
"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

SCHEMA_PATH = Path("data/novatech/eval/schemas/trace_record.schema.json")

FIELD_MAP = {
    "identity": "request_start (request_id, session_id, ts->timestamp, spec_version, dataset_version, fault_flags, scenario_id)",
    "window": "window.channels (items, tokens, budget, position per channel)",
    "selection": "select events: candidates[].item_id, selected[], excluded[{item_id, reason}] per channel",
    "retrieval": "candidates items of channel knowledge with meta.retrieval {retriever, rank, score, query}; in-window = window.channels.knowledge.items",
    "memory": "memory-channel candidates meta {scope, confidence, age_days}; select excluded for memory; your resolver's 'memory' events for conflicts",
    "tools": "window.channels.tool_definitions.items; tool_call events (tool, args, latency_ms, raw_tokens, retained_tokens, retention)",
    "compression": "compress events where applied or method != none",
    "isolation": "isolate events where invoked",
    "economics": "request_end (latency_ms, stage_latency_ms, prompt_tokens, completion_tokens, tokens_by_channel, cost_ccu)",
    "response": "response (text, citations)",
}


def load_events(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def group_by_request(events: list[dict]) -> "OrderedDict[str, list[dict]]":
    """Events per request_id, in emission order (sub-agent traces live in their own files)."""
    grouped: OrderedDict[str, list[dict]] = OrderedDict()
    for e in sorted(events, key=lambda e: e["seq"]):
        if e.get("request_id"):
            grouped.setdefault(e["request_id"], []).append(e)
    return grouped


def validate_record(record: dict, schema_path: str | Path = SCHEMA_PATH) -> list[str]:
    """Readable schema problems (empty list = valid)."""
    import jsonschema

    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '(record)'}: {e.message}"
            for e in jsonschema.Draft202012Validator(schema).iter_errors(record)]


def assemble_record(events: list[dict]) -> dict:
    """M9 lab: build one §12 trace record from a request's raw events (see FIELD_MAP)."""
    raise NotImplementedError("M9 lab: assemble the trace record from raw events (see tools.traces.FIELD_MAP)")


def write_records(records: list[dict], path: str | Path) -> Path:
    """One assembled record per line (JSONL), readable with jq."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    return path
