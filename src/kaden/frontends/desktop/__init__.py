"""Desktop app frontend: PySide6 widgets on a qasync event loop.

Its factory names the event loop it runs on (loop.run_on_qt: a QApplication and qasync's
loop); `run` needs both. PySide6 and qasync are imported here, so the entry point loads (and
the app is offered in settings) only where the `desktop` extra is installed.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from kaden.api import Frontend, FrontendInfo, InstancePaths, RuntimeAPI, Tools

# PySide6 6.12 on Python 3.15 breaks reading a lazily imported module attribute (PEP 810)
# from another module once Qt is loaded. asyncio.to_thread reads this one, so resolve it
# before any Qt import.
_ = concurrent.futures.ThreadPoolExecutor

import qasync  # noqa: F401
from PySide6 import QtWidgets  # noqa: F401

from kaden.frontends.desktop import single
from kaden.frontends.desktop.loop import run_on_qt
from kaden.frontends.life import Life
from kaden.frontends.sessions import SubSessions
from kaden.runtime import backup


def factory(options: dict[str, Any], paths: InstancePaths) -> DesktopFrontend:
    """Options: conversation_id (open it first; default: the newest)."""
    return DesktopFrontend(options.get("conversation_id"), paths.data_dir)


ZOOM_FILE = "desktop.json"  # the app's own view preferences, in the data folder


factory.display_name = "Desktop app"  # type: ignore[attr-defined]
factory.kind = "window"  # type: ignore[attr-defined]
factory.event_loop = run_on_qt  # type: ignore[attr-defined]


class DesktopFrontend(Frontend):
    def __init__(self, conversation_id: str | None = None,
                 data_dir: Path | None = None) -> None:
        self.conversation_id = conversation_id
        self.data_dir = data_dir  # None: no zoom or theme remembered, no single window
        self.zoom_file = data_dir / ZOOM_FILE if data_dir is not None else None
        self.sessions = SubSessions(data_dir) if data_dir is not None else None
        self.life = Life(data_dir) if data_dir is not None else None

    def tools(self) -> Sequence[Tools]:
        return [self.sessions] if self.sessions is not None else []

    @property
    def info(self) -> FrontendInfo:
        return FrontendInfo(name="desktop", interactive_permissions=True, kind="window")

    async def run(self, runtime: RuntimeAPI) -> None:
        """Returns when the main window closes or the runtime's event stream ends."""
        from PySide6.QtWidgets import QApplication, QSystemTrayIcon

        from kaden.frontends.desktop import theme
        from kaden.frontends.desktop.viewmodel import Controller, ConversationModel
        from kaden.frontends.desktop.window import (
            MainWindow,
            load_look,
            load_view,
            load_voice,
            load_voice_at_start,
            save_look,
        )

        app = QApplication.instance()
        if app is None:
            raise RuntimeError("the desktop frontend runs on its own event loop (factory.event_loop)")
        if self.data_dir is not None and (await runtime.settings()).already_open:
            await self._hand_over(self.data_dir)
            return
        if self.zoom_file is not None:
            zoom, look = await load_look(self.zoom_file)
            theme.set_theme(app, look)
            theme.set_zoom(app, zoom)
        controller = Controller(runtime, ConversationModel())
        window = MainWindow(runtime, controller, self.zoom_file, self.sessions,
                            life=self.life,
                            tray=QSystemTrayIcon.isSystemTrayAvailable())
        if self.life is not None and self.life.has_lived:  # it lives on
            controller.command(self.life.start())
            if self.zoom_file is not None and await load_view(self.zoom_file):
                window.continuous_switch.setChecked(True)  # where the user left off
        if self.zoom_file is not None:
            await window.voice.restore(await load_voice(self.zoom_file))
            if await load_voice_at_start(self.zoom_file):
                window.voice.load_at_start = True
                controller.command(window.voice.preload())
        server = (await single.listen(self.data_dir, window.bring_forward)
                  if self.data_dir is not None else None)
        closed = asyncio.Event()
        window.closed.connect(closed.set)
        window.showMaximized()  # the whole screen, as other chat apps open
        events = asyncio.create_task(controller.run())
        controller.command(window.begin(self.conversation_id))
        waiter = asyncio.create_task(closed.wait())
        tasks = {events, waiter}
        if self.data_dir is not None:  # the chats and Continuous, backed up while it runs
            tasks.add(asyncio.create_task(backup.keep_backed_up(
                self.data_dir, failed=lambda why: window.model.notify(why))))
        try:
            await asyncio.wait({events, waiter}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            if server is not None:
                server.close()
                single.socket_path(self.data_dir).unlink(missing_ok=True)
            window.quit()
            if self.life is not None:
                await self.life.aclose()
            if self.zoom_file is not None:
                await save_look(self.zoom_file, window.voice.voice,
                                life=window.showing_life,
                                load_voice=window.voice.load_at_start)

    async def _hand_over(self, data_dir: Path) -> None:
        """Another Kaden has the data folder: bring its window forward, or, when none
        answers (one with no window has it), say so once."""
        from PySide6.QtWidgets import QMessageBox

        if await single.show_other(data_dir):
            return
        box = QMessageBox(QMessageBox.Icon.Information, "Kaden",
                          "Kaden is already running without a window, using the same "
                          "data. Quit it, then open Kaden again.")
        closed = asyncio.get_running_loop().create_future()
        box.finished.connect(lambda _: closed.done() or closed.set_result(None))
        box.open()
        await closed
