"""A conversation's plan and life: the agent plans with TodoWrite, a due step reminds it,
and a life (Continuous) goes on by itself, woken by finished work and the times it chose,
until it is paused or ended."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any

import pytest

from kaden.api import (
    TODO_STATUS,
    AgentState,
    Budget,
    ConversationUpdated,
    Duty,
    DutyKind,
    DutyScheduled,
    Failure,
    PlanItem,
    PlanStatus,
    RuntimeStateError,
    Text,
    ToolResult,
    ToolStatus,
)
from kaden.runtime import duty as duties
from tests.conftest import Harness, eventually
from tests.fakes.core import ScriptedCore, Step, call
from tests.fakes.tools import FakeTools

LIMIT = Budget(max_cost_usd=5.0)
QUIET = Duty("Keep the inbox answered", min_interval_s=0.05, max_interval_s=30)


async def duty_harness(make: Any, steps: list[Step] | None = None,
                       tools: FakeTools | None = None, **config: Any) -> Harness:
    h: Harness = await make(core=ScriptedCore(steps or []), tools=tools,
                            budget=config.pop("budget", LIMIT), **config)
    await h.start()
    return h


def wakes(h: Harness) -> list[str]:
    """Why each cycle was requested, from the event log."""
    lines = (h.data_dir / "events.jsonl").read_text().splitlines()
    return [e["reason"] for e in map(json.loads, lines) if e.get("decision") == "cycle_requested"]


def notices(request: Any) -> list[str]:
    return [b.text for m in request.messages for b in m.blocks
            if isinstance(b, Text) and "[Runtime notice]" in b.text]


async def idle(h: Harness, requests: int) -> None:
    await eventually(lambda: len(h.core.requests) >= requests
                     and h.runtime.snapshot().state == AgentState.IDLE)


# ── Turning it on and off ──


async def test_a_life_runs_without_a_spending_limit(make):
    """A limit is the user's choice, as in Claude's apps: nothing requires one."""
    h = await duty_harness(make, [Step(text="on it")], budget=Budget())
    await h.runtime.set_duty(QUIET)
    await idle(h, 1)
    assert h.runtime.snapshot().conversation.duty is not None
    assert not h.rec.of(Failure)


def test_a_duty_is_a_life():
    """Duties and goals are gone: a life is the one way a conversation goes on by itself."""
    assert list(DutyKind) == [DutyKind.LIFE] and Duty("x").kind is DutyKind.LIFE
    with pytest.raises(ValueError):
        DutyKind("goal")


@pytest.mark.parametrize("duty", [
    Duty("  "), Duty("x", min_interval_s=0), Duty("x", min_interval_s=10, max_interval_s=5)])
async def test_invalid_duties_are_refused(make, duty):
    h = await duty_harness(make)
    with pytest.raises(ValueError):
        await h.runtime.set_duty(duty)


async def test_set_duty_needs_an_open_conversation(make):
    h: Harness = await make(budget=LIMIT)
    with pytest.raises(RuntimeStateError):
        await h.runtime.set_duty(QUIET)


async def test_turning_a_life_on_starts_it_and_shows_it(make):
    h = await duty_harness(make, [Step(text="on it")])
    await h.runtime.set_duty(QUIET)
    await idle(h, 1)
    [request] = h.core.requests
    assert "Keep the inbox answered" in request.system
    names = {t.name for t in request.tools}
    assert {"TodoWrite", "Rest"} <= names
    assert names.isdisjoint({"finish_goal", "ask_for_help"})  # no duty mode
    assert notices(request)[0].endswith("[Runtime notice] Your life: Keep the inbox answered")
    [updated] = [e for e in h.rec.of(ConversationUpdated) if e.info.duty is not None]
    assert updated.info.duty == QUIET
    assert h.store.conversations[h.conversation.id].info.duty == QUIET


async def test_an_ordinary_conversation_keeps_a_plan_but_has_no_duty_tools(make):
    h = await duty_harness(make, [
        Step(calls=[call("TodoWrite", {"todos": [
            {"content": "Read the code", "activeForm": "Read the code", "status": "completed"},
            {"content": "Fix the bug", "activeForm": "Fix the bug", "status": "in_progress"},
            {"status": "pending", "content": "Run the tests", "activeForm": "Run the tests"}]}, id="p")]),
        Step(text="Working on it.")])
    await h.turn("fix the bug")
    names = {t.name for t in h.core.requests[0].tools}
    assert "TodoWrite" in names  # a plan for any task with steps
    assert names.isdisjoint({"finish_goal", "ask_for_help", "Rest"})
    plan = h.runtime.snapshot().conversation.plan
    assert [(i.text, i.status) for i in plan] == [
        ("Read the code", PlanStatus.DONE), ("Fix the bug", PlanStatus.DONE),
        ("Run the tests", PlanStatus.DONE)]  # ticked off as the turn ended
    assert h.runtime.snapshot().conversation.duty is None
    # The agent's own call shows the plan from here on: the prompt does not repeat it,
    # so a plan update leaves the start of every later request as it was.
    assert h.core.requests[1].system == h.core.requests[0].system
    assert "Fix the bug" not in h.core.requests[1].system
    result = (await h.results())["p"].content
    assert "Plan saved" in result and "before you end your turn" in result


async def test_the_prompt_shows_a_plan_the_conversation_does_not(make):
    """A plan the user edited, or one whose update_plan call is no longer in view (a
    conversation opened again, older messages summarized), is in the prompt."""
    h = await duty_harness(make, [
        Step(calls=[call("TodoWrite", {"todos": [
            {"content": "Fix the bug", "activeForm": "Fix the bug", "status": "completed"}]}, id="p")]),
        Step(text="Fixed."), Step(text="Sure."), Step(text="That one is yours to do."),
        Step(calls=[call("TodoWrite", {"todos": [
            {"content": "Ship it", "activeForm": "Ship it", "status": "completed"}]}, id="q")]),
        Step(text="Shipped."), Step(text="Hello again.")])
    await h.turn("fix the bug")
    await h.runtime.set_plan([PlanItem("Write the docs first")])
    await h.turn("carry on")
    system = h.core.requests[2].system
    assert "## Plan\n- [pending] Write the docs first" in system and duties.CLOSE_PLAN in system
    await h.turn("plan again")  # the agent's own update is in the conversation again
    assert "## Plan" not in h.core.requests[5].system
    first = h.conversation.id
    await h.chat("Other")  # away and back: what the conversation shows is not known
    await h.start(conversation_id=first)
    await h.turn("hello again")
    assert "- [completed] Ship it" in h.core.requests[6].system


async def test_a_life_can_be_added_mid_conversation_and_keeps_the_context(make):
    h = await duty_harness(make, [Step(text="noted"), Step(text="continuing")])
    await h.turn("we sell bikes")
    await h.runtime.set_duty(QUIET)
    await idle(h, 2)
    assert any("we sell bikes" in str(m.blocks) for m in h.core.requests[1].messages)


async def test_turning_it_off_ends_the_schedule_and_keeps_the_plan(make):
    h = await duty_harness(make, [Step(calls=[call("TodoWrite", {
        "todos": [{"status": "pending", "content": "Read mail", "activeForm": "Read mail",
                   "due_in_minutes": 60}]})]), Step(text="planned")])
    await h.runtime.set_duty(QUIET)
    await idle(h, 2)
    assert h.runtime.snapshot().duty_wake_at is not None
    await h.runtime.set_duty(None)
    info = h.runtime.snapshot().conversation
    assert info.duty is None and [i.text for i in info.plan] == ["Read mail"]
    assert h.runtime.snapshot().duty_wake_at is None
    await h.rec.wait_for(lambda r: r.of(DutyScheduled)[-1] == DutyScheduled(None))
    await h.turn("thanks")
    request = h.core.requests[-1]
    # The plan is still in view: the agent's own call shows it.
    assert any(getattr(b, "name", None) == "TodoWrite" and b.arguments["todos"]
               for m in request.messages for b in m.blocks)
    assert "TodoWrite" in [t.name for t in request.tools]


# ── The plan ──


# ── Reminders: a due step without a life ──


def soon(text: str = "Stand up") -> Step:
    return Step(calls=[call("TodoWrite", {"todos": [
        {"status": "pending", "content": text, "activeForm": text, "due_in_minutes": 0.002}]}, id="p")])  # about a tenth of a second


async def test_a_due_step_wakes_the_agent_without_a_life(make):
    h = await duty_harness(make, [soon(), Step(text="I will remind you."),
                                  Step(text="Time to stand up!")], reminder_min_s=0.05,
                           budget=Budget())  # no spending limit is needed
    await h.turn("remind me to stand up in a moment")
    assert "woken" in (await h.results())["p"].content
    assert h.runtime.snapshot().duty_wake_at is not None  # frontends show when
    await idle(h, 3)
    assert wakes(h)[-1] == "duty: a plan item is due"
    assert "[Runtime notice] Due now in your plan: Stand up." in notices(
        h.core.requests[2])[-1]
    # The step is still open and overdue: it reminded once, not again and again.
    await asyncio.sleep(0.3)
    assert len(h.core.requests) == 3 and h.runtime.snapshot().duty_wake_at is None


async def test_a_reopened_conversation_reminds_of_what_is_still_to_come_only(make):
    """A step that fell due while the conversation was closed waits for the user; one
    that is still ahead wakes the agent."""
    h = await duty_harness(make, [Step(text="Second one!")], reminder_min_s=0.05)
    now = time.time()
    await h.runtime.set_plan([PlanItem("First", due=now - 60),
                              PlanItem("Second", due=now + 1.0)])
    first = h.conversation.id
    await h.chat("Other")
    await h.start(conversation_id=first)
    await idle(h, 1)
    assert "[Runtime notice] Due now in your plan: First; Second." in notices(
        h.core.requests[0])[-1]


async def test_stop_puts_reminders_off_until_the_next_message(make):
    h = await duty_harness(make, [soon(), Step(text="ok", hang=True), Step(text="Back."),
                                  Step(text="Stand up!")], reminder_min_s=0.05)
    await h.runtime.send("remind me")
    await eventually(lambda: len(h.core.requests) == 2)
    await h.runtime.stop()
    await eventually(lambda: h.runtime.snapshot().state == AgentState.STOPPED)
    await asyncio.sleep(0.3)
    assert len(h.core.requests) == 2  # stopped: nothing wakes it
    await h.turn("carry on")
    await idle(h, 4)
    assert wakes(h)[-1] == "duty: a plan item is due"


# ── Finishing the plan ──


def plan_call(*items: tuple[str, str], id: str = "p") -> Step:
    return Step(calls=[call("TodoWrite", {"todos": [
        {"content": text, "activeForm": text, "status": TODO_STATUS[PlanStatus(status)]}
        for text, status in items]}, id=id)])


async def test_a_turn_does_not_end_with_steps_open(make):
    """The agent is sent back once to finish or close its plan, as Claude works until its
    checklist is done."""
    h = await duty_harness(make, [
        plan_call(("Read the code", "done"), ("Fix the bug", "doing")),
        Step(text="I looked at it."),  # ends its turn with a step open
        plan_call(("Read the code", "done"), ("Fix the bug", "done"), id="q"),
        Step(text="Fixed.")])
    await h.turn("fix the bug")
    assert len(h.core.requests) == 4
    sent_back = notices(h.core.requests[2])[-1]
    assert "still has open steps: Fix the bug" in sent_back
    assert [i.status for i in h.runtime.snapshot().conversation.plan] == [
        PlanStatus.DONE, PlanStatus.DONE]


async def test_a_turn_that_ends_closes_the_list(make):
    """Sent back once, the agent still leaves a step open: the runtime ticks it off, as
    Claude's app checks off the task list when a turn ends."""
    h = await duty_harness(make, [
        plan_call(("Ask which colour", "doing")), Step(text="Which colour?"),
        Step(text="I need your answer on the colour first.")])
    await h.turn("paint it")
    assert len(h.core.requests) == 3
    assert h.runtime.snapshot().state == AgentState.IDLE
    assert h.runtime.snapshot().conversation.plan[0].status == PlanStatus.DONE


async def test_steps_that_wait_are_not_sent_back_for(make):
    """A step with a due time waits for it, and a life keeps its plan between turns."""
    h = await duty_harness(make, [
        Step(calls=[call("TodoWrite", {"todos": [
            {"status": "pending", "content": "Call back", "activeForm": "Call back", "due": "2099-01-01T09:00"}]})]),
        Step(text="I will.")])
    await h.turn("remind me")
    assert len(h.core.requests) == 2
    assert h.runtime.snapshot().conversation.plan[0].status == PlanStatus.TODO
    duty = await duty_harness(make, [plan_call(("Read mail", "doing")), Step(text="On it.")])
    await duty.runtime.set_duty(QUIET)
    await idle(duty, 2)
    await asyncio.sleep(0.2)
    assert len(duty.core.requests) == 2
    assert duty.runtime.snapshot().conversation.plan[0].status == PlanStatus.DOING


def test_what_update_plan_says_about_due_steps():
    later = PlanItem("Check the build", PlanStatus.TODO, due=time.time() + 600)
    now = time.time()
    assert duties.WAKES in duties.saved([later], None, now)
    assert duties.WAKES not in duties.saved([later], Duty("Watch the build"), now)
    assert duties.WAKES not in duties.saved([PlanItem("Fix it")], None, now)


def test_an_open_plan_tells_the_agent_to_close_it_before_ending_its_turn():
    """An agent tends to stop ticking steps off once the work is done."""
    done = PlanItem("Read the code", PlanStatus.DONE)
    doing = PlanItem("Fix the bug", PlanStatus.DOING)
    assert duties.CLOSE_PLAN in duties.prompt(None, [done, doing])
    assert duties.CLOSE_PLAN not in duties.prompt(None, [done])  # nothing open
    # A life's plan stays open between turns: its steps fall due later.
    assert duties.CLOSE_PLAN not in duties.prompt(Duty("Watch the inbox"), [done, doing])


async def test_update_plan_saves_the_plan_and_every_request_shows_it(make):
    h = await duty_harness(make, [
        Step(calls=[call("TodoWrite", {"todos": [
            {"content": "Read mail", "activeForm": "Read mail", "status": "in_progress", "note": "start with VIPs"},
            {"status": "pending", "content": "Weekly report", "activeForm": "Weekly report", "due": "2030-01-07T09:00"},
            {"status": "pending", "content": "Call back", "activeForm": "Call back", "due_in_minutes": 90}]})]),
        Step(text="planned")])
    before = time.time()
    await h.runtime.set_duty(QUIET)
    await idle(h, 2)
    plan = h.runtime.snapshot().conversation.plan
    assert [(i.text, i.status) for i in plan] == [
        ("Read mail", PlanStatus.DOING), ("Weekly report", PlanStatus.TODO),
        ("Call back", PlanStatus.TODO)]
    assert plan[0].note == "start with VIPs" and plan[0].due is None
    assert plan[1].due == pytest.approx(
        datetime(2030, 1, 7, 9).astimezone().timestamp())
    assert before + 90 * 60 <= plan[2].due <= time.time() + 90 * 60
    assert "Weekly report" not in h.core.requests[1].system  # the result shows it
    result = next(b for m in h.core.requests[1].messages for b in m.blocks
                  if isinstance(b, ToolResult))
    assert "- Weekly report: due 2030-01-07T09:00" in result.content
    assert h.store.conversations[h.conversation.id].info.plan == plan


async def test_a_bad_due_time_is_an_error_and_changes_nothing(make):
    h = await duty_harness(make, [
        Step(calls=[call("TodoWrite", {"todos": [{"status": "pending", "content": "x", "activeForm": "x", "due": "soon"}]}, id="p")]),
        Step(text="oops")])
    await h.runtime.set_duty(QUIET)
    await idle(h, 2)
    assert (await h.results())["p"].status == ToolStatus.ERROR
    assert h.runtime.snapshot().conversation.plan == ()


async def test_the_user_edits_the_plan(make):
    h = await duty_harness(make)
    await h.runtime.set_plan([PlanItem("Ship it", due=2e9)])
    assert [i.text for i in h.runtime.snapshot().conversation.plan] == ["Ship it"]
    await h.rec.wait_for(lambda r: any(e.info.plan and e.info.plan[0].due == 2e9
                                       for e in r.of(ConversationUpdated)))


# ── Waking up ──


async def test_a_due_plan_item_wakes_it(make):
    h = await duty_harness(make, [
        Step(calls=[call("TodoWrite", {"todos": [{"status": "pending", "content": "Post", "activeForm": "Post", "due_in_minutes": 0.003}]})]),
        Step(text="later"), Step(text="posting")])
    await h.runtime.set_duty(Duty("Run socials", min_interval_s=0.05, max_interval_s=30))
    await idle(h, 3)
    assert "A step in your plan is due." in notices(h.core.requests[2])[-1]


async def test_the_minimum_interval_holds_back_an_item_due_sooner(make):
    h = await duty_harness(make, [Step(text="done")])
    await h.runtime.set_plan([PlanItem("Soon", due=time.time() + 1)])
    await h.runtime.set_duty(Duty("x", min_interval_s=5, max_interval_s=30))
    await idle(h, 1)
    assert h.runtime.snapshot().duty_wake_at - time.time() > 4


async def test_it_wakes_on_the_first_finished_operation(make):
    tools = FakeTools()
    tools.slow("watch_mail", 0.3)
    tools.slow("long_build", 30)
    h = await duty_harness(make, [
        Step(calls=[call("watch_mail", id="w"), call("long_build", id="b")]),
        Step(text="waiting"), Step(text="mail came in")], tools=tools)
    await h.runtime.set_duty(QUIET)
    await idle(h, 3)
    assert "b" in [op.call.id for op in h.runtime.snapshot().operations]  # still running
    assert "Background work finished." in notices(h.core.requests[2])[-1]


async def test_a_user_message_still_wakes_it(make):
    h = await duty_harness(make, [Step(text="on it"), Step(text="sure")])
    await h.runtime.set_duty(QUIET)
    await idle(h, 1)
    await h.turn("also check spam")
    assert notices(h.core.requests[1]) == notices(h.core.requests[0])  # no new notice


async def test_editing_the_life_while_idle_reschedules_without_starting_work(make):
    h = await duty_harness(make, [Step(text="on it")])
    await h.runtime.set_plan([PlanItem("Look again", due=time.time() + 100)])
    await h.runtime.set_duty(QUIET)
    await idle(h, 1)
    assert h.runtime.snapshot().duty_wake_at - time.time() < 101
    await h.runtime.set_duty(Duty("Keep it answered", min_interval_s=500, max_interval_s=600))
    assert h.runtime.snapshot().duty_wake_at - time.time() > 490
    await asyncio.sleep(0.1)
    assert len(h.core.requests) == 1


# ── Pausing ──


async def test_stop_pauses_and_activate_resumes(make):
    h = await duty_harness(make, [Step(text="on it"), Step(text="back")])
    now = time.time()
    await h.runtime.set_plan([PlanItem("Look", due=now + 0.2), PlanItem("Later", due=now + 60)])
    await h.runtime.set_duty(Duty("Watch", min_interval_s=0.05, max_interval_s=3600))
    await idle(h, 1)
    await h.runtime.stop()  # nothing outstanding: no full Stop, but the schedule ends
    assert h.runtime.snapshot().duty_wake_at is None
    await asyncio.sleep(0.4)
    assert len(h.core.requests) == 1
    await h.runtime.activate()
    await idle(h, 2)
    assert h.runtime.snapshot().duty_wake_at is not None


async def test_an_exhausted_budget_pauses_it(make):
    h = await duty_harness(make, [Step(text="expensive")], budget=Budget(max_cost_usd=1e-9))
    await h.runtime.set_plan([PlanItem("Look", due=time.time() + 0.2)])
    await h.runtime.set_duty(Duty("Watch", min_interval_s=0.05, max_interval_s=0.1))
    await eventually(lambda: h.runtime.snapshot().state == AgentState.FAILED)
    await asyncio.sleep(0.3)
    assert len(h.core.requests) == 1 and h.runtime.snapshot().duty_wake_at is None


async def test_continue_from_does_not_copy_the_duty(make):
    h = await duty_harness(make, [Step(text="on it")])
    await h.runtime.set_duty(QUIET)
    await idle(h, 1)
    source = h.conversation.id
    seeded = await h.start(seed_from=source)
    assert seeded.duty is None and h.runtime.snapshot().duty_wake_at is None


def test_the_agent_is_told_to_plan_first_whenever_a_task_is_more_than_one_action():
    """The rule is in the tool's description, which reaches the model whatever system
    prompt the user has written."""
    from kaden.api import DUTY_TOOLS

    told = DUTY_TOOLS["TodoWrite"].description
    assert "Every task that edits files, runs more than one command or has several steps" in told
    assert "with your very first tool call" in told


# ── A life ──

LIFE = Duty("You are Mira. You like old maps and bad puns.", DutyKind.LIFE,
            min_interval_s=0.05, max_interval_s=0.3)


async def test_a_life_is_framed_as_its_own_not_as_work(make):
    """A life's time is its own: no task, no supervisor to ask, no goal to finish."""
    h = await duty_harness(make, [Step(text="")])
    await h.runtime.set_duty(LIFE)
    await idle(h, 1)
    [request] = h.core.requests
    assert "## Your life" in request.system and "old maps" in request.system
    assert "## Your duty" not in request.system
    # How it comes back is a fact it is told: what wakes it, and the times it chooses.
    assert "you rest until something wakes you" in request.system
    assert "Rest" in request.system
    # The user is part of it, and may be reached; no claim that nothing waits on it.
    assert "The user is part of your life" in request.system
    assert "reach out to them whenever you like" in request.system
    assert "no one is waiting" not in request.system
    assert "nothing has to be done" not in request.system
    names = {t.name for t in request.tools}
    assert "TodoWrite" in names and names.isdisjoint({"ask_for_help", "finish_goal"})
    assert notices(request)[0].endswith(f"[Runtime notice] Your life: {LIFE.instruction}")


def test_a_life_sleeps_until_something_wakes_it():
    """No time of Kaden's: with nothing due it is not woken; a time in its plan wakes it
    then, however far."""
    life = Duty("You are Mira.", DutyKind.LIFE, min_interval_s=60, max_interval_s=86_400)
    now = 1_000_000.0
    assert duties.next_wake(life, [], now) is None
    resting = [PlanItem("Read the map book", PlanStatus.TODO, now + 3 * 3600)]
    assert duties.next_wake(life, resting, now) == (3 * 3600, duties.DUE)
    far = [PlanItem("Visit the archive", PlanStatus.TODO, now + 3 * 86_400)]
    assert duties.next_wake(life, far, now) == (3 * 86_400, duties.DUE)


async def test_time_passing_does_not_wake_a_life(make):
    """No nudges: after its turn it sleeps until something wakes it."""
    h = await duty_harness(make, [Step(text=""), Step(text="Hi!")])
    await h.runtime.set_duty(LIFE)
    await idle(h, 1)
    await asyncio.sleep(0.5)  # past its shortest and longest waits
    assert len(h.core.requests) == 1 and h.runtime.snapshot().duty_wake_at is None
    await h.turn("hello")  # the user wakes it
    assert len(h.core.requests) == 2


async def test_a_restart_keeps_the_time_a_life_chose(make):
    """After Kaden restarts, a life still wakes at the due time in its plan."""
    later = time.time() + 1800
    plan = call("TodoWrite", {"todos": [{
        "content": "Look at the stars", "status": "pending", "activeForm": "Looking at the stars",
        "due": datetime.fromtimestamp(later).isoformat(timespec="seconds")}]})
    life = Duty(LIFE.instruction, DutyKind.LIFE, min_interval_s=0.05, max_interval_s=3600)
    h = await duty_harness(make, [Step(calls=[plan]), Step(text="")])
    await h.runtime.set_duty(life)
    await idle(h, 2)
    conversation = h.conversation.id
    await h.close()
    again: Harness = await make(core=ScriptedCore([Step(text="")]), store=h.store, budget=LIMIT)
    await again.start(conversation)
    wake_at = again.runtime.snapshot().duty_wake_at
    assert wake_at is not None and abs(wake_at - later) < 2


async def test_a_life_stops_at_the_days_amount_and_still_answers_the_user(make):
    """The daily amount holds back what a life does by itself, not a reply."""
    h = await duty_harness(make, [Step(text=""), Step(text="Hi!")],
                           budget=Budget(max_cost_usd_per_day=1e-9))
    await h.runtime.set_duty(LIFE)
    await idle(h, 1)  # its first cycle spends the day's amount
    wake_at = h.runtime.snapshot().duty_wake_at
    tomorrow = datetime.now().astimezone().replace(hour=0, minute=0, second=0,
                                                   microsecond=0).timestamp() + 86400
    assert wake_at is not None and abs(wake_at - tomorrow) < 3700  # the next day
    await asyncio.sleep(0.4)
    assert len(h.core.requests) == 1
    await h.turn("hello")
    assert len(h.core.requests) == 2 and h.runtime.snapshot().state == AgentState.IDLE
    assert not h.rec.of(Failure)


async def test_only_its_own_time_counts_toward_the_days_amount(make):
    """Talking with the user is not the life's own time: it does not spend its day."""
    h = await duty_harness(make, [Step(text="Hi!"), Step(text="")],
                           budget=Budget(max_cost_usd_per_day=1.0))
    await h.turn("hello")  # before it lives: the user's
    assert h.runtime._budget.day_cost() == 0.0
    await h.runtime.set_duty(LIFE)
    await idle(h, 2)  # its own cycle
    assert h.runtime._budget.day_cost() > 0.0


async def test_its_own_time_runs_on_the_lifes_model_and_talk_on_the_conversations(make):
    """A cheaper model for what it does by itself; the user is answered by the one they
    chose."""
    h = await duty_harness(make, [Step(text=""), Step(text="Hi!")])
    life = Duty(LIFE.instruction, DutyKind.LIFE, min_interval_s=30, max_interval_s=60,
                model="fake-2")
    await h.runtime.set_duty(life)
    await idle(h, 1)
    await h.turn("hello")
    assert [r.model for r in h.core.requests] == ["fake-2", "fake-1"]
    assert h.runtime.snapshot().conversation.duty.model == "fake-2"  # kept with it


async def test_a_days_spending_starts_again_the_next_day(tmp_path):
    from kaden.api import Usage
    from kaden.runtime.budget import BudgetTracker

    tracker = BudgetTracker(Budget(max_cost_usd_per_day=1.0))
    tracker.load(tmp_path / "usage.json")
    tracker.charge(Usage(1, 1), 0.6, own_time=True)
    assert tracker.day_cost() == pytest.approx(0.6)
    tracker.totals.day = "2000-01-01"  # as if it was spent on another day
    assert tracker.day_cost() == 0.0
    tracker.charge(Usage(1, 1), 0.3, own_time=True)
    tracker.charge(Usage(1, 1), 0.2)  # not its own time
    assert tracker.day_cost() == pytest.approx(0.3)
    assert tracker.totals.cost_usd == pytest.approx(1.1)  # the lifetime total goes on


@pytest.mark.parametrize("due", [{"due_in_minutes": 1e12}, {"due_in_minutes": float("inf")},
                                 {"due": "2999-01-01T09:00"}])
def test_a_due_time_too_far_ahead_is_refused(due):
    from kaden.runtime.duty import parse_plan

    with pytest.raises(ValueError, match="at most"):
        parse_plan([{"status": "pending", "content": "x", "activeForm": "x", **due}],
                   time.time())
    parse_plan([{"status": "pending", "content": "x", "activeForm": "x",
                 "due_in_minutes": 60 * 24 * 365}], time.time())  # a year: fine


# ── A life rests ──

RESTFUL = Duty("You are Mira.", DutyKind.LIFE, min_interval_s=0.05, max_interval_s=3600)


def rest(**arguments: Any) -> Step:
    return Step(calls=[call("Rest", arguments, id=f"rest{len(arguments)}")])


async def wake_in(h: Harness) -> float:
    await eventually(lambda: h.runtime.snapshot().duty_wake_at is not None
                     and h.runtime.snapshot().state == AgentState.IDLE)
    return h.runtime.snapshot().duty_wake_at - time.time()


async def test_a_life_can_rest_until_a_time_and_a_message_still_wakes_it(make):
    h = await duty_harness(make, [rest(minutes=10), Step(text=""), Step(text="Hi!")])
    await h.runtime.set_duty(RESTFUL)
    assert 590 < await wake_in(h) <= 600
    assert "Rest" in {t.name for t in h.core.requests[0].tools}
    asked = len(h.core.requests)
    await h.runtime.send("hello Mira")
    await idle(h, asked + 1)
    assert h.runtime.snapshot().duty_wake_at is None  # the rest is over: asleep again


async def test_a_rest_without_a_time_lasts_until_something_wakes_it(make):
    h = await duty_harness(make, [rest(), Step(text="")])
    await h.runtime.set_duty(RESTFUL)
    await idle(h, 2)
    assert h.runtime.snapshot().duty_wake_at is None


@pytest.mark.parametrize("arguments", [{"minutes": 10_000_000},
                                       {"until": "2999-01-01T00:00"}])
async def test_a_rest_is_at_most_the_longest_wait(make, arguments):
    h = await duty_harness(make, [rest(**arguments), Step(text="")])
    await h.runtime.set_duty(RESTFUL)
    assert 3590 < await wake_in(h) <= 3600


async def test_a_rest_until_a_time_ends_then(make):
    until = datetime.fromtimestamp(time.time() + 1800).isoformat(timespec="seconds")
    h = await duty_harness(make, [rest(until=until), Step(text="")])
    await h.runtime.set_duty(RESTFUL)
    assert 1790 < await wake_in(h) <= 1801


async def test_only_a_life_rests(make):
    h = await duty_harness(make, [Step(text="")])
    await h.turn("hi")
    assert "Rest" not in {t.name for t in h.core.requests[0].tools}


def test_a_lifes_prompt_names_resting_as_one_way_to_wait():
    section = duties.prompt(RESTFUL, [])
    assert "Rest" in section and "due time" in section


def test_a_wake_up_without_a_life_says_only_that_time_has_passed():
    """Reminders have their own message (duties.reminder); nothing else wakes a
    conversation without a life by itself."""
    assert duties.notice(duties.FINISHED, None) == "Time has passed."


def test_a_lifes_check_in_says_how_long_it_rested_and_the_time():
    now = datetime(2026, 11, 26, 22, 15).timestamp()
    said = duties.notice(duties.CHECK_IN, RESTFUL, rested_s=61, now=now)
    assert said == "1 minute passed; it is 22:15."
    assert duties.notice(duties.CHECK_IN, RESTFUL) == "Time has passed."
