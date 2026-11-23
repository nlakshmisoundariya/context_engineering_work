"""Memory store (CAPSTONE_ARCHITECTURE §9): one local SQLite store of memory records with scope and lifecycle.

Record shape (mandatory fields marked *):
    memory_id*, content*, scope* (working|session|persistent), subject* {type: customer|user|product, id},
    provenance* {type: turn|ticket_event|document|tool, ref}, timestamp*, confidence* (0..1),
    status* (active|superseded|expired), supersedes (memory_id of the record this one replaced)

Provided: storage, seed loading, lookups, supersede/expire (never delete), and turning records into context items
with visible age and provenance. **Your design (M5):** which memories to retrieve (scope, relevance, freshness,
confidence), how conflicts are resolved, and when extraction output is trusted.

    store = MemoryStore("stores/memory.db")
    store.load_seed("data/novatech/memories/seed_memories.jsonl")
    store.load_seed("data/novatech/memories/preferences.jsonl")
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

from .context_items import ContextItem

SCOPES = ("working", "session", "persistent")
STATUSES = ("active", "superseded", "expired")
SUBJECT_TYPES = ("customer", "user", "product")
PROVENANCE_TYPES = ("turn", "ticket_event", "document", "tool")


class MemoryError(ValueError):
    """A memory record breaks the shape or lifecycle rules."""


@dataclass
class MemoryRecord:
    memory_id: str
    content: str
    scope: str
    subject: dict
    provenance: dict
    timestamp: str
    confidence: float
    status: str = "active"
    supersedes: str | None = None

    def validate(self) -> None:
        problems = []
        if not self.content.strip():
            problems.append("content is empty")
        if self.scope not in SCOPES:
            problems.append(f"scope must be one of {SCOPES}")
        if self.status not in STATUSES:
            problems.append(f"status must be one of {STATUSES}")
        if (self.subject or {}).get("type") not in SUBJECT_TYPES or not (self.subject or {}).get("id"):
            problems.append(f"subject needs a type in {SUBJECT_TYPES} and an id")
        if (self.provenance or {}).get("type") not in PROVENANCE_TYPES or not (self.provenance or {}).get("ref"):
            problems.append(f"provenance is mandatory: type in {PROVENANCE_TYPES} and a ref")
        try:
            datetime.fromisoformat(self.timestamp)
        except (TypeError, ValueError):
            problems.append("timestamp is mandatory and must be ISO format")
        if not isinstance(self.confidence, (int, float)) or not 0 <= self.confidence <= 1:
            problems.append("confidence must be between 0 and 1")
        if problems:
            raise MemoryError(f"{self.memory_id}: " + "; ".join(problems))


SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY, content TEXT NOT NULL, scope TEXT NOT NULL,
    subject_type TEXT NOT NULL, subject_id TEXT NOT NULL, provenance_type TEXT NOT NULL, provenance_ref TEXT NOT NULL,
    timestamp TEXT NOT NULL, confidence REAL NOT NULL, status TEXT NOT NULL, supersedes TEXT
);
CREATE INDEX IF NOT EXISTS idx_memories_subject ON memories (subject_type, subject_id, status);
"""


class MemoryStore:
    def __init__(self, path: str | Path = "stores/memory.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    @staticmethod
    def _row(r) -> MemoryRecord:
        return MemoryRecord(r[0], r[1], r[2], {"type": r[3], "id": r[4]}, {"type": r[5], "ref": r[6]}, r[7], r[8], r[9], r[10])

    def add(self, record: MemoryRecord) -> MemoryRecord:
        record.validate()
        if record.supersedes and not self.get(record.supersedes):
            raise MemoryError(f"{record.memory_id}: supersedes unknown memory {record.supersedes}")
        self.db.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (record.memory_id, record.content, record.scope, record.subject["type"], record.subject["id"],
                         record.provenance["type"], record.provenance["ref"], record.timestamp, float(record.confidence),
                         record.status, record.supersedes))
        self.db.commit()
        return record

    def next_id(self, prefix: str = "MEM-L") -> str:
        """IDs for memories you write at runtime; seed memories keep their dataset IDs (MEM-####)."""
        n = self.db.execute("SELECT COUNT(*) FROM memories WHERE memory_id LIKE ?", (prefix + "%",)).fetchone()[0]
        return f"{prefix}{n + 1:04d}"

    def get(self, memory_id: str) -> MemoryRecord | None:
        r = self.db.execute("SELECT * FROM memories WHERE memory_id = ?", (memory_id,)).fetchone()
        return self._row(r) if r else None

    def find(self, subject_id: str | None = None, scope: str | None = None, status: str | None = "active") -> list[MemoryRecord]:
        sql, args = "SELECT * FROM memories WHERE 1=1", []
        for col, val in (("subject_id", subject_id), ("scope", scope), ("status", status)):
            if val is not None:
                sql += f" AND {col} = ?"
                args.append(val)
        return [self._row(r) for r in self.db.execute(sql + " ORDER BY timestamp, memory_id", args)]

    def supersede(self, loser_id: str, winner_id: str) -> None:
        """Mark the loser superseded and link the winner to it. Nothing is ever deleted."""
        if not self.get(loser_id) or not self.get(winner_id):
            raise MemoryError(f"cannot supersede {loser_id} with {winner_id}: unknown memory")
        self.db.execute("UPDATE memories SET status = 'superseded' WHERE memory_id = ?", (loser_id,))
        self.db.execute("UPDATE memories SET supersedes = ? WHERE memory_id = ?", (loser_id, winner_id))
        self.db.commit()

    def expire(self, memory_id: str) -> None:
        self.db.execute("UPDATE memories SET status = 'expired' WHERE memory_id = ?", (memory_id,))
        self.db.commit()

    def load_seed(self, path: str | Path) -> int:
        """Load JSONL memory records once (records already present are skipped)."""
        n = 0
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = MemoryRecord(**json.loads(line))
                if not self.get(rec.memory_id):
                    supersedes, rec.supersedes = rec.supersedes, None  # link after both sides exist
                    self.add(rec)
                    if supersedes:
                        self.db.execute("UPDATE memories SET supersedes = ? WHERE memory_id = ?", (supersedes, rec.memory_id))
                    n += 1
        self.db.commit()
        return n

    def export(self) -> list[dict]:
        return [asdict(m) for m in self.find(status=None)]


def age_days(record: MemoryRecord, today: str) -> int:
    return (date.fromisoformat(today) - date.fromisoformat(record.timestamp[:10])).days


def to_context_item(record: MemoryRecord, today: str, relevance: float | None = None, source: str = "memory_store") -> ContextItem:
    """A memory as a context item; age and provenance are visible in the text the model sees."""
    age = age_days(record, today)
    header = (f"[{record.memory_id} | {record.scope} | {age} days old | from {record.provenance['type']} "
              f"{record.provenance['ref']} | confidence {record.confidence}]")
    return ContextItem(item_id=record.memory_id, channel="memory", content=f"{header} {record.content}", source=source,
                       provenance={"type": "memory", "ref": record.memory_id, "origin": record.provenance},
                       timestamp=record.timestamp, relevance=relevance,
                       metadata={"scope": record.scope, "age_days": age, "confidence": record.confidence,
                                 "status": record.status, "subject": record.subject})


class MemorySource:
    """Starter memory source: every *active* memory about the session's active customer, plus the persona's
    preferences, as candidates. Ranking, freshness, confidence floors and conflict handling are yours (M5)."""

    name = "memory_store"
    channel = "memory"

    def __init__(self, store: MemoryStore):
        self.store = store

    def gather(self, request, session, spec) -> list[ContextItem]:
        today = spec.today if spec else request.timestamp[:10]
        out = []
        customer = (session.state or {}).get("active_customer")
        if customer:
            out += [to_context_item(m, today) for m in self.store.find(subject_id=customer)]
        if session.persona_id:
            out += [to_context_item(m, today) for m in self.store.find(subject_id=session.persona_id)]
        return out
