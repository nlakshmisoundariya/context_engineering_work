# Evaluation report — <team> — <session>

## Run manifest
- Dataset version: <from data/novatech/MANIFEST.json>
- Context Spec version(s): <spec_version per arm>
- Scenario set: <dev scenario IDs / query set>
- Model and settings: <LLM_MODEL, completion budget>; token counter: <from traces>
- Fault flags active: <none | F..>

## Layer A — deterministic results
<tools.reports.scenario_table(...)>

## Layer B — retrieval and context metrics (vs M3/M4 baselines)
<tools.reports.compare_runs(baseline, current)>
Context metrics (required core): completeness · freshness · authority correctness.

## Layer C — RAGAS (tracked signal, never a gate)
Faithfulness: <..>   Answer relevancy: <..>   (vs baseline: <delta>)

## Layer D — efficiency
Tokens per request by channel, relative latency (this machine, same run), cost in CCU.

## Findings
One regression or weakness located from traces and metrics alone: <trace reference, evidence, cause>.

## Decision
<what you change next, and the experiment record that will test it>
