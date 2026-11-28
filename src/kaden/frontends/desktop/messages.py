"""What the conversation shows: messages rendered from markdown, thinking, tool steps
and their groups, notices, the plan and the wrap-up card.
"""

from __future__ import annotations

import base64
import html
import re
import time
from collections.abc import Callable, Container, Sequence
from functools import partial
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFont,
    QPainter,
    QPaintEvent,
    QPixmap,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
    QTextFrame,
    QTextFrameFormat,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from kaden.api import Image, ToolStatus
from kaden.frontends.common import Card
from kaden.frontends.desktop import syntax, theme
from kaden.frontends.desktop.viewmodel import (
    AssistantItem,
    ChecklistItem,
    NoticeItem,
    ToolItem,
    UserItem,
    group_heading,
    relative_age,
)
from kaden.frontends.desktop.widgets import (
    ElidedLabel,
    Spinner,
    discard,
    fitted,
    place,
    primary,
    restyled,
    text_label,
)

LINE_HEIGHT_PERCENT = 118  # reply text, of the font's line spacing (itself ~1.35 em)
CODE_SCALE = 0.88  # inline code, of the text size: monospace looks larger at the same size
PARAGRAPH_SPACING = 10  # px below a reply paragraph
CODE_PADDING = 10  # px around the lines of a code block, inside its box
CODE_HEADER = 22  # px above them, for the block's language and its copy button
CODE_RADIUS = 8  # px, the corners of the box
TURN_GAP = 18  # px more below your message, before Kaden's reply, than between its steps
LIST_ITEM_SPACING = 4  # px below a list item
PICTURE_HEIGHT = 240  # px, at most, of a picture in a tool's card: a QR code scans at it
PICTURE_WIDTH = 480  # px, at most: a wide one (a screenshot) fits the card


HEADING_SPACING = 6  # px above a heading, on top of the paragraph below the previous block
RENDER_INTERVAL_MS = 100  # Markdown of a streaming reply is re-rendered at most this often


SHOWN_IMAGE = 260  # px: a picture in a message you sent, at most
BUBBLE_SHARE = 0.75  # a message of yours takes at most this share of the transcript's width


COPIED_MS = 1500  # a reply's copy button shows a tick this long


TOOL_TONES = {ToolStatus.OK: "ok", ToolStatus.ERROR: "error", ToolStatus.ABANDONED: "warning",
              ToolStatus.TIMED_OUT: "warning", ToolStatus.INTERRUPTED: "warning"}


def _duration(seconds: float) -> str:
    """"4s", "1m 5s"."""
    minutes, rest = divmod(max(1, round(seconds)), 60)
    return f"{minutes}m {rest}s" if minutes else f"{rest}s"


def _thought(seconds: float | None) -> str:
    """The thinking line once the thinking is over: "Thought for 4s"."""
    return "Thought" if seconds is None else f"Thought for {_duration(seconds)}"


# ── transcript widgets ────────────────────────────────────────────────────


_FENCE = re.compile(r"^( *)(`{3,}|~{3,})(.*)$")


def _code_blocks_apart(markdown: str) -> str:
    """The Markdown with a comment between code blocks that follow each other: Qt makes
    one run of lines of them, with nothing to tell where one ends."""
    lines: list[str] = []
    fence = ""  # the fence of the code block these lines are in
    closed = False  # a code block ended and nothing but blank lines came since
    for line in markdown.split("\n"):
        found = _FENCE.match(line)
        if fence:
            if (found and found[2][0] == fence[0] and len(found[2]) >= len(fence)
                    and not found[3].strip()):
                fence, closed = "", True
        elif found:
            if closed:
                lines += [f"{found[1]}<!-- -->", ""]
            fence, closed = found[2], False
        elif line.strip():
            closed = False
        lines.append(line)
    return "\n".join(lines)


class MarkdownView(QTextBrowser):
    """Read-only Markdown that grows to fit its text instead of scrolling."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("reply")
        self.setOpenExternalLinks(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.viewport().setAutoFillBackground(False)
        self.document().setDocumentMargin(0)
        self.document().setIndentWidth(20)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)
        self._boxes: list[tuple[QTextFrame, str]] = []  # each code block and its language
        self.code_buttons: list[QToolButton] = []  # one per code block; spares are hidden
        self._markdown = ""
        self._painted: theme.Palette | None = None  # the colours it was rendered in

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if restyled(self, event):
            self.setMarkdown(self._markdown)

    def setMarkdown(self, markdown: str) -> None:
        """Markdown with room to breathe: taller lines, space between paragraphs and list
        items, more above headings; inline code a little smaller, on a soft background; a
        code block in one round box under its language and a copy button, its lines
        together and wrapped when long."""
        self._markdown, self._painted = markdown, theme.current()
        self._boxes = []
        super().setMarkdown(_code_blocks_apart(markdown))
        code = QTextCharFormat()
        code.setFontPointSize(self.font().pointSizeF() * CODE_SCALE)
        inline = QTextCharFormat(code)
        inline.setBackground(QColor(theme.current().select))
        boxes: list[list[Any]] = []  # each code block: its start, end and language
        previous_fenced = False
        block = self.document().begin()
        while block.isValid():
            fragments = block.begin()
            spans = []
            while not fragments.atEnd():
                fragment = fragments.fragment()
                if fragment.charFormat().fontFixedPitch():
                    spans.append((fragment.position(), fragment.length()))
                fragments += 1
            for start, length in spans:
                cursor = QTextCursor(self.document())
                cursor.setPosition(start)
                cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)
                cursor.mergeCharFormat(code if block.blockFormat().nonBreakableLines()
                                       else inline)
            fmt = block.blockFormat()
            fenced = fmt.hasProperty(QTextFormat.Property.BlockCodeFence)
            if fenced:  # Qt makes a block of every line
                end = block.position() + block.length() - 1
                language = fmt.stringProperty(QTextFormat.Property.BlockCodeLanguage)
                if previous_fenced and boxes[-1][2] == language:
                    boxes[-1][1] = end
                else:
                    boxes.append([block.position(), end, language])
                fmt.setNonBreakableLines(False)
                fmt.setTopMargin(0)
                fmt.setBottomMargin(0)
                QTextCursor(block).setBlockFormat(fmt)
                previous_fenced = True
                block = block.next()
                continue
            if previous_fenced and not block.text():  # the comment between two code blocks
                fmt.setLineHeight(1, QTextBlockFormat.LineHeightTypes.FixedHeight.value)
                fmt.setTopMargin(0)
                fmt.setBottomMargin(0)
                QTextCursor(block).setBlockFormat(fmt)
                previous_fenced = False
                block = block.next()
                continue
            previous_fenced = False
            fmt.setLineHeight(LINE_HEIGHT_PERCENT,
                              QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            if block.textList() is not None:
                fmt.setTopMargin(0)
                fmt.setBottomMargin(LIST_ITEM_SPACING)
            else:
                fmt.setBottomMargin(PARAGRAPH_SPACING)
            if fmt.headingLevel():
                fmt.setTopMargin(HEADING_SPACING)
            QTextCursor(block).setBlockFormat(fmt)
            block = block.next()
        box = QTextFrameFormat()  # its background is painted here, for the round corners
        box.setPadding(CODE_PADDING)
        box.setTopMargin(CODE_HEADER)  # inside the painted box: the frame's whole rectangle
        for start, end, language in reversed(boxes):  # from the end: a frame moves the rest
            cursor = QTextCursor(self.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            frame = cursor.insertFrame(box)
            self._boxes.insert(0, (frame, language))
            self._colour(frame, language)
            # Qt puts an empty line before and after a frame: the one before is kept from
            # showing, the one after is the space below the box.
            for position, height in ((frame.firstPosition() - 1, 1),
                                     (frame.lastPosition() + 1, PARAGRAPH_SPACING)):
                edge = self.document().findBlock(position)
                if edge.isValid() and not edge.text():
                    flat = QTextBlockFormat()
                    flat.setLineHeight(height,
                                       QTextBlockFormat.LineHeightTypes.FixedHeight.value)
                    QTextCursor(edge).setBlockFormat(flat)
        while len(self.code_buttons) < len(self._boxes):
            button = QToolButton(self.viewport(), objectName="codeCopy")
            button.setAccessibleName("Copy code")
            button.setIconSize(QSize(14, 14))
            button.setCursor(Qt.CursorShape.ArrowCursor)
            button.clicked.connect(partial(self._copy_code, len(self.code_buttons)))
            self.code_buttons.append(button)
        for index, button in enumerate(self.code_buttons):
            self._paint_copy(button)
            button.setVisible(index < len(self._boxes))
        self._place_buttons()

    def _code(self, frame: QTextFrame) -> str:
        cursor = QTextCursor(self.document())
        cursor.setPosition(frame.firstPosition())
        cursor.setPosition(frame.lastPosition(), QTextCursor.MoveMode.KeepAnchor)
        return cursor.selectedText().replace("\u2029", "\n")  # Qt's paragraph separator

    def _colour(self, frame: QTextFrame, language: str) -> None:
        """Syntax colours for a code block whose language is named and known."""
        colors = theme.code_colours()
        for start, length, kind in syntax.spans(self._code(frame), language):
            cursor = QTextCursor(self.document())
            cursor.setPosition(frame.firstPosition() + start)
            cursor.setPosition(frame.firstPosition() + start + length,
                               QTextCursor.MoveMode.KeepAnchor)
            colored = QTextCharFormat()
            colored.setForeground(QColor(colors[kind]))
            cursor.mergeCharFormat(colored)

    def code_boxes(self) -> list[QRect]:
        """Where each code block's box is, in the view."""
        layout = self.document().documentLayout()
        return [layout.frameBoundingRect(frame).toAlignedRect() for frame, _ in self._boxes]

    def code_languages(self) -> list[str]:
        return [language for _, language in self._boxes]

    def _place_buttons(self) -> None:
        for box, button in zip(self.code_boxes(), self.code_buttons, strict=False):
            size = button.sizeHint()
            button.setGeometry(box.right() - size.width() - 5, box.top() + 4,
                               size.width(), size.height())

    def _copy_code(self, index: int) -> None:
        QApplication.clipboard().setText(self._code(self._boxes[index][0]))
        button = self.code_buttons[index]
        self._paint_copy(button, copied=True)
        QTimer.singleShot(COPIED_MS, button, partial(self._paint_copy, button))

    def _paint_copy(self, button: QToolButton, copied: bool = False) -> None:
        button.setIcon(theme.icon("check" if copied else "copy", theme.current().muted, 14))
        button.setToolTip("Copied" if copied else "Copy")

    def paintEvent(self, event: QPaintEvent) -> None:
        """The boxes of the code blocks and their languages, under the text."""
        colors = theme.current()
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        small = QFont(self.font())
        small.setPointSizeF(self.font().pointSizeF() * CODE_SCALE)
        painter.setFont(small)
        for box, language in zip(self.code_boxes(), self.code_languages(), strict=True):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors.select))
            painter.drawRoundedRect(box, CODE_RADIUS, CODE_RADIUS)
            painter.setPen(QColor(colors.muted))
            painter.drawText(box.adjusted(CODE_PADDING + 1, 4, 0, 0).topLeft()
                             + QPoint(0, painter.fontMetrics().ascent() + 2), language)
        painter.end()
        super().paintEvent(event)

    def _fit(self, *_: object) -> None:
        self.document().setTextWidth(self.viewport().width())
        self.setFixedHeight(int(self.document().size().height()) + 4)
        self._place_buttons()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._fit()


class UserBubble(QWidget):
    """Your message: an accent bubble on the right."""

    def __init__(self, item: UserItem) -> None:
        super().__init__()
        self.item = item
        row = QHBoxLayout(self)
        row.setContentsMargins(0, TURN_GAP // 3, 0, TURN_GAP)  # a turn starts here
        row.addStretch(1)
        self.bubble = QFrame(objectName="user")
        layout = QVBoxLayout(self.bubble)
        layout.setContentsMargins(12, 8, 12, 8)
        self.images: list[QLabel] = []
        self.files: list[QLabel] = []
        for attached in item.attachments:
            if isinstance(attached, Image):
                picture = QPixmap()
                picture.loadFromData(base64.b64decode(attached.data))
                if not picture.isNull():
                    shown = QLabel()
                    shown.setPixmap(fitted(picture, SHOWN_IMAGE))
                    shown.setAccessibleName(f"Image {attached.name}".strip())
                    self.images.append(shown)
                    layout.addWidget(shown)
                    continue
            name = attached.name if isinstance(attached, Image) else Path(attached.path).name
            chip = text_label(name, role="small", selectable=False)
            chip.setToolTip("" if isinstance(attached, Image) else attached.path)
            self.files.append(chip)
            layout.addWidget(chip)
        self.text = text_label()
        layout.addWidget(self.text)
        row.addWidget(self.bubble)
        self.refresh()

    def refresh(self) -> None:
        self.text.setText(self.item.text + ("\n(queued)" if self.item.queued else ""))
        self.text.setVisible(bool(self.text.text()))
        self.setEnabled(not self.item.queued)
        self.setAccessibleName("You" + (", queued" if self.item.queued else ""))
        self._fit()

    def _fit(self) -> None:
        """As wide as its longest line, up to BUBBLE_SHARE of the transcript."""
        widest = int(self.width() * BUBBLE_SHARE)
        margins = self.bubble.layout().contentsMargins()  # the padding around the text
        natural = max(self.text.fontMetrics().horizontalAdvance(line)
                      for line in self.text.text().split("\n")) + 2
        self.text.setMinimumWidth(min(natural, widest - margins.left() - margins.right()))
        self.bubble.setMaximumWidth(widest)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._fit()


class ThinkingLine(QToolButton):
    """A line that unfolds what is under it, as Claude draws one: its words, muted, then a
    chevron close behind them. The model's thinking above a reply, and a run of steps."""

    ICON, GAP = 12, 4

    def __init__(self) -> None:
        super().__init__(checkable=True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)  # repainted when the pointer comes

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance(self.text()) + self.GAP + self.ICON,
                     metrics.height() + 4)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def setText(self, text: str) -> None:
        super().setText(text)
        self.updateGeometry()

    def paintEvent(self, event: QPaintEvent) -> None:
        colors = theme.current()
        color = colors.text if self.underMouse() else colors.muted
        painter = QPainter(self)
        painter.setPen(QColor(color))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self.text())
        chevron = theme.icon("unfolded" if self.isChecked() else "folded", color, self.ICON)
        left = self.fontMetrics().horizontalAdvance(self.text()) + self.GAP
        chevron.paint(painter, left, (self.height() - self.ICON) // 2 + 1, self.ICON, self.ICON)


class AssistantBubble(QWidget):
    """Kaden's reply: an avatar, then the text with no bubble, easy to read when long."""

    def __init__(self, item: AssistantItem) -> None:
        super().__init__()
        self.item = item
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        layout = QVBoxLayout()
        layout.setSpacing(4)
        row.addLayout(layout, 1)
        self.thinking = ThinkingLine()
        self.thinking.toggled.connect(self._toggle_thinking)
        self.reasoning = text_label(tone="muted")
        self.reasoning.setObjectName("reasoning")
        self.reasoning.hide()
        self.body = MarkdownView()
        self.body.setAccessibleName("Kaden")
        # While it streams: the parts already whole, each rendered once, above the body,
        # which renders the rest; once it is final, the body renders all of it.
        self.parts: list[MarkdownView] = []
        self._parted = ""  # the text the parts hold
        self._text = QVBoxLayout()
        self._text.setSpacing(0)
        self._text.addWidget(self.body)
        self.marker = text_label(selectable=False, tone="muted")
        # Shown while the pointer is on the reply: copy it, and how long ago it came.
        self.footer = QWidget()
        footer = QHBoxLayout(self.footer)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        self.copy = QToolButton(objectName="replyAction")
        self.copy.setToolTip("Copy")
        self.copy.setAccessibleName("Copy reply")
        self.copy.setIconSize(QSize(15, 15))
        self.copy.clicked.connect(self._copy)
        self.age = text_label(selectable=False, role="small")
        footer.addWidget(self.copy)
        footer.addWidget(self.age)
        footer.addStretch()
        policy = self.footer.sizePolicy()
        policy.setRetainSizeWhenHidden(True)  # no jump when it shows
        self.footer.setSizePolicy(policy)
        self.footer.hide()
        self._copied = QTimer(self, singleShot=True, interval=COPIED_MS)
        self._copied.timeout.connect(self._paint_copy)
        self._paint_copy()
        layout.addWidget(self.thinking, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.reasoning)
        layout.addLayout(self._text)
        for widget in (self.marker, self.footer):
            layout.addWidget(widget)
        self._timer = QTimer(self, singleShot=True, interval=RENDER_INTERVAL_MS)
        self._timer.timeout.connect(self._render)
        self.ticking = QTimer(self, interval=1000)  # the thinking line's seconds, as they pass
        self.ticking.timeout.connect(self._render_thinking)
        self._render()

    def refresh(self) -> None:
        if self.item.final or not self._timer.isActive():
            if self.item.final:
                self._timer.stop()
                self._render()
            else:
                self._timer.start()

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        if self.item.final and self.item.text:
            self.age.setText(relative_age(self.item.created_at)
                             if self.item.created_at is not None else "")
            self.footer.show()

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self.footer.hide()

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.item.text)
        self._paint_copy(copied=True)
        self._copied.start()

    def _paint_copy(self, copied: bool = False) -> None:
        self._painted = theme.current()
        self.copy.setIcon(theme.icon("check" if copied else "copy", self._painted.muted))
        self.copy.setToolTip("Copied" if copied else "Copy")

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if restyled(self, event):
            self._paint_copy(copied=self._copied.isActive())

    def _render_thinking(self) -> None:
        """"Thinking… 12s" while it thinks, counting; "Thought for 14s" once it is over."""
        item = self.item
        self.thinking.setVisible(bool(item.reasoning))
        thinking = bool(item.reasoning) and not item.final and not item.text
        if thinking and item.thinking_since is not None:
            self.thinking.setText(
                f"Thinking… {_duration(time.monotonic() - item.thinking_since)}")
        else:
            self.thinking.setText("Thinking…" if thinking else _thought(item.thought_s))
        if thinking and item.thinking_since is not None:
            if not self.ticking.isActive():
                self.ticking.start()
        else:
            self.ticking.stop()

    def _toggle_thinking(self, shown: bool) -> None:
        self.reasoning.setVisible(shown)

    def _render(self) -> None:
        item = self.item
        self._render_thinking()
        self.reasoning.setText(item.reasoning)
        self._render_text()
        marker = ("The model declined to answer." if item.refused
                  else "Interrupted before it finished." if item.interrupted else "")
        self.marker.setText(marker)
        self.marker.setVisible(bool(marker))


    def _render_text(self) -> None:
        text = self.item.text
        if self.item.final or not text.startswith(self._parted):
            for part in self.parts:
                discard(part)
            self.parts, self._parted = [], ""
        if not self.item.final and (end := _whole_part_end(text)) > len(self._parted):
            part = MarkdownView()
            part.setMarkdown(text[len(self._parted):end])
            self._text.insertWidget(len(self.parts), part)
            self.parts.append(part)
            self._parted = text[:end]
        rest = text[len(self._parted):]
        self.body.setVisible(bool(rest.strip()))
        self.body.setMarkdown(rest)


def _whole_part_end(markdown: str) -> int:
    """Where the whole blocks of a streaming reply end: after the last blank line outside a
    code block, so what comes after it can still change; 0 for none."""
    end, fence, position = 0, "", 0
    for line in markdown.splitlines(keepends=True):
        position += len(line)
        found = _FENCE.match(line.rstrip("\n"))
        if fence:
            if (found and found[2][0] == fence[0] and len(found[2]) >= len(fence)
                    and not found[3].strip()):
                fence = ""
        elif found:
            fence = found[2]
        elif not line.strip() and line.endswith("\n"):
            end = position
    return end


class ToolCard(QFrame):
    """One row: the tool, what it works on, its state; details unfold below."""

    def __init__(self, item: ToolItem, cancel: Callable[[str], None]) -> None:
        super().__init__()
        self.item = item
        self.setObjectName("tool")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 8, 6)
        layout.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        # As Claude draws a step: a small arrow, then its words, muted, in a rounded row.
        self.toggle = QToolButton(checkable=True, objectName="stepArrow")
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.setToolTip("Details")
        self.toggle.setAccessibleName("Details")
        self.toggle.toggled.connect(self._toggle)
        self.title = ElidedLabel()
        self.title.setProperty("tone", "muted")
        self.state = text_label(wrap=False, selectable=False)
        self.cancel = QPushButton("Cancel")
        self.cancel.setAccessibleName("Cancel this task")
        self.cancel.clicked.connect(lambda: cancel(self.item.call_id))
        head.addWidget(self.toggle)
        head.addWidget(self.title, 1)
        head.addWidget(self.state)
        head.addWidget(self.cancel)
        layout.addLayout(head)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mousePressEvent = (  # type: ignore[method-assign]
            lambda event: self.toggle.click())
        self.progress = text_label(tone="muted")
        # A tool's pictures, shown folded or not: a code to scan, a frame it looked at.
        self.pictures: list[QLabel] = []
        self._shown: tuple[Image, ...] = ()
        self._picture_row = QHBoxLayout()
        self._picture_row.setSpacing(6)
        self._picture_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.details = QPlainTextEdit(readOnly=True)
        self.details.setMaximumHeight(200)
        self.details.hide()
        layout.addWidget(self.progress)
        layout.addLayout(self._picture_row)
        layout.addWidget(self.details)
        self.refresh()

    def refresh(self) -> None:
        item = self.item
        duration = f" in {item.duration_s:.1f} s" if item.duration_s is not None else ""
        self.title.setText(item.summary)
        self.title.setToolTip(f"{item.name}: {item.detail}"
                              + (f" ({item.state_label}{duration})" if item.status else ""))
        self.state.setText(item.state_label + duration)
        self.state.setVisible(item.status is not ToolStatus.OK)
        theme.mark(self.state, "tone", TOOL_TONES.get(item.status, "muted")
                   if item.status is not None else "muted")
        self.cancel.setVisible(item.cancellable)
        self.progress.setText(item.progress)
        self.progress.setVisible(bool(item.progress) and item.status is None)
        self.details.setPlainText(f"{item.detail}\n\n{item.content}".strip())
        self.setAccessibleName(f"Tool {item.name}, {item.state_label}")
        if item.pictures != self._shown:
            self._show_pictures(item.pictures)

    def _show_pictures(self, pictures: tuple[Image, ...]) -> None:
        for label in self.pictures:
            discard(label)
        self.pictures, self._shown = _picture_labels(pictures), pictures
        for label in self.pictures:
            self._picture_row.addWidget(label)

    def _toggle(self, shown: bool) -> None:
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)
        self.details.setVisible(shown)


def _picture_labels(pictures: Sequence[Image]) -> list[QLabel]:
    """A tool's pictures, small enough for the conversation; one that is not base64 or not
    a picture is left out."""
    labels = []
    for picture in pictures:
        pixmap = QPixmap()
        try:
            data = base64.b64decode(picture.data, validate=True)
        except ValueError:  # binascii.Error: not base64
            continue
        if not pixmap.loadFromData(data):
            continue
        if pixmap.height() > PICTURE_HEIGHT or pixmap.width() > PICTURE_WIDTH:
            pixmap = pixmap.scaled(PICTURE_WIDTH, PICTURE_HEIGHT,
                                   Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
        label = QLabel()
        label.setPixmap(pixmap)
        label.setAccessibleName(picture.name or "Picture")
        labels.append(label)
    return labels


class StepGroup(QFrame):
    """A run of steps, and the thinking between them, folded into one line: the step under
    way while it runs, then how many there were and what went wrong. Opens on a click."""

    def __init__(self, cancel: Callable[[str], None]) -> None:
        super().__init__()
        self.setObjectName("steps")
        self.active: ToolItem | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.header = QFrame(objectName="stepsHeader")
        head = QHBoxLayout(self.header)
        head.setContentsMargins(0, 2, 6, 2)
        head.setSpacing(6)
        self.title = self.toggle = ThinkingLine()  # its words, then a chevron
        self.toggle.setAccessibleName("Show the steps")
        self.toggle.toggled.connect(self._toggle)
        self.state = text_label(wrap=False, selectable=False)
        self.cancel = QPushButton("Cancel")
        self.cancel.setAccessibleName("Cancel this task")
        self.cancel.clicked.connect(lambda: self.active and cancel(self.active.call_id))
        head.addWidget(self.title)
        head.addWidget(self.state)
        head.addStretch()
        head.addWidget(self.cancel)
        layout.addWidget(self.header)
        # Folded, its steps' pictures still show (a code to scan); open, their cards do.
        self.pictures: list[QLabel] = []
        self._shown: tuple[Image, ...] = ()
        self._picture_row = QHBoxLayout()
        self._picture_row.setSpacing(6)
        self._picture_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addLayout(self._picture_row)
        self.body = QFrame(objectName="stepsBody")
        self.steps = QVBoxLayout(self.body)
        self.steps.setContentsMargins(0, 0, 0, 0)
        self.steps.setSpacing(4)
        self.body.hide()
        layout.addWidget(self.body)

    def set_members(self, widgets: Sequence[QWidget]) -> None:
        """Its steps in order; the view says which of them show (ConversationView._arrange)."""
        for index, widget in enumerate(widgets):
            place(self.steps, index, widget)
        self.refresh()

    def members(self) -> list[QWidget]:
        return [w for i in range(self.steps.count()) if (w := self.steps.itemAt(i).widget())]

    def refresh(self) -> None:
        steps = [w.item for w in self.members() if isinstance(w, ToolCard)]
        title, state, self.active = group_heading(steps)
        self.title.setText(title)
        self.title.setToolTip("\n".join(step.summary for step in steps))
        self.state.setText(state)
        tone = "muted" if self.active is not None else "error" if state else None
        theme.mark(self.state, "tone", tone)
        self.state.setVisible(bool(state))
        self.cancel.setVisible(self.active is not None and self.active.cancellable)
        pictures = tuple(p for step in steps for p in step.pictures)
        if pictures != self._shown:
            for label in self.pictures:
                discard(label)
            self.pictures, self._shown = _picture_labels(pictures), pictures
            for label in self.pictures:
                self._picture_row.addWidget(label)
                label.setVisible(not self.body.isVisibleTo(self))

    def _toggle(self, shown: bool) -> None:
        self.toggle.setAccessibleName("Hide the steps" if shown else "Show the steps")
        self.body.setVisible(shown)
        for label in self.pictures:
            label.setVisible(not shown)


NOTICE_LINKS = {"memory": "Open memory"}


class NoticeLine(QLabel):
    """A line from Kaden itself; with a link, linkActivated carries the item's link."""

    def __init__(self, item: NoticeItem) -> None:
        super().__init__()
        self.item = item
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setProperty("tone", "muted" if item.severity == "info" else item.severity)
        self.refresh()

    def refresh(self) -> None:
        if self.item.link is None:
            self.setTextFormat(Qt.TextFormat.PlainText)
            self.setText(self.item.text)
            return
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setText(f"{html.escape(self.item.text)} · "
                     f'<a href="{self.item.link}">{NOTICE_LINKS[self.item.link]}</a>')


class PlanCard(QFrame):
    """The agent's plan as a checklist: done steps ticked and dimmed, the one under way with
    an arc that turns while the agent works on it, the rest open; dropped ones struck
    through."""

    def __init__(self, item: ChecklistItem) -> None:
        super().__init__()
        self.item = item
        self.setObjectName("plan")
        self.setAccessibleName("Plan")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        self._body: QWidget | None = None
        self._painted: theme.Palette | None = None
        self._build()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if restyled(self, event):
            self._build()

    def _build(self) -> None:
        item, outer = self.item, self.layout()
        assert outer is not None
        if self._body is not None:
            outer.removeWidget(self._body)
            self._body.deleteLater()
        self._body = QWidget()
        outer.addWidget(self._body)
        layout = QVBoxLayout(self._body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._steps = item.steps
        colors = self._painted = theme.current()
        self._rows: list[tuple[str, str]] = []
        for step in self._steps:
            mark = str(step.status)  # done, doing, todo or dropped
            row = QHBoxLayout()
            row.setSpacing(8)
            icon: QWidget
            if mark == "doing":
                icon = Spinner()
            else:
                icon = QLabel()
                icon.setPixmap(theme.icon(mark, colors.muted, 16).pixmap(16, 16))
            text = text_label(step.text, tone=None if mark in ("doing", "todo") else "muted")
            text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            if mark == "dropped":
                font = text.font()
                font.setStrikeOut(True)
                text.setFont(font)
            # Beside the first line of the step's text, also when the text wraps.
            icon.setFixedSize(16, 16)
            lead = max(0, (text.fontMetrics().height() - 16) // 2)
            holder = QVBoxLayout()
            holder.setContentsMargins(0, lead, 0, 0)
            holder.addWidget(icon)
            holder.addStretch()
            row.addLayout(holder)
            row.addWidget(text, 1)
            layout.addLayout(row)
            self._rows.append((mark, step.text))

    def refresh(self) -> None:
        if self.item.steps != self._steps:
            self._build()

    def rows(self) -> list[tuple[str, str]]:
        """(mark, text) per step, for tests and accessibility checks."""
        return list(self._rows)


class WrapUpCard(QFrame):
    """The card that ends the agent's turn: its recap, its question and the options, its pick
    first. A click sends an option as the user's message, or runs its call while that is
    pending; the close button puts the card away."""

    def __init__(self, card: Card, pending: Container[str], choose: Callable[[int], None],
                 close: Callable[[], None]) -> None:
        super().__init__()
        self.setObjectName("prompt")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 8, 10)
        top = QHBoxLayout()
        text = QVBoxLayout()
        if card.recap:
            text.addWidget(text_label(card.recap, tone="muted"))
        if card.question:
            text.addWidget(text_label(card.question, role="strong"))
        top.addLayout(text, 1)
        dismiss = self.dismiss = QToolButton(objectName="windowControl")
        self._painted: theme.Palette | None = None
        self._paint()
        dismiss.setToolTip("Close")
        dismiss.setAccessibleName("Close")
        dismiss.clicked.connect(close)
        top.addWidget(dismiss, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)
        if card.options:
            buttons = QHBoxLayout()
            for i, option in enumerate(card.options):
                button = QPushButton(option)
                if i == 0:
                    primary(button)
                button.setToolTip("Do this" if card.runs(i, pending) else "Send this reply")
                button.clicked.connect(lambda _=False, index=i: choose(index))
                buttons.addWidget(button)
            buttons.addStretch()
            layout.addLayout(buttons)
        self.setAccessibleName(card.question or "Turn summary")

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if restyled(self, event):
            self._paint()

    def _paint(self) -> None:
        self._painted = theme.current()
        self.dismiss.setIcon(theme.icon("close", self._painted.muted))
