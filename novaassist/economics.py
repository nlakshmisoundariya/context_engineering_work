"""Cost accounting in Course Cost Units (CCU) — a provider-neutral unit for *relative* comparisons
(EVALUATION_SPEC §2 Layer D). It does not simulate real pricing. Rates are provisional until the
instructor dry runs calibrate them; a Context Spec may override them under ``economics.ccu_per_1k``.
"""
from __future__ import annotations

DEFAULT_CCU_PER_1K = {"prompt": 1.0, "completion": 4.0}


def ccu_cost(prompt_tokens: int, completion_tokens: int, rates: dict | None = None) -> float:
    r = {**DEFAULT_CCU_PER_1K, **(rates or {})}
    return round(prompt_tokens / 1000 * r["prompt"] + completion_tokens / 1000 * r["completion"], 4)
