"""Run the M1 probes through NovaAssist with a chosen Context Spec and the replayed autopsy history.

    python labs/m1/run_probes.py --spec context_spec/spec_v1.yaml --session autopsy
    python labs/m1/run_probes.py --spec context_spec/example_spec.yaml --session autopsy   # the baseline
    python labs/m1/run_probes.py --spec context_spec/spec_v1.yaml --probe P-1              # one probe only
    python labs/m1/run_probes.py --fake ...                                                # offline, no model

Every run starts from a fresh copy of the replayed session (sessions/<session>/history.jsonl), so the probes
do not see each other's turns and spec v1 and the example spec are compared on the same history.
Each probe runs twice by default (--repeat): the model does not accept temperature 0, so answers vary.
Prints one table per run and writes a Markdown run report to labs/m1/runs/<spec name>_<time>.md, with an
empty rubric checklist for you to score.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
KIT_ROOT = HERE.parents[1]
sys.path.insert(0, str(KIT_ROOT))
sys.path.insert(0, str(HERE))

from novaassist.config import ConfigError  # noqa: E402
from novaassist.context_source import Session  # noqa: E402
from novaassist.llm_client import FakeLLMClient, LLMError, get_client  # noqa: E402
from novaassist.orchestrator import Orchestrator  # noqa: E402
from novaassist.sources import HistorySource, HistoryStore, StateSource, SystemContextSource, UserInputSource  # noqa: E402
from novaassist.spec import SpecError, load_spec  # noqa: E402
from novaassist.trace import read_events  # noqa: E402
from trace_summary import summarize  # noqa: E402

PROBES = HERE / "probes.yaml"
RUNS = HERE / "runs"


def load_probes(path: Path = PROBES) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def probe_input(probe: dict, folder: Path = HERE) -> str:
    text = probe["input"].strip()
    if probe.get("attach"):
        text += "\n\n" + (folder / probe["attach"]).read_text(encoding="utf-8").strip()
    return text


def fresh_session(source_session: str, run_session: str, probe: dict) -> Session:
    """A new session whose history is a copy of the replayed one."""
    source = HistoryStore(source_session)
    if not source.path.is_file() or not source.turns():
        raise SystemExit(f"session {source_session!r} has no history - run: "
                         f"python labs/m1/load_autopsy_history.py --session {source_session}")
    target = HistoryStore(run_session)
    shutil.copyfile(source.path, target.path)
    return Session(session_id=run_session, persona_id=probe.get("persona"), state=dict(probe.get("state") or {}),
                   history=target)


def run_probe(spec, probe: dict, llm, source_session: str, run_session: str) -> dict:
    session = fresh_session(source_session, run_session, probe)
    agent = Orchestrator(spec, [SystemContextSource(), StateSource(), HistorySource(), UserInputSource()], llm, session)
    result = agent.handle(probe_input(probe))
    summary = summarize(read_events(result.trace_path, result.request_id))
    return {"probe": probe, "session": run_session, "text": result.text, "trace": result.trace_path, **summary}


def format_run(run: dict) -> str:
    history = run["channels"].get("history", {})
    lines = [f"{run['probe']['id']} {run['probe']['title']}  [{run['request_id']}]",
             f"  window {run['window_tokens']} of {run['window_budget']} tokens   cost {run['cost_ccu']} CCU   "
             f"history kept {history.get('kept', 0)} / excluded {history.get('excluded', 0)}   "
             f"budget violations: {', '.join(run['violations']) or 'none'}",
             "  tokens by channel: " + ", ".join(f"{ch}={t}" for ch, t in run["tokens_by_channel"].items() if t),
             f"  trace: {run['trace']}",
             "  answer:", *("    " + ln for ln in run["text"].splitlines())]
    return "\n".join(lines)


def report_markdown(spec, spec_path: Path, runs: list[dict], rubric: dict, model: str, fake: bool, stamp: str) -> str:
    out = [f"# M1 probe run: {spec_path.name}", "",
           "| | |", "|---|---|",
           f"| Spec | `{spec_path.as_posix()}` (spec_version {spec.version}) |",
           f"| Run at | {stamp} |",
           f"| Model | {model}{' (fake client: offline, answers are placeholders)' if fake else ''} |",
           f"| Window budget | {spec.total_tokens - spec.response_reserve} tokens "
           f"(total {spec.total_tokens} - reserve {spec.response_reserve}) |", "",
           "## Summary", "",
           "| Probe | Run | Request | Window tokens | History kept / excluded | Violations | CCU |",
           "|---|---|---|---|---|---|---|"]
    for r in runs:
        h = r["channels"].get("history", {})
        out.append(f"| {r['probe']['id']} | {r['repeat']} | {r['request_id']} | {r['window_tokens']} | "
                   f"{h.get('kept', 0)} / {h.get('excluded', 0)} | {', '.join(r['violations']) or 'none'} | "
                   f"{r['cost_ccu']} |")
    for r in runs:
        p = r["probe"]
        out += ["", f"## {p['id']} {p['title']}, run {r['repeat']}", "",
                f"Persona {p.get('persona')}, state `{p.get('state')}`, trace `{Path(r['trace']).name}`, "
                f"request `{r['request_id']}`.", "",
                "| Channel | Tokens | Budget | Kept | Excluded | Reasons |", "|---|---|---|---|---|---|"]
        for ch, tokens in r["tokens_by_channel"].items():
            info = r["channels"].get(ch, {})
            reasons = ", ".join(f"{k}={v}" for k, v in info.get("reasons", {}).items())
            out.append(f"| {ch} | {tokens} | {info.get('budget', '')} | {info.get('kept', 0)} | "
                       f"{info.get('excluded', 0)} | {reasons} |")
        out += ["", "Answer:", "", "```text", r["text"], "```", "",
                "Rubric (score 0 or 1; score the worse of the two runs):", ""]
        out += [f"- [ ] {item} {rubric[item]}" for item in p["rubric"]]
    out += ["", "## Notes", "", "- What changed versus the other spec, and which trace line shows it:", ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the M1 probes and write a run report.")
    parser.add_argument("--spec", type=Path, default=KIT_ROOT / "context_spec" / "spec_v1.yaml")
    parser.add_argument("--session", default="autopsy", help="the replayed session to start from")
    parser.add_argument("--probe", action="append", default=None, help="run only this probe (repeatable)")
    parser.add_argument("--repeat", type=int, default=2, help="runs per probe (default 2)")
    parser.add_argument("--fake", action="store_true", help="use the offline fake LLM client")
    parser.add_argument("--out", type=Path, default=RUNS)
    args = parser.parse_args(argv)

    try:
        spec = load_spec(args.spec)
        llm = FakeLLMClient() if args.fake else get_client()
    except (SpecError, ConfigError) as exc:
        print(exc)
        return 2
    config = load_probes()
    probes = [p for p in config["probes"] if not args.probe or p["id"] in args.probe]
    if not probes:
        print(f"no probe matches {args.probe}; known: {[p['id'] for p in config['probes']]}")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    runs = []
    for probe in probes:
        for repeat in range(1, args.repeat + 1):
            run_session = f"{args.session}-{args.spec.stem}-{probe['id']}-{stamp}-r{repeat}"
            try:
                run = run_probe(spec, probe, llm, args.session, run_session)
            except LLMError as exc:
                print(f"{probe['id']} run {repeat}: LLM error: {exc}")
                return 1
            run["repeat"] = repeat
            runs.append(run)
            print(format_run(run) + "\n")

    args.out.mkdir(parents=True, exist_ok=True)
    report = args.out / f"{args.spec.stem}_{stamp}.md"
    report.write_text(report_markdown(spec, args.spec, runs, config["rubric"], llm.model, args.fake, stamp),
                      encoding="utf-8")
    print(f"run report: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
