"""Evaluation report templates (Module 9 onwards): file-based reports and charts, no dashboard.

    from tools.reports import scenario_table, compare_runs, write_report, bar_chart
    md = scenario_table(results)                         # pass/fail by scenario and by assertion family
    md += compare_runs(baseline_metrics, current_metrics, latency_ms=(base_ms, cur_ms))
    write_report("reports/eval-S10.md", "Evaluation after M9", md)

Latency is reported only *relative* to a baseline on the same machine and run (EVALUATION_SPEC §2 Layer D);
RAGAS and judge scores are shown as tracked signals, never as pass/fail.
"""
from __future__ import annotations

from pathlib import Path

FAMILIES = {
    "FACT": ("fact_present", "fact_absent"),
    "EVIDENCE": ("evidence_in_window", "evidence_excluded", "citation_valid"),
    "TOOL": ("tool_called", "tool_not_called", "call_count_within"),
    "MEMORY/STATE": ("memory_injected", "memory_excluded", "memory_written", "memory_superseded", "state_matches"),
    "CONTEXT/POLICY": ("conflict_disclosed", "channel_active", "channel_silent"),
    "BUDGET/BOUNDARY": ("budget_respected", "digest_conforms"),
}


def family_of(assertion: str) -> str:
    name = assertion.split(":", 1)[0]
    return next((f for f, names in FAMILIES.items() if name in names), "OTHER")


def scenario_table(results: list[dict]) -> str:
    """``results``: [{"scenario_id", "passed": bool, "assertions": [{"assertion", "passed", "detail"}]}]."""
    lines = ["## Scenario results", "", "| scenario | result | failed assertions |", "|---|---|---|"]
    for r in results:
        failed = [a["assertion"] for a in r["assertions"] if not a["passed"]]
        lines.append(f"| {r['scenario_id']} | {'PASS' if r['passed'] else 'FAIL'} | {', '.join(failed) or '—'} |")
    totals: dict[str, list[int]] = {f: [0, 0] for f in FAMILIES}
    for r in results:
        for a in r["assertions"]:
            fam = family_of(a["assertion"])
            if fam in totals:
                totals[fam][0] += a["passed"]
                totals[fam][1] += 1
    lines += ["", "## By assertion family", "", "| family | passed | total |", "|---|---|---|"]
    lines += [f"| {f} | {p} | {t} |" for f, (p, t) in totals.items() if t]
    passed = sum(r["passed"] for r in results)
    lines += ["", f"**Layer A pass rate: {passed}/{len(results)}**", ""]
    return "\n".join(lines)


def compare_runs(baseline: dict, current: dict, latency_ms: tuple[float, float] | None = None, title: str = "Against baseline") -> str:
    """Metric deltas (current - baseline). Latency only as a ratio to the baseline."""
    lines = [f"## {title}", "", "| metric | baseline | current | delta |", "|---|---|---|---|"]
    for k in sorted(set(baseline) | set(current)):
        b, c = baseline.get(k), current.get(k)
        delta = round(c - b, 4) if isinstance(b, (int, float)) and isinstance(c, (int, float)) else ""
        lines.append(f"| {k} | {b} | {c} | {delta} |")
    if latency_ms:
        lines.append(f"| latency (relative) | 1.00 | {latency_ms[1] / latency_ms[0]:.2f} | |")
    return "\n".join(lines) + "\n"


def bar_chart(values: dict[str, float], path: str | Path, title: str, ylabel: str = "") -> Path:
    """A simple bar chart saved as PNG (matplotlib, no display needed)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7, 3.5))
    ax.bar(list(values), list(values.values()), color="#4C72B0")
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.tick_params(axis="x", rotation=30)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def write_report(path: str | Path, title: str, body: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\n\n{body}", encoding="utf-8")
    return path


def ragas_samples(runs: list[dict], context_text: dict[str, str]) -> list[dict]:
    """Build RAGAS samples from scenario runs: question, answer, and the text of the knowledge items in-window.

    ``runs``: [{"question", "answer", "record"}] with assembled trace records; ``context_text``: item_id -> text
    (e.g. from your chunk store). Feed the result to ``tools.ragas_eval.ragas_scores``.
    """
    return [{"question": r["question"], "answer": r["answer"],
             "contexts": [context_text[i] for i in r["record"]["window"].get("knowledge", {}).get("items", []) if i in context_text]}
            for r in runs]
