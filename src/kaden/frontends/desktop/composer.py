"""The message box and the files attached to a message.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QMimeData, QRect, QSize, Qt, Signal
from PySide6.QtGui import QImage, QKeyEvent, QPainter, QPaintEvent, QPalette, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QToolButton,
    QWidget,
)

from kaden.frontends.desktop import theme
from kaden.frontends.desktop.viewmodel import clip
from kaden.frontends.desktop.widgets import (
    detach,
    discard,
    fitted,
    local_files,
    restyled,
    text_label,
)

THUMBNAIL = 56  # px: a picture waiting above the message box


class MessageBox(QPlainTextEdit):
    """Enter sends; Shift+Enter starts a new line. Grows with its text up to a few lines."""

    submitted = Signal()
    focus_changed = Signal(bool)
    attached = Signal(list)  # paths of files dropped or pasted into it
    pasted = Signal(QImage)  # an image pasted from the clipboard
    MIN_LINES = 1  # one slim row until there is more to show
    MAX_LINES = 8

    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._hint = ""
        self.document().documentLayout().documentSizeChanged.connect(self._fit)
        self._fit()

    # The hint is drawn here, not by Qt: Qt starts it where the cursor blinks, which
    # then covers its first letter ("|lessage Kaden").
    def setPlaceholderText(self, text: str) -> None:
        self._hint = text
        self.viewport().update()

    def placeholderText(self) -> str:
        return self._hint

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if not self._hint or not self.document().isEmpty():
            return
        caret = self.cursorRect()
        left = caret.x() + max(self.cursorWidth(), 1) + 1  # a pixel's gap after the cursor
        painter = QPainter(self.viewport())
        painter.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
        width = self.viewport().width() - left
        painter.drawText(QRect(left, caret.y(), width, caret.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self.fontMetrics().elidedText(self._hint, Qt.TextElideMode.ElideRight,
                                                       width))

    def _fit(self, *_: object) -> None:
        lines = min(max(self.MIN_LINES, int(self.document().size().height())), self.MAX_LINES)
        margins = self.contentsMargins()
        height = (lines * self.fontMetrics().lineSpacing() + 2 * self.document().documentMargin()
                  + margins.top() + margins.bottom() + 2)
        self.setFixedHeight(int(height))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if enter and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
            return
        super().keyPressEvent(event)

    def canInsertFromMimeData(self, source: QMimeData) -> bool:
        return bool(local_files(source)) or source.hasImage() or \
            super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source: QMimeData) -> None:
        """Files and pictures are attached, not typed in as their paths."""
        if files := local_files(source):
            self.attached.emit(files)
        elif source.hasImage() and isinstance(image := source.imageData(), QImage):
            self.pasted.emit(image)
        else:
            super().insertFromMimeData(source)

    def focusInEvent(self, event: Any) -> None:
        super().focusInEvent(event)
        self.focus_changed.emit(True)

    def focusOutEvent(self, event: Any) -> None:
        super().focusOutEvent(event)
        self.focus_changed.emit(False)


class AttachmentChip(QFrame):
    """One file waiting to be sent: a small preview of a picture, else its name; ✕ takes
    it off."""

    removed = Signal()

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = path
        self.setObjectName("attachment")
        self.setToolTip(path)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(4)
        picture = QPixmap(path)
        self.preview: QLabel | None = None
        if not picture.isNull():
            self.preview = QLabel()
            self.preview.setPixmap(fitted(picture, THUMBNAIL))
            self.preview.setAccessibleName(f"Preview of {Path(path).name}")
            row.addWidget(self.preview)
        else:
            row.addWidget(text_label(clip(Path(path).name, 40), wrap=False, selectable=False))
        self.remove = QToolButton(objectName="removeAttachment")
        self._painted: theme.Palette | None = None
        self._paint()
        self.remove.setIconSize(QSize(12, 12))
        self.remove.setFixedSize(18, 18)
        self.remove.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove.setToolTip("Remove")
        self.remove.setAccessibleName(f"Remove {Path(path).name}")
        self.remove.clicked.connect(self.removed)
        row.addWidget(self.remove, 0, Qt.AlignmentFlag.AlignTop)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if restyled(self, event):
            self._paint()

    def _paint(self) -> None:
        self._painted = theme.current()
        self.remove.setIcon(theme.icon("close", self._painted.muted, 12))


class AttachmentStrip(QWidget):
    """The files attached to the message being written, above the text."""

    def __init__(self) -> None:
        super().__init__()
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 2)
        self._row.setSpacing(6)
        self._row.addStretch(1)
        self.chips: list[AttachmentChip] = []
        self.hide()

    def paths(self) -> list[str]:
        return [chip.path for chip in self.chips]

    def add(self, paths: Sequence[str]) -> None:
        for path in paths:
            if path in self.paths():
                continue
            chip = AttachmentChip(path)
            chip.removed.connect(lambda chip=chip: self._remove(chip))
            self.chips.append(chip)
            self._row.insertWidget(self._row.count() - 1, chip)
        self.setVisible(bool(self.chips))

    def clear(self) -> None:
        for chip in list(self.chips):
            self._remove(chip)

    def _remove(self, chip: AttachmentChip) -> None:
        self.chips.remove(chip)
        detach(chip)
        discard(chip)
        self.setVisible(bool(self.chips))
