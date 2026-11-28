"""Small widgets and helpers the desktop app's parts share: labels, switches, the
spinner, collapsible detail, and the sidebar list's data roles.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QMimeData, QPoint, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from kaden.frontends.desktop import theme

DOCS_URL = "https://github.com/K5031/Kaden/blob/main/docs/desktop-app.md"


COLUMN_WIDTH = 780  # px: the conversation and the message box, centred


# Item data the sidebar and memory rows are drawn from.
TITLE_ROLE = Qt.ItemDataRole.UserRole + 1
PREVIEW_ROLE = Qt.ItemDataRole.UserRole + 2
DATE_ROLE = Qt.ItemDataRole.UserRole + 3
FLAGS_ROLE = Qt.ItemDataRole.UserRole + 4  # {"archived"}, {"folded"}, {"working"}
KIND_ROLE = Qt.ItemDataRole.UserRole + 5  # sidebar rows: "group", "conversation", "session"


def restyled(widget: QWidget, event: QEvent) -> bool:
    """Whether `event` is the app's palette changing from the one `widget` was painted
    with (its `_painted`, None before it is): what was drawn in its colours is drawn
    again."""
    painted = getattr(widget, "_painted", None)
    return (event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange)
            and painted is not None and painted is not theme.current())


def text_label(text: str = "", *, wrap: bool = True, selectable: bool = True,
           tone: str | None = None, role: str | None = None) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(wrap)
    label.setTextFormat(Qt.TextFormat.PlainText)
    if selectable:
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setProperty("tone", tone)
    label.setProperty("role", role)
    return label


def error_label() -> QLabel:
    """A form's line for what went wrong: hidden until `say` fills it."""
    label = text_label(tone="error")
    label.hide()
    return label


def say(label: QLabel, text: str) -> None:
    label.setText(text)
    label.show()


def discard(widget: QWidget) -> None:
    """Hidden at once: deleteLater runs only when the event loop next turns, and until then
    a widget taken out of its layout would still be drawn where it was."""
    widget.hide()
    widget.deleteLater()


def primary(button: QPushButton) -> QPushButton:
    button.setProperty("primary", True)
    return button


class ElidedLabel(QLabel):
    """One line that ends in "…" instead of widening its window."""

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight,
                                             self.contentsRect().width())
        painter.drawText(self.contentsRect(), Qt.AlignmentFlag.AlignVCenter, text)


class Switch(QCheckBox):
    """A check box drawn as a sliding switch; a dot on it while `unseen` (something new
    behind it, as a messaging app's)."""

    TRACK = QSize(30, 18)
    GAP = 8
    DOT = 7.0
    unseen = False

    def set_unseen(self, unseen: bool) -> None:
        if unseen != self.unseen:
            self.unseen = unseen
            self.update()

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        return QSize(self.TRACK.width() + self.GAP + metrics.horizontalAdvance(self.text()) + 2,
                     max(self.TRACK.height(), metrics.height()) + 4)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def hitButton(self, pos: QPoint) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, event: Any) -> None:
        colors = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.TRACK.width(), self.TRACK.height()
        track = QRectF(0.5, (self.height() - height) / 2, width, height)
        fill = QColor(colors.accent if self.isChecked() else colors.track)
        if not self.isEnabled():
            fill.setAlphaF(0.45)
        painter.setPen(QPen(QColor(colors.accent), 2) if self.hasFocus()
                       and self.focusPolicy() != Qt.FocusPolicy.NoFocus
                       and self.testAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange)
                       else Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(track, height / 2, height / 2)
        knob = height - 4
        x = track.x() + (width - knob - 2 if self.isChecked() else 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QRectF(x, track.y() + 2, knob, knob))
        if self.unseen:  # at the track's top right corner, ringed in the background
            painter.setPen(QPen(QColor(colors.bg), 1.5))
            painter.setBrush(QColor(colors.accent))
            painter.drawEllipse(QRectF(track.right() - self.DOT + 1, track.top() - 2,
                                       self.DOT, self.DOT))
        painter.setPen(QColor(colors.text if self.isEnabled() else colors.muted))
        painter.drawText(QRectF(width + self.GAP, 0, self.width() - width - self.GAP,
                                self.height()), Qt.AlignmentFlag.AlignVCenter, self.text())


def place(layout: QVBoxLayout, index: int, widget: QWidget) -> None:
    """Put `widget` at `index` of `layout`, taking it out of wherever it was. A new parent
    hides it: the caller says whether it shows."""
    if layout.indexOf(widget) != index:
        detach(widget)
        layout.insertWidget(index, widget)


def detach(widget: QWidget) -> None:
    """Take `widget` out of its layout, which may be nested in its parent's."""
    parent = widget.parentWidget()
    layouts = [parent.layout()] if parent is not None and parent.layout() is not None else []
    while layouts:
        layout = layouts.pop()
        if layout.indexOf(widget) >= 0:
            layout.removeWidget(widget)
            return
        layouts.extend(child for i in range(layout.count())
                       if (child := layout.itemAt(i).layout()) is not None)


class Detail(QWidget):
    """Text that may run long, folded to its start with Show all: a call's arguments on a
    card (a sub-session's brief, a long command), or a long notice (a life turned on)."""

    def __init__(self, text: str, *, lines: int = 3, chars: int = 240) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        short = "\n".join(text.splitlines()[:lines])
        if len(short) > chars:
            short = short[:chars].rstrip()
        self.full, self.short = text, (short + "…" if short != text else text)
        self.label = text_label(self.short, tone="muted")
        layout.addWidget(self.label)
        self.toggle = QToolButton(text="Show all")
        self.toggle.setVisible(self.short != text)
        self.toggle.clicked.connect(self._toggled)
        layout.addWidget(self.toggle, alignment=Qt.AlignmentFlag.AlignLeft)

    def _toggled(self) -> None:
        expanded = self.label.text() == self.full
        self.label.setText(self.short if expanded else self.full)
        self.toggle.setText("Show all" if expanded else "Show less")

    def refresh(self) -> None:
        pass  # its text does not change


class Spinner(QWidget):
    """A turning arc: the step under way."""

    SIZE, STEP_MS, STEP_DEGREES, ARC_DEGREES = 16, 60, 30, 100

    def __init__(self) -> None:
        super().__init__()
        self._angle = 0
        self.setFixedSize(self.SIZE, self.SIZE)
        self._timer = QTimer(self, interval=self.STEP_MS)
        self._timer.timeout.connect(self._turn)

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event: Any) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def _turn(self) -> None:
        self._angle = (self._angle + self.STEP_DEGREES) % 360
        self.update()

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(theme.current().muted), 1.6, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        inset = self.SIZE * 0.1
        painter.drawArc(QRectF(inset, inset, self.SIZE - 2 * inset, self.SIZE - 2 * inset),
                        (90 - self._angle) * 16, self.ARC_DEGREES * 16)


def local_files(source: QMimeData) -> list[str]:
    """The files among what was dropped or pasted."""
    return [url.toLocalFile() for url in source.urls()
            if url.isLocalFile() and Path(url.toLocalFile()).is_file()] \
        if source.hasUrls() else []


def fitted(picture: QPixmap, size: int) -> QPixmap:
    """The picture no wider or taller than `size`, never enlarged."""
    if picture.width() <= size and picture.height() <= size:
        return picture
    return picture.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
