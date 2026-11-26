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
        "core": {"factory": "tests.fakes.core:factory", "options": {"script": script}}}))
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
        assert "You are Ahri" not in settings["system_prompt"]
        assert "accomplish the user's goals" not in settings["system_prompt"]
        assert "TaskOutput" in settings["system_prompt"]  # the facts of its tools stay
        assert Path(settings["work_folder"]) == folder / "home"  # a folder of its own
        assert not list(data.glob("*.db"))  # nothing of it in the user's own memory
    finally:
        await life.aclose()


async def test_what_it_says_by_itself_reaches_the_user_and_a_reply_does_not(
        tmp_path, child_env):
    data = home(tmp_path, [{"text": ""}, {"text": "I found an old map of Kyoto!"},
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
        await eventually(lambda: again.snapshot.duty_wake_at is not None)
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


async def test_it_keeps_a_journal_the_user_can_read(tmp_path, child_env):
    """Its prompt says the user reads journal.md in its folder; the app reads it there."""
    data = home(tmp_path, [{"text": ""}])
    life = Life(data, pace_s=(0.05, 30))
    try:
        await life.start()
        await eventually(lambda: life.snapshot)
        settings = tomllib.loads((data / "life" / "settings.toml").read_text())
        assert "journal.md" in settings["system_prompt"]
        assert await life.read_journal() == ""  # nothing written yet
        (data / "life" / "home" / "journal.md").write_text("Found an old map today.")
        assert await life.read_journal() == "Found an old map today."
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
