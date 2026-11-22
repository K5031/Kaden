"""ToolSearch: past a size, tool servers' tools are offered by name and loaded on demand."""

from __future__ import annotations

from typing import Any

import pytest

from kadence.api import ToolOutput, ToolSpec, ToolStatus
from kadence.runtime import orchestrator
from tests.conftest import Harness
from tests.fakes.core import ScriptedCore, Step, call
from tests.fakes.tools import FakeTools


def served(tools: FakeTools, *names: str) -> None:
    async def run(c: Any, ctx: Any) -> ToolOutput:
        return ToolOutput(f"{c.name} ran")

    for name in names:
        tools.add(ToolSpec(name, f"{name.replace('_', ' ')} on the server",
                           {"type": "object", "properties": {"q": {"type": "string"}}},
                           True, deferrable=True), run)


@pytest.fixture
def small_limit(monkeypatch: Any) -> None:
    monkeypatch.setattr(orchestrator, "DEFER_OVER_CHARS", 100)


async def test_few_server_tools_are_all_offered(make: Any) -> None:
    tools = FakeTools()
    served(tools, "github_create_issue")
    core = ScriptedCore([Step(text="hi")])
    h: Harness = await make(core=core, tools=tools)
    await h.start()
    await h.turn("go")
    names = {t.name for t in core.requests[0].tools}
    assert "github_create_issue" in names and "ToolSearch" not in names


async def test_many_are_listed_by_name_and_loaded_by_a_search(make: Any, small_limit: None
                                                             ) -> None:
    tools = FakeTools()
    served(tools, "github_create_issue", "github_list_issues", "slack_send")
    tools.instant("local")  # a tool that is not deferrable stays offered
    core = ScriptedCore([
        Step(calls=[call("ToolSearch", {"query": "select:slack_send"}, id="s")]),
        Step(calls=[call("ToolSearch", {"query": "github issue", "max_results": 1}, id="k")]),
        Step(calls=[call("slack_send", {"q": "hi"}, id="u")]),
        Step(text="sent")])
    h: Harness = await make(core=core, tools=tools)
    await h.start()
    await h.turn("go")
    first = core.requests[0]
    assert {"ToolSearch", "local"} <= {t.name for t in first.tools}
    assert not {"slack_send", "github_create_issue"} & {t.name for t in first.tools}
    assert "slack_send" in first.system and "ToolSearch" in first.system
    results = await h.results()
    assert '"name": "slack_send"' in results["s"].content
    assert results["k"].content.count('"name": "github_') == 1
    later = {t.name for t in core.requests[2].tools}
    assert "slack_send" in later and len({n for n in later if n.startswith("github_")}) == 1
    assert results["u"].status == ToolStatus.OK and results["u"].content == "slack_send ran"
    assert "slack_send" not in core.requests[2].system.split("ToolSearch")[-1]


async def test_a_deferred_tool_called_without_a_search_still_runs(make: Any,
                                                                 small_limit: None) -> None:
    tools = FakeTools()
    served(tools, "github_create_issue", "slack_send")
    core = ScriptedCore([Step(calls=[call("slack_send", {"q": "hi"}, id="u")]), Step(text="ok")])
    h: Harness = await make(core=core, tools=tools)
    await h.start()
    await h.turn("go")
    assert (await h.results())["u"].content == "slack_send ran"
    assert "slack_send" in {t.name for t in core.requests[1].tools}


async def test_a_loaded_tool_can_be_searched_for_again(make: Any, small_limit: None) -> None:
    tools = FakeTools()
    served(tools, "github_create_issue", "slack_send")
    core = ScriptedCore([
        Step(calls=[call("ToolSearch", {"query": "select:slack_send"}, id="a")]),
        Step(calls=[call("ToolSearch", {"query": "select:slack_send"}, id="b"),
                    call("ToolSearch", {"query": "slack"}, id="c")]),
        Step(text="ok")])
    h: Harness = await make(core=core, tools=tools)
    await h.start()
    await h.turn("go")
    results = await h.results()
    assert '"name": "slack_send"' in results["b"].content
    assert '"name": "slack_send"' in results["c"].content
