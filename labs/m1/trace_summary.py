"""Summarise a NovaAssist trace file without jq: per request, what entered the window and what did not.

    python labs/m1/trace_summary.py traces/<session>.jsonl
    python labs/m1/trace_summary.py traces/<session>.jsonl --request REQ-1a2b3c4d5e

Per request it prints window tokens by channel, kept / excluded item counts per channel with the exclusion
reasons, the excluded history turns, budget violations and the cost in CCU. Everything comes from the raw
trace events (docs: novaassist/trace.py), so what you see here is what the pipeline actually did.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(KIT_ROOT))

from novaassist.trace import read_events  # noqa: E402


def summarize(events: list[dict]) -> dict:
    """One request's events -> a flat summary. Missing events give empty values, never an error."""
    start = next((e for e in events if e["event"] == "request_start"), {})
    end = next((e for e in events if e["event"] == "request_end"), {})
    window = next((e for e in events if e["event"] == "window"), {})
    # every channel's budget comes from the window layout; select events exist only for channels with candidates
    channels = {ch: {"budget": info["budget"], "kept": 0, "excluded": 0, "reasons": {}, "excluded_items": [],
                     "budget_violation": False} for ch, info in window.get("channels", {}).items()}
    for e in events:
        if e["event"] != "select":
            continue
        channels[e["channel"]] = {
            "budget": e["budget"],
            "kept": len(e["selected"]),
            "excluded": len(e["excluded"]),
            "reasons": dict(Counter(x["reason"] for x in e["excluded"])),
            "excluded_items": [{"item_id": x["item_id"], "reason": x["reason"], "detail": x["detail"],
                                "tokens": x["tokens"]} for x in e["excluded"]],
            "budget_violation": bool(e.get("budget_violation")),
        }
    return {
        "request_id": start.get("request_id") or end.get("request_id"),
        "spec_version": start.get("spec_version"),
        "persona_id": start.get("persona_id"),
        "model": start.get("model"),
        "window_tokens": end.get("window_tokens"),
        "window_budget": (start.get("budget_total") or 0) - (start.get("response_reserve") or 0),
        "tokens_by_channel": end.get("tokens_by_channel", {}),
        "channels": channels,
        "violations": sorted(ch for ch, info in channels.items() if info["budget_violation"]),
        "cost_ccu": end.get("cost_ccu"),
    }


def summarize_file(path: Path, request_id: str | None = None) -> list[dict]:
    events = read_events(path, request_id)
    order = list(dict.fromkeys(e["request_id"] for e in events if e["request_id"]))
    return [summarize([e for e in events if e["request_id"] == rid]) for rid in order]


def format_summary(s: dict) -> str:
    lines = [f"{s['request_id']}  spec v{s['spec_version']}  persona {s['persona_id']}  model {s['model']}",
             f"  window {s['window_tokens']} of {s['window_budget']} tokens   cost {s['cost_ccu']} CCU   "
             f"budget violations: {', '.join(s['violations']) or 'none'}",
             f"  {'channel':<17}{'tokens':>8}{'budget':>8}{'kept':>6}{'excl.':>7}  reasons"]
    for ch, tokens in s["tokens_by_channel"].items():
        info = s["channels"].get(ch, {})
        reasons = ", ".join(f"{r}={n}" for r, n in info.get("reasons", {}).items())
        lines.append(f"  {ch:<17}{tokens:>8}{info.get('budget', ''):>8}{info.get('kept', 0):>6}"
                     f"{info.get('excluded', 0):>7}  {reasons}")
    history = s["channels"].get("history", {}).get("excluded_items", [])
    if history:
        lines.append("  excluded history turns:")
        lines += [f"    {x['item_id']:<24}{x['tokens']:>6} tokens  {x['reason']}: {x['detail']}" for x in history]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarise a NovaAssist trace file.")
    parser.add_argument("trace", type=Path)
    parser.add_argument("--request", default=None, help="only this request id")
    args = parser.parse_args(argv)
    if not args.trace.is_file():
        print(f"trace file not found: {args.trace}")
        return 2
    summaries = summarize_file(args.trace, args.request)
    if not summaries:
        print("no requests found in this trace")
        return 1
    print("\n\n".join(format_summary(s) for s in summaries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
