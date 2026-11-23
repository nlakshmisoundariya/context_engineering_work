"""Context Spec: load and validate the YAML contract for the context window (CAPSTONE_ARCHITECTURE §6).

The spec is the single source of every budget, priority, assembly-order and policy value the pipeline uses.
Nothing in the pipeline hard-codes a budget. Validation collects *all* problems and reports them together.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from .context_items import CHANNELS

COMPONENTS = ("system", "user_input", "history", "knowledge", "tools", "memory")
NEVER_EVICTED = ("system", "user_input")
COMPRESSION_METHODS = {"none", "truncate", "summarize", "structure"}
FRESHNESS_ACTIONS = {"exclude", "flag", "prefer_fresher"}
# Knowledge-section keys added in M2 (optional until you add them; checked when present)
UNKNOWN_DATE_ACTIONS = {"flag", "exclude", "include_with_warning"}
STATUS_ACTIONS = {"include", "flag", "down_rank", "exclude"}
# channel -> (component, budget key inside a dict-valued token_budget, or None)
CHANNEL_BUDGET = {
    "system": ("system", None), "user_input": ("user_input", None), "history": ("history", None),
    "knowledge": ("knowledge", None), "tool_definitions": ("tools", "definitions"),
    "tool_results": ("tools", "results"), "memory": ("memory", None), "state": ("memory", None),
}


class SpecError(ValueError):
    """The Context Spec is invalid. The message lists every problem found."""


@dataclass
class ContextSpec:
    path: Path
    data: dict

    @property
    def version(self) -> str:
        return str(self.data["spec_version"])

    @property
    def total_tokens(self) -> int:
        return self.data["budget"]["total_tokens"]

    @property
    def response_reserve(self) -> int:
        return self.data["budget"]["response_reserve"]

    @property
    def assembly_order(self) -> list[str]:
        return list(self.data["assembly_order"])

    @property
    def evict_order(self) -> list[str]:
        return list(self.data["overflow"]["evict_order"])

    @property
    def today(self) -> str:
        """The agent's 'today' (the dataset reference date T0), never the system clock."""
        return str(self.data["today"])

    def component(self, name: str) -> dict:
        return self.data["components"][name]

    def budget(self, channel: str) -> int:
        """Token budget for a window channel. Memory and state share the memory budget."""
        comp, key = CHANNEL_BUDGET[channel]
        value = self.component(comp)["token_budget"]
        return value[key] if key else value

    def selection(self, component: str) -> dict:
        return self.component(component).get("selection") or {}

    def resolve(self, relative: str) -> Path:
        """Paths in the spec are relative to the spec file's folder."""
        return (self.path.parent / relative).resolve()


def _positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _knowledge_problems(comp: dict, where: str) -> list[str]:
    """The M2 knowledge keys: store, metadata_required, status_rule, freshness.unknown_date."""
    p = []
    if "store" in comp and not (isinstance(comp["store"], str) and comp["store"].strip()):
        p.append(f"{where}.store must be a path to the chunk store (relative to the spec file)")
    required = comp.get("metadata_required")
    if required is not None and not (isinstance(required, list) and all(isinstance(f, str) for f in required)):
        p.append(f"{where}.metadata_required must be a list of field names")
    rule = comp.get("status_rule")
    if rule is not None:
        if not isinstance(rule, dict):
            p.append(f"{where}.status_rule must map a document status to one of {sorted(STATUS_ACTIONS)}")
        else:
            p += [f"{where}.status_rule.{k}: {v!r} not in {sorted(STATUS_ACTIONS)}"
                  for k, v in rule.items() if v not in STATUS_ACTIONS]
    unknown = (comp.get("freshness") or {}).get("unknown_date")
    if unknown is not None and unknown not in UNKNOWN_DATE_ACTIONS:
        p.append(f"{where}.freshness.unknown_date {unknown!r} not in {sorted(UNKNOWN_DATE_ACTIONS)}")
    return p


def validate(data: dict, path: Path) -> list[str]:
    p = []
    if not isinstance(data, dict):
        return ["the spec must be a YAML mapping"]
    for key in ("spec_version", "today", "budget", "assembly_order", "overflow", "components"):
        if key not in data:
            p.append(f"missing top-level key '{key}'")
    if p:
        return p
    try:
        date.fromisoformat(str(data["today"]))
    except ValueError:
        p.append(f"today must be an ISO date (the dataset reference date), got {data['today']!r}")
    budget = data["budget"] or {}
    for key in ("total_tokens", "response_reserve"):
        if not _positive_int(budget.get(key)):
            p.append(f"budget.{key} must be a positive integer (got {budget.get(key)!r})")
    components = data["components"] or {}
    for name in COMPONENTS:
        if name not in components:
            p.append(f"components.{name} is missing (all six context components must be declared)")
    for name in components:
        if name not in COMPONENTS:
            p.append(f"components.{name} is not a known component {COMPONENTS}")
    channel_total = 0
    for name, comp in components.items():
        if name not in COMPONENTS:
            continue
        where = f"components.{name}"
        if not isinstance(comp, dict):
            p.append(f"{where} must be a mapping")
            continue
        for key in ("source", "priority", "token_budget"):
            if key not in comp:
                p.append(f"{where}.{key} is missing")
        tb = comp.get("token_budget")
        if name == "tools":
            if not isinstance(tb, dict) or not all(_positive_int(tb.get(k)) for k in ("definitions", "results")):
                p.append(f"{where}.token_budget must be {{definitions: <int>, results: <int>}} (got {tb!r})")
            else:
                channel_total += tb["definitions"] + tb["results"]
        elif "token_budget" in comp:
            if not _positive_int(tb):
                p.append(f"{where}.token_budget must be a positive integer (got {tb!r})")
            else:
                channel_total += tb
        if "priority" in comp and not (isinstance(comp["priority"], int) and comp["priority"] >= 0):
            p.append(f"{where}.priority must be a non-negative integer")
        method = (comp.get("compression") or {}).get("method", "none")
        if method not in COMPRESSION_METHODS:
            p.append(f"{where}.compression.method {method!r} not in {sorted(COMPRESSION_METHODS)}")
        fresh = comp.get("freshness")
        if fresh and fresh.get("on_violation", "flag") not in FRESHNESS_ACTIONS:
            p.append(f"{where}.freshness.on_violation must be one of {sorted(FRESHNESS_ACTIONS)}")
        if name == "knowledge":
            p.extend(_knowledge_problems(comp, where))
        for f in comp.get("files", []) or []:
            if not (path.parent / f).is_file():
                p.append(f"{where}.files: {f} not found next to the spec")
    if _positive_int(budget.get("total_tokens")) and _positive_int(budget.get("response_reserve")):
        available = budget["total_tokens"] - budget["response_reserve"]
        if channel_total > available:
            p.append(f"component budgets add up to {channel_total} tokens, more than total_tokens - response_reserve "
                     f"= {available}")
    order = data["assembly_order"] or []
    if sorted(order) != sorted(CHANNELS):
        p.append(f"assembly_order must list each window channel exactly once: {list(CHANNELS)} (got {order})")
    evict = (data["overflow"] or {}).get("evict_order", [])
    for ch in evict:
        if ch in NEVER_EVICTED:
            p.append(f"overflow.evict_order must not contain {ch!r}: system and user input are never evicted")
        elif ch not in CHANNELS:
            p.append(f"overflow.evict_order: unknown channel {ch!r}")
    return p


def load_spec(path: str | Path) -> ContextSpec:
    path = Path(path)
    if not path.is_file():
        raise SpecError(f"Context Spec not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SpecError(f"{path.name} is not valid YAML: {exc}") from exc
    problems = validate(data, path)
    if problems:
        raise SpecError(f"{path.name} has {len(problems)} problem(s):\n  - " + "\n  - ".join(problems))
    return ContextSpec(path=path.resolve(), data=data)
