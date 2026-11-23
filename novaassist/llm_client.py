"""Thin, provider-neutral LLM client.

Everything outside this file talks to the model through ``LLMClient.complete``. One real
implementation (OpenAI) sits behind it, plus a deterministic ``FakeLLMClient`` for tests and
offline dry runs.

Messages use the common chat shape: ``{"role": "system"|"user"|"assistant"|"tool", "content": str}``.
An assistant turn that requested tools carries ``tool_calls``; a tool result carries ``tool_call_id``.

Tools are described neutrally: ``{"name", "description", "input_schema"}`` (JSON Schema).

Known deviation (recorded in the build reports): the configured model rejects ``temperature``
and ``max_tokens``. The client therefore never sends ``temperature`` and uses
``max_completion_tokens``. The specs' "temperature 0" is not achievable with this model.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import LLMSettings, llm_settings
from .tokens import count_tokens


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    """What every client returns. ``usage`` feeds the trace's economics block."""

    text: str
    model: str
    provider: str
    usage: dict  # {"prompt_tokens", "completion_tokens", "total_tokens"}
    latency_ms: float
    tool_calls: list[ToolCall] = field(default_factory=list)
    parsed: Any = None  # filled when a JSON schema was requested
    finish_reason: str | None = None


class LLMError(RuntimeError):
    """The provider call failed or returned something unusable."""


class LLMClient:
    """Interface: one method, ``complete``."""

    provider = "abstract"
    model = ""

    def complete(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        json_schema: dict | None = None,
        schema_name: str = "result",
        max_completion_tokens: int | None = None,
    ) -> LLMResponse:
        raise NotImplementedError


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Model did not return valid JSON for the requested schema: {text[:200]!r}") from exc


class OpenAIClient(LLMClient):
    provider = "openai"

    def __init__(self, settings: LLMSettings):
        from openai import OpenAI  # imported here so the fake client works without the package

        self.settings = settings
        self.model = settings.model
        self._client = OpenAI(api_key=settings.api_key)

    @staticmethod
    def _to_openai_messages(messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                out.append({
                    "role": "assistant",
                    "content": m.get("content") or None,
                    "tool_calls": [
                        {"id": c["id"], "type": "function",
                         "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                        for c in m["tool_calls"]
                    ],
                })
            elif m.get("role") == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                out.append({"role": m["role"], "content": m["content"]})
        return out

    def complete(self, messages, *, tools=None, json_schema=None, schema_name="result",
                 max_completion_tokens=None) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_openai_messages(messages),
            "max_completion_tokens": max_completion_tokens or self.settings.max_completion_tokens,
        }
        if tools:
            kwargs["tools"] = [
                {"type": "function",
                 "function": {"name": t["name"], "description": t.get("description", ""),
                              "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}}
                for t in tools
            ]
        if json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": json_schema, "strict": True},
            }
        start = time.perf_counter()
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # surface provider errors with the model name, never the key
            raise LLMError(f"{self.provider} call failed for model {self.model!r}: {exc}") from exc
        latency_ms = (time.perf_counter() - start) * 1000
        choice = resp.choices[0]
        text = choice.message.content or ""
        calls = []
        for c in choice.message.tool_calls or []:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_unparsed": c.function.arguments}
            calls.append(ToolCall(id=c.id, name=c.function.name, arguments=args))
        usage = {
            "prompt_tokens": getattr(resp.usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(resp.usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(resp.usage, "total_tokens", 0) or 0,
        }
        if choice.finish_reason == "length" and not text and not calls:
            raise LLMError(
                f"Model {self.model!r} used the whole completion budget ({kwargs['max_completion_tokens']} tokens) "
                "without answering. Raise LLM_MAX_COMPLETION_TOKENS."
            )
        parsed = _parse_json(text) if json_schema is not None and text else None
        return LLMResponse(text=text, model=resp.model or self.model, provider=self.provider, usage=usage,
                           latency_ms=latency_ms, tool_calls=calls, parsed=parsed,
                           finish_reason=choice.finish_reason)


class FakeLLMClient(LLMClient):
    """Deterministic offline client.

    - ``responses``: a queue of scripted replies. Each item is a string, a dict
      (``{"text":..., "tool_calls": [{"name":..., "arguments":{...}}], "parsed": ...}``), or an LLMResponse.
    - ``handler``: alternatively a function ``(messages, tools, json_schema) -> str | dict``.
    - With neither, it replies with a short, stable echo of the last user message.

    Every call is recorded in ``calls`` so tests can inspect what was sent.
    """

    provider = "fake"

    def __init__(self, responses: list | None = None, handler: Callable | None = None, model: str = "fake-model"):
        self.model = model
        self._responses = list(responses or [])
        self._handler = handler
        self.calls: list[dict] = []

    def complete(self, messages, *, tools=None, json_schema=None, schema_name="result",
                 max_completion_tokens=None) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools, "json_schema": json_schema})
        if self._responses:
            reply = self._responses.pop(0)
        elif self._handler:
            reply = self._handler(messages, tools, json_schema)
        else:
            last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
            reply = f"[fake] received {len(messages)} messages; last user message: {last_user[:80]}"
            if json_schema is not None:
                reply = {"text": "{}", "parsed": {}}
        if isinstance(reply, LLMResponse):
            return reply
        if isinstance(reply, str):
            reply = {"text": reply}
        text = reply.get("text", "")
        calls = [ToolCall(id=c.get("id", f"call_{i}"), name=c["name"], arguments=c.get("arguments", {}))
                 for i, c in enumerate(reply.get("tool_calls", []))]
        parsed = reply.get("parsed")
        if json_schema is not None and parsed is None and text:
            parsed = _parse_json(text)
        prompt_tokens = sum(count_tokens(str(m.get("content") or "")) for m in messages)
        completion_tokens = count_tokens(text)
        return LLMResponse(text=text, model=self.model, provider=self.provider,
                           usage={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                                  "total_tokens": prompt_tokens + completion_tokens},
                           latency_ms=0.0, tool_calls=calls, parsed=parsed,
                           finish_reason="tool_calls" if calls else "stop")


def get_client(settings: LLMSettings | None = None) -> LLMClient:
    """Build the client named by LLM_PROVIDER (``openai`` or ``fake``)."""
    settings = settings or llm_settings(require_key=True)
    if settings.provider == "fake":
        return FakeLLMClient(model="fake-model")
    return OpenAIClient(settings)
