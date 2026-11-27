"""Sub-sessions: the frontend hosts children, headless Kaden processes the agent starts
for self-contained tasks; each reports back and can be watched while it works."""

from __future__ import annotations

import asyncio
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import tomli_w

from kaden.api import AgentState, OperationResult, PermissionPolicy, ToolResult, ToolStatus
from kaden.frontends.sessions import SubSessions
from kaden.runtime import InstanceConfig, ModuleSpec, RuntimeConfig, run_instance
from tests.conftest import Harness, eventually
from tests.fakes.core import ScriptedCore, Step, call

ROOT = Path(__file__).resolve().parents[1]


def finish(report: str) -> dict[str, Any]:
    return {"calls": [{"name": "finish_goal", "arguments": {"report": report}}]}


async def parent(make: Any, tmp_path: Path, steps: list[Step], child_script: list[Any],
                 child_runtime: dict[str, Any] | None = None, folder: Path | None = None
                 ) -> tuple[Harness, SubSessions]:
    data = tmp_path / "parent"
    data.mkdir()
    (data / "config.toml").write_text(tomli_w.dumps({
        "core": {"factory": "tests.fakes.core:factory", "options": {"script": child_script}},
        "tools": {"factory": "tests.fakes.tools:factory", "options": {"instant": ["lookup"]}},
        **(child_runtime or {})}))
    sessions = SubSessions(data, check_in_s=(0.05, 0.2))
    h: Harness = await make(core=ScriptedCore(steps), data_dir=data, more_tools=[sessions],
                            permissions=PermissionPolicy.ASK)
    h.auto_approve()
    await h.start(folder=str(folder or tmp_path))
    return h, sessions


def outcome(transcript: list[Any], call_id: str) -> OperationResult | ToolResult | None:
    """The call's final result, whether it finished in the foreground or the background."""
    return next((b for m in transcript for b in m.blocks
                 if isinstance(b, OperationResult | ToolResult) and b.call_id == call_id
                 and b.status != ToolStatus.BACKGROUNDED), None)


async def test_a_child_works_on_its_task_and_reports_back(make, tmp_path, child_env):
    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Write a haiku", "prompt": "Write a haiku", "run_in_background": True, "budget_usd": 0.5},
                         id="s")]),
        Step(text="A sub-session is on it."),
        Step(text="Here is the haiku.")],
        [{"text": "Drafting."}, finish("Haiku written."), {"text": "Done."}])
    await h.turn("Get me a haiku")
    result = await eventually(lambda: outcome(h.store.conversations[h.conversation.id]
                                              .messages, "s"), timeout=30)
    assert result.status == ToolStatus.OK and "Haiku written." in result.content
    [child] = sessions.sessions
    assert child.task == "Write a haiku" and child.state == "done"
    assert child.conversation_id == h.conversation.id
    assert any("Drafting." in str(m.blocks) for m in child.messages)
    settings = tomllib.loads((child.data_dir / "settings.toml").read_text())
    assert settings["budget"] == {"max_cost_usd": 0.5}
    assert settings["work_folder"] == str(tmp_path)
    await eventually(lambda: child.returncode is not None, timeout=10)  # it quit


async def test_a_question_comes_back_and_an_answer_lets_it_finish(make, tmp_path, child_env):
    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Write a poem", "prompt": "Write a poem", "run_in_background": True}, id="s")]),
        Step(text="Started."),
        Step(calls=[call("SendMessage", {"to": "1", "message": "A sonnet."}, id="m")]),
        Step(text="Answered."),
        Step(text="Got the sonnet.")],
        [{"calls": [{"name": "ask_for_help", "arguments": {"question": "Which form?"}}]},
         {"text": "Waiting."}, finish("Wrote a sonnet."), {"text": "Done."}])
    await h.turn("Get me a poem")
    asked = await eventually(lambda: outcome(h.store.conversations[h.conversation.id]
                                             .messages, "s"), timeout=30)
    assert "Which form?" in asked.content and "SendMessage" in asked.content
    answered = await eventually(lambda: outcome(h.store.conversations[h.conversation.id]
                                                .messages, "m"), timeout=30)
    assert "Wrote a sonnet." in answered.content
    assert sessions.sessions[0].state == "done"


async def test_cancelling_the_call_stops_the_child(make, tmp_path, child_env):
    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Think forever", "prompt": "Think forever", "run_in_background": True}, id="s")]),
        Step(text="Started.")],
        [{"hang": True}])
    await h.turn("Go")
    await eventually(lambda: sessions.sessions and sessions.sessions[0].state == "working",
                     timeout=30)
    child = sessions.sessions[0]
    await h.runtime.cancel_operation("s")
    await eventually(lambda: child.state == "stopped", timeout=15)
    assert child.live is None and child.agent_state is None  # no longer listened to
    await eventually(lambda: child.returncode is not None, timeout=15)
    assert child.state == "stopped"


async def test_an_answer_given_where_the_child_is_open_clears_its_question(
        make, tmp_path, child_env):
    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Write a poem", "prompt": "Write a poem", "run_in_background": True}, id="s")]),
        Step(text="Started."), Step(text="It asks."), Step(text="Noted.")],
        [{"calls": [{"name": "ask_for_help", "arguments": {"question": "Which form?"}}]},
         {"text": "Waiting."}, {"text": "A sonnet, then."}, finish("Wrote a sonnet."),
         {"text": "Done."}])
    await h.turn("Get me a poem")
    child = await eventually(lambda: sessions.sessions and sessions.sessions[0].state
                             == "waiting" and sessions.sessions[0], timeout=30)
    assert child.question == "Which form?" and child.has_goal
    remote = await sessions.open(child)
    await remote.send("A sonnet.")  # the user answers it directly
    await eventually(lambda: child.question is None, timeout=20)
    await eventually(lambda: child.state == "done", timeout=30)  # it went on to its goal
    assert child.report == "Wrote a sonnet." and not child.has_goal
    sessions.release(child, remote)


async def test_a_childs_permission_prompt_waits_for_the_user(make, tmp_path, child_env):
    from kaden.api import Decision, Refusal, RuntimeStateError

    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Look it up", "prompt": "Look it up", "run_in_background": True}, id="s")]),
        Step(text="Started."), Step(text="Reported.")],
        [{"calls": [{"name": "lookup"}]}, finish("Looked it up."), {"text": "Done."}],
        child_runtime={"runtime": {"permissions": "ask"}})
    await h.turn("Go")
    child = await eventually(lambda: sessions.sessions and sessions.sessions[0].approvals
                             and sessions.sessions[0], timeout=30)
    assert child.summary.startswith("Needs your approval")
    remote = await sessions.open(child)
    [request] = remote.snapshot().prompts
    assert request.call.name == "lookup"
    await remote.respond(request.id, Decision.ALLOW_ONCE)
    await eventually(lambda: not child.approvals, timeout=10)
    with pytest.raises(RuntimeStateError) as again:  # the child's refusal, with its reason
        await remote.respond(request.id, Decision.ALLOW_ONCE)
    assert again.value.reason is Refusal.NOT_PENDING
    result = await eventually(lambda: outcome(h.store.conversations[h.conversation.id]
                                              .messages, "s"), timeout=30)
    assert "Looked it up." in result.content
    sessions.release(child, remote)


async def test_a_child_is_listed_after_a_restart_and_opens_again(make, tmp_path, child_env):
    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Write a haiku", "prompt": "Write a haiku", "run_in_background": True}, id="s")]),
        Step(text="Started."), Step(text="Got it.")],
        [finish("Haiku written."), {"text": "Done."}])
    await h.turn("Go")
    await eventually(lambda: sessions.sessions and sessions.sessions[0].state == "done",
                     timeout=30)
    await h.close()
    again = SubSessions(h.data_dir)
    [child] = again.sessions
    assert (child.task, child.state, child.report) == ("Write a haiku", "done",
                                                       "Haiku written.")
    remote = await again.open(child)  # starts it again on its conversation
    history = await remote.history(child.child_conversation_id)
    assert any("Done." in str(m.blocks) for m in history)
    assert remote.snapshot().conversation.id == child.child_conversation_id
    again.release(child, remote)
    await again.aclose()
    assert child.returncode is not None


async def test_unknown_sessions_are_errors(make, tmp_path, child_env):
    h, _ = await parent(make, tmp_path, [
        Step(calls=[call("SendMessage", {"to": "9", "message": "hi"}, id="m")]),
        Step(text="ok")], [])
    await h.turn("Go")
    assert (await h.results())["m"].status == ToolStatus.ERROR


async def test_a_frontend_offers_its_tools(tmp_path, clean_env):
    from tests.fakes.tools import FakeTools

    extra = FakeTools()
    extra.instant("from_the_frontend")
    seen: list[str] = []

    async def script(runtime, frontend) -> None:
        seen.extend(t.name for t in await runtime.list_tools())

    import tests.fakes.frontend as fakes

    original = fakes.factory

    def factory(options, paths):
        return original({**options, "script": script, "tools": [extra]}, paths)

    fakes.tools_factory = factory  # type: ignore[attr-defined]
    try:
        await run_instance(InstanceConfig(
            runtime=RuntimeConfig(data_dir=tmp_path / "d"),
            core=ModuleSpec("tests.fakes.core:factory"),
            memory=ModuleSpec("tests.fakes.memory:factory"),
            tools=ModuleSpec("tests.fakes.tools:factory", {"instant": ["lookup"]}),
            frontend=ModuleSpec("tests.fakes.frontend:tools_factory")))
    finally:
        del fakes.tools_factory  # type: ignore[attr-defined]
    assert seen.index("from_the_frontend") > seen.index("lookup")
    assert extra.closed


async def test_a_cancelled_open_leaves_the_child_running(make, tmp_path, child_env):
    import asyncio

    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Think forever", "prompt": "Think forever", "run_in_background": True}, id="s")]),
        Step(text="Started.")],
        [{"hang": True}])
    await h.turn("Go")
    child = await eventually(lambda: sessions.sessions and sessions.sessions[0].agent_state
                             == AgentState.THINKING and sessions.sessions[0], timeout=30)
    opening = asyncio.create_task(sessions.open(child))
    await asyncio.sleep(0)
    opening.cancel()
    await asyncio.gather(opening, return_exceptions=True)
    remote = await sessions.open(child)  # the reply to the cancelled request is ignored
    assert remote.snapshot().state.value == "thinking"
    sessions.release(child, remote)
    assert child.state == "working"


async def test_an_idea_of_the_agents_is_proposed_and_starts_on_approval(
        make, tmp_path, child_env):
    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("propose", {"call": {"name": "Agent",
                                              "arguments": {"description": "Tidy the notes",
                                                            "prompt": "Tidy the notes"}},
                                     "reason": "They are messy"}, id="p")]),
        Step(text="Proposed."), Step(text="It is done.")],
        [finish("Tidied."), {"text": "Done."}])
    await h.turn("Hi")
    assert sessions.sessions == []  # nothing starts before the user approves
    await h.runtime.respond("p", True)
    result = await eventually(lambda: outcome(h.store.conversations[h.conversation.id]
                                              .messages, "p"), timeout=30)
    assert "Tidied." in result.content


async def test_a_turn_nobody_waited_for_becomes_a_notice_for_the_parent(
        make, tmp_path, child_env):
    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Write a haiku", "prompt": "Write a haiku", "run_in_background": True}, id="s")]),
        Step(text="Started."), Step(text="Got it.")],
        [finish("Haiku written."), {"text": "Done."}, {"text": "Made it shorter."}])
    await h.turn("Go")
    child = await eventually(lambda: sessions.sessions and sessions.sessions[0].state == "done"
                             and sessions.sessions[0], timeout=30)
    assert sessions.take_notes(h.conversation.id) == []  # the call got that turn
    remote = await sessions.open(child)
    await remote.send("Shorter, please")  # the user talks to it directly
    [note] = await eventually(lambda: sessions.notes.get(h.conversation.id), timeout=20)
    assert note.startswith("Sub-session 1 (Write a haiku) ended a turn")
    assert sessions.take_notes(h.conversation.id) == [note]
    assert sessions.take_notes(h.conversation.id) == []
    sessions.release(child, remote)


async def test_a_stopped_child_resumes_its_goal(make, tmp_path, child_env):
    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Keep at it", "prompt": "Keep at it", "run_in_background": True}, id="s")]),
        Step(text="Started.")],
        [{"hang": True}])
    await h.turn("Go")
    child = await eventually(lambda: sessions.sessions and sessions.sessions[0].state
                             == "working" and sessions.sessions[0], timeout=30)
    await h.runtime.cancel_operation("s")
    await eventually(lambda: child.returncode is not None, timeout=15)
    assert child.state == "stopped" and child.resumable
    await sessions.resume(child)
    await eventually(lambda: child.agent_state is not None
                     and child.agent_state.value == "thinking", timeout=20)
    assert child.state == "working"
    sessions.stop(child)


async def test_children_are_renamed_archived_and_deleted(tmp_path):
    from kaden.frontends.sessions import SubSession

    sessions = SubSessions(tmp_path)
    folder = tmp_path / "sessions" / "conv-1"
    folder.mkdir(parents=True)
    child = SubSession("1", "Write a haiku", "conv", folder, state="done")
    sessions.sessions.append(child)
    sessions.rename(child, "Haiku")
    sessions.archive(child, True)
    await sessions.aclose()  # the record is written
    again = SubSessions(tmp_path)
    [kept] = again.sessions
    assert (kept.name, kept.archived) == ("Haiku", True)
    assert again.of("conv") == [] and again.of("conv", include_archived=True) == [kept]
    await again.delete(kept)
    assert again.sessions == [] and not folder.exists()


async def test_cancelling_while_a_child_starts_stops_it(tmp_path):
    """The child's first requests are still unanswered: the call is cancelled there."""
    import asyncio
    import sys

    from kaden.api import ToolCall

    class Ctx:
        conversation_id = "conv"

        def progress(self, text: str) -> None:
            pass

    silent = [sys.executable, "-c", "import sys; sys.stdin.read()"]  # quits on EOF
    sessions = SubSessions(tmp_path, command=silent)
    call_ = ToolCall("c", "Agent", {"description": "Anything", "prompt": "Anything"})
    running = asyncio.create_task(sessions.execute(call_, Ctx()))
    await eventually(lambda: sessions.sessions and sessions.sessions[0].live,
                     timeout=10)  # started, not answering
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)
    [child] = sessions.sessions
    await eventually(lambda: child.returncode is not None, timeout=20)
    assert child.state == "stopped"


async def test_a_sub_session_has_no_spending_limit_unless_one_is_set(
        make, tmp_path, child_env):
    """As Claude's sub-agents: a limit is the user's or the agent's choice."""
    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Free", "prompt": "Free", "run_in_background": True}, id="a"),
                    call("Agent", {"description": "Frugal", "prompt": "Frugal", "run_in_background": True, "budget_usd": 0.2}, id="b")]),
        Step(text="Started.")],
        [{"hang": True}])
    assert sessions.limit_usd is None
    assert "may spend up to" not in (await sessions.list_tools())[0].description
    await h.turn("Go")
    await eventually(lambda: len(sessions.sessions) == 2, timeout=30)
    budgets = {s.task: s.budget_usd for s in sessions.sessions}
    assert budgets == {"Free": None, "Frugal": 0.2}
    free, frugal = sorted(sessions.sessions, key=lambda s: s.task)
    assert tomllib.loads((free.data_dir / "settings.toml").read_text())["budget"] == {}
    assert " of $" not in free.summary and "of $0.2" in frugal.summary
    await sessions.set_child_limit(frugal, None)  # the user removes it
    assert frugal.budget_usd is None
    for child in sessions.sessions:
        sessions.stop(child)


async def test_the_users_limit_is_the_default_and_the_most_a_child_gets(
        make, tmp_path, child_env):
    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Small", "prompt": "Small", "run_in_background": True}, id="a"),
                    call("Agent", {"description": "Greedy", "prompt": "Greedy", "run_in_background": True, "budget_usd": 50}, id="b"),
                    call("Agent", {"description": "Frugal", "prompt": "Frugal", "run_in_background": True, "budget_usd": 0.2}, id="c")]),
        Step(text="Started.")],
        [{"hang": True}])
    await sessions.set_limit(3)
    assert "up to $3" in (await sessions.list_tools())[0].description
    await h.turn("Go")
    await eventually(lambda: len(sessions.sessions) == 3, timeout=30)
    assert {s.task: s.budget_usd for s in sessions.sessions} == {
        "Small": 3, "Greedy": 3, "Frugal": 0.2}
    await eventually(lambda: all((c.data_dir / "settings.toml").exists()
                                 for c in sessions.sessions), timeout=30)
    for child in sessions.sessions:
        assert tomllib.loads((child.data_dir / "settings.toml").read_text())["budget"] == {
            "max_cost_usd": child.budget_usd}
    assert SubSessions(h.data_dir).limit_usd == 3  # kept
    for child in sessions.sessions:
        sessions.stop(child)


async def test_a_childs_limit_changes_while_it_runs_and_after(make, tmp_path, child_env):
    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Keep at it", "prompt": "Keep at it", "run_in_background": True}, id="s")]),
        Step(text="Started.")],
        [{"hang": True}])
    await h.turn("Go")
    child = await eventually(lambda: sessions.sessions and sessions.sessions[0].state
                             == "working" and sessions.sessions[0], timeout=30)
    await eventually(lambda: child.child_conversation_id, timeout=30)
    await sessions.set_child_limit(child, 7.5)
    settings = await child.live.settings()
    assert settings.budget.max_cost_usd == 7.5 and child.budget_usd == 7.5
    sessions.stop(child)
    await eventually(lambda: child.returncode is not None, timeout=20)
    await sessions.set_child_limit(child, 2)
    assert tomllib.loads((child.data_dir / "settings.toml").read_text())["budget"] == {
        "max_cost_usd": 2}
    with pytest.raises(ValueError):
        await sessions.set_child_limit(child, 0)


async def test_a_snapshot_has_the_events_read_while_it_was_awaited():
    """The reply and the events after it can be read in one go, before attach() goes on."""
    import asyncio

    from kaden.api import EventEnvelope, RuntimeSnapshot, StateChanged, Usage, UsageSnapshot
    from kaden.frontends.remote import RemoteRuntime
    from kaden.frontends.wire import to_json

    class Channel:
        def __init__(self) -> None:
            self.listeners: list = []
            self.ended = asyncio.Event()

        async def request(self, method: str, **params: Any) -> Any:
            for seq, state in ((4, AgentState.RUNNING_TOOLS), (6, AgentState.IDLE)):
                for listener in self.listeners:
                    listener(EventEnvelope(seq, StateChanged(state)))
            return to_json(RuntimeSnapshot(5, AgentState.THINKING, None, None, None,
                                           UsageSnapshot(Usage(), 0, 0.0), (), (), ()))

    heard: list[int] = []
    remote = RemoteRuntime(Channel(), lambda envelope: heard.append(envelope.seq))
    assert not remote.attached
    await remote.attach()
    assert remote.attached and heard == [4, 6]
    assert (remote.snapshot().seq, remote.snapshot().state) == (6, AgentState.IDLE)


async def test_an_agent_in_the_foreground_is_waited_for_and_named_by_its_description(
        make, tmp_path, child_env):
    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Haiku", "prompt": "Write a haiku"},
                         id="s")]),
        Step(text="Got it.")],
        [finish("Haiku written.")])
    await asyncio.wait_for(h.turn("Get me a haiku"), 60)
    result = (await h.results())["s"]
    assert result.status == ToolStatus.OK and "Haiku written." in result.content
    [child] = sessions.sessions
    assert (child.title, child.task) == ("Haiku", "Write a haiku")


# ── Worktrees ─────────────────────────────────────────────────────────────


def git(folder: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(folder), "-c", "user.name=t", "-c",
                           "user.email=t@example.com", *args], check=True,
                          capture_output=True, text=True).stdout


def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    (repo / "sub" / "a.txt").write_text("a\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "first")
    return repo


def isolated(prompt: str, **more: Any) -> dict[str, Any]:
    return {"description": prompt, "prompt": prompt, "isolation": "worktree", **more}


async def test_a_subagent_that_changes_nothing_leaves_no_worktree(make, tmp_path, child_env):
    repo = repository(tmp_path)
    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", isolated("Read the code"), id="s")]),
        Step(text="Read.")],
        [finish("Nothing to change."), {"text": "Done."}], folder=repo / "sub")
    await asyncio.wait_for(h.turn("Read it"), 60)
    [child] = sessions.sessions
    name = f"agent-{h.conversation.id[:8]}-1"
    worktree = repo / ".claude" / "worktrees" / name
    settings = tomllib.loads((child.data_dir / "settings.toml").read_text())
    assert settings["work_folder"] == str(worktree / "sub")  # the same place inside it
    result = (await h.results())["s"]
    assert result.status == ToolStatus.OK and "Nothing to change." in result.content
    assert "removed" in result.content
    exclude = Path(git(repo, "rev-parse", "--git-path", "info/exclude").strip())
    exclude = exclude if exclude.is_absolute() else repo / exclude
    assert "/.claude/worktrees/" in exclude.read_text().splitlines()  # never in git add -A
    assert not worktree.exists()
    assert git(repo, "branch", "--list", f"worktree-{name}") == ""
    assert name not in git(repo, "worktree", "list")

    remote = await sessions.open(child)  # opening it again adds the worktree again
    assert (worktree / "sub" / "a.txt").read_text() == "a\n"
    sessions.release(child, remote)
    await eventually(lambda: not worktree.exists(), timeout=20)  # and it is removed again


async def test_a_subagents_changes_stay_in_its_worktree(make, tmp_path, child_env):
    repo = repository(tmp_path)
    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", isolated("Change the code", run_in_background=True),
                         id="s")]),
        Step(text="Started."),
        Step(calls=[call("SendMessage", {"to": "1", "message": "Go on."}, id="m")]),
        Step(text="Answered."),
        Step(text="Changed.")],
        [{"calls": [{"name": "ask_for_help", "arguments": {"question": "Ready?"}}]},
         {"text": "Waiting."}, finish("Changed a.txt."), {"text": "Done."}],
        folder=repo)
    await h.turn("Change it")
    child = await eventually(lambda: sessions.sessions and sessions.sessions[0].state
                             == "waiting" and sessions.sessions[0], timeout=30)
    name = f"agent-{h.conversation.id[:8]}-1"
    worktree = repo / ".claude" / "worktrees" / name
    (worktree / "sub" / "a.txt").write_text("b\n")  # what the subagent changed
    assert (repo / "sub" / "a.txt").read_text() == "a\n"  # not in the parent's checkout
    answered = await eventually(lambda: outcome(h.store.conversations[h.conversation.id]
                                                .messages, "m"), timeout=30)
    assert "Changed a.txt." in answered.content
    assert str(worktree) in answered.content and f"worktree-{name}" in answered.content
    await eventually(lambda: child.returncode is not None, timeout=15)
    assert worktree.exists() and git(repo, "branch", "--list", f"worktree-{name}")
    await eventually(lambda: [s.worktree for s in SubSessions(h.data_dir).sessions]
                     == [child.worktree], timeout=10)  # its record keeps it
    await sessions.delete(child)  # its changes are the user's: deleting it keeps them
    assert (worktree / "sub" / "a.txt").read_text() == "b\n"


async def test_isolation_outside_a_git_repository_is_an_error(make, tmp_path, child_env):
    folder = tmp_path / "plain"
    folder.mkdir()
    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", isolated("Change the code"), id="s")]),
        Step(text="No.")], [], folder=folder)
    await asyncio.wait_for(h.turn("Go"), 30)
    result = (await h.results())["s"]
    assert result.status == ToolStatus.ERROR and "git repository" in result.content
    assert sessions.sessions == []


class _Ctx:
    def __init__(self, conversation_id: str = "conv") -> None:
        self.conversation_id = conversation_id

    def progress(self, text: str) -> None:
        pass


REFUSER = (  # a child that refuses every request, as one that cannot start does
    "import json, sys\n"
    "for line in sys.stdin:\n"
    "    r = json.loads(line)\n"
    "    print(json.dumps({'jsonrpc': '2.0', 'id': r['id'], 'error': {'code': -32000, "
    "'message': 'cannot start', 'data': {'type': 'RuntimeStateError', "
    "'reason': 'unavailable'}}}), flush=True)\n")


async def test_a_child_that_refuses_to_start_fails_and_is_closed(tmp_path):
    import sys

    from kaden.api import ToolCall

    sessions = SubSessions(tmp_path, command=[sys.executable, "-c", REFUSER])
    result = await sessions.execute(
        ToolCall("c", "Agent", {"description": "Anything", "prompt": "Anything"}), _Ctx())
    [child] = sessions.sessions
    assert child.state == "failed" and "cannot start" in (child.failure or "")
    assert result.is_error and "failed: cannot start" in result.content
    await eventually(lambda: child.returncode is not None, timeout=10)
    await sessions.aclose()


async def test_concurrent_opens_start_one_process(make, tmp_path, child_env):
    import psutil

    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Write a haiku", "prompt": "Write a haiku",
                                   "run_in_background": True}, id="s")]),
        Step(text="Started."), Step(text="Got it.")],
        [finish("Haiku written."), {"text": "Done."}])
    await h.turn("Go")
    await eventually(lambda: sessions.sessions and sessions.sessions[0].returncode
                     is not None, timeout=30)
    await h.close()
    again = SubSessions(h.data_dir)
    [child] = again.sessions
    first, second = await asyncio.gather(again.open(child), again.open(child))
    children = [p for p in psutil.Process().children(recursive=True)
                if "--headless" in p.cmdline()]
    assert len(children) == 1
    again.release(child, first)
    again.release(child, second)
    await again.aclose()


async def test_a_reopened_child_keeps_the_state_another_caller_gave_it(tmp_path):
    """open() marks a child it started again as idle, unless meanwhile something else
    (a SendMessage, a resume) set it working."""
    from kaden.frontends.sessions import SubSession

    sessions = SubSessions(tmp_path)
    child = SubSession("1", "Task", "conv", tmp_path / "c", state="stopped",
                       child_conversation_id="x")

    class Live:
        attached = True
        channel = None

    async def running(session: SubSession) -> Any:
        session.live = Live()  # type: ignore[assignment]
        sessions._set(session, state="working")  # another caller, while this one waited
        return Live(), True  # this call started its process

    async def attach(self: Any) -> Any:
        return self

    sessions._running = running  # type: ignore[method-assign]
    import kaden.frontends.sessions as module

    original = module.RemoteRuntime
    module.RemoteRuntime = type("R", (), {"__init__": lambda self, ch: None,  # type: ignore
                                          "attach": attach, "detach": lambda self: None})
    try:
        await sessions.open(child)
    finally:
        module.RemoteRuntime = original  # type: ignore[misc]
    assert child.state == "working"


async def test_a_blank_reply_is_no_step_and_later_listeners_still_hear(tmp_path):
    import time

    from kaden.api import EventEnvelope, Message, MessageCommitted, Role, Text
    from kaden.frontends.sessions import SubSession

    sessions = SubSessions(tmp_path)
    child = SubSession("1", "Task", "conv", tmp_path / "c", state="working", step="Before")
    sessions.sessions.append(child)
    message = Message("m", Role.ASSISTANT, (Text("  \n "),), time.time())
    sessions._event(child, EventEnvelope(1, MessageCommitted(message)))
    assert child.step is None and child.messages == [message]
    await sessions.aclose()


async def test_a_step_without_a_sentence_reads_as_the_desktop_shows_it(tmp_path):
    """A call with no step_summary is its tool's phrase or title (common.tool_sentence,
    ToolTitles), not its bare name."""
    import time

    from kaden.api import EventEnvelope, Message, MessageCommitted, Role, ToolCall, ToolTitles
    from kaden.frontends.sessions import SubSession

    sessions = SubSessions(tmp_path)
    child = SubSession("1", "Task", "conv", tmp_path / "c", state="working")
    sessions.sessions.append(child)
    sessions._event(child, EventEnvelope(0, ToolTitles({"mcp__notes__add": "Add numbers"})))
    steps = []
    for n, made in enumerate((ToolCall("r", "Read", {"file_path": "/a/notes.md"}),
                              ToolCall("m", "mcp__notes__add", {"a": 1}),
                              ToolCall("b", "Bash", {"command": "ls",
                                                     "step_summary": "List the files"}))):
        message = Message(f"m{n}", Role.ASSISTANT, (made,), time.time())
        sessions._event(child, EventEnvelope(n + 1, MessageCommitted(message)))
        steps.append(child.step)
    assert steps == ["Read notes.md", "Add numbers", "List the files"]
    await sessions.aclose()


async def test_a_listener_that_fails_does_not_keep_the_others_from_hearing():
    import json

    from kaden.frontends.remote import Channel

    class Process:
        def __init__(self) -> None:
            self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
            self.stdin = None

    process = Process()
    channel = Channel(process)  # type: ignore[arg-type]
    heard: list[int] = []

    def broken(envelope: Any) -> None:
        raise IndexError("a bug")

    channel.listeners += [broken, lambda envelope: heard.append(envelope and envelope.seq)]
    process.stdout.feed_data(json.dumps({"method": "event", "params": {
        "seq": 3, "event": {"type": "StateChanged", "state": "idle"}}}).encode() + b"\n")
    await eventually(lambda: heard == [3])
    process.stdout.feed_eof()
    process.stderr.feed_eof()
    await channel.ended.wait()


async def test_a_broken_pipe_is_a_closed_channel():
    from kaden.frontends.remote import Channel, ChannelClosed

    class Process:
        def __init__(self) -> None:
            self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
            self.stdin = self

        def write(self, data: bytes) -> None:
            pass

        async def drain(self) -> None:
            raise BrokenPipeError

    process = Process()
    channel = Channel(process)  # type: ignore[arg-type]
    with pytest.raises(ChannelClosed):
        await channel.request("snapshot")
    assert channel._pending == {}
    process.stdout.feed_eof()
    process.stderr.feed_eof()
    await channel.ended.wait()


def test_a_child_that_ended_its_turn_says_so_with_its_last_reply(tmp_path):
    import time

    from kaden.api import Message, Role, Text
    from kaden.frontends.sessions import SubSession

    sessions = SubSessions(tmp_path)
    child = SubSession("4", "Task", "conv", tmp_path / "c", state="idle", messages=[
        Message("m", Role.ASSISTANT, (Text("Nothing more to do here."),), time.time())])
    text = sessions._outcome(child)
    assert "failed" not in text and "None" not in text
    assert "Sub-session 4" in text and "Nothing more to do here." in text


async def test_send_message_reaches_only_the_conversations_own_subagents(tmp_path):
    from kaden.api import ToolCall
    from kaden.frontends.sessions import SubSession

    sessions = SubSessions(tmp_path)
    sessions.sessions.append(SubSession("1", "Theirs", "other", tmp_path / "c",
                                        state="done", child_conversation_id="x"))
    result = await sessions.execute(
        ToolCall("m", "SendMessage", {"to": "1", "message": "hi"}), _Ctx("conv"))
    assert result.is_error and "no subagent '1'" in result.content


async def test_a_deleted_childs_id_is_not_given_again(tmp_path):
    """Its kept worktree and branch are named by it."""
    sessions = SubSessions(tmp_path)
    first = await sessions._new("One", None, "conv")
    second = await sessions._new("Two", None, "conv")
    await sessions.delete(second)
    await sessions.aclose()
    again = SubSessions(tmp_path)
    third = await again._new("Three", None, "conv")
    assert third.id not in (first.id, second.id)
    await again.aclose()


async def test_its_settings_are_written_whole_and_a_broken_one_is_refused(tmp_path):
    """The parent's settings, as the child gets them; a settings file that does not read
    is a failure to start, not a crash."""
    import sys

    from kaden.api import ToolCall

    (tmp_path / "settings.toml").write_text("this is = = not toml")
    sessions = SubSessions(tmp_path, command=[sys.executable, "-c", "pass"])
    result = await sessions.execute(
        ToolCall("c", "Agent", {"description": "Anything", "prompt": "Anything"}), _Ctx())
    [child] = sessions.sessions
    assert result.is_error and child.state == "failed"
    await sessions.aclose()


def test_a_sub_sessions_snapshot_follows_its_tool_titles():
    """RemoteRuntime keeps the snapshot it took at attach() current from the events: a view
    opened later names the child's steps by the titles its listing found since."""
    from kaden.api import (
        AgentState,
        EventEnvelope,
        RuntimeSnapshot,
        ToolTitles,
        Usage,
        UsageSnapshot,
    )
    from kaden.frontends.remote import _applied

    taken = RuntimeSnapshot(seq=0, state=AgentState.IDLE, conversation=None, config=None,
                            model=None, usage=UsageSnapshot(Usage(), 0, 0.0), operations=(),
                            queued_messages=())
    snap = _applied(taken,
                    EventEnvelope(1, ToolTitles({"mcp__notes__add": "Add numbers"})))
    assert snap.tool_titles == {"mcp__notes__add": "Add numbers"} and snap.seq == 1
