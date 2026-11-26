"""Budget semantics (SPEC section 9)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from kaden.api import (
    AgentState,
    Budget,
    Decision,
    Failure,
    FailureKind,
    PermissionPolicy,
    SettingsUpdate,
    ToolFinished,
    ToolStatus,
    TransientCoreError,
    Usage,
)
from tests.conftest import Harness, asked, eventually
from tests.fakes.core import ScriptedCore, Step, call
from tests.fakes.tools import FakeTools


async def test_tokens_count_input_and_output(make: Any) -> None:
    core = ScriptedCore(default=Step(text="ok", usage=Usage(300, 40, cached_tokens=200)))
    h: Harness = await make(core=core, budget=Budget(max_tokens=10_000))
    await h.start()
    await h.turn("a")
    await h.turn("b")
    usage = h.runtime.snapshot().usage
    assert usage.usage.input_tokens == 600 and usage.usage.output_tokens == 80
    assert usage.usage.cached_tokens == 400
    assert usage.budget_remaining["max_tokens"] == 10_000 - 680  # cached not added again


async def test_cost_reported(make: Any) -> None:
    core = ScriptedCore(default=Step(text="ok", usage=Usage(1000, 100, cost_usd=0.5)))
    h: Harness = await make(core=core, budget=Budget(max_cost_usd=10))
    await h.start()
    await h.turn("a")
    assert h.runtime.snapshot().usage.usage.cost_usd == pytest.approx(0.5)
    assert h.runtime.snapshot().usage.budget_remaining["max_cost_usd"] == pytest.approx(9.5)


async def test_cost_from_prices(make: Any) -> None:
    usage = Usage(1_000_000, 500_000, cached_tokens=200_000)
    h: Harness = await make(core=ScriptedCore(default=Step(text="ok", usage=usage)))
    await h.start()
    await h.turn("a")
    # fake-1: input 1.0, cached 0.5, output 2.0 USD per Mtok
    assert h.runtime.snapshot().usage.usage.cost_usd == pytest.approx(0.8 + 0.1 + 1.0)


async def test_cost_budget_requires_prices(make: Any) -> None:
    h: Harness = await make(budget=Budget(max_cost_usd=1.0))
    with pytest.raises(ValueError):
        await h.start(model="fake-2")
    await h.start(model="fake-1")
    with pytest.raises(ValueError):
        await h.runtime.select_model("fake-2")


async def test_duration_counts_running_time(make: Any) -> None:
    tools = FakeTools()
    tools.slow("fg", 0.2)
    tools.slow("bg", 0.6)
    core = ScriptedCore([Step(calls=[call("fg")]), Step(calls=[call("bg", id="bg")]),
                         Step(text="ok")])
    h: Harness = await make(core=core, tools=tools, initial_wait_s=0.3,
                            budget=Budget(max_duration_s=100))
    await h.start()
    await h.turn("go")
    await h.rec.wait_for(lambda r: any(e.result.call_id == "bg" for e in r.of(ToolFinished)))
    elapsed = h.runtime.snapshot().usage.elapsed_s
    assert 0.75 <= elapsed < 1.5  # fg 0.2 + bg 0.6 (background included), not serial idle time
    await asyncio.sleep(0.3)
    assert h.runtime.snapshot().usage.elapsed_s == pytest.approx(elapsed, abs=0.01)


async def test_duration_excludes_permission_wait_and_backoff(make: Any) -> None:
    tools = FakeTools()
    tools.instant("write", read_only=False)
    core = ScriptedCore([
        Step(error=TransientCoreError("busy", retry_after=0.4)),
        Step(calls=[call("write")]),
        Step(text="ok"),
    ])
    h: Harness = await make(core=core, tools=tools, permissions=PermissionPolicy.ASK,
                            budget=Budget(max_duration_s=100))
    await h.start()
    await h.runtime.send("go")
    request = (await h.rec.wait_for(lambda r: asked(r)))[0]
    await asyncio.sleep(0.4)
    await h.runtime.respond(request.id, Decision.ALLOW_ONCE)
    await h.rec.wait_state(AgentState.IDLE, count=2)
    assert h.runtime.snapshot().usage.elapsed_s < 0.3


async def test_builtins_not_counted(make: Any) -> None:
    tools = FakeTools()
    tools.instant("t")
    core = ScriptedCore([
        Step(calls=[call("recall", {"query": "x"}), call("TaskOutput", {"task_id": "zz"})]),
        Step(calls=[call("t")]),
        Step(text="ok"),
    ])
    h: Harness = await make(core=core, tools=tools, budget=Budget(max_tool_calls=5))
    await h.start()
    await h.turn("go")
    assert h.runtime.snapshot().usage.tool_calls == 1
    assert h.runtime.snapshot().usage.budget_remaining["max_tool_calls"] == 4


async def test_tool_refused_when_exhausted(make: Any) -> None:
    tools = FakeTools()
    tools.instant("t")
    core = ScriptedCore([Step(calls=[call("t", id="t1")], usage=Usage(5000, 200))])
    # Well above the request's own estimated size, below what the answer reports.
    h: Harness = await make(core=core, tools=tools, budget=Budget(max_tokens=4000))
    await h.start()
    await h.runtime.send("go")
    await h.rec.wait_state(AgentState.FAILED)
    result = (await h.results())["t1"]
    assert result.status == ToolStatus.REFUSED and "budget" in result.content.lower()
    assert tools.started == []
    assert len(h.core.requests) == 1
    assert h.rec.of(Failure)[-1].kind == FailureKind.BUDGET


async def test_mid_stream_exhaustion(make: Any) -> None:
    core = ScriptedCore([Step(text="stream", reports=[Usage(5500, 600)], hang=True)])
    h: Harness = await make(core=core, budget=Budget(max_tokens=6000))  # above one request
    await h.start()
    await h.runtime.send("go")
    await h.rec.wait_state(AgentState.FAILED, timeout=2)
    assert core.cancelled == 1
    assert h.rec.of(Failure)[-1].kind == FailureKind.BUDGET
    usage = h.runtime.snapshot().usage.usage
    assert usage.input_tokens >= 5500 and usage.output_tokens >= 600


async def test_failed_request_charged(make: Any) -> None:
    core = ScriptedCore([Step(text="abc", reports=[Usage(100_000, 5)],
                              error=TransientCoreError("reset"))])
    h: Harness = await make(core=core, max_retries=0)
    await h.start()
    await h.runtime.send("go")
    await h.rec.wait_state(AgentState.FAILED)
    usage = h.runtime.snapshot().usage.usage
    assert usage.input_tokens == 100_000  # report beats the estimate
    assert usage.output_tokens == 5

    core.add(Step(text="x", error=TransientCoreError("reset")))  # no report: estimate
    await h.runtime.activate()
    await h.rec.wait_state(AgentState.FAILED, count=2)
    assert h.runtime.snapshot().usage.usage.input_tokens > 100_000


async def test_failure_before_first_event_not_charged(make: Any) -> None:
    core = ScriptedCore([Step(error=TransientCoreError("429")), Step(text="ok")])
    h: Harness = await make(core=core)
    await h.start()
    await h.turn("go")
    assert len(core.requests) == 2
    usage = h.runtime.snapshot().usage.usage
    assert (usage.input_tokens, usage.output_tokens) == (100, 10)  # the successful request only


async def test_cancel_before_first_event_charges_the_input_estimate(make: Any) -> None:
    core = ScriptedCore([Step(hang=True)])
    h: Harness = await make(core=core)
    await h.start()
    await h.runtime.send("go")
    await eventually(lambda: core.active == 1)
    await h.runtime.stop()
    await h.rec.wait_state(AgentState.STOPPED)
    usage = h.runtime.snapshot().usage.usage
    assert usage.input_tokens > 0 and usage.output_tokens == 0


async def test_duration_exhaustion_cancels_foreground_call(make: Any) -> None:
    tools = FakeTools()
    tools.hanging("fg")
    core = ScriptedCore([Step(calls=[call("fg", id="fg")]), Step(text="never")])
    h: Harness = await make(core=core, tools=tools, initial_wait_s=5,
                            budget=Budget(max_duration_s=0.3))
    await h.start()
    await h.runtime.send("go")
    await h.rec.wait_state(AgentState.FAILED, timeout=2)
    assert (await h.results())["fg"].status == ToolStatus.CANCELLED
    assert tools.cancelled == ["fg"]
    assert len(core.requests) == 1
    assert h.rec.of(Failure)[-1].kind == FailureKind.BUDGET


async def test_duration_exhaustion_abandons_background_call(make: Any) -> None:
    tools = FakeTools()
    tools.hanging("bg", honor_cancel=False)
    core = ScriptedCore([Step(calls=[call("bg", id="bg")]), Step(text="started")])
    h: Harness = await make(core=core, tools=tools, initial_wait_s=0.05,
                            budget=Budget(max_duration_s=0.3))
    await h.start()
    await h.turn("go")
    await h.rec.wait_state(AgentState.FAILED, timeout=2)
    finished = await h.rec.wait_for(
        lambda r: [e for e in r.of(ToolFinished) if e.result.call_id == "bg"])
    assert finished[0].result.status == ToolStatus.ABANDONED
    assert h.runtime.snapshot().operations == []
    assert h.rec.of(Failure)[-1].kind == FailureKind.BUDGET


async def test_usage_persists(make: Any) -> None:
    core = ScriptedCore(default=Step(text="ok", usage=Usage(500, 50)))
    h: Harness = await make(core=core)
    await h.start()
    await h.turn("a")
    await h.close()
    data = json.loads((h.data_dir / "usage.json").read_text())
    assert data["input_tokens"] == 500 and data["output_tokens"] == 50

    h2: Harness = await make(data_dir=h.data_dir, store=h.store)
    assert h2.runtime.snapshot().usage.usage.input_tokens == 0  # not loaded before start
    await h2.start()
    assert h2.runtime.snapshot().usage.usage.input_tokens == 500

    await h2.close()
    h3: Harness = await make(data_dir=h.data_dir)
    await h3.start()  # one budget per data folder, whatever the conversation
    assert h3.runtime.snapshot().usage.usage.input_tokens == 500


async def test_usage_is_written_off_the_loop_with_the_latest_totals(tmp_path):
    import json

    from kaden.api import Budget, Usage
    from kaden.runtime.budget import BudgetTracker

    tracker = BudgetTracker(Budget())
    tracker.load(tmp_path / "usage.json")
    for _ in range(50):
        tracker.charge(Usage(input_tokens=10), 0.01)
    assert not (tmp_path / "usage.json").exists()  # nothing written on the loop
    await tracker.flush()
    saved = json.loads((tmp_path / "usage.json").read_text())
    assert saved["input_tokens"] == 500 and saved["cost_usd"] == pytest.approx(0.5)


async def test_removing_the_duration_limit_lets_running_calls_go_on(make: Any) -> None:
    tools = FakeTools()
    tools.hanging("bg")
    core = ScriptedCore([Step(calls=[call("bg", id="bg")]), Step(text="started")])
    h: Harness = await make(core=core, tools=tools, initial_wait_s=0.05,
                            budget=Budget(max_duration_s=0.4))
    await h.start()
    await h.turn("go")
    await h.runtime.update_settings(SettingsUpdate(budget=Budget()))
    await asyncio.sleep(0.6)
    assert tools.cancelled == []
    assert [op.call.id for op in h.runtime.snapshot().operations] == ["bg"]


async def test_a_stream_the_duration_limit_cannot_end_is_reported(make: Any) -> None:
    held = asyncio.Event()
    core = ScriptedCore([Step(text="partial", hang=True, ignore_cancel=held)])
    h: Harness = await make(core=core, budget=Budget(max_duration_s=0.2))
    await h.start()
    await h.runtime.send("go")
    try:
        await h.rec.wait_state(AgentState.FAILED, timeout=3)
    finally:
        held.set()
    kinds = [f.kind for f in h.rec.of(Failure)]
    assert FailureKind.INTERNAL in kinds and kinds[-1] == FailureKind.BUDGET


@pytest.mark.parametrize("content", [
    {"schema_version": 2, "input_tokens": 5},
    {"schema_version": 1, "input_tokens": 5, "spent_on_cake": 1},
    {"input_tokens": 5},
])
async def test_usage_of_another_kind_is_refused_and_left_as_it_is(
        make: Any, tmp_path: Any, content: dict[str, Any]) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "usage.json").write_text(json.dumps(content))
    h: Harness = await make(data_dir=data)
    with pytest.raises(ValueError, match="usage.json"):
        await h.start()
    await h.close()
    assert json.loads((data / "usage.json").read_text()) == content


async def test_a_failed_usage_write_still_lets_go_of_the_data_folder(
        make: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from kaden.runtime import budget
    from kaden.runtime.instance import InstanceLock

    h: Harness = await make()
    await h.start()

    def fail(path: Any, data: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(budget, "_write_json", fail)
    await h.close()
    InstanceLock.acquire(h.data_dir).release()  # free again
