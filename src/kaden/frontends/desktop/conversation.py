"""One conversation on screen: its messages, steps and prompts, laid out from the view
model.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from functools import partial

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from kaden.api import PromptKind
from kaden.frontends.desktop.messages import (
    AssistantBubble,
    NoticeLine,
    PlanCard,
    StepGroup,
    ToolCard,
    UserBubble,
    WrapUpCard,
    duration,
)
from kaden.frontends.desktop.prompts import (
    AnyPromptCard,
    PermissionCard,
    PlanApprovalCard,
    ProposalCard,
    QuestionCard,
)
from kaden.frontends.desktop.viewmodel import (
    AssistantItem,
    Change,
    ChecklistItem,
    Controller,
    ConversationModel,
    Inserted,
    Item,
    NoticeItem,
    PromptsChanged,
    Removed,
    ToolItem,
    Updated,
    UserItem,
    step_segments,
)
from kaden.frontends.desktop.widgets import COLUMN_WIDTH, Detail, detach, discard, place, text_label


def is_long(text: str) -> bool:
    return "\n" in text or len(text) > 160


# ── the conversation ──────────────────────────────────────────────────────


class ConversationView(QScrollArea):
    """A conversation as its ConversationModel holds it: its items, grouped into steps,
    the line that says the agent thinks, and in `prompts` what waits for an answer, which
    the window that hosts the view places where the person answers. The host passes on
    what changes in the model (`changed`; `rebuild` when all of it did, `refresh` for how
    it stands). `controller` gives the conversation on screen, which may change."""

    chose = Signal()  # an option of a card was clicked

    def __init__(self, controller: Callable[[], Controller], empty_state: QWidget,
                 notice_link: Callable[[str], None]) -> None:
        super().__init__(widgetResizable=True)
        self._controller, self._notice_link = controller, notice_link
        self._widgets: dict[int, QWidget] = {}  # by id() of its item
        self._groups: dict[int, StepGroup] = {}  # by id() of its first step
        self._arrange_pending = False
        # Whether an answer can be kept for the conversation: not on the life's, which
        # never ends (MainWindow sets it).
        self.per_conversation = True
        self.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget(objectName="transcript")
        centred = QHBoxLayout(content)
        centred.setContentsMargins(24, 16, 24, 24)
        reading = QWidget()
        reading.setMaximumWidth(COLUMN_WIDTH)
        centred.addWidget(reading)
        outer = QVBoxLayout(reading)
        outer.setContentsMargins(0, 0, 0, 0)
        self.load_earlier = QPushButton("Load earlier messages")
        self.load_earlier.clicked.connect(self._load_earlier)
        self.load_earlier.hide()
        self.items_layout = QVBoxLayout()
        self.items_layout.setSpacing(12)
        self.empty_state = empty_state  # what shows where nothing was said yet
        outer.addWidget(self.load_earlier, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addLayout(self.items_layout)
        self.waiting_line = text_label("Thinking…", selectable=False, tone="muted")
        self.waiting_line.hide()
        self.waiting_timer = QTimer(self, interval=1000)  # its seconds, as they pass
        self.waiting_timer.timeout.connect(self.tick_waiting)
        outer.addWidget(self.waiting_line)
        outer.addWidget(self.empty_state, 1)
        outer.addStretch()
        self.setWidget(content)
        self._at_end = True
        bar = self.verticalScrollBar()
        bar.valueChanged.connect(lambda v: setattr(self, "_at_end", v >= bar.maximum() - 4))
        bar.rangeChanged.connect(lambda _lo, hi: self._at_end and bar.setValue(hi))
        self.prompts = QVBoxLayout()
        self.prompts.setContentsMargins(0, 0, 0, 8)

    @property
    def controller(self) -> Controller:
        return self._controller()

    @property
    def model(self) -> ConversationModel:
        return self.controller.model

    def changed(self, change: Change) -> None:
        match change:
            case Inserted(item=item):
                self._widgets[id(item)] = self._widget_for(item)
                self._arrange_soon()
                self._show_empty_state()
            case Updated(item=item):
                widget = self._widgets[id(item)]
                widget.refresh()  # type: ignore[attr-defined]
                if isinstance(item, AssistantItem) and item.shown == widget.isHidden():
                    self._arrange_soon()
                if isinstance(group := self._group_of(widget), StepGroup):
                    if isinstance(item, AssistantItem) and (item.text or item.refused):
                        self._arrange_soon()  # a reply now: out of the group
                    else:
                        group.refresh()
            case Removed(item=item):
                if (widget := self._widgets.pop(id(item), None)) is not None:
                    detach(widget)
                    discard(widget)
                self._arrange_soon()
                self._show_empty_state()
            case PromptsChanged():
                self._rebuild_prompts()

    def rebuild(self) -> None:
        """Draw the model's conversation from nothing."""
        for widget in (*self._widgets.values(), *self._groups.values()):
            discard(widget)
        self._widgets.clear()
        self._groups.clear()
        for item in self.model.items:
            self._widgets[id(item)] = self._widget_for(item)
        self._arrange()
        self._at_end = True
        self._rebuild_prompts()

    def to_end(self) -> None:
        """Show the latest of the conversation, and stay with it as it grows."""
        self._at_end = True
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def refresh(self) -> None:
        """How the conversation stands: whether the agent thinks, whether there is more
        above, and whether nothing was said yet."""
        self.waiting_line.setVisible(self.model.waiting)
        self.tick_waiting()
        self._show_empty_state()
        self.load_earlier.setVisible(self.model.has_earlier)
        self.load_earlier.setEnabled(True)

    def tick_waiting(self) -> None:
        """"Thinking… 4s" while the model is asked and nothing of it shows, counting."""
        if not self.model.waiting:
            self.waiting_timer.stop()
            return
        since = self.model.thinking_since
        elapsed = time.monotonic() - since if since is not None else 0.0
        self.waiting_line.setText(
            f"Thinking… {duration(elapsed)}" if elapsed >= 1 else "Thinking…")
        if not self.waiting_timer.isActive():
            self.waiting_timer.start()

    def _widget_for(self, item: Item) -> QWidget:
        match item:
            case UserItem():
                return UserBubble(item)
            case AssistantItem():
                return AssistantBubble(item)
            case ToolItem():
                return ToolCard(item, self.controller.cancel)
            case ChecklistItem():
                return PlanCard(item)
            case NoticeItem() if item.link is None and is_long(item.text):
                return Detail(item.text, lines=1, chars=140)  # a new goal's brief
            case NoticeItem():
                line = NoticeLine(item)
                line.linkActivated.connect(self._notice_link)
                return line

    def _arrange_soon(self) -> None:
        """Arrange once the current burst of changes is in (a page of history, a commit
        that replaces what streamed), not once per item."""
        if not self._arrange_pending:
            self._arrange_pending = True
            asyncio.get_running_loop().call_soon(self._arrange)

    def _arrange(self) -> None:
        """Lay the item widgets out as step_segments groups them, keeping each group (and
        whether it is open) for as long as its first step stays first, and show each item
        that has something to draw: the one place that decides what of the conversation
        is visible."""
        self._arrange_pending = False
        groups: dict[int, StepGroup] = {}
        for index, segment in enumerate(step_segments(self.model.items)):
            if isinstance(segment, list):
                key = id(next(item for item in segment if isinstance(item, ToolItem)))
                group = self._groups.get(key) or StepGroup(self.controller.cancel)
                groups[key] = group
                group.set_members([self._widgets[id(item)] for item in segment])
                place(self.items_layout, index, group)
                group.show()
            else:
                place(self.items_layout, index, self._widgets[id(segment)])
        for item in self.model.items:
            self._widgets[id(item)].setVisible(
                not isinstance(item, AssistantItem) or item.shown)
        for key, group in self._groups.items():
            if key not in groups:
                detach(group)
                discard(group)
        self._groups = groups

    @staticmethod
    def _group_of(widget: QWidget) -> QWidget | None:
        body = widget.parentWidget()
        return body.parentWidget() if body is not None else None

    def set_per_conversation(self, on: bool) -> None:
        """Whether a permission card offers "this conversation" answers (not in the
        life's view, whose one conversation is its whole life)."""
        if self.per_conversation != on:
            self.per_conversation = on
            self._rebuild_prompts()

    def _rebuild_prompts(self) -> None:
        while (child := self.prompts.takeAt(0)) is not None:
            if child.widget() is not None:
                discard(child.widget())
        respond = self.controller.respond
        permission = partial(PermissionCard, per_conversation=self.per_conversation)
        cards = {PromptKind.QUESTION: QuestionCard, PromptKind.PLAN: PlanApprovalCard,
                 PromptKind.PERMISSION: permission, PromptKind.PROPOSAL: ProposalCard}
        for prompt in self.model.prompts.values():
            if prompt.kind == PromptKind.PROPOSAL and prompt.details.get("card"):
                continue  # shown on its wrap-up card
            self.prompts.addWidget(cards.get(prompt.kind, AnyPromptCard)(prompt, respond))
        if (card := self.model.card) is not None:
            self.prompts.addWidget(WrapUpCard(card, self.model.prompts, self._choose,
                                              self.model.close_card))

    def _choose(self, index: int) -> None:
        if (card := self.model.card) is None:
            return
        if card.runs(index, self.model.prompts):
            self.controller.respond(card.call_id, True)
            self.model.close_card()
        else:
            self.controller.send(card.options[index])
        self.chose.emit()

    def _show_empty_state(self) -> None:
        self.empty_state.setVisible(self.model.conversation is not None and not self.model.items
                                    and not self.model.has_earlier)

    def _load_earlier(self) -> None:
        self.load_earlier.setEnabled(False)
        self._at_end = False
        self.controller.load_earlier()
