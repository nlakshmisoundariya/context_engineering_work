"""Raw trace-event emission (CAPSTONE_ARCHITECTURE §12; field names from EVALUATION_SPEC §12).

One JSON object per line in ``traces/<session_id>.jsonl``. Every event carries
``event, request_id, session_id, seq, ts``. Read a request's events with:

    jq -c 'select(.request_id == "REQ-...")' traces/<session>.jsonl

Event types are documented in docs/interfaces/trace_events.md. Items are referenced by ID with token counts;
full content lives in the stores, so traces stay readable. Module 9 assembles these raw events into one
trace record per request.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parents[1]


def trace_dir() -> Path:
    return Path(os.environ.get("CE_TRACE_DIR") or KIT_ROOT / "traces")


class TraceWriter:
    """Appends raw events for one session to a JSONL file."""

    def __init__(self, session_id: str, directory: Path | None = None):
        self.session_id = session_id
        self.path = (directory or trace_dir()) / f"{session_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self.request_id: str | None = None

    def emit(self, event: str, **fields) -> dict:
        self._seq += 1
        record = {"event": event, "request_id": self.request_id, "session_id": self.session_id, "seq": self._seq,
                  "ts": datetime.now().isoformat(timespec="milliseconds"), **fields}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return record

    def start_request(self, request_id: str, **identity) -> dict:
        self.request_id = request_id
        return self.emit("request_start", **identity)


def read_events(path: Path, request_id: str | None = None) -> list[dict]:
    events = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return [e for e in events if request_id is None or e["request_id"] == request_id]
