"""A Tools module that offers the tools of MCP servers (Model Context Protocol), through the
official `mcp` SDK.

Each server is a command Kaden starts (talking over its stdin and stdout) or a URL. Every
server runs in a task of its own that holds its connection, so it is opened and closed in
the same task, as the SDK requires. A server that fails to start is left out and logged;
the others still work. Tools are named `mcp__<server>__<tool>` so their source is visible and
names from different servers never clash.

A command gets a small environment (the SDK's safe default: PATH, HOME and the like), the
variables configured for it, PERMISSION_CEILING_ENV and PROTECTED_FOLDERS_ENV, so a Kaden
it launches cannot escape the ceiling or change the folders above it. No call is confined and no tool is read-only (a server's readOnlyHint
is its own claim): what a server does is unknown, so the runtime reviews or asks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.client.stdio import get_default_environment

from kaden.api import (
    PERMISSION_CEILING_ENV,
    PROTECTED_FOLDERS_ENV,
    InstancePaths,
    ToolCall,
    ToolContext,
    ToolOutput,
    Tools,
    ToolSpec,
)

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 30.0  # seconds for a server to start and list its tools
CLOSE_TIMEOUT = 5.0
MAX_NAME = 64  # what model APIs accept for a tool name
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """One server: `command` (a shell-style command line) or `url`, and its variables."""

    name: str
    command: str | None = None
    url: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)

    def target(self) -> StdioServerParameters | str:
        if self.url:
            return self.url
        if not self.command:
            raise ValueError(f"MCP server {self.name!r} needs a command or a URL")
        argv = shlex.split(self.command)
        env = {**get_default_environment(), **self.env}
        for name in (PERMISSION_CEILING_ENV, PROTECTED_FOLDERS_ENV):
            if (value := os.environ.get(name)) is not None:
                env[name] = value
        return StdioServerParameters(command=argv[0], args=argv[1:], env=env)


def factory(options: dict[str, Any], paths: InstancePaths) -> McpTools:
    """Options: servers, a list of {name, command or url, env}."""
    servers = [ServerConfig(s["name"], s.get("command"), s.get("url"), dict(s.get("env", {})))
               for s in options.get("servers", [])]
    return McpTools({s.name: s.target() for s in servers})


factory.display_name = "MCP servers"  # type: ignore[attr-defined]


@dataclass
class _Server:
    name: str
    target: Any  # what mcp.Client connects to
    client: Client | None = None
    tools: dict[str, ToolSpec] = field(default_factory=dict)  # by Kaden's name
    originals: dict[str, str] = field(default_factory=dict)  # Kaden's name -> the server's
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None


class McpTools(Tools):
    """`servers` maps a server name to what `mcp.Client` connects to: StdioServerParameters,
    a URL, or (in tests) a server object."""

    def __init__(self, servers: Mapping[str, Any]) -> None:
        self._servers = [_Server(name, target) for name, target in servers.items()]
        self._startup: asyncio.Task[None] | None = None
        self._closed = False
        self._owner: dict[str, _Server] = {}

    async def list_tools(self) -> Sequence[ToolSpec]:
        if self._closed:
            return []
        # One start for every listing, however many wait on it at once; a listing that
        # is cancelled does not end it.
        if self._startup is None:
            self._startup = asyncio.create_task(self._start())
        try:
            await asyncio.shield(self._startup)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise  # this listing was cancelled
            # else aclose() ended the start: what the servers have
        specs = []
        for server in self._servers:
            for name, spec in server.tools.items():
                self._owner[name] = server
                specs.append(spec)
        return specs

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolOutput:
        server = self._owner.get(call.name)
        if server is None:
            return ToolOutput(f"Unknown tool: {call.name}", is_error=True)
        if server.client is None:
            return ToolOutput(f"The MCP server {server.name!r} is not running.", is_error=True)
        result = await server.client.call_tool(server.originals[call.name], call.arguments)
        return ToolOutput(_text(result), is_error=bool(result.is_error))

    async def _start(self) -> None:
        """Every server at once, then up to CONNECT_TIMEOUT for them to list their tools."""
        for server in self._servers:
            server.task = asyncio.create_task(self._serve(server))
        waiters = [asyncio.create_task(s.ready.wait()) for s in self._servers]
        if waiters:
            _, late = await asyncio.wait(waiters, timeout=CONNECT_TIMEOUT)
            for waiter in late:
                waiter.cancel()
        for server in self._servers:
            if not server.ready.is_set():
                logger.warning("MCP server %s did not start in %ss", server.name,
                               CONNECT_TIMEOUT)

    async def aclose(self) -> None:
        self._closed = True
        if self._startup is not None and not self._startup.done():
            self._startup.cancel()
            await asyncio.gather(self._startup, return_exceptions=True)
        for server in self._servers:
            server.stop.set()
        tasks = [s.task for s in self._servers if s.task is not None]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=CLOSE_TIMEOUT)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def _serve(self, server: _Server) -> None:
        """Open the connection, list the tools, hold it until aclose(), close it here."""
        try:
            async with Client(server.target) as client:
                listed, cursor = [], None
                while True:
                    page = await client.list_tools(cursor=cursor)
                    listed += page.tools
                    if not (cursor := page.next_cursor):
                        break
                for tool in listed:
                    name = _name(server.name, tool.name)
                    if name in server.tools:  # two long names cut to the same one
                        logger.warning("MCP server %s: the tool %s is left out, its name "
                                       "is taken by %s", server.name, tool.name,
                                       server.originals[name])
                        continue
                    server.originals[name] = tool.name
                    # Never read-only, whatever its readOnlyHint says: the server is a
                    # program of its own, so the runtime reviews or asks.
                    server.tools[name] = ToolSpec(
                        name, _describe(server.name, tool), tool.input_schema or {"type": "object"},
                        deferrable=True, title=_title(tool))
                server.client = client
                server.ready.set()
                await server.stop.wait()
        except Exception:
            logger.exception("MCP server %s failed", server.name)
        finally:  # its tools go with its connection
            server.client = None
            server.tools.clear()
            server.ready.set()


def _name(server: str, tool: str) -> str:
    return _UNSAFE.sub("_", f"mcp__{server}__{tool}")[:MAX_NAME]


def _title(tool: Any) -> str | None:
    """The tool's name for people: its own title, else its annotations' (MCP has both)."""
    annotations = getattr(tool, "annotations", None)
    return tool.title or getattr(annotations, "title", None) or None


def _describe(server: str, tool: Any) -> str:
    text = tool.description or tool.title or tool.name
    return f"{text} (from the MCP server {server!r})"


def _text(result: Any) -> str:
    parts = []
    for item in result.content:
        if getattr(item, "type", None) == "text":
            parts.append(item.text)
        else:
            parts.append(f"[{getattr(item, 'type', 'unknown')} content]")
    if not parts and result.structured_content is not None:
        parts.append(json.dumps(result.structured_content, ensure_ascii=False, indent=1))
    return "\n".join(parts)
