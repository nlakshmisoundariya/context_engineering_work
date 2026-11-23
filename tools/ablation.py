"""Ablation runner (EVALUATION_SPEC §5.3, §13): replay the query set per selection configuration and emit
paired experiment records with quality deltas and efficiency deltas (tokens, relative latency, CCU).

    from tools.ablation import ablation_matrix, run_ablation
    matrix = ablation_matrix(StackConfig(retriever="hybrid"))          # add-one and drop-one variants
    report = run_ablation(stack, queries, matrix, scorer=ce_score, out_dir="runs/ablation", team="team1")

``scorer(run_path) -> {"all": {...}, "keyword": {...}, ...}`` turns a run file into per-type aggregates.
On the course servers ``ce_score`` calls the trainer's scoring command (labels stay hidden).
Every comparison changes exactly one variable, so every record is a valid §13 experiment.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

from novaassist.economics import ccu_cost

from .experiments import new_experiment, save_experiment
from .scoring import write_run
from .selection import SelectionStack, StackConfig, config_dict

ELEMENTS = ("retriever", "rewrite", "hyde", "mmr", "rerank")


def ablation_matrix(base: StackConfig, full: StackConfig | None = None) -> list[dict]:
    """Named configurations plus the one-variable comparison each one belongs to.

    add-one: base vs base + one element  |  drop-one: full stack vs full stack minus one element.
    The retriever element compares dense -> sparse -> hybrid one step at a time.
    """
    full = full or replace(base, retriever="hybrid", rewrite=True, hyde=True, mmr=True, rerank=True)
    configs = {"base": base, "full": full}
    pairs = []
    for flag in ("rewrite", "hyde", "mmr", "rerank"):
        if not getattr(base, flag):
            configs[f"base+{flag}"] = replace(base, **{flag: True})
            pairs.append(("base", f"base+{flag}"))
        configs[f"full-{flag}"] = replace(full, **{flag: False})
        pairs.append((f"full-{flag}", "full"))
    for retriever in ("dense", "sparse", "hybrid"):
        if retriever != base.retriever:
            configs[f"base@{retriever}"] = replace(base, retriever=retriever)
            pairs.append(("base", f"base@{retriever}"))
    return [{"configs": configs, "pairs": pairs}]


def ce_score(run_path: str | Path) -> dict:
    """Score through the trainer's ``ce-score`` command (aggregates only)."""
    out = subprocess.run(["ce-score", "--json", str(run_path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def run_config(stack: SelectionStack, queries: list[dict], config: StackConfig) -> dict:
    results, usage, latency, context_tokens = {}, {"prompt_tokens": 0, "completion_tokens": 0}, 0.0, 0
    for q in queries:
        r = stack.run(q["query"], config)
        results[q["query_id"]] = r.hits
        latency += r.latency_ms
        context_tokens += sum(stack.by_id[h.chunk_id].token_count for h in r.hits)
        for k in usage:
            usage[k] += r.llm_usage[k]
    return {"results": results, "llm_usage": usage, "latency_ms": latency, "context_tokens": context_tokens}


def run_ablation(stack: SelectionStack, queries: list[dict], matrix: list[dict], scorer, out_dir: str | Path,
                 team: str = "team", module: str = "M4", dataset_version: str | None = None) -> dict:
    out_dir = Path(out_dir)
    plan = matrix[0]
    measured = {}
    for name, cfg in plan["configs"].items():
        run = run_config(stack, queries, cfg)
        path = write_run(out_dir / f"{name}.jsonl", run["results"], stack.by_id, name, cfg.top_k)
        measured[name] = {"config": config_dict(cfg), "run_file": str(path), "quality": scorer(path),
                          "efficiency": {"llm_tokens": run["llm_usage"]["prompt_tokens"] + run["llm_usage"]["completion_tokens"],
                                         "context_tokens": run["context_tokens"], "latency_ms": round(run["latency_ms"], 1),
                                         "cost_ccu": ccu_cost(run["llm_usage"]["prompt_tokens"], run["llm_usage"]["completion_tokens"])}}
    records = []
    for n, (before, after) in enumerate(plan["pairs"], start=1):
        b, a = measured[before], measured[after]
        exp = new_experiment(f"EXP-{module}-{team}-{n:02d}", f"{after} vs {before}: effect on retrieval quality",
                             [q["query_id"] for q in queries], b["config"], a["config"], dataset_version=dataset_version)
        exp.metrics_before = b["quality"].get("all", {})
        exp.metrics_after = a["quality"].get("all", {})
        base_latency = b["efficiency"]["latency_ms"] or 1.0
        exp.efficiency_delta = {"llm_tokens": a["efficiency"]["llm_tokens"] - b["efficiency"]["llm_tokens"],
                                "context_tokens": a["efficiency"]["context_tokens"] - b["efficiency"]["context_tokens"],
                                "latency_relative": round(a["efficiency"]["latency_ms"] / base_latency, 2),
                                "cost_ccu": round(a["efficiency"]["cost_ccu"] - b["efficiency"]["cost_ccu"], 4)}
        exp.trace_refs = {"baseline": [b["run_file"]], "variant": [a["run_file"]]}
        exp.decision = "PENDING: record your decision"  # participants replace this with their engineering decision
        records.append(str(save_experiment(exp, out_dir / "experiments")))
    report = {"configs": measured, "experiments": records}
    (out_dir / "ablation_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def format_matrix(report: dict, metric: str = "must_retrieve_hit") -> str:
    lines = [f"{'config':<16}{'recall@k':>10}{'must_hit':>10}{'forbidden':>10}{'llm_tok':>9}{'ctx_tok':>9}{'ms':>9}{'CCU':>8}"]
    for name, m in report["configs"].items():
        q, e = m["quality"].get("all", {}), m["efficiency"]
        lines.append(f"{name:<16}{q.get('recall@k', ''):>10}{q.get('must_retrieve_hit', ''):>10}"
                     f"{q.get('forbidden_rate', ''):>10}{e['llm_tokens']:>9}{e['context_tokens']:>9}"
                     f"{e['latency_ms']:>9.0f}{e['cost_ccu']:>8}")
    return "\n".join(lines)
