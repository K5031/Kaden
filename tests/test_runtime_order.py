"""The order of things across start, Stop and the cycle loop: what belongs to one
conversation never reaches another, a start that is refused changes nothing, and a Stop
is never overtaken by a cycle."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import pytest

from kaden.api import (
    AgentState,
    Budget,
    Duty,
    DutyKind,
    Message,
    PermissionPolicy,
    PromptKind,
    Text,
)
from tests.conftest import Harness, eventually
from tests.fakes.core import ScriptedCore, Step, call
from tests.fakes.memory import InMemoryMemory, MemoryStore
from tests.fakes.tools import FakeTools
from tests.test_watches import FAST_WATCH, Source, watching

WRITE = {"name": "write", "arguments": {"path": "notes.md"}}


def asked(h: Harness, text: str) -> bool:
    """Whether a request of the agent's held `text`."""
    return any(text in b.text for r in h.core.requests if not r.maintenance
               for m in r.messages for b in m.blocks if isinstance(b, Text))


async def test_a_message_waiting_in_one_conversation_is_kept_there(make: Any) -> None:
    h: Harness = await make(core=ScriptedCore(default=Step(text="ok")))
    first = await h.start()
    h.memory.fail_append = 2  # the message cannot be saved: it waits, and so does the next
    await h.runtime.send("FIRST-ONE")
    await h.rec.wait_state(AgentState.FAILED)
    await h.runtime.send("FIRST-TWO")
    await eventually(lambda: h.rec.states.count(AgentState.FAILED) >= 2)
    second = await h.start()
    await h.turn("hello")
    assert not asked(h, "FIRST-")
    kept = [b.text for m in h.store.conversations[first.id].messages for b in m.blocks
            if isinstance(b, Text)]
    assert kept == ["FIRST-ONE", "FIRST-TWO"]
    assert h.runtime.snapshot().conversation.id == second.id


async def test_a_held_firing_stays_with_its_conversation(make: Any) -> None:
    news, tools = Source(), FakeTools()
    watching(tools, news)
    life = Duty("You are Mira.", DutyKind.LIFE, min_interval_s=30, max_interval_s=60)
    core = ScriptedCore([Step(calls=[call("news", {}, id="w")]), Step(text="")],
                        default=Step(text="ok"))
    h: Harness = await make(core=core, tools=tools, budget=Budget(max_cost_usd_per_day=1e-9),
                            **FAST_WATCH)
    await h.start()
    await h.runtime.set_duty(life)
    await eventually(lambda: news.calls == 1)
    await eventually(lambda: h.runtime.snapshot().state == AgentState.IDLE)
    await news.items.put("NEWS-OF-MIRA")  # its own time, on a spent day: held
    await asyncio.sleep(0.1)
    await h.start()
    await h.turn("hello")
    assert not asked(h, "NEWS-OF-MIRA")


@pytest.mark.parametrize("options", [{"conversation_id": "no-such-one"},
                                     {"folder": "/no/such/folder"}])
async def test_a_start_that_is_refused_leaves_a_proposal_waiting(make: Any,
                                                                 options: dict[str, Any]
                                                                 ) -> None:
    tools = FakeTools()
    tools.instant("write", "written", read_only=False)
    h: Harness = await make(core=ScriptedCore([
        Step(calls=[call("propose", {"call": WRITE, "reason": "Messy"}, id="p")]),
        Step(text="Proposed.")]), tools=tools, permissions=PermissionPolicy.ASK)
    await h.start()
    await h.turn("hi")
    with pytest.raises((KeyError, ValueError)):
        await h.start(**options)
    assert [p.kind for p in h.runtime.snapshot().prompts] == [PromptKind.PROPOSAL]


class HeldAppends(InMemoryMemory):
    """Its appends wait while `gate` is set."""

    gate: asyncio.Event | None = None

    async def append(self, conversation_id: str, messages: Sequence[Message]) -> None:
        if self.gate is not None:
            await self.gate.wait()
        await super().append(conversation_id, messages)


async def test_stop_while_background_outcomes_are_saved_starts_no_cycle(make: Any) -> None:
    store = MemoryStore()
    memory = HeldAppends(store)
    tools = FakeTools()
    tools.gated("bg")
    h: Harness = await make(core=ScriptedCore([Step(calls=[call("bg", id="bg")]),
                                               Step(text="started")],
                                              default=Step(text="again")),
                            tools=tools, store=store, memory=memory, initial_wait_s=0.01)
    await h.start()
    await h.turn("go")
    answered = len([r for r in h.core.requests if not r.maintenance])
    memory.gate = asyncio.Event()
    tools.gates["bg"].set()  # its outcome is being saved, outside a cycle
    await eventually(lambda: not h.runtime.snapshot().operations)
    await h.runtime.send("and now?")  # the loop waits for that save
    await asyncio.sleep(0.05)
    await h.runtime.stop()
    memory.gate.set()
    await h.rec.wait_state(AgentState.STOPPED)
    await asyncio.sleep(0.1)
    assert len([r for r in h.core.requests if not r.maintenance]) == answered
    assert h.runtime.snapshot().state == AgentState.STOPPED

