"""The MCP Tools module: tools of MCP servers, named by server, called through the SDK."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from kaden.api import PERMISSION_CEILING_ENV, InstancePaths, ToolCall
from kaden.tools.mcp import McpTools, ServerConfig, factory
from tests.conformance.tools_suite import RecordingContext
from tests.fakes.mcp_server import build


@pytest.fixture
async def tools() -> Any:
    module = McpTools({"notes": build()})
    yield module
    await module.aclose()


async def run(tools: McpTools, tool: str, **args: Any):
    return await tools.execute(ToolCall("c", tool, args), RecordingContext("c"))


async def test_tools_are_named_by_server(tools):
    specs = {s.name: s for s in await tools.list_tools()}
    assert set(specs) >= {"mcp__notes__add", "mcp__notes__broken", "mcp__notes__env"}
    add = specs["mcp__notes__add"]
    assert "Add two numbers" in add.description and "notes" in add.description
    assert add.parameters["properties"].keys() == {"a", "b"}


async def test_a_tools_title_is_the_servers_own(tools):
    """Frontends name a step by it (common.tool_sentence); a tool without one has none."""
    specs = {s.name: s for s in await tools.list_tools()}
    assert specs["mcp__notes__add"].title == "Add numbers"
    assert specs["mcp__notes__broken"].title is None


async def test_every_listing_waits_for_the_servers_to_start():
    """The runtime may list while an earlier listing still waits for the servers (one
    made as a conversation opens): each gets the servers' tools, even if the first is
    cancelled meanwhile."""
    import asyncio

    module = McpTools({"notes": build()})
    try:
        first = asyncio.create_task(module.list_tools())
        await asyncio.sleep(0)
        second = asyncio.create_task(module.list_tools())
        await asyncio.sleep(0)
        first.cancel()
        names = {s.name for s in await second}
        assert "mcp__notes__add" in names
    finally:
        await module.aclose()


async def test_a_listing_while_closing_returns():
    """Closing during the servers' start ends it; a listing waiting on it, or made after,
    returns what there is rather than raising a cancellation nobody asked for."""
    import asyncio

    module = McpTools({"notes": build()})
    waiting = asyncio.create_task(module.list_tools())
    await asyncio.sleep(0)
    await module.aclose()
    assert isinstance(await waiting, list)
    assert await module.list_tools() == []


async def test_a_listing_after_closing_starts_no_server():
    module = McpTools({"notes": build()})
    await module.aclose()
    assert await module.list_tools() == []
    assert all(server.task is None for server in module._servers)


async def test_a_servers_read_only_hint_is_not_taken_on_trust(tools):
    """`add` says it is read-only; the server is a program of its own, so its calls are
    reviewed or asked about all the same."""
    assert not any(spec.read_only for spec in await tools.list_tools())


async def test_closing_waits_for_every_server_to_end(monkeypatch):
    import asyncio

    from kaden.tools import mcp

    monkeypatch.setattr(mcp, "CLOSE_TIMEOUT", 0.05)
    module = McpTools({"notes": build()})
    await module.list_tools()
    [server] = module._servers
    ended = asyncio.Event()

    async def slow_to_end() -> None:
        try:
            await asyncio.sleep(3600)
        finally:  # cancelled, it still tidies up for a moment
            await asyncio.sleep(0.05)
            ended.set()

    running, server.task = server.task, asyncio.create_task(slow_to_end())
    await asyncio.sleep(0)
    await module.aclose()
    assert ended.is_set() and server.task.done()
    server.stop.set()
    await running


async def test_calls_return_text_and_errors(tools):
    await tools.list_tools()
    out = await run(tools, "mcp__notes__add", a=2, b=3)
    assert out.content == "5" and not out.is_error
    out = await run(tools, "mcp__notes__broken")
    assert out.is_error and "broken" in out.content  # the SDK hides the server's own error


async def test_no_call_is_confined(tools):
    await tools.list_tools()
    assert not await tools.confined(ToolCall("c", "mcp__notes__add", {"a": 1, "b": 1}))


async def test_a_server_that_fails_is_left_out():
    module = McpTools({"gone": ServerConfig("gone", "kaden-no-such-command").target(),
                       "notes": build()})
    try:
        names = {s.name for s in await module.list_tools()}
        assert names and all(n.startswith("mcp__notes__") for n in names)
    finally:
        await module.aclose()


async def test_a_server_that_ended_offers_no_tools_any_more(tools):
    """Its tools go with its connection: they are not offered, and a call still on its
    way to one is told why."""
    import asyncio

    assert await tools.list_tools()
    [server] = tools._servers
    server.task.cancel()  # its connection ends
    await asyncio.gather(server.task, return_exceptions=True)
    assert await tools.list_tools() == []
    out = await run(tools, "mcp__notes__add", a=2, b=3)
    assert out.is_error and "notes" in out.content and "not running" in out.content


def test_a_server_needs_a_command_or_url():
    with pytest.raises(ValueError, match="command or a URL"):
        ServerConfig("x").target()


async def test_a_command_server_gets_its_variables_and_the_ceiling(tmp_path, monkeypatch):
    monkeypatch.setenv(PERMISSION_CEILING_ENV, "ask")
    monkeypatch.setenv("KADEN_TEST_SECRET", "leaked")
    module = factory({"servers": [{
        "name": "notes", "command": f"{sys.executable} -m tests.fakes.mcp_server",
        "env": {"GREETING": "hello"}}]}, InstancePaths(tmp_path))
    try:
        assert await module.list_tools()
        assert (await run(module, "mcp__notes__env", name="GREETING")).content == "hello"
        assert (await run(module, "mcp__notes__env", name=PERMISSION_CEILING_ENV)).content == "ask"
        assert (await run(module, "mcp__notes__env", name="KADEN_TEST_SECRET")).content == (
            "(unset)")  # only the safe default environment, not Kaden's
    finally:
        await module.aclose()
