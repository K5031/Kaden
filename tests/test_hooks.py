"""Hooks: the user's own commands, run before or after a tool call (config.toml)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from kadence.api import PermissionPolicy, ToolStatus
from kadence.runtime.hooks import Hook
from tests.conftest import Harness
from tests.fakes.core import ScriptedCore, Step, call
from tests.fakes.tools import FakeTools


def script(tmp_path: Path, name: str, body: str) -> str:
    """A hook command: a Python script given the call as JSON on stdin."""
    path = tmp_path / f"{name}.py"
    path.write_text("import json, sys\ncall = json.load(sys.stdin)\n" + body)
    return f"{sys.executable} {path}"


def tools() -> FakeTools:
    made = FakeTools()
    made.instant("Bash", "ran", read_only=False)
    made.instant("read", "the text")
    made.confined_names = {"Bash", "read"}  # in the work folder: nothing asks
    return made


async def test_a_hook_before_a_call_can_refuse_it_with_its_reason(make: Any, tmp_path):
    seen = tmp_path / "seen.json"
    hook = Hook("before", script(tmp_path, "guard", (
        f"open({str(seen)!r}, 'a').write(json.dumps(call) + '\\n')\n"
        "if 'rm ' in call['arguments'].get('command', ''):\n"
        "    print('no deleting in this project', file=sys.stderr)\n"
        "    sys.exit(2)\n")), tool="Bash")
    core = ScriptedCore([Step(calls=[call("Bash", {"command": "rm -rf build"}, id="a"),
                                     call("Bash", {"command": "ls"}, id="b"),
                                     call("read", id="c")]), Step(text="ok")])
    h: Harness = await make(core=core, tools=tools(), hooks=(hook,), permissions=PermissionPolicy.ALLOW_WORKSPACE)
    await h.start()
    await h.turn("tidy")
    results = await h.results()
    assert results["a"].status == ToolStatus.REFUSED
    assert "no deleting in this project" in results["a"].content
    assert results["b"].status == ToolStatus.OK and results["c"].status == ToolStatus.OK
    calls = [json.loads(line) for line in seen.read_text().splitlines()]
    assert [c["arguments"] for c in calls] == [{"command": "rm -rf build"}, {"command": "ls"}]
    assert calls[0]["tool"] == "Bash" and calls[0]["when"] == "before"  # only its tool


async def test_a_hook_after_a_call_sees_its_result_and_can_add_to_it(make: Any, tmp_path):
    hook = Hook("after", script(tmp_path, "lint", (
        "assert call['result']['status'] == 'ok'\n"
        "assert call['result']['content'].startswith('ran')\n"
        "print('2 style problems in main.py', file=sys.stderr)\n"
        "sys.exit(2)\n")))
    core = ScriptedCore([Step(calls=[call("Bash", {"command": "make"}, id="a")]),
                         Step(text="ok")])
    h: Harness = await make(core=core, tools=tools(), hooks=(hook,), permissions=PermissionPolicy.ALLOW_WORKSPACE)
    await h.start()
    await h.turn("build")
    result = (await h.results())["a"]
    assert result.status == ToolStatus.OK
    assert result.content.startswith("ran")
    assert result.content.endswith("\n[hook] 2 style problems in main.py")


async def test_a_hook_that_fails_or_hangs_does_not_stop_the_work(make: Any, tmp_path,
                                                                 monkeypatch):
    monkeypatch.setattr("kadence.runtime.hooks.TIMEOUT_S", 0.5)
    broken = Hook("before", script(tmp_path, "broken", "sys.exit(1)\n"))
    slow = Hook("before", script(tmp_path, "slow", "import time\ntime.sleep(30)\n"))
    core = ScriptedCore([Step(calls=[call("Bash", {"command": "ls"}, id="a")]),
                         Step(text="ok")])
    h: Harness = await make(core=core, tools=tools(), hooks=(broken, slow), permissions=PermissionPolicy.ALLOW_WORKSPACE)
    await h.start()
    await h.turn("look")
    assert (await h.results())["a"].status == ToolStatus.OK
    failed = [line for line in (h.data_dir / "events.jsonl").read_text().splitlines()
              if '"hook_failed"' in line]
    assert len(failed) == 2


async def test_the_runtimes_own_tools_run_no_hooks(make: Any, tmp_path):
    seen = tmp_path / "seen"
    hook = Hook("before", script(tmp_path, "any", f"open({str(seen)!r}, 'a').write('x')\n"))
    core = ScriptedCore([Step(calls=[call("update_plan", {"items": []}, id="p")]),
                         Step(text="ok")])
    h: Harness = await make(core=core, tools=tools(), hooks=(hook,), permissions=PermissionPolicy.ALLOW_WORKSPACE)
    await h.start()
    await h.turn("plan")
    assert not seen.exists()


@pytest.mark.parametrize("text", [
    '[[hooks]]\nwhen = "during"\ncommand = "x"\n',
    '[[hooks]]\nwhen = "before"\n',
    '[[hooks]]\nwhen = "before"\ncommand = "x"\nshell = true\n'])
def test_a_hook_that_is_not_one_is_named(tmp_path, text):
    from kadence.__main__ import Launch, load_config

    (tmp_path / "config.toml").write_text(text)
    with pytest.raises(SystemExit, match="hooks"):
        load_config(Launch(tmp_path))


def test_hooks_are_read_from_the_configuration(tmp_path):
    from kadence.__main__ import Launch, load_config

    (tmp_path / "config.toml").write_text(
        '[[hooks]]\nwhen = "after"\ntool = "Edit"\ncommand = "ruff format ."\n')
    assert load_config(Launch(tmp_path)).runtime.hooks == (
        Hook("after", "ruff format .", tool="Edit"),)
