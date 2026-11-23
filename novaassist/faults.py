"""Fault Injection Harness (CAPSTONE_ARCHITECTURE §13): faults F1-F9 at defined seams, toggled by configuration.

The trainer activates faults by pointing CE_FAULT_CONFIG at a YAML file:

    flags: [F2, F5]
    params: {F8: {budget_factor: 0.4}}

Faults are **never silent**: the active flags are written into every trace's identity block (``fault_flags``) and the
CLI prints a banner. Without CE_FAULT_CONFIG nothing changes. Seams:

  F1 history -> pipeline   windowing and the history budget are lifted, so low-value turns accumulate
  F2 assembly ordering     the most relevant knowledge items are buried mid-window, padded with low-relevance ones
  F3 chunk store           superseded documents are labelled current and effective dates are removed
  F4 memory store          plausible-but-wrong memories (from the config) are injected with high confidence
  F5 tool layer            every tool definition enters the window; results bypass the result manager
  F6 retrieval stack       worst single retriever, no reranking, metadata filters dropped
  F7 knowledge + tools     legacy order fields are surfaced next to the policy, unflagged
  F8 spec budgets          the total window and every component budget shrink by a factor
  F9 extractor             the memory trust threshold drops so speculative memories are stored
"""
from __future__ import annotations

import copy
import json
import os
from dataclasses import replace
from pathlib import Path

import yaml

from .context_items import ContextItem

ALL_FLAGS = [f"F{n}" for n in range(1, 10)]


class FaultHarness:
    def __init__(self, flags: list[str] | None = None, params: dict | None = None, source: str | None = None):
        unknown = [f for f in flags or [] if f not in ALL_FLAGS]
        if unknown:
            raise ValueError(f"unknown fault flags {unknown}; use {ALL_FLAGS}")
        self.flags = sorted(set(flags or []), key=lambda f: int(f[1:]))
        self.params = params or {}
        self.source = source

    @classmethod
    def from_env(cls) -> "FaultHarness":
        path = os.environ.get("CE_FAULT_CONFIG", "").strip()
        if not path:
            return cls()
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(data.get("flags", []), data.get("params", {}), source=Path(path).name)

    def on(self, flag: str) -> bool:
        return flag in self.flags

    def p(self, flag: str, key: str, default):
        return (self.params.get(flag) or {}).get(key, default)

    def banner(self) -> str:
        return f"FAULT INJECTION ACTIVE: {', '.join(self.flags)} (config {self.source})" if self.flags else ""

    # ------------------------------------------------------------------ F1, F8: the Context Spec
    def patch_spec(self, spec):
        if not (self.on("F1") or self.on("F2") or self.on("F8")):
            return spec
        patched = copy.deepcopy(spec)
        comps = patched.data["components"]
        if self.on("F2"):
            patched.data["assembly_order"] = self.assembly_order(list(patched.data["assembly_order"]))
        if self.on("F1"):
            comps["history"]["selection"] = {**(comps["history"].get("selection") or {}), "recent_turns": None}
            comps["history"]["token_budget"] = self.p("F1", "history_budget", 100000)
            comps["history"]["compression"] = {"method": "none", "trigger": None}
        if self.on("F8"):
            factor = self.p("F8", "budget_factor", 0.4)
            patched.data["budget"]["total_tokens"] = int(patched.data["budget"]["total_tokens"] * factor)
            patched.data["budget"]["response_reserve"] = int(patched.data["budget"]["response_reserve"] * factor)
            for name, comp in comps.items():
                tb = comp["token_budget"]
                comp["token_budget"] = {k: max(1, int(v * factor)) for k, v in tb.items()} if isinstance(tb, dict) \
                    else max(1, int(tb * factor))
        return patched

    # ------------------------------------------------------------------ F3, F4: candidates after gather
    def transform_candidates(self, candidates: dict, request_timestamp: str) -> dict:
        if self.on("F3"):
            for item in candidates.get("knowledge", []):
                if item.metadata.get("status") == "superseded":
                    item.metadata["status"] = "current"
                    item.provenance["status"] = "current"
                item.metadata.pop("effective_date", None)
        if self.on("F4"):
            for m in self.p("F4", "memories", []):
                candidates.setdefault("memory", []).append(ContextItem(
                    item_id=m["memory_id"], channel="memory", source="memory_store",
                    content=f"[{m['memory_id']} | persistent | from {m.get('provenance', 'turn')} | confidence {m.get('confidence', 0.95)}] {m['content']}",
                    provenance={"type": "memory", "ref": m["memory_id"]}, timestamp=m.get("timestamp", request_timestamp),
                    relevance=0.99, metadata={"confidence": m.get("confidence", 0.95), "scope": "persistent", "injected_fault": "F4"}))
        return candidates

    # ------------------------------------------------------------------ F2, F5: the selected window
    def transform_selected(self, selected: dict, candidates: dict) -> dict:
        if self.on("F5"):
            selected["tool_definitions"] = list(candidates.get("tool_definitions", []))
        if self.on("F2"):
            items = list(selected.get("knowledge", []))
            padding = [it for it in candidates.get("knowledge", []) if it not in items][: self.p("F2", "padding_items", 6)]
            critical = items[: self.p("F2", "critical_items", 2)]
            rest = items[len(critical):] + padding
            half = len(rest) // 2
            selected["knowledge"] = rest[:half] + critical + rest[half:]
        return selected

    def assembly_order(self, order: list[str]) -> list[str]:
        """F2 also moves the knowledge block into the middle of the window."""
        if not self.on("F2"):
            return order
        others = [ch for ch in order if ch != "knowledge"]
        return others[: len(others) // 2] + ["knowledge"] + others[len(others) // 2:]

    # ------------------------------------------------------------------ F5, F7: tool results
    def wrap_result_manager(self, manager):
        if not (self.on("F5") or self.on("F7")):
            return manager

        def faulty(item, spec):
            if self.on("F7") and "refund_eligible_until" in item.content:
                try:
                    data = json.loads(item.content)
                    for order in data.get("orders", []):
                        if order.get("refund_eligible_until"):
                            order["refund_status"] = f"ELIGIBLE FOR REFUND until {order['refund_eligible_until']}"
                    item = replace(item, content=json.dumps(data, indent=2), token_count=-1)
                except json.JSONDecodeError:
                    pass
            if self.on("F5"):
                return item, "raw (F5: result manager disabled)"
            return manager(item, spec) if manager else (item, "raw")
        return faulty

    # ------------------------------------------------------------------ F6: retrieval stack
    def stack_config(self, config):
        if not self.on("F6"):
            return config
        return replace(config, retriever=self.p("F6", "retriever", "sparse"), rerank=False, mmr=False, rewrite=False, hyde=False)

    def drop_filters(self, filters: dict) -> dict:
        return {} if self.on("F6") else filters

    # ------------------------------------------------------------------ F9: extraction
    def extraction_threshold(self, threshold: float) -> float:
        return self.p("F9", "threshold", 0.0) if self.on("F9") else threshold
