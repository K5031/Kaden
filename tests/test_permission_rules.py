"""Saved permission rules: what the user answered "always" or "never" to, in every
conversation from then on."""

from __future__ import annotations

from typing import Any

import pytest

from kadence.api import (
    AgentState,
    Decision,
    PermissionPolicy,
    PermissionRequested,
    PermissionRule,
    SettingsUpdate,
    ToolCall,
    ToolStatus,
)
from tests.conftest import Harness
from tests.fakes.core import ScriptedCore, Step, call
from tests.fakes.tools import FakeTools


@pytest.mark.parametrize(("tool", "arguments", "rule"), [
    ("shell", {"command": "git status -s"}, PermissionRule("shell", "git status")),
    ("shell", {"command": "ls -la"}, PermissionRule("shell", "ls")),
    ("shell", {"command": "npm run build"}, PermissionRule("shell", "npm run")),
    # What runs code of any kind is taken whole: "python" would allow everything.
    ("shell", {"command": "python tools/fix.py --all"},
     PermissionRule("shell", "python tools/fix.py --all")),
    ("web_fetch", {"url": "https://example.com"}, PermissionRule("web_fetch")),
])
def test_an_answer_becomes_a_rule_for_calls_like_it(tool, arguments, rule):
    assert PermissionRule.for_call(ToolCall("c", tool, arguments)) == rule


def test_a_rule_matches_what_it_says_and_nothing_chained_to_it():
    allow = PermissionRule("shell", "git status")
    assert allow.matches(ToolCall("c", "shell", {"command": "git status"}))
    assert allow.matches(ToolCall("c", "shell", {"command": "git status --short"}))
    assert not allow.matches(ToolCall("c", "shell", {"command": "git statusx"}))
    assert not allow.matches(ToolCall("c", "shell", {"command": "git status && rm -rf x"}))
    assert not allow.matches(ToolCall("c", "shell", {"command": "git status; curl evil"}))
    assert not allow.matches(ToolCall("c", "web_fetch", {"url": "git status"}))
    never = PermissionRule("shell", "rm -rf", allow=False)
    assert never.matches(ToolCall("c", "shell", {"command": "ls && rm -rf build"}))
    assert PermissionRule("web_fetch").matches(ToolCall("c", "web_fetch", {"url": "x"}))
    assert PermissionRule.parse(str(allow), allow=True) == allow  # as settings.toml keeps it


def shell_tools() -> FakeTools:
    tools = FakeTools()
    tools.instant("shell", read_only=False)
    return tools


async def answered(h: Harness, decision: Decision, seen: int = 0) -> None:
    requests = await h.rec.wait_for(lambda r: r.of(PermissionRequested)[seen:])
    await h.runtime.resolve_permission(requests[0].request.id, decision)


async def test_always_allow_holds_in_every_conversation_from_then_on(make: Any):
    core = ScriptedCore([
        Step(calls=[call("shell", {"command": "git status"}, id="a")]), Step(text="ok"),
        Step(calls=[call("shell", {"command": "git status -s"}, id="b"),
                    call("shell", {"command": "git push"}, id="c")]), Step(text="ok")])
    h: Harness = await make(core=core, tools=shell_tools(), permissions=PermissionPolicy.ASK)
    await h.start()
    await h.runtime.send("look")
    await answered(h, Decision.ALLOW_ALWAYS)
    await h.rec.wait_state(AgentState.IDLE, count=2)
    assert (await h.runtime.settings()).permission_rules == (PermissionRule("shell",
                                                                            "git status"),)
    await h.start()  # a new conversation
    await h.runtime.send("again")
    requests = await h.rec.wait_for(lambda r: r.of(PermissionRequested)[1:])
    assert [r.request.call.id for r in requests] == ["c"]  # "git push" still asks
    await h.runtime.resolve_permission(requests[0].request.id, Decision.DENY)
    await h.rec.wait_state(AgentState.IDLE, count=3)
    results = await h.results()
    assert results["b"].status == ToolStatus.OK and results["c"].status == ToolStatus.REFUSED


async def test_never_refuses_without_asking_and_rules_can_be_taken_back(make: Any):
    core = ScriptedCore([
        Step(calls=[call("shell", {"command": "rm -rf build"}, id="a")]), Step(text="ok"),
        Step(calls=[call("shell", {"command": "rm -rf dist"}, id="b")]), Step(text="ok"),
        Step(calls=[call("shell", {"command": "rm -rf dist"}, id="c")]), Step(text="ok")])
    h: Harness = await make(core=core, tools=shell_tools(), permissions=PermissionPolicy.ASK)
    await h.start()
    await h.runtime.send("clean")
    await answered(h, Decision.DENY_ALWAYS)
    await h.rec.wait_state(AgentState.IDLE, count=2)
    await h.runtime.send("clean again")
    await h.rec.wait_state(AgentState.IDLE, count=3)
    assert len(h.rec.of(PermissionRequested)) == 1  # the second was refused unasked
    assert (await h.results())["b"].status == ToolStatus.REFUSED
    s = await h.runtime.update_settings(SettingsUpdate(permission_rules=[]))
    assert s.permission_rules == ()
    await h.runtime.send("once more")
    await h.rec.wait_for(lambda r: r.of(PermissionRequested)[1:])  # it asks again


def test_saved_rules_are_read_back_and_a_wrong_one_is_named(tmp_path):
    from kadence.__main__ import read_saved

    path = tmp_path / "settings.toml"
    path.write_text('always_allow = ["shell: git status", "web_fetch"]\n'
                    'never_allow = ["shell: rm -rf"]\n')
    assert read_saved(path)["permission_rules"] == (
        PermissionRule("shell", "git status"), PermissionRule("web_fetch"),
        PermissionRule("shell", "rm -rf", allow=False))
    path.write_text("always_allow = [3]\n")
    with pytest.raises(SystemExit, match="always_allow"):
        read_saved(path)
