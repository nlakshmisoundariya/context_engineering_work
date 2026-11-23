"""Tools as context (CAPSTONE_ARCHITECTURE §10): an MCP client for the agent, and tool definitions as a Context Source.

    from novaassist.tools_mcp import MCPToolClient, ToolDefinitionsSource
    client = MCPToolClient()                      # starts `python -m mcp_server.server` over stdio
    defs = client.list_tools()                    # name, description, input_schema, context_cost
    result = client.call("entitlement_check", {"customer_id": "CUST-0002"})

``ToolDefinitionsSource`` offers *every* tool definition as a candidate — the Module 6 anti-pattern baseline.
Your dynamic tool selector replaces it (or filters its candidates). Tool results reach the window through the
orchestrator's tool loop and its ``result_manager`` (raw pass-through until you write one).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path

from .context_items import ContextItem

KIT_ROOT = Path(__file__).resolve().parents[1]


class MCPToolClient:
    """Synchronous wrapper: one MCP session kept open on a background event loop."""

    def __init__(self, server=None, timeout: float = 60.0):
        """``server``: None to launch the stdio server subprocess, or an MCPServer object to connect in-process."""
        self.timeout = timeout
        self._target = server
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._queue = None
        self._session_task = asyncio.run_coroutine_threadsafe(self._session(), self._loop)
        self._ready.result(self.timeout)

    @property
    def _ready(self):
        if not hasattr(self, "_ready_future"):
            import concurrent.futures

            self._ready_future = concurrent.futures.Future()
        return self._ready_future

    async def _session(self):
        """One task owns the MCP session for its whole life (anyio requires open and close in the same task)."""
        from mcp import Client, StdioServerParameters

        target = self._target
        if target is None:
            env = dict(os.environ)
            env["PYTHONPATH"] = str(KIT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            target = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"], cwd=str(KIT_ROOT), env=env)
        self._queue = asyncio.Queue()
        try:
            async with Client(target) as client:
                self._ready.set_result(True)
                while True:
                    job = await self._queue.get()
                    if job is None:
                        break
                    make_coro, future = job
                    try:
                        future.set_result(await make_coro(client))
                    except Exception as exc:  # noqa: BLE001 — hand the error to the caller
                        future.set_exception(exc)
        except Exception as exc:  # noqa: BLE001
            if not self._ready.done():
                self._ready.set_exception(exc)
            raise

    def _run(self, make_coro):
        async def submit():
            future = self._loop.create_future()
            await self._queue.put((make_coro, future))
            return await future

        return asyncio.run_coroutine_threadsafe(submit(), self._loop).result(self.timeout)

    def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        while True:  # the tool list is paginated
            result = self._run(lambda c, cur=cursor: c.list_tools(cursor=cur))
            tools += result.tools
            cursor = getattr(result, "next_cursor", None)
            if not cursor:
                break
        return [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema,
                 "context_cost": (t.meta or {}).get("context_cost", {})} for t in tools]

    def call(self, name: str, arguments: dict) -> dict:
        """Call a tool. Returns {"ok": bool, "result": <structured result or text>}."""
        result = self._run(lambda c: c.call_tool(name, arguments))
        if result.structured_content is not None:
            payload = result.structured_content
            payload = payload.get("result", payload) if isinstance(payload, dict) and set(payload) == {"result"} else payload
        else:
            payload = "\n".join(getattr(c, "text", "") for c in result.content)
            try:
                payload = json.loads(payload)  # tools return JSON objects as text content
            except json.JSONDecodeError:
                pass  # error messages stay plain text
        return {"ok": not result.is_error, "result": payload}

    def close(self) -> None:
        if self._queue is not None:
            asyncio.run_coroutine_threadsafe(self._queue.put(None), self._loop).result(self.timeout)
            try:
                self._session_task.result(self.timeout)
            finally:
                self._queue = None
        self._loop.call_soon_threadsafe(self._loop.stop)


def definition_text(tool: dict) -> str:
    return json.dumps({"name": tool["name"], "description": tool["description"], "input_schema": tool["input_schema"]},
                      ensure_ascii=False)


class ToolDefinitionsSource:
    """Every tool definition, every request (the M6 baseline to measure your selector against)."""

    name = "mcp_registry"
    channel = "tool_definitions"

    def __init__(self, client: MCPToolClient):
        self.tools = client.list_tools()

    def gather(self, request, session, spec) -> list[ContextItem]:
        today = spec.today if spec else request.timestamp
        return [ContextItem(item_id=f"tool:{t['name']}", channel=self.channel, content=definition_text(t), source=self.name,
                            provenance={"type": "tool_definition", "ref": t["name"]}, timestamp=today,
                            metadata={"tool": {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]},
                                      "context_cost": t["context_cost"]})
                for t in self.tools]


def raw_result_manager(item: ContextItem, spec) -> tuple[ContextItem, str]:
    """The anti-pattern default: keep the raw tool result unchanged. Returns (item, retention decision)."""
    return item, "raw"
