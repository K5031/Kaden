"""A tool can declare a plain-language description of each call (api.DESCRIPTION_ARG),
which frontends show; the runtime adds it to no tool and passes it on as any argument."""

from __future__ import annotations

import asyncio
from typing import Any

from kaden.api import (
    DESCRIPTION_ARG,
    DESCRIPTION_SCHEMA,
    Decision,
    PermissionPolicy,
    PromptOpened,
    ToolSpec,
    ToolStarted,
    ToolTitles,
)
from tests.conftest import Harness, eventually
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


async def test_the_runtime_hands_out_tool_titles_from_its_listing(make: Any) -> None:
    """Frontends name a step without a sentence by its tool's title (ToolSpec.title): the
    runtime lists the tools soon after a conversation opens, without waiting for them, and
    says so (ToolTitles, the snapshot), so history is named before any request; a
    permission prompt carries its tool's. No frontend has to list the tools itself."""
    from dataclasses import replace

    h = await make(core=ScriptedCore([Step(calls=[call("add", {"a": 1}, id="a")]),
                                      Step(text="done")]),
                   permissions=PermissionPolicy.ASK)
    spec = h.tools.instant("add", read_only=False)
    h.tools.specs["add"] = replace(spec, title="Add numbers")
    await h.start()
    await eventually(lambda: h.runtime.snapshot().tool_titles == {"add": "Add numbers"})
    assert [e.titles for e in h.rec.of(ToolTitles)] == [{"add": "Add numbers"}]
    task = asyncio.ensure_future(h.turn("add"))
    await eventually(lambda: h.rec.of(PromptOpened))
    [opened] = h.rec.of(PromptOpened)
    assert opened.prompt.details["title"] == "Add numbers"
    await h.runtime.respond(opened.prompt.id, Decision.ALLOW_ONCE)
    await task
    assert len(h.rec.of(ToolTitles)) == 1  # said again only when they change


async def test_listings_never_overlap(make: Any) -> None:
    """The listing made as a conversation opens and a request's run one after another, so
    the request sees what the first found and an older result never replaces a newer one."""
    h = await make(core=ScriptedCore([Step(text="done")]))
    real, running, most = h.tools.list_tools, [0], [0]

    async def slow() -> Any:
        running[0] += 1
        most[0] = max(most[0], running[0])
        try:
            await asyncio.sleep(0.05)
            return await real()
        finally:
            running[0] -= 1

    h.tools.list_tools = slow  # type: ignore[method-assign]
    await h.start()
    await h.turn("hi")
    await eventually(lambda: running[0] == 0)
    assert most[0] == 1


async def test_a_proposal_carries_its_tools_title(make: Any) -> None:
    from dataclasses import replace

    proposed = {"name": "add", "arguments": {"a": 1}}
    h = await make(core=ScriptedCore([
        Step(calls=[call("propose", {"call": proposed, "reason": "Sums"}, id="p")]),
        Step(text="Proposed.")]))
    spec = h.tools.instant("add", read_only=False)
    h.tools.specs["add"] = replace(spec, title="Add numbers")
    await h.start()
    await h.turn("add")
    [opened] = [e for e in h.rec.of(PromptOpened) if e.prompt.kind == "proposal"]
    assert opened.prompt.details["title"] == "Add numbers"
