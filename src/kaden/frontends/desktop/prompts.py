"""Cards for what waits on the user: a permission, a question, a plan to approve, a
proposed call.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
)

from kaden.api import Decision, PermissionPolicy, Prompt
from kaden.frontends.common import (
    PERMISSION_LABELS,
    call_parts,
    proposal_parts,
    request_note,
    tool_sentence,
)
from kaden.frontends.desktop.messages import MarkdownView
from kaden.frontends.desktop.widgets import Detail, primary, text_label

LABEL_CHARS = 60  # a button's text, longer cut with "…" (all of it in its tooltip)


def _cut(text: str) -> str:
    return text if len(text) <= LABEL_CHARS else text[:LABEL_CHARS - 1].rstrip() + "…"


def _kept_label(rules: Sequence[str]) -> str:
    """Saved answers in words: “cp”, “echo” and “ls”; a tool by its name."""
    names = [f"“{rule.partition(': ')[2]}”" if ": " in rule else rule for rule in rules]
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


class PermissionCard(QFrame):
    """Allow once, for the conversation (not in the life's view, whose conversation never
    ends), always, or deny; never allow is on Deny's menu, a step away from always."""

    def __init__(self, request: Prompt, resolve: Callable[[str, Any], None], *,
                 per_conversation: bool = True) -> None:
        super().__init__()
        self.setObjectName("prompt")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 12, 10)
        call = request.call
        assert call is not None
        headline, detail = call_parts(call.arguments)
        headline = headline or tool_sentence(call.name, call.arguments,
                                             request.details.get("title"))
        layout.addWidget(text_label(f"Kaden wants to: {headline}", role="strong"))
        layout.addWidget(text_label(request_note(request), tone="muted"))
        if detail:
            layout.addWidget(Detail(detail))
        # Always and never are kept for every conversation: what the runtime chose to keep.
        always = _kept_label(request.details.get("always") or [])
        never = _kept_label(request.details.get("never") or [])
        answers = [(Decision.ALLOW_ONCE, "Allow once")]
        if per_conversation:
            answers.append((Decision.ALLOW_CONVERSATION, "Allow for this conversation"))
        if always:
            answers.append((Decision.ALLOW_ALWAYS, f"Always allow {always}"))
        answers.append((Decision.DENY, "Deny"))
        buttons = QHBoxLayout()
        for decision, label in answers:
            button = QPushButton(_cut(label))
            button.setToolTip(label if button.text() != label else "")
            if decision is Decision.ALLOW_ONCE:
                primary(button)
            button.clicked.connect(lambda _=False, d=decision: self._answer(request.id, d))
            buttons.addWidget(button)
        # Deny stays one click; never allow is behind ▾ beside it.
        self.more_deny = QToolButton(text="▾")
        self.more_deny.setToolTip("More ways to deny")
        self.more_deny.setAccessibleName("More ways to deny")
        self.more_deny.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.more_deny)
        menu.addAction(_cut(f"Never allow {never}")).triggered.connect(
            lambda: self._answer(request.id, Decision.DENY_ALWAYS))
        self.more_deny.setMenu(menu)
        buttons.addWidget(self.more_deny)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.resolve = resolve
        self.setAccessibleName(f"Permission request for {call.name}")

    def _answer(self, request_id: str, decision: Decision) -> None:
        self.setEnabled(False)  # first-wins; the card goes when PromptClosed arrives
        self.resolve(request_id, decision)


class QuestionCard(QFrame):
    """The agent's questions, which it waits on: each with its options, one to pick or, for
    a multiple choice, any; Send once each has an answer. A reply typed instead answers
    them in words."""

    def __init__(self, asked: Prompt, answer: Callable[[str, Any], None]) -> None:
        super().__init__()
        self.setObjectName("prompt")
        self.asked, self.answer = asked, answer
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 12, 10)
        self.choices: list[tuple[str, list[QPushButton]]] = []
        for question in asked.details.get("questions", ()):
            header = str(question.get("header") or "")
            text = str(question.get("question") or "")
            layout.addWidget(text_label(f"{header}: {text}" if header else text, role="strong"))
            row = QHBoxLayout()
            buttons = []
            many = bool(question.get("multiSelect"))
            for option in question.get("options") or ():
                button = QPushButton(str(option.get("label", "")))
                button.setCheckable(True)
                if option.get("description"):
                    button.setToolTip(str(option["description"]))
                button.toggled.connect(lambda on, b=button, many=many: self._toggled(b, on, many))
                buttons.append(button)
                row.addWidget(button)
            row.addStretch()
            layout.addLayout(row)
            self.choices.append((text, buttons))
        bottom = QHBoxLayout()
        self.send = primary(QPushButton("Send"))
        self.send.setEnabled(False)
        self.send.clicked.connect(self._send)
        bottom.addWidget(self.send)
        bottom.addWidget(text_label("or type your answer", tone="muted"))
        bottom.addStretch()
        layout.addLayout(bottom)
        self.setAccessibleName("The agent's questions")

    def _toggled(self, button: QPushButton, on: bool, many: bool) -> None:
        if on and not many:
            for _, buttons in self.choices:
                if button in buttons:
                    for other in buttons:
                        if other is not button and other.isChecked():
                            other.setChecked(False)
        self.send.setEnabled(all(any(b.isChecked() for b in buttons)
                                 for _, buttons in self.choices))

    def _send(self) -> None:
        self.setEnabled(False)  # the card goes when PromptClosed arrives
        self.answer(self.asked.id, {
            text: ", ".join(b.text() for b in buttons if b.isChecked())
            for text, buttons in self.choices})


class PlanApprovalCard(QFrame):
    """The plan the agent waits to have approved (ExitPlanMode): Approve, Approve and… a
    permission level for what follows, or Keep planning. A reply typed instead sends it
    back with that reply."""

    PLAN_HEIGHT = 360  # past this, the plan scrolls

    def __init__(self, proposed: Prompt, resolve: Callable[[str, Any], None]) -> None:
        super().__init__()
        self.setObjectName("prompt")
        self.resolve, self.prompt_id = resolve, proposed.id
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 12, 10)
        layout.addWidget(text_label("Kaden's plan", role="strong"))
        self.plan = MarkdownView()
        self.plan.setMarkdown(str(proposed.details.get("plan", "")))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMaximumHeight(self.PLAN_HEIGHT)
        scroll.setWidget(self.plan)
        layout.addWidget(scroll)
        buttons = QHBoxLayout()
        self.approve = primary(QPushButton("Approve"))
        self.approve.clicked.connect(lambda: self._answer(True))
        buttons.addWidget(self.approve)
        self.approve_and = QPushButton("Approve and…")
        menu = QMenu(self.approve_and)
        self.levels: dict[PermissionPolicy, QAction] = {}
        for policy, label in PERMISSION_LABELS.items():
            if policy is PermissionPolicy.DENY:
                continue
            action = menu.addAction(label)
            action.triggered.connect(lambda _=False, p=policy: self._answer(True, p))
            self.levels[policy] = action
        self.approve_and.setMenu(menu)
        buttons.addWidget(self.approve_and)
        self.keep = QPushButton("Keep planning")
        self.keep.clicked.connect(lambda: self._answer(False))
        buttons.addWidget(self.keep)
        buttons.addWidget(text_label("or type what to change", tone="muted"))
        buttons.addStretch()
        layout.addLayout(buttons)
        self.setAccessibleName("The agent's plan")

    def _answer(self, approve: bool, permissions: PermissionPolicy | None = None) -> None:
        self.setEnabled(False)  # the card goes when PromptClosed arrives
        self.resolve(self.prompt_id, {"approve": approve, "permissions":
                                      permissions.value if permissions else None})


class ProposalCard(QFrame):
    """A call the agent proposed: it runs only if the user approves it."""

    def __init__(self, proposal: Prompt, resolve: Callable[[str, Any], None]) -> None:
        super().__init__()
        self.setObjectName("prompt")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 12, 10)
        headline, detail = proposal_parts(proposal)
        layout.addWidget(text_label(headline, role="strong"))
        layout.addWidget(text_label(str(proposal.details.get("reason", "")), tone="muted"))
        if detail:
            layout.addWidget(Detail(detail))
        buttons = QHBoxLayout()
        approve = primary(QPushButton("Approve"))
        dismiss = QPushButton("Dismiss")
        approve.clicked.connect(lambda: self._answer(proposal.id, True))
        dismiss.clicked.connect(lambda: self._answer(proposal.id, False))
        buttons.addWidget(approve)
        buttons.addWidget(dismiss)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.resolve = resolve
        self.setAccessibleName(f"Proposal: {headline}")

    def _answer(self, proposal_id: str, accept: bool) -> None:
        self.setEnabled(False)  # the card goes when PromptClosed arrives
        self.resolve(proposal_id, accept)


class AnyPromptCard(QFrame):
    """Something the agent waits on that this app has no card for: what it asks, and a
    reply in words."""

    def __init__(self, prompt: Prompt, resolve: Callable[[str, Any], None]) -> None:
        super().__init__()
        self.setObjectName("prompt")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 12, 10)
        layout.addWidget(text_label(prompt.text, role="strong"))
        row = QHBoxLayout()
        self.reply = QLineEdit()
        self.reply.setPlaceholderText("Your answer")
        send = primary(QPushButton("Send"))
        send.clicked.connect(lambda: self._answer(prompt.id, resolve))
        self.reply.returnPressed.connect(send.click)
        row.addWidget(self.reply, 1)
        row.addWidget(send)
        layout.addLayout(row)
        self.setAccessibleName(prompt.text)

    def _answer(self, prompt_id: str, resolve: Callable[[str, Any], None]) -> None:
        if self.reply.text().strip():
            self.setEnabled(False)
            resolve(prompt_id, self.reply.text().strip())
