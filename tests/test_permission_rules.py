"""Saved permission rules: what the user answered "always" or "never" to, in every
conversation from then on."""

from __future__ import annotations

from typing import Any

import pytest

from kaden.api import (
    AgentState,
    Decision,
    PermissionPolicy,
    PermissionRule,
    SettingsUpdate,
    ToolCall,
    ToolStatus,
)
from tests.conftest import Harness, asked
from tests.fakes.core import ScriptedCore, Step, call
from tests.fakes.tools import FakeTools


@pytest.mark.parametrize(("tool", "arguments", "rule"), [
    ("Bash", {"command": "git status -s"}, PermissionRule("Bash", "git status")),
    ("Bash", {"command": "ls -la"}, PermissionRule("Bash", "ls")),
    ("Bash", {"command": "npm run build"}, PermissionRule("Bash", "npm run")),
    # What runs code of any kind is taken whole: "python" would allow everything.
    ("Bash", {"command": "python tools/fix.py --all"},
     PermissionRule("Bash", "python tools/fix.py --all")),
    ("WebFetch", {"url": "https://example.com"}, PermissionRule("WebFetch")),
])
def test_an_answer_becomes_a_rule_for_calls_like_it(tool, arguments, rule):
    assert PermissionRule.for_call(ToolCall("c", tool, arguments)) == rule


def test_a_rule_matches_what_it_says_and_nothing_chained_to_it():
    allow = PermissionRule("Bash", "git status")
    assert allow.matches(ToolCall("c", "Bash", {"command": "git status"}))
    assert allow.matches(ToolCall("c", "Bash", {"command": "git status --short"}))
    assert not allow.matches(ToolCall("c", "Bash", {"command": "git statusx"}))
    assert not allow.matches(ToolCall("c", "Bash", {"command": "git status && rm -rf x"}))
    assert not allow.matches(ToolCall("c", "Bash", {"command": "git status; curl evil"}))
    assert not allow.matches(ToolCall("c", "WebFetch", {"url": "git status"}))
    never = PermissionRule("Bash", "rm -rf", allow=False)
    assert never.matches(ToolCall("c", "Bash", {"command": "ls && rm -rf build"}))
    assert PermissionRule("WebFetch").matches(ToolCall("c", "WebFetch", {"url": "x"}))
    assert PermissionRule.parse(str(allow), allow=True) == allow  # as settings.toml keeps it


def shell_tools() -> FakeTools:
    tools = FakeTools()
    tools.instant("Bash", read_only=False)
    return tools


async def answered(h: Harness, decision: Decision, seen: int = 0) -> None:
    requests = await h.rec.wait_for(lambda r: asked(r)[seen:])
    await h.runtime.respond(requests[0].id, decision)


async def test_always_allow_holds_in_every_conversation_from_then_on(make: Any):
    core = ScriptedCore([
        Step(calls=[call("Bash", {"command": "git status"}, id="a")]), Step(text="ok"),
        Step(calls=[call("Bash", {"command": "git status -s"}, id="b"),
                    call("Bash", {"command": "git push"}, id="c")]), Step(text="ok")])
    h: Harness = await make(core=core, tools=shell_tools(), permissions=PermissionPolicy.ASK)
    await h.start()
    await h.runtime.send("look")
    await answered(h, Decision.ALLOW_ALWAYS)
    await h.rec.wait_state(AgentState.IDLE, count=2)
    assert (await h.runtime.settings()).permission_rules == (PermissionRule("Bash",
                                                                            "git status"),)
    await h.start()  # a new conversation
    await h.runtime.send("again")
    requests = await h.rec.wait_for(lambda r: asked(r)[1:])
    assert [r.call.id for r in requests] == ["c"]  # "git push" still asks
    await h.runtime.respond(requests[0].id, Decision.DENY)
    await h.rec.wait_state(AgentState.IDLE, count=3)
    results = await h.results()
    assert results["b"].status == ToolStatus.OK and results["c"].status == ToolStatus.REFUSED


async def test_never_refuses_without_asking_and_rules_can_be_taken_back(make: Any):
    core = ScriptedCore([
        Step(calls=[call("Bash", {"command": "rm -rf build"}, id="a")]), Step(text="ok"),
        Step(calls=[call("Bash", {"command": "rm -rf dist"}, id="b")]), Step(text="ok"),
        Step(calls=[call("Bash", {"command": "rm -rf dist"}, id="c")]), Step(text="ok")])
    h: Harness = await make(core=core, tools=shell_tools(), permissions=PermissionPolicy.ASK)
    await h.start()
    await h.runtime.send("clean")
    await answered(h, Decision.DENY_ALWAYS)
    await h.rec.wait_state(AgentState.IDLE, count=2)
    await h.runtime.send("clean again")
    await h.rec.wait_state(AgentState.IDLE, count=3)
    assert len(asked(h.rec)) == 1  # the second was refused unasked
    assert (await h.results())["b"].status == ToolStatus.REFUSED
    s = await h.runtime.update_settings(SettingsUpdate(permission_rules=[]))
    assert s.permission_rules == ()
    await h.runtime.send("once more")
    await h.rec.wait_for(lambda r: asked(r)[1:])  # it asks again


def test_saved_rules_are_read_back_and_a_wrong_one_is_named(tmp_path):
    from kaden.__main__ import read_saved

    path = tmp_path / "settings.toml"
    path.write_text('always_allow = ["Bash: git status", "WebFetch"]\n'
                    'never_allow = ["Bash: rm -rf"]\n')
    assert read_saved(path)["permission_rules"] == (
        PermissionRule("Bash", "git status"), PermissionRule("WebFetch"),
        PermissionRule("Bash", "rm -rf", allow=False))
    path.write_text("always_allow = [3]\n")
    with pytest.raises(SystemExit, match="always_allow"):
        read_saved(path)
