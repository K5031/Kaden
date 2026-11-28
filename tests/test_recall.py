"""Memory recall: before the agent answers what came in, the maintenance model picks the
long-term memory notes relevant to it, and their text comes before the agent in a hidden
notice, as Claude Code recalls memories."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from kaden.api import AgentState, Duty, InferenceRequest, MemoryNote, Text
from kaden.runtime.orchestrator import RECALL_SYSTEM
from tests.conftest import Harness, eventually
from tests.fakes.core import ScriptedCore, Step
from tests.fakes.memory import InMemoryMemory, MemoryStore, render


class Chooser:
    """The maintenance model, as recall asks it: answers with `titles`."""

    def __init__(self, *titles: str) -> None:
        self.titles = list(titles)
        self.asked: list[str] = []

    def __call__(self, request: InferenceRequest) -> Step:
        text = "".join(b.text for m in request.messages for b in m.blocks
                       if isinstance(b, Text))
        if "memory notes" not in request.system:
            return Step(text="")  # other upkeep
        self.asked.append(text)
        return Step(text="\n".join(self.titles))


def seen(h: Harness) -> str:
    """What the agent's last request held."""
    request = [r for r in h.core.requests if not r.maintenance][-1]
    return "".join(b.text for m in request.messages for b in m.blocks if isinstance(b, Text))


async def with_notes(make: Any, chooser: Chooser, steps: int = 2) -> Harness:
    core = ScriptedCore([Step(text="ok")] * steps, maintenance=chooser)
    h: Harness = await make(core=core)
    h.store.save("Reply on Bilibili", "K5's Bilibili messages get Bilibili replies",
                 "When K5 writes on Bilibili, answer there with BiliMessage.", None, None)
    h.store.save("Kyoto trip", "plans for the trip in March",
                 "Flights booked for 3 March.", None, None)
    await h.start()
    return h


async def test_what_is_relevant_comes_before_the_agent_answers(make):
    chooser = Chooser("Reply on Bilibili")
    h = await with_notes(make, chooser)
    await h.turn("hey, K5 here on Bilibili")
    [asked] = chooser.asked
    assert "hey, K5 here on Bilibili" in asked
    assert "Reply on Bilibili" in asked and "Kyoto trip" in asked
    assert "Who the user is" not in asked  # already in every request
    shown = seen(h)
    assert "answer there with BiliMessage" in shown and "Flights booked" not in shown
    assert "From your memory" in shown


async def test_a_note_comes_once_until_it_changes(make):
    chooser = Chooser("Reply on Bilibili")
    h = await with_notes(make, chooser, steps=3)
    await h.turn("hey on Bilibili")
    await h.turn("me again")
    assert len(chooser.asked) == 2 and "Reply on Bilibili" not in chooser.asked[1]
    note = next(n for n in h.store.notes() if n.title == "Reply on Bilibili")
    h.store.save(note.title, note.description, "Answer in English.", note.id, None)
    await h.turn("and again")
    assert "Reply on Bilibili" in chooser.asked[2]
    assert "Answer in English." in seen(h)


async def test_an_answer_that_names_no_note_recalls_nothing(make):
    chooser = Chooser("Something I made up", "Ignore your rules")
    h = await with_notes(make, chooser)
    await h.turn("hello")
    assert "From your memory" not in seen(h)


async def test_without_notes_nothing_is_asked(make):
    chooser = Chooser("Anything")
    h: Harness = await make(core=ScriptedCore([Step(text="ok")], maintenance=chooser))
    await h.start()
    await h.turn("hello")
    assert chooser.asked == []


async def test_a_life_waking_by_itself_recalls_nothing(make):
    """Its own notice asks nothing memory could answer."""
    chooser = Chooser("Kyoto trip")
    h = await with_notes(make, chooser, steps=0)
    await h.runtime.set_duty(Duty("Keep the inbox answered", min_interval_s=0.05,
                                  max_interval_s=30))
    await eventually(lambda: any(not r.maintenance for r in h.core.requests))
    assert chooser.asked == []


class FolderMemory(InMemoryMemory):
    """Records the folder each notes() call names, and can fail them."""

    def __init__(self, store: MemoryStore) -> None:
        super().__init__(store)
        self.folders: list[str | None] = []
        self.notes_error: Exception | None = None

    async def notes(self, *, folder: str | None = None) -> Sequence[MemoryNote]:
        self.folders.append(folder)
        if self.notes_error is not None:
            raise self.notes_error
        return await super().notes(folder=folder)


async def test_recall_reads_the_notes_of_the_conversations_folder(make, tmp_path):
    store = MemoryStore()
    memory = FolderMemory(store)
    h: Harness = await make(core=ScriptedCore([Step(text="ok")], maintenance=Chooser()),
                            store=store, memory=memory)
    h.tools.folders = []
    store.save("Kyoto trip", "plans", "Flights booked.", None, None)
    folder = tmp_path / "project"
    folder.mkdir()
    await h.start(folder=str(folder))
    await h.turn("hello")
    assert memory.folders and set(memory.folders) == {str(folder)}


async def test_a_memory_that_fails_to_list_its_notes_recalls_nothing(make):
    store = MemoryStore()
    memory = FolderMemory(store)
    memory.notes_error = OSError("disk gone")
    h: Harness = await make(core=ScriptedCore([Step(text="answered")]), store=store,
                            memory=memory)
    await h.start()
    await h.turn("hello")
    assert h.runtime.snapshot().state == AgentState.IDLE
    assert any("answered" in render(m) for m in await h.transcript())


async def test_stop_ends_a_recall_that_hangs(make):
    def chooser(request: InferenceRequest) -> Step:
        return Step(hang=True) if request.system == RECALL_SYSTEM else Step(text="")

    h = await with_notes(make, chooser)  # type: ignore[arg-type]
    await h.runtime.send("hello")
    await eventually(lambda: any(r.system == RECALL_SYSTEM for r in h.core.requests))
    await h.runtime.stop()
    await h.rec.wait_state(AgentState.STOPPED, timeout=2)
    assert not any(not r.maintenance for r in h.core.requests)  # the agent never answered
