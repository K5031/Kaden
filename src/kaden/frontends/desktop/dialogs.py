"""Dialogs: long-term memory, notes, Continuous's journal and life, saved answers, a
duty, and the welcome.

Dialogs use open() and signals, never exec(): exec() would run a nested event loop inside
the qasync loop that also runs the runtime.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from kaden.api import (
    ConversationInfo,
    Duty,
    MemoryNote,
    PermissionRule,
    RuntimeAPI,
    Settings,
    SettingsUpdate,
)
from kaden.frontends.common import (
    DUTY_HINT,
    DUTY_KIND_LABELS,
    MEMORY_HINT,
    PLAN_HINT,
    parse_plan_text,
    plan_text,
)
from kaden.frontends.desktop.sidebar import RowDelegate
from kaden.frontends.desktop.viewmodel import (
    Controller,
    clip,
    limit_update,
    memory_source,
    user_message,
)
from kaden.frontends.desktop.widgets import (
    DOCS_URL,
    PREVIEW_ROLE,
    error_label,
    primary,
    say,
    text_label,
)
from kaden.frontends.life import NAME

KEY_PAGES = {
    "anthropic": "https://platform.claude.com/",
    "deepseek": "https://platform.deepseek.com/api_keys",
    "openai": "https://platform.openai.com/api-keys",
}


def _right(*widgets: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addStretch()
    for widget in widgets:
        row.addWidget(widget)
    return row


# ── long-term memory ──────────────────────────────────────────────────────


class MemoryDialog(QDialog):
    """What Kaden remembers across conversations: the profile and notes, which the user
    adds, edits and deletes."""

    def __init__(self, parent: QWidget, controller: Controller, *, embedded: bool = False
                 ) -> None:
        super().__init__(parent)
        self.controller = controller
        if embedded:  # a page of Settings
            self.setWindowFlags(Qt.WindowType.Widget)
        self.setWindowTitle("Memory")
        self.setMinimumSize(560, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*((4, 16, 18, 4) if embedded else (20, 18, 20, 16)))
        layout.addWidget(text_label("Memory", selectable=False,
                                role="title" if embedded else "heading"))
        layout.addWidget(text_label(MEMORY_HINT, tone="muted"))
        self.entries = QListWidget()
        self.entries.setAccessibleName("Memory notes")
        self.entries.setItemDelegate(RowDelegate(self.entries, wrap=True))
        self.entries.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.entries.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.entries.setMouseTracking(True)
        self.entries.currentItemChanged.connect(lambda *_: self._selection_changed())
        self.entries.itemActivated.connect(lambda _: self._edit())
        layout.addWidget(self.entries, 1)
        self.add_button = primary(QPushButton("Add…"))
        self.add_button.clicked.connect(lambda: NoteDialog(self, None).open())
        self.edit_button = QPushButton("Edit…")
        self.edit_button.clicked.connect(self._edit)
        self.forget_button = QPushButton("Delete…")
        self.forget_button.clicked.connect(self._forget)
        row = QHBoxLayout()
        for button in (self.add_button, self.edit_button, self.forget_button):
            row.addWidget(button)
        row.addStretch()
        if not embedded:
            close = QPushButton("Close")
            close.clicked.connect(self.accept)
            row.addWidget(close)
        layout.addLayout(row)
        self._selection_changed()

    def refresh(self) -> None:
        async def load() -> None:
            notes = await self.controller.notes()
            selected = self._selected()
            self.entries.clear()
            for note in notes or ():
                item = QListWidgetItem(note.title)
                item.setData(Qt.ItemDataRole.UserRole, note)
                item.setData(PREVIEW_ROLE, note.description)
                item.setToolTip(memory_source(note))
                self.entries.addItem(item)
                if selected is not None and note.id == selected.id:
                    self.entries.setCurrentItem(item)
            self._selection_changed()

        self.controller.command(load())

    def _selected(self) -> MemoryNote | None:
        item = self.entries.currentItem()
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)

    def _selection_changed(self) -> None:
        note = self._selected()
        self.edit_button.setEnabled(note is not None)
        self.forget_button.setEnabled(note is not None and not note.fixed)

    def _edit(self) -> None:
        if (note := self._selected()) is not None:
            NoteDialog(self, note).open()

    def _forget(self) -> None:
        if (note := self._selected()) is not None:
            confirm(self, f"Delete the note “{clip(note.title, 80)}”?",
                    lambda: self.controller.delete_note(note.id), yes="Delete", danger=True,
                    details="Kaden won't use it again. The messages it came from stay until "
                            "you delete their conversation.")


class NoteDialog(QDialog):
    """Add or edit one memory note; the profile keeps its title."""

    def __init__(self, parent: MemoryDialog, note: MemoryNote | None) -> None:
        super().__init__(parent)
        self.memory, self.note = parent, note
        self.setWindowTitle("Edit note" if note else "Add a note")
        self.setMinimumSize(560, 420)
        form = QFormLayout(self)
        form.setContentsMargins(20, 18, 20, 16)
        self.title = QLineEdit(note.title if note else "")
        self.title.setPlaceholderText("A project, person or topic")
        self.title.setReadOnly(note is not None and note.fixed)
        self.description = QLineEdit(note.description if note else "")
        self.description.setPlaceholderText("One line: what the note is about")
        self.text = QPlainTextEdit(note.text if note else "")
        self.error = error_label()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        self.save_button = primary(buttons.button(QDialogButtonBox.StandardButton.Save))
        form.addRow("Title", self.title)
        form.addRow("About", self.description)
        form.addRow("Note", self.text)
        form.addRow(self.error)
        form.addRow(buttons)

    def _save(self) -> None:
        self.save_button.setEnabled(False)

        def failed(error: Exception) -> None:
            self.save_button.setEnabled(True)
            say(self.error, user_message(error))

        self.memory.controller.save_note(
            self.title.text(), self.description.text(), self.text.toPlainText(),
            self.note.id if self.note else None, failed=failed,
            done=lambda _: self.accept())


class JournalDialog(QDialog):
    """The life's journal, as it is when opened: what it wrote of its days."""

    def __init__(self, parent: QWidget, name: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{name}'s journal")
        self.resize(640, 560)
        column = QVBoxLayout(self)
        column.setContentsMargins(20, 18, 20, 16)
        column.addWidget(text_label(f"{name}'s journal", selectable=False, role="heading"))
        self.page = QTextBrowser()
        self.page.setOpenExternalLinks(True)
        column.addWidget(self.page, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        column.addWidget(buttons)

    def show_text(self, text: str) -> None:
        if text.strip():
            self.page.setMarkdown(text)
        else:
            self.page.setPlainText("Nothing written yet.")


class LifeDialog(QDialog):
    """The life's name and who it is, in the user's words, before it first lives; later
    they are changed in its settings (LifeSettingsDialog)."""

    def __init__(self, parent: QWidget, name: str, about: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Who they are")
        self.setMinimumWidth(520)
        form = QFormLayout(self)
        form.setContentsMargins(20, 18, 20, 16)
        form.addRow(text_label("Who they are", selectable=False, role="heading"))
        self.name = QLineEdit(name)
        self.name.setPlaceholderText(NAME)
        self.about = QPlainTextEdit(about)
        self.about.setPlaceholderText("Personality, interests, how they talk… or leave it to them")
        self.about.setMaximumHeight(140)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        primary(buttons.addButton("Start",
                                   QDialogButtonBox.ButtonRole.AcceptRole))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow("Name", self.name)
        form.addRow("Who they are", self.about)
        form.addRow(buttons)


class SavedAnswers(QWidget):
    """The saved answers (always or never allow), and a way to take one back."""

    def __init__(self, rules: Sequence[PermissionRule]) -> None:
        super().__init__()
        self.rules = QListWidget()
        self.rules.setMaximumHeight(140)
        for rule in rules:
            item = QListWidgetItem(f"{'Always allow' if rule.allow else 'Never allow'}: {rule}")
            item.setData(Qt.ItemDataRole.UserRole, rule)
            self.rules.addItem(item)
        self.remove = QPushButton("Remove")
        self.remove.setEnabled(False)
        self.rules.currentRowChanged.connect(lambda row: self.remove.setEnabled(row >= 0))
        self.remove.clicked.connect(lambda: self.rules.takeItem(self.rules.currentRow()))
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        if not rules:
            column.addWidget(text_label("None yet.", selectable=False, tone="muted"))
            self.rules.hide()
            self.remove.hide()
        column.addWidget(self.rules)
        column.addWidget(self.remove, 0, Qt.AlignmentFlag.AlignLeft)

    def kept(self) -> tuple[PermissionRule, ...]:
        return tuple(self.rules.item(i).data(Qt.ItemDataRole.UserRole)
                     for i in range(self.rules.count()))


class DutyDialog(QDialog):
    """The open conversation's duty and plan."""

    def __init__(self, parent: QWidget, controller: Controller, info: ConversationInfo) -> None:
        super().__init__(parent)
        self.controller = controller
        duty = info.duty or Duty("")
        self.setWindowTitle("Duty")
        self.setMinimumSize(600, 520)
        form = QFormLayout(self)
        form.setContentsMargins(20, 18, 20, 16)
        form.addRow(text_label("Duty", selectable=False, role="heading"))
        form.addRow(text_label(DUTY_HINT, tone="muted"))
        self.instruction = QPlainTextEdit(duty.instruction)
        self.instruction.setPlaceholderText("e.g. Keep my inbox answered")
        self.instruction.setMaximumHeight(90)
        self.kind = QComboBox()
        for kind, label in DUTY_KIND_LABELS.items():
            self.kind.addItem(label, kind)
        self.kind.setCurrentIndex(self.kind.findData(duty.kind))
        self.max_minutes, self.min_minutes = QDoubleSpinBox(), QDoubleSpinBox()
        for box, seconds in ((self.max_minutes, duty.max_interval_s),
                             (self.min_minutes, duty.min_interval_s)):
            box.setRange(0.1, 7 * 24 * 60)
            box.setDecimals(1)
            box.setSuffix(" minutes")
            box.setValue(seconds / 60)
        self.plan = QPlainTextEdit(plan_text(info.plan))
        self.plan.setPlaceholderText("[todo] A step | due 2026-11-12 09:00 | a note")
        self.error = error_label()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.save_button = primary(buttons.addButton(
            "Save" if info.duty else "Turn on", QDialogButtonBox.ButtonRole.AcceptRole))
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        if info.duty is not None:
            off = buttons.addButton("Turn off", QDialogButtonBox.ButtonRole.DestructiveRole)
            off.clicked.connect(lambda: self._apply(None))
        form.addRow("What to do", self.instruction)
        form.addRow("How long", self.kind)
        form.addRow("Check in at least every", self.max_minutes)
        form.addRow("and at most every", self.min_minutes)
        form.addRow(text_label(PLAN_HINT, tone="muted"))
        form.addRow("Plan", self.plan)
        form.addRow(self.error)
        form.addRow(buttons)

    def _save(self) -> None:
        instruction = self.instruction.toPlainText().strip()
        self._apply(Duty(instruction, self.kind.currentData(), self.min_minutes.value() * 60,
                         self.max_minutes.value() * 60) if instruction else None)

    def _fail(self, error: Exception) -> None:
        self.save_button.setEnabled(True)
        say(self.error, user_message(error))

    def _apply(self, duty: Duty | None) -> None:
        try:
            plan = parse_plan_text(self.plan.toPlainText())
        except ValueError as e:
            self._fail(e)
            return
        self.save_button.setEnabled(False)
        self.controller.save_duty(duty, plan, failed=self._fail, done=lambda _: self.accept())


# ── settings ──────────────────────────────────────────────────────────────


def confirm(parent: QWidget, text: str, then: Callable[[], None], *, yes: str = "Continue",
            details: str = "", danger: bool = False) -> QMessageBox:
    """Cancel is the default; `yes` names the action."""
    box = QMessageBox(QMessageBox.Icon.NoIcon, "Kaden", text, parent=parent)
    box.setInformativeText(details)
    cancel = box.addButton(QMessageBox.StandardButton.Cancel)
    action = box.addButton(yes, QMessageBox.ButtonRole.DestructiveRole if danger
                           else QMessageBox.ButtonRole.AcceptRole)
    if danger:
        action.setProperty("danger", True)
        action.style().polish(action)
    box.setDefaultButton(cancel)
    box.buttonClicked.connect(lambda button: button is action and then())
    box.open()
    return box


def choose_folder(parent: QWidget, start: str, then: Callable[[str], None]) -> QFileDialog:
    dialog = QFileDialog(parent, "Choose the work folder", start)
    dialog.setFileMode(QFileDialog.FileMode.Directory)
    dialog.setOption(QFileDialog.Option.ShowDirsOnly)
    dialog.fileSelected.connect(then)
    dialog.open()
    return dialog


# ── first run ─────────────────────────────────────────────────────────────


class WelcomeDialog(QDialog):
    """Three pages: provider, key, then work folder and spending limit, saved on the setup
    runtime before anything is built. Accepted when everything is saved; the window then
    restarts Kaden, which builds the provider with its key."""

    def __init__(self, parent: QWidget, runtime: RuntimeAPI, controller: Controller,
                 settings: Settings) -> None:
        super().__init__(parent)
        self.runtime, self.controller, self.settings = runtime, controller, settings
        self.setWindowTitle("Welcome to Kaden")
        self.setMinimumSize(520, 400)
        self.pages = QStackedWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.addWidget(text_label("Welcome to Kaden", selectable=False, role="heading"))
        layout.addWidget(self.pages)

        # 1. provider
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 8, 0, 0)
        column.addWidget(text_label("Choose your AI provider. You pay it directly."))
        self.provider = QListWidget(objectName="choices")
        # Rows are sized by the style sheet, and the list lays them out as soon as one is
        # selected: without this they are placed by their unstyled height and overlap.
        self.provider.ensurePolished()
        self.provider.setAccessibleName("AI provider")
        self.cores = [m for m in settings.modules["core"].installed if m.key_name is not None]
        for core in self.cores:
            entry = QListWidgetItem(core.name)
            entry.setData(Qt.ItemDataRole.UserRole, core.id)
            self.provider.addItem(entry)
        chosen = settings.modules["core"].chosen
        self.provider.setCurrentRow(max(0, next(
            (i for i, core in enumerate(self.cores) if core.id == chosen), 0)))
        column.addWidget(self.provider)
        next_button = primary(QPushButton("Continue"))
        next_button.setDefault(True)
        next_button.clicked.connect(lambda: self.pages.setCurrentIndex(1))
        column.addLayout(_right(next_button))
        self.pages.addWidget(page)

        # 2. key
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 8, 0, 0)
        self.key_intro = text_label()
        get_key = QPushButton("Get a key")
        get_key.clicked.connect(self._open_key_page)
        self.key = QLineEdit(echoMode=QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Paste your API key")
        self.key.setAccessibleName("API key")
        self.key_error = error_label()
        column.addWidget(self.key_intro)
        column.addWidget(get_key, 0, Qt.AlignmentFlag.AlignLeft)
        column.addWidget(self.key)
        column.addWidget(self.key_error)
        column.addStretch()
        self.key_continue = primary(QPushButton("Save the key"))
        self.key_continue.clicked.connect(self._save_key)
        back = QPushButton("Back")
        back.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        row = QHBoxLayout()
        row.addWidget(back)
        row.addStretch()
        row.addWidget(self.key_continue)
        column.addLayout(row)
        self.pages.addWidget(page)
        self.pages.currentChanged.connect(self._page_shown)

        # 3. limit
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 8, 0, 0)
        column.addWidget(text_label("Spending limit in US dollars (optional)."))
        self.limit = QLineEdit()
        self.limit.setPlaceholderText("No limit")
        column.addWidget(self.limit)
        column.addWidget(text_label("Each chat works in a folder you choose for it. Kaden works "
                                "freely there and asks before anything outside it."))
        self.finish_error = error_label()
        column.addWidget(self.finish_error)
        column.addStretch()
        self.finish_button = primary(QPushButton("Start using Kaden"))
        self.finish_button.clicked.connect(self._finish)
        column.addLayout(_right(self.finish_button))
        self.pages.addWidget(page)

    def _chosen(self) -> Any:
        provider_id = self.provider.currentItem().data(Qt.ItemDataRole.UserRole)
        return next(core for core in self.cores if core.id == provider_id)

    def _page_shown(self, index: int) -> None:
        if index == 1:
            self.key_intro.setText(f"Paste your {self._chosen().name} API key. Kaden keeps it "
                                   "in your computer's keychain.")
            self.key.setFocus()

    def _open_key_page(self) -> None:
        if url := KEY_PAGES.get(self._chosen().id):
            QDesktopServices.openUrl(QUrl(url))

    def _save_key(self) -> None:
        key = self.key.text().strip()
        if not key:
            say(self.key_error, "Paste a key first.")
            return
        provider = self._chosen()
        chosen = self.settings.modules["core"].chosen
        update = SettingsUpdate(api_key=key, modules=None if provider.id == chosen
                                else {"core": provider.id})
        self.key_continue.setEnabled(False)
        self.key_error.hide()

        def failed(error: Exception) -> None:
            self.key_continue.setEnabled(True)
            hint = (f"\nIf your computer has no keychain, set the environment variable "
                    f"{provider.key_name} to the key instead (see {DOCS_URL})."
                    if provider.key_name else "")
            say(self.key_error, user_message(error) + hint)

        def saved(settings: Settings) -> None:
            self.settings = settings
            self.pages.setCurrentIndex(2)

        self.controller.command(self.runtime.update_settings(update), done=saved, failed=failed)

    def _finish(self) -> None:
        changes: dict[str, Any] = {}
        try:
            budget = limit_update(self.settings, self.limit.text())
        except ValueError as e:
            say(self.finish_error, str(e))
            return
        if budget != self.settings.budget:
            changes["budget"] = budget  # a first limit only tightens: no confirmation
        if not changes:
            self.accept()
            return
        self.finish_button.setEnabled(False)

        def failed(error: Exception) -> None:
            self.finish_button.setEnabled(True)
            say(self.finish_error, user_message(error))

        self.controller.command(self.runtime.update_settings(SettingsUpdate(**changes)),
                                done=lambda _: self.accept(), failed=failed)
