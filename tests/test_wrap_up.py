"""The wrap-up card: when the user has a decision to make, the agent ends its turn with a
question and options to click, and an optional recap. An option sends its label as the user's message,
or runs the one call it carries."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kaden.api import (
    AgentState,
    Author,
    Message,
    OperationResult,
    PermissionPolicy,
    Role,
    ToolCall,
    ToolResult,
    ToolStatus,
)
from kaden.frontends.common import Card, card_of
from tests.conftest import Harness, asked, eventually
from tests.fakes.core import ScriptedCore, Step, call
from tests.fakes.tools import FakeTools
from tests.test_desktop_window import qapp  # noqa: F401 - a fixture

RECAP = "Committed the fix. Not pushed yet."
PUSH = {"name": "write", "arguments": {"path": "remote"}}
WRAP = {"recap": RECAP, "question": "Push it?",
        "options": [{"label": "Yes, push"}, {"label": "Not now"}]}
ACTION = {**WRAP, "options": [{"label": "Yes, push", "call": PUSH}, {"label": "Not now"}]}


async def harness(make: Any, steps: list[Step]) -> Harness:
    tools = FakeTools()
    tools.instant("read", "contents")
    tools.instant("write", "pushed", read_only=False)
    h: Harness = await make(core=ScriptedCore(steps), tools=tools,
                            permissions=PermissionPolicy.ALLOW_READ_ONLY)
    await h.start()
    return h


def blocks(h: Harness) -> list[Any]:
    return [b for m in h.store.conversations[h.conversation.id].messages for b in m.blocks]


def results(h: Harness) -> dict[str, ToolResult]:
    return {b.call_id: b for b in blocks(h) if isinstance(b, ToolResult)}


def outcomes(h: Harness) -> dict[str, OperationResult]:
    return {b.call_id: b for b in blocks(h) if isinstance(b, OperationResult)}


async def test_wrapping_up_ends_the_turn_without_another_request(make):
    h = await harness(make, [
        Step(text="Committed.", calls=[call("wrap_up", WRAP, id="w")]),
        Step(text="never asked for")])
    await h.turn("commit it")
    assert len(h.core.requests) == 1
    assert results(h)["w"].status == ToolStatus.OK
    assert h.runtime.snapshot().state == AgentState.IDLE
    [wrap] = [b for b in blocks(h) if getattr(b, "name", "") == "wrap_up"]
    assert card_of(wrap) == Card("w", RECAP, "Push it?", ("Yes, push", "Not now"), None)


async def test_wrapping_up_with_other_calls_is_an_error(make):
    h = await harness(make, [
        Step(calls=[call("read", id="r"), call("wrap_up", WRAP, id="w")]),
        Step(text="Read it.")])
    await h.turn("go")
    await eventually(lambda: len(h.core.requests) == 2)
    assert results(h)["r"].status == ToolStatus.OK
    assert results(h)["w"].status == ToolStatus.ERROR and "alone" in results(h)["w"].content


@pytest.mark.parametrize("arguments", [
    {"recap": RECAP},  # no decision
    {"options": [{"label": "Yes"}]},  # no question
    {"question": "Push?", "options": []},
    {"question": "Push?", "options": [{"label": str(i)} for i in range(4)]},
    {"question": "Push?", "options": [{"label": ""}]},
    {"question": "Push?", "options": [{"label": "A", "call": PUSH}, {"label": "B", "call": PUSH}]},
    {"question": "Push?", "options": [{"label": "A", "call": {"name": "nope"}}]},
])
async def test_an_invalid_card_is_an_error(make, arguments: dict[str, Any]):
    h = await harness(make, [
        Step(calls=[call("wrap_up", arguments, id="w")]), Step(text="ok")])
    await h.turn("go")
    await eventually(lambda: len(h.core.requests) == 2)
    assert results(h)["w"].status == ToolStatus.ERROR
    assert tuple(p for p in h.runtime.snapshot().prompts if p.kind == "proposal") == ()


async def test_an_option_with_a_call_runs_it_when_clicked(make):
    h = await harness(make, [
        Step(text="Committed.", calls=[call("wrap_up", ACTION, id="w")]),
        Step(text="Pushed.")])
    await h.turn("commit it")
    assert len(h.core.requests) == 1  # the turn ended
    assert results(h)["w"].status == ToolStatus.BACKGROUNDED
    [made] = asked(h.rec, "proposal")
    assert made.details["card"] and made.id == "w" and made.call.name == "write"
    assert h.tools.started == []
    await h.runtime.respond("w", True)
    outcome = await eventually(lambda: outcomes(h).get("w"))
    assert outcome.status == ToolStatus.OK and "pushed" in outcome.content
    await eventually(lambda: len(h.core.requests) == 2)  # woken with the result


async def test_writing_instead_withdraws_the_option_before_the_message(make):
    h = await harness(make, [
        Step(text="Committed.", calls=[call("wrap_up", ACTION, id="w")]),
        Step(text="Fine, not pushing."), Step(text="never asked for")])
    await h.turn("commit it")
    await h.runtime.send("Leave it for now")
    await eventually(lambda: h.runtime.snapshot().state == AgentState.IDLE
                     and len(h.core.requests) == 2)
    assert tuple(p for p in h.runtime.snapshot().prompts if p.kind == "proposal") == ()
    assert h.tools.started == []
    sent = h.core.requests[1].messages
    texts = [repr(m) for m in sent]
    withdrawn = next(i for i, t in enumerate(texts) if "OperationResult" in t and "'w'" in t)
    asked = next(i for i, t in enumerate(texts) if "Leave it for now" in t)
    assert withdrawn < asked
    await asyncio.sleep(0.2)
    assert len(h.core.requests) == 2  # the withdrawal woke nothing on its own


@pytest.mark.usefixtures("qapp")
async def test_the_desktop_shows_the_card_and_sends_a_reply(make):
    from PySide6.QtWidgets import QLabel, QPushButton, QToolButton

    from kaden.frontends.desktop.messages import WrapUpCard
    from kaden.frontends.desktop.viewmodel import Controller, ConversationModel
    from kaden.frontends.desktop.window import MainWindow

    h = await harness(make, [
        Step(text="Committed.", calls=[call("wrap_up", WRAP, id="w")]),
        Step(text="Pushed.")])
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller)
    asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    window.show()
    await h.turn("commit it")
    [card] = await eventually(lambda: window.findChildren(WrapUpCard))
    assert [label.text() for label in card.findChildren(QLabel)] == [RECAP, "Push it?"]
    buttons = card.findChildren(QPushButton)
    assert [b.text() for b in buttons] == ["Yes, push", "Not now"]
    assert buttons[0].property("primary") and not buttons[1].property("primary")
    assert card.findChildren(QToolButton)[0].accessibleName() == "Close"
    assert not [i for i in controller.model.items
                if getattr(i, "name", "") == "wrap_up"]  # not shown as a step
    buttons[0].click()
    await eventually(lambda: len(h.core.requests) == 2)
    assert "Yes, push" in repr(h.core.requests[-1].messages[-1])
    await eventually(lambda: all(c.isHidden() for c in window.findChildren(WrapUpCard)))


@pytest.mark.usefixtures("qapp")
async def test_the_desktop_runs_the_option_with_a_call(make):
    from PySide6.QtWidgets import QPushButton

    from kaden.frontends.desktop.messages import WrapUpCard
    from kaden.frontends.desktop.prompts import ProposalCard
    from kaden.frontends.desktop.viewmodel import Controller, ConversationModel
    from kaden.frontends.desktop.window import MainWindow

    h = await harness(make, [
        Step(text="Committed.", calls=[call("wrap_up", ACTION, id="w")]),
        Step(text="Pushed.")])
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller)
    asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    window.show()
    await h.turn("commit it")
    await eventually(lambda: "w" in controller.model.prompts)
    await eventually(lambda: controller.model.card is not None)
    card = window.findChildren(WrapUpCard)[-1]
    assert not window.findChildren(ProposalCard)
    card.findChildren(QPushButton)[0].click()
    outcome = await eventually(lambda: outcomes(h).get("w"))
    assert "pushed" in outcome.content
    await eventually(lambda: len(h.core.requests) == 2)
    assert "Yes, push" not in [getattr(b, "text", None) for b in blocks(h)]  # not sent


@pytest.mark.usefixtures("qapp")
async def test_closing_the_card_puts_it_away(make):
    from PySide6.QtWidgets import QToolButton

    from kaden.frontends.desktop.messages import WrapUpCard
    from kaden.frontends.desktop.viewmodel import Controller, ConversationModel
    from kaden.frontends.desktop.window import MainWindow

    h = await harness(make, [Step(text="Committed.", calls=[call("wrap_up", WRAP, id="w")])])
    await h.turn("commit it")
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller)
    asyncio.create_task(controller.run())
    window.show()
    await eventually(lambda: controller.model.card is not None)  # shown on reopening
    card = window.findChildren(WrapUpCard)[-1]
    card.findChildren(QToolButton)[0].click()
    await eventually(lambda: all(c.isHidden() for c in window.findChildren(WrapUpCard)))
    assert controller.model.card is None and len(h.core.requests) == 1


def test_cards_come_from_wrap_up_and_ask_for_help():
    assert card_of(ToolCall("a", "wrap_up", ACTION)) == Card(
        "a", RECAP, "Push it?", ("Yes, push", "Not now"), 0)
    card = card_of(ToolCall("a", "wrap_up", ACTION))
    assert card.runs(0, {"a"}) and not card.runs(1, {"a"}) and not card.runs(0, set())
    assert card_of(ToolCall("b", "wrap_up", {"question": "Push?", "options": [{"label": "Yes"}]})
                   ) == Card("b", "", "Push?", ("Yes",), None)
    assert card_of(ToolCall("c", "ask_for_help", {"question": "Which?", "replies": ["A"]})) == (
        Card("c", "", "Which?", ("A",), None))
    assert card_of(ToolCall("d", "ask_for_help", {"question": "Which?"})) is None
    assert card_of(ToolCall("e", "read", WRAP)) is None


def _committed(message: Message) -> object:
    from kaden.api import MessageCommitted

    return MessageCommitted(message)


async def test_a_background_result_or_notice_leaves_the_card_up(make):
    from kaden.api import OperationResult
    from kaden.api import Text as TextBlock
    from kaden.frontends.desktop.viewmodel import ConversationModel

    model = ConversationModel()
    wrap = ToolCall("w", "wrap_up", WRAP)
    model.apply(_committed(Message("a", Role.ASSISTANT, (wrap,), 0.0)))
    assert model.card is not None
    model.apply(_committed(Message(
        "b", Role.USER, (OperationResult("op", ToolStatus.OK, "built"),), 0.0)))
    model.apply(_committed(Message("c", Role.USER, (TextBlock("Sub-session ended."),), 0.0,
                                   Author.KADEN)))
    assert model.card is not None  # neither is the user's answer
    model.apply(_committed(Message("d", Role.USER, (TextBlock("Not now"),), 0.0)))
    assert model.card is None


def test_a_closed_card_stays_closed_when_the_history_is_reloaded():
    from kaden.frontends.desktop.viewmodel import ConversationModel

    model = ConversationModel()
    wrap = ToolCall("w", "wrap_up", WRAP)
    model.apply(_committed(Message("a", Role.ASSISTANT, (wrap,), 0.0)))
    model.close_card()
    model.apply(_committed(Message("a2", Role.ASSISTANT, (wrap,), 0.0)))  # as after a recover
    assert model.card is None


@pytest.mark.usefixtures("qapp")
async def test_options_are_told_apart_by_position_not_label(make):
    from PySide6.QtWidgets import QPushButton

    from kaden.frontends.desktop.messages import WrapUpCard
    from kaden.frontends.desktop.viewmodel import Controller, ConversationModel
    from kaden.frontends.desktop.window import MainWindow

    same = {**WRAP, "options": [{"label": "Yes", "call": PUSH}, {"label": "Yes"}]}
    h = await harness(make, [
        Step(text="Committed.", calls=[call("wrap_up", same, id="w")]), Step(text="ok")])
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller)
    asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    await h.turn("commit it")
    await eventually(lambda: "w" in controller.model.prompts)
    window.findChildren(WrapUpCard)[-1].findChildren(QPushButton)[1].click()  # the reply
    await eventually(lambda: len(h.core.requests) == 2)
    assert h.tools.started == []  # the call did not run
    assert "Yes" in [getattr(b, "text", None) for b in blocks(h)]
