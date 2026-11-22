"""AskUserQuestion, as in Claude Code: the call waits for the user's answers and returns
them; the agent goes on in the same turn."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kadence.api import (
    AgentState,
    QuestionAnswered,
    QuestionAsked,
    Role,
    RuntimeStateError,
    ToolStatus,
)
from tests.conftest import Harness, eventually
from tests.fakes.core import ScriptedCore, Step, call

DB = {"question": "Which database?", "header": "Database", "multiSelect": False,
      "options": [{"label": "SQLite", "description": "One file"},
                  {"label": "Postgres", "description": "A server"}]}
PARTS = {"question": "Which parts?", "header": "Parts", "multiSelect": True,
         "options": [{"label": "API", "description": ""}, {"label": "UI", "description": ""}]}


async def asking(make: Any, questions: list[dict[str, Any]]) -> Harness:
    core = ScriptedCore([Step(calls=[call("AskUserQuestion", {"questions": questions},
                                          id="q")]),
                         Step(text="Setting it up.")])
    h: Harness = await make(core=core)
    await h.start()
    await h.runtime.send("set it up")
    await eventually(lambda: h.runtime.snapshot().pending_question is not None)
    return h


async def test_the_call_waits_for_the_answers_and_returns_them(make: Any) -> None:
    h = await asking(make, [DB, PARTS])
    asked = h.runtime.snapshot().pending_question
    assert asked == QuestionAsked("q", (DB, PARTS))
    assert h.runtime.snapshot().state == AgentState.AWAITING_PERMISSION
    assert len(h.core.requests) == 1  # nothing goes on while it waits
    await h.runtime.answer_question("q", {"Which database?": "SQLite",
                                          "Which parts?": "API, UI"})
    await h.rec.wait_state(AgentState.IDLE, count=2)
    result = (await h.results())["q"]
    assert result.status == ToolStatus.OK
    assert '"Which database?"="SQLite"' in result.content
    assert '"Which parts?"="API, UI"' in result.content
    assert len(h.core.requests) == 2  # the same turn goes on
    assert h.rec.of(QuestionAnswered)[-1] == QuestionAnswered(
        "q", {"Which database?": "SQLite", "Which parts?": "API, UI"})
    assert h.runtime.snapshot().pending_question is None


async def test_a_message_while_it_waits_answers_it_in_words(make: Any) -> None:
    h = await asking(make, [DB])
    await h.runtime.send("Whatever you think is best")
    await h.rec.wait_state(AgentState.IDLE, count=2)
    result = (await h.results())["q"]
    assert result.status == ToolStatus.OK and "message that follows" in result.content
    messages = await h.transcript()
    at = next(i for i, m in enumerate(messages)
              if any(getattr(b, "call_id", None) == "q" for b in m.blocks))
    answer = messages[at + 1]  # the user's own message, right after the result
    assert answer.role == Role.USER and answer.from_user
    assert [b.text for b in answer.blocks if hasattr(b, "text")] == [
        "Whatever you think is best"]


async def test_stop_ends_the_question(make: Any) -> None:
    h = await asking(make, [DB])
    await h.runtime.stop()
    await h.rec.wait_state(AgentState.STOPPED, timeout=2)
    assert (await h.results())["q"].status == ToolStatus.CANCELLED
    assert h.rec.of(QuestionAnswered)[-1] == QuestionAnswered("q", None)
    assert h.runtime.snapshot().pending_question is None


async def test_answering_what_is_not_asked_is_refused(make: Any) -> None:
    h = await asking(make, [DB])
    with pytest.raises(RuntimeStateError):
        await h.runtime.answer_question("other", {"Which database?": "SQLite"})
    await h.runtime.answer_question("q", {"Which database?": "Postgres"})
    await h.rec.wait_state(AgentState.IDLE, count=2)
    with pytest.raises(RuntimeStateError):
        await h.runtime.answer_question("q", {"Which database?": "SQLite"})
    await asyncio.sleep(0)
