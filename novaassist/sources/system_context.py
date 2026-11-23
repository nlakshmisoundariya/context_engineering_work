"""System context: the versioned files named in the Context Spec (components.system.files).

Designed in M1 (role, grounding and citation rules, conflict and authority rules, tone), never accreted.
One item per file, in the order the spec lists them.
"""
from __future__ import annotations

from ..context_items import ContextItem


class SystemContextSource:
    name = "system_files"
    channel = "system"

    def gather(self, request, session, spec) -> list[ContextItem]:
        items = []
        for rel in spec.component("system").get("files", []):
            text = spec.resolve(rel).read_text(encoding="utf-8").strip()
            items.append(ContextItem(
                item_id=rel, channel=self.channel, content=text, source=self.name,
                provenance={"type": "file", "ref": rel, "spec_version": spec.version},
                timestamp=spec.today))
        return items
