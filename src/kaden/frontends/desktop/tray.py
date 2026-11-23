"""Kaden's icon in the system tray, while its window is closed: Kaden goes on running
(a life, duties, background work) until the user quits."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from kaden.frontends.desktop import app_menu


class Tray(QSystemTrayIcon):
    """A click, or Open Kaden, calls `open`; Quit calls `quit`."""

    def __init__(self, parent: QWidget, open: Callable[[], None],
                 quit: Callable[[], None]) -> None:
        super().__init__(QIcon(str(app_menu.ICON)), parent)
        self.setToolTip("Kaden")
        menu = QMenu(parent)
        self.open_action = QAction("Open Kaden", menu)
        self.open_action.triggered.connect(open)
        self.quit_action = QAction("Quit", menu)
        self.quit_action.triggered.connect(quit)
        menu.addActions([self.open_action, self.quit_action])
        self.setContextMenu(menu)
        self.activated.connect(
            lambda reason: open() if reason == QSystemTrayIcon.ActivationReason.Trigger
            else None)
        self.show()
