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
    assert PermissionRule.for_calls(ToolCall("c", tool, arguments)) == (rule,)


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


def bash(command: str) -> ToolCall:
    return ToolCall("c", "Bash", {"command": command})


def saved(command: str, have: tuple[PermissionRule, ...] = ()) -> list[str]:
    return [str(r) for r in PermissionRule.for_calls(bash(command), have=have)]


def test_always_allow_on_a_chain_keeps_each_part():
    """As Claude Code: one answer per part of a chain, at most five, the parts already
    allowed left out."""
    assert saved("cp /tmp/a.png b.png && echo x >> /dev/null && ls -la b.png") == [
        "Bash: cp", "Bash: echo", "Bash: ls"]
    assert saved("git status | head -5", have=(PermissionRule("Bash", "head"),)) == [
        "Bash: git status"]
    assert saved("a; b; c; d; e; f") == []  # too many to keep: no "always"
    # Programs that run what they are given are kept whole, as before.
    assert saved("find . -name x -exec rm {} +") == ["Bash: find . -name x -exec rm {} +"]
    assert saved("git -C /tmp/x push") == ["Bash: git -C /tmp/x push"]


@pytest.mark.parametrize("command", [
    "ls\nrm -rf x",  # a newline is a second command
    "git status#; rm -rf x",  # a comment hides the rest from a reader, not from bash
    "echo $(rm -rf x)", "echo `rm -rf x`", "(rm -rf x)", "cat <(rm -rf x)",
    "echo 'unclosed",  # bash runs what comes before the error
    "FOO=1 rm -rf x",  # what a variable does to a program is not read
    "cat <<EOF\nx\nEOF",
])
def test_what_cannot_be_read_with_certainty_is_not_kept_or_covered(command):
    assert saved(command) == []
    everything = tuple(PermissionRule("Bash", p) for p in ("ls", "git status", "echo", "cat",
                                                           "rm", "FOO=1 rm"))
    assert not PermissionRule.allows(everything, bash(command))


def test_a_chain_runs_unasked_only_when_every_part_is_allowed():
    rules = (PermissionRule("Bash", "cp"), PermissionRule("Bash", "ls"))
    assert PermissionRule.allows(rules, bash("cp c d && ls"))
    assert PermissionRule.allows(rules, bash("timeout 5 ls -la"))  # a wrapper, skipped
    assert not PermissionRule.allows(rules, bash("cp c d && rm -rf x"))
    assert not PermissionRule.allows(rules, bash("cp c d > notes.md"))  # where it writes
    assert PermissionRule.allows(rules, bash("cp c d 2>&1 >/dev/null"))
    assert not PermissionRule.allows(rules, bash("nice -n 5 ls"))  # a wrapper's options
    assert PermissionRule.allows((PermissionRule("Bash", "python fix.py --all"),),
                                 bash("python fix.py --all"))  # kept whole, matched whole
    assert not PermissionRule.allows((PermissionRule("Bash", "python fix.py"),),
                                     bash("python fix.py && rm x"))


def test_never_refuses_a_part_wherever_it_is():
    never = PermissionRule("Bash", "rm", allow=False)
    assert never.matches(bash("ls && rm -rf build"))
    assert never.matches(bash("echo $(rm -rf x)"))
    # "Never allow" on a chain keeps the whole of it: not each part forever.
    assert [str(r) for r in PermissionRule.for_calls(bash("cp a b && ls"), allow=False)] == [
        "Bash: cp a b && ls"]
