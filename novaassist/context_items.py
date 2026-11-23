"""The context-item shape every Context Source produces and every pipeline stage consumes.

One shape for all six context dimensions (CAPSTONE_ARCHITECTURE §5):
``{content, source, provenance, timestamp, relevance signal, token count}`` plus a stable ``item_id``,
the window ``channel`` it is destined for, and free-form ``metadata`` (e.g. a chunk's document metadata).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .tokens import count_tokens

# Window channels. The six context dimensions map onto these: tools split into definitions and results,
# memory/state split into memories and the session-state summary (they share the memory budget).
CHANNELS = ("system", "user_input", "history", "knowledge", "tool_definitions", "tool_results", "memory", "state")


class ExclusionReason(str, Enum):
    """Closed set of reasons an item can be excluded from the window (EVALUATION_SPEC §12)."""

    BUDGET = "budget"
    RELEVANCE_FLOOR = "relevance_floor"
    FRESHNESS = "freshness"
    CONFLICT_LOSER = "conflict_loser"
    TOOL_CAP = "tool_cap"
    ISOLATION = "isolation"


@dataclass
class ContextItem:
    item_id: str  # stable ID: "DOC-POL-001::c03", "sess-x-T04", "system/v1/role.md", ...
    channel: str  # one of CHANNELS
    content: str
    source: str  # which Context Source produced it (e.g. "history_store", "system_files")
    provenance: dict  # at least {"type": ..., "ref": ...}: where the content came from
    timestamp: str  # ISO date/time the content was produced or became effective
    relevance: float | None = None  # relevance signal from the producing source (None = not scored)
    token_count: int = -1  # filled from content when not given
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.token_count < 0:
            self.token_count = count_tokens(self.content)

    def trace_view(self) -> dict:
        """What the trace records about an item: identity and accounting, never the full content.

        ``meta`` carries the small evidence fields the §12 trace record needs (retrieval rank/score, memory scope,
        confidence, age, document status) when the producing source set them.
        """
        meta = {k: self.metadata[k] for k in TRACE_META if k in self.metadata}
        view = {"item_id": self.item_id, "channel": self.channel, "source": self.source, "tokens": self.token_count,
                "relevance": self.relevance, "timestamp": self.timestamp, "provenance": self.provenance}
        if meta:
            view["meta"] = meta
        return view


TRACE_META = ("retrieval", "scope", "confidence", "age_days", "status", "injected_fault", "context_cost")


@dataclass
class Exclusion:
    item: ContextItem
    reason: ExclusionReason
    detail: str = ""

    def trace_view(self) -> dict:
        return {"item_id": self.item.item_id, "reason": self.reason.value, "detail": self.detail,
                "tokens": self.item.token_count}
