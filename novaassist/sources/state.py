"""Session state: a small structured object the orchestrator maintains (never LLM-extracted).

Injected as one compact summary item (active customer, open ticket, task phase, ...). State is *maintained*;
memory is *remembered* — a taught design boundary (CAPSTONE_ARCHITECTURE §9).
"""
from __future__ import annotations

from ..context_items import ContextItem


class StateSource:
    name = "session_state"
    channel = "state"

    def gather(self, request, session, spec) -> list[ContextItem]:
        if not session.state:
            return []
        summary = "; ".join(f"{k}: {v}" for k, v in sorted(session.state.items()))
        return [ContextItem(item_id=f"{session.session_id}-state", channel=self.channel,
                            content=f"Session state — {summary}", source=self.name,
                            provenance={"type": "session_state", "ref": session.session_id},
                            timestamp=request.timestamp)]
