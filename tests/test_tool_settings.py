"""Tool settings: a Tools module declares settings (its factory's user_config); the user's
values are saved per Kaden, checked against the declaration, and reach the factory at the
next start, only those it declares."""

from __future__ import annotations

import json
import tomllib
from typing import Any

import pytest
import tomli_w

import kaden.__main__ as launcher
from kaden.api import Settings, SettingsUpdate, ToolSetting
from kaden.frontends.wire import from_json, to_json
from kaden.runtime import ModuleState
from tests.conftest import Harness
from tests.fakes.tools import configurable

DECLARED = tuple(configurable.user_config)  # type: ignore[attr-defined]


async def setup(make: Any, **values: Any) -> Harness:
    state = ModuleState(tool_settings=DECLARED, tool_values=values)
    return await make(modules=state)


def saved(h: Harness) -> dict[str, Any]:
    return tomllib.loads((h.data_dir / "settings.toml").read_text())


async def test_settings_show_what_the_tools_declare_with_their_values(make):
    h = await setup(make, wake="anyone")
    s = await h.runtime.settings()
    assert [t.name for t in s.tool_settings] == ["wake", "greet", "signature"]
    assert s.tool_settings[0].value == "anyone"
    assert s.tool_settings[1].value is False  # the default
    assert not s.restart_needed


async def test_a_change_is_saved_at_once_and_applies_at_the_next_start(make):
    h = await setup(make)
    s = await h.runtime.update_settings(SettingsUpdate(tool_settings={"wake": "nobody"}))
    assert saved(h)["tool_settings"] == {"wake": "nobody"}
    assert s.tool_settings[0].value == "nobody" and s.restart_needed
    await h.runtime.update_settings(SettingsUpdate(tool_settings={"greet": True}))
    assert saved(h)["tool_settings"] == {"wake": "nobody", "greet": True}  # merged
    s = await h.runtime.update_settings(SettingsUpdate(tool_settings={"wake": None,
                                                                      "greet": None}))
    assert saved(h)["tool_settings"] == {}  # back to the defaults
    assert s.tool_settings[0].value == "follows" and not s.restart_needed


@pytest.mark.parametrize("values, message", [
    ({"volume": 3}, "volume"),
    ({"wake": "everyone"}, "everyone"),
    ({"greet": "yes"}, "greet"),
    ({"signature": True}, "signature"),
])
async def test_a_value_the_tools_do_not_take_changes_nothing(make, values, message):
    h = await setup(make)
    with pytest.raises(ValueError, match=message):
        await h.runtime.update_settings(SettingsUpdate(tool_settings=values))
    assert not (h.data_dir / "settings.toml").exists()


def configured(tmp_path: Any, settings: dict[str, Any]) -> Any:
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.toml").write_text(tomli_w.dumps({"tools": {
        "factory": "tests.fakes.tools:configurable", "options": {"signature": "-- K"}}}))
    (data / "settings.toml").write_text(tomli_w.dumps({"tool_settings": settings}))
    return launcher.load_config(launcher.parse_args(["--data-dir", str(data)]))


def test_the_factory_gets_only_the_values_it_declares(tmp_path):
    config = configured(tmp_path, {"wake": "anyone", "greet": "yes", "left_over": 1})
    assert config.tools.options == {"signature": "-- K", "wake": "anyone"}
    state = config.runtime.modules
    assert [t.name for t in state.tool_settings] == ["wake", "greet", "signature"]
    assert state.tool_values == {"wake": "anyone"}


def test_a_saved_value_replaces_the_one_in_config_toml(tmp_path):
    config = configured(tmp_path, {"signature": "-- Ahri"})
    assert config.tools.options["signature"] == "-- Ahri"


def test_tools_that_declare_nothing_get_nothing(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.toml").write_text(tomli_w.dumps({"tools": {
        "factory": "tests.fakes.tools:factory"}}))
    (data / "settings.toml").write_text(tomli_w.dumps({"tool_settings": {"wake": "x"}}))
    config = launcher.load_config(launcher.parse_args(["--data-dir", str(data)]))
    assert config.tools.options == {} and config.runtime.modules.tool_settings == ()


def test_they_cross_the_headless_channel_as_they_are():
    update = SettingsUpdate(tool_settings={"wake": "anyone", "greet": True})
    assert from_json(json.loads(json.dumps(to_json(update)))) == update
    setting = ToolSetting("wake", "Wake", "choice", "follows",
                          choices={"follows": "Follows", "anyone": "Anyone"}, value="anyone")
    back = from_json(json.loads(json.dumps(to_json(setting))), ToolSetting)
    assert back == setting
    assert Settings.__dataclass_fields__["tool_settings"]


def test_a_tools_module_names_its_own_work_folder_option(tmp_path, monkeypatch):
    """The launcher gives the work folder to the option the factory declares, not to the
    built-in module's `root` by name."""
    data = tmp_path / "data"
    data.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(tmp_path)
    (data / "config.toml").write_text(tomli_w.dumps({"tools": {
        "factory": "tests.fakes.tools:in_a_folder"}}))
    config = launcher.load_config(launcher.parse_args(["--data-dir", str(data)]))
    assert config.tools.options["folder"] == str(tmp_path)  # where Kaden was started
    assert config.runtime.modules.work_folder == str(tmp_path)
    (data / "settings.toml").write_text(tomli_w.dumps({"work_folder": str(work)}))
    config = launcher.load_config(launcher.parse_args(["--data-dir", str(data)]))
    assert config.tools.options == {"folder": str(work)}
