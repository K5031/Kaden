"""The desktop app's widgets, offscreen, against the runtime with scripted fakes."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import asyncio
import itertools
import time
from collections.abc import Callable
from typing import Any

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QColor, QContextMenuEvent, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QInputDialog,
    QLabel,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPushButton,
)

from kaden.api import (
    AgentState,
    Budget,
    ConversationInfo,
    PermissionPolicy,
    Role,
    SettingsUpdate,
    ToolStatus,
)
from kaden.frontends.desktop import app_menu, theme
from kaden.frontends.desktop import window as window_module
from kaden.frontends.desktop.viewmodel import (
    AssistantItem,
    Controller,
    ConversationModel,
    NoticeItem,
)
from kaden.frontends.desktop.window import (
    DATE_ROLE,
    PREVIEW_ROLE,
    AssistantBubble,
    LifeSettingsDialog,
    MainWindow,
    PermissionCard,
    SettingsDialog,
    StepGroup,
    ToolCard,
    UserBubble,
    WelcomeDialog,
)
from kaden.runtime import ModuleState
from kaden.runtime.config import RuntimeConfig
from kaden.runtime.setup import SetupRuntime
from tests.conftest import Harness, eventually
from tests.fakes.core import Step, call
from tests.fakes.tools import FakeTools

RUNNING = {"core": "first", "memory": "fake-memory", "tools": "fake-tools",
           "frontend": "scripted-window"}


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """Styled as the launcher styles it: sizes and layout depend on the style sheet."""
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)  # the launcher's setting: the runtime ends the loop
    theme.apply(app, dark=False)
    return app


async def open_window(h: Harness) -> tuple[MainWindow, Controller, asyncio.Task[None]]:
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller)
    task = asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    return window, controller, task


def widgets[W](window: MainWindow, kind: type[W]) -> list[W]:
    return [w for w in window.conversation._widgets.values() if isinstance(w, kind)]


def buttons(widget: Any, text: str) -> list[QPushButton]:
    return [b for b in widget.findChildren(QPushButton) if b.text() == text]


async def test_a_turn_is_rendered(make, qapp):
    h: Harness = await make()
    h.tools.instant("look", "found")
    h.core.add(Step(calls=[call("look", {"q": 1})]), Step(text="# Title\n\n| a | b |\n|---|---|"))
    await h.start()
    window, controller, _ = await open_window(h)
    window.input.setPlainText("hello")
    QTest.keyClick(window.input, Qt.Key.Key_Return)
    assert window.input.toPlainText() == ""
    await eventually(lambda: widgets(window, AssistantBubble) and not controller.model.busy)
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    (user,) = widgets(window, UserBubble)
    assert user.text.text() == "hello"
    (tool,) = widgets(window, ToolCard)
    assert tool.state.text().startswith("done") and not tool.cancel.isVisibleTo(tool)
    (reply,) = widgets(window, AssistantBubble)
    assert "Title" in reply.body.toPlainText()
    assert window.send_button.text() == "Send"


async def test_permission_card_buttons_answer_the_prompt(make, qapp):
    h: Harness = await make()
    h.tools.instant("write", read_only=False)
    h.core.add(Step(calls=[call("write")]), Step(text="ok"))
    await h.start()
    window, controller, _ = await open_window(h)
    controller.send("write")
    await eventually(lambda: window.conversation.prompts.count() == 1)
    card = window.conversation.prompts.itemAt(0).widget()
    assert isinstance(card, PermissionCard)
    assert window.send_button.text() == "Stop"
    buttons(card, "Allow once")[0].click()
    await eventually(lambda: window.conversation.prompts.count() == 0 and not controller.model.busy)
    assert widgets(window, ToolCard)[0].state.text().startswith("done")


async def test_an_answer_can_be_kept_for_every_conversation(make, qapp):
    """Always allow, or never: the card says what it will keep, and Settings lists it,
    where it can be taken back."""
    from kaden.api import PermissionRule

    h: Harness = await make(permissions=PermissionPolicy.ASK)
    h.tools.instant("Bash", read_only=False)
    h.core.add(Step(calls=[call("Bash", {"command": "git status -s"})]), Step(text="ok"))
    await h.start()
    window, controller, _ = await open_window(h)
    controller.send("look")
    await eventually(lambda: window.conversation.prompts.count() == 1)
    card = window.conversation.prompts.itemAt(0).widget()
    labels = [b.text() for b in card.findChildren(QPushButton)]
    assert labels == ["Allow once", "Allow for this conversation",
                      "Always allow “git status”", "Deny"]  # always: one click
    [deny] = buttons(card, "Deny")
    assert deny.menu() is None  # deny is one click
    assert [a.text() for a in card.more_deny.menu().actions()] == [
        "Never allow “git status”"]
    [always] = buttons(card, "Always allow “git status”")
    always.click()
    await eventually(lambda: window.conversation.prompts.count() == 0 and not controller.model.busy)
    settings = await h.runtime.settings()
    assert settings.permission_rules == (PermissionRule("Bash", "git status"),)

    dialog = SettingsDialog(window, h.runtime, controller, settings, [], 0.0)
    dialog.open()
    assert [dialog.rules.item(i).text() for i in range(dialog.rules.count())] == [
        "Always allow: Bash: git status"]
    dialog.rules.setCurrentRow(0)
    dialog.remove_rule.click()
    dialog._save()
    await eventually(lambda: not dialog.isVisible())
    assert (await h.runtime.settings()).permission_rules == ()


async def test_switching_is_disabled_while_busy(make, qapp):
    h: Harness = await make()
    h.tools.hanging("wait")
    h.core.add(Step(calls=[call("wait")]))
    await h.start()
    window, controller, _ = await open_window(h)
    controller.send("wait")
    await eventually(lambda: widgets(window, ToolCard))
    await eventually(lambda: not window.new_button.isEnabled())
    assert window.switch_reason.text() == "Finish or stop the current task first."
    window.send_button.click()  # Stop
    await eventually(lambda: window.new_button.isEnabled())


async def setup(make: Any) -> Harness:
    return await make(modules=ModuleState(running=RUNNING, work_folder="/start"),
                      permissions=PermissionPolicy.DENY, kind="window")


def message_box(dialog: Any) -> QMessageBox:
    (box,) = [b for b in dialog.findChildren(QMessageBox) if b.isVisible()]
    return box


async def test_loosening_in_settings_asks_first_and_no_sends_nothing(make, qapp):
    h = await setup(make)
    await h.runtime.update_settings(SettingsUpdate(budget=Budget(max_cost_usd=10.0)))
    await h.start()
    window, controller, _ = await open_window(h)
    settings = await h.runtime.settings()
    dialog = SettingsDialog(window, h.runtime, controller, settings, [], 0.0)
    dialog.open()
    dialog.limit.setText("50")
    dialog.permissions.setCurrentIndex(dialog.permissions.findData(PermissionPolicy.ASK))
    dialog._save()
    box = message_box(dialog)
    assert box.text() == "Save these changes?"
    assert "$10 to $50" in box.informativeText()
    assert "ask me before every action" in box.informativeText()
    box.defaultButton().click()  # Cancel
    await asyncio.sleep(0.05)
    assert (await h.runtime.settings()).budget.max_cost_usd == 10.0
    dialog._save()
    next(b for b in message_box(dialog).buttons() if b.text() == "Save").click()
    await eventually(lambda: not dialog.isVisible())
    after = await h.runtime.settings()
    assert after.budget.max_cost_usd == 50.0
    assert after.permissions is PermissionPolicy.ASK


async def test_tightening_saves_without_asking(make, qapp):
    h = await setup(make)
    await h.start()
    window, controller, _ = await open_window(h)
    dialog = SettingsDialog(window, h.runtime, controller, await h.runtime.settings(), [], 0.0)
    dialog.open()
    dialog.limit.setText("5")
    dialog._save()
    await eventually(lambda: not dialog.isVisible())
    assert (await h.runtime.settings()).budget.max_cost_usd == 5.0


def setup_runtime(tmp_path: Any, error: str = "tests.fakes.core:first_factory: "
                                               "FIRST_API_KEY is not set") -> SetupRuntime:
    """Nothing could be built; by default because the provider has no key (first run)."""
    state = ModuleState(running=RUNNING, work_folder="/start")
    return SetupRuntime(RuntimeConfig(data_dir=tmp_path / "data", modules=state), "window",
                        error)


async def test_welcome_saves_the_key_and_limit_then_restarts(installed, qapp, keychain,
                                                                    tmp_path, monkeypatch):
    monkeypatch.delenv("FIRST_API_KEY", raising=False)
    runtime = setup_runtime(tmp_path)
    controller = Controller(runtime, ConversationModel())
    window = MainWindow(runtime, controller)
    window.show()  # as the launcher does, before the welcome opens
    asyncio.create_task(controller.run())
    await window.begin()
    dialog = window._dialog
    assert isinstance(dialog, WelcomeDialog)
    names = [dialog.provider.item(i).text() for i in range(dialog.provider.count())]
    assert "First AI" in names and "keyless" not in names  # only providers that take keys
    await asyncio.sleep(0)
    rows = [dialog.provider.visualItemRect(dialog.provider.item(i)) for i in range(len(names))]
    assert all(below.top() >= above.bottom() for above, below in itertools.pairwise(rows)), (
        "provider rows overlap")
    dialog.provider.setCurrentRow(names.index("Second AI"))
    dialog.pages.setCurrentIndex(1)
    dialog.key.setText("sk-good")
    dialog.key_continue.click()
    await eventually(lambda: dialog.pages.currentIndex() == 2)
    assert keychain.store[("kaden", "SECOND_API_KEY")] == "sk-good"
    assert not hasattr(dialog, "folder")  # each chat is given its folder
    dialog.limit.setText("3")
    dialog.finish_button.click()
    await eventually(lambda: runtime.restart_request is not None)
    after = await runtime.settings()
    assert after.modules["core"].chosen == "second"
    assert after.budget.max_cost_usd == 3.0


async def test_a_startup_error_opens_the_settings(installed, qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("FIRST_API_KEY", "k")  # not a first run
    runtime = setup_runtime(tmp_path, "the memory module 'gone' is not installed")
    controller = Controller(runtime, ConversationModel())
    window = MainWindow(runtime, controller)
    asyncio.create_task(controller.run())
    await window.begin()
    await eventually(lambda: isinstance(window._dialog, SettingsDialog))
    dialog = window._dialog
    assert "'gone' is not installed" in dialog.startup_error.text()
    assert dialog.restart_button.isVisibleTo(dialog)


async def test_settings_offer_no_memory_or_interface_choice(make, installed, qapp):
    h = await setup(make)
    await h.start()
    window, controller, _ = await open_window(h)
    dialog = SettingsDialog(window, h.runtime, controller, await h.runtime.settings(), [], 0.0)
    assert set(dialog.modules) == {"core", "tools"}
    # Memory is set in config.toml; the interface is each launcher's own.
    assert not {"memory", "frontend"} & set(dialog.form().modules)


async def test_kaden_can_be_added_to_the_app_menu(make, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(app_menu, "_reload_icons", lambda: None)  # not the real session
    h: Harness = await make()
    await h.start()
    window, controller, _ = await open_window(h)
    add = window.app_menu_action
    assert add.text() == "&Add to App Menu" and add.isVisible()
    add.trigger()
    await eventually(lambda: not add.isVisible())
    assert (tmp_path / "applications/io.github.k5031.kaden.desktop").exists()
    assert "app menu" in controller.model.items[-1].text
    other, _controller, _ = await open_window(h)
    assert not other.app_menu_action.isVisible()  # already there


async def test_model_menu_is_filled_before_it_opens(make, qapp):
    h: Harness = await make()
    await h.start()
    window, _controller, _ = await open_window(h)
    await eventually(lambda: [a.data() for a in window.model_menu.actions()]
                     == [m.id for m in h.core.models])
    window.model_menu.aboutToShow.emit()
    actions = window.model_menu.actions()  # there at once: an empty menu does not open
    assert [a.data() for a in actions if a.isChecked()] == [h.runtime.snapshot().model]
    other = next(a for a in actions if not a.isChecked())
    chosen = other.data()
    other.trigger()
    await eventually(lambda: h.runtime.snapshot().model == chosen)
    window.model_menu.aboutToShow.emit()
    assert [a.data() for a in window.model_menu.actions() if a.isChecked()] == [chosen]


async def test_model_menu_says_why_it_has_no_models(make, qapp):
    h: Harness = await make()
    window, _controller, _ = await open_window(h)

    async def fail() -> Any:
        raise RuntimeError("no key")

    h.runtime.list_models = fail
    await window.refresh_models()
    (only,) = window.model_menu.actions()
    assert only.text() == "no key" and not only.isEnabled()


# ── conversations and long-term memory ───────────────────────────────────


def visible[W](parent: Any, kind: type[W]) -> W:
    (found,) = [w for w in parent.findChildren(kind) if w.isVisible()]
    return found


def entries(window: MainWindow) -> list[str]:
    return [window.conversation_list.item(i).text()
            for i in range(window.conversation_list.count())]


def action(menu: QMenu, text: str) -> QAction:
    (found,) = [a for a in menu.actions() if a.text() == text and a.isVisible()]
    return found


async def test_sidebar_labels_rename_and_archive(make, qapp):
    h: Harness = await make()
    other = await h.start()
    await h.turn("What should I pack for Oslo?")
    await h.start()
    window, _controller, _ = await open_window(h)
    await window.fill_sidebar()
    assert entries(window) == ["What should I pack for Oslo?"]  # the new chat is not listed
    window.conversation_list.setCurrentRow(0)
    QApplication.sendEvent(window.conversation_list.viewport(), QContextMenuEvent(
        QContextMenuEvent.Reason.Keyboard, QPoint(0, 0), QPoint(0, 0)))
    menu = visible(window, QMenu)
    assert [a.text() for a in menu.actions() if a.text()] == [
        "Rename…", "Continue in a new conversation", "Archive", "Delete…"]
    menu.close()
    info = window.conversation_info(other.id)
    action(window.conversation_menu(info), "Rename…").trigger()
    dialog = visible(window, QInputDialog)
    dialog.setTextValue("Oslo trip")
    dialog.accept()
    await eventually(lambda: "Oslo trip" in entries(window))
    action(window.conversation_menu(window.conversation_info(other.id)), "Archive").trigger()
    await eventually(lambda: "Oslo trip" not in entries(window))
    window.show_archived.setChecked(True)
    await eventually(lambda: "Oslo trip  (archived)" in entries(window))
    menu = window.conversation_menu(window.conversation_info(other.id))
    action(menu, "Unarchive").trigger()
    await eventually(lambda: "Oslo trip" in entries(window))


async def test_sidebar_rows_show_a_preview_and_a_date(make, qapp):
    h: Harness = await make()
    named = await h.start()
    await h.turn("What should I pack for Oslo?")
    await h.runtime.rename_conversation(named.id, "Oslo trip")
    untitled = await h.start()
    await h.turn("Tidy my invoices folder")
    await h.start()
    window, _controller, _ = await open_window(h)
    await window.fill_sidebar()
    rows = {window.conversation_list.item(i).text(): window.conversation_list.item(i)
            for i in range(window.conversation_list.count())}
    assert set(rows) == {"Tidy my invoices folder", "Oslo trip"}
    assert rows["Oslo trip"].data(PREVIEW_ROLE) == "What should I pack for Oslo?"
    assert rows["Tidy my invoices folder"].data(PREVIEW_ROLE) == ""  # the label already is
    assert ":" in rows["Oslo trip"].data(DATE_ROLE)  # updated today: the time
    assert untitled.id in window._conversations


async def test_an_empty_conversation_shows_the_empty_state(make, qapp):
    h: Harness = await make()
    h.core.add(Step(text="hi"))
    await h.start()
    window, controller, _ = await open_window(h)
    window.conversation.rebuild()
    window._refresh_status()
    assert window.empty_state.isVisibleTo(window)
    controller.send("hello")
    await eventually(lambda: widgets(window, AssistantBubble) and not controller.model.busy)
    assert not window.empty_state.isVisibleTo(window)


async def test_delete_asks_with_cancel_as_the_default(make, qapp):
    h: Harness = await make()
    other = await h.start()
    await h.runtime.rename_conversation(other.id, "Old notes")
    current = await h.chat()
    window, _controller, _ = await open_window(h)
    await window.fill_sidebar()
    action(window.conversation_menu(window.conversation_info(current.id)), "Delete…").trigger()
    box = visible(window, QMessageBox)
    assert box.informativeText().endswith("A new chat will open.")
    box.defaultButton().click()
    await eventually(lambda: not box.isVisible())
    action(window.conversation_menu(window.conversation_info(other.id)), "Delete…").trigger()
    box = visible(window, QMessageBox)
    assert box.text() == "Delete “Old notes”?"
    assert box.informativeText() == (
        "This permanently deletes the conversation. What Kaden remembered from it stays in Memory.")
    assert box.icon() == QMessageBox.Icon.NoIcon
    assert [b.text() for b in box.buttons()] == ["Cancel", "Delete"]
    assert box.defaultButton().text() == "Cancel"
    next(b for b in box.buttons() if b.text() == "Delete").click()
    await eventually(lambda: not any("Old notes" in e for e in entries(window)))
    assert [c.id for c in await h.runtime.list_conversations(include_archived=True)] == [
        current.id]


async def test_continue_in_a_new_conversation(make, qapp):
    h: Harness = await make()
    first = await h.chat()
    window, controller, _ = await open_window(h)
    await window.fill_sidebar()
    action(window.conversation_menu(window.conversation_info(first.id)),
           "Continue in a new conversation").trigger()
    await eventually(lambda: controller.model.conversation.seed_from == first.id)


async def test_memory_dialog_add_edit_delete_notes(make, qapp):
    from kaden.api import PROFILE
    from kaden.frontends.desktop.window import NoteDialog

    h: Harness = await make()
    await h.start()
    window, _controller, _ = await open_window(h)
    window.show()
    await window.check_memory()
    assert window.memory_action.isVisible()
    await h.runtime.save_note(PROFILE, "", "Likes tea")
    window.open_memory()
    dialog = await eventually(lambda: window.memory_dialog)
    await eventually(lambda: dialog.entries.count() == 1)
    assert dialog.entries.item(0).text() == PROFILE
    dialog.entries.setCurrentRow(0)
    assert not dialog.forget_button.isEnabled()  # the profile stays
    dialog.add_button.click()
    editor = visible(dialog, NoteDialog)
    editor.title.setText("Trip")
    editor.description.setText("The Oslo trip")
    editor.text.setPlainText("Leaves Friday")
    editor.save_button.click()
    await eventually(lambda: dialog.entries.count() == 2)
    dialog.entries.setCurrentRow(1)
    dialog.edit_button.click()
    editor = visible(dialog, NoteDialog)
    assert editor.text.toPlainText() == "Leaves Friday"
    editor.text.setPlainText("Leaves Saturday")
    editor.save_button.click()
    await eventually(lambda: (h.store.notes()[1].text == "Leaves Saturday"))
    await eventually(lambda: dialog.entries.count() == 2)
    dialog.entries.setCurrentRow(1)
    dialog.forget_button.click()
    box = visible(dialog, QMessageBox)
    assert "stay until you delete their conversation" in box.informativeText()
    next(b for b in box.buttons() if b.text() == "Delete").click()
    await eventually(lambda: dialog.entries.count() == 1)
    assert [n.title for n in await h.runtime.notes()] == [PROFILE]
    window.close()


async def test_unsupported_memory_hides_memory_and_delete(make, qapp):
    h: Harness = await make(memory_kw={"editable": False})
    other = await h.chat()
    await h.start()
    window, controller, _ = await open_window(h)
    await window.check_memory()
    assert not window.memory_action.isVisible()
    await window.fill_sidebar()
    action(window.conversation_menu(window.conversation_info(other.id)), "Delete…").trigger()
    next(b for b in visible(window, QMessageBox).buttons() if b.text() == "Delete").click()
    await eventually(lambda: not controller.can_delete)
    assert not [a for a in window.conversation_menu(window.conversation_info(other.id)).actions()
                if a.text() == "Delete…" and a.isVisible()]


async def test_the_folder_is_shown_and_choosing_another_starts_a_chat_there(make, qapp,
                                                                           tmp_path):
    default, project, other = (tmp_path / n for n in ("default", "project", "other"))
    for folder in (default, project, other):
        folder.mkdir()
    tools = FakeTools()
    tools.folders = []
    h: Harness = await make(tools=tools, modules=ModuleState(work_folder=str(default)))
    h.core.add(Step(text="hi"))
    empty = await h.start()
    window, controller, _ = await open_window(h)
    assert window.folder_action.isVisible()

    window.folder_chosen(str(project))  # a new chat there; the unused one leaves nothing
    await eventually(lambda: controller.model.conversation.folder == str(project))
    await eventually(lambda: str(project) in window.title_label.toolTip())
    assert controller.model.conversation.id != empty.id
    assert await h.runtime.list_conversations() == []

    controller.send("hello")
    await eventually(lambda: widgets(window, AssistantBubble) and not controller.model.busy)
    talked = controller.model.conversation.id
    window.folder_chosen(str(other))  # a chat with messages stays; a new one starts
    await eventually(lambda: controller.model.conversation.folder == str(other))
    assert talked in [c.id for c in await h.runtime.list_conversations()]


async def test_the_folder_item_is_hidden_for_tools_with_a_fixed_folder(make, qapp):
    h: Harness = await make()
    await h.start()
    window, _controller, _ = await open_window(h)
    window._refresh_status()
    assert not window.folder_action.isVisible()


async def test_a_command_shows_its_description_with_the_command_behind_it(make, qapp):
    h: Harness = await make(permissions=PermissionPolicy.ASK)
    h.tools.instant("Bash", read_only=False)
    h.core.add(Step(calls=[call("Bash", {"command": "rm -rf build",
                                          "step_summary": "Remove the old build"})]),
               Step(text="done"))
    await h.start()
    window, controller, _ = await open_window(h)
    controller.send("clean")
    await eventually(lambda: window.conversation.prompts.count() == 1)
    card = window.conversation.prompts.itemAt(0).widget()
    texts = [label.text() for label in card.findChildren(QLabel)]
    assert "Kaden wants to: Remove the old build" in texts
    assert "rm -rf build" in texts  # what actually runs, on its own line
    [tool] = widgets(window, ToolCard)
    assert tool.title.text() == "Remove the old build"
    assert tool.title.toolTip() == "Bash: rm -rf build"


async def test_a_reply_with_nothing_to_show_is_not_drawn(make, qapp):
    """Claude's thinking can arrive with no readable text, in a turn that only calls a tool.
    The window decides what shows when it arranges the conversation; a reply never hides
    or shows itself."""
    from kaden.api import Message, MessageCommitted, Reasoning, Text

    h: Harness = await make()
    await h.start()
    window, controller, _ = await open_window(h)
    model = controller.model
    model.apply(MessageCommitted(Message("a", Role.ASSISTANT, (Reasoning(""),), 0.0)))
    model.apply(MessageCommitted(Message("b", Role.ASSISTANT, (Text("Hi"),), 0.0)))
    await asyncio.sleep(0)
    empty, reply = widgets(window, AssistantBubble)
    assert empty.isHidden() and not reply.isHidden()
    empty.item.reasoning = "Let me think"
    model.on_change(window_module.Updated(empty.item))
    await asyncio.sleep(0)
    assert not empty.isHidden()
    lone = AssistantBubble(AssistantItem())
    lone.show()
    lone.refresh()
    assert lone.isVisible()  # drawing it is not what hides it


async def test_a_finished_step_is_in_the_past_tense_without_a_permission_line(make, qapp):
    h: Harness = await make()
    h.tools.instant("look")
    h.core.add(Step(calls=[call("look", {"q": "x", "step_summary": "Search the web for x"})]),
               Step(text="done"))
    await h.start()
    window, controller, _ = await open_window(h)
    controller.send("go")
    await eventually(lambda: not controller.model.busy and widgets(window, ToolCard))
    [card] = widgets(window, ToolCard)
    await eventually(lambda: card.title.text() == "Searched the web for x")
    assert not hasattr(card, "notes")


def test_replies_have_no_avatar_and_comfortable_lines(qapp):
    bubble = AssistantBubble(AssistantItem(text="One.\n\n- a\n- b\n\nTwo.", final=True))
    assert not [w for w in bubble.findChildren(QLabel) if w.objectName() == "avatar"]
    block = bubble.body.document().begin()
    # percent of the font's line spacing, itself about 1.35 em: about 1.6 em, not double
    assert 110 <= block.blockFormat().lineHeight() <= 125
    assert block.blockFormat().bottomMargin() >= 8


def test_inline_code_is_smaller_on_a_soft_background(qapp):
    bubble = AssistantBubble(AssistantItem(text="Run `uv run pytest` now.", final=True))
    fragments = bubble.body.document().begin().begin()
    formats = []
    while not fragments.atEnd():
        formats.append(fragments.fragment().charFormat())
        fragments += 1
    [code] = [f for f in formats if f.fontFixedPitch()]
    assert code.fontPointSize() < bubble.body.font().pointSizeF()
    assert code.background().style() != Qt.BrushStyle.NoBrush


def test_a_code_block_is_one_box_with_its_lines_together(qapp):
    bubble = AssistantBubble(AssistantItem(
        text="Before.\n\n```python\none = 1\ntwo = 2\n```\n\nAfter.", final=True))
    document = bubble.body.document()
    lines = {}
    block = document.begin()
    while block.isValid():
        lines[block.text()] = block
        block = block.next()
    first, last = lines["one = 1"], lines["two = 2"]
    assert first.blockFormat().bottomMargin() == 0  # no paragraph gap between its lines
    assert not first.blockFormat().nonBreakableLines()  # a long line wraps, not cut off
    frame = QTextCursor(first).currentFrame()
    assert frame is not document.rootFrame() and frame is QTextCursor(last).currentFrame()
    assert frame.frameFormat().padding() > 0
    assert QTextCursor(lines["After."]).currentFrame() is document.rootFrame()


def test_a_code_block_has_round_corners_its_language_and_a_copy_button(qapp):
    bubble = AssistantBubble(AssistantItem(
        text="Before.\n\n```python\none = 1\ntwo = 2\n```\n\nThen:\n\n```\nls\n```", final=True))
    bubble.resize(500, 300)
    bubble.show()
    qapp.processEvents()
    body = bubble.body
    [box, _] = body.code_boxes()
    picture = body.grab().toImage()
    inside = picture.pixelColor(box.center())
    assert inside == QColor(theme.current().select)
    assert picture.pixelColor(box.left(), box.top()) != inside  # the corner is cut round
    assert body.code_languages() == ["python", ""]
    first, second = body.code_buttons
    assert first.isVisible() and box.contains(first.geometry()) and first.toolTip() == "Copy"
    first.click()
    assert QApplication.clipboard().text() == "one = 1\ntwo = 2"
    assert first.toolTip() == "Copied"
    second.click()
    assert QApplication.clipboard().text() == "ls"
    bubble.item.text = "No code now."
    bubble.refresh()
    assert not body.code_boxes() and not any(b.isVisible() for b in body.code_buttons)


def test_code_blocks_that_follow_each_other_are_separate_boxes(qapp):
    bubble = AssistantBubble(AssistantItem(
        text="Run:\n\n```sh\nls\n```\n\n```sh\npwd\n```\n\n```json\n{}\n```\n\n"
             "- item\n\n  ```\n  a\n  ```\n\n  ```\n  b\n  ```", final=True))
    bubble.resize(500, 400)
    bubble.show()
    qapp.processEvents()
    body = bubble.body
    assert body.code_languages() == ["sh", "sh", "json", "", ""]
    boxes = body.code_boxes()
    assert all(above.bottom() < below.top() for above, below in itertools.pairwise(boxes))
    for button, code in zip(body.code_buttons, ["ls", "pwd", "{}", "a", "b"], strict=True):
        button.click()
        assert QApplication.clipboard().text() == code


def colours_of(bubble: AssistantBubble) -> dict[str, str]:
    """The colour of each piece of text in a reply, by its text."""
    found = {}
    block = bubble.body.document().begin()
    while block.isValid():
        fragments = block.begin()
        while not fragments.atEnd():
            fragment = fragments.fragment()
            found[fragment.text().strip()] = fragment.charFormat().foreground().color().name()
            fragments += 1
        block = block.next()
    return found


def test_code_in_a_named_language_is_coloured(qapp):
    from kaden.frontends.desktop import syntax

    code = 'def area(r):\n    return 3.14 * r  # a circle\n\nname = "disc"'
    bubble = AssistantBubble(AssistantItem(text=f"```python\n{code}\n```", final=True))
    palette = {kind: color.lower() for kind, color in syntax.colors(dark=False).items()}
    found = colours_of(bubble)
    assert found["def"] == palette["keyword"] and found["return"] == palette["keyword"]
    assert found["area"] == palette["name"]
    assert found["3.14"] == palette["number"]
    assert found["# a circle"] == palette["comment"]
    assert found['"disc"'] == palette["string"]
    assert len(set(palette.values())) == len(palette)
    assert syntax.spans("ls  # all", "sh")[-1] == (4, 5, "comment")  # on a last line too


@pytest.mark.parametrize("fence", ["", "nonsense"])
def test_code_in_no_known_language_stays_plain(qapp, fence):
    bubble = AssistantBubble(AssistantItem(text=f"```{fence}\ndef area(r):\n```", final=True))
    assert len(set(colours_of(bubble).values())) == 1


@pytest.fixture
def unzoomed(qapp):
    yield
    theme.set_zoom(qapp, 1.0)


def test_zoom_scales_the_text(qapp, unzoomed):
    base = qapp.font().pointSizeF()
    theme.set_zoom(qapp, 1.5)
    assert qapp.font().pointSizeF() == pytest.approx(base * 1.5)
    assert f"{base * 1.5 * 1.45:.1f}pt" in qapp.styleSheet()
    theme.apply(qapp, dark=True)  # restyling keeps the zoom
    assert qapp.font().pointSizeF() == pytest.approx(base * 1.5)
    theme.apply(qapp, dark=False)
    theme.set_zoom(qapp, 1.0)
    assert qapp.font().pointSizeF() == pytest.approx(base)


def test_zoom_steps_stop_at_the_ends():
    assert theme.zoom_step(1.0, 1) > 1.0 and theme.zoom_step(1.0, -1) < 1.0
    assert theme.zoom_step(theme.ZOOM_LEVELS[-1], 1) == theme.ZOOM_LEVELS[-1]
    assert theme.zoom_step(theme.ZOOM_LEVELS[0], -1) == theme.ZOOM_LEVELS[0]
    assert theme.zoom_step(1.3, 1) == 1.5  # a level saved by hand moves to the next one


@pytest.fixture
def system_theme(qapp):
    yield
    theme.set_theme(qapp, "system")


async def test_ctrl_plus_minus_and_zero_zoom(make, qapp, unzoomed):
    h: Harness = await make()
    await h.start()
    window = MainWindow(h.runtime, Controller(h.runtime, ConversationModel()))
    actions = {a.text().replace("&", ""): a for a in window.findChildren(QAction)}
    actions["Zoom In"].trigger()
    actions["Zoom In"].trigger()
    assert theme.zoom() == theme.zoom_step(theme.zoom_step(1.0, 1), 1)
    actions["Zoom Out"].trigger()
    assert theme.zoom() == theme.zoom_step(1.0, 1)
    actions["Actual Size"].trigger()
    assert theme.zoom() == 1.0
    assert "Ctrl+=" in [k.toString() for k in actions["Zoom In"].shortcuts()]
    assert actions["Actual Size"].shortcut().toString() == "Ctrl+0"


async def test_the_zoom_and_theme_are_written_once_when_the_window_closes(
        make, qapp, unzoomed, system_theme, tmp_path):
    from kaden.frontends import desktop

    h: Harness = await make()
    await h.start()
    saved = tmp_path / desktop.ZOOM_FILE
    run = asyncio.ensure_future(desktop.DesktopFrontend(data_dir=tmp_path).run(h.runtime))

    def shown() -> list[MainWindow]:
        return [w for w in qapp.topLevelWidgets() if isinstance(w, MainWindow) and w.isVisible()]

    await eventually(shown)
    [window] = shown()
    window.zoom(1)
    window.zoom(1)
    window.set_theme("light")
    await asyncio.sleep(0.05)
    assert not saved.exists()
    window.close()
    await asyncio.wait_for(run, 5)
    assert await window_module.load_look(saved) == (
        theme.zoom_step(theme.zoom_step(1.0, 1), 1), "light")


async def test_a_saved_zoom_and_theme_are_read_back(tmp_path):
    saved = tmp_path / "desktop.json"
    assert await window_module.load_look(saved) == (1.0, "system")
    saved.write_text('{"zoom": 1.25, "theme": "light"}')
    assert await window_module.load_look(saved) == (1.25, "light")
    saved.write_text('{"theme": "pink"}')
    assert await window_module.load_look(saved) == (1.0, "system")
    saved.write_text("not json")
    assert await window_module.load_look(saved) == (1.0, "system")


async def test_the_theme_is_chosen_in_the_view_menu(make, qapp, system_theme):
    h: Harness = await make()
    await h.start()
    window = MainWindow(h.runtime, Controller(h.runtime, ConversationModel()))
    assert window.theme_actions["system"].isChecked()
    window.theme_actions["light"].trigger()
    assert theme.choice() == "light" and not theme.current().dark
    window.theme_actions["dark"].trigger()
    assert theme.current().dark and window.theme_actions["dark"].isChecked()
    theme.apply(qapp)  # as when the desktop switches: the choice stays
    assert theme.current().dark


def groups(window: MainWindow) -> list[StepGroup]:
    layout = window.conversation.items_layout
    return [w for i in range(layout.count())
            if isinstance(w := layout.itemAt(i).widget(), StepGroup)]


async def test_steps_fold_into_one_line_until_opened(make, qapp):
    h: Harness = await make()
    h.tools.instant("look", "found")
    h.tools.failing("open")
    h.core.add(Step(calls=[call("look", {"q": 1, "step_summary": "Search the web"}),
                           call("open", {"url": "u", "step_summary": "Open the page"})]),
               Step(text="Here it is."))
    await h.start()
    window, controller, _ = await open_window(h)
    window.show()
    controller.send("go")
    await eventually(lambda: widgets(window, AssistantBubble) and not controller.model.busy)
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    (group,) = groups(window)
    assert group.title.text() == "Open the page" and group.state.text() == "1 failed"
    cards = widgets(window, ToolCard)
    assert len(cards) == 2 and not any(card.isVisible() for card in cards)
    group.toggle.click()
    assert all(card.isVisible() for card in cards)
    assert not cards[0].state.isVisible() and cards[1].state.text().startswith("failed")
    reply = widgets(window, AssistantBubble)[0]
    layout = window.conversation.items_layout
    assert layout.indexOf(reply) == layout.indexOf(group) + 1  # the reply stays outside
    window.close()


async def test_a_running_group_shows_its_current_step(make, qapp):
    h: Harness = await make()
    h.tools.instant("look", "found")
    h.tools.hanging("wait")
    h.core.add(Step(calls=[call("look", {"step_summary": "Search the web"})]),
               Step(calls=[call("wait", {"step_summary": "Wait for the build"})]))
    await h.start()
    window, controller, _ = await open_window(h)
    controller.send("go")
    await eventually(lambda: groups(window) and groups(window)[0].title.text()
                     == "Wait for the build" and groups(window)[0].state.text() == "running")
    (group,) = groups(window)  # "waiting" until the call starts, then "running"
    assert group.cancel.isVisibleTo(group)
    group.cancel.click()
    await eventually(lambda: not controller.model.busy)
    await eventually(lambda: group.title.text() == "Wait for the build")
    assert group.state.text() == "1 cancelled" and not group.cancel.isVisibleTo(group)


async def test_a_replaced_streaming_reply_stays_hidden(make, qapp):
    h: Harness = await make()
    h.core.add(Step(text="Reply text."))
    await h.start()
    window, controller, _ = await open_window(h)
    window.show()
    controller.send("q")
    await eventually(lambda: widgets(window, AssistantBubble) and not controller.model.busy)

    def settled() -> bool:  # past the redraw delay of the streamed bubble
        qapp.processEvents()
        shown = [b for b in window.findChildren(AssistantBubble) if b.isVisible()]
        return shown == widgets(window, AssistantBubble)

    await eventually(settled)
    window.close()


async def test_a_burst_of_changes_is_laid_out_once(make, qapp, monkeypatch):
    h: Harness = await make()
    await h.start()
    window, controller, _ = await open_window(h)
    await asyncio.sleep(0)
    arranged = []
    original = window.conversation._arrange
    monkeypatch.setattr(window.conversation, "_arrange", lambda: (arranged.append(1), original()))
    for n in range(20):  # as a page of earlier messages arrives
        controller.model.notify(f"note {n}", "info")
    await asyncio.sleep(0)
    assert len(arranged) == 1 and window.conversation.items_layout.count() >= 20


async def test_tool_servers_are_added_edited_and_removed(make, qapp, keychain):
    from kaden.frontends.desktop.window import McpDialog, ServerDialog

    h: Harness = await make(modules=ModuleState(running=RUNNING, work_folder="/start"))
    await h.start()
    window, _controller, _ = await open_window(h)
    window.show()  # dialogs and their confirmations need a visible parent
    window.open_tool_servers()  # the Tool servers page of Settings
    dialog = await eventually(lambda: window.mcp_dialog)
    assert isinstance(dialog, McpDialog) and dialog.isVisible()
    await eventually(lambda: dialog.empty.isVisible())
    dialog.add_button.click()
    server = dialog.findChild(ServerDialog)
    assert "trust" in server.warning.text()
    server.name.setText("github")
    server.target.setText("npx -y server-github")
    server.variables.setPlainText("GITHUB_TOKEN=ghp-1")
    server.save_button.click()
    await eventually(lambda: dialog.servers.count() == 1)
    assert keychain.store[("kaden", "mcp/github/GITHUB_TOKEN")] == "ghp-1"
    assert dialog.restart_button.isVisible()  # servers start with the next start
    dialog.servers.setCurrentRow(0)
    dialog.edit_button.click()
    server = [d for d in dialog.findChildren(ServerDialog) if d.isVisible()][-1]
    assert server.variables.toPlainText() == "GITHUB_TOKEN="  # the value stays hidden
    server.target.setText("not 'closed")
    server.save_button.click()
    await eventually(lambda: server.error.isVisible())
    assert "quotes" in server.error.text()
    server.reject()
    assert dialog.servers.currentRow() == 0 and dialog.remove_button.isEnabled()
    dialog.remove_button.click()
    [remove] = [b for b in message_box(dialog).buttons() if b.text() == "Remove"]
    remove.click()
    await eventually(lambda: dialog.servers.count() == 0)
    assert ("kaden", "mcp/github/GITHUB_TOKEN") not in keychain.store


async def test_a_server_list_read_before_a_save_does_not_replace_it(make, qapp, keychain):
    from kaden.api import McpServer
    from kaden.frontends.desktop.window import McpDialog

    h: Harness = await make(modules=ModuleState(running=RUNNING, work_folder="/start"))
    await h.start()
    window, controller, _ = await open_window(h)
    release = asyncio.Event()
    settings = h.runtime.settings

    async def slow_settings() -> Any:  # the window's first read answers late
        current = await settings()
        await release.wait()
        return current

    h.runtime.settings = slow_settings  # type: ignore[method-assign]
    dialog = McpDialog(window, h.runtime, controller)
    dialog.refresh()
    h.runtime.settings = settings  # type: ignore[method-assign]
    shown = await dialog.save([McpServer("docs", url="https://d.example/mcp")])
    dialog._show(shown)
    release.set()
    await asyncio.sleep(0.05)
    assert dialog.servers.count() == 1  # the late, older list was ignored


async def test_a_memory_update_after_an_answer_is_shown_with_a_link(make, qapp):
    from kaden.api import Completion
    from kaden.frontends.desktop.window import NoticeLine

    h: Harness = await make()

    async def maintain(complete: Completion) -> bool:
        conversation = h.runtime.snapshot().conversation
        if (await h.memory.history(conversation.id))[-1].role is not Role.ASSISTANT:
            return False
        h.store.save("Trip", "The Oslo trip", "Leaves Friday", None, conversation.id)
        return True

    h.memory.maintain_override = maintain
    h.core.add(Step(text="Have a good trip."))
    await h.start()
    window, controller, _ = await open_window(h)
    window.show()
    controller.send("I leave for Oslo on Friday")
    line = await eventually(lambda: next(
        (w for w in window.conversation._widgets.values() if isinstance(w, NoticeLine)), None))
    assert "Memory updated: Trip" in line.text() and "Open memory" in line.text()
    line.linkActivated.emit("memory")
    await eventually(lambda: window.memory_dialog is not None
                     and window.memory_dialog.isVisible())
    window.close()


async def test_the_duty_dialog_turns_a_duty_on_and_off(make, qapp):
    from kaden.api import Duty, DutyKind, PlanStatus
    from kaden.frontends.desktop.window import DutyDialog

    h: Harness = await make(budget=Budget(max_cost_usd=5))
    h.core.add(Step(text="on it"))
    await h.start()
    window, _controller, _ = await open_window(h)
    window.show()
    window.duty_action.trigger()
    [dialog] = window.findChildren(DutyDialog)
    assert dialog.save_button.text() == "Turn on"
    dialog.instruction.setPlainText("Keep the inbox answered")
    dialog.kind.setCurrentIndex(dialog.kind.findData(DutyKind.GOAL))
    dialog.max_minutes.setValue(30)
    dialog.plan.setPlainText("[doing] Read mail\nReply | due 2030-01-07 09:00")
    dialog.save_button.click()
    await eventually(lambda: h.runtime.snapshot().conversation.duty is not None)
    info = h.runtime.snapshot().conversation
    assert info.duty == Duty("Keep the inbox answered", DutyKind.GOAL, 60, 1800)
    assert [(i.text, i.status) for i in info.plan] == [
        ("Read mail", PlanStatus.DOING), ("Reply", PlanStatus.TODO)]
    await eventually(lambda: "Goal" in window.duty_label.text())
    window.open_duty()
    dialog = [d for d in window.findChildren(DutyDialog) if d.isVisible()][-1]
    [off] = buttons(dialog, "Turn off")
    off.click()
    await eventually(lambda: h.runtime.snapshot().conversation.duty is None)
    assert len(h.runtime.snapshot().conversation.plan) == 2  # the plan stays


async def test_the_duty_dialog_shows_why_it_cannot_save(make, qapp):
    from kaden.frontends.desktop.window import DutyDialog

    h: Harness = await make()
    await h.start()
    window, _controller, _ = await open_window(h)

    async def refuse(duty: object) -> None:
        raise ValueError("this duty cannot run")

    h.runtime.set_duty = refuse  # type: ignore[method-assign]
    window.show()
    window.open_duty()
    [dialog] = window.findChildren(DutyDialog)
    dialog.instruction.setPlainText("Watch")
    dialog.save_button.click()
    await eventually(lambda: dialog.error.isVisible())
    assert "cannot run" in dialog.error.text()
    assert dialog.isVisible() and dialog.save_button.isEnabled()


async def test_a_sub_session_sits_under_its_conversation_and_opens_in_place(
        make, qapp, tmp_path, child_env):
    from kaden.frontends.desktop.window import KIND_ROLE
    from tests.test_sub_sessions import finish, parent

    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Write a haiku", "prompt": "Write a haiku", "run_in_background": True}, id="s")]),
        Step(text="Started."), Step(text="Got it.")],
        [finish("Haiku written."), {"text": "Here is the haiku."}, {"text": "Shorter now."}])
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller, sessions=sessions)
    asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    window.show()
    await h.turn("Get me a haiku")
    await eventually(lambda: sessions.sessions and sessions.sessions[0].state == "done",
                     timeout=30)
    await window.fill_sidebar()
    kinds = [window.conversation_list.item(i).data(KIND_ROLE)
             for i in range(window.conversation_list.count())]
    assert kinds == ["group", "conversation", "session"]  # the project, then the tree
    row = window.conversation_list.item(2)
    assert row.text() == "Write a haiku" and "Done" in row.data(PREVIEW_ROLE)
    window._conversation_chosen(row)
    window._render_sidebar()  # the list changes while the child starts again
    current = window.conversation_list.currentItem()
    assert (current.data(KIND_ROLE), current.text()) == ("session", "Write a haiku")
    await eventually(lambda: window.controller is not window.home
                     and any(isinstance(i, AssistantItem) and "Here is the haiku." in i.text
                             for i in window.model.items), timeout=15)
    await asyncio.sleep(0.1)
    assert sessions._viewers[sessions.sessions[0].id] == 1
    current = window.conversation_list.currentItem()
    assert (current.data(KIND_ROLE), current.text()) == ("session", "Write a haiku")
    replies = sum(isinstance(i, AssistantItem) for i in window.model.items)
    window.input.setPlainText("Make it shorter")
    window._send()  # to the sub-session, started again on its conversation
    await eventually(lambda: sum(isinstance(i, AssistantItem) and bool(i.text)
                                 for i in window.model.items) > replies, timeout=15)
    assert any(getattr(i, "text", None) == "Make it shorter" for i in window.model.items)
    window._conversation_chosen(window.conversation_list.item(1))  # back to the parent
    assert window.controller is window.home
    assert window.model.conversation.id == h.conversation.id


async def test_the_windows_own_conversation_is_heard_while_a_sub_session_shows(
        make, qapp, tmp_path, child_env):
    """What is the window's (the list of conversations, whether a new chat can start)
    follows its own conversation, whatever the conversation area shows."""
    from kaden.frontends.desktop.window import KIND_ROLE
    from tests.test_sub_sessions import finish, parent

    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Write a haiku", "prompt": "Write a haiku", "run_in_background": True}, id="s")]),
        Step(text="Started.")],
        [finish("Haiku written."), {"text": "Here is the haiku."}])
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller, sessions=sessions)
    asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    window.show()
    await h.turn("Get me a haiku")
    await eventually(lambda: sessions.sessions and sessions.sessions[0].state == "done",
                     timeout=30)
    await window.fill_sidebar()
    window._conversation_chosen(window.conversation_list.item(2))
    await eventually(lambda: window.controller is not window.home, timeout=15)

    def titles() -> list[str]:
        return [window.conversation_list.item(i).text()
                for i in range(window.conversation_list.count())
                if window.conversation_list.item(i).data(KIND_ROLE) == "conversation"]

    await h.runtime.rename_conversation(h.conversation.id, "Poems")  # as the sidebar does
    await eventually(lambda: titles() == ["Poems"])
    # The window's own conversation is idle: a new chat can start, whatever the
    # sub-session on screen is doing.
    assert window.new_button.isEnabled() and not window.switch_reason.isVisibleTo(window)


async def test_the_open_chat_clicked_again_while_another_opens_is_what_stays(
        make, qapp, tmp_path):
    """Last click wins: back to the chat that is open, before the other one has loaded."""
    h: Harness = await make()
    first = await h.chat("First")
    second = await h.chat("Second")
    window, controller, _ = await open_window(h)
    await window.fill_sidebar()
    assert controller.model.conversation.id == second.id
    window.open_conversation(first.id)
    window.open_conversation(second.id)  # changed their mind before it opened
    await eventually(lambda: window._target.conversation is None
                     and not window._target.opening)
    await asyncio.sleep(0.2)
    assert controller.model.conversation.id == second.id
    assert window.conversation_list.currentItem().data(Qt.ItemDataRole.UserRole) == second.id


async def test_clicks_in_a_row_open_the_last_one_and_not_each(make, qapp, tmp_path):
    """Only the newest conversation asked for is opened: the ones clicked past are not
    loaded on the way."""
    h: Harness = await make()
    chats = [await h.chat(name) for name in ("One", "Two", "Three", "Four")]
    window, controller, _ = await open_window(h)
    await window.fill_sidebar()
    started: list[str | None] = []
    start = h.runtime.start

    async def counted(options: Any) -> Any:
        started.append(options.conversation_id)
        return await start(options)

    h.runtime.start = counted  # type: ignore[method-assign]
    for chat in chats[:3]:
        window.open_conversation(chat.id)
    await eventually(lambda: controller.model.conversation.id == chats[2].id
                     and not window._target.opening)
    assert started == [chats[2].id]
    assert window.conversation_list.currentItem().data(Qt.ItemDataRole.UserRole) == chats[2].id


async def test_projects_group_the_sidebar_and_fold(make, qapp, tmp_path):
    from kaden.frontends.desktop.window import KIND_ROLE

    h: Harness = await make()
    await h.start()
    window, _controller, _ = await open_window(h)
    one, two = tmp_path / "alpha", tmp_path / "beta"
    window._conversations = {
        "a": ConversationInfo("a", "First", 1.0, 3.0, folder=str(one)),
        "b": ConversationInfo("b", "Second", 1.0, 2.0, folder=str(two)),
        "c": ConversationInfo("c", "Third", 1.0, 1.0, folder=str(one))}
    window._render_sidebar()
    rows = [(window.conversation_list.item(i).data(KIND_ROLE),
             window.conversation_list.item(i).text())
            for i in range(window.conversation_list.count())]
    assert rows == [("group", "alpha"), ("conversation", "First"), ("conversation", "Third"),
                    ("group", "beta"), ("conversation", "Second")]
    window._conversation_chosen(window.conversation_list.item(0))  # fold alpha
    assert [window.conversation_list.item(i).text()
            for i in range(window.conversation_list.count())] == ["alpha", "beta", "Second"]


async def test_child_rows_have_a_menu(make, qapp, tmp_path, child_env):
    from kaden.frontends.desktop.window import KIND_ROLE
    from tests.test_sub_sessions import finish, parent

    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Tidy the notes", "prompt": "Tidy the notes", "run_in_background": True}, id="s")]),
        Step(text="Started."), Step(text="Thanks.")],
        [finish("Tidied."), {"text": "Done."}])
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller, sessions=sessions)
    asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    window.show()
    await h.turn("Hi")
    [child] = sessions.sessions
    await eventually(lambda: child.state == "done", timeout=30)
    await window.fill_sidebar()
    menu = window.session_menu(child)
    assert [a.text() for a in menu.actions()] == [
        "Open", "Change spending limit…", "Rename…", "Archive", "Delete…"]
    sessions.rename(child, "Notes")
    assert any(window.conversation_list.item(i).text() == "Notes"
               and window.conversation_list.item(i).data(KIND_ROLE) == "session"
               for i in range(window.conversation_list.count()))


async def test_a_notice_from_a_child_wakes_the_open_conversation(make, qapp, tmp_path):
    from kaden.frontends.sessions import SubSessions

    h: Harness = await make()
    h.core.add(Step(text="Noted."))
    info = await h.start()
    sessions = SubSessions(tmp_path)
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller, sessions=sessions)
    asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    assert window.home is controller
    sessions.notes[info.id] = ["Sub-session 1 (x) ended a turn: done."]
    sessions._notify()
    await eventually(lambda: info.id in h.store.conversations and any(
        "Sub-session 1 (x) ended a turn" in str(m.blocks) and not m.from_user
        for m in h.store.conversations[info.id].messages))
    assert sessions.notes == {}


def test_long_call_details_are_clipped_until_shown(qapp):
    from kaden.frontends.desktop.window import Detail

    brief = "\n".join(f"Line {n} of a long sub-session brief." for n in range(12))
    detail = Detail(brief)
    assert detail.label.text().endswith("…") and "Line 5" not in detail.label.text()
    assert detail.toggle.isVisibleTo(detail) and detail.toggle.text() == "Show all"
    detail.toggle.click()
    assert detail.label.text() == brief and detail.toggle.text() == "Show less"
    detail.toggle.click()
    assert "Line 5" not in detail.label.text()
    short = Detail("ls -la")
    assert short.label.text() == "ls -la" and not short.toggle.isVisibleTo(short)


async def test_settings_set_the_limit_per_sub_session(make, qapp, tmp_path):
    from kaden.frontends.sessions import SubSessions

    h: Harness = await make()
    await h.start()
    window, controller, _ = await open_window(h)
    window.show()
    sessions = SubSessions(tmp_path)
    dialog = SettingsDialog(window, h.runtime, controller, await h.runtime.settings(), [], 0.0,
                            sessions)
    assert dialog.child_limit.text() == ""  # none, unless the user sets one
    assert dialog.child_limit.placeholderText() == "No limit"
    dialog.child_limit.setText("4")
    dialog.save_button.click()  # a limit where there was none: nothing to confirm
    await eventually(lambda: sessions.limit_usd == 4)
    assert SubSessions(tmp_path).limit_usd == 4
    dialog = SettingsDialog(window, h.runtime, controller, await h.runtime.settings(), [], 0.0,
                            sessions)
    assert dialog.child_limit.text() == "4"
    dialog.child_limit.setText("")
    dialog.save_button.click()  # removing it asks first, as raising does
    box = message_box(dialog)
    assert "removes the limit per sub-session" in box.informativeText()
    [save] = [b for b in box.buttons() if b.text() == "Save"]
    save.click()
    await eventually(lambda: sessions.limit_usd is None)
    assert SubSessions(tmp_path).limit_usd is None


async def test_a_long_notice_is_folded(make, qapp):
    from kaden.frontends.desktop.window import Detail, NoticeLine

    h: Harness = await make()
    await h.start()
    window, _controller, _ = await open_window(h)
    brief = "New goal: Research Gen.G's path through Worlds.\n\nFind every series they played."
    assert isinstance(window.conversation._widget_for(NoticeItem(brief)), Detail)
    folded = window.conversation._widget_for(NoticeItem(brief))
    assert folded.label.text() == "New goal: Research Gen.G's path through Worlds.…"
    assert isinstance(window.conversation._widget_for(NoticeItem("Time to check in.")), NoticeLine)


async def test_the_menus_live_behind_two_buttons_and_their_shortcuts_still_work(make, qapp):
    import getpass

    h: Harness = await make()
    await h.start()
    window, _controller, _ = await open_window(h)
    assert not window.findChildren(QMenuBar)
    texts = [a.text() for a in window.menu_button.menu().actions() if a.text()]
    assert texts == ["&New chat", "New chat in another &folder…", "Set a &duty…", "S&top",
                     "&View", "&Model"]
    profile = window.profile_button.menu()
    window._fill_profile_header()
    texts = [a.text() for a in profile.actions() if a.text() and a.isVisible()]
    assert texts[0].startswith(getpass.getuser())
    assert texts[1:4] == ["&Settings…", "&Memory…", "&Tool servers (MCP)…"]
    assert texts[-2:] == ["&Help", "&Quit"]
    shortcuts = {s.toString() for a in window.actions() for s in a.shortcuts()}
    assert {"Ctrl+N", "Ctrl+,", "Esc"} <= shortcuts
    window.gear_button.click()  # the click's `checked` is not a page name
    dialog = await eventually(lambda: window._dialog)
    await eventually(dialog.isVisible)
    assert dialog.stack.currentIndex() == 0


async def test_the_window_has_rounded_corners_and_a_shadow_unless_maximized(make, qapp):
    from kaden.frontends.desktop.window import RoundedCorners

    h: Harness = await make()
    await h.start()
    window, controller, _ = await open_window(h)
    dialog = SettingsDialog(window, h.runtime, controller, await h.runtime.settings(), [], 0.0)
    s = RoundedCorners.SHADOW  # the margin around the frame, where the shadow is drawn
    for shown in (window, dialog):
        shown.resize(900, 700)
        shown.show()
        qapp.processEvents()
        assert shown.contentsRect().getRect() == (s, s, 900 - 2 * s, 700 - 2 * s)
        image = shown.grab().toImage()
        for x, y in ((0, 0), (899, 0), (0, 699), (899, 699), (450, 0), (0, 350)):
            assert image.pixelColor(x, y).alpha() == 0  # the desktop shows through
        assert image.pixelColor(s, s).alpha() < 255  # the frame's corner is rounded
        for x, y in ((450, s - 2), (s - 2, 350), (901 - s, 350), (450, 701 - s)):
            pixel = image.pixelColor(x, y)  # just outside the frame: its shadow
            assert 0 < pixel.alpha() < 255 and pixel.lightness() == 0
        assert image.pixelColor(450, 350).alpha() == 255
        assert image.pixelColor(450, s).alpha() == 255 == image.pixelColor(s, 350).alpha()
        assert image.pixelColor(880 - s, 692 - s).alpha() == 255  # the window's background
    window.showMaximized()
    qapp.processEvents()
    assert window.grab().toImage().pixelColor(0, 0).alpha() == 255
    assert window.contentsRect() == window.rect()  # no shadow at the screen's edge


async def test_the_window_draws_its_own_title_bar(make, qapp):
    from PySide6.QtCore import QPoint

    from kaden.frontends.desktop.window import DragArea

    h: Harness = await make()
    await h.start()
    window, _controller, _ = await open_window(h)
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert set(window.window_buttons) == {"minimize", "maximize", "close"}
    assert window.findChildren(DragArea)  # the header and the sidebar's top row
    window.resize(800, 600)
    window.show()
    edges = window._edges.edges
    frame = window.contentsRect()  # the window without its shadow
    left, right, bottom = frame.left(), frame.right(), frame.bottom()
    assert edges(window.mapToGlobal(QPoint(left + 2, 300))) == Qt.Edge.LeftEdge
    assert edges(window.mapToGlobal(QPoint(right, bottom))) == (Qt.Edge.RightEdge
                                                               | Qt.Edge.BottomEdge)
    assert not edges(window.mapToGlobal(QPoint(400, 300)))
    # The shadow next to the frame resizes too; further out it is not the window's.
    assert edges(window.mapToGlobal(QPoint(left - 5, 300))) == Qt.Edge.LeftEdge
    assert not edges(window.mapToGlobal(QPoint(2, 300)))
    # A scroll bar along the edge keeps all but the outermost pixels for itself.
    bar = window.conversation.verticalScrollBar()
    assert edges(window.mapToGlobal(QPoint(right - 4, 300))) == Qt.Edge.RightEdge
    assert not edges(window.mapToGlobal(QPoint(right - 4, 300)), bar)
    assert edges(window.mapToGlobal(QPoint(right, 300)), bar) == Qt.Edge.RightEdge
    window.window_buttons["maximize"].click()
    await eventually(lambda: window.isMaximized())
    assert window.window_buttons["maximize"].toolTip() == "Restore"
    assert not edges(window.mapToGlobal(QPoint(2, 300)))  # maximized: nothing to resize
    window.window_buttons["maximize"].click()
    await eventually(lambda: not window.isMaximized())


async def test_the_model_picker_under_the_message_box_shows_the_models_name(make, qapp):
    h: Harness = await make()
    await h.start()
    window, _controller, _ = await open_window(h)
    await window.refresh_models()
    assert window.model_button.text() == "Fake One"  # its name, not its id
    assert window.model_button.menu() is window.model_menu
    line = window.input.fontMetrics().lineSpacing()
    assert line <= window.input.height() < 2 * line  # one slim row while empty
    window.input.setPlainText("one\ntwo\nthree")
    assert window.input.height() >= 3 * line  # and it grows with the text


async def test_the_permission_picker_tightens_at_once_and_asks_before_loosening(make, qapp):
    h: Harness = await make(permissions=PermissionPolicy.ALLOW_READ_ONLY)
    await h.start()
    window, _controller, _ = await open_window(h)
    window.show()
    window._refresh_status()
    assert window.permissions_button.text() == "Read only"
    window.choose_permissions(PermissionPolicy.ASK)  # stricter: no question
    await eventually(lambda: window.permissions_button.text() == "Ask first")
    assert (await h.runtime.settings()).permissions == PermissionPolicy.ASK
    window.choose_permissions(PermissionPolicy.AUTO)  # looser: asks first
    box = message_box(window)
    [allow] = [b for b in box.buttons() if b.text() == "Allow"]
    allow.click()
    await eventually(lambda: window.permissions_button.text() == "Auto")
    assert window.permission_actions[PermissionPolicy.AUTO].isChecked()


async def test_deleting_a_conversation_deletes_its_sub_sessions(make, qapp, tmp_path, child_env):
    from tests.test_sub_sessions import finish, parent

    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Write a haiku", "prompt": "Write a haiku", "run_in_background": True}, id="s")]),
        Step(text="Started."), Step(text="Got it.")],
        [finish("Haiku written."), {"text": "Done."}])
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller, sessions=sessions)
    asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    window.show()
    await h.turn("Go")
    [child] = sessions.sessions
    await eventually(lambda: child.state == "done", timeout=30)
    # the parent answers the child's report; a busy conversation cannot be deleted
    await eventually(lambda: len(h.core.requests) == 3
                     and h.runtime.snapshot().state == AgentState.IDLE, timeout=10)
    parent_info = h.conversation
    await window.fill_sidebar()
    window.delete_conversation(window.conversation_info(parent_info.id))
    box = message_box(window)
    assert "Its sub-session stops and is deleted too." in box.informativeText()
    [delete] = [b for b in box.buttons() if b.text() == "Delete"]
    delete.click()
    await eventually(lambda: not sessions.sessions, timeout=20)
    assert not child.data_dir.exists()


async def test_settings_has_sections_with_memory_and_tool_servers(make, qapp):
    h: Harness = await make()
    await h.start()
    window, controller, _ = await open_window(h)
    dialog = SettingsDialog(window, h.runtime, controller, await h.runtime.settings(), [], 0.0)
    assert [dialog.nav.item(i).text() for i in range(dialog.nav.count())] == [
        "General", "Spending", "Permissions", "Instructions", "Memory", "Tool servers"]
    assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
    dialog.show_page("Memory")
    assert dialog.stack.currentWidget() is dialog.memory
    dialog.show_page("Instructions")
    assert dialog.instructions.isVisibleTo(dialog.stack.currentWidget())


async def test_the_sidebar_starts_wide_enough_to_read_titles(make, qapp):
    from PySide6.QtWidgets import QSplitter

    h: Harness = await make()
    await h.start()
    window, _, _ = await open_window(h)
    window.resize(1400, 900)
    window.show()
    await asyncio.sleep(0.05)
    sidebar = window.findChild(QSplitter).sizes()[0]
    assert sidebar >= 288 * theme.zoom()
    window.close()


async def test_the_close_button_reaches_the_top_right_corner(make, qapp):
    from PySide6.QtCore import QPoint

    h: Harness = await make()
    await h.start()
    window, _controller, _ = await open_window(h)
    window.resize(1000, 700)
    window.show()
    await asyncio.sleep(0.05)
    frame = window.contentsRect()  # the window without its shadow
    assert window.childAt(frame.topRight()) is window.window_buttons["close"]
    for name, button in window.window_buttons.items():
        assert button.mapTo(window, QPoint(0, 0)).y() == frame.top(), name  # reaches the top
        # Small, as a title bar's are: not as tall as the header they sit in.
        assert button.height() <= 30 and button.width() <= 40, name
        assert button.height() < window.title_label.parentWidget().height(), name
    window.close()


async def test_a_new_chat_stays_out_of_the_sidebar_until_its_first_message(make, qapp):
    h: Harness = await make()
    h.core.add(Step(text="hi"))
    await h.start()
    window, controller, _ = await open_window(h)
    await window.fill_sidebar()
    assert entries(window) == []
    controller.send("hello there")
    await eventually(lambda: entries(window) == ["hello there"])


async def test_leaving_an_unused_chat_leaves_nothing(make, qapp):
    h: Harness = await make()
    talked = await h.start()
    await h.turn("Pack for Oslo")
    window, controller, _ = await open_window(h)
    await window.fill_sidebar()
    window.new_action.trigger()
    await eventually(lambda: controller.model.conversation.id != talked.id)
    await window.fill_sidebar()
    assert entries(window) == ["Pack for Oslo"]
    window.open_conversation(talked.id)
    await eventually(lambda: controller.model.conversation.id == talked.id)
    assert [c.id for c in await h.runtime.list_conversations()] == [talked.id]


async def test_the_clicked_conversation_stays_highlighted_while_it_opens(make, qapp):
    h: Harness = await make()
    target = await h.chat("Target")
    await h.chat("Current")
    window, controller, _ = await open_window(h)
    await window.fill_sidebar()
    history = h.runtime.history

    async def slow_history(*args: Any, **kwargs: Any) -> Any:  # as SQLite, in a thread
        await asyncio.sleep(0.1)
        return await history(*args, **kwargs)

    h.runtime.history = slow_history  # type: ignore[method-assign]
    window.open_conversation(target.id)
    await asyncio.sleep(0.03)
    assert controller.model.conversation.id != target.id  # still opening
    window._render_sidebar()  # any refresh meanwhile: sub-sessions, a rename
    assert window.conversation_list.currentItem().text() == "Target"
    await eventually(lambda: controller.model.conversation.id == target.id)
    await window.fill_sidebar()
    assert window.conversation_list.currentItem().text() == "Target"


async def test_nothing_is_highlighted_while_a_new_chat_opens(make, qapp):
    h: Harness = await make()
    await h.chat("Current")
    window, _controller, _ = await open_window(h)
    await window.fill_sidebar()
    assert window.conversation_list.currentItem().text() == "Current"
    window.open_conversation(None)
    window._render_sidebar()
    assert window.conversation_list.currentItem() is None


async def test_one_click_switches_once_where_a_click_also_activates(make, qapp):
    h: Harness = await make()
    target = await h.chat("Target")
    await h.chat("Current")
    window, controller, _ = await open_window(h)
    await window.fill_sidebar()
    starts: list[Any] = []
    start = h.runtime.start

    async def counting(options: Any) -> Any:
        starts.append(options)
        return await start(options)

    h.runtime.start = counting  # type: ignore[method-assign]
    lst = window.conversation_list
    row = next(lst.item(i) for i in range(lst.count()) if lst.item(i).text() == "Target")
    lst.itemClicked.emit(row)
    lst.itemActivated.emit(row)  # KDE activates on a single click too
    await eventually(lambda: controller.model.conversation.id == target.id)
    await asyncio.sleep(0.05)
    assert len(starts) == 1


async def test_leaving_a_sub_session_for_its_parent_never_shows_the_old_chat(
        make, qapp, tmp_path, child_env):
    """Open chat A, a sub-session of chat B, then B: A must not light up on the way."""
    from PySide6.QtWidgets import QListWidgetItem

    from kaden.frontends.desktop.window import KIND_ROLE
    from tests.test_sub_sessions import finish, parent

    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Research", "prompt": "Research", "run_in_background": True}, id="s")]),
        Step(text="Started."), Step(text="Noted.")], [finish("Done.")])
    await h.turn("Start research")
    parent_id = h.conversation.id
    await eventually(lambda: sessions.sessions and sessions.sessions[0].state == "done",
                     timeout=30)
    await eventually(lambda: len(h.core.requests) == 3  # the parent read the notice
                     and h.runtime.snapshot().state == AgentState.IDLE, timeout=15)
    await h.chat("Test message")  # now the open chat
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller, sessions=sessions)
    asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    await window.fill_sidebar()
    lst = window.conversation_list

    def row(kind: str, match: Callable[[QListWidgetItem], bool]) -> QListWidgetItem:
        return next(lst.item(i) for i in range(lst.count())
                    if lst.item(i).data(KIND_ROLE) == kind and match(lst.item(i)))

    window._conversation_chosen(row("session", lambda r: True))
    await eventually(lambda: window._viewing is not None, timeout=15)
    highlighted: list[str] = []
    lst.currentItemChanged.connect(
        lambda now, _: now is not None and highlighted.append(now.text()))
    window._conversation_chosen(
        row("conversation", lambda r: r.data(Qt.ItemDataRole.UserRole) == parent_id))
    await eventually(lambda: controller.model.conversation.id == parent_id)
    await window.fill_sidebar()
    assert "Test message" not in highlighted


async def test_enter_opens_the_selected_conversation(make, qapp):
    h: Harness = await make()
    target = await h.chat("Target")
    await h.chat("Current")
    window, controller, _ = await open_window(h)
    await window.fill_sidebar()
    lst = window.conversation_list
    lst.setCurrentItem(next(lst.item(i) for i in range(lst.count())
                            if lst.item(i).text() == "Target"))
    QTest.keyClick(lst, Qt.Key.Key_Return)
    await eventually(lambda: controller.model.conversation.id == target.id)


async def test_the_last_click_wins_while_a_conversation_opens(make, qapp):
    h: Harness = await make()
    first = await h.chat("First")
    second = await h.chat("Second")
    await h.chat("Current")
    window, controller, _ = await open_window(h)
    await window.fill_sidebar()
    history = h.runtime.history

    async def slow_history(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.05)
        return await history(*args, **kwargs)

    h.runtime.history = slow_history  # type: ignore[method-assign]
    window.open_conversation(first.id)
    window.open_conversation(second.id)  # clicked before the first had opened
    window._render_sidebar()
    assert window.conversation_list.currentItem().text() == "Second"
    await eventually(lambda: controller.model.conversation.id == second.id
                     and not window._target.opening)
    await asyncio.sleep(0.1)
    assert controller.model.conversation.id == second.id
    assert window.conversation_list.currentItem().text() == "Second"


async def test_a_failed_load_does_not_stop_later_switches(make, qapp):
    h: Harness = await make()
    first = await h.chat("First")
    await h.chat("Current")
    window, controller, _ = await open_window(h)
    await window.fill_sidebar()
    history = h.runtime.history
    failures = [KeyError("gone")]

    async def failing_once(*args: Any, **kwargs: Any) -> Any:
        if failures:
            raise failures.pop()
        return await history(*args, **kwargs)

    h.runtime.history = failing_once  # type: ignore[method-assign]
    window.open_conversation(first.id)
    await eventually(lambda: not window._target.opening)
    window.open_conversation(None)
    await eventually(lambda: controller.model.conversation.id != first.id
                     and not window._target.opening)


async def test_a_sub_session_still_opening_gives_way_to_a_later_click(
        make, qapp, tmp_path, child_env):
    from tests.test_sub_sessions import finish, parent

    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Research", "prompt": "Research", "run_in_background": True}, id="s")]),
        Step(text="Started."), Step(text="Noted.")], [finish("Done.")])
    await h.turn("Start research")
    await eventually(lambda: sessions.sessions and sessions.sessions[0].state == "done"
                     and len(h.core.requests) == 3
                     and h.runtime.snapshot().state == AgentState.IDLE, timeout=30)
    parent_id = h.conversation.id
    await h.chat("Other")  # the open chat
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller, sessions=sessions)
    asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    await window.fill_sidebar()
    child = sessions.sessions[0]
    opened = sessions.open

    async def slow_open(session: Any) -> Any:  # a stopped child starting again
        await asyncio.sleep(0.2)
        return await opened(session)

    sessions.open = slow_open  # type: ignore[method-assign]
    window.open_session(child)
    window.open_conversation(parent_id)  # changed their mind meanwhile
    await asyncio.sleep(0.5)
    assert window._viewing is None and window.controller is window.home
    # released, not left open (the child may still be starting on a busy machine)
    await eventually(lambda: sessions._viewers.get(child.id, 0) == 0)
    assert controller.model.conversation.id == parent_id
    assert window.conversation_list.currentItem().data(Qt.ItemDataRole.UserRole) == parent_id


async def test_the_open_chat_clicked_while_a_sub_session_opens_stays(
        make, qapp, tmp_path, child_env):
    """What was asked for last is what shows, also when it is already on screen."""
    from tests.test_sub_sessions import finish, parent

    h, sessions = await parent(make, tmp_path, [
        Step(calls=[call("Agent", {"description": "Research", "prompt": "Research", "run_in_background": True}, id="s")]),
        Step(text="Started."), Step(text="Noted.")], [finish("Done.")])
    await h.turn("Start research")
    await eventually(lambda: sessions.sessions and sessions.sessions[0].state == "done"
                     and len(h.core.requests) == 3
                     and h.runtime.snapshot().state == AgentState.IDLE, timeout=30)
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller, sessions=sessions)
    asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    await window.fill_sidebar()
    child = sessions.sessions[0]
    opened = sessions.open
    starts: list[Any] = []
    start = h.runtime.start

    async def slow_open(session: Any) -> Any:
        await asyncio.sleep(0.2)
        return await opened(session)

    async def counting(options: Any) -> Any:
        starts.append(options)
        return await start(options)

    sessions.open = slow_open  # type: ignore[method-assign]
    h.runtime.start = counting  # type: ignore[method-assign]
    lst = window.conversation_list
    window.open_session(child)
    assert lst.currentItem().text() == child.name  # highlighted while it opens
    window.open_session(child)  # a second click on it asks for nothing new
    window._conversation_chosen(next(
        lst.item(i) for i in range(lst.count())
        if lst.item(i).data(Qt.ItemDataRole.UserRole) == h.conversation.id))
    await asyncio.sleep(0.5)
    assert window._viewing is None and window.controller is window.home and not starts
    await eventually(lambda: sessions._viewers.get(child.id, 0) == 0)
    assert lst.currentItem().data(Qt.ItemDataRole.UserRole) == h.conversation.id


async def test_a_new_chat_offers_its_folder_and_recent_ones(make, qapp, tmp_path):
    default, project, other = (tmp_path / n for n in ("default", "project", "other"))
    for folder in (default, project, other):
        folder.mkdir()
    tools = FakeTools()
    tools.folders = []
    h: Harness = await make(tools=tools, modules=ModuleState(work_folder=str(default)))
    h.core.add(Step(text="hi"))
    await h.chat("In project", folder=str(project))
    await h.chat("In other", folder=str(other))
    await h.start()  # a new chat: no folder is chosen for it yet
    window, controller, _ = await open_window(h)
    await window.fill_sidebar()
    window._refresh_status()
    button = window.folder_button
    assert not button.isHidden() and button.text().strip(" ⌄") == "Choose folder"
    assert button.toolTip() == "" and window.title_label.toolTip() == ""
    menu = button.menu()
    menu.aboutToShow.emit()
    labels = [a.text() for a in menu.actions() if a.text()]
    assert labels == ["other", "project", "Choose folder…"]
    assert [a.text() for a in menu.actions() if a.isChecked()] == []
    window.input.setPlainText("hello there")
    window._send()  # no folder yet: the message stays and the folders are offered
    assert menu.isVisible() and window.input.toPlainText() == "hello there"
    assert not h.core.requests and controller.model.items == []
    menu.hide()
    refused: list[bool] = []
    controller.send("said aloud", spoken=True, refused=lambda: refused.append(True))
    assert refused == [True] and menu.isVisible()  # nor by voice, which listens again
    await asyncio.sleep(0.1)
    assert not h.core.requests and controller.model.items == []
    menu.hide()
    window.open_duty()  # nor a duty before the chat has its folder
    assert menu.isVisible()
    menu.hide()
    next(a for a in menu.actions() if a.text() == "project").trigger()
    await eventually(lambda: controller.model.conversation.folder == str(project))
    await eventually(lambda: button.text().split()[0] == "project")
    assert button.toolTip().endswith(str(project))
    assert window.input.toPlainText() == "hello there"
    menu.aboutToShow.emit()
    assert [a.text() for a in menu.actions() if a.isChecked()] == ["project"]
    controller.send("hello")
    await eventually(lambda: widgets(window, AssistantBubble) and not controller.model.busy)
    assert button.isHidden()  # chosen with the first message; the header shows it after
    task = controller.open_conversation(None)  # the next new chat asks again
    await task
    await eventually(lambda: not button.isHidden())
    assert button.text().strip(" ⌄") == "Choose folder"


async def test_the_folder_a_new_chat_starts_with_can_be_chosen_as_it_is(make, qapp, tmp_path):
    """Choosing the folder the chat already has counts as choosing."""
    tools = FakeTools()
    tools.folders = []
    h: Harness = await make(tools=tools, modules=ModuleState(work_folder=str(tmp_path)))
    h.core.add(Step(text="hi"))
    await h.start()
    window, controller, _ = await open_window(h)
    window.folder_chosen(str(tmp_path))
    await eventually(lambda: window.folder_button.text().split()[0] == tmp_path.name)
    window.input.setPlainText("hello")
    window._send()
    await eventually(lambda: widgets(window, AssistantBubble) and not controller.model.busy)


async def test_the_checklist_is_part_of_the_conversation(make, qapp):
    """It sits under the newest message while the agent works and is gone when it stops."""
    from kaden.frontends.desktop.viewmodel import ChecklistItem, Phase, ToolItem
    from kaden.frontends.desktop.window import PlanCard

    h: Harness = await make()
    plan = [{"content": "Read the code", "activeForm": "Read the code", "status": "in_progress"}, {"status": "pending", "content": "Fix the bug", "activeForm": "Fix the bug"}]
    later = [{"content": "Read the code", "activeForm": "Read the code", "status": "completed"},
             {"content": "Fix the bug", "activeForm": "Fix the bug", "status": "in_progress"}]
    h.core.add(Step(calls=[call("TodoWrite", {"todos": plan})]),
               Step(calls=[call("TodoWrite", {"todos": later}), call("look", {"q": 1})]))
    h.tools.hanging("look")
    await h.start()
    window, controller, _ = await open_window(h)
    controller.send("fix the bug")
    items = controller.model.items
    await eventually(lambda: any(isinstance(i, ToolItem) and i.phase is Phase.RUNNING
                                 for i in items))
    lists = [i for i in items if isinstance(i, ChecklistItem)]
    assert len(lists) == 1 and items[-1] is lists[0]  # under the step that came after it
    card = window.conversation._widgets[id(lists[0])]
    assert isinstance(card, PlanCard) and window.conversation.isAncestorOf(card)
    assert card.rows() == [("done", "Read the code"), ("doing", "Fix the bug")]
    controller.stop()
    await eventually(lambda: not controller.model.busy)
    await eventually(lambda: not any(isinstance(i, ChecklistItem) for i in items))
    assert id(lists[0]) not in window.conversation._widgets


async def test_dropped_files_wait_above_the_message_box_and_go_with_the_message(
        make, qapp, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl
    from PySide6.QtGui import QColor, QImage

    from kaden.api import File, Image
    from kaden.frontends.desktop.viewmodel import UserItem

    picture = tmp_path / "shot.png"
    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(QColor("teal"))
    image.save(str(picture))
    note = tmp_path / "notes.txt"
    note.write_text("Buy oat milk.")
    h: Harness = await make()
    h.core.add(Step(text="A teal picture and a shopping note."))
    await h.start()
    window, controller, _ = await open_window(h)
    dropped = QMimeData()
    dropped.setUrls([QUrl.fromLocalFile(str(picture)), QUrl.fromLocalFile(str(note))])
    assert window.input.canInsertFromMimeData(dropped)
    window.input.insertFromMimeData(dropped)  # a drop on the box, or a paste of files
    assert window.input.toPlainText() == ""  # attached, not typed in as paths
    assert window.attachments.paths() == [str(picture), str(note)]
    shot, text = window.attachments.chips
    assert shot.preview is not None and not shot.preview.pixmap().isNull()  # a small preview
    assert shot.preview.pixmap().width() <= 56 and text.preview is None
    text.remove.click()  # one can be taken off again
    assert window.attachments.paths() == [str(picture)]
    window.attachments.add([str(note)])
    window._paste_image(image)  # a picture copied from elsewhere
    await eventually(lambda: len(window.attachments.paths()) == 3)
    window.input.setPlainText("what are these?")
    QTest.keyClick(window.input, Qt.Key.Key_Return)
    assert window.attachments.paths() == [] and window.attachments.isHidden()
    await eventually(lambda: not controller.model.busy and h.core.requests)
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    sent = h.core.requests[0].messages[0]
    assert [type(b).__name__ for b in sent.blocks] == ["Text", "Image", "File", "Image"]
    [mine] = [i for i in controller.model.items if isinstance(i, UserItem)]
    assert [type(a) for a in mine.attachments] == [Image, File, Image]
    bubble = window.conversation._widgets[id(mine)]
    assert len(bubble.images) == 2 and [f.text() for f in bubble.files] == ["notes.txt"]
    assert not bubble.images[0].pixmap().isNull()


def test_a_sent_pdf_shows_by_its_name(qapp):
    from kaden.api import Document
    from kaden.frontends.desktop.viewmodel import UserItem

    bubble = UserBubble(UserItem("summarize", "m1", attachments=(
        Document("/home/me/report.pdf", "JVBERi0=", "Sales rose", 2),)))
    assert [f.text() for f in bubble.files] == ["report.pdf"] and not bubble.images
    assert bubble.files[0].toolTip() == "/home/me/report.pdf"


def test_a_reply_shows_copy_and_its_age_on_hover(qapp):
    import time

    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QEnterEvent

    bubble = AssistantBubble(AssistantItem(text="Hello **there**", final=True,
                                           created_at=time.time() - 125))
    assert not bubble.footer.isVisible() and bubble.footer.sizePolicy().retainSizeWhenHidden()
    bubble.show()
    QApplication.sendEvent(bubble, QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
    assert bubble.footer.isVisible() and bubble.age.text() == "2 minutes ago"
    bubble.copy.click()
    assert QApplication.clipboard().text() == "Hello **there**"
    QApplication.sendEvent(bubble, QEvent(QEvent.Type.Leave))
    assert not bubble.footer.isVisible()


def test_relative_age_reads_like_a_person_would_say_it():
    from kaden.frontends.desktop.viewmodel import relative_age

    now = 1_000_000.0
    assert relative_age(now - 20, now) == "just now"
    assert relative_age(now - 60, now) == "1 minute ago"
    assert relative_age(now - 3 * 3600, now) == "3 hours ago"


async def test_sidebar_tooltips_show_only_the_full_title(make, qapp, tmp_path):
    from kaden.frontends.sessions import SubSession

    h: Harness = await make()
    talked = await h.start()
    await h.turn("A long first message that reads as the title of this conversation")
    window, _controller, _ = await open_window(h)
    session = SubSession("1", "Research the full bracket\n\nTips: lots of detail", talked.id,
                         tmp_path / "s1")

    class Sessions:
        listeners: list = []  # noqa: RUF012

        def of(self, conversation_id, include_archived=False):
            return (session,) if conversation_id == talked.id else ()

    window.sessions = Sessions()
    await window.fill_sidebar()
    lst = window.conversation_list
    tips = [lst.item(i).toolTip() for i in range(lst.count())]
    assert tips == ["A long first message that reads as the title of this conversation",
                    "Research the full bracket"]  # the brief's first line, not all of it


def test_a_long_message_wraps_inside_its_bubble(qapp):
    from kaden.frontends.desktop.viewmodel import UserItem

    text = ("ayo i jus added a planning tool like u can make a roadmap while working on "
            "some task and it shows as a checklist")
    bubble = UserBubble(UserItem(text, "m1"))
    bubble.resize(600, 200)
    bubble.show()
    qapp.processEvents()
    padding = bubble.bubble.layout().contentsMargins()
    room = bubble.bubble.width() - padding.left() - padding.right()
    assert bubble.text.width() <= room  # nothing cut at the right edge
    assert bubble.text.minimumWidth() <= room
    bubble.close()


async def test_a_line_says_kaden_is_thinking_before_anything_streams(make, qapp):
    from kaden.api import ReasoningDelta, StateChanged

    h = await make()
    await h.start()
    window, controller, _ = await open_window(h)
    assert window.conversation.waiting_line.isHidden()
    controller.model.apply(StateChanged(AgentState.THINKING))
    assert not window.conversation.waiting_line.isHidden() and window.conversation.waiting_line.text() == "Thinking…"
    controller.model.apply(ReasoningDelta("Looking at the files"))
    assert window.conversation.waiting_line.isHidden()  # the reply's own Thinking… line shows now
    controller.model.apply(StateChanged(AgentState.IDLE))
    assert window.conversation.waiting_line.isHidden()


def test_thinking_says_so_while_it_streams(qapp):
    item = AssistantItem(reasoning="Looking at the files")
    bubble = AssistantBubble(item)
    assert not bubble.thinking.isHidden() and bubble.thinking.text() == "Thinking…"
    item.text, item.final = "Done.", True
    bubble.refresh()
    assert bubble.thinking.text() == "Thought"  # it is over; how long is not known
    item.thought_s = 3.6
    bubble.refresh()
    assert bubble.thinking.text() == "Thought for 4s"
    item.thought_s = 75.2
    bubble.refresh()
    assert bubble.thinking.text() == "Thought for 1m 15s"


def test_the_thinking_line_unfolds_what_was_thought(qapp):
    bubble = AssistantBubble(AssistantItem("Done.", "Looking at the files", final=True))
    assert bubble.reasoning.isHidden()
    # No wider than its words and the chevron close behind them.
    words = bubble.thinking.fontMetrics().horizontalAdvance(bubble.thinking.text())
    assert bubble.thinking.sizeHint().width() == words + 16
    bubble.thinking.click()
    assert not bubble.reasoning.isHidden() and bubble.reasoning.text() == "Looking at the files"


def test_a_scroll_bar_takes_clicks_across_its_whole_width(qapp):
    """Also on its outermost pixel, where the pointer lands at the edge of the screen."""
    from PySide6.QtWidgets import QScrollArea

    theme.apply(qapp, dark=True)
    area = QScrollArea()
    area.setWidget(QLabel("line\n" * 400))
    area.resize(300, 300)
    area.show()
    qapp.processEvents()
    bar = area.verticalScrollBar()
    for x in (0, bar.width() - 1):
        bar.setValue(0)
        QTest.mouseClick(bar, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         QPoint(x, bar.height() - 20))
        assert bar.value() > 0  # it paged down


async def test_add_files_picks_files_to_go_with_the_message(make, qapp, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    h: Harness = await make()
    await h.start()
    window, _controller, _ = await open_window(h)
    note = tmp_path / "note.txt"
    note.write_text("hello")
    assert window.attach_button.toolTip() == "Add files"
    window.attach_button.click()
    dialog = window.file_dialog
    assert dialog is not None and dialog.fileMode() is QFileDialog.FileMode.ExistingFiles
    dialog.filesSelected.emit([str(note)])
    dialog.close()
    assert window.attachments.paths() == [str(note)]


async def test_the_agents_questions_are_answered_from_a_card(make, qapp):
    from kaden.frontends.desktop.window import QuestionCard

    questions = [
        {"question": "Which database?", "header": "Database", "multiSelect": False,
         "options": [{"label": "SQLite", "description": "One file"},
                     {"label": "Postgres", "description": "A server"}]},
        {"question": "Which parts?", "header": "Parts", "multiSelect": True,
         "options": [{"label": "API", "description": ""}, {"label": "UI", "description": ""}]}]
    h: Harness = await make()
    h.core.add(Step(calls=[call("AskUserQuestion", {"questions": questions}, id="q")]),
               Step(text="On it."))
    await h.start()
    window, controller, _ = await open_window(h)
    controller.send("set it up")
    await eventually(lambda: window.conversation.prompts.count() == 1)
    card = window.conversation.prompts.itemAt(0).widget()
    assert isinstance(card, QuestionCard) and not card.send.isEnabled()
    buttons = {b.text(): b for b in card.findChildren(QPushButton)}
    buttons["SQLite"].click()
    buttons["Postgres"].click()  # one of the two
    buttons["API"].click()
    buttons["UI"].click()  # any of them
    assert not buttons["SQLite"].isChecked() and card.send.isEnabled()
    card.send.click()
    await eventually(lambda: window.conversation.prompts.count() == 0 and not controller.model.busy)
    result = (await h.results())["q"]
    assert '"Which database?"="Postgres"' in result.content
    assert '"Which parts?"="API, UI"' in result.content


async def test_plan_mode_is_turned_on_from_the_permission_picker(make, qapp):
    h: Harness = await make()
    await h.start()
    window, _, _ = await open_window(h)
    window.plan_action.trigger()
    await eventually(lambda: h.runtime.snapshot().plan_mode
                     and window.permissions_button.text() == "Plan mode")
    assert window.plan_action.isChecked()
    window.plan_action.trigger()
    await eventually(lambda: not h.runtime.snapshot().plan_mode
                     and window.permissions_button.text() != "Plan mode")


async def test_the_agents_plan_is_approved_from_a_card(make, qapp):
    from kaden.frontends.desktop.window import PlanApprovalCard

    h: Harness = await make(permissions=PermissionPolicy.ASK)
    h.core.add(Step(calls=[call("ExitPlanMode", {"plan": "1. Add the column"}, id="x")]),
               Step(text="On it."))
    await h.start()
    await h.runtime.set_plan_mode(True)
    window, controller, _ = await open_window(h)
    controller.send("add a column")
    await eventually(lambda: window.conversation.prompts.count() == 1)
    card = window.conversation.prompts.itemAt(0).widget()
    assert isinstance(card, PlanApprovalCard) and "Add the column" in card.plan.toPlainText()
    card.levels[PermissionPolicy.ALLOW_WORKSPACE].trigger()
    await eventually(lambda: window.conversation.prompts.count() == 0 and not controller.model.busy)
    assert (await h.results())["x"].status == ToolStatus.OK
    assert not h.runtime.snapshot().plan_mode
    assert (await h.runtime.settings()).permissions == PermissionPolicy.ALLOW_WORKSPACE


async def test_a_plan_sent_back_from_its_card_keeps_plan_mode(make, qapp):
    from kaden.frontends.desktop.window import PlanApprovalCard

    h: Harness = await make()
    h.core.add(Step(calls=[call("ExitPlanMode", {"plan": "1. Add the column"}, id="x")]),
               Step(text="Waiting."))
    await h.start()
    await h.runtime.set_plan_mode(True)
    window, controller, _ = await open_window(h)
    controller.send("add a column")
    await eventually(lambda: window.conversation.prompts.count() == 1)
    card = window.conversation.prompts.itemAt(0).widget()
    assert isinstance(card, PlanApprovalCard)
    card.keep.click()
    await eventually(lambda: window.conversation.prompts.count() == 0 and not controller.model.busy)
    assert (await h.results())["x"].status == ToolStatus.REFUSED
    assert h.runtime.snapshot().plan_mode


def test_the_thinking_line_counts_the_seconds_until_the_thinking_is_over(qapp):
    item = AssistantItem(reasoning="Looking", thinking_since=time.monotonic() - 12)
    bubble = AssistantBubble(item)
    assert bubble.thinking.text() == "Thinking… 12s" and bubble.ticking.isActive()
    item.thinking_since -= 60  # a minute later
    bubble.ticking.timeout.emit()
    assert bubble.thinking.text() == "Thinking… 1m 12s"
    item.final, item.thought_s = True, 73.0
    bubble.refresh()
    bubble._render()
    assert bubble.thinking.text() == "Thought for 1m 13s" and not bubble.ticking.isActive()



def test_text_is_claudes_size_at_100_percent(qapp):
    from PySide6.QtGui import QFontInfo

    assert theme.zoom() == 1.0
    assert QFontInfo(qapp.font()).pixelSize() == theme.BASE_PX


async def test_zooming_rescales_the_sidebar_with_the_text(make, qapp, unzoomed):
    from PySide6.QtWidgets import QSplitter

    h: Harness = await make()
    await h.start()
    window, _, _ = await open_window(h)
    window.resize(1600, 900)
    window.show()
    await asyncio.sleep(0.05)
    splitter = window.findChild(QSplitter)
    window.zoom(1)  # 110%
    await asyncio.sleep(0.05)
    assert splitter.sizes()[0] == pytest.approx(288 * 1.1, abs=2)
    splitter.setSizes([400, 1200])  # the user dragged it wider
    window.zoom(0)  # back to 100%: the dragged width shrinks by the same share
    await asyncio.sleep(0.05)
    assert splitter.sizes()[0] == pytest.approx(400 / 1.1, abs=2)
    window.close()


def test_a_prompt_of_a_kind_the_app_does_not_know_is_answered_in_words(qapp):
    from kaden.api import Prompt
    from kaden.frontends.desktop.window import AnyPromptCard

    replies: list[tuple[str, Any]] = []
    card = AnyPromptCard(Prompt("p1", "survey", "How did it go?"),
                         lambda prompt_id, reply: replies.append((prompt_id, reply)))
    assert any(label.text() == "How did it go?" for label in card.findChildren(QLabel))
    card.reply.setText("Well")
    [send] = buttons(card, "Send")
    send.click()
    assert replies == [("p1", "Well")] and not card.isEnabled()


async def test_the_chosen_voice_is_kept_with_the_look(tmp_path):
    saved = tmp_path / "desktop.json"
    assert await window_module.load_voice(saved) == "Heart"
    await window_module.save_look(saved, voice="Bella")
    assert await window_module.load_voice(saved) == "Bella"
    assert await window_module.load_look(saved) == (theme.zoom(), theme.choice())
    saved.write_text('{"voice": 3}')
    assert await window_module.load_voice(saved) == "Heart"
    # Which view was open: Continuous, or the user's chats (the default).
    assert await window_module.load_view(saved) is False
    await window_module.save_look(saved, voice="Bella", life=True)
    assert await window_module.load_view(saved) is True
    assert await window_module.load_voice_at_start(saved) is False
    await window_module.save_look(saved, voice="Bella", load_voice=True)
    assert await window_module.load_voice_at_start(saved) is True


async def test_the_voice_is_chosen_in_settings(make, qapp):
    from kaden.frontends.desktop.voice.mode import KOKORO_VOICES

    h: Harness = await make()
    await h.start()
    window, controller, _ = await open_window(h)
    dialog = SettingsDialog(window, h.runtime, controller, await h.runtime.settings(), [],
                            0.0, voice=window.voice)
    names = [dialog.voice.itemText(i) for i in range(dialog.voice.count())]
    assert names == list(KOKORO_VOICES) and dialog.voice.currentText() == "Heart"
    assert dialog.add_voice.isHidden()  # recordings are copied by the natural voice only
    dialog.voice.setCurrentText("Fenrir")
    assert window.voice.voice == "Fenrir"
    assert dialog.play_voice.isEnabled()
    # Loading it at start, so that the first click is quick: off unless the user turns it on.
    assert not dialog.load_voice.isChecked() and not window.voice.load_at_start
    dialog.load_voice.click()
    assert window.voice.load_at_start


async def test_a_voice_is_added_from_a_recording_in_settings(make, qapp, tmp_path):
    from kaden.frontends.desktop.voice.mode import NATURAL, VoiceMode

    h: Harness = await make()
    await h.start()
    window, controller, _ = await open_window(h)

    async def yes(text: str, details: str) -> bool:
        return True

    voice = VoiceMode(controller, tmp_path / "voice", yes, models=(), natural=True,
                      prepare=lambda source, target: target.write_bytes(source.read_bytes()))
    dialog = SettingsDialog(window, h.runtime, controller, await h.runtime.settings(), [],
                            0.0, voice=voice)
    assert not dialog.add_voice.isHidden() and dialog.voice.currentText() == NATURAL
    dialog.show()
    QApplication.processEvents()
    for button in (dialog.play_voice, dialog.add_voice, dialog.remove_voice):  # not cut off
        assert button.width() >= button.sizeHint().width(), button.text()
    dialog.close()
    dialog._choose_recording(voice)  # the picker shows the kinds Kaden reads
    [kinds] = dialog.recording_dialog.nameFilters()
    for kind in ("wav", "mp3", "flac", "ogg", "opus", "m4a"):
        assert f"*.{kind}" in kinds
    dialog.recording_dialog.reject()
    recording = tmp_path / "Ada.mp3"
    recording.write_bytes(b"ID3")
    await dialog._add_recording(voice, recording)
    names = [dialog.voice.itemText(i) for i in range(dialog.voice.count())]
    assert names == [NATURAL, "Ada"] and dialog.voice.currentText() == voice.voice == "Ada"
    assert dialog.remove_voice.isEnabled()  # a copied voice can be removed
    await dialog._remove_recording(voice, "Ada")
    names = [dialog.voice.itemText(i) for i in range(dialog.voice.count())]
    assert names == [NATURAL] and dialog.voice.currentText() == voice.voice == NATURAL
    assert not dialog.remove_voice.isEnabled()  # Natural cannot


def test_the_hint_in_the_message_box_starts_after_the_cursor(qapp):
    """The blinking cursor does not cover the first letter of "Message Kaden"."""
    box = window_module.MessageBox()
    box.setPlaceholderText("Message Kaden")
    assert box.placeholderText() == "Message Kaden"
    box.resize(300, 40)
    caret_ends = box.cursorRect().x() + box.cursorWidth()  # the first pixel after it
    box.setCursorWidth(0)  # nothing but the hint is drawn
    image = box.viewport().grab().toImage()
    inked = [x for x in range(image.width()) for y in range(image.height())
             if image.pixelColor(x, y) != image.pixelColor(0, 0)]
    assert inked and min(inked) >= caret_ends + 1  # a pixel's gap
    box.setPlainText("Hi")
    assert box.placeholderText() == "Message Kaden"  # kept for when the box empties


class FakeLife:
    """A life whose life runs on a harness's runtime instead of its own process."""

    def __init__(self, runtime: Any, *, has_lived: bool = True) -> None:
        self.runtime, self.listeners, self.started = runtime, [], 0
        self.said: list[str] = []
        self.asked: list[Any] = []
        self.has_lived, self.name, self.about = has_lived, "Kaden", ""

    async def set_identity(self, name: str, about: str) -> None:
        self.name, self.about = name, about


    async def read_journal(self) -> str:
        return self.journal

    journal = ""

    @property
    def running(self) -> bool:
        return self.started > 0

    async def start(self) -> None:
        self.started += 1

    restarted = 0
    daily_usd: float | None = 1.0

    async def restart(self) -> None:
        self.restarted += 1

    async def set_daily_usd(self, usd: float | None) -> None:
        self.daily_usd = usd

    def take_messages(self) -> list[str]:
        said, self.said = self.said, []
        return said

    def take_questions(self) -> list[Any]:
        asked, self.asked = self.asked, []
        return asked


async def test_the_life_takes_over_the_window_and_gives_it_back(make, qapp):
    """The Continuous switch in the title bar, the one way between the views: the whole
    window is the life's, its life in the conversation area and no chat list; switching
    back leaves it living."""
    h: Harness = await make()
    h.core.add(Step(text="Hi there."))
    await h.start()
    them: Harness = await make()
    them.core.add(Step(text="Oh, hello!"))
    await them.start()
    life = FakeLife(them.runtime)
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller, life=life)
    asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    switch = window.continuous_switch
    assert switch.isVisibleTo(window) and not switch.isChecked()
    menu = [a.text() for a in window.menu_button.menu().actions()]
    assert "&Continuous" not in menu  # the switch is the only way
    window.show()
    window.title_label.setText("A long title about the trip to Kyoto and the old maps")
    switch_at = switch.mapTo(window, switch.rect().center())
    switch.click()
    await eventually(lambda: window.showing_life)
    assert life.started == 1 and window.sidebar.isHidden()
    assert switch.isVisibleTo(window) and switch.isChecked()
    def laid_out(check):  # Qt lays a window out from its own events: let it
        return lambda: (qapp.processEvents(), check())[1]
    await eventually(laid_out(
        lambda: switch.mapTo(window, switch.rect().center()).y() == switch_at.y()))
    # Its name is the title, whole, with Journal after it.
    name = window.life_name
    assert window.title_label.isHidden()
    await eventually(laid_out(lambda: name.text() == "Kaden" and name.width()
                              >= name.fontMetrics().horizontalAdvance("Kaden")))
    def gap() -> int:  # from its name to Journal
        journal = window.journal_button
        return (journal.mapTo(window, journal.rect().topLeft()).x()
                - name.mapTo(window, name.rect().topRight()).x())
    await eventually(laid_out(lambda: gap() < 20))
    assert window.controller is not window.home  # the message box talks to it
    assert window.voice.controller is window.controller  # and so does voice
    window.input.setPlainText("hey")
    window._send()
    await eventually(lambda: any(isinstance(m, AssistantItem) for m in window.model.items))
    # Its view has no sidebar: ☰ is in its title bar.
    assert window.view_menu_button.isVisibleTo(window)
    assert window.view_menu_button.menu() is window.menu_button.menu()  # the same menu
    assert window.view_gear_button.isVisibleTo(window)  # and Settings, bottom left
    opened: list[bool] = []
    window.open_settings = lambda *args, **kwargs: opened.append(True)  # type: ignore[method-assign]
    window.view_gear_button.click()
    assert opened == [True]
    switch.click()
    await eventually(lambda: not window.showing_life)
    assert not window.sidebar.isHidden() and window.controller is window.home
    title = window.title_label
    assert not title.isHidden() and name.isHidden()
    title.setText("A long title about the trip to Kyoto and the old maps of the city")
    await eventually(laid_out(lambda: title.width() > window.title_row.geometry().width() // 2))
    assert not window.view_menu_button.isVisibleTo(window)  # the sidebar's ☰ is back
    assert not window.view_gear_button.isVisibleTo(window)  # and its gear
    assert not switch.isChecked()
    assert window.voice.controller is window.home
    assert them.runtime.snapshot().state != AgentState.STOPPED  # it lives on
    window.hide()


async def test_what_the_life_says_by_itself_shows_in_the_chat_on_screen(make, qapp):
    h: Harness = await make()
    await h.start()
    them: Harness = await make()
    await them.start()
    life = FakeLife(them.runtime)
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller, life=life)
    asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    life.said.append("I found an old map of Kyoto!")
    for listener in life.listeners:
        listener()
    await eventually(lambda: any("I found an old map of Kyoto!" in n.text
                                 for n in window.model.items if isinstance(n, NoticeItem)))


async def test_the_user_names_the_life_before_it_first_lives(make, qapp):
    h: Harness = await make()
    await h.start()
    them: Harness = await make()
    await them.start()
    life = FakeLife(them.runtime, has_lived=False)
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller, life=life)
    asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    window.continuous_switch.click()
    dialog = await eventually(lambda: window.life_dialog)
    assert life.started == 0  # not before it has a name
    dialog.name.setText("Mira")
    dialog.about.setPlainText("Curious, loves old maps.")
    dialog.accept()
    await eventually(lambda: window.showing_life)
    assert (life.name, life.about) == ("Mira", "Curious, loves old maps.")
    assert life.started == 1 and window.life_name.text() == "Mira"
    assert window.input.placeholderText() == "Message Mira"  # whom the box talks to
    assert not hasattr(window, "identity_button")  # who they are is in their settings
    menu = [a.text() for a in window.menu_button.menu().actions()]
    assert "Who they &are…" not in menu
    window.open_settings()
    settings = await eventually(lambda: isinstance(window._dialog, LifeSettingsDialog)
                                and window._dialog.isVisible() and window._dialog)
    assert (settings.name.text(), settings.about.toPlainText()) == (
        "Mira", "Curious, loves old maps.")
    settings.name.setText("Mira Fox")
    settings._save()
    await eventually(lambda: life.name == "Mira Fox" and not settings.isVisible())
    assert life.about == "Curious, loves old maps." and life.restarted == 0
    await eventually(lambda: window.life_name.text() == "Mira Fox")
    assert window.input.placeholderText() == "Message Mira Fox"
    life.said.append("Look at this map!")  # by itself, while its view is on screen
    window.continuous_switch.click()
    await eventually(lambda: not window.showing_life)
    assert window.input.placeholderText() == "Message Kaden"
    for listener in life.listeners:
        listener()
    await eventually(lambda: any(n.text == "Mira Fox: Look at this map!"
                                 for n in window.model.items if isinstance(n, NoticeItem)))


async def test_its_view_is_on_screen_at_once_while_it_wakes(make, qapp):
    """Opening on the life shows its view before its process has started: never the
    user's chats in between."""
    h: Harness = await make()
    await h.start()
    them: Harness = await make()
    await them.start()
    life = FakeLife(them.runtime)
    awake = asyncio.Event()

    async def slow_start() -> None:
        life.started += 1
        await awake.wait()

    life.start = slow_start  # type: ignore[method-assign]
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller, life=life)
    asyncio.create_task(controller.run())
    window.continuous_switch.setChecked(True)  # as the app does before showing the window
    assert window.sidebar.isHidden() and not window.waking.isHidden()  # at once
    assert window.conversation.isHidden() and window.composer.isHidden()
    assert window.life_name.text() == "Kaden" and window.continuous_switch.isChecked()
    awake.set()
    await eventually(lambda: window.showing_life)
    assert window.waking.isHidden() and not window.conversation.isHidden()
    assert not window.composer.isHidden()
    window.continuous_switch.click()  # and back, then again: going back while it wakes works
    await eventually(lambda: not window.showing_life)
    awake.clear()
    window.continuous_switch.setChecked(True)
    window.continuous_switch.click()
    assert not window.sidebar.isHidden() and window.waking.isHidden()
    awake.set()
    await asyncio.sleep(0.05)
    assert not window.showing_life  # it woke after the user had gone back




async def test_the_lifes_view_shows_what_it_is_doing_and_its_journal(make, qapp):
    h: Harness = await make()
    await h.start()
    them: Harness = await make()
    them.tools.slow("look", 0.5)
    them.core.add(Step(calls=[call("look", {"step_summary": "Reading about fox legends"})]),
                  Step(text=""))
    await them.start()
    life = FakeLife(them.runtime)
    life.journal = "# Monday\n\nFound an old map of Kyoto."
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller, life=life)
    asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    window.continuous_switch.click()
    await eventually(lambda: window.showing_life)
    await them.runtime.activate()  # it does something by itself
    await eventually(lambda: window.duty_label.text() == "Reading about fox legends")
    assert window.journal_button.isVisibleTo(window)
    window.journal_button.click()
    journal = await eventually(lambda: window.journal_dialog)
    await eventually(lambda: "old map of Kyoto" in journal.page.toPlainText())
    journal.reject()
    window.continuous_switch.click()
    await eventually(lambda: not window.showing_life)
    assert not window.journal_button.isVisibleTo(window)


async def test_closing_hides_to_the_tray_and_quit_quits(make, qapp):
    """With a system tray, closing the window leaves Kaden running (a life, a duty,
    background work); the tray brings it back; Quit quits."""
    h: Harness = await make()
    await h.start()
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller, tray=True)
    closed: list[bool] = []
    window.closed.connect(lambda: closed.append(True))
    window.show()
    window.close()
    assert window.isHidden() and closed == []  # hidden, still running
    assert window.tray is not None and window.told_of_tray  # said once, as a tray message
    window.tray.open_action.trigger()
    assert not window.isHidden()
    window.tray.quit_action.trigger()
    assert closed == [True]


async def test_without_a_tray_closing_quits(make, qapp):
    h: Harness = await make()
    await h.start()
    window, _controller, _ = await open_window(h)
    closed: list[bool] = []
    window.closed.connect(lambda: closed.append(True))
    window.close()
    assert closed == [True] and window.tray is None


async def test_a_tools_pictures_show_in_its_card(make, qapp):
    """A picture a tool returns (a QR code to scan, a frame it looked at) is shown in the
    step's card, not only sent to the model."""
    import base64

    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage

    from kaden.api import Image, ToolOutput, ToolSpec

    picture = QImage(40, 30, QImage.Format.Format_RGB32)
    picture.fill(QColor("black"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    picture.save(buffer, "PNG")
    png = base64.b64encode(bytes(buffer.data())).decode()

    async def show(call, ctx):
        return ToolOutput("Scan this code.", attachments=[Image("image/png", png, "qr.png")])

    h: Harness = await make()
    h.tools.add(ToolSpec("show", "Shows a code", {"type": "object", "properties": {}},
                         read_only=True), show)
    h.core.add(Step(calls=[call("show")]), Step(text="There it is."))
    await h.start()
    window, controller, _ = await open_window(h)
    controller.send("show me")
    await eventually(lambda: widgets(window, ToolCard) and not controller.model.busy)
    [card] = widgets(window, ToolCard)
    [shown] = card.pictures
    assert shown.isVisibleTo(card) and not card.details.isVisibleTo(card)  # folded, still seen
    assert shown.pixmap().width() == 40


def test_a_broken_picture_is_left_out_and_a_wide_one_fits(qapp):
    """A tool's picture that is not one (bad data) is skipped without breaking its card;
    a wide one is made small enough to fit."""
    import base64

    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage

    from kaden.api import Image
    from kaden.frontends.desktop.viewmodel import ToolItem
    from kaden.frontends.desktop.window import PICTURE_HEIGHT, PICTURE_WIDTH

    wide = QImage(3000, 200, QImage.Format.Format_RGB32)
    wide.fill(QColor("white"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    wide.save(buffer, "PNG")
    item = ToolItem("c1", "show", pictures=(
        Image("image/png", "not base64 at all!", "bad.png"),
        Image("image/png", base64.b64encode(b"not a picture").decode(), "junk.png"),
        Image("image/png", base64.b64encode(bytes(buffer.data())).decode(), "wide.png")))
    card = ToolCard(item, lambda call_id: None)
    [shown] = card.pictures
    assert shown.pixmap().width() <= PICTURE_WIDTH and shown.pixmap().height() <= PICTURE_HEIGHT


async def test_a_folded_run_of_steps_still_shows_their_pictures(make, qapp):
    """Steps in a row fold into one line; a picture one of them returned (a code to scan)
    shows under that line, not only inside the fold."""
    import base64

    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage

    from kaden.api import Image, ToolOutput, ToolSpec
    from kaden.frontends.desktop.window import StepGroup

    picture = QImage(40, 30, QImage.Format.Format_RGB32)
    picture.fill(QColor("black"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    picture.save(buffer, "PNG")
    png = base64.b64encode(bytes(buffer.data())).decode()

    async def show(call, ctx):
        return ToolOutput("Scan this code.", attachments=[Image("image/png", png, "qr.png")])

    h: Harness = await make()
    h.tools.instant("look")
    h.tools.add(ToolSpec("show", "Shows a code", {"type": "object", "properties": {}},
                         read_only=True), show)
    h.core.add(Step(calls=[call("look")]), Step(calls=[call("show")]), Step(text="Scan it."))
    await h.start()
    window, controller, _ = await open_window(h)
    controller.send("log in")
    await eventually(lambda: not controller.model.busy and len(widgets(window, ToolCard)) == 2)
    [group] = window.conversation._groups.values()
    assert isinstance(group, StepGroup)
    assert not group.body.isVisibleTo(group)  # folded
    [shown] = group.pictures
    assert shown.isVisibleTo(group)
    group.toggle.click()  # unfolded, the card shows it: not twice
    assert not shown.isVisibleTo(group)


async def test_what_the_tools_declare_has_a_page_in_settings(make, qapp):
    from tests.fakes.tools import configurable

    state = ModuleState(tool_settings=tuple(configurable.user_config))
    h: Harness = await make(modules=state)
    await h.start()
    window, controller, _ = await open_window(h)
    dialog = SettingsDialog(window, h.runtime, controller, await h.runtime.settings(), [], 0.0)
    pages = [dialog.nav.item(i).text() for i in range(dialog.nav.count())]
    assert pages.index("Tools") == pages.index("Instructions") + 1
    dialog.show_page("Tools")
    rows = dialog.tool_rows
    assert rows.controls["wake"].currentText() == "From accounts it follows"
    rows.controls["wake"].setCurrentIndex(rows.controls["wake"].findData("nobody"))
    rows.controls["greet"].setChecked(True)
    dialog._save()
    await eventually(lambda: not dialog.isVisible())
    values = {t.name: t.value for t in (await h.runtime.settings()).tool_settings}
    assert values == {"wake": "nobody", "greet": True, "signature": ""}


async def test_tools_that_declare_nothing_have_no_page(make, qapp):
    h: Harness = await make()
    await h.start()
    window, controller, _ = await open_window(h)
    dialog = SettingsDialog(window, h.runtime, controller, await h.runtime.settings(), [], 0.0)
    assert "Tools" not in [dialog.nav.item(i).text() for i in range(dialog.nav.count())]


async def test_in_the_lifes_view_settings_are_its_own(make, qapp):
    """The gear there opens the life's tool settings; saving restarts its process."""
    from tests.fakes.tools import configurable

    h: Harness = await make()
    await h.start()
    them: Harness = await make(modules=ModuleState(
        tool_settings=tuple(configurable.user_config)))
    await them.start()
    life = FakeLife(them.runtime)
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller, life=life)
    asyncio.create_task(controller.run())
    window.continuous_switch.click()
    await eventually(lambda: window.showing_life)
    window.view_gear_button.click()
    await eventually(lambda: isinstance(window._dialog, LifeSettingsDialog)
                     and window._dialog.isVisible())
    dialog = window._dialog
    assert dialog.windowTitle() == "Settings · Kaden"
    assert dialog.name.text() == "Kaden"
    wake = dialog.tool_rows.controls["wake"]
    wake.setCurrentIndex(wake.findData("anyone"))
    dialog._save()
    await eventually(lambda: life.restarted == 1)
    values = {t.name: t.value for t in (await them.runtime.settings()).tool_settings}
    assert values["wake"] == "anyone"
    await eventually(lambda: window.showing_life and window.controller is not window.home)


async def test_the_lifes_daily_amount_is_set_in_its_settings(make, qapp):
    """At once, without a restart; empty for no limit."""
    h: Harness = await make()
    await h.start()
    them: Harness = await make()
    await them.start()
    life = FakeLife(them.runtime)
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller, life=life)
    asyncio.create_task(controller.run())
    window.continuous_switch.click()
    await eventually(lambda: window.showing_life)
    window.open_settings()
    await eventually(lambda: isinstance(window._dialog, LifeSettingsDialog)
                     and window._dialog.isVisible())
    dialog = window._dialog
    assert dialog.daily.text() == "1"
    dialog.daily.setText("2.50")
    dialog._save()
    await eventually(lambda: not dialog.isVisible())
    assert life.daily_usd == 2.5 and life.restarted == 0
    window.open_settings()
    await eventually(lambda: window._dialog is not dialog and window._dialog.isVisible())
    window._dialog.daily.setText("lots")
    window._dialog._save()
    assert window._dialog.isVisible() and window._dialog.error.isVisible()
    window._dialog.daily.setText("")
    window._dialog._save()
    await eventually(lambda: life.daily_usd is None)


async def test_the_lifes_settings_show_their_descriptions_whole(make, qapp):
    from PySide6.QtWidgets import QLabel

    from tests.fakes.tools import configurable

    h: Harness = await make(modules=ModuleState(tool_settings=tuple(configurable.user_config)))
    await h.start()
    window, _, _ = await open_window(h)
    dialog = LifeSettingsDialog(window, window.home, "Ahri", "", await h.runtime.settings(), 1.0)
    dialog.show()
    # A window manager may give it less height than wrapped text needs: Qt's minimum size
    # for a window knows nothing of wrapping.
    dialog.resize(dialog.minimumWidth(), 200)
    for _ in range(3):
        qapp.processEvents()
        await asyncio.sleep(0.02)
    wrapped = [label for label in dialog.findChildren(QLabel)
               if label.wordWrap() and label.isVisible() and label.text()]
    assert wrapped
    for label in wrapped:
        assert label.height() >= label.heightForWidth(label.width()), label.text()
    dialog.close()


async def test_in_the_lifes_view_an_answer_is_once_or_for_good(make, qapp):
    """Its conversation never ends, so "for this conversation" would only last until its
    process restarts: not offered there."""
    h: Harness = await make()
    await h.start()
    them: Harness = await make(permissions=PermissionPolicy.ASK)
    them.tools.instant("BiliMessage", read_only=False)
    them.core.add(Step(calls=[call("BiliMessage", {"to": "900", "text": "hi"})]),
                  Step(text="sent"))
    await them.start()
    life = FakeLife(them.runtime)
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller, life=life)
    asyncio.create_task(controller.run())
    window.continuous_switch.click()
    await eventually(lambda: window.showing_life and window.controller is not window.home)
    window.controller.send("say hi to K5")
    await eventually(lambda: window.conversation.prompts.count() == 1)
    card = window.conversation.prompts.itemAt(0).widget()
    labels = [b.text() for b in card.findChildren(QPushButton)]
    assert labels == ["Allow once", "Always allow BiliMessage", "Deny"]


class FakeTray:
    def __init__(self) -> None:
        self.shown: list[tuple[str, str]] = []

    def showMessage(self, title: str, text: str, *args: Any) -> None:
        self.shown.append((title, text))

    def hide(self) -> None:
        pass


async def test_a_question_waiting_while_you_are_away_is_a_notification(make, qapp):
    """The window not in front: Kaden's question, or the life's, comes as a desktop
    notification, and clicking it shows the question."""
    from kaden.api import Prompt, ToolCall

    h: Harness = await make(permissions=PermissionPolicy.ASK)
    h.tools.instant("Bash", read_only=False)
    h.core.add(Step(calls=[call("Bash", {"command": "git push"})]), Step(text="ok"))
    await h.start()
    them: Harness = await make()
    await them.start()
    life = FakeLife(them.runtime)
    life.name = "Ahri"
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller, life=life)
    tray = window.tray = FakeTray()  # type: ignore[assignment]
    asyncio.create_task(controller.run())
    await eventually(lambda: controller.caught_up(h.runtime.snapshot().seq))
    assert not window.isActiveWindow()  # not on screen: away
    controller.send("push it")
    await eventually(lambda: tray.shown)
    assert tray.shown == [("Kaden needs your answer", "Run a command: git push")]
    life.asked.append(Prompt("p1", "permission", "May I?", call=ToolCall(
        "c1", "BiliMessage", {"to": "900", "text": "hi"})))
    for listener in life.listeners:
        listener()
    await eventually(lambda: len(tray.shown) == 2)
    title, text = tray.shown[1]
    assert title == "Ahri needs your answer" and text
    window.notification_clicked()  # brings their view, where the question is
    await eventually(lambda: window.showing_life)


async def test_always_allow_on_a_chain_names_each_part_it_keeps(make, qapp):
    from kaden.api import PermissionRule

    h: Harness = await make(permissions=PermissionPolicy.ASK)
    h.tools.instant("Bash", read_only=False)
    h.core.add(Step(calls=[call("Bash", {"command": "cp /tmp/a.png b.png && ls -la b.png"})]), Step(text="ok"),
               Step(calls=[call("Bash", {"command": "ls\nrm -rf x"})]), Step(text="ok"))
    await h.start()
    window, controller, _ = await open_window(h)
    controller.send("copy it")
    await eventually(lambda: window.conversation.prompts.count() == 1)
    card = window.conversation.prompts.itemAt(0).widget()
    [always] = buttons(card, "Always allow “cp” and “ls”")
    always.click()
    await eventually(lambda: window.conversation.prompts.count() == 0 and not controller.model.busy)
    rules = (await h.runtime.settings()).permission_rules
    assert rules == (PermissionRule("Bash", "cp"), PermissionRule("Bash", "ls"))
    controller.send("and this")  # what cannot be read with certainty: no "always"
    await eventually(lambda: window.conversation.prompts.count() == 1)
    labels = [b.text() for b in window.conversation.prompts.itemAt(0).widget().findChildren(QPushButton)]
    assert labels == ["Allow once", "Allow for this conversation", "Deny"]


async def test_the_lifes_saved_answers_are_in_its_settings(make, qapp):
    """Its own, apart from the chats': seen and taken back there, at once."""
    from kaden.api import PermissionRule, SettingsUpdate

    h: Harness = await make()
    await h.start()
    them: Harness = await make()
    await them.start()
    await them.runtime.update_settings(SettingsUpdate(permission_rules=[
        PermissionRule("BiliMessage"), PermissionRule("Bash", "cp")]))
    life = FakeLife(them.runtime)
    controller = Controller(h.runtime, ConversationModel())
    window = MainWindow(h.runtime, controller, life=life)
    asyncio.create_task(controller.run())
    window.continuous_switch.click()
    await eventually(lambda: window.showing_life)
    window.open_settings()
    dialog = await eventually(lambda: isinstance(window._dialog, LifeSettingsDialog)
                              and window._dialog.isVisible() and window._dialog)
    assert [dialog.answers.rules.item(i).text()
            for i in range(dialog.answers.rules.count())] == [
        "Always allow: BiliMessage", "Always allow: Bash: cp"]
    dialog.answers.rules.setCurrentRow(1)
    dialog.answers.remove.click()
    dialog._save()
    await eventually(lambda: not dialog.isVisible())
    assert (await them.runtime.settings()).permission_rules == (PermissionRule("BiliMessage"),)
    assert life.restarted == 0  # at once


def test_a_reply_takes_the_colours_of_a_new_theme(qapp, system_theme):
    from kaden.frontends.desktop import syntax

    theme.set_theme(qapp, "light")
    bubble = AssistantBubble(AssistantItem(
        text="Call `area`.\n\n```python\ndef area(r):\n    pass\n```", final=True))
    assert colours_of(bubble)["def"] == syntax.colors(dark=False)["keyword"].lower()
    theme.set_theme(qapp, "dark")
    qapp.processEvents()
    assert colours_of(bubble)["def"] == syntax.colors(dark=True)["keyword"].lower()
    inline = next(f for f in _fragments(bubble) if f.text() == "area")
    assert inline.charFormat().background().color().name() == theme.current().select.lower()


def _fragments(bubble: AssistantBubble) -> list[Any]:
    found = []
    block = bubble.body.document().begin()
    while block.isValid():
        fragments = block.begin()
        while not fragments.atEnd():
            found.append(fragments.fragment())
            fragments += 1
        block = block.next()
    return found


def test_a_spinner_turns_only_while_it_shows(qapp):
    from kaden.frontends.desktop.window import Spinner

    spinner = Spinner()
    assert not spinner._timer.isActive()
    spinner.show()
    assert spinner._timer.isActive()
    spinner.hide()
    assert not spinner._timer.isActive()


async def test_pictures_pasted_at_once_are_each_kept(make, qapp):
    from PySide6.QtGui import QImage

    h: Harness = await make()
    await h.start()
    window, _, _ = await open_window(h)
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    for _ in range(4):
        window._paste_image(image)
    await eventually(lambda: len(window.attachments.paths()) == 4)
    assert len(set(window.attachments.paths())) == 4


def test_a_streaming_reply_renders_each_finished_part_once(qapp, monkeypatch):
    rendered: list[str] = []
    original = window_module.MarkdownView.setMarkdown

    def counting(self: Any, markdown: str) -> None:
        rendered.append(markdown)
        original(self, markdown)

    monkeypatch.setattr(window_module.MarkdownView, "setMarkdown", counting)
    item = AssistantItem(text="")
    bubble = AssistantBubble(item)
    text = ""
    for part in ("First paragraph.\n\n", "```python\nx = 1\n\ny = 2\n```\n\n",
                 "Third para", "graph.\n\nFourth"):
        text += part
        item.text = text
        bubble._render()
    assert sum("First paragraph." in markdown for markdown in rendered) == 1
    assert sum("y = 2" in markdown for markdown in rendered) == 1  # a fence is one part
    assert "Fourth" in bubble.body.toPlainText()
    item.final = True
    bubble._render()  # once it is whole: all of it, in one view
    assert bubble.parts == [] and "First paragraph." in bubble.body.toPlainText()
    assert "Fourth" in bubble.body.toPlainText()


async def test_the_window_paints_its_icons_only_when_they_change(make, qapp, monkeypatch):
    h: Harness = await make()
    await h.start()
    window, _, _ = await open_window(h)
    painted: list[str] = []
    original = theme.icon

    def counting(name: str, *args: Any, **kwargs: Any) -> Any:
        painted.append(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(theme, "icon", counting)
    window._refresh_status()
    window._show_voice()
    assert painted == []  # nothing changed since they were painted


async def test_in_the_lifes_view_a_change_updates_only_what_it_does(make, qapp, monkeypatch):
    from kaden.frontends.desktop.viewmodel import PromptsChanged

    h: Harness = await make()
    await h.start()
    window, _, _ = await open_window(h)
    window._life_on = True
    refreshed: list[bool] = []
    monkeypatch.setattr(window, "_refresh_status", lambda: refreshed.append(True))
    window._pane_changed(PromptsChanged())
    assert refreshed == []
