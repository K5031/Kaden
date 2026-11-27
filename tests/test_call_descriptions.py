"""A tool can declare a plain-language description of each call (api.DESCRIPTION_ARG),
which frontends show; the runtime adds it to no tool and passes it on as any argument."""

from __future__ import annotations

from typing import Any

from kaden.api import (
    DESCRIPTION_ARG,
    DESCRIPTION_SCHEMA,
    PermissionPolicy,
    ToolSpec,
    ToolStarted,
)
from tests.conftest import Harness
from tests.fakes.core import ScriptedCore, Step, call

STRICT = {"type": "object", "properties": {"path": {"type": "string"}},
          "required": ["path"], "additionalProperties": False}


async def harness(make: Any, *steps: Step) -> Harness:
    h = await make(core=ScriptedCore([*steps, Step(text="done")]),
                   permissions=PermissionPolicy.ALLOW_WORKSPACE)
    h.tools.instant("read", schema=STRICT)
    h.tools.confined_names = {"read", "own"}
    return h


async def test_offered_schemas_are_the_modules_own(make: Any) -> None:
    """The runtime adds no argument to any tool: a strict schema is offered as declared."""
    h = await harness(make)
    await h.start()
    offered = {s.name: s for s in await h.runtime.list_tools()}
    assert offered["read"].parameters == STRICT
    for spec in offered.values():
        assert DESCRIPTION_ARG not in spec.parameters.get("properties", {}), spec.name


def test_the_shared_schema_asks_for_plain_words() -> None:
    assert DESCRIPTION_SCHEMA["type"] == "string" and "plain" in DESCRIPTION_SCHEMA["description"]


async def test_a_tool_that_declares_it_gets_it_with_the_call(make: Any) -> None:
    h = await harness(make, Step(calls=[call("own", {"path": "a.txt",
                                                     DESCRIPTION_ARG: "Read your notes"},
                                             id="o")]))
    own = {"type": "object", "properties": {"path": {"type": "string"},
                                            DESCRIPTION_ARG: DESCRIPTION_SCHEMA}}
    h.tools.add(ToolSpec("own", "reads a file", own), h.tools.fns["read"])
    await h.start()
    await h.turn("go")
    assert h.tools.received[-1].arguments == {"path": "a.txt", DESCRIPTION_ARG: "Read your notes"}
    [started] = h.rec.of(ToolStarted)
    assert started.call.arguments[DESCRIPTION_ARG] == "Read your notes"  # frontends show it
    [stored] = [b for m in await h.transcript() for b in m.blocks
                if getattr(b, "id", None) == "o"]
    assert stored.arguments[DESCRIPTION_ARG] == "Read your notes"  # and history keeps it


async def test_a_call_without_a_description_runs_as_before(make: Any) -> None:
    h = await harness(make, Step(calls=[call("read", {"path": "a.txt"}, id="r")]))
    await h.start()
    await h.turn("go")
    assert (await h.results())["r"].content.startswith("done")
