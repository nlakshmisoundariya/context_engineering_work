"""User input: the current staff request, normalised (whitespace collapsed). Never evicted."""
from __future__ import annotations

from ..context_items import ContextItem


class UserInputSource:
    name = "request"
    channel = "user_input"

    def gather(self, request, session, spec) -> list[ContextItem]:
        text = " ".join(request.text.split())
        return [ContextItem(item_id=f"{request.request_id}-input", channel=self.channel, content=text,
                            source=self.name, provenance={"type": "request", "ref": request.request_id},
                            timestamp=request.timestamp)]
