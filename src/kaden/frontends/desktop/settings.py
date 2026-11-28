"""The settings dialogs: the app's, Continuous's, and the tool servers'.

Dialogs use open() and signals, never exec(): exec() would run a nested event loop inside
the qasync loop that also runs the runtime.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from kaden.api import (
    McpServer,
    ModelInfo,
    PermissionPolicy,
    PermissionRule,
    RuntimeAPI,
    Settings,
    SettingsUpdate,
    ToolSetting,
)
from kaden.frontends.common import (
    AT_RESTART,
    MCP_VARIABLES_HINT,
    MCP_WARNING,
    MODULE_LABELS,
    PERMISSION_LABELS,
    UNCHANGED,
    dollars,
    loosening,
    mcp_server,
    mcp_target,
    mcp_variables,
    model_label,
    module_options,
    needs_restart,
    raises_limit,
    restart_notice,
)
from kaden.frontends.desktop import theme
from kaden.frontends.desktop.dialogs import MemoryDialog, SavedAnswers, confirm
from kaden.frontends.desktop.frame import DragArea, RoundedCorners
from kaden.frontends.desktop.sidebar import RowDelegate
from kaden.frontends.desktop.viewmodel import (
    Controller,
    SettingsForm,
    settings_update,
    tool_changes,
    user_message,
)
from kaden.frontends.desktop.voice.mode import KOKORO_VOICES, VoiceMode
from kaden.frontends.desktop.voice.natural import RECORDING_TYPES
from kaden.frontends.desktop.widgets import (
    PREVIEW_ROLE,
    Switch,
    error_label,
    primary,
    say,
    text_label,
)
from kaden.frontends.life import NAME
from kaden.frontends.sessions import SubSessions

MODULE_HINTS = {"tools": "What Kaden can do on this computer."}


def _setting(title: str, description: str, control: QWidget | QHBoxLayout, *,
             below: bool = False) -> QWidget:
    """One row of Settings: its name and what it does on the left, the control on the
    right (or under them, for a big one)."""
    row = QFrame(objectName="settingRow")
    layout = QVBoxLayout(row) if below else QHBoxLayout(row)
    layout.setContentsMargins(0, 12, 0, 12)
    layout.setSpacing(16 if not below else 8)
    text = QVBoxLayout()
    text.setSpacing(2)
    text.addWidget(text_label(title, selectable=False))
    text.addWidget(text_label(description, selectable=False, role="small"))
    layout.addLayout(text, 1)
    if isinstance(control, QHBoxLayout):
        holder = QWidget()
        holder.setLayout(control)
        control = holder
    if not below:
        control.setFixedWidth(300)
    layout.addWidget(control)
    return row


def _page(title: str, *rows: QWidget) -> QScrollArea:
    page = QWidget(objectName="settingsPage")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(4, 16, 18, 4)
    layout.setSpacing(0)
    layout.addWidget(text_label(title, selectable=False, role="title"))
    layout.addSpacing(6)
    for row in rows:
        layout.addWidget(row)
    layout.addStretch()
    scroll = QScrollArea(widgetResizable=True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(page)
    return scroll


class ToolSettingRows:
    """The rows of the settings a Tools module declares: a switch, a field or options."""

    def __init__(self, settings: Sequence[ToolSetting]) -> None:
        self.controls: dict[str, QWidget] = {}
        self.rows: list[QWidget] = []
        for setting in settings:
            value = setting.default if setting.value is None else setting.value
            control: QWidget
            if setting.type == "boolean":
                control = Switch()
                control.setChecked(bool(value))
            elif setting.type == "choice":
                control = QComboBox()
                for choice, label in setting.choices.items():
                    control.addItem(label, choice)
                control.setCurrentIndex(max(0, control.findData(value)))
            else:
                control = QLineEdit(str(value))
            self.controls[setting.name] = control
            self.rows.append(_setting(setting.title, setting.description, control))

    def values(self) -> dict[str, bool | str]:
        values: dict[str, bool | str] = {}
        for name, control in self.controls.items():
            if isinstance(control, QCheckBox):
                values[name] = control.isChecked()
            elif isinstance(control, QComboBox):
                values[name] = control.currentData()
            elif isinstance(control, QLineEdit):
                values[name] = control.text()
        return values


class LifeSettingsDialog(QDialog):
    """The life's own settings: its name and who it is, and what its own time may cost a
    day (at once); its model and what its tools declare (when its process starts again).
    Its provider follows the user's chats."""

    def __init__(self, parent: QWidget, controller: Controller, name: str, about: str,
                 settings: Settings, daily_usd: float | None, *,
                 models: Sequence[ModelInfo] = (), model: str | None = None) -> None:
        super().__init__(parent)
        self._controller = controller  # runs the save
        self.settings, self.daily_usd, self.chosen_model = settings, daily_usd, model
        self.identity = (name, about)
        self.setWindowTitle(f"Settings · {name}")
        self.resize(780, 600)  # pages scroll: it keeps this size however much they hold
        self.name = QLineEdit(name)
        self.name.setPlaceholderText(NAME)
        self.about = QPlainTextEdit(about)
        self.about.setPlaceholderText("Personality, interests, how they talk… or leave it to them")
        self.about.setMaximumHeight(96)
        self.daily = QLineEdit("" if daily_usd is None else f"{daily_usd:g}")
        self.daily.setPlaceholderText("No limit")
        self.model = QComboBox()
        self.model.addItem("Same as your chats", None)
        for info in models:
            self.model.addItem(info.name or info.id, info.id)
        if model is not None and self.model.findData(model) < 0:
            self.model.addItem(model, model)  # one the provider no longer lists
        self.model.setCurrentIndex(max(0, self.model.findData(model)))
        self.tool_rows = ToolSettingRows(settings.tool_settings)
        self.answers = SavedAnswers(settings.permission_rules)
        pages: dict[str, QWidget] = {"General": _page(
            "General",
            _setting("Name", "What they are called.", self.name),
            _setting("Who they are", "In your words. Applies at once.", self.about,
                     below=True),
            _setting("Model", "Runs all they do: replies, their own time, upkeep. A change "
                     "restarts them.", self.model),
            _setting("Daily amount (US$)", "What their own time may cost a day. Your "
                     "messages always reach them.", self.daily))}
        if self.tool_rows.rows:
            pages["Tools"] = _page("Tools", *self.tool_rows.rows, text_label(
                f"A change to their tools restarts {name}.", selectable=False, role="small"))
        pages["Saved answers"] = _page("Saved answers", _setting(
            "Saved answers", "What you said always or never to in their view. Theirs, apart "
            "from your chats'.", self.answers, below=True))
        self.nav = QListWidget(objectName="settingsNav")
        self.nav.setFixedWidth(170)
        self.stack = QStackedWidget()
        for title, page in pages.items():
            item = QListWidgetItem(title)
            item.setSizeHint(QSize(0, 34))
            self.nav.addItem(item)
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self.error = error_label()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = primary(buttons.button(QDialogButtonBox.StandardButton.Save))
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        body = QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        footer = QHBoxLayout()
        footer.addWidget(self.error, 1)
        footer.addWidget(buttons)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.addLayout(body, 1)
        outer.addLayout(footer)
        # Called with the new name and who they are (None if unchanged), the new daily
        # amount (UNCHANGED if it is not), the tool settings changed, the saved answers
        # kept (None if unchanged), and the model (UNCHANGED if it is not).
        self.saved: Callable[[tuple[str, str] | None, Any, dict[str, bool | str],
                              tuple[PermissionRule, ...] | None, Any],
                             Awaitable[None]] | None = None

    def _save(self) -> None:
        changes = tool_changes(self.settings, self.tool_rows.values())
        text = self.daily.text().strip().lstrip("$")
        try:
            daily = float(text) if text else None
            if daily is not None and daily < 0:
                raise ValueError
        except ValueError:
            self.nav.setCurrentRow(0)  # where the amount is
            say(self.error, "Write the daily amount as dollars, like 1 or 2.50, or leave "
                             "it empty for no limit.")
            return
        daily = UNCHANGED if daily == self.daily_usd else daily
        identity: tuple[str, str] | None = (self.name.text().strip() or NAME,
                                            self.about.toPlainText().strip())
        if identity == self.identity:
            identity = None
        rules: tuple[PermissionRule, ...] | None = self.answers.kept()
        if rules == tuple(self.settings.permission_rules):
            rules = None
        model = self.model.currentData()
        model = UNCHANGED if model == self.chosen_model else model
        if (not changes and daily is UNCHANGED and identity is None and rules is None
                and model is UNCHANGED or self.saved is None):
            self.accept()
            return
        self.save_button.setEnabled(False)
        self.error.hide()

        async def save() -> None:
            assert self.saved is not None
            try:
                await self.saved(identity, daily, changes, rules, model)
            except Exception as e:  # noqa: BLE001 - shown in the dialog
                self.save_button.setEnabled(True)
                say(self.error, user_message(e))
                return
            self.accept()
        self._controller.command(save())


class McpDialog(QDialog):
    """The MCP servers whose tools Kaden offers. Changes are saved at once and start with
    the next start, which Restart now brings."""

    def __init__(self, parent: QWidget, runtime: RuntimeAPI, controller: Controller, *,
                 embedded: bool = False) -> None:
        super().__init__(parent)
        self.runtime, self.controller = runtime, controller
        if embedded:  # a page of Settings
            self.setWindowFlags(Qt.WindowType.Widget)
        self.saved: list[McpServer] = []
        self._changes = 0  # saves made here; a list read before the latest one is stale
        self.setWindowTitle("Tool servers")
        self.setMinimumSize(520, 400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*((4, 16, 18, 4) if embedded else (20, 18, 20, 16)))
        layout.addWidget(text_label("Tool servers (MCP)", selectable=False,
                                role="title" if embedded else "heading"))
        self.servers = QListWidget()
        self.servers.setAccessibleName("Tool servers")
        self.servers.setItemDelegate(RowDelegate(self.servers))
        self.servers.currentItemChanged.connect(lambda *_: self._selection_changed())
        self.servers.itemActivated.connect(lambda _: self._edit())
        self.empty = text_label("No servers yet.", selectable=False, tone="muted")
        self.empty.hide()  # until the list has loaded
        layout.addWidget(self.servers, 1)
        layout.addWidget(self.empty)
        self.notice = text_label("", selectable=False, tone="warning")
        self.notice.hide()
        layout.addWidget(self.notice)
        self.add_button = primary(QPushButton("Add…"))
        self.add_button.clicked.connect(self._add)
        self.edit_button = QPushButton("Edit…")
        self.edit_button.clicked.connect(self._edit)
        self.remove_button = QPushButton("Remove…")
        self.remove_button.clicked.connect(self._remove)
        self.restart_button = QPushButton("Restart now")
        self.restart_button.clicked.connect(
            lambda: (self.accept(), self.controller.command(self.runtime.restart())))
        self.restart_button.hide()
        row = QHBoxLayout()
        for button in (self.add_button, self.edit_button, self.remove_button):
            row.addWidget(button)
        row.addStretch()
        row.addWidget(self.restart_button)
        if not embedded:
            close = QPushButton("Close")
            close.clicked.connect(self.accept)
            row.addWidget(close)
        layout.addLayout(row)
        self._selection_changed()

    def refresh(self) -> None:
        changes = self._changes
        self.controller.command(self.runtime.settings(),
                                done=lambda s: changes == self._changes and self._show(s))

    def _show(self, settings: Settings) -> None:
        self.saved = list(settings.mcp_servers)
        self.servers.clear()
        for server in self.saved:
            item = QListWidgetItem(server.name)
            item.setData(Qt.ItemDataRole.UserRole, server)
            item.setData(PREVIEW_ROLE, mcp_target(server))
            self.servers.addItem(item)
        self.empty.setVisible(not self.saved)
        notice = restart_notice(settings) if settings.restart_needed else None
        self.notice.setText(notice or "")
        self.notice.setVisible(notice is not None)
        self.restart_button.setVisible(settings.restart_needed)
        self._selection_changed()

    def _selected(self) -> McpServer | None:
        item = self.servers.currentItem()
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)

    def _selection_changed(self) -> None:
        for button in (self.edit_button, self.remove_button):
            button.setEnabled(self._selected() is not None)

    async def save(self, servers: list[McpServer]) -> Settings:
        self._changes += 1
        return await self.runtime.update_settings(SettingsUpdate(mcp_servers=servers))

    def _add(self) -> None:
        ServerDialog(self, None).open()

    def _edit(self) -> None:
        if (server := self._selected()) is not None:
            ServerDialog(self, server).open()

    def _remove(self) -> None:
        if (server := self._selected()) is not None:
            rest = [s for s in self.saved if s.name != server.name]
            confirm(self, f"Remove the tool server “{server.name}”?",
                    lambda: self.controller.command(self.save(rest), done=self._show),
                    yes="Remove", danger=True,
                    details="Its tools are gone after the next restart, and the keys saved "
                            "for it are deleted.")


class ServerDialog(QDialog):
    """Add or edit one server; saved through the runtime, which checks it."""

    def __init__(self, parent: McpDialog, server: McpServer | None) -> None:
        super().__init__(parent)
        self.list, self.original = parent, server
        self.setWindowTitle("Edit tool server" if server else "Add tool server")
        self.setMinimumWidth(520)
        form = QFormLayout(self)
        form.setContentsMargins(20, 18, 20, 16)
        self.name = QLineEdit(server.name if server else "")
        self.name.setPlaceholderText("github")
        self.target = QLineEdit(mcp_target(server) if server else "")
        self.target.setPlaceholderText("npx -y @modelcontextprotocol/server-github, or a URL")
        self.variables = QPlainTextEdit(mcp_variables(server) if server else "")
        self.variables.setPlaceholderText("GITHUB_TOKEN=…")
        self.variables.setMaximumHeight(90)
        self.warning = text_label(MCP_WARNING, tone="warning")
        self.error = error_label()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        self.save_button = primary(buttons.button(QDialogButtonBox.StandardButton.Save))
        form.addRow("Name", self.name)
        form.addRow("Command or URL", self.target)
        form.addRow("Variables", self.variables)
        form.addRow("", text_label(MCP_VARIABLES_HINT, selectable=False, role="small"))
        form.addRow(self.warning)
        form.addRow(self.error)
        form.addRow(buttons)

    def _save(self) -> None:
        try:
            server = mcp_server(self.name.text(), self.target.text(),
                                self.variables.toPlainText())
        except ValueError as e:
            say(self.error, str(e))
            return
        servers = [server if self.original and s.name == self.original.name else s
                   for s in self.list.saved]
        if self.original is None:
            servers.append(server)
        self.save_button.setEnabled(False)

        def saved(settings: Settings) -> None:
            self.accept()
            self.list._show(settings)

        def failed(error: Exception) -> None:
            self.save_button.setEnabled(True)
            say(self.error, user_message(error))

        self.list.controller.command(self.list.save(servers), done=saved, failed=failed)


class SettingsDialog(QDialog):
    """Sends only what changed; loosening and a different Memory are confirmed first.
    Module settings wait for a restart, which Restart now starts."""

    def __init__(self, parent: QWidget, runtime: RuntimeAPI, controller: Controller,
                 settings: Settings, models: Sequence[ModelInfo], spent: float,
                 sessions: SubSessions | None = None, *, voice: VoiceMode | None = None
                 ) -> None:
        super().__init__(parent)
        self.runtime, self.controller, self.settings = runtime, controller, settings
        self.sessions = sessions
        self.setWindowTitle("Settings")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setObjectName("settings")
        self.resize(900, 640)

        self.startup_error = text_label(f"Kaden could not start: {settings.startup_error}")
        theme.mark(self.startup_error, "tone", "error")
        self.startup_error.setVisible(settings.startup_error is not None)
        self.modules = {kind: self._module_box(kind) for kind in MODULE_LABELS
                        if kind in settings.modules}
        self.modules["core"].currentIndexChanged.connect(self._core_changed)
        self.api_key = QLineEdit(echoMode=QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("Leave empty to keep the current key")
        self.model = QComboBox()
        self.light = QComboBox()
        self.light.addItem("Same as the main model", None)
        for info in models:
            self.model.addItem(model_label(info), info.id)
            self.light.addItem(model_label(info), info.id)
        if self.model.findData(settings.model) < 0:
            self.model.insertItem(0, settings.model or "Provider's default", settings.model)
        self.model.setCurrentIndex(self.model.findData(settings.model))
        self.light.setCurrentIndex(max(0, self.light.findData(settings.maintenance_model)))
        self.has_models = bool(models)
        for box in (self.model, self.light):
            box.setEnabled(self.has_models)
        self.model_note = text_label("After the restart, the new provider starts with its first "
                                 "model.", selectable=False, role="small")
        self.model_note.hide()
        self.limit = QLineEdit("" if settings.budget.max_cost_usd is None
                               else f"{settings.budget.max_cost_usd:g}")
        self.limit.setPlaceholderText("No limit")
        self.child_limit = QLineEdit(f"{sessions.limit_usd:g}" if sessions is not None
                                     and sessions.limit_usd is not None else "")
        self.child_limit.setPlaceholderText("No limit")
        self.child_limit.setToolTip("What a sub-session may spend unless the agent gives it "
                                    "less. It is not part of your own limit.")
        self.permissions = QComboBox()
        for policy, label in PERMISSION_LABELS.items():
            self.permissions.addItem(label, policy)
        self.permissions.setCurrentIndex(self.permissions.findData(settings.permissions))
        self.instructions = QPlainTextEdit(settings.system_prompt)
        self.instructions.setPlaceholderText(
            "Your own instructions, added to Kaden's built-in guidance: who it is to you, "
            "how it should talk, what to keep in mind")
        self.instructions.setMinimumHeight(120)
        self.notice = text_label(restart_notice(settings) or "", selectable=False, tone="warning")
        self.notice.setVisible(restart_notice(settings) is not None)
        self.error = error_label()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        self.save_button = primary(buttons.button(QDialogButtonBox.StandardButton.Save))
        self.restart_button = buttons.addButton("Restart now",
                                                QDialogButtonBox.ButtonRole.ActionRole)
        self.restart_button.clicked.connect(lambda: self._save(restart=True))
        self.restart_button.setVisible(settings.restart_needed
                                       or settings.startup_error is not None)

        self.memory = MemoryDialog(self, controller, embedded=True)
        self.servers = McpDialog(self, runtime, controller, embedded=True)
        # How Kaden sounds when you talk with it: chosen at once, as Claude's voice is.
        self.voice = QComboBox()
        self.voice.addItems(voice.voices() if voice is not None else list(KOKORO_VOICES))
        self.play_voice = QPushButton("Play")
        self.play_voice.setEnabled(voice is not None)
        # The natural voice speaks in any voice it hears in a recording.
        self.add_voice = QPushButton("Add…")
        self.add_voice.setToolTip("Add a voice from a recording")
        self.add_voice.setVisible(voice is not None and voice.natural)
        self.remove_voice = QToolButton()
        self.remove_voice.setIcon(theme.icon("close", theme.current().muted, 14))
        self.remove_voice.setToolTip("Remove this voice")
        self.remove_voice.setAccessibleName("Remove this voice")
        self.remove_voice.setVisible(self.add_voice.isVisibleTo(self))
        self.recording_dialog: QFileDialog | None = None
        if voice is not None:
            self.voice.setCurrentText(voice.voice)
            self.voice.currentTextChanged.connect(voice.set_voice)
            self.play_voice.clicked.connect(
                lambda: self.controller.command(voice.preview()))
            self.add_voice.clicked.connect(lambda: self._choose_recording(voice))
            self.remove_voice.clicked.connect(lambda: self._confirm_removal(voice))
            self.voice.currentTextChanged.connect(self._show_removable)
        voice_row = QHBoxLayout()
        voice_row.addWidget(self.voice)
        voice_row.addWidget(self.play_voice)
        voice_row.addWidget(self.add_voice)
        voice_row.addWidget(self.remove_voice)
        self._show_removable()
        # Voice loaded when Kaden starts, so the first click does not wait for it.
        self.load_voice = Switch()
        self.load_voice.setEnabled(voice is not None)
        if voice is not None:
            self.load_voice.setChecked(voice.load_at_start)
            self.load_voice.toggled.connect(lambda on: setattr(voice, "load_at_start", on))
        self.pages: dict[str, QWidget] = {
            "General": _page(
                "General", self.startup_error,
                _setting("AI provider", "Which company's models Kaden uses.",
                         self.modules["core"]),
                _setting("API key", "Your key for that provider, kept in the system keychain.",
                         self.api_key),
                _setting("Model", "Writes the answers.", self.model),
                _setting("Model for memory upkeep", "Summarizes and saves memory; a cheaper "
                         "one is fine.", self.light),
                self.model_note,
                _setting(MODULE_LABELS["tools"], MODULE_HINTS["tools"], self.modules["tools"]),
                _setting("Voice", "When you talk with Kaden.", voice_row),
                _setting("Load voice at start", "Ready at the first click. Uses memory while "
                         "Kaden runs.", self.load_voice),
                text_label(AT_RESTART, selectable=False, role="small")),
            "Spending": _page(
                "Spending", _setting("Spending limit (US$)", f"Kaden stops when it reaches it. "
                         f"{dollars(spent)} spent so far.", self.limit),
                *((_setting("Limit per sub-session (US$)", "Empty for no limit. Not part "
                            "of your own limit.",
                            self.child_limit),) if sessions is not None else ())),
            "Permissions": _page(
                "Permissions", _setting("What the agent may do", "When it asks you before acting.",
                         self.permissions),
                _setting("Saved answers", "What you said always or never to, in every "
                         "conversation.", self._rules_box(settings), below=True)),
            "Instructions": _page(
                "Instructions", _setting("Instructions", "Your own instructions, added after "
                         "Kaden's built-in guidance in every conversation.", self.instructions,
                         below=True)),
            "Memory": self.memory,
            "Tool servers": self.servers,
        }
        self.tool_rows = ToolSettingRows(settings.tool_settings)
        if self.tool_rows.rows:  # after Instructions
            pages = list(self.pages.items())
            at = list(self.pages).index("Instructions") + 1
            pages.insert(at, ("Tools", _page("Tools", *self.tool_rows.rows,
                                             text_label(AT_RESTART, selectable=False,
                                                    role="small"))))
            self.pages = dict(pages)
        self.nav = QListWidget(objectName="settingsNav")
        self.nav.setFixedWidth(190)
        self.stack = QStackedWidget()
        for name, page in self.pages.items():
            item = QListWidgetItem(name)
            item.setSizeHint(QSize(0, 34))
            self.nav.addItem(item)
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self._page_changed)
        self.nav.setCurrentRow(0)

        header = DragArea(objectName="settingsHeader")
        top = QHBoxLayout(header)
        top.setContentsMargins(18, 10, 10, 10)
        top.addWidget(text_label("Settings", selectable=False, role="title"), 1)
        close = QToolButton(objectName="windowControl")
        close.setIcon(theme.icon("close", theme.current().muted))
        close.setToolTip("Close")
        close.setAccessibleName("Close")
        close.clicked.connect(self.reject)
        top.addWidget(close)
        footer = QHBoxLayout()
        footer.setContentsMargins(18, 8, 18, 14)
        notes = QVBoxLayout()
        notes.addWidget(self.notice)
        notes.addWidget(self.error)
        footer.addLayout(notes, 1)
        footer.addWidget(buttons)
        body = QHBoxLayout()
        body.setContentsMargins(12, 0, 0, 0)
        body.setSpacing(12)
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(header)
        outer.addLayout(body, 1)
        outer.addLayout(footer)
        self._corners = RoundedCorners(self)

    def _choose_recording(self, voice: VoiceMode) -> None:
        """A recording of about ten seconds of someone speaking, made a voice."""
        self.recording_dialog = QFileDialog(self, "Add a voice from a recording",
                                            str(Path.home()))
        self.recording_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        self.recording_dialog.setNameFilter(
            f"Recordings ({' '.join('*' + kind for kind in RECORDING_TYPES)})")
        self.recording_dialog.fileSelected.connect(
            lambda path: self.controller.command(self._add_recording(voice, Path(path))))
        self.recording_dialog.open()

    def _show_removable(self, *_: object) -> None:
        """Only a voice copied from a recording can be removed."""
        self.remove_voice.setEnabled(self.voice.currentIndex() > 0
                                     and self.add_voice.isVisibleTo(self))

    def _confirm_removal(self, voice: VoiceMode) -> None:
        name = self.voice.currentText()
        self.removal = confirm(
            self, f"Remove {name}?",
            lambda: self.controller.command(self._remove_recording(voice, name)),
            yes="Remove", details="Your own file of the recording stays.", danger=True)

    async def _remove_recording(self, voice: VoiceMode, name: str) -> None:
        await voice.remove_recording(name)
        self.voice.removeItem(self.voice.findText(name))
        self.voice.setCurrentText(voice.voice)
        self._show_removable()

    async def _add_recording(self, voice: VoiceMode, path: Path) -> None:
        name = await voice.add_recording(path)
        if self.voice.findText(name) < 0:
            self.voice.addItem(name)
        self.voice.setCurrentText(name)

    def show_page(self, name: str) -> None:
        self.nav.setCurrentRow(list(self.pages).index(name))

    def _page_changed(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        page = self.stack.currentWidget()
        if page is self.memory or page is self.servers:
            page.refresh()

    def _module_box(self, kind: str) -> QComboBox:
        choice = self.settings.modules[kind]
        box = QComboBox()
        for label, module_id in module_options(choice):
            box.addItem(label, module_id)
        if choice.chosen is None:
            box.insertItem(0, "Custom (from config.toml)", None)
        box.setCurrentIndex(max(0, box.findData(choice.chosen)))
        return box

    def _core_changed(self) -> None:
        switching = self.modules["core"].currentData() != self.settings.modules["core"].running
        self.model.setEnabled(not switching and self.has_models)
        self.light.setEnabled(not switching and self.has_models)
        self.model_note.setVisible(switching)

    def form(self) -> SettingsForm:
        return SettingsForm(
            modules={kind: box.currentData() for kind, box in self.modules.items()},
            api_key=self.api_key.text(),
            model=self.model.currentData(),
            maintenance_model=self.light.currentData(),
            spending_limit=self.limit.text(),
            permissions=PermissionPolicy(self.permissions.currentData()),
            instructions=self.instructions.toPlainText(),
            permission_rules=self.answers.kept(),
            tool_settings=self.tool_rows.values(),
        )

    def _rules_box(self, settings: Settings) -> QWidget:
        self.answers = SavedAnswers(settings.permission_rules)
        self.rules, self.remove_rule = self.answers.rules, self.answers.remove
        return self.answers

    def _save(self, *, restart: bool = False) -> None:
        try:
            update = settings_update(self.settings, self.form())
            child_limit = self._child_limit()
        except ValueError as e:
            say(self.error, str(e))
            return
        if update == SettingsUpdate() and child_limit is UNCHANGED and not restart:
            self.accept()
            return
        reasons = loosening(self.settings, update)
        if (self.sessions is not None and child_limit is not UNCHANGED
                and raises_limit(self.sessions.limit_usd, child_limit)):
            reasons.append("raises the limit per sub-session" if child_limit is not None
                           else "removes the limit per sub-session")
        if reasons:
            confirm(self, "Save these changes?",
                    lambda: self._apply(update, restart, child_limit),
                    yes="Save", details="This change " + " and ".join(reasons) + ".")
        else:
            self._apply(update, restart, child_limit)

    def _child_limit(self) -> Any:
        """The new limit per sub-session (None: no limit), UNCHANGED when it is as it was."""
        if self.sessions is None:
            return UNCHANGED
        text = self.child_limit.text().strip().lstrip("$")
        try:
            usd = float(text) if text else None
        except ValueError:
            raise ValueError("Write the limit per sub-session as a number of dollars, or "
                             "leave it empty for no limit.") from None
        if usd is not None and usd <= 0:
            raise ValueError("The limit per sub-session must be more than $0; leave it "
                             "empty for no limit.")
        return UNCHANGED if usd == self.sessions.limit_usd else usd

    def _apply(self, update: SettingsUpdate, restart: bool,
               child_limit: Any = UNCHANGED) -> None:
        self.save_button.setEnabled(False)
        self.error.hide()

        async def apply() -> Settings:
            settings = self.settings
            if child_limit is not UNCHANGED and self.sessions is not None:
                await self.sessions.set_limit(child_limit)
            if update != SettingsUpdate():
                settings = await self.runtime.update_settings(update)
            if restart:
                await self.runtime.restart()
            return settings

        def failed(error: Exception) -> None:
            self.save_button.setEnabled(True)
            say(self.error, user_message(error))

        def saved(settings: Settings) -> None:
            self.accept()
            if not restart and needs_restart(update) and (notice := restart_notice(settings)):
                confirm(self.parentWidget(), "Restart Kaden now?",
                        lambda: self.controller.command(self.runtime.restart()),
                        yes="Restart now", details=notice)

        self.controller.command(apply(), done=saved, failed=failed)
