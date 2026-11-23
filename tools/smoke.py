"""Smoke-suite runner for the capstone Integrate gate (CAPSTONE_ARCHITECTURE §16; EVALUATION_SPEC §15).

Gate: the smoke scenarios for J1, J2 and J4 pass (Layer A), J3 runs end-to-end without an error, and no channel
exceeds its Context Spec budget anywhere in the suite.

    python -m tools.smoke --agent my_agent:build_for_scenario
    python -m tools.smoke --agent my_agent:build_for_scenario --scenarios GS-J1-01 GS-J5-01   # any dev scenarios

``build_for_scenario(scenario) -> (orchestrator, memory_store_or_None)`` is yours: wire your sources, seed the
session (``seed_session`` helps with CONV/SES seeds), run any prior sessions the scenario names (J4: replay
CONV-0031 through your extractor). ``--assembler module:function`` defaults to your ``tools.traces.assemble_record``.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

from .assertions import RunEvidence, evaluate
from .reports import scenario_table, write_report
from .traces import group_by_request, load_events

DEV = Path("data/novatech/eval/scenarios/dev")
MANIFESTS = Path("data/novatech/eval/memory_manifests")
GATE_PASS = ("GS-J1-01", "GS-J2-01", "GS-J4-01")
GATE_RUN = ("GS-J3-01",)


def load_scenarios(ids=None, folder: Path = DEV) -> list[dict]:
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(folder).glob("GS-*.json"))]
    return [r for r in rows if ids is None or r["scenario_id"] in ids]


def load_manifest_targets(folder: Path = MANIFESTS) -> dict:
    return {t["target_id"]: t for p in Path(folder).glob("*.json")
            for t in json.loads(p.read_text(encoding="utf-8"))["write_targets"]}


def seed_session(session, scenario: dict, data_root: Path = Path("data/novatech")) -> None:
    """Load a scenario's session seed: history from a conversation file, state from a snapshot."""
    seed = scenario.get("session_seed")
    if not isinstance(seed, dict):
        return
    if seed.get("history") and session.history is not None:
        lines = (data_root / "conversations" / f"{seed['history']}.jsonl").read_text(encoding="utf-8").splitlines()[1:]
        for turn in map(json.loads, lines):
            session.history.append("assistant" if turn["role"] == "assistant" else "user", turn["text"], turn["timestamp"])
    if seed.get("state"):
        snap = json.loads((data_root / "sessions" / f"{seed['state']}.json").read_text(encoding="utf-8"))
        session.state.update({k: v for k, v in snap["state"].items() if v is not None})


def _query(scenario: dict) -> list[str]:
    q = scenario["user_query"]
    return q if isinstance(q, list) else [q]


def run_scenario(scenario: dict, build, assembler, manifests: dict) -> dict:
    try:
        agent, store = build(scenario)
        result = None
        for text in _query(scenario):
            result = agent.handle(text)
        events = group_by_request(load_events(result.trace_path))[result.request_id]
        record = assembler(events)
        run = RunEvidence(response=result.text, window=result.window, tool_results=result.tool_results, record=record,
                          state=agent.session.state, memory_store=store, isolation=result.isolation, manifests=manifests)
        out = evaluate(scenario, run)
        out["error"] = None
    except Exception as exc:  # noqa: BLE001 — the gate reports errors, it does not crash
        out = {"scenario_id": scenario["scenario_id"], "passed": False, "assertions": [], "judge_pending": [],
               "error": f"{type(exc).__name__}: {exc}"}
    return out


def gate(results: list[dict]) -> dict:
    by_id = {r["scenario_id"]: r for r in results}
    budget_ok = all(a["passed"] for r in results for a in r["assertions"] if a["assertion"].startswith("budget_respected"))
    checks = {f"{sid} passes": bool(by_id.get(sid, {}).get("passed")) for sid in GATE_PASS}
    checks.update({f"{sid} runs end-to-end": sid in by_id and by_id[sid]["error"] is None for sid in GATE_RUN})
    checks["no budget violations"] = budget_ok
    return {"passed": all(checks.values()), "checks": checks}


def run_suite(build, assembler, ids=None, folder: Path = DEV, manifests_folder: Path = MANIFESTS) -> dict:
    ids = ids or list(GATE_PASS + GATE_RUN)
    results = [run_scenario(s, build, assembler, load_manifest_targets(manifests_folder)) for s in load_scenarios(ids, folder)]
    return {"results": results, "gate": gate(results)}


def _load(spec: str):
    module, _, attr = spec.partition(":")
    sys.path.insert(0, str(Path.cwd()))
    return getattr(importlib.import_module(module), attr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the capstone smoke suite (Integrate gate).")
    parser.add_argument("--agent", required=True, help="module:function returning (orchestrator, memory_store) for a scenario")
    parser.add_argument("--assembler", default="tools.traces:assemble_record")
    parser.add_argument("--scenarios", nargs="*", default=None)
    parser.add_argument("--report", default="reports/smoke.md")
    args = parser.parse_args(argv)
    out = run_suite(_load(args.agent), _load(args.assembler), args.scenarios)
    body = scenario_table(out["results"]) + "\n## Integrate gate\n\n" + "\n".join(
        f"- [{'x' if ok else ' '}] {name}" for name, ok in out["gate"]["checks"].items())
    errors = [f"- {r['scenario_id']}: {r['error']}" for r in out["results"] if r["error"]]
    if errors:
        body += "\n\n## Errors\n\n" + "\n".join(errors)
    print(body)
    print(f"\nreport: {write_report(args.report, 'Capstone smoke suite', body)}")
    return 0 if out["gate"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
