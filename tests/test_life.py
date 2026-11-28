"""The life: one Kaden that lives its own life in its own process and data folder,
hosted by the app, apart from the user's conversations."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import tomli_w

from kaden.api import DutyKind
from kaden.frontends.life import Life
from tests.conftest import eventually


def home(tmp_path: Path, script: list[dict[str, Any]]) -> Path:
    """A data folder whose model, for the life too, follows `script`, and whose
    user has instructions of their own for their chats."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.toml").write_text('system_prompt = "You are Ahri, a foxgirl."\n')
    (data / "config.toml").write_text(tomli_w.dumps({
        "core": {"factory": "tests.fakes.core:factory", "options": {"script": script}},
        "runtime": {"system_prompt": "Always answer in French."}}))
    return data


async def test_it_lives_in_its_own_folder_on_a_life_of_its_own(tmp_path, child_env):
    data = home(tmp_path, [{"text": ""}])
    with (data / "settings.toml").open("a") as settings:
        settings.write('maintenance_model = "fake-2"\n')
    life = Life(data, pace_s=(0.05, 30), daily_usd=0.5)
    try:
        await life.start()
        snapshot = await eventually(lambda: life.snapshot)
        duty = snapshot.conversation.duty
        assert duty is not None and duty.kind == DutyKind.LIFE
        assert duty.instruction == life.who
        assert duty.model == "fake-2"  # its own time on the user's cheaper model
        folder = data / "life"
        assert (folder / "life.json").exists()
        settings = tomllib.loads((folder / "settings.toml").read_text())
        assert settings["budget"] == {"max_cost_usd_per_day": 0.5}
        # Not the work chats' prompt or the user's instructions for them: who it is comes
        # from its own life, and an assistant's framing would contradict it.
        # The user's instructions, in settings.toml or config.toml, are their chats'.
        assert settings["system_prompt"] == ""
        assert "## Doing the work" not in settings["base_prompt"]  # not an assistant's
        assert "TaskOutput" in settings["base_prompt"]  # the facts of its tools stay
        assert Path(settings["work_folder"]) == folder / "home"  # a folder of its own
        assert settings["compact_window"] == 150_000  # compacted before a day costs much
        assert not list(data.glob("*.db"))  # nothing of it in the user's own memory
    finally:
        await life.aclose()


async def test_what_it_says_by_itself_reaches_the_user_and_a_reply_does_not(
        tmp_path, child_env):
    data = home(tmp_path, [{"text": "I found an old map of Kyoto!"},
                           {"text": "Hi! Good to see you."}])
    life = Life(data, pace_s=(0.05, 0.3))
    heard: list[str] = []
    life.listeners.append(lambda: heard.extend(life.take_messages()))
    try:
        await life.start()
        await eventually(lambda: heard == ["I found an old map of Kyoto!"], timeout=10)
        await life.runtime.send("hello")
        await eventually(lambda: any("Good to see you" in text
                                     for text in life.replies), timeout=10)
        assert heard == ["I found an old map of Kyoto!"]  # a reply is not a message
    finally:
        await life.aclose()


async def test_it_carries_on_its_life_after_the_app_restarts(tmp_path, child_env):
    data = home(tmp_path, [{"text": ""}, {"text": ""}, {"text": ""}])
    life = Life(data, pace_s=(0.05, 30))
    await life.start()
    first = (await eventually(lambda: life.snapshot)).conversation.id
    await life.aclose()
    again = Life(data, pace_s=(0.05, 20))
    try:
        await again.start()
        snapshot = await eventually(lambda: again.snapshot)
        assert snapshot.conversation.id == first  # the same life, not a new one
        # with its pace (and model and who it is) as the app has them now
        await eventually(lambda: again.snapshot.conversation.duty.max_interval_s == 20)
    finally:
        await again.aclose()


async def test_the_user_names_it_and_says_who_it_is(tmp_path, child_env):
    data = home(tmp_path, [{"text": ""}, {"text": ""}])
    life = Life(data, pace_s=(0.05, 30))
    assert life.name == "Kaden" and not life.has_lived
    try:
        await life.set_identity("Mira", "Curious, loves old maps.")
        await life.start()
        duty = (await eventually(lambda: life.snapshot)).conversation.duty
        assert duty.instruction.startswith("You are Mira.") and "old maps" in duty.instruction
        await life.set_identity("Mira", "")  # changed while it lives
        await eventually(lambda: "old maps" not in life.snapshot.conversation.duty.instruction)
        kept = Life(data)
        assert (kept.name, kept.about) == ("Mira", "")
    finally:
        await life.aclose()


async def test_starting_it_twice_at_once_starts_one_life(tmp_path, child_env):
    """Reopening into its view starts it as the app does: one process, not two."""
    import asyncio

    import psutil

    data = home(tmp_path, [{"text": ""}, {"text": ""}])
    life = Life(data, pace_s=(0.05, 30))
    try:
        await asyncio.gather(life.start(), life.start())
        children = [p for p in psutil.Process().children(recursive=True)
                    if "--headless" in p.cmdline()]
        assert len(children) == 1
    finally:
        await life.aclose()


async def test_the_user_can_see_its_folder_and_no_file_is_named_for_it(tmp_path, child_env):
    """Its prompt says the user can see the files in its folder, and names none: what it
    keeps there, a journal or anything else, is its own choice."""
    data = home(tmp_path, [{"text": ""}])
    life = Life(data, pace_s=(0.05, 30))
    try:
        await life.start()
        await eventually(lambda: life.snapshot)
        settings = tomllib.loads((data / "life" / "settings.toml").read_text())
        assert "The user can see the files in your folder" in settings["base_prompt"]
        assert "journal" not in settings["base_prompt"].lower()
        assert life.home == data / "life" / "home" == Path(settings["work_folder"])
    finally:
        await life.aclose()


def test_its_clock_is_its_own_with_a_day_as_the_net():
    from kaden.frontends.life import PACE_S

    assert PACE_S == (60.0, 86_400.0)  # it chooses; at the latest a day


async def test_its_tool_settings_are_its_own_and_last(tmp_path, child_env):
    """Saved in its folder, they apply when it restarts; the chats' never reach it."""
    from kaden.api import SettingsUpdate

    data = home(tmp_path, [{"text": ""}, {"text": ""}, {"text": ""}])
    with (data / "settings.toml").open("a") as settings:
        settings.write('[tool_settings]\nbilibili_wake = "anyone"\n')  # the chats'
    life = Life(data, pace_s=(0.05, 30))
    try:
        await life.start()
        await eventually(lambda: life.snapshot)
        values = {t.name: t.value for t in (await life.runtime.settings()).tool_settings}
        assert values["bilibili_wake"] == "follows"
        await life.runtime.update_settings(
            SettingsUpdate(tool_settings={"bilibili_wake": "nobody"}))
        await life.restart()
        await eventually(lambda: life.snapshot)
        s = await life.runtime.settings()
        assert {t.name: t.value for t in s.tool_settings}["bilibili_wake"] == "nobody"
        own = tomllib.loads((data / "life" / "settings.toml").read_text())
        assert own["tool_settings"] == {"bilibili_wake": "nobody"}  # kept by the app
    finally:
        await life.aclose()


async def test_the_user_sets_its_daily_amount(tmp_path, child_env):
    """At once while it lives, kept for its next start; None for no limit."""
    data = home(tmp_path, [{"text": ""}, {"text": ""}, {"text": ""}])
    life = Life(data, pace_s=(0.05, 30))
    assert life.daily_usd == 1.0
    try:
        await life.start()
        await eventually(lambda: life.snapshot)
        await life.set_daily_usd(3.0)
        assert (await life.runtime.settings()).budget.max_cost_usd_per_day == 3.0
        assert Life(data).daily_usd == 3.0
        await life.restart()
        settings = tomllib.loads((data / "life" / "settings.toml").read_text())
        assert settings["budget"] == {"max_cost_usd_per_day": 3.0}
        await life.set_daily_usd(None)
        assert (await life.runtime.settings()).budget.max_cost_usd_per_day is None
        assert Life(data).daily_usd is None
    finally:
        await life.aclose()


async def test_what_the_user_always_allows_it_is_its_own_and_lasts(tmp_path, child_env):
    """Saved answers given in its view last across its restarts; the chats' never reach
    it."""
    from kaden.api import PermissionRule, SettingsUpdate

    data = home(tmp_path, [{"text": ""}, {"text": ""}, {"text": ""}])
    with (data / "settings.toml").open("a") as settings:
        settings.write('always_allow = ["Bash: git status"]\n')  # the chats'
    life = Life(data, pace_s=(0.05, 30))
    try:
        await life.start()
        await eventually(lambda: life.snapshot)
        assert (await life.runtime.settings()).permission_rules == ()
        await life.runtime.update_settings(SettingsUpdate(
            permission_rules=[PermissionRule("BiliMessage")]))
        await life.restart()
        await eventually(lambda: life.snapshot)
        rules = (await life.runtime.settings()).permission_rules
        assert rules == (PermissionRule("BiliMessage"),)
    finally:
        await life.aclose()


def test_what_it_asks_the_user_reaches_the_app(tmp_path):
    """A question of its own (a permission, say) is heard, so the app can tell the user
    it waits on them."""
    from kaden.api import EventEnvelope, Prompt, PromptOpened

    life = Life(tmp_path)
    heard: list[Prompt] = []
    life.listeners.append(lambda: heard.extend(life.take_questions()))
    prompt = Prompt("p1", "permission", "May I send a message?")
    life._event(EventEnvelope(1, PromptOpened(prompt)))
    assert heard == [prompt]


async def test_it_lives_without_a_ceiling_on_what_the_user_allows_it(
        tmp_path, child_env, monkeypatch):
    """The ceiling is for a Kaden an agent starts, whose saved answers an agent could have
    written. The life is the user's: the app starts it, and its answers are the user's."""
    import psutil

    from kaden.api import PERMISSION_CEILING_ENV

    monkeypatch.setenv(PERMISSION_CEILING_ENV, "auto")  # as the app's own runtime sets it
    data = home(tmp_path, [{"text": ""}])
    life = Life(data, pace_s=(0.05, 30))
    try:
        await life.start()
        await eventually(lambda: life.snapshot)
        child = psutil.Process(life.runtime.channel.process.pid)
        assert PERMISSION_CEILING_ENV not in child.environ()
    finally:
        await life.aclose()


async def test_a_start_that_fails_leaves_it_stopped_and_it_can_start_again(tmp_path, child_env):
    import sys

    import pytest

    data = home(tmp_path, [{"text": ""}])
    life = Life(data, pace_s=(0.05, 30), command=(sys.executable, "-c", "pass"))  # quits
    with pytest.raises(Exception):  # noqa: B017 - whatever the channel says
        await life.start()
    assert not life.running
    life.command = (sys.executable, "-m", "kaden")
    try:
        await life.start()  # tried again, not the first failure again
        assert life.running and await eventually(lambda: life.snapshot)
    finally:
        await life.aclose()


async def test_it_is_not_running_once_its_process_ends(tmp_path, child_env):
    data = home(tmp_path, [{"text": ""}])
    life = Life(data, pace_s=(0.05, 30))
    changes: list[bool] = []
    life.listeners.append(lambda: changes.append(life.running))
    try:
        await life.start()
        life.runtime.channel.process.kill()
        await eventually(lambda: not life.running, timeout=10)
        assert changes[-1] is False  # the app was told
    finally:
        await life.aclose()


async def test_restarting_it_while_it_starts_starts_it_once(tmp_path, child_env):
    import asyncio
    import sys

    import psutil
    import pytest

    from kaden.api import RuntimeStateError

    data = home(tmp_path, [{"text": ""}, {"text": ""}])
    silent = (sys.executable, "-c", "import sys; sys.stdin.read()")  # never answers
    life = Life(data, pace_s=(0.05, 30), command=silent)
    first = asyncio.create_task(life.start())
    await eventually(lambda: any("--headless" in p.cmdline()
                                 for p in psutil.Process().children(recursive=True)),
                     timeout=10)
    life.command = (sys.executable, "-m", "kaden")
    try:
        await life.restart()  # no CancelledError from the start it closed
        assert life.running
        with pytest.raises(RuntimeStateError):  # that start was closed, and says so
            await first
        children = [p for p in psutil.Process().children(recursive=True)
                    if "--headless" in p.cmdline()]
        assert len(children) == 1  # the silent one was closed
    finally:
        await life.aclose()


async def test_its_settings_are_written_whole(tmp_path, child_env, monkeypatch):
    """The settings file is replaced in one step, never seen half written."""
    import os

    data = home(tmp_path, [{"text": ""}])
    life = Life(data, pace_s=(0.05, 30))
    replaced: list[str] = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (replaced.append(str(b)), real(a, b)))
    try:
        await life.start()
        assert str(data / "life" / "settings.toml") in replaced
    finally:
        await life.aclose()
