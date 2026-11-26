"""Looking at the user's desktop, for the built-in Tools.

A program cannot read the screen itself on Wayland, nor on macOS without the user's
permission, so each system's own way is used (`find`): on Linux the desktop portal's
Screenshot (xdg-desktop-portal, over D-Bus), on macOS `screencapture`. The tool is offered
only where one is there.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ctypes
import secrets
import shutil
import struct
import sys
import tempfile
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from jeepney import DBusAddress, MatchRule, MessageType, Properties, new_method_call
from jeepney.bus_messages import message_bus
from jeepney.io import blocking
from jeepney.io.asyncio import DBusRouter, Proxy, open_dbus_router

from kaden.api import Image, ToolOutput, ToolSpec
from kaden.tools.shell_files.attachments import MAX_IMAGE_BYTES

NAME = "screenshot"
CAPTURE_TIMEOUT = 20.0  # seconds for a capture
PROBE_TIMEOUT = 2.0  # seconds to ask the session bus whether the portal is there
_PNG_HEADER = 24  # the signature and the start of IHDR, which holds the size

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
_SCREENSHOT = DBusAddress(PORTAL_PATH, bus_name=PORTAL_BUS,
                          interface="org.freedesktop.portal.Screenshot")
_REQUEST = "org.freedesktop.portal.Request"
_CORE_GRAPHICS = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"

SPEC = ToolSpec(
    name=NAME,
    description=(
        "Look at the user's desktop: takes a picture of everything on their screen right "
        "now, all monitors, and shows it to you. Use it when the user asks you to look at "
        "their screen or at something on it. The picture can hold private things, so do "
        "not take one the user did not ask for. You can only look: you cannot click or type."
    ),
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    timeout=60.0, deferred=True,  # only when the user asks about their screen
)


class ScreenError(Exception):
    """Expected failure, reported to the model as an error result."""


class Capture(Protocol):
    async def capture(self) -> bytes:
        """A picture of the whole desktop, as PNG; ScreenError when there is none."""
        ...


def find() -> Capture | None:
    """This system's way to capture the screen, if it has one. Blocking: run it where
    module factories run, off the event loop."""
    if sys.platform == "darwin":
        program = shutil.which("screencapture")
        return Screencapture(program) if program else None
    if sys.platform.startswith("linux") and Portal.available():
        return Portal()
    return None


class Screen:
    """The screenshot tool, offered when there is a `capture`."""

    def __init__(self, capture: Capture | None) -> None:
        self._capture = capture

    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        return (SPEC,) if self._capture is not None else ()

    async def run(self) -> ToolOutput:
        if self._capture is None:
            raise ScreenError("There is no way to take a screenshot on this computer.")
        raw = await self._capture.capture()
        if len(raw) > MAX_IMAGE_BYTES:
            raise ScreenError(f"The screenshot is too large to look at "
                              f"({len(raw) // 2 ** 20} MB): pictures up to "
                              f"{MAX_IMAGE_BYTES // 2 ** 20} MB can be.")
        size = ""
        if len(raw) >= _PNG_HEADER and raw.startswith(b"\x89PNG"):
            width, height = struct.unpack(">II", raw[16:_PNG_HEADER])
            size = f", {width} x {height} pixels"
        image = Image("image/png", base64.b64encode(raw).decode(), "screenshot.png")
        return ToolOutput(f"[Screenshot of the user's desktop{size}]", attachments=(image,))


# ── Linux: the desktop portal ─────────────────────────────────────────

class Portal:
    """xdg-desktop-portal's Screenshot, not interactive: the desktop (GNOME, KDE, ...)
    takes the picture, writes it to a file and answers with its URI. The file was made
    for this look, so it is removed once read."""

    def __init__(self, *, bus: str = "SESSION", timeout: float = CAPTURE_TIMEOUT) -> None:
        self._bus, self._timeout = bus, timeout

    @staticmethod
    def available(bus: str = "SESSION") -> bool:
        """Whether the bus has a portal that takes screenshots (its version answers).
        Blocking."""
        try:
            with blocking.open_dbus_connection(bus) as connection:
                reply = connection.send_and_get_reply(
                    Properties(_SCREENSHOT).get("version"), timeout=PROBE_TIMEOUT)
        except (OSError, ValueError, KeyError):  # no session bus, no portal, no answer
            return False
        return reply.header.message_type == MessageType.method_return

    async def capture(self) -> bytes:
        try:
            async with open_dbus_router(self._bus) as router:
                uri = await asyncio.wait_for(self._ask(router), self._timeout)
        except TimeoutError:
            raise ScreenError(f"The screenshot did not finish within {self._timeout:g} "
                              "seconds.") from None
        except (OSError, ValueError, KeyError) as e:  # no bus, or not one we can use
            raise ScreenError(f"The desktop portal could not be reached: {e}") from None
        path = Path(urllib.parse.unquote(urllib.parse.urlsplit(uri).path))
        try:
            return await asyncio.to_thread(_read_and_remove, path)
        except OSError as e:
            raise ScreenError(f"The screenshot could not be read: {e}") from None

    async def _ask(self, router: DBusRouter) -> str:
        """The URI of the picture. The answer comes as a signal on a request object
        whose path is known beforehand, so it is listened for before asking."""
        token = f"kaden{secrets.token_hex(8)}"
        sender = router.unique_name[1:].replace(".", "_")
        handle = f"{PORTAL_PATH}/request/{sender}/{token}"
        rule = MatchRule(type="signal", interface=_REQUEST, member="Response", path=handle)
        await Proxy(message_bus, router).AddMatch(rule)
        with router.filter(rule) as responses:
            try:
                reply = await router.send_and_get_reply(new_method_call(
                    _SCREENSHOT, "Screenshot", "sa{sv}",
                    ("", {"handle_token": ("s", token), "interactive": ("b", False)})))
                if reply.header.message_type == MessageType.error:
                    raise ScreenError(f"The desktop portal refused: {reply.body}")
                code, results = (await responses.get()).body
            except asyncio.CancelledError:  # stopped, or too slow: the request is withdrawn
                with contextlib.suppress(Exception):
                    await router.send(new_method_call(
                        DBusAddress(handle, bus_name=PORTAL_BUS, interface=_REQUEST), "Close"))
                raise
        if code == 1:
            raise ScreenError("The screenshot was refused on the desktop.")
        if code != 0 or "uri" not in results:
            raise ScreenError("The desktop gave no screenshot.")
        return str(results["uri"][1])


def _read_and_remove(path: Path) -> bytes:
    data = path.read_bytes()
    path.unlink(missing_ok=True)
    return data


# ── macOS: screencapture ──────────────────────────────────────────────

class Screencapture:
    """macOS's `screencapture -x` (no sound), into a temporary folder removed after the
    read. Without the Screen Recording permission macOS gives a picture of the wallpaper
    alone, so `allowed` (CoreGraphics' check) is asked first."""

    def __init__(self, program: str, *, timeout: float = CAPTURE_TIMEOUT,
                 allowed: Callable[[], bool] | None = None) -> None:
        self.program, self.timeout = program, timeout
        self._allowed = allowed or _screen_recording_allowed

    async def capture(self) -> bytes:
        if not await asyncio.to_thread(self._allowed):
            raise ScreenError(
                "macOS does not let Kaden see the screen. The user can allow it in System "
                "Settings → Privacy & Security → Screen Recording, for Kaden or the terminal "
                "it was started from, then start Kaden again.")
        folder = await asyncio.to_thread(tempfile.mkdtemp, prefix="kaden-screen-")
        try:
            return await self._run(Path(folder) / "screenshot.png")
        finally:
            await asyncio.to_thread(shutil.rmtree, folder, True)

    async def _run(self, path: Path) -> bytes:
        process = await asyncio.create_subprocess_exec(
            self.program, "-x", "-t", "png", str(path),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE)
        try:
            _, errors = await asyncio.wait_for(process.communicate(), self.timeout)
        except TimeoutError:
            raise ScreenError(f"The screenshot did not finish within {self.timeout:g} "
                              "seconds.") from None
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        if process.returncode != 0:
            said = errors.decode(errors="replace").strip()[-500:]
            raise ScreenError(f"The screenshot failed (exit code {process.returncode})"
                              + (f": {said}" if said else "."))
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError:
            raise ScreenError("The screenshot program made no picture.") from None


def _screen_recording_allowed() -> bool:
    """Whether macOS lets this process record the screen. Blocking."""
    try:
        check = ctypes.CDLL(_CORE_GRAPHICS).CGPreflightScreenCaptureAccess
    except (OSError, AttributeError):  # a macOS before 10.15 asks no one
        return True
    check.restype = ctypes.c_bool
    return bool(check())
