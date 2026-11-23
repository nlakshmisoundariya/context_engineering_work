"""Layer A assertions (EVALUATION_SPEC §2): the deterministic, executable vocabulary behind the six families.

    FACT            fact_present, fact_absent
    EVIDENCE        evidence_in_window, evidence_excluded, citation_valid
    TOOL            tool_called, tool_not_called, call_count_within
    MEMORY/STATE    memory_injected, memory_excluded, memory_written, memory_superseded, state_matches
    CONTEXT/POLICY  conflict_disclosed, channel_active, channel_silent
    BUDGET/BOUNDARY budget_respected, digest_conforms

``evaluate(scenario, run)`` runs every assertion listed in a golden scenario. ``run`` bundles what the agent did:
``RunEvidence(response, window, tool_results, record, state, memory_store, isolation, manifests)``.
Assertions anchor on IDs, structured trace fields and stores, never on free-text judgement; semantic facts are
reported as "judge" and never gate.

Evidence that is a document chunk is matched independently of your chunking: an in-window item from the same
document that contains the scenario's anchor phrase (``evidence_anchors``). Record evidence (ENT-, ORD-, TCK-,
MEM-, KI-/SA-, turns) counts as in-window when an in-window item or tool result carries that ID.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

DISCLOSURE_MARKERS = ("conflict", "disagree", "differ", "however", "but the", "inconsistent", "contradict", "whereas",
                      "although", "overrides", "takes precedence", "governs")


@dataclass
class RunEvidence:
    response: str
    window: dict  # channel -> [{item_id, text, source, provenance}] (Result.window)
    tool_results: list = field(default_factory=list)  # [{tool, args, text}]
    record: dict | None = None  # assembled §12 trace record
    state: dict = field(default_factory=dict)  # session state after the run
    memory_store: object | None = None  # novaassist.memory.MemoryStore
    isolation: dict | None = None
    manifests: dict = field(default_factory=dict)  # memory-manifest target_id -> target


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _items(run: RunEvidence, channels=None) -> list[dict]:
    return [it for ch, items in run.window.items() if channels is None or ch in channels for it in items]


def _doc_of(item: dict) -> str | None:
    m = re.match(r"(DOC-[A-Z]{2,3}-\d{3})", item["item_id"])
    return m.group(1) if m else (item.get("provenance") or {}).get("ref")


def _evidence_present(ref: str, scenario: dict, run: RunEvidence) -> bool:
    anchor = scenario.get("evidence_anchors", {}).get(ref)
    if anchor:
        same_doc = [it for it in _items(run) if _doc_of(it) == anchor["doc_id"]]
        if anchor.get("all"):
            return bool(same_doc)
        phrase = _norm(anchor["phrase"])
        return any(phrase in _norm(it["text"]) for it in same_doc)
    if any(it["item_id"] == ref for it in _items(run)):
        return True
    texts = [it["text"] for it in _items(run)] + [t["text"] for t in run.tool_results]
    return any(ref in t for t in texts)


def _fact(scenario: dict, fid: str, key: str) -> dict:
    return next(f for f in scenario[key] if f["fact_id"] == fid)


def _cited_docs(response: str) -> set[str]:
    return set(re.findall(r"DOC-[A-Z]{2,3}-\d{3}", response))


def _get(state: dict, dotted: str):
    cur = state
    for part in dotted.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def check(assertion: str, scenario: dict, run: RunEvidence) -> tuple[bool | None, str]:
    """(passed, detail). passed is None for non-gating checks that need a judge."""
    name, _, arg = assertion.partition(":")
    resp = _norm(run.response)
    if name in ("fact_present", "fact_absent"):
        fact = _fact(scenario, arg, "expected_answer_facts" if name == "fact_present" else "forbidden_answer_facts")
        if fact["anchor_type"] == "semantic":
            return None, "semantic fact: judge-scored, not gating"
        hit = next((a for a in fact["anchors"] if _norm(a) in resp), None)
        if name == "fact_present" and fact["anchor_type"] == "clause":
            cited = fact["doc_id"] in _cited_docs(run.response)
            return bool(hit and cited), f"anchor {hit!r}, cited {fact['doc_id']}: {cited}"
        return (hit is not None, f"found {hit!r}") if name == "fact_present" else (hit is None, f"found {hit!r}" if hit else "absent")
    if name == "evidence_in_window":
        ok = _evidence_present(arg, scenario, run)
        return ok, "in window" if ok else "missing from the window"
    if name == "evidence_excluded":
        ok = not _evidence_present(arg, scenario, run)
        return ok, "excluded" if ok else "present in the window"
    if name == "citation_valid":
        in_window = {_doc_of(it) for it in _items(run)} | {d for t in run.tool_results for d in _cited_docs(t["text"])}
        cited = _cited_docs(run.response)
        bad = sorted(cited - in_window)
        return (bool(cited) and not bad), f"cited {sorted(cited)}; not in window {bad}"
    calls = (run.record or {}).get("tools", {}).get("calls") or [{"tool": t["tool"], "args": t["args"]} for t in run.tool_results]
    if name == "tool_called":
        wanted = [c for c in scenario["expected_tool_calls"] if c["tool"] == arg]
        def match(c, w):
            return c["tool"] == w["tool"] and all(str((c.get("args") or {}).get(k)) == str(v) or
                                                   str(v) in str((c.get("args") or {}).values()) for k, v in w["args"].items())
        ok = all(any(match(c, w) for c in calls) for w in wanted) if wanted else any(c["tool"] == arg for c in calls)
        return ok, f"{sum(c['tool'] == arg for c in calls)} call(s)"
    if name == "tool_not_called":
        n = sum(c["tool"] == arg for c in calls)
        return n == 0, f"{n} call(s)"
    if name == "call_count_within":
        tool, limit = arg.rsplit(":", 1)
        n = sum(c["tool"] == tool for c in calls)
        return n <= int(limit), f"{n} call(s), limit {limit}"
    if name in ("memory_injected", "memory_excluded"):
        present = any(it["item_id"] == arg for it in _items(run, {"memory"}))
        return (present, "injected" if present else "not injected") if name == "memory_injected" else (not present, "excluded" if not present else "injected")
    if name == "memory_written":
        target = run.manifests.get(arg)
        if not target or run.memory_store is None:
            return False, "no memory store or manifest target"
        mems = run.memory_store.find(subject_id=target["subject"])
        ok = any(all(_norm(a) in _norm(m.content) for a in target["fact_anchors"]) for m in mems)
        return ok, "written" if ok else f"no active memory about {target['subject']} with {target['fact_anchors']}"
    if name == "memory_superseded":
        rec = run.memory_store.get(arg) if run.memory_store else None
        return bool(rec and rec.status == "superseded"), f"status {rec.status if rec else 'missing'}"
    if name == "state_matches":
        key, _, value = arg.partition("=")
        got = _get(run.state, key)
        return str(got) == value, f"{key} = {got!r}"
    if name == "conflict_disclosed":
        a, b = arg.split("|")
        both = a.lower() in resp and b.lower() in resp
        marker = next((m for m in DISCLOSURE_MARKERS if m in resp), None)
        return both and marker is not None, f"both sources named: {both}; marker: {marker!r}"
    if name in ("channel_active", "channel_silent"):
        channel_map = {"tools": {"tool_definitions", "tool_results"}, "memory": {"memory", "state"}}
        if arg == "graph":
            active = any(it.get("source") == "graph" for it in _items(run, {"knowledge"}))
        elif arg == "tools":
            active = bool(run.tool_results) or bool(_items(run, {"tool_results"}))
        else:
            active = bool(_items(run, channel_map.get(arg, {arg})))
        return (active, "active" if active else "silent") if name == "channel_active" else (not active, "silent" if not active else "active")
    if name == "budget_respected":
        window = (run.record or {}).get("window", {})
        over = [ch for ch, info in window.items() if info.get("tokens", 0) > info.get("budget", 10 ** 9)]
        return not over, f"over budget: {over}" if over else "within every channel budget"
    if name == "digest_conforms":
        ok = bool(run.isolation and run.isolation.get("digest_conforms"))
        return ok, "digest conforms" if ok else "no conforming digest"
    return False, f"unknown assertion {name!r}"


def evaluate(scenario: dict, run: RunEvidence) -> dict:
    results = []
    for assertion in scenario["assertions"]:
        try:
            passed, detail = check(assertion, scenario, run)
        except Exception as exc:  # noqa: BLE001 — a crashing assertion is a failed assertion, with the reason
            passed, detail = False, f"error: {type(exc).__name__}: {exc}"
        results.append({"assertion": assertion, "passed": passed, "detail": detail})
    gating = [r for r in results if r["passed"] is not None]
    return {"scenario_id": scenario["scenario_id"], "passed": all(r["passed"] for r in gating), "assertions": gating,
            "judge_pending": [r["assertion"] for r in results if r["passed"] is None]}
