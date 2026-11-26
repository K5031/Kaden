"""MCP servers in the settings: saved like module settings, their variables in the keychain,
built at the next start as a second Tools module."""

from __future__ import annotations

import tomllib
from typing import Any

import pytest

import kaden.__main__ as launcher
from kaden.api import McpServer, SettingsUpdate
from kaden.runtime import ModuleState
from tests.conftest import Harness

RUNNING = {"core": "first", "memory": "fake-memory", "tools": "fake-tools",
           "frontend": "scripted"}
GITHUB = McpServer("github", command="npx -y @modelcontextprotocol/server-github",
                   env={"GITHUB_TOKEN": "ghp-secret"})
DOCS = McpServer("docs", url="https://docs.example.com/mcp")


@pytest.fixture(autouse=True)
def _modules(installed):
    """The fake modules are installed for every test here."""


async def setup(make: Any, servers: tuple[McpServer, ...] = ()) -> Harness:
    state = ModuleState(running=RUNNING, work_folder="/start", mcp_servers=servers)
    return await make(modules=state)


def saved(h: Harness) -> dict[str, Any]:
    return tomllib.loads((h.data_dir / "settings.toml").read_text())


async def test_servers_are_saved_with_their_variables_in_the_keychain(make, keychain):
    h = await setup(make)
    await h.start()  # opens the event log
    s = await h.runtime.update_settings(SettingsUpdate(mcp_servers=[GITHUB, DOCS]))
    assert saved(h)["mcp_servers"] == [
        {"name": "github", "command": GITHUB.command, "env": ["GITHUB_TOKEN"]},
        {"name": "docs", "url": DOCS.url}]
    assert keychain.store[("kaden", "mcp/github/GITHUB_TOKEN")] == "ghp-secret"
    assert "ghp-secret" not in (h.data_dir / "settings.toml").read_text()
    assert "ghp-secret" not in (h.data_dir / "events.jsonl").read_text()
    assert [srv.name for srv in s.mcp_servers] == ["github", "docs"]
    assert s.mcp_servers[0].env == {"GITHUB_TOKEN": ""}  # values are never returned
    assert s.restart_needed  # servers start with the next start


async def test_an_empty_value_keeps_the_saved_one_and_removal_erases_it(make, keychain):
    h = await setup(make)
    await h.runtime.update_settings(SettingsUpdate(mcp_servers=[GITHUB]))
    kept = McpServer("github", command=GITHUB.command, env={"GITHUB_TOKEN": ""})
    await h.runtime.update_settings(SettingsUpdate(mcp_servers=[kept]))
    assert keychain.store[("kaden", "mcp/github/GITHUB_TOKEN")] == "ghp-secret"
    await h.runtime.update_settings(SettingsUpdate(mcp_servers=[]))
    assert ("kaden", "mcp/github/GITHUB_TOKEN") not in keychain.store
    assert saved(h)["mcp_servers"] == []


async def test_unchanged_servers_need_no_restart(make):
    h = await setup(make, servers=(McpServer("docs", url=DOCS.url),))
    s = await h.runtime.update_settings(SettingsUpdate(mcp_servers=[DOCS]))
    assert not s.restart_needed


@pytest.mark.parametrize("servers, message", [
    ([DOCS, DOCS], "twice"),
    ([McpServer("both", command="x", url="https://x")], "a command or a URL"),
    ([McpServer("neither")], "a command or a URL"),
    ([McpServer("bad name!", url="https://x")], "name"),
    ([McpServer("files", url="file:///etc")], "http"),
    ([McpServer("quote", command="say 'unclosed")], "command"),
    ([McpServer("env", url="https://x", env={"BAD-NAME": "v"})], "variable"),
    ([McpServer("env", url="https://x", env={"TOKEN": ""})], "value"),
])
async def test_invalid_servers_change_nothing(make, servers, message, keychain):
    h = await setup(make)
    with pytest.raises(ValueError, match=message):
        await h.runtime.update_settings(SettingsUpdate(mcp_servers=servers))
    assert not (h.data_dir / "settings.toml").exists() and not keychain.store


def test_saved_servers_become_an_mcp_tools_module(tmp_path, keychain):
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.toml").write_text(
        '[[mcp_servers]]\nname = "github"\ncommand = "npx server-github"\n'
        'env = ["GITHUB_TOKEN"]\n\n[[mcp_servers]]\nname = "docs"\nurl = "https://d/mcp"\n')
    keychain.store[("kaden", "mcp/github/GITHUB_TOKEN")] = "ghp-secret"
    config = launcher.load_config(launcher.parse_args(["--data-dir", str(data)]))
    [mcp] = config.extra_tools
    assert mcp.factory == "kaden.tools.mcp:factory"
    assert mcp.options["servers"] == [
        {"name": "github", "command": "npx server-github", "env": {"GITHUB_TOKEN": "ghp-secret"}},
        {"name": "docs", "url": "https://d/mcp", "env": {}}]
    assert [s.env for s in config.runtime.modules.mcp_servers] == [{"GITHUB_TOKEN": ""}, {}]
    assert "ghp-secret" not in launcher.show_config(config, data / "config.toml")


def test_no_servers_no_module(tmp_path):
    config = launcher.load_config(launcher.parse_args(["--data-dir", str(tmp_path)]))
    assert config.extra_tools == ()


async def test_without_a_keychain_a_server_with_a_value_is_not_saved(make, keychain,
                                                                     monkeypatch):
    import keyring.errors

    def refuse(service: str, username: str, password: str) -> None:
        raise keyring.errors.KeyringError("locked")

    h = await setup(make)
    monkeypatch.setattr(keychain, "set_password", refuse)
    with pytest.raises(ValueError, match="keychain"):
        await h.runtime.update_settings(SettingsUpdate(mcp_servers=[GITHUB]))
    assert not (h.data_dir / "settings.toml").exists()
    assert (await h.runtime.settings()).mcp_servers == ()


async def test_a_failed_save_puts_the_servers_values_back(make, keychain, monkeypatch):
    h = await setup(make)
    await h.runtime.update_settings(SettingsUpdate(mcp_servers=[GITHUB]))
    monkeypatch.setattr("kaden.runtime.modules.save_settings",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    changed = McpServer("github", command=GITHUB.command, env={"GITHUB_TOKEN": "ghp-new"})
    with pytest.raises(OSError):
        await h.runtime.update_settings(SettingsUpdate(mcp_servers=[changed, DOCS]))
    assert keychain.store[("kaden", "mcp/github/GITHUB_TOKEN")] == "ghp-secret"


async def test_setup_runtime_saves_concurrent_changes_one_at_a_time(tmp_path):
    import asyncio

    from kaden.api import PermissionPolicy
    from kaden.runtime import RuntimeConfig
    from kaden.runtime.setup import SetupRuntime

    runtime = SetupRuntime(RuntimeConfig(data_dir=tmp_path), "terminal", "no key")
    await asyncio.gather(*[runtime.update_settings(SettingsUpdate(system_prompt=f"p{i}"))
                           for i in range(10)],
                         runtime.update_settings(SettingsUpdate(
                             permissions=PermissionPolicy.ASK)))
    data = tomllib.loads((tmp_path / "settings.toml").read_text())
    assert data["permissions"] == "ask" and data["system_prompt"] == "p9"
