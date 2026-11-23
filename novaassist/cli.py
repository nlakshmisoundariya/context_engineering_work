"""Minimal CLI chat loop for NovaAssist.

    python -m novaassist.cli                                  # interactive, real LLM from .env
    python -m novaassist.cli --fake                           # offline fake LLM
    python -m novaassist.cli --once "What does Premium support cover?"
    python -m novaassist.cli --spec context_spec/example_spec.yaml --session my-session --persona P1

In the loop:  :state key=value   set session state (e.g. :state active_customer=CUST-0001)
              :trace             show the trace file path
              :quit              leave
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .config import ConfigError
from .context_source import Session
from .llm_client import FakeLLMClient, LLMError, get_client
from .orchestrator import Orchestrator
from .sources import HistorySource, HistoryStore, StateSource, SystemContextSource, UserInputSource
from .spec import SpecError, load_spec

KIT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = KIT_ROOT / "context_spec" / "example_spec.yaml"


def build_agent(spec_path: Path, session_id: str, persona: str | None, fake: bool) -> Orchestrator:
    spec = load_spec(spec_path)
    llm = FakeLLMClient() if fake else get_client()
    session = Session(session_id=session_id, persona_id=persona, history=HistoryStore(session_id))
    sources = [SystemContextSource(), StateSource(), HistorySource(), UserInputSource()]
    return Orchestrator(spec, sources, llm, session)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NovaAssist chat loop")
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--session", default=None, help="session id (default: a new one per run)")
    parser.add_argument("--persona", default="P1", choices=["P1", "P2", "P3"])
    parser.add_argument("--fake", action="store_true", help="use the offline fake LLM client")
    parser.add_argument("--once", default=None, help="ask one question and exit")
    args = parser.parse_args(argv)
    session_id = args.session or f"sess-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    try:
        agent = build_agent(args.spec, session_id, args.persona, args.fake)
    except (SpecError, ConfigError) as exc:
        print(exc)
        return 2

    def ask(text: str) -> None:
        try:
            result = agent.handle(text)
        except LLMError as exc:
            print(f"LLM error: {exc}")
            return
        print(f"\nNovaAssist: {result.text}\n")
        print(f"  [{result.request_id} | window {result.window_tokens} tokens | trace {result.trace_path}]\n")

    if args.once:
        if agent.faults.flags:
            print(agent.faults.banner())
        ask(args.once)
        return 0
    print(f"NovaAssist - session {session_id}, spec v{agent.spec.version}. Type :quit to leave.")
    if agent.faults.flags:
        print(agent.faults.banner())
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line == ":quit":
            return 0
        if line == ":trace":
            print(agent.tracer.path)
        elif line.startswith(":state"):
            for pair in line.split()[1:]:
                key, _, value = pair.partition("=")
                agent.session.state[key] = value
            print(f"state: {agent.session.state}")
        else:
            ask(line)


if __name__ == "__main__":
    sys.exit(main())
