"""The shell's sandbox: each system's backend, the one `find` picks, and the boundary it
keeps, probed from inside with whichever this system has."""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from kaden.tools.shell_files import sandbox
from kaden.tools.shell_files.sandbox import Bubblewrap, Seatbelt

HOME = Path("/Users/u")
ROOT = HOME / "project"
TMP = Path("/private/var/folders/ab/xyz/T/kaden-sandbox-1")
SENT = HOME / "Pictures" / "fox.png"


def profile(**kwargs: Any) -> str:
    args = {"root": ROOT, "tmp": TMP, "granted": (SENT,), **kwargs}
    command = Seatbelt("/usr/bin/sandbox-exec", HOME).wrap(["sh", "-c", "true"], **args)
    assert command[:2] == ["/usr/bin/sandbox-exec", "-p"] and command[3:] == ["sh", "-c", "true"]
    return command[2]


def rules(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip().startswith("(")]


def at(lines: list[str], start: str) -> int:
    [index] = [n for n, line in enumerate(lines) if line.startswith(start)]
    return index


# ── macOS: the Seatbelt profile, row by row of the table ──

def test_seatbelt_denies_what_it_does_not_allow():
    lines = rules(profile())
    assert lines[:2] == ["(version 1)", "(deny default)"]


def test_seatbelt_the_system_is_readable_and_the_homes_and_tmp_are_not():
    lines = rules(profile())
    everything = lines.index("(allow file-read*)")
    hidden = at(lines, "(deny file-read* file-write*")
    assert everything < hidden
    for path in ("/Users", "/private/var/root", str(HOME), "/private/tmp",
                 "/private/var/folders"):
        assert f'(subpath "{path}")' in lines[hidden]


def test_seatbelt_the_work_folder_and_its_tmp_are_read_and_written_after_the_hiding():
    lines = rules(profile())
    hidden = at(lines, "(deny file-read* file-write*")
    work = at(lines, "(allow file-read* file-write*")
    assert work > hidden  # the later rule wins
    assert lines[work] == (f'(allow file-read* file-write* (subpath "{ROOT}") '
                           f'(subpath "{TMP}"))')


def test_seatbelt_writes_go_only_to_the_work_folder_tmp_and_devices():
    for line in rules(profile()):
        if "file-write" in line and line.startswith("(allow"):
            assert (str(ROOT) in line and str(TMP) in line) or "/dev/" in line, line
            assert str(SENT) not in line


def test_seatbelt_paths_resolve_through_the_hidden_folders():
    lines = rules(profile())
    metadata = lines[at(lines, "(allow file-read-metadata")]
    for path in ("/", "/Users", str(HOME), "/private/var/folders/ab/xyz/T",
                 str(SENT.parent)):
        assert f'(literal "{path}")' in metadata
    assert at(lines, "(allow file-read-metadata") > at(lines, "(deny file-read* file-write*")


def test_seatbelt_a_file_the_user_attached_is_readable_only():
    lines = rules(profile())
    granted = lines[at(lines, '(allow file-read* (literal')]
    assert granted == f'(allow file-read* (literal "{SENT}"))'
    assert granted.startswith("(allow file-read* (") and "write" not in granted
    assert "(allow file-read* (literal" not in profile(granted=())


def test_seatbelt_the_login_sessions_services_and_keychain_are_out_of_reach():
    lines = rules(profile())
    [services] = [line for line in lines if "mach-lookup" in line]
    assert services.startswith("(allow mach-lookup (global-name ")
    assert '"com.apple.system.opendirectoryd.libinfo"' in services
    for kept_out in ("SecurityServer", "securityd", "pasteboard", "launchservicesd",
                     "appleevents", "windowserver", "cfprefsd.agent"):
        assert kept_out not in services


def test_seatbelt_the_network_is_as_outside_and_local_sockets_are_not():
    lines = rules(profile())
    for rule in ('(allow network-outbound (remote ip "*:*"))',
                 '(allow network-inbound (local ip "*:*"))',
                 '(allow network-bind (local ip "*:*"))'):
        assert rule in lines
    unix = [line for line in lines if "unix-socket" in line]
    assert unix == [('(allow network-outbound (remote unix-socket '
                     '(path-literal "/private/var/run/mDNSResponder")))')]


def test_seatbelt_processes_reach_only_their_own():
    lines = rules(profile())
    assert "(allow signal (target same-sandbox))" in lines
    assert "(allow process-info* (target same-sandbox))" in lines


def test_seatbelt_quotes_what_a_path_holds():
    text = profile(root=Path('/Users/u/a "quoted" \\ folder'))
    assert r'(subpath "/Users/u/a \"quoted\" \\ folder")' in text


# ── Linux: bubblewrap ──

def unix_socket(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with socket.socket(socket.AF_UNIX) as s:
        s.bind(str(path))


def pairs(command: list[str], option: str) -> list[tuple[str, str]]:
    return [(command[n + 1], command[n + 2]) for n, arg in enumerate(command)
            if arg == option]


def test_bubblewrap_covers_every_local_socket_under_run(tmp_path):
    run = tmp_path / "run"
    for name in ("docker.sock", "dbus/system_bus_socket", "systemd/journal/socket",
                 "user/1000/bus"):
        unix_socket(run / name)
    (run / "locked").mkdir(mode=0)
    home = tmp_path / "home"
    try:
        command = Bubblewrap("bwrap", home, run=run).wrap(
            ["sh", "-c", "true"], root=home / "w", tmp=tmp_path / "t", granted=())
    finally:
        (run / "locked").chmod(0o755)
    covered = {target for source, target in pairs(command, "--ro-bind") if source == "/dev/null"}
    assert covered == {str(run / "docker.sock"), str(run / "dbus/system_bus_socket"),
                       str(run / "systemd/journal/socket")}  # /run/user is hidden whole


def test_bubblewrap_binds_the_work_folder_tmp_and_what_was_attached(tmp_path):
    home, root, tmp = tmp_path / "home", tmp_path / "home" / "w", tmp_path / "t"
    sent, inside = tmp_path / "fox.png", root / "notes.txt"
    command = Bubblewrap("/usr/bin/bwrap", home, run=tmp_path / "no-run").wrap(
        ["sh", "-c", "true"], root=root, tmp=tmp, granted=(sent, inside))
    assert command[:3] == ["/usr/bin/bwrap", "--ro-bind", "/"]
    assert command[-3:] == ["sh", "-c", "true"]
    assert (str(root), str(root)) in pairs(command, "--bind")
    assert (str(tmp), str(tmp)) in pairs(command, "--bind")
    assert pairs(command, "--ro-bind-try") == [(str(sent), str(sent))]
    assert "--unshare-pid" in command and "--die-with-parent" in command


# ── which one ──

class Probe:
    def __init__(self, code: int = 0) -> None:
        self.code, self.commands = code, []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        self.commands.append(command)
        return subprocess.CompletedProcess(command, self.code)


@pytest.mark.parametrize("platform, program, kind", [
    ("darwin", "sandbox-exec", Seatbelt),
    ("linux", "bwrap", Bubblewrap),
])
def test_find_picks_the_systems_own_and_probes_it(monkeypatch, platform, program, kind):
    probe = Probe()
    monkeypatch.setattr(sandbox.sys, "platform", platform)
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: f"/usr/bin/{name}"
                        if name == program else None)
    monkeypatch.setattr(sandbox.subprocess, "run", probe)
    assert isinstance(sandbox.find(), kind)
    [command] = probe.commands
    assert command[0] == f"/usr/bin/{program}" and command[-1] == "true"


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_find_gives_none_when_the_probe_fails_or_nothing_is_there(monkeypatch, platform):
    monkeypatch.setattr(sandbox.sys, "platform", platform)
    monkeypatch.setattr(sandbox.subprocess, "run", Probe(code=1))
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert sandbox.find() is None  # e.g. user namespaces off, or a sandbox already
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    assert sandbox.find() is None


def test_find_gives_none_on_other_systems(monkeypatch):
    monkeypatch.setattr(sandbox.sys, "platform", "win32")
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(sandbox.subprocess, "run", Probe())
    assert sandbox.find() is None


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_on_a_mac_the_seatbelt_profile_is_taken():
    """The profile is only text everywhere else: here macOS runs it (the CI's macOS job)."""
    assert isinstance(sandbox.find(), Seatbelt)
