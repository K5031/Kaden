"""Plan mode, as in Claude Code: the agent may only look around until the user approves
the plan it presents with ExitPlanMode."""

from __future__ import annotations

from typing import Any

import pytest

from kaden.api import (
    AgentState,
    PermissionPolicy,
    PlanModeChanged,
    PlanProposed,
    PlanResolved,
    RuntimeStateError,
    ToolStatus,
)
from tests.conftest import Harness, eventually
from tests.fakes.core import ScriptedCore, Step, call

PLAN = "1. Add the column\n2. Run the tests"


async def planning(make: Any, *steps: Step, **config: Any) -> Harness:
    """A conversation in plan mode whose agent presents PLAN, then goes on with `steps`."""
    core = ScriptedCore([Step(calls=[call("ExitPlanMode", {"plan": PLAN}, id="x")]), *steps])
    h: Harness = await make(core=core, **config)
    h.tools.instant("write", read_only=False)
    await h.start()
    await h.runtime.set_plan_mode(True)
    await h.runtime.send("add a column")
    await eventually(lambda: h.runtime.snapshot().pending_plan is not None)
    return h


async def test_in_plan_mode_only_calls_that_read_run(make):
    h: Harness = await make(core=ScriptedCore([
        Step(calls=[call("look", id="r"), call("write", id="w"),
                    call("TodoWrite", {"todos": []}, id="t")]),
        Step(text="Here is what I found.")]))
    h.tools.instant("look")
    h.tools.instant("write", read_only=False)
    await h.start()
    await h.runtime.set_plan_mode(True)
    assert h.runtime.snapshot().plan_mode
    await eventually(lambda: h.rec.of(PlanModeChanged) == [PlanModeChanged(True)])
    await h.turn("add a column")
    results = await h.results()
    assert results["r"].status == ToolStatus.OK
    assert results["w"].status == ToolStatus.REFUSED
    assert "plan mode" in results["w"].content and "ExitPlanMode" in results["w"].content
    assert results["t"].status == ToolStatus.OK  # the todo list is the agent's own
    assert h.tools.started == ["r"]
    assert "plan mode" in h.core.requests[0].system.lower()


async def test_the_agent_enters_plan_mode_itself(make):
    h: Harness = await make(core=ScriptedCore([
        Step(calls=[call("EnterPlanMode", {}, id="e")]),
        Step(calls=[call("write", id="w")]),
        Step(text="Planning first.")]))
    h.tools.instant("write", read_only=False)
    await h.start()
    await h.turn("add a column")
    results = await h.results()
    assert results["e"].status == ToolStatus.OK
    assert results["w"].status == ToolStatus.REFUSED
    assert h.runtime.snapshot().plan_mode


async def test_exit_plan_mode_outside_it_is_an_error(make):
    h: Harness = await make(core=ScriptedCore([
        Step(calls=[call("ExitPlanMode", {"plan": PLAN}, id="x")]), Step(text="ok")]))
    await h.start()
    await h.turn("go")
    assert (await h.results())["x"].status == ToolStatus.ERROR
    assert h.runtime.snapshot().pending_plan is None


async def test_the_plan_waits_for_approval_then_the_agent_goes_on(make):
    h = await planning(make, Step(calls=[call("write", id="w")]), Step(text="Done."))
    h.auto_approve()
    snap = h.runtime.snapshot()
    assert snap.pending_plan == PlanProposed("x", PLAN)
    assert snap.state == AgentState.AWAITING_PERMISSION
    assert len(h.core.requests) == 1
    await h.runtime.resolve_plan("x", True)
    await eventually(lambda: h.runtime.snapshot().state == AgentState.IDLE
                     and len(h.core.requests) == 3)
    results = await h.results()
    assert results["x"].status == ToolStatus.OK and PLAN in results["x"].content
    assert results["w"].status == ToolStatus.OK  # plan mode is over
    assert not h.runtime.snapshot().plan_mode
    await eventually(lambda: h.rec.of(PlanResolved) == [PlanResolved("x", True, None)]
                     and h.rec.of(PlanModeChanged)[-1] == PlanModeChanged(False))


async def test_approval_can_set_what_the_agent_may_then_do(make):
    h = await planning(make, Step(text="Done."), permissions=PermissionPolicy.ASK)
    await h.runtime.resolve_plan("x", True, permissions=PermissionPolicy.ALLOW_WORKSPACE)
    await h.rec.wait_state(AgentState.IDLE, count=2)
    assert (await h.runtime.settings()).permissions == PermissionPolicy.ALLOW_WORKSPACE


async def test_sent_back_it_keeps_planning_with_the_feedback(make):
    h = await planning(make, Step(text="I will rethink it."))
    await h.runtime.resolve_plan("x", False, feedback="Use a migration instead")
    await h.rec.wait_state(AgentState.IDLE, count=2)
    result = (await h.results())["x"]
    assert result.status == ToolStatus.REFUSED
    assert "Use a migration instead" in result.content
    assert h.runtime.snapshot().plan_mode
    assert h.rec.of(PlanResolved)[-1] == PlanResolved("x", False, "Use a migration instead")


async def test_a_message_while_it_waits_sends_it_back(make):
    h = await planning(make, Step(text="Rethinking."))
    await h.runtime.send("Smaller steps, please")
    await h.rec.wait_state(AgentState.IDLE, count=2)
    assert "message that follows" in (await h.results())["x"].content
    users = [m for m in await h.transcript() if m.role.value == "user" and m.from_user]
    assert [b.text for b in users[-1].blocks if hasattr(b, "text")] == ["Smaller steps, please"]
    assert h.runtime.snapshot().plan_mode


async def test_leaving_plan_mode_approves_the_waiting_plan(make):
    h = await planning(make, Step(text="Done."))
    await h.runtime.set_plan_mode(False)
    await h.rec.wait_state(AgentState.IDLE, count=2)
    assert h.rec.of(PlanResolved)[-1].approved is True
    assert (await h.results())["x"].status == ToolStatus.OK


async def test_stop_ends_the_wait(make):
    h = await planning(make)
    await h.runtime.stop()
    await h.rec.wait_state(AgentState.STOPPED, timeout=2)
    assert (await h.results())["x"].status == ToolStatus.CANCELLED
    assert h.rec.of(PlanResolved)[-1] == PlanResolved("x", None, None)
    assert h.runtime.snapshot().pending_plan is None


async def test_resolving_what_is_not_waiting_is_refused(make):
    h = await planning(make, Step(text="Done."))
    with pytest.raises(RuntimeStateError):
        await h.runtime.resolve_plan("other", True)
    await h.runtime.resolve_plan("x", True)
    with pytest.raises(RuntimeStateError):
        await h.runtime.resolve_plan("x", True)


async def test_plan_mode_needs_an_open_conversation_and_ends_with_it(make):
    h: Harness = await make()
    with pytest.raises(RuntimeStateError):
        await h.runtime.set_plan_mode(True)
    await h.start()
    await h.runtime.set_plan_mode(True)
    await h.start()  # another conversation
    assert not h.runtime.snapshot().plan_mode
