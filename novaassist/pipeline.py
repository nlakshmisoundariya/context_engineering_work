"""The context assembly pipeline: WRITE -> SELECT -> COMPRESS -> ISOLATE (CAPSTONE_ARCHITECTURE §7).

Starter versions. Every stage consumes and produces items grouped by channel, and emits trace events.
- write:    passes candidates through and records what was captured.             (extended in M5-M7)
- select:   a naive per-channel budget cut, the only enforcement the starter has. (your policy in M4-M7)
- compress: no compression yet; records "method: none" with before/after tokens.  (M7)
- isolate:  no sub-agent boundary yet; records that none was invoked.             (M7)

Every budget comes from the Context Spec. System and user input are never evicted; if they exceed their
budget the select event records a budget violation instead.
"""
from __future__ import annotations

from .context_items import CHANNELS, ContextItem, Exclusion, ExclusionReason
from .spec import CHANNEL_BUDGET, NEVER_EVICTED, ContextSpec

Grouped = dict[str, list[ContextItem]]


def write_stage(candidates: Grouped, tracer) -> Grouped:
    tracer.emit("stage", stage="write", action="capture_candidates",
                counts={ch: len(items) for ch, items in candidates.items()})
    return candidates


def _preference_order(channel: str, items: list[ContextItem]) -> list[ContextItem]:
    """The order in which the naive budget cut admits items."""
    if channel == "history":
        return list(reversed(items))  # newest turns first
    return list(items)  # as the source ranked them


def select_stage(candidates: Grouped, spec: ContextSpec, tracer) -> tuple[Grouped, dict[str, list[Exclusion]]]:
    selected: Grouped = {}
    excluded: dict[str, list[Exclusion]] = {}
    pool_used: dict[tuple, int] = {}
    recent_turns = spec.selection("history").get("recent_turns")
    for channel in CHANNELS:
        items = candidates.get(channel, [])
        pool = CHANNEL_BUDGET[channel]
        budget = spec.budget(channel)
        used = pool_used.get(pool, 0)
        keep, drop, violation = [], [], False
        for rank, item in enumerate(_preference_order(channel, items)):
            if channel in NEVER_EVICTED:
                keep.append(item)
                used += item.token_count
                violation = violation or used > budget
            elif channel == "history" and recent_turns is not None and rank >= recent_turns:
                drop.append(Exclusion(item, ExclusionReason.BUDGET, f"outside the recent window of {recent_turns} turns"))
            elif used + item.token_count <= budget:
                keep.append(item)
                used += item.token_count
            else:
                drop.append(Exclusion(item, ExclusionReason.BUDGET, f"would exceed the {channel} budget of {budget}"))
        pool_used[pool] = used
        order = {id(it): i for i, it in enumerate(items)}
        keep.sort(key=lambda it: order[id(it)])  # restore source order (history back to chronological)
        selected[channel], excluded[channel] = keep, drop
        if items:
            tracer.emit("select", stage="select", channel=channel, budget=budget,
                        candidates=[it.trace_view() for it in items],
                        selected=[it.item_id for it in keep],
                        excluded=[e.trace_view() for e in drop],
                        tokens_selected=sum(it.token_count for it in keep),
                        budget_violation=violation)
    return selected, excluded


def compress_stage(selected: Grouped, spec: ContextSpec, tracer) -> Grouped:
    for channel, items in selected.items():
        if not items:
            continue
        component = CHANNEL_BUDGET[channel][0]
        method = (spec.component(component).get("compression") or {}).get("method", "none")
        tokens = sum(it.token_count for it in items)
        tracer.emit("compress", stage="compress", channel=channel, trigger=None, method=method, applied=False,
                    tokens_before=tokens, tokens_after=tokens, original_ref=None)
    return selected


def isolate_stage(selected: Grouped, spec: ContextSpec, tracer, event: dict | None = None) -> Grouped:
    """Records whether a sub-agent boundary ran for this request (the orchestrator's isolator decides; M7)."""
    tracer.emit("isolate", stage="isolate", **(event or {"boundary": None, "invoked": False, "inbound_brief_ref": None,
                                                         "digest_ref": None, "sub_trace_id": None}))
    return selected
