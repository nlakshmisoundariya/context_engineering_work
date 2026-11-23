"""The common Context Source contract (architecture invariant, CAPSTONE_ARCHITECTURE §5).

    given (request, session, spec) -> return candidate context items

Every context-producing component — system files, user input, history, retrievers and the selection stack,
memory retriever, tool selector and result manager — implements ``ContextSource``. Each module's capstone
checkpoint runs ``check_conformance`` on the components built in that module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from .context_items import CHANNELS, ContextItem
from .tokens import count_tokens


@dataclass
class Request:
    request_id: str
    text: str
    timestamp: str  # wall-clock ISO time the request arrived


@dataclass
class Session:
    session_id: str
    persona_id: str | None = None
    state: dict = field(default_factory=dict)  # maintained by the orchestrator: active_customer, open_ticket, task_phase...
    history: object | None = None  # a HistoryStore


@runtime_checkable
class ContextSource(Protocol):
    name: str
    channel: str  # the window channel this source feeds (one of CHANNELS)

    def gather(self, request: Request, session: Session, spec) -> list[ContextItem]:
        """Return candidate items for this request. Selection, compression and isolation happen later."""
        ...


def _is_iso(value: str) -> bool:
    try:
        datetime.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def check_conformance(source, request: Request, session: Session, spec) -> list[str]:
    """Return a list of problems (empty = conforms). Used by tests and the module checkpoints."""
    problems = []
    for attr in ("name", "channel"):
        if not isinstance(getattr(source, attr, None), str):
            problems.append(f"source has no string attribute {attr!r}")
    if getattr(source, "channel", None) not in CHANNELS:
        problems.append(f"channel {getattr(source, 'channel', None)!r} is not one of {CHANNELS}")
    if not callable(getattr(source, "gather", None)):
        return problems + ["source has no gather(request, session, spec) method"]
    try:
        items = source.gather(request, session, spec)
    except Exception as exc:  # noqa: BLE001 — report, do not crash the checkpoint
        return problems + [f"gather() raised {type(exc).__name__}: {exc}"]
    if not isinstance(items, list):
        return problems + [f"gather() must return a list, got {type(items).__name__}"]
    seen = set()
    for i, item in enumerate(items):
        where = f"item[{i}]"
        if not isinstance(item, ContextItem):
            problems.append(f"{where} is {type(item).__name__}, not ContextItem")
            continue
        where = f"item {item.item_id!r}"
        if item.item_id in seen:
            problems.append(f"{where}: duplicate item_id")
        seen.add(item.item_id)
        if item.channel != source.channel:
            problems.append(f"{where}: channel {item.channel!r} differs from the source's {source.channel!r}")
        if not isinstance(item.provenance, dict) or not item.provenance.get("type") or not item.provenance.get("ref"):
            problems.append(f"{where}: provenance must include 'type' and 'ref'")
        if not _is_iso(item.timestamp):
            problems.append(f"{where}: timestamp {item.timestamp!r} is not ISO format")
        if item.token_count != count_tokens(item.content):
            problems.append(f"{where}: token_count {item.token_count} != counted {count_tokens(item.content)}")
        if item.relevance is not None and not isinstance(item.relevance, (int, float)):
            problems.append(f"{where}: relevance must be a number or None")
        if not item.source:
            problems.append(f"{where}: source is empty")
    return problems


def assert_conforms(source, request: Request, session: Session, spec) -> None:
    problems = check_conformance(source, request, session, spec)
    if problems:
        raise AssertionError(f"{getattr(source, 'name', source)} does not conform to the Context Source contract:\n  "
                             + "\n  ".join(problems))
