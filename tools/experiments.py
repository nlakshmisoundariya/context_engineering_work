"""Experiment records (EVALUATION_SPEC §13): one shape for every baseline-vs-variant comparison.

    from tools.experiments import new_experiment, save_experiment
    exp = new_experiment("EXP-M3-team1-01", "BM25 beats FAISS on keyword queries (recall@8)",
                         scenario_set=["GQ-01", ...], baseline_config={"retriever": "faiss", "top_k": 8},
                         variant_config={"retriever": "bm25", "top_k": 8})
    exp.metrics_before, exp.metrics_after = faiss_scores, bm25_scores
    exp.observation = "..."; exp.decision = "..."
    save_experiment(exp)                       # experiments/EXP-M3-team1-01.json

The variant must change exactly one variable (a multi-variable change needs a chained series).
``decision`` is mandatory before an experiment can be saved as final.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ExperimentRecord:
    experiment_id: str
    hypothesis: str
    scenario_set: list
    baseline_config: dict
    variant_config: dict
    dataset_version: str | None = None
    spec_versions: dict = field(default_factory=dict)  # {"baseline": "1.2", "variant": "1.2"}
    metrics_before: dict = field(default_factory=dict)
    metrics_after: dict = field(default_factory=dict)
    efficiency_delta: dict = field(default_factory=dict)  # tokens, latency (relative), cost_ccu
    observation: str = ""
    decision: str = ""
    trace_refs: dict = field(default_factory=dict)  # {"baseline": [...], "variant": [...]}
    changed_variable: str = ""


def _flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def changed_variables(baseline: dict, variant: dict) -> list[str]:
    a, b = _flatten(baseline), _flatten(variant)
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def new_experiment(experiment_id: str, hypothesis: str, scenario_set: list, baseline_config: dict,
                   variant_config: dict, chained: bool = False, **fields) -> ExperimentRecord:
    changed = changed_variables(baseline_config, variant_config)
    if not chained and len(changed) != 1:
        raise ValueError(f"a variant must change exactly one variable; it changes {changed or 'nothing'}. "
                         "Split it into a chained series, or pass chained=True for a deliberate multi-step record.")
    return ExperimentRecord(experiment_id, hypothesis, list(scenario_set), baseline_config, variant_config,
                            changed_variable=", ".join(changed), **fields)


def metric_deltas(before: dict, after: dict) -> dict:
    return {k: round(after[k] - before[k], 4) for k in before if k in after
            and isinstance(before[k], (int, float)) and isinstance(after[k], (int, float))}


def save_experiment(record: ExperimentRecord, folder: str | Path = "experiments") -> Path:
    if not record.decision.strip():
        raise ValueError("record the engineering decision (the 'decision' field) before saving the experiment")
    path = Path(folder) / f"{record.experiment_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(record) | {"deltas": metric_deltas(record.metrics_before, record.metrics_after)}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_experiment(path: str | Path) -> ExperimentRecord:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data.pop("deltas", None)
    return ExperimentRecord(**data)
