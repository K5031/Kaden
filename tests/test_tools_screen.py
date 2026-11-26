"""The screenshot tool: macOS's screencapture (a stand-in for it), the desktop portal (a
stand-in on a private D-Bus), and which one `find` picks."""

import asyncio
import base64
import contextlib
import os
import shutil
import struct
import subprocess
import sys
import zlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from kaden.api import Image, ToolCall
from kaden.tools.shell_files import ShellFilesTools, screen
from tests.conformance.tools_suite import RecordingContext


def png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data)))

    rows = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8,
                                                              2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def program(tmp_path: Path, script: str) -> str:
    """A program taking screencapture's arguments: `$out` is the file it was told to
    write, the last one."""
    path = tmp_path / "screencapture"
    path.write_text('#!/bin/sh\n[ "$1 $2 $3" = "-x -t png" ] || exit 64\nout=$4\n'
                    + script + "\n")
    path.chmod(0o755)
    return str(path)


def tools(tmp_path: Path, script: str | None, *, allowed: bool = True,
          **kw: float) -> ShellFilesTools:
    capture = None if script is None else screen.Screencapture(
        program(tmp_path, script), allowed=lambda: allowed, **kw)
    return ShellFilesTools(tmp_path / "ws", screen_tools=screen.Screen(capture))


async def shoot(module: ShellFilesTools):
    return await module.execute(ToolCall("c", "screenshot", {}), RecordingContext("c"))


async def test_the_screen_is_returned_as_a_picture(tmp_path: Path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(png(64, 36))
    out = await shoot(tools(tmp_path, f'cp {shot} "$out"'))
    assert not out.is_error and "64 x 36" in out.content, out.content
    assert out.attachments == (
        Image("image/png", base64.b64encode(shot.read_bytes()).decode(), "screenshot.png"),)


async def test_it_asks_like_any_tool_that_reaches_outside_the_folder(tmp_path: Path):
    module = tools(tmp_path, "true")
    [spec] = [s for s in await module.list_tools() if s.name == "screenshot"]
    assert not spec.read_only
    assert not await module.confined(ToolCall("c", "screenshot", {}))


async def test_it_is_not_offered_without_a_way_to_capture(tmp_path: Path):
    module = tools(tmp_path, None)
    assert "screenshot" not in {s.name for s in await module.list_tools()}
    assert (await shoot(module)).is_error


@pytest.mark.parametrize("script, says", [
    ("echo no display >&2; exit 1", "no display"),
    ("true", "no picture"),
    ("sleep 30", "did not finish"),
])
async def test_a_capture_that_fails_is_an_error(tmp_path: Path, script: str, says: str):
    out = await shoot(tools(tmp_path, script, timeout=0.3))
    assert out.is_error and says in out.content and not out.attachments


async def test_without_macos_screen_recording_permission_it_says_how_to_give_it(tmp_path):
    """Without it macOS gives a picture of the wallpaper alone: none is taken."""
    shot = tmp_path / "shot.png"
    shot.write_bytes(png(64, 36))
    out = await shoot(tools(tmp_path, f'cp {shot} "$out"', allowed=False))
    assert out.is_error and "Screen Recording" in out.content and not out.attachments


async def test_a_picture_too_large_for_a_provider_is_an_error(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(screen, "MAX_IMAGE_BYTES", 10)
    shot = tmp_path / "shot.png"
    shot.write_bytes(png(64, 36))
    out = await shoot(tools(tmp_path, f'cp {shot} "$out"'))
    assert out.is_error and "too large" in out.content


async def test_stop_ends_the_capture(tmp_path: Path):
    marker = tmp_path / "pid"
    module = tools(tmp_path, f"echo $$ > {marker}; exec sleep 30")
    task = asyncio.create_task(shoot(module))
    while not marker.exists() or not marker.read_text().strip():
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    pid = int(marker.read_text())
    for _ in range(200):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("the capture program still runs")


# ── the desktop portal, on a bus of the test's own ──

PRIVATE_BUS = pytest.mark.skipif(not sys.platform.startswith("linux")
                                 or shutil.which("dbus-daemon") is None,
                                 reason="needs dbus-daemon")


@contextlib.contextmanager
def private_bus(tmp_path: Path):
    config = tmp_path / "bus.conf"
    config.write_text(
        '<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN" '
        '"http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd"><busconfig>'
        '<type>session</type><listen>unix:path=' + str(tmp_path / "bus") + '</listen>'
        '<policy context="default"><allow send_destination="*" eavesdrop="true"/>'
        '<allow eavesdrop="true"/><allow own="*"/></policy></busconfig>')
    daemon = subprocess.Popen(["dbus-daemon", f"--config-file={config}", "--nofork",
                               "--print-address"], stdout=subprocess.PIPE, text=True)
    try:
        assert daemon.stdout is not None
        yield daemon.stdout.readline().strip()
    finally:
        daemon.terminate()
        daemon.wait()


@contextlib.asynccontextmanager
async def fake_portal(bus: str, answer: tuple[int, str]) -> AsyncIterator[list[str]]:
    """The portal's Screenshot: it writes a picture and answers with its URI (or with a
    response code that is not 0). Yields the paths it wrote."""
    from jeepney import DBusAddress, HeaderFields, MatchRule, new_method_return, new_signal
    from jeepney.bus_messages import message_bus
    from jeepney.io.asyncio import Proxy, open_dbus_router

    written: list[str] = []
    async with open_dbus_router(bus) as router:
        await Proxy(message_bus, router).RequestName(screen.PORTAL_BUS)
        rule = MatchRule(type="method_call", path=screen.PORTAL_PATH)

        async def serve() -> None:
            with router.filter(rule, bufsize=10) as calls:
                while True:
                    call = await calls.get()
                    if call.header.fields[HeaderFields.member] == "Get":  # the version property
                        await router.send(new_method_return(call, "v", (("u", 2),)))
                        continue
                    _parent, options = call.body
                    sender = call.header.fields[HeaderFields.sender][1:].replace(".", "_")
                    handle = f"{screen.PORTAL_PATH}/request/{sender}/{options['handle_token'][1]}"
                    await router.send(new_method_return(call, "o", (handle,)))
                    code, picture = answer
                    if picture:
                        Path(picture).write_bytes(png(8, 4))
                        written.append(picture)
                    results = {"uri": ("s", f"file://{picture}")} if code == 0 else {}
                    await router.send(new_signal(
                        DBusAddress(handle, interface="org.freedesktop.portal.Request"),
                        "Response", "ua{sv}", (code, results)))

        serving = asyncio.create_task(serve())
        try:
            yield written
        finally:
            serving.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await serving


@PRIVATE_BUS
async def test_the_portal_gives_the_picture_and_its_file_is_removed(tmp_path: Path):
    with private_bus(tmp_path) as bus:
        picture = tmp_path / "Screenshot from now.png"
        async with fake_portal(bus, (0, str(picture))) as written:
            assert await asyncio.to_thread(screen.Portal.available, bus)
            raw = await screen.Portal(bus=bus).capture()
    assert raw == png(8, 4) and written == [str(picture)]
    assert not picture.exists()  # it was made for this look only


@PRIVATE_BUS
async def test_a_screenshot_the_desktop_refused_is_an_error(tmp_path: Path):
    with private_bus(tmp_path) as bus:
        async with fake_portal(bus, (1, "")):
            module = ShellFilesTools(tmp_path / "ws",
                                     screen_tools=screen.Screen(screen.Portal(bus=bus)))
            out = await shoot(module)
    assert out.is_error and "refused" in out.content


@PRIVATE_BUS
def test_without_a_portal_there_is_none(tmp_path: Path):
    with private_bus(tmp_path) as bus:
        assert not screen.Portal.available(bus)
    assert not screen.Portal.available(f"unix:path={tmp_path / 'no-bus'}")


# ── which one ──

@pytest.mark.parametrize("platform, which, portal, kind", [
    ("darwin", "/usr/sbin/screencapture", False, screen.Screencapture),
    ("darwin", None, True, type(None)),
    ("linux", None, True, screen.Portal),
    ("linux", "/usr/bin/screencapture", False, type(None)),
    ("win32", "/x", True, type(None)),
])
def test_find_picks_the_systems_own(monkeypatch, platform, which, portal, kind):
    monkeypatch.setattr(screen.sys, "platform", platform)
    monkeypatch.setattr(screen.shutil, "which", lambda name: which)
    monkeypatch.setattr(screen.Portal, "available", staticmethod(lambda bus="SESSION": portal))
    assert isinstance(screen.find(), kind)
