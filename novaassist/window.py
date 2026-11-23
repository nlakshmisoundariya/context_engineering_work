"""Render the selected items into chat messages, in the Context Spec's assembly order.

- system items  -> one system message
- history items -> real user/assistant turns
- user input    -> the final user message
- every other channel (state, memory, knowledge, tool definitions, tool results) -> one labelled context
  block per channel, sent as a user message so the model sees it as supplied material, not instructions.
"""
from __future__ import annotations

from .context_items import ContextItem
from .spec import ContextSpec

LABELS = {"state": "Session state", "memory": "Memories", "knowledge": "Retrieved knowledge",
          "tool_definitions": "Available tools", "tool_results": "Tool results"}


def _block(channel: str, items: list[ContextItem]) -> str:
    parts = [f"[{it.item_id}]\n{it.content}" for it in items]
    return f"## {LABELS[channel]}\n\n" + "\n\n".join(parts)


def render(selected: dict[str, list[ContextItem]], spec: ContextSpec) -> tuple[list[dict], dict]:
    """Return (messages, layout). ``layout`` is the window-composition block of the trace."""
    messages: list[dict] = []
    layout: dict = {}
    for position, channel in enumerate(spec.assembly_order):
        items = selected.get(channel, [])
        layout[channel] = {"position": position, "items": [it.item_id for it in items],
                           "tokens": sum(it.token_count for it in items), "budget": spec.budget(channel)}
        if not items:
            continue
        if channel == "system":
            messages.append({"role": "system", "content": "\n\n".join(it.content for it in items)})
        elif channel == "history":
            for it in items:
                role = "assistant" if it.metadata.get("role") == "assistant" else "user"
                messages.append({"role": role, "content": it.content})
        elif channel == "user_input":
            messages.append({"role": "user", "content": "\n\n".join(it.content for it in items)})
        else:
            messages.append({"role": "user", "content": _block(channel, items)})
    return messages, layout
