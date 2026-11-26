"""Duties: the prompt section, the plan the agent writes with TodoWrite, and when the
duty wakes next."""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from kaden.api import PLAN_STATUS, TODO_STATUS, Duty, DutyKind, PlanItem, PlanStatus

TURNED_ON = "the duty was turned on"
DUE = "a plan item is due"
CHECK_IN = "scheduled check-in"
FINISHED = "background work finished"
NEW_DAY = "a new day"  # a life held back by its daily amount wakes again
WATCH = "something it watches happened"  # a standing watch fired (ToolSpec.watch)
OPEN = (PlanStatus.TODO, PlanStatus.DOING)
MAX_DUE_AHEAD_S = 100 * 365.25 * 24 * 3600  # the furthest a step may fall due
CLOSE_PLAN = (
    "Steps are still open. Before you end your turn, bring the todo list up to date with "
    "TodoWrite: mark what you finished completed, leave out what you gave up, and finish "
    "what you can. Leave a step open only when it waits for the user or for background "
    "work.")


def local(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="minutes")


def _span(seconds: float) -> str:
    if seconds >= 3600 and seconds % 3600 == 0:
        n, unit = int(seconds // 3600), "hour"
    elif seconds >= 60:
        n, unit = round(seconds / 60), "minute"
    else:
        n, unit = round(seconds), "second"
    return f"{n} {unit}{'s' if n != 1 else ''}"


STILL_OPEN = ("Steps are still open: update the plan again before you end your turn, "
              "unless they wait for the user or for background work.")


WAKES = ("You will be woken when a step falls due, as long as this conversation stays open "
         "in Kaden: you can end your turn meanwhile.")


def saved(plan: Sequence[PlanItem], duty: Duty | None, now: float) -> str:
    """What TodoWrite answers. The call and this answer are how the plan stays in view,
    so it says what the call's arguments do not: the time, when each step is due, and
    that a due step wakes the agent."""
    lines = [f"Plan saved. It is now {local(now)}."]
    due = [f"- {i.text}: due {local(i.due)}" for i in plan if i.due and i.status in OPEN]
    lines += due
    if due and duty is None:
        lines.append(WAKES)
    if duty is None and any(i.status in OPEN and not i.due for i in plan):
        lines.append(STILL_OPEN)
    return "\n".join(lines)


def prompt(duty: Duty | None, plan: Sequence[PlanItem], *, in_view: bool = False) -> str:
    """The system prompt section for a conversation with a duty or a plan. A plan the
    conversation itself shows (`in_view`: the agent's TodoWrite call is among the
    messages) is left out, so that updating it does not change the prompt."""
    parts = []
    if duty is not None and duty.kind == DutyKind.LIFE:
        parts.append(
            f"## Your life\n{duty.instruction}\n\n"
            "Your time is your own: no one is waiting on you, and nothing has to be done. "
            "You have your tools, a folder of your own, the web, and memory to keep what "
            "you want to remember. When you end your turn, you go on after "
            f"{_span(duty.min_interval_s)}, unless you rest until a later time you choose "
            "(a step of your plan given a due time with TodoWrite, at most "
            f"{_span(duty.max_interval_s)} ahead). The user writing, or background work of "
            "yours finishing, wakes you sooner. When the user writes, you answer them. Text you "
            "write in a turn the user did not start reaches them as a message; a turn that "
            "ends without text reaches no one.")
    elif duty is not None:
        until = (" until the goal is reached; then call finish_goal with a short report"
                 if duty.kind == DutyKind.GOAL else "")
        parts.append(
            f"## Your duty\n{duty.instruction}\n\n"
            f"This is standing work{until}. Carry it on by yourself, without waiting for "
            "the user. Keep a todo list with TodoWrite: the steps, what you are doing, and "
            "when each falls due. After you end your turn you are woken again: when "
            "background work finishes, when an open plan item falls due, and at least "
            f"every {_span(duty.max_interval_s)}. To react to something outside (new mail, "
            "a webhook, a file), start a tool that waits for it; when it finishes it wakes "
            "you. If you are blocked and only your supervisor "
            "can unblock you, call ask_for_help and end your turn; solve what you can "
            "yourself first.")
    if plan and in_view:
        pass
    elif plan:
        lines = [f"- [{TODO_STATUS[i.status]}] {i.text}" + (f" (due {local(i.due)})" if i.due else "")
                 + (f" — {i.note}" if i.note else "") for i in plan]
        section = "## Plan\n" + "\n".join(lines)
        if duty is None and any(i.status in OPEN for i in plan):
            section += f"\n\n{CLOSE_PLAN}"
        parts.append(section)
    elif duty is not None:
        parts.append("## Plan\n(empty: write one with TodoWrite)")
    return "\n\n".join(parts)


_SAYS = {DUE: "A step in your plan is due.", CHECK_IN: "Time to check in on your {kind}.",
         FINISHED: "Background work finished."}


def notice(cause: str, duty: Duty | None) -> str:
    """The message that starts a cycle a duty started. The one that turns it on states the
    task, so the transcript keeps it after the duty ends (a goal reached mid-cycle)."""
    goal = duty is not None and duty.kind == DutyKind.GOAL
    if duty is not None and duty.kind == DutyKind.LIFE:
        return {TURNED_ON: f"Your life: {duty.instruction}", NEW_DAY: "A new day began.",
                DUE: "A step in your plan is due.", FINISHED: "Background work finished.",
                }.get(cause, "Time has passed.")
    if cause == TURNED_ON and duty is not None:
        return f"{'New goal' if goal else 'New duty'}: {duty.instruction}"
    says = _SAYS.get(cause, "{cause}").format(kind="goal" if goal else "work", cause=cause)
    return f"{says} Carry on, or end your turn if there is nothing to do now."


def next_reminder(plan: Sequence[PlanItem], after: float) -> float | None:
    """Without a duty: when the next open step falls due, counting only those due later
    than `after` (the last reminder), so a step left open reminds once."""
    due = [i.due for i in plan if i.due is not None and i.status in OPEN and i.due > after]
    return min(due) if due else None


def reminder(plan: Sequence[PlanItem], now: float) -> str:
    """The message that starts a cycle for a due step in a conversation without a duty."""
    due = "; ".join(i.text for i in plan
                    if i.due is not None and i.status in OPEN and i.due <= now)
    return (f"Due now in your plan: {due}. This is the time you set: do what "
            "the step says (for a reminder, tell the user), then update the plan.")


def unfinished(plan: Sequence[PlanItem]) -> list[PlanItem]:
    """The steps a turn should not end on: open ones that do not wait for a due time."""
    return [i for i in plan if i.status in OPEN and i.due is None]


def finish(plan: Sequence[PlanItem]) -> str:
    """The message that sends the agent back when it ends a turn with steps open."""
    steps = "; ".join(i.text for i in unfinished(plan))
    return (f"Your plan still has open steps: {steps}. Do them now. If one is "
            "finished, mark it completed with TodoWrite; if it is no longer needed, leave it "
            "out of the list. If one "
            "waits for the user, say what you need from them and end your turn.")


def parse_plan(items: Sequence[dict[str, Any]], now: float) -> list[PlanItem]:
    """TodoWrite's todos; ValueError for a due time that cannot be read, or that is more
    than MAX_DUE_AHEAD_S ahead (one that far could not be shown)."""
    plan = []
    for item in items:
        due = None
        if "due_in_minutes" in item:
            minutes = float(item["due_in_minutes"])
            due = now + 60 * minutes if math.isfinite(minutes) else math.inf
        elif item.get("due"):
            try:
                when = datetime.fromisoformat(item["due"])
                due = (when if when.tzinfo else when.astimezone()).timestamp()
            except (ValueError, OverflowError):
                raise ValueError(f"Cannot read the due time {item['due']!r}: use ISO 8601, "
                                 "e.g. 2026-11-12T09:00.") from None
        if due is not None and due - now > MAX_DUE_AHEAD_S:
            raise ValueError("A due time is at most a hundred years ahead.")
        plan.append(PlanItem(item["content"], PLAN_STATUS[item["status"]], due,
                             item.get("note", "")))
    return plan


def next_wake(duty: Duty, plan: Sequence[PlanItem], now: float | None = None
              ) -> tuple[float, str]:
    """(seconds from now, cause): the earliest open item due, held between the duty's
    intervals; with nothing due, the maximum interval, or for a life the minimum: it is
    awake unless it chose to rest until a later time."""
    now = time.time() if now is None else now
    due = [i.due for i in plan if i.due is not None and i.status in OPEN]
    if due and min(due) - now < duty.max_interval_s:
        return max(duty.min_interval_s, min(due) - now), DUE
    if not due and duty.kind == DutyKind.LIFE:
        return duty.min_interval_s, CHECK_IN
    return duty.max_interval_s, CHECK_IN


def check(duty: Duty) -> Duty:
    """The duty with its instruction trimmed; ValueError when it cannot run."""
    instruction = duty.instruction.strip()
    if not instruction:
        raise ValueError("a duty needs an instruction")
    if not 0 < duty.min_interval_s <= duty.max_interval_s:
        raise ValueError("a duty's intervals must be 0 < minimum <= maximum")
    return Duty(instruction, DutyKind(duty.kind), duty.min_interval_s, duty.max_interval_s,
                duty.model)
