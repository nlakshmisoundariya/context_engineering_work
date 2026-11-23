"""Write the autopsy conversation's 22 history turns into a NovaAssist session, so they can be replayed.

    python labs/m1/load_autopsy_history.py --session autopsy

Reads the history component of data/novatech/m1/autopsy_window.json (the window NovaBot sent) and writes one
turn per line to sessions/<session>/history.jsonl, in the kit's HistoryStore format. Running it again rewrites
the session from scratch, so the replay always starts from the same 22 turns.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(KIT_ROOT))

from novaassist.sources import HistoryStore  # noqa: E402

DEFAULT_WINDOW = KIT_ROOT / "data" / "novatech" / "m1" / "autopsy_window.json"
TURN_HEADER = re.compile(r"^\[(?P<turn_id>\S+) (?P<role>\w+)\]$", re.M)


def parse_turns(history_text: str) -> list[dict]:
    """Split the window's history block ("[CONV-0044-T01 staff]\\n<text>\\n\\n[...]") into turns."""
    headers = list(TURN_HEADER.finditer(history_text))
    turns = []
    for i, match in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(history_text)
        role = "assistant" if match["role"] == "assistant" else "user"  # the transcript calls the user "staff"
        turns.append({"source_turn": match["turn_id"], "role": role,
                      "text": history_text[match.end():end].strip()})
    return turns


def load(session: str, window_path: Path = DEFAULT_WINDOW, directory: Path | None = None) -> HistoryStore:
    if not window_path.is_file():
        raise SystemExit(f"autopsy window not found: {window_path} (is the S01 checkpoint installed?)")
    window = json.loads(window_path.read_text(encoding="utf-8"))
    history = next(c["content"] for c in window["components"] if c["component"] == "history")
    turns = parse_turns(history)
    if len(turns) != window["history_turns"]:
        raise SystemExit(f"expected {window['history_turns']} turns in the autopsy window, parsed {len(turns)}")
    store = HistoryStore(session, directory)
    store.path.write_text("", encoding="utf-8")  # idempotent: start the session from scratch
    for turn in turns:
        store.append(turn["role"], turn["text"], window["request_time"])
    return store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay the autopsy conversation into a NovaAssist session.")
    parser.add_argument("--session", default="autopsy")
    parser.add_argument("--window", type=Path, default=DEFAULT_WINDOW)
    args = parser.parse_args(argv)
    store = load(args.session, args.window)
    print(f"session {args.session!r}: {len(store.turns())} history turns written to {store.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
