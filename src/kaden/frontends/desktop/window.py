"""The desktop app's main window, and the look and view it keeps in desktop.json. Its
parts are in the modules beside it (widgets, sidebar, conversation, messages, prompts,
composer, frame, dialogs, settings). Widgets render the view model and turn clicks into
Controller commands; no widget awaits the runtime.

Dialogs use open() and signals, never exec(): exec() would run a nested event loop inside
the qasync loop that also runs the runtime.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QEvent,
    QFileSystemWatcher,
    QPoint,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QImage,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from kaden.api import (
    ConversationInfo,
    ModelInfo,
    PermissionPolicy,
    PermissionRule,
    Prompt,
    PromptKind,
    RuntimeAPI,
    SettingsUpdate,
)
from kaden.frontends.common import (
    PERMISSION_LABELS,
    PERMISSION_SHORT,
    UNCHANGED,
    delete_details,
    duty_status,
    folder_path,
    model_label,
    prompt_summary,
    raises_limit,
)
from kaden.frontends.desktop import app_menu, theme
from kaden.frontends.desktop.composer import AttachmentStrip, MessageBox
from kaden.frontends.desktop.conversation import ConversationView
from kaden.frontends.desktop.dialogs import (
    DutyDialog,
    LifeDialog,
    MemoryDialog,
    WelcomeDialog,
    choose_folder,
    confirm,
)
from kaden.frontends.desktop.frame import DragArea, RoundedCorners, WindowEdges
from kaden.frontends.desktop.settings import LifeSettingsDialog, McpDialog, SettingsDialog
from kaden.frontends.desktop.sidebar import ConversationList, RowDelegate
from kaden.frontends.desktop.tray import Tray
from kaden.frontends.desktop.viewmodel import (
    BUDGET_MESSAGE,
    Change,
    Controller,
    ConversationListChanged,
    ConversationModel,
    MemoryListChanged,
    PromptsChanged,
    Reset,
    StatusChanged,
    ToolItem,
    clip,
    conversation_label,
    conversation_preview,
    needs_welcome,
    relative_date,
    user_message,
)
from kaden.frontends.desktop.voice.mode import DEFAULT_VOICE, VoiceMode
from kaden.frontends.desktop.widgets import (
    COLUMN_WIDTH,
    DATE_ROLE,
    DOCS_URL,
    FLAGS_ROLE,
    KIND_ROLE,
    PREVIEW_ROLE,
    TITLE_ROLE,
    ElidedLabel,
    Switch,
    local_files,
    text_label,
)
from kaden.frontends.life import Life
from kaden.frontends.remote import RemoteRuntime
from kaden.frontends.sessions import SubSession, SubSessions

VOICE_DIR = "voice"  # the speech models, in the data folder


EMPTY_TITLE = "Start a conversation"


RECENT_FOLDERS = 6  # a new chat's folder menu: its own and recent ones
WINDOW_BUTTON = QSize(38, 28)  # minimize, maximize and close, at the header's top


SIDEBAR_WIDTH = 288  # px at 100%, as Claude's: room for a title and a sub-session's under it


async def load_look(path: Path) -> tuple[float, str]:
    """The zoom and the theme saved in `path`; 1.0 and "system" where there is none or it
    is unreadable."""
    def read() -> tuple[float, str]:
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            name = saved.get("theme")
            known = name in theme.THEMES or (
                isinstance(name, str) and name.startswith(theme.CUSTOM))
            return float(saved.get("zoom", 1.0)), name if known else "system"
        except (OSError, ValueError, TypeError, AttributeError):
            return 1.0, "system"
    return await asyncio.to_thread(read)


async def load_themes(folder: Path) -> None:
    """The custom themes in `folder`, which is made so it can be watched: read before the
    saved theme is chosen."""
    def read() -> dict[str, theme.CustomTheme]:
        folder.mkdir(parents=True, exist_ok=True)
        return theme.read_themes(folder)
    theme.set_custom(await asyncio.to_thread(read))


async def save_look(path: Path, voice: str = DEFAULT_VOICE, *, life: bool = False,
                    load_voice: bool = False) -> None:
    """Remember the zoom and the theme in use, the voice chosen, whether the life's view
    was open (so the app opens on it again), and whether voice loads at start."""
    look = json.dumps({"zoom": theme.zoom(), "theme": theme.choice(), "voice": voice,
                       "life": life, "load_voice": load_voice})
    await asyncio.to_thread(path.write_text, look, encoding="utf-8")


async def _saved(path: Path, key: str) -> bool:
    def read() -> bool:
        try:
            return json.loads(path.read_text(encoding="utf-8")).get(key) is True
        except (OSError, ValueError, AttributeError):
            return False
    return await asyncio.to_thread(read)


async def load_view(path: Path) -> bool:
    """Whether the life's view was open when the app last closed."""
    return await _saved(path, "life")


async def load_voice_at_start(path: Path) -> bool:
    """Whether the user wants voice loaded when Kaden starts."""
    return await _saved(path, "load_voice")



async def load_voice(path: Path) -> str:
    """The name of the voice saved in `path`; DEFAULT_VOICE where there is none."""
    def read() -> str:
        try:
            name = json.loads(path.read_text(encoding="utf-8")).get("voice")
        except (OSError, ValueError, AttributeError):
            return DEFAULT_VOICE
        return name if isinstance(name, str) else DEFAULT_VOICE
    return await asyncio.to_thread(read)


@dataclass(frozen=True, slots=True, eq=False)
class Target:
    """What the user last asked to see, which is what shows once it has opened; the newest
    one wins and the sidebar highlights it. A sub-session, else the window's own
    conversation: the one open there or, while another `opening`s, the one asked for
    (`conversation`; None is a new chat)."""

    session: SubSession | None = None
    opening: bool = False
    conversation: str | None = None


@dataclass(frozen=True, slots=True)
class Viewing:
    """A sub-session on screen, or (no session) the life: what shows it, and what is
    given back when it leaves."""

    session: SubSession | None
    remote: RemoteRuntime
    controller: Controller
    pump: asyncio.Task[None]


# ── main window ───────────────────────────────────────────────────────────


class MainWindow(QMainWindow):
    closed = Signal()

    def __init__(self, runtime: RuntimeAPI, controller: Controller,
                 zoom_file: Path | None = None, sessions: SubSessions | None = None,
                 voice: VoiceMode | None = None, *,
                 life: Life | None = None, tray: bool = False) -> None:
        super().__init__()
        self.runtime = runtime
        self.home = controller  # this window's own conversation
        controller.folder_required = True  # its new chats get a folder before a message
        controller.ask_for_folder = self._offer_folders
        self.life = life  # the one who lives on, hosted by the app
        self.life_dialog: LifeDialog | None = None
        # With a system tray, closing the window hides it and Kaden runs on; quit() quits.
        self.tray = Tray(self, self.bring_forward, self.quit) if tray else None
        if self.tray is not None:
            self.tray.messageClicked.connect(self.notification_clicked)
        self._prompts_seen: set[str] = set()  # the window's own conversation's
        self._notified_life = False  # the last notification was the life's
        self.told_of_tray = False  # the first close in a run says Kaden runs on
        self._quitting = False
        self._life_on = False  # its view is up, or coming up while it starts
        self._viewing: Viewing | None = None  # the sub-session on screen instead of it
        self._target = Target()
        # Talking with Kaden; its speech models are kept beside the zoom's file, which
        # the frontend reads before the window opens and writes when it has closed.
        self.voice = voice or VoiceMode(
            controller, zoom_file.parent / VOICE_DIR if zoom_file is not None else None,
            self._ask)
        self.voice.changed.connect(self._show_voice)
        # Custom themes, kept beside the zoom's file; saving one restyles the app at once.
        self._themes_dir = zoom_file.parent / theme.THEMES_DIR if zoom_file else None
        self._themes_watch = QFileSystemWatcher(self)
        # One save may change a file and the folder both: one read for them, a moment on.
        self._themes_due = QTimer(self, singleShot=True, interval=150)
        self._themes_due.timeout.connect(self._reload_themes)
        self._themes_watch.directoryChanged.connect(self._themes_due.start)
        self._themes_watch.fileChanged.connect(self._themes_due.start)
        self._themes_read = 0  # reads started; a read finishing after a newer one is dropped
        self._watch_themes()
        self.sessions = sessions  # the sub-sessions this window hosts
        controller.sessions = sessions
        self._collapsed: set[str] = set()  # sidebar groups the user folded
        self.home.model.on_change = self._home_changed  # for as long as the window is
        self._dialog: QDialog | None = None
        self.memory_dialog: MemoryDialog | None = None
        self.mcp_dialog: McpDialog | None = None
        self._conversations: dict[str, ConversationInfo] = {}
        self._models: Sequence[ModelInfo] | str | None = None  # a str says why there are none
        self.setWindowTitle("Kaden")
        # No title bar from the desktop: the header and the sidebar's top row move the
        # window, the header has its controls, and the edges resize it (WindowEdges).
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self._edges = WindowEdges(self)
        self.resize(1000, 720)
        self._build()
        self._build_menus()
        self._refresh_status()
        self._corners = RoundedCorners(self)
        if sessions is not None:
            sessions.listeners.append(self._sessions_changed)
        if life is not None:
            life.listeners.append(self._life_said)

    @property
    def controller(self) -> Controller:
        """Of what is on screen: a sub-session, else the window's own conversation."""
        return self._viewing.controller if self._viewing is not None else self.home

    @property
    def model(self) -> ConversationModel:
        return self.controller.model

    # ── layout ────────────────────────────────────────────────────────────

    @property
    def showing_life(self) -> bool:
        return self._viewing is not None and self._viewing.session is None

    def _build(self) -> None:
        sidebar = self.sidebar = QWidget(objectName="sidebar")
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(8, 8, 8, 10)
        side.setSpacing(2)
        self.new_action = QAction("&New chat", self, shortcut=QKeySequence.StandardKey.New)
        self.new_action.triggered.connect(lambda: self.open_conversation(None))
        self.menu_button = QToolButton(objectName="menu")
        self.menu_button.setIconSize(QSize(18, 18))
        self.menu_button.setToolTip("Menu")
        self.menu_button.setAccessibleName("Menu")
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.new_button = QPushButton("  New chat", objectName="nav")
        self.new_button.setIconSize(QSize(16, 16))
        self.new_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_button.setToolTip("Start a new conversation (Ctrl+N)")
        self.new_button.clicked.connect(self.new_action.trigger)
        self.conversation_list = ConversationList()
        self.conversation_list.setObjectName("conversations")
        self.conversation_list.setItemDelegate(RowDelegate(self.conversation_list, compact=True))
        self.conversation_list.setMouseTracking(True)
        self.conversation_list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.conversation_list.setAccessibleName("Conversations")
        self.conversation_list.setToolTip("Right-click a conversation, or press the Menu key, "
                                          "to rename, archive or delete it.")
        self.conversation_list.chosen.connect(self._conversation_chosen)
        self.conversation_list.menu_requested.connect(self._row_menu)
        for keys, command in ((QKeySequence("F2"), self.rename_conversation),
                              (QKeySequence.StandardKey.Delete, self.delete_conversation)):
            shortcut = QShortcut(keys, self.conversation_list,
                                 context=Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(lambda c=command: self._on_current(c))
        self.show_archived = QAction("Show &archived conversations", self, checkable=True)
        self.show_archived.toggled.connect(lambda _: self.home.command(self.fill_sidebar()))
        self.switch_reason = text_label(selectable=False, role="small")
        self.user_name = getpass.getuser()
        self.profile_button = QToolButton(objectName="profile", text=f" {self.user_name}")
        self.profile_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.profile_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.profile_button.setIconSize(QSize(22, 22))
        self.profile_button.setAccessibleName("Profile and settings")
        self.profile_button.setSizePolicy(QSizePolicy.Policy.Expanding,
                                          QSizePolicy.Policy.Fixed)
        self.gear_button = QToolButton(objectName="gear")
        self.gear_button.setToolTip("Settings (Ctrl+,)")
        self.gear_button.setAccessibleName("Settings")
        self.gear_button.setIconSize(QSize(18, 18))
        top_row = DragArea()
        top = QHBoxLayout(top_row)
        top.setContentsMargins(0, 0, 0, 6)
        top.addWidget(self.menu_button)
        top.addStretch()
        side.addWidget(top_row)
        side.addWidget(self.new_button)
        side.addSpacing(10)
        side.addWidget(self.conversation_list, 1)
        side.addWidget(self.switch_reason)
        profile_row = QHBoxLayout()
        profile_row.setContentsMargins(0, 6, 0, 0)
        profile_row.addWidget(self.profile_button, 1)
        profile_row.addWidget(self.gear_button)
        side.addLayout(profile_row)

        main = QWidget()
        column = QVBoxLayout(main)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.banner = QFrame(objectName="banner")
        banner = QHBoxLayout(self.banner)
        banner.setContentsMargins(12, 8, 8, 8)
        self.banner_text = text_label()
        self.banner_settings = QPushButton("Open Settings")
        self.banner_settings.clicked.connect(lambda: self.open_settings())
        banner.addWidget(self.banner_text, 1)
        banner.addWidget(self.banner_settings)
        self.banner.hide()

        header = DragArea(objectName="header")
        head = QHBoxLayout(header)
        head.setContentsMargins(18, 0, 0, 0)  # the window buttons reach the top and right
        head.setSpacing(4)
        self.title_label = ElidedLabel()
        self.title_label.setProperty("role", "header")
        self.duty_label = text_label(wrap=False, selectable=False, role="small")
        self.duty_label.hide()  # only with a duty: when it checks in next, or paused
        titles = self.title_row = QHBoxLayout()
        titles.setContentsMargins(0, 14, 0, 14)  # the header's height
        titles.setSpacing(4)
        # The life's view has no sidebar: its ☰ and the way back are here.
        self.view_menu_button = QToolButton(objectName="menu")
        self.view_menu_button.setIconSize(QSize(18, 18))
        self.view_menu_button.setToolTip("Menu")
        self.view_menu_button.setAccessibleName("Menu")
        self.view_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.view_menu_button.hide()
        self.files_button = QToolButton(text="Files")
        self.files_button.setToolTip("Open their folder: what they keep there")
        self.files_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.files_button.clicked.connect(self.open_files)
        self.files_button.hide()
        titles.addWidget(self.view_menu_button)
        titles.addWidget(self.title_label, 100)  # a chat's title takes the room there is
        # The life's name in its view, as wide as it is, so Files follows it.
        self.life_name = QLabel()
        self.life_name.setProperty("role", "header")
        self.life_name.hide()
        titles.addWidget(self.life_name)
        # As tall in both views, so the Continuous switch keeps its place.
        self.title_label.setMinimumHeight(self.view_menu_button.sizeHint().height())
        titles.addWidget(self.files_button)
        titles.addStretch(1)
        titles.addWidget(self.duty_label)
        head.addLayout(titles, 1)
        head.addSpacing(8)
        # The one way between the user's chats and Continuous, in the same place in both.
        self.continuous_switch = Switch("Continuous")
        self.continuous_switch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.continuous_switch.setVisible(self.life is not None)
        self.continuous_switch.toggled.connect(self.show_life)
        head.addWidget(self.continuous_switch)
        head.addSpacing(12)
        self.window_buttons: dict[str, QToolButton] = {}
        for name, tip, command in (("minimize", "Minimize", self.showMinimized),
                                   ("maximize", "Maximize", self._toggle_maximized),
                                   ("close", "Close", self.close)):
            button = QToolButton(objectName="titleClose" if name == "close"
                                 else "titleControl")
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            button.setIconSize(QSize(14, 14))
            button.setFixedSize(WINDOW_BUTTON)
            button.clicked.connect(command)
            head.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
            self.window_buttons[name] = button

        self.empty_state = QWidget()
        empty = QVBoxLayout(self.empty_state)
        empty.addStretch(2)
        title = text_label(EMPTY_TITLE, selectable=False, role="title")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        empty.addWidget(title)
        # A new chat's folder, and recent ones to start it in instead; only before the first
        # message (the header's tooltip shows it after).
        self.folder_button = QToolButton(objectName="folderPicker")
        self.folder_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.folder_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.folder_button.setIconSize(QSize(18, 18))
        self.folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.folder_button.setAccessibleName("Work folder")
        folder_menu = QMenu(self, objectName="folderMenu")
        folder_menu.aboutToShow.connect(self._fill_folder_menu)
        self.folder_button.setMenu(folder_menu)
        self.folder_button.hide()
        empty.addSpacing(16)
        empty.addWidget(self.folder_button, 0, Qt.AlignmentFlag.AlignHCenter)
        empty.addStretch(3)
        self.conversation = ConversationView(lambda: self.controller, self.empty_state,
                                             self._notice_link)
        self.conversation.chose.connect(lambda: self.input.setFocus())

        self.composer = QFrame(objectName="composer")
        box = QVBoxLayout(self.composer)  # what is attached, then the row to write in
        box.setContentsMargins(10, 12, 10, 12)  # Claude's room around the text
        box.setSpacing(4)
        self.attachments = AttachmentStrip()
        box.addWidget(self.attachments)
        # The speech models' download, while it runs: how far it is, in words and a bar.
        self.voice_progress = QWidget()
        downloading = QVBoxLayout(self.voice_progress)
        downloading.setContentsMargins(10, 6, 10, 2)
        downloading.setSpacing(6)
        self.voice_progress_label = text_label(selectable=False, role="small")
        self.voice_progress_bar = QProgressBar()
        self.voice_progress_bar.setRange(0, 1000)
        self.voice_progress_bar.setTextVisible(False)
        self.voice_progress_bar.setFixedHeight(6)
        downloading.addWidget(self.voice_progress_label)
        downloading.addWidget(self.voice_progress_bar)
        self.voice_progress.hide()
        box.addWidget(self.voice_progress)
        composer = QHBoxLayout()  # one row: Add files, the text, then Send at its foot
        composer.setSpacing(8)
        box.addLayout(composer)
        self._pasted: Path | None = None  # the folder pasted pictures are saved in
        self._pasting = asyncio.Lock()  # one saved at a time: each gets its own name
        self._icons: tuple[Any, ...] | None = None  # what the icons were last painted for
        self.setAcceptDrops(True)  # files can be dropped anywhere on the window
        self.input = MessageBox()
        self.input.setPlaceholderText("Message Kaden")
        self.input.setToolTip("Enter sends. Shift+Enter starts a new line.")
        self.input.setAccessibleName("Message")
        self.input.submitted.connect(self._send)
        self.input.attached.connect(self.attachments.add)
        self.input.pasted.connect(self._paste_image)
        self.input.focus_changed.connect(lambda on: theme.mark(self.composer, "focused", on))
        self.send_button = QToolButton(objectName="send", text="Send")
        self.send_button.setFixedSize(32, 32)
        self.send_button.setIconSize(QSize(16, 16))
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.clicked.connect(self._send_or_stop)
        self.attach_button = QToolButton(objectName="send")
        self.attach_button.setFixedSize(32, 32)
        self.attach_button.setIconSize(QSize(16, 16))
        self.attach_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.attach_button.setToolTip("Add files")
        self.attach_button.setAccessibleName("Add files")
        self.attach_button.clicked.connect(self._choose_files)
        self.file_dialog: QFileDialog | None = None
        composer.addWidget(self.attach_button, 0, Qt.AlignmentFlag.AlignBottom)
        composer.addWidget(self.input, 1, Qt.AlignmentFlag.AlignVCenter)
        self.microphone_button = QToolButton(objectName="send")
        self.microphone_button.setAccessibleName("Microphone")
        self.microphone_button.clicked.connect(self.voice.toggle_microphone)
        self.speaker_button = QToolButton(objectName="send")
        self.speaker_button.setAccessibleName("Read aloud")
        self.speaker_button.clicked.connect(self.voice.toggle_speaker)
        for button in (self.microphone_button, self.speaker_button):
            button.setFixedSize(32, 32)
            button.setIconSize(QSize(16, 16))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            composer.addWidget(button, 0, Qt.AlignmentFlag.AlignBottom)
        composer.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignBottom)
        self.model_button = QToolButton(objectName="modelPicker")
        self.model_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.model_button.setToolTip("Change the model")
        self.model_button.setAccessibleName("Model")
        footer = QHBoxLayout()
        footer.setContentsMargins(10, 4, 4, 0)
        self.permissions_button = QToolButton(objectName="modelPicker")
        self.permissions_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.permissions_button.setAccessibleName("What the agent may do")
        permissions_menu = QMenu(self)
        self.permission_actions: dict[PermissionPolicy, QAction] = {}
        for policy, label in PERMISSION_LABELS.items():
            action = permissions_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda _=False, p=policy: self.choose_permissions(p))
            self.permission_actions[policy] = action
        permissions_menu.addSeparator()
        self.plan_action = permissions_menu.addAction("Plan mode")
        self.plan_action.setCheckable(True)
        self.plan_action.setToolTip("Only look around until you approve a plan")
        self.plan_action.triggered.connect(lambda on: self.controller.set_plan_mode(on))
        self.permissions_button.setMenu(permissions_menu)
        footer.addStretch(1)
        footer.addWidget(self.permissions_button)
        footer.addWidget(self.model_button)
        bottom_column = QWidget()
        bottom_column.setMaximumWidth(COLUMN_WIDTH)
        bottom = QVBoxLayout(bottom_column)
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(0)
        bottom.addLayout(self.conversation.prompts)
        bottom.addWidget(self.composer)
        bottom.addLayout(footer)
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(24, 0, 24, 10)
        # The life's view has no sidebar: Settings, bottom left as there, and as much
        # room on the right, so the message box stays centred.
        self.view_gear_button = QToolButton(objectName="gear")
        self.view_gear_button.setToolTip("Settings (Ctrl+,)")
        self.view_gear_button.setAccessibleName("Settings")
        self.view_gear_button.setIconSize(QSize(18, 18))
        self.view_gear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.view_gear_button.clicked.connect(lambda: self.open_settings())
        self.view_gear_button.hide()
        self._gear_balance = QWidget()
        self._gear_balance.hide()
        bottom_row.addWidget(self.view_gear_button, 0, Qt.AlignmentFlag.AlignBottom)
        bottom_row.addStretch(1)  # the gear in the corner, the message box centred
        bottom_row.addWidget(bottom_column, 4)
        bottom_row.addStretch(1)
        bottom_row.addWidget(self._gear_balance)

        column.addWidget(header)
        banner_row = QVBoxLayout()
        banner_row.setContentsMargins(20, 10, 20, 0)
        banner_row.addWidget(self.banner)
        column.addLayout(banner_row)
        column.addWidget(self.conversation, 1)
        # Where the life's conversation comes while its process starts.
        self.waking = text_label("Waking up…", selectable=False, tone="muted")
        self.waking.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.waking.hide()
        column.addWidget(self.waking, 1)
        column.addLayout(bottom_row)

        splitter = self.splitter = QSplitter()
        splitter.setHandleWidth(1)
        splitter.addWidget(sidebar)
        splitter.addWidget(main)
        splitter.setStretchFactor(1, 1)
        sidebar_width = round(SIDEBAR_WIDTH * theme.zoom())
        splitter.setSizes([sidebar_width, max(self.width() - sidebar_width, COLUMN_WIDTH)])
        self.setCentralWidget(splitter)
        self._show_voice()

    def _paint_icons(self) -> None:
        """Paint the window's icons, when what they show has changed since."""
        colors = theme.current()
        painted = (colors, self.model.busy, self.voice.listening, self.voice.speaking,
                   self.isMaximized(), self.user_name)
        if painted == self._icons:
            return
        self._icons = painted
        self.send_button.setIcon(theme.icon("stop" if self.model.busy else "send",
                                            colors.text))
        self.gear_button.setIcon(theme.icon("gear", colors.muted, 18))
        self.view_gear_button.setIcon(theme.icon("gear", colors.muted, 18))
        self.menu_button.setIcon(theme.icon("menu", colors.muted, 18))
        self.view_menu_button.setIcon(theme.icon("menu", colors.muted, 18))
        self.new_button.setIcon(theme.icon("plus", colors.text, 16))
        self.attach_button.setIcon(theme.icon("plus", colors.muted, 16))
        self.microphone_button.setIcon(theme.icon(
            "microphone", colors.on_accent if self.voice.listening else colors.muted, 16))
        self.speaker_button.setIcon(theme.icon(
            "speaker", colors.on_accent if self.voice.speaking else colors.muted, 16))
        self.folder_button.setIcon(theme.icon("folder", colors.text, 18))
        maximized = self.isMaximized()
        for name, button in self.window_buttons.items():
            kind = "restore" if name == "maximize" and maximized else name
            button.setIcon(theme.icon(kind, colors.muted))
        self.window_buttons["maximize"].setToolTip("Restore" if maximized else "Maximize")
        self.profile_button.setIcon(theme.avatar(self.user_name))

    def _choose_files(self) -> None:
        """Add files picks files to go with the message, as dropping them does."""
        folder = self.model.conversation.folder if self.model.conversation else None
        self.file_dialog = QFileDialog(self, "Add files", folder or str(Path.home()))
        self.file_dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
        self.file_dialog.filesSelected.connect(self.attachments.add)
        self.file_dialog.open()

    def _choose_folder(self) -> None:
        if reason := self.home.can_switch():
            self.model.notify(reason)
            return
        folder = self.model.conversation.folder if self.model.conversation else None
        choose_folder(self, folder or str(Path.home()), self.folder_chosen)

    def _offer_folders(self) -> None:
        """A new chat needs a folder before it is used: open the folder menu."""
        button = self.folder_button
        button.menu().popup(button.mapToGlobal(QPoint(0, button.height())))

    def _fill_folder_menu(self) -> None:
        """The folder chosen for the open chat, then those of recent conversations, newest
        first."""
        menu = self.folder_button.menu()
        menu.clear()
        own = (self.model.conversation.folder
               if self.model.conversation and self.controller.folder_picked else None)
        recent = [info.folder for info in sorted(self._conversations.values(),
                                                 key=lambda i: i.updated_at, reverse=True)
                  if info.folder is not None]
        for folder in list(dict.fromkeys([own, *recent]))[:RECENT_FOLDERS]:
            if folder is None:
                continue
            action = menu.addAction(Path(folder).name or folder)
            action.setCheckable(True)
            action.setChecked(folder == own)
            action.setToolTip(folder_path(folder))
            action.triggered.connect(lambda _=False, f=folder: self.folder_chosen(f))
        menu.setToolTipsVisible(True)
        menu.addSeparator()
        menu.addAction("Choose folder…", self._choose_folder)

    def folder_chosen(self, folder: str) -> None:
        """Start a chat in `folder`; an open chat with no messages moves there."""
        if not folder or (self.model.conversation is not None and self.controller.folder_picked
                          and folder == self.model.conversation.folder):
            return
        self._leave_session()  # the chat starts in the window's own conversation
        task = self.home.new_in_folder(folder)
        task.add_done_callback(lambda _: self.input.setFocus())  # its Reset fills the sidebar

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange,  # the system switched light or dark
                            QEvent.Type.WindowStateChange):  # maximize becomes restore
            self._icons = None
            self._paint_icons()

    def choose_permissions(self, policy: PermissionPolicy) -> None:
        """Change what the agent may do; doing more without asking is confirmed first."""
        current = self.home.model.permissions
        self._refresh_status()  # the menu shows the current choice until it changes

        def apply() -> None:
            self.home.command(self.runtime.update_settings(SettingsUpdate(permissions=policy)))

        order = list(PermissionPolicy)
        if current is not None and order.index(policy) > order.index(current):
            confirm(self, "Let the agent do more without asking?", apply, yes="Allow",
                    details=PERMISSION_LABELS[policy] + ".")
        elif policy != current:
            apply()

    def _toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _build_menus(self) -> None:
        """No menu bar. The ☰ menu holds what is about the conversation (new chat, duty,
        stop, view, model); the profile menu at the bottom holds the app (settings, memory,
        tool servers, help, quit). Every action is also the window's, so its shortcut works
        with the menus closed."""
        settings = QAction("&Settings…", self)
        settings.setShortcuts([QKeySequence("Ctrl+,")])  # Preferences is unbound on Linux
        settings.triggered.connect(lambda: self.open_settings())
        stop = QAction("S&top", self, shortcut=QKeySequence("Esc"))
        stop.triggered.connect(self._stop)
        quit_action = QAction("&Quit", self, shortcut=QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.quit)
        self.memory_action = QAction("&Memory…", self)
        self.memory_action.triggered.connect(self.open_memory)
        self.duty_action = QAction("Set a &duty…", self)
        self.duty_action.setStatusTip("Give this conversation standing work it carries on "
                                      "by itself.")
        self.duty_action.triggered.connect(self.open_duty)
        servers = QAction("&Tool servers (MCP)…", self)
        servers.triggered.connect(self.open_tool_servers)
        self.app_menu_action = QAction("&Add to App Menu", self)
        self.app_menu_action.setStatusTip("Start Kaden from your app menu, with its own icon.")
        self.app_menu_action.triggered.connect(self._add_to_app_menu)
        self.app_menu_action.setVisible(app_menu.supported() and not app_menu.is_current())
        help_action = QAction("&Help", self, shortcut=QKeySequence.StandardKey.HelpContents)
        help_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(DOCS_URL)))

        self.folder_action = QAction("New chat in another &folder…", self)
        self.folder_action.triggered.connect(self._choose_folder)
        main_menu = QMenu(self)
        self.menu_button.setMenu(main_menu)
        self.view_menu_button.setMenu(main_menu)
        main_menu.addActions([self.new_action, self.folder_action, self.duty_action, stop])
        main_menu.addSeparator()
        view_menu = main_menu.addMenu("&View")
        for text, keys, steps in (
                ("Zoom &In", [QKeySequence.StandardKey.ZoomIn, QKeySequence("Ctrl+=")], 1),
                ("Zoom &Out", [QKeySequence.StandardKey.ZoomOut], -1),
                ("&Actual Size", [QKeySequence("Ctrl+0")], 0)):
            action = QAction(text, self)
            action.setShortcuts(keys)
            action.triggered.connect(lambda _=False, s=steps: self.zoom(s))
            view_menu.addAction(action)
        theme_menu = self._theme_menu = view_menu.addMenu("&Theme")
        theme_menu.setToolTipsVisible(True)  # what of a custom theme's file was skipped
        themes = self._theme_group = QActionGroup(self)
        self.theme_actions: dict[str, QAction] = {}
        for name, text in (("system", "&System"), ("light", "&Light"), ("dark", "&Dark")):
            action = QAction(text, self, checkable=True)
            action.setChecked(name == theme.choice())
            action.triggered.connect(lambda _=False, n=name: self.set_theme(n))
            themes.addAction(action)
            theme_menu.addAction(action)
            self.theme_actions[name] = action
        self._custom_separator = theme_menu.addSeparator()
        self._fill_custom_themes()
        view_menu.addSeparator()
        view_menu.addAction(self.show_archived)
        self.model_menu = main_menu.addMenu("&Model")
        self.model_menu.aboutToShow.connect(self._fill_model_menu)
        self._fill_model_menu()
        self.model_button.setMenu(self.model_menu)

        profile_menu = QMenu(self)
        self.profile_button.setMenu(profile_menu)
        self.profile_header = profile_menu.addAction(self.user_name)
        self.profile_header.setEnabled(False)
        profile_menu.aboutToShow.connect(self._fill_profile_header)
        profile_menu.addSeparator()
        profile_menu.addActions([settings, self.memory_action, servers])
        profile_menu.addSeparator()
        profile_menu.addActions([self.app_menu_action, help_action])
        profile_menu.addSeparator()
        profile_menu.addAction(quit_action)
        self.gear_button.clicked.connect(settings.trigger)
        for menu in (main_menu, view_menu, profile_menu):
            self.addActions([a for a in menu.actions() if not a.menu()])

    def _fill_profile_header(self) -> None:
        spent = self.model.spending_text
        self.profile_header.setText(f"{self.user_name} · {spent}" if spent else self.user_name)

    def zoom(self, steps: int) -> None:
        """Make everything larger (steps > 0), smaller, or its normal size (0)."""
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        before = theme.zoom()
        theme.set_zoom(app, theme.zoom_step(before, steps) if steps else 1.0)
        # The sidebar grows and shrinks with everything else, as Claude's does, keeping the
        # width the user dragged it to in proportion.
        sidebar, rest = self.splitter.sizes()
        wider = round(sidebar * theme.zoom() / before)
        self.splitter.setSizes([wider, max(rest - (wider - sidebar), 0)])

    def set_theme(self, name: str) -> None:
        """Light, dark, whatever the desktop is set to, or a custom theme; remembered like
        the zoom."""
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        theme.set_theme(app, name)
        if (chosen := self.theme_actions.get(theme.choice())) is not None:
            chosen.setChecked(True)

    def _fill_custom_themes(self) -> None:
        """The custom themes after the built-in ones, by name; a file's skipped parts in
        the item's tooltip."""
        for key in [k for k in self.theme_actions if k.startswith(theme.CUSTOM)]:
            action = self.theme_actions.pop(key)
            self._theme_group.removeAction(action)
            self._theme_menu.removeAction(action)
            action.deleteLater()
        custom = sorted(theme.custom().items(), key=lambda item: item[1].name.lower())
        self._custom_separator.setVisible(bool(custom))
        for slug, found in custom:
            key = theme.CUSTOM + slug
            action = QAction(found.name, self, checkable=True)
            action.setChecked(key == theme.choice())
            action.setToolTip("; ".join(found.problems))
            action.triggered.connect(lambda _=False, k=key: self.set_theme(k))
            self._theme_group.addAction(action)
            self._theme_menu.addAction(action)
            self.theme_actions[key] = action

    def _watch_themes(self) -> None:
        """The folder and each theme file read (theme.custom()), not yet watched."""
        folder = self._themes_dir
        if folder is None:
            return
        paths = [str(folder), *(str(folder / f"{slug}.json") for slug in theme.custom())]
        watched = set(self._themes_watch.directories() + self._themes_watch.files())
        if new := [p for p in paths if p not in watched]:
            self._themes_watch.addPaths(new)

    def _reload_themes(self) -> None:
        """A theme file was added, changed or removed: read them again, and restyle if
        the chosen one changed (or fall back to System if it is gone)."""
        folder = self._themes_dir
        assert folder is not None
        self._themes_read += 1
        read = self._themes_read

        def read_folder() -> dict[str, theme.CustomTheme]:
            folder.mkdir(parents=True, exist_ok=True)  # made again if it was deleted
            return theme.read_themes(folder)

        async def reload() -> None:
            themes = await asyncio.to_thread(read_folder)
            if read != self._themes_read:
                return  # a newer read is under way, or the window closed
            chosen = theme.choice().removeprefix(theme.CUSTOM)
            before = theme.custom().get(chosen)
            theme.set_custom(themes)
            self._watch_themes()  # a file saved by replacing it is a new one to watch
            self._fill_custom_themes()
            if theme.choice().startswith(theme.CUSTOM) and themes.get(chosen) != before:
                self.set_theme(theme.choice())  # changed, or gone: System
        self.home.command(reload())

    # ── startup ───────────────────────────────────────────────────────────

    async def begin(self, conversation_id: str | None = None) -> None:
        """When Kaden could not start: the welcome on first run, else the settings with
        the error. Otherwise open `conversation_id`, or resume the newest conversation."""
        settings = await self.runtime.settings()
        if needs_welcome(settings):
            self._dialog = WelcomeDialog(self, self.runtime, self.controller, settings)
            self._dialog.accepted.connect(lambda: self.controller.command(self.runtime.restart()))
            self._dialog.open()
            return
        if settings.startup_error is not None:
            self.open_settings()
            return
        await self.check_memory()
        if self.runtime.snapshot().conversation is None:
            if conversation_id is None:
                conversations = await self.home.conversations()
                conversation_id = conversations[0].id if conversations else None
            self.open_conversation(conversation_id)

    async def check_memory(self) -> None:
        """Hide Memory when the Memory module cannot show what it remembers."""
        self.memory_action.setVisible(await self.controller.notes() is not None)

    def open_conversation(self, conversation_id: str | None, *,
                          seed_from: str | None = None) -> None:
        self._leave_session()
        own = self.home.model.conversation
        if (own is not None and conversation_id == own.id and seed_from is None
                and not self._target.opening):  # one that is opening would take its place
            self._open()  # open here already
        elif reason := self.home.can_switch():
            self._open()
            self.model.notify(reason)
        else:
            target = self._open(opening=True, conversation=conversation_id)
            task = self.home.open_conversation(conversation_id, seed_from=seed_from)
            task.add_done_callback(lambda _: self._opened(target))

    def _open(self, *, session: SubSession | None = None, opening: bool = False,
              conversation: str | None = None) -> Target:
        """Say what the window shows, here and nowhere else: the sidebar follows."""
        self._target = Target(session, opening, conversation)
        self._render_sidebar()
        return self._target

    def _opened(self, target: Target) -> None:
        if self._target is target:
            self._open()  # opened or refused: the conversation open here shows
            self.input.setFocus()
            self._deliver_notes()

    # ── view model changes ────────────────────────────────────────────────

    def _home_changed(self, change: Change) -> None:
        """A change in the window's own conversation. What is the window's (the list of
        conversations, memory, whether another chat can start) follows it always; the
        conversation area only while it shows this conversation."""
        if isinstance(change, (Reset, PromptsChanged)):
            self._notify_new_prompts()
        match change:
            case Reset():
                self.home.command(self.fill_sidebar())
                self.home.command(self.refresh_models())
            case ConversationListChanged():
                self.home.command(self.fill_sidebar())
                return
            case MemoryListChanged():
                if self.memory_dialog is not None and self.memory_dialog.isVisible():
                    self.memory_dialog.refresh()
                return
        if self._viewing is None:
            self._pane_changed(change)
        elif isinstance(change, StatusChanged):
            self._refresh_status()  # the permissions shown, and whether a chat can start

    def _doing(self) -> str | None:
        """The step under way in the conversation on screen, in words."""
        step = next((item for item in reversed(self.model.items)
                     if isinstance(item, ToolItem)), None)
        return step.sentence if step is not None and step.status is None else None

    def _pane_changed(self, change: Change) -> None:
        """A change in the conversation the conversation area shows."""
        match change:
            case Reset():
                self.conversation.rebuild()
                self._refresh_status()
            case StatusChanged():
                self._refresh_status()
            case ConversationListChanged() | MemoryListChanged():
                pass  # a sub-session's own: the window lists the home's
            case _:
                self.conversation.changed(change)
                if self._life_on:  # its status line says what it is doing
                    self._show_duty()

    def _show_duty(self) -> None:
        """The note beside the title: the duty's next check-in, or what the life does."""
        model = self.model
        duty = (duty_status(model.conversation.duty, model.duty_wake_at, model.state,
                            model.help_needed) if model.conversation is not None else None)
        if self._life_on and model.busy and (doing := self._doing()):
            duty = doing  # its life: what it does now, not just that it is busy
        self.duty_label.setText(duty or "")
        self.duty_label.setVisible(duty is not None)

    def _show_title(self) -> None:
        conversation = self.model.conversation
        if conversation is not None and not self._life_on:
            self.title_label.setText(conversation_label(
                self._conversations.get(conversation.id, conversation)))

    def _refresh_status(self) -> None:
        model = self.model
        self._show_duty()
        known = () if self._models is None or isinstance(self._models, str) else self._models
        names = {m.id: m.name for m in known}
        self.model_button.setText(names.get(model.model or "", model.model or ""))
        policy = self.home.model.permissions
        self.permissions_button.setVisible(policy is not None)
        if policy is not None:
            self.permissions_button.setText("Plan mode" if model.plan_mode
                                            else PERMISSION_SHORT[policy])
            self.permissions_button.setToolTip(f"What the agent may do: "
                                               f"{PERMISSION_LABELS[policy].lower()}")
            for each, action in self.permission_actions.items():
                action.setChecked(each == policy)
        self.plan_action.setChecked(model.plan_mode)
        self.plan_action.setEnabled(model.conversation is not None)
        conversation = model.conversation
        if self.life is not None:
            self.life_name.setText(self.life.name)
        self._show_title()
        folder = conversation.folder if conversation is not None else None
        self.folder_action.setVisible(folder is not None)
        new_chat = (self._viewing is None and folder is not None and not model.items
                    and not model.has_earlier)
        needs_folder = self.controller.needs_folder
        self.title_label.setToolTip(f"Kaden works in {folder_path(folder)}"
                                    if folder is not None and not needs_folder else "")
        self.folder_button.setVisible(new_chat)
        if needs_folder:
            self.folder_button.setText("  Choose folder  ⌄")
            self.folder_button.setToolTip("")
        elif folder is not None:
            self.folder_button.setText(f"  {Path(folder).name or folder}  ⌄")
            self.folder_button.setToolTip(f"Kaden works in {folder_path(folder)}")
        self.send_button.setText("Stop" if model.busy else "Send")
        self.send_button.setToolTip("Stop (Esc)" if model.busy else "Send (Enter)")
        self.send_button.setAccessibleName("Stop" if model.busy else "Send message")
        self._paint_icons()
        self.conversation.refresh()
        reason = self.home.can_switch()  # another chat opens in the window's own runtime
        for widget in (self.new_button, self.new_action):
            widget.setEnabled(reason is None)
        self.switch_reason.setText(reason or "")
        self.switch_reason.setVisible(reason is not None)
        if (failure := model.failure) is not None:
            budget = self.controller.failure_is_budget
            self.banner_text.setText(BUDGET_MESSAGE if budget else failure.message)
            self.banner_settings.setVisible(budget)
        self.banner.setVisible(failure is not None)

    async def fill_sidebar(self) -> None:
        try:
            conversations = await self.home.conversations(
                include_archived=self.show_archived.isChecked())
        except Exception as e:  # noqa: BLE001 - shown, never fatal
            self.model.notify(user_message(e))
            return
        self._conversations = {info.id: info for info in conversations}
        self._render_sidebar()

    def _render_sidebar(self) -> None:
        """Conversations grouped by project (their folder), newest first, each with its
        sub-sessions under it. A folded group shows only its heading."""
        target, own = self._target, self.home.model.conversation
        viewing = target.session.id if target.session is not None else None
        current = target.conversation if target.opening else own.id if own is not None else None
        archived = self.show_archived.isChecked()
        groups: dict[str | None, list[ConversationInfo]] = {}
        for info in self._conversations.values():
            groups.setdefault(info.folder, []).append(info)
        headed = len(groups) > 1 or None not in groups
        self.conversation_list.clear()
        for folder, members in groups.items():
            key = folder or ""
            if headed:
                heading = QListWidgetItem(Path(folder).name if folder else "Other")
                heading.setData(KIND_ROLE, "group")
                heading.setData(Qt.ItemDataRole.UserRole, key)
                heading.setData(FLAGS_ROLE, {"folded"} if key in self._collapsed else set())
                heading.setToolTip(folder_path(folder) if folder else "")
                heading.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.conversation_list.addItem(heading)
                if key in self._collapsed:
                    continue
            for info in members:
                title = conversation_label(info)
                entry = QListWidgetItem(title + ("  (archived)" if info.archived else ""))
                entry.setData(KIND_ROLE, "conversation")
                entry.setData(Qt.ItemDataRole.UserRole, info.id)
                entry.setData(TITLE_ROLE, title)
                entry.setData(PREVIEW_ROLE, conversation_preview(info))
                entry.setData(DATE_ROLE, relative_date(info.updated_at))
                entry.setData(FLAGS_ROLE, {"archived"} if info.archived else set())
                entry.setToolTip(conversation_label(info, full=True))
                self.conversation_list.addItem(entry)
                if viewing is None and info.id == current:
                    self.conversation_list.setCurrentItem(entry)
                children = (self.sessions.of(info.id, include_archived=archived)
                            if self.sessions else ())
                for session in children:
                    row = QListWidgetItem(session.name)
                    row.setData(KIND_ROLE, "session")
                    row.setData(Qt.ItemDataRole.UserRole, session.id)
                    row.setData(TITLE_ROLE, session.name)
                    row.setData(PREVIEW_ROLE, session.summary)
                    row.setData(FLAGS_ROLE, {"working"} if session.running else
                                {"archived"} if session.archived else set())
                    row.setToolTip(session.name)
                    self.conversation_list.addItem(row)
                    if session.id == viewing:
                        self.conversation_list.setCurrentItem(row)
        self._show_title()  # its title, as the list has it now

    def conversation_info(self, conversation_id: str) -> ConversationInfo:
        return self._conversations[conversation_id]

    def _info_of(self, entry: QListWidgetItem) -> ConversationInfo | None:
        if entry.data(KIND_ROLE) not in (None, "conversation"):
            return None
        return self._conversations[entry.data(Qt.ItemDataRole.UserRole)]

    def _on_current(self, command: Callable[[ConversationInfo], None]) -> None:
        entry = self.conversation_list.currentItem()
        if entry is not None and (info := self._info_of(entry)) is not None:
            command(info)

    def _conversation_chosen(self, entry: QListWidgetItem) -> None:
        match entry.data(KIND_ROLE):
            case "group":
                key = entry.data(Qt.ItemDataRole.UserRole)
                self._collapsed.symmetric_difference_update({key})
                self._render_sidebar()
            case "session":
                session = self._session_of(entry.data(Qt.ItemDataRole.UserRole))
                if session is not None:
                    self.open_session(session)
            case _:
                self.open_conversation(entry.data(Qt.ItemDataRole.UserRole))

    def conversation_menu(self, info: ConversationInfo) -> QMenu:
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        menu.addAction("Rename…", lambda: self.rename_conversation(info))
        go_on = menu.addAction("Continue in a new conversation",
                               lambda: self.open_conversation(None, seed_from=info.id))
        go_on.setToolTip("Start a new conversation that knows where this one left off.")
        menu.addAction("Unarchive" if info.archived else "Archive",
                       lambda: self.home.archive(info.id, not info.archived))
        menu.addSeparator()
        menu.addAction("Delete…", lambda: self.delete_conversation(info)).setVisible(
            self.home.can_delete)
        return menu

    def rename_conversation(self, info: ConversationInfo) -> None:
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Rename conversation")
        dialog.setLabelText("New name (leave it empty to let Kaden name it):")
        dialog.setTextValue(info.title or "")
        dialog.textValueSelected.connect(lambda title: self.home.rename(info.id, title))
        dialog.open()

    def delete_conversation(self, info: ConversationInfo) -> None:
        children = len(self.sessions.of(info.id, include_archived=True)) if self.sessions else 0
        confirm(self, f"Delete “{conversation_label(info)}”?",
                lambda: self.home.delete(info.id), yes="Delete", danger=True,
                details=delete_details(self.home.is_open(info.id), children))

    def _add_to_app_menu(self) -> None:
        def added(_: None) -> None:
            self.app_menu_action.setVisible(False)
            self.model.notify("Kaden is now in your app menu.")

        self.controller.command(
            asyncio.to_thread(app_menu.install), done=added,
            failed=lambda e: self.model.notify(f"Kaden could not be added to the app menu: {e}"))

    def open_memory(self) -> None:
        self.open_settings("Memory")

    def _notice_link(self, link: str) -> None:
        if link == "memory":
            self.open_memory()

    def bring_forward(self) -> None:
        """Another launch of Kaden, or its tray icon, asked for this window: shown as it
        was before it was hidden or minimized."""
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def quit(self) -> None:
        """Close the window and end Kaden, tray or not."""
        self._quitting = True
        self.close()

    def _sessions_changed(self) -> None:
        self._refresh_status()
        self._render_sidebar()
        self._deliver_notes()

    def _deliver_notes(self) -> None:
        """Tell this window's conversation what its sub-sessions did while nobody waited:
        a notice as a message, which wakes the agent (queued while it works)."""
        own = self.home.model.conversation
        if self.sessions is not None and own is not None:
            for note in self.sessions.take_notes(own.id):
                self.home.notify(note)

    def _session_of(self, session_id: str) -> SubSession | None:
        return next((s for s in (self.sessions.sessions if self.sessions else ())
                     if s.id == session_id), None)

    def _row_menu(self, entry: QListWidgetItem, at: QPoint) -> None:
        if entry.data(KIND_ROLE) == "session":
            if (session := self._session_of(entry.data(Qt.ItemDataRole.UserRole))) is not None:
                self.session_menu(session).popup(at)
        elif (info := self._info_of(entry)) is not None:
            self.conversation_menu(info).popup(at)

    def session_menu(self, session: SubSession) -> QMenu:
        assert self.sessions is not None
        sessions = self.sessions
        menu = QMenu(self)
        menu.addAction("Open", lambda: self.open_session(session))
        if session.resumable:
            menu.addAction("Resume", lambda: self.home.command(sessions.resume(session)))
        if session.running:
            menu.addAction("Stop", lambda: sessions.stop(session))
        menu.addAction("Change spending limit…", lambda: self.change_session_limit(session))
        menu.addAction("Rename…", lambda: self.rename_session(session))
        menu.addAction("Unarchive" if session.archived else "Archive",
                       lambda: sessions.archive(session, not session.archived))
        menu.addAction("Delete…", lambda: self.delete_session(session))
        return menu

    def change_session_limit(self, session: SubSession) -> None:
        assert self.sessions is not None
        sessions = self.sessions
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Sub-session spending limit")
        dialog.setLabelText(f"Most “{clip(session.name, 60)}” may spend, in US dollars; 0 "
                            f"for no limit (${session.spent:.2f} spent so far):")
        dialog.setInputMode(QInputDialog.InputMode.DoubleInput)
        dialog.setDoubleDecimals(2)
        dialog.setDoubleRange(0, 1_000_000)
        dialog.setDoubleValue(session.budget_usd or 0)

        def chosen(value: float) -> None:
            usd = value or None

            def apply() -> None:
                self.home.command(sessions.set_child_limit(session, usd))

            if raises_limit(session.budget_usd, usd):
                confirm(self, "Raise this sub-session's limit?", apply, yes="Raise",
                        details=("It may then spend without a limit of its own." if usd is None
                                 else f"It may then spend up to ${usd:g}."))
            else:
                apply()

        dialog.doubleValueSelected.connect(chosen)
        dialog.open()

    def rename_session(self, session: SubSession) -> None:
        assert self.sessions is not None
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Rename sub-session")
        dialog.setLabelText("New name (leave it empty to show its task):")
        dialog.setTextValue(session.title or "")
        dialog.textValueSelected.connect(lambda title: self.sessions.rename(session, title))
        dialog.open()

    def delete_session(self, session: SubSession) -> None:
        assert self.sessions is not None
        sessions = self.sessions

        def delete() -> None:
            if self._target.session is session:
                self._leave_session()
                self._open()
            self.home.command(sessions.delete(session))

        confirm(self, f"Delete the sub-session “{clip(session.name, 80)}”?", delete,
                yes="Delete", danger=True,
                details="It stops, and its conversation is erased. This cannot be undone.")

    def open_session(self, session: SubSession) -> None:
        """Show a sub-session in the conversation area, as its own conversation: its
        messages, steps and prompts, and the message box talks to it. Its row stays
        highlighted while it opens, which may mean starting it again."""
        if session is not self._target.session:
            self.home.command(self._open_session(self._open(session=session)))

    async def _open_session(self, target: Target) -> None:
        session = target.session
        assert self.sessions is not None and session is not None
        try:
            remote = await self.sessions.open(session)
        except Exception as e:  # noqa: BLE001 - shown, never fatal
            if self._target is target:  # what is on screen stays
                self._open(session=self._viewing.session if self._viewing else None)
            self.model.notify(f"The sub-session could not be opened: {user_message(e)}")
            return
        if self._target is not target:  # the user asked for something else meanwhile
            self.sessions.release(session, remote)
            return
        self._leave_session()
        controller = Controller(remote, ConversationModel())
        self._show(Viewing(session, remote, controller, asyncio.create_task(controller.run())))

    def _leave_session(self) -> None:
        """Back to this window's own conversation; the sub-session keeps working."""
        if (viewing := self._viewing) is not None:
            self._show(None)
            viewing.pump.cancel()
            if viewing.session is not None:  # the life goes on as it is
                assert self.sessions is not None
                self.sessions.release(viewing.session, viewing.remote)

    def _show(self, viewing: Viewing | None) -> None:
        """Put a sub-session on screen, or (None) the window's own conversation."""
        if self._viewing is not None:
            self._viewing.controller.model.on_change = lambda change: None
        self._viewing = viewing
        if viewing is not None:
            viewing.controller.model.on_change = self._pane_changed
        self.voice.use(self.controller)  # voice talks with what is on screen
        self.conversation.rebuild()
        self._refresh_status()

    def show_life(self, on: bool) -> None:
        """The whole window is the life's (its life, and no chat list), or back to
        the user's chats; it lives on either way. It starts living the first time."""
        if on == self._life_on:
            return
        if on and self.life is not None and not self.life.has_lived:
            self._name_life()  # its name and who it is, first
            return
        self._life_frame(on, waking=on)  # at once, before its process answers
        self.home.command(self._show_life(on))

    def _life_frame(self, on: bool, *, waking: bool = False) -> None:
        """The window around the life's conversation (no sidebar; its ☰ and who it is
        in the title bar), or the chats'. While it wakes, a line
        stands where its conversation will be."""
        self._life_on = on
        self.conversation.set_per_conversation(not on)
        self.sidebar.setVisible(not on)
        self.title_label.setVisible(not on)
        for widget in (self.life_name, self.view_menu_button,
                       self.files_button, self.view_gear_button, self._gear_balance):
            widget.setVisible(on)
        self._gear_balance.setFixedWidth(self.view_gear_button.sizeHint().width())
        self.waking.setVisible(waking)
        self.conversation.setVisible(not waking)
        self.composer.setVisible(not waking)
        self._refresh_status()
        self._show_voice()  # the message box's hint names whom it talks to

    def _name_life(self) -> None:
        """Before it first lives: its name and who it is. Later they are in its settings."""
        life = self.life
        assert life is not None
        dialog = self.life_dialog = LifeDialog(self, life.name, life.about)

        def chosen() -> None:
            async def apply() -> None:
                await life.set_identity(dialog.name.text(), dialog.about.toPlainText())
                self._life_frame(True, waking=True)
                await self._show_life(True)
            self.home.command(apply())

        dialog.accepted.connect(chosen)
        # not chosen: no life
        dialog.rejected.connect(lambda: self.continuous_switch.setChecked(False))
        dialog.open()

    async def _show_life(self, on: bool) -> None:
        life = self.life
        assert life is not None
        if not on:
            self._leave_session()
            self._open()
            self._life_frame(False)
            return
        try:
            if not life.running:
                await life.start()
        except Exception as e:  # noqa: BLE001 - shown, never fatal
            self._life_frame(False)
            self.continuous_switch.setChecked(False)
            self.model.notify(f"Continuous could not start: {user_message(e)}")
            return
        if not self._life_on:  # the user went back meanwhile
            return
        self._leave_session()
        self._open()
        controller = Controller(life.runtime, ConversationModel())
        self._show(Viewing(None, life.runtime, controller,
                           asyncio.create_task(controller.run())))
        self._life_frame(True)

    def open_files(self) -> None:
        """Its folder, in the user's own file manager; made first, since it is only made
        as the life first starts."""
        assert self.life is not None
        folder = self.life.home

        async def open_folder() -> None:
            await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
                QMessageBox.warning(self, "Kaden", f"Could not open their folder, {folder}.")
        self.home.command(open_folder())

    def _notify_new_prompts(self) -> None:
        prompts = self.home.model.prompts
        new = [p for key, p in prompts.items() if key not in self._prompts_seen]
        self._prompts_seen = set(prompts)
        for prompt in new:
            if prompt.kind != PromptKind.PROPOSAL:
                self._notify("Kaden", prompt, life=False)

    def _notify(self, who: str, prompt: Prompt, *, life: bool) -> None:
        """A desktop notification that `who` waits on the user, when the window is not in
        front: otherwise nothing would tell them, and the work waits."""
        if self.tray is None or (self.isActiveWindow() and not self.isMinimized()):
            return
        self._notified_life = life
        self.tray.showMessage(f"{who} needs your answer", prompt_summary(prompt),
                              QIcon(str(app_menu.ICON)), 10_000)

    def notification_clicked(self) -> None:
        """The window in front, on the conversation the question is in."""
        self.bring_forward()
        if self._notified_life and not self.showing_life:
            self.continuous_switch.setChecked(True)

    def _life_said(self) -> None:
        """What the life said by itself: in its own view it is in the conversation;
        elsewhere a notice in the chat on screen. What it asks the user is a notification
        when the window is not in front."""
        if self.life is None:
            return
        for prompt in self.life.take_questions():
            self._notify(self.life.name, prompt, life=True)
        if self.showing_life:
            return
        for text in self.life.take_messages():
            self.model.notify(f"{self.life.name}: {text}")

    def open_duty(self) -> None:
        if self.model.conversation is None:
            self.model.notify("Open a conversation first.")
            return
        if self.controller.needs_folder:
            self._offer_folders()
            return
        DutyDialog(self, self.controller, self.model.conversation).open()

    def open_tool_servers(self) -> None:
        self.open_settings("Tool servers")

    # ── commands ──────────────────────────────────────────────────────────

    def _send(self) -> None:
        text, files = self.input.toPlainText().strip(), self.attachments.paths()
        if (text or files) and self.controller.needs_folder:
            self._offer_folders()  # the message stays in the box
            return
        if text or files:
            self.input.clear()
            self.attachments.clear()
            self.controller.send(text, files, spoken=self.voice.spoken)

    def _paste_image(self, image: QImage) -> None:
        """A picture pasted from the clipboard: saved as a file, then attached like one."""
        def save() -> str | None:  # off the event loop: encoding a screenshot takes a while
            if self._pasted is None:
                self._pasted = Path(tempfile.mkdtemp(prefix="kaden-pasted-"))
            path = self._pasted / f"pasted-{len(list(self._pasted.iterdir())) + 1}.png"
            return str(path) if image.save(str(path), "PNG") else None

        async def attach() -> None:
            async with self._pasting:
                path = await asyncio.to_thread(save)
            if path is not None:
                self.attachments.add([path])

        self.home.command(attach())

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if local_files(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        if files := local_files(event.mimeData()):
            self.attachments.add(files)
            self.input.setFocus()
            event.acceptProposedAction()

    def _stop(self) -> None:
        """Esc: while a reply is read aloud it stops the reading, otherwise the agent."""
        if not self.voice.interrupt():
            self.controller.stop()

    def _show_voice(self) -> None:
        """The microphone and speaker buttons, and what voice is doing, where the message
        is typed."""
        theme.mark(self.microphone_button, "active", self.voice.listening)
        self.microphone_button.setToolTip(
            "Stop listening" if self.voice.listening else f"Talk to {self._whom()}")
        theme.mark(self.speaker_button, "active", self.voice.speaking)
        self.speaker_button.setToolTip(
            "Stop reading aloud" if self.voice.speaking else "Read replies aloud")
        fetching = self.voice.progress is not None
        self.voice_progress.setVisible(fetching)
        if fetching:
            self.voice_progress_label.setText(f"{self.voice.status}. Turn voice off to "
                                              "cancel.")
            self.voice_progress_bar.setValue(round((self.voice.progress or 0.0) * 1000))
        self.input.setPlaceholderText(
            self._hint() if fetching or not self.voice.status else self.voice.status)
        self._paint_icons()

    def _whom(self) -> str:
        """Whom the message box talks to."""
        if self._life_on and self.life is not None:
            return self.life.name
        return "Kaden"

    def _hint(self) -> str:
        return f"Message {self._whom()}"

    def _ask(self, text: str, details: str) -> asyncio.Future[bool]:
        """A question to the user; the future says whether they agreed."""
        answer: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        box = QMessageBox(QMessageBox.Icon.NoIcon, "Kaden", text, parent=self)
        box.setInformativeText(details)
        box.setDefaultButton(box.addButton(QMessageBox.StandardButton.Cancel))
        agree = box.addButton("Download", QMessageBox.ButtonRole.AcceptRole)
        box.finished.connect(
            lambda _: answer.done() or answer.set_result(box.clickedButton() is agree))
        box.open()
        return answer

    def _send_or_stop(self) -> None:
        if self.model.busy:
            self.controller.stop()
        else:
            self._send()

    async def refresh_models(self) -> None:
        try:
            self._models = await self.runtime.list_models()
        except Exception as e:  # noqa: BLE001 - e.g. no key yet
            self._models = user_message(e)
        self._fill_model_menu()
        self._refresh_status()  # the model's name, now that it is known

    def _fill_model_menu(self) -> None:
        """From the models already loaded: a menu that is empty when clicked does not
        open, so it is never filled while it opens."""
        menu = self.model_menu
        menu.clear()
        if self._models is None or isinstance(self._models, str):
            menu.addAction(self._models or "Loading models…").setEnabled(False)
            return
        for info in self._models:
            action = menu.addAction(model_label(info))
            action.setData(info.id)
            action.setCheckable(True)
            action.setChecked(info.id == self.model.model)
            action.triggered.connect(lambda _=False, m=info.id: self.controller.select_model(m))

    def open_settings(self, page: str | None = None) -> None:
        """The Settings window, at `page` (a section's name) or its first; in the life's
        view, the life's own settings."""
        if self.showing_life:
            self._open_life_settings()
            return

        async def load() -> None:
            settings = await self.runtime.settings()
            try:
                models = await self.runtime.list_models()
            except Exception:  # noqa: BLE001 - e.g. no key yet; the provider can still change
                models = []
            spent = self.runtime.snapshot().usage.usage.cost_usd or 0.0
            dialog = SettingsDialog(self, self.runtime, self.home, settings, models, spent,
                                    self.sessions, voice=self.voice)
            self._dialog = dialog
            self.memory_dialog, self.mcp_dialog = dialog.memory, dialog.servers
            if page is not None:
                dialog.show_page(page)
            dialog.open()

        self.controller.command(load())

    def _open_life_settings(self) -> None:
        life = self.life
        assert life is not None

        async def restart() -> None:
            self._life_frame(True, waking=True)
            try:
                await life.restart()
            except Exception as e:  # noqa: BLE001 - shown, never fatal
                self.model.notify(f"{life.name} could not restart: {user_message(e)}")
                return
            await self._show_life(True)

        async def saved(identity: tuple[str, str] | None, daily: Any,
                        changes: dict[str, bool | str],
                        rules: tuple[PermissionRule, ...] | None) -> None:
            if rules is not None:
                await life.runtime.update_settings(SettingsUpdate(permission_rules=rules))
            if identity is not None:
                await life.set_identity(*identity)
                self._refresh_status()
                self._show_voice()  # the message box names them
            if daily is not UNCHANGED:
                await life.set_daily_usd(daily)
            if changes:
                await life.runtime.update_settings(SettingsUpdate(tool_settings=changes))
                self.home.command(restart())

        async def load() -> None:
            dialog = LifeSettingsDialog(self, self.home, life.name, life.about,
                                        await life.runtime.settings(), life.daily_usd)
            dialog.saved = saved
            self._dialog = dialog
            dialog.open()

        self.controller.command(load())

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.tray is not None and not self._quitting:  # Kaden runs on in the tray
            event.ignore()
            self.hide()
            if not self.told_of_tray:
                self.tray.showMessage("Kaden", "Still running. Quit from this icon.",
                                      QIcon(str(app_menu.ICON)), 4000)
                self.told_of_tray = True
            return
        if self.tray is not None:
            self.tray.hide()
        self.voice.stop()
        self._themes_watch.blockSignals(True)  # nothing reads the themes for it any more
        self._themes_due.stop()
        self._themes_read += 1  # a read under way is dropped
        if self._pasted is not None:
            shutil.rmtree(self._pasted, ignore_errors=True)
        self.closed.emit()
        super().closeEvent(event)
