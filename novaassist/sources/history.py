"""Conversation history: an append-only per-session turn log, and the source that offers it as candidates.

History is its own dimension, distinct from memory and state: the store keeps every turn; which turns enter
the window is a SELECT decision (the history policy you design in M5). This source returns all stored turns,
oldest first, as candidates.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..context_items import ContextItem

KIT_ROOT = Path(__file__).resolve().parents[2]


def session_dir() -> Path:
    return Path(os.environ.get("CE_SESSION_DIR") or KIT_ROOT / "sessions")


class HistoryStore:
    """Turns persisted as JSONL: sessions/<session_id>/history.jsonl."""

    def __init__(self, session_id: str, directory: Path | None = None):
        self.session_id = session_id
        self.path = (directory or session_dir()) / session_id / "history.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def turns(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def append(self, role: str, text: str, timestamp: str, request_id: str | None = None) -> dict:
        turn = {"turn_id": f"{self.session_id}-T{len(self.turns()) + 1:02d}", "role": role, "text": text,
                "timestamp": timestamp, "request_id": request_id}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(turn, ensure_ascii=False) + "\n")
        return turn


class HistorySource:
    name = "history_store"
    channel = "history"

    def gather(self, request, session, spec) -> list[ContextItem]:
        store: HistoryStore = session.history
        return [ContextItem(item_id=t["turn_id"], channel=self.channel, content=t["text"], source=self.name,
                            provenance={"type": "turn", "ref": t["turn_id"], "role": t["role"]},
                            timestamp=t["timestamp"], metadata={"role": t["role"]})
                for t in store.turns()] if store else []
