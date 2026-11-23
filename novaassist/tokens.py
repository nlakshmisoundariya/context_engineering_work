"""Token counting for the simulated application-level context budget.

The 12K budget in the Context Spec is enforced by NovaAssist, independent of the model's
native window, so token counts only need to be consistent, not provider-exact.

CE_TOKEN_COUNTER selects the counter:
- ``tiktoken`` — the o200k_base encoding (must be cached locally; never downloaded at runtime here)
- ``approx``   — a deterministic word/punctuation heuristic (offline, used by the tests)
- ``auto``     — tiktoken when it loads, otherwise approx (the default)

The counter in use is reported by ``counter_name()`` and recorded in every trace.
"""
from __future__ import annotations

import math
import os
import re
from functools import lru_cache

_WORD_OR_PUNCT = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def _approx(text: str) -> int:
    # Roughly 1.3 tokens per word for English technical text; punctuation counts as one token each.
    pieces = _WORD_OR_PUNCT.findall(text)
    words = sum(1 for p in pieces if p[0].isalnum() or p[0] == "_")
    punct = len(pieces) - words
    return math.ceil(words * 1.3) + punct


@lru_cache(maxsize=1)
def _encoder():
    choice = os.environ.get("CE_TOKEN_COUNTER", "auto").strip().lower() or "auto"
    if choice == "approx":
        return None
    try:
        import tiktoken

        return tiktoken.get_encoding("o200k_base")
    except Exception:
        if choice == "tiktoken":
            raise RuntimeError(
                "CE_TOKEN_COUNTER=tiktoken but the o200k_base encoding could not be loaded "
                "(is TIKTOKEN_CACHE_DIR populated?). Tell the trainer, or set CE_TOKEN_COUNTER=approx."
            )
        return None


def counter_name() -> str:
    return "tiktoken:o200k_base" if _encoder() is not None else "approx:v1"


def count_tokens(text: str) -> int:
    """Number of tokens in ``text`` under the active counter."""
    if not text:
        return 0
    enc = _encoder()
    if enc is None:
        return _approx(text)
    return len(enc.encode(text, disallowed_special=()))
