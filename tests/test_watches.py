"""Standing watches: a watch tool's call is answered at once with a watch id, and the
runtime keeps it: each outcome is a notice that wakes the conversation, it watches again,
it starts again after a restart, and TaskStop ends it. A Tools module may keep watches for
the user, whose outcomes wake a life as the user's own message does."""

from __future__ import annotations

import asyncio
from typing import Any

from kaden.api import (
    AgentState,
    Budget,
    Duty,
    DutyKind,
    SettingsUpdate,
    StandingWatch,
    Text,
    ToolOutput,
    ToolSpec,
    ToolStatus,
    Usage,
    UsageUpdated,
)
from tests.conftest import Harness, eventually
from tests.fakes.core import ScriptedCore, Step, call
from tests.fakes.tools import FakeTools

FAST_WATCH = {"watch_retry_s": 0.02, "watch_retry_max_s": 0.08}


class Source:
    """Something outside to watch: each call waits for the next thing put in."""

    def __init__(self) -> None:
        self.items: asyncio.Queue[str | ToolOutput] = asyncio.Queue()
        self.calls = 0
        self.confirmed: list[str] = []

    async def run(self, c: Any, ctx: Any) -> ToolOutput:
        self.calls += 1
        item = await self.items.get()
        if isinstance(item, ToolOutput):
            return item
        return ToolOutput(item, confirm=lambda: self.confirmed.append(item))


def watching(tools: FakeTools, source: Source, name: str = "news") -> None:
    tools.add(ToolSpec(name, f"Waits for {name}", {"type": "object", "properties": {
        "topic": {"type": "string"}}}, True, watch=True), source.run)


class Keeping(FakeTools):
    """A Tools module that keeps a watch for the user while `keep`."""

    keep = True
    as_user = True

    async def standing_watches(self) -> list[StandingWatch]:
        return [StandingWatch("inbox", as_user=self.as_user)] if self.keep else []


def seen(h: Harness, text: str) -> bool:
    return any(text in b.text for r in h.core.requests for m in r.messages for b in m.blocks
               if isinstance(b, Text))


async def idle(h: Harness) -> None:
    await eventually(lambda: h.runtime.snapshot().state == AgentState.IDLE)


async def test_a_watch_is_answered_at_once_and_wakes_it_each_time_it_fires(make):
    source, tools = Source(), FakeTools()
    watching(tools, source)
    core = ScriptedCore([Step(calls=[call("news", {"topic": "birds"}, id="w")]),
                         Step(text="Watching."), Step(text="Oh, lyrebirds!"),
                         Step(text="And another.")])
    h: Harness = await make(core=core, tools=tools, **FAST_WATCH)
    await h.start()
    await h.turn("tell me when there is bird news")
    result = (await h.results())["w"]
    assert result.status is ToolStatus.OK and "Watching (w1)" in result.content
    assert "TaskStop" in result.content
    assert not h.runtime._ops  # not a background operation: nothing is "running"
    await eventually(lambda: source.calls == 1)
    await source.items.put("Lyrebirds copy chainsaws")
    await eventually(lambda: seen(h, "[Watch w1, news] Lyrebirds copy chainsaws"))
    await idle(h)
    await eventually(lambda: source.calls == 2)  # watching again
    await source.items.put("Pigeons have accents")
    await eventually(lambda: seen(h, "Pigeons have accents"))
    assert len(h.core.requests) == 4


async def test_an_outcome_counts_as_delivered_once_it_is_in_the_conversation(make):
    source, tools = Source(), FakeTools()
    watching(tools, source)
    core = ScriptedCore([Step(calls=[call("news", {}, id="w")]), Step(text="ok"),
                         Step(text="noted")])
    h: Harness = await make(core=core, tools=tools, **FAST_WATCH)
    await h.start()
    await h.turn("watch the news")
    await eventually(lambda: source.calls == 1)
    await source.items.put("a headline")
    await eventually(lambda: source.confirmed == ["a headline"])
    texts = [b.text for m in await h.transcript() for b in m.blocks if isinstance(b, Text)]
    assert any("a headline" in t for t in texts)  # in it before it was confirmed


async def test_taskstop_ends_a_watch_and_a_watch_never_holds_anything_up(make):
    source, tools = Source(), FakeTools()
    watching(tools, source)
    core = ScriptedCore([Step(calls=[call("news", {}, id="w")]), Step(text="ok"),
                         Step(calls=[call("TaskStop", {"task_id": "w1"}, id="s")]),
                         Step(text="stopped"), Step(text="hi")])
    h: Harness = await make(core=core, tools=tools, **FAST_WATCH)
    first = await h.start()
    await h.turn("watch the news")
    await h.turn("stop watching")
    assert "Stopped watch w1" in (await h.results())["s"].content
    await asyncio.sleep(0.05)
    await source.items.put("too late")
    await asyncio.sleep(0.1)
    assert not seen(h, "too late")
    # Another, watching again: switching conversations is not held up by it.
    core.add(Step(calls=[call("news", {}, id="w2")]), Step(text="watching"))
    await h.turn("watch again")
    await h.start()  # a new conversation, while the first one's watch exists
    await h.start(first.id)


async def test_a_second_call_for_the_same_watch_gives_the_one_there_is(make):
    source, tools = Source(), FakeTools()
    watching(tools, source)
    core = ScriptedCore([Step(calls=[call("news", {"topic": "x"}, id="a")]),
                         Step(calls=[call("news", {"topic": "x"}, id="b")]), Step(text="ok")])
    h: Harness = await make(core=core, tools=tools, **FAST_WATCH)
    await h.start()
    await h.turn("watch x")
    results = await h.results()
    assert "Watching (w1)" in results["a"].content
    assert "Already watching (w1)" in results["b"].content


async def test_watches_go_on_after_a_restart_without_a_word(make):
    source, tools = Source(), FakeTools()
    watching(tools, source)
    h: Harness = await make(core=ScriptedCore([Step(calls=[call("news", {}, id="w")]),
                                               Step(text="ok")]), tools=tools, **FAST_WATCH)
    info = await h.start()
    await h.turn("watch the news")
    await eventually(lambda: source.calls == 1)
    before = len(await h.transcript())
    await h.close()
    again_source, again_tools = Source(), FakeTools()
    watching(again_tools, again_source)
    again: Harness = await make(core=ScriptedCore([Step(text="Big news!")]),
                                tools=again_tools, store=h.store, data_dir=h.data_dir,
                                **FAST_WATCH)
    await again.start(info.id)
    await eventually(lambda: again_source.calls == 1)  # watching again
    assert len(await again.transcript()) == before  # nothing said of the restart
    await again_source.items.put("after the restart")
    await eventually(lambda: seen(again, "[Watch w1, news] after the restart"))


async def test_a_failing_watch_says_so_once_and_one_that_cannot_pass_ends(make):
    source, tools = Source(), FakeTools()
    watching(tools, source)
    core = ScriptedCore([Step(calls=[call("news", {}, id="w")]), Step(text="ok"),
                         Step(text="hm"), Step(text="good"), Step(text="ah")])
    h: Harness = await make(core=core, tools=tools, **FAST_WATCH)
    await h.start()
    await h.turn("watch the news")
    for _ in range(3):
        await source.items.put(ToolOutput("the site is down", is_error=True))
    await source.items.put("back up")
    await eventually(lambda: seen(h, "back up"))
    failures = [b.text for r in h.core.requests[-1:] for m in r.messages for b in m.blocks
                if isinstance(b, Text) and "the site is down" in b.text]
    assert len(failures) == 1  # told once, not three times
    await idle(h)
    calls = source.calls
    await source.items.put(ToolOutput("logged out", is_error=True, retry=False))
    await eventually(lambda: seen(h, "logged out"))
    await asyncio.sleep(0.1)
    assert source.calls == calls  # not watched again


async def test_a_watch_kept_for_the_user_wakes_a_life_as_the_user_does(make):
    """The user's message by another way in: it wakes the life even on a day whose own
    time is spent, where a watch of its own waits for the next day."""
    inbox, news = Source(), Source()
    tools = Keeping()
    watching(tools, inbox, "inbox")
    watching(tools, news, "news")
    life = Duty("You are Mira.", DutyKind.LIFE, min_interval_s=30, max_interval_s=60)
    core = ScriptedCore([Step(calls=[call("news", {}, id="w")]), Step(text=""),
                         Step(text="Hi K5!"), Step(text="")])
    h: Harness = await make(core=core, tools=tools, budget=Budget(max_cost_usd_per_day=1e-9),
                            **FAST_WATCH)
    await h.start()
    await h.runtime.set_duty(life)
    await eventually(lambda: inbox.calls == 1 and news.calls == 1)  # kept, and its own
    await idle(h)
    asked = len(h.core.requests)  # its day's amount is spent by now
    await news.items.put("a video")
    await asyncio.sleep(0.15)
    assert len(h.core.requests) == asked  # its own: tomorrow
    await inbox.items.put("hey Mira")
    await eventually(lambda: seen(h, "hey Mira"))  # the user's: now
    await idle(h)
    tools.keep = False  # no longer kept (logged out, say): it ends after the next turn
    await h.turn("bye")
    await eventually(lambda: "inbox" not in {w.name for w in h.runtime._watch_list()})


async def test_a_watch_kept_as_its_own_time_waits_for_the_next_day(make):
    """A module may keep a watch that is the life's own business (an inbox anyone can
    write to): its firing counts as the life's own time, and waits on a spent day."""
    inbox = Source()
    tools = Keeping()
    tools.as_user = False
    watching(tools, inbox, "inbox")
    life = Duty("You are Mira.", DutyKind.LIFE, min_interval_s=30, max_interval_s=60)
    h: Harness = await make(core=ScriptedCore([Step(text="")]), tools=tools,
                            budget=Budget(max_cost_usd_per_day=1e-9), **FAST_WATCH)
    await h.start()
    await h.runtime.set_duty(life)
    await eventually(lambda: inbox.calls == 1)
    await idle(h)
    asked = len(h.core.requests)  # its day's amount is spent by now
    await inbox.items.put("spam, spam, spam")
    await asyncio.sleep(0.15)
    assert len(h.core.requests) == asked  # tomorrow
    assert inbox.calls == 1  # and it looks again only once that is delivered
    assert inbox.confirmed == []



async def test_a_firing_past_the_budget_waits_without_starting_a_turn(make):
    """Once a turn failed on the spent budget, a firing waits in the queue: no turn starts
    that could only fail again, and it is delivered once the agent goes on."""
    source, tools = Source(), FakeTools()
    watching(tools, source)
    core = ScriptedCore([Step(calls=[call("news", {}, id="w")], usage=Usage(3000, 10)),
                         Step(text="Watching.", usage=Usage(6990, 0))])
    h: Harness = await make(core=core, tools=tools, budget=Budget(max_tokens=10_000),
                            **FAST_WATCH)
    await h.start()
    await h.runtime.send("watch the news")
    await eventually(lambda: source.calls == 1)
    await idle(h)  # with the budget spent
    await source.items.put("first")  # as a message would: one turn, which fails
    await eventually(lambda: h.runtime.snapshot().state == AgentState.FAILED
                     and source.calls == 2)
    asked, upkeep = len(h.core.requests), h.memory.maintain_calls
    await source.items.put("second")
    await eventually(lambda: h.runtime.snapshot().queued_messages)
    await asyncio.sleep(0.15)
    assert len(h.core.requests) == asked and h.memory.maintain_calls == upkeep
    assert h.runtime.snapshot().state == AgentState.FAILED
    assert source.confirmed == ["first"]  # the second waits to be delivered
    await h.runtime.update_settings(SettingsUpdate(budget=Budget(max_tokens=1_000_000)))
    await eventually(lambda: source.confirmed == ["first", "second"])  # the agent goes on
    assert seen(h, "second")


async def test_a_watch_looks_again_only_once_its_news_is_in_the_conversation(make):
    """Until then what it saw is not marked seen: looking again would see it again."""
    source, tools = Source(), FakeTools()
    watching(tools, source)
    core = ScriptedCore([Step(calls=[call("news", {}, id="w")]), Step(text="ok"),
                         Step(text="noted")])
    h: Harness = await make(core=core, tools=tools, **FAST_WATCH)
    await h.start()
    await h.turn("watch the news")
    await eventually(lambda: source.calls == 1)
    looks: list[list[str]] = []
    original = source.run

    async def run(c: Any, ctx: Any) -> ToolOutput:
        looks.append(list(source.confirmed))  # what was confirmed when it looked
        return await original(c, ctx)
    tools.add(ToolSpec("news", "Waits for news", {"type": "object"}, True, watch=True), run)
    await source.items.put("a headline")
    await eventually(lambda: source.calls == 2)
    assert looks == [["a headline"]]


async def test_a_watch_is_counted_once(make):
    source, tools = Source(), FakeTools()
    watching(tools, source)
    core = ScriptedCore([Step(calls=[call("news", {"topic": "x"}, id="a")]),
                         Step(calls=[call("news", {"topic": "x"}, id="b")]), Step(text="ok")])
    h: Harness = await make(core=core, tools=tools, **FAST_WATCH)
    await h.start()
    await h.turn("watch x")
    assert h.runtime.snapshot().usage.tool_calls == 1  # the second is the one there is
    assert max(e.snapshot.tool_calls for e in h.rec.of(UsageUpdated)) == 1


class Shuffling(Keeping):
    """Keeps one watch, its arguments in another order each time it is asked."""

    asked = 0

    async def standing_watches(self) -> list[StandingWatch]:
        self.asked += 1
        arguments = {"a": 1, "b": 2} if self.asked % 2 else {"b": 2, "a": 1}
        return [StandingWatch("inbox", arguments, as_user=True)]


async def test_a_kept_watch_is_kept_once_whatever_the_order_of_its_arguments(make):
    inbox, tools = Source(), Shuffling()
    tools.add(ToolSpec("inbox", "Waits for mail", {"type": "object"}, True, watch=True),
              inbox.run)
    life = Duty("You are Mira.", DutyKind.LIFE, min_interval_s=30, max_interval_s=60)
    h: Harness = await make(core=ScriptedCore(default=Step(text="")), tools=tools,
                            **FAST_WATCH)
    await h.start()
    await h.runtime.set_duty(life)
    await idle(h)
    await h.turn("hello")
    await eventually(lambda: tools.asked >= 2)
    await asyncio.sleep(0.1)  # what it asked is acted on
    assert [w.name for w in h.runtime._watch_list()] == ["inbox"]
