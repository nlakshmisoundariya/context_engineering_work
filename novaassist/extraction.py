"""Memory extraction scaffold (CAPSTONE_ARCHITECTURE §9 lifecycle: capture -> extract -> store).

After a response, an LLM proposes candidate memories from the turn: facts, preferences, commitments, each with
scope, subject, confidence and the turn it came from. Candidates are *proposals*: ``accept`` keeps only those at
or above a trust threshold that **you** choose and justify (M5). Below-threshold candidates are returned as a
queue, never auto-trusted.

    from novaassist.extraction import extract_candidates, accept
    cands = extract_candidates(llm, turns=[user_turn, assistant_turn], known_subjects={"CUST-0001": "Orbital Foods"})
    stored, queued = accept(cands, threshold=..., store=store, timestamp=user_turn["timestamp"])
"""
from __future__ import annotations

from .memory import MemoryRecord, MemoryStore

EXTRACTION_PROMPT = """You extract durable memories from a support-staff conversation turn for NovaTech's assistant.

Extract only: (a) facts about a customer's environment or plans that are stated as facts, (b) user preferences about
how answers should be given, (c) commitments someone made with a date. Do NOT extract guesses, hunches, rumours or
questions. For each memory give: content (one sentence), scope (session or persistent), subject_type (customer, user
or product), subject_id (use the IDs provided), confidence between 0 and 1 (how clearly the turn states it), and the
source_turn_id it came from. Return an empty list if nothing qualifies.

Known subjects: {subjects}

Turns:
{turns}
"""

CANDIDATE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["memories"],
    "properties": {"memories": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["content", "scope", "subject_type", "subject_id", "confidence", "source_turn_id"],
        "properties": {"content": {"type": "string"}, "scope": {"type": "string", "enum": ["session", "persistent"]},
                       "subject_type": {"type": "string", "enum": ["customer", "user", "product"]},
                       "subject_id": {"type": "string"}, "confidence": {"type": "number"},
                       "source_turn_id": {"type": "string"}}}}},
}


def extract_candidates(llm, turns: list[dict], known_subjects: dict[str, str]) -> list[dict]:
    """Ask the model for candidate memories (structured output). Returns the raw candidate dicts."""
    subjects = ", ".join(f"{k} = {v}" for k, v in sorted(known_subjects.items())) or "none"
    rendered = "\n".join(f"[{t['turn_id']}] {t['role']}: {t['text']}" for t in turns)
    reply = llm.complete([{"role": "user", "content": EXTRACTION_PROMPT.format(subjects=subjects, turns=rendered)}],
                         json_schema=CANDIDATE_SCHEMA, schema_name="memory_candidates")
    candidates = (reply.parsed or {}).get("memories", [])
    turn_ids = {t["turn_id"] for t in turns}
    return [c for c in candidates if c.get("source_turn_id") in turn_ids]  # provenance must point at a real turn


def accept(candidates: list[dict], threshold: float, store: MemoryStore, timestamp: str) -> tuple[list[MemoryRecord], list[dict]]:
    """Store candidates with confidence >= threshold; return (stored records, queued candidates)."""
    if threshold is None or not 0 <= threshold <= 1:
        raise ValueError("choose a trust threshold between 0 and 1 (an M5 design decision) and pass it explicitly")
    stored, queued = [], []
    for c in candidates:
        if c["confidence"] >= threshold:
            rec = MemoryRecord(store.next_id(), c["content"], c["scope"], {"type": c["subject_type"], "id": c["subject_id"]},
                               {"type": "turn", "ref": c["source_turn_id"]}, timestamp, float(c["confidence"]))
            stored.append(store.add(rec))
        else:
            queued.append(c)
    return stored, queued
