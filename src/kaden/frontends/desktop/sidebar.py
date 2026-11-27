"""The sidebar's conversation list: its rows, drawn by one delegate.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from kaden.frontends.desktop import theme
from kaden.frontends.desktop.widgets import (
    DATE_ROLE,
    FLAGS_ROLE,
    KIND_ROLE,
    PREVIEW_ROLE,
    TITLE_ROLE,
)


class RowDelegate(QStyledItemDelegate):
    """Draws a list row as a title, an optional muted second line and a date on the right.
    With `wrap`, the title wraps instead of ending in "…". In the sidebar (KIND_ROLE) a
    project heading is a small foldable label, and a sub-session is indented under its
    conversation with a tree line."""

    PAD = QSize(10, 7)
    INDENT = 22  # a sub-session under its conversation

    def __init__(self, view: QListWidget, *, wrap: bool = False, compact: bool = False
                 ) -> None:
        super().__init__(view)
        self.view, self.wrap, self.compact = view, wrap, compact
        if compact:
            self.PAD = QSize(10, 6)

    def _texts(self, index: Any) -> tuple[str, str, str, set[str]]:
        title = index.data(TITLE_ROLE) or index.data(Qt.ItemDataRole.DisplayRole) or ""
        flags = index.data(FLAGS_ROLE) or set()
        if index.data(KIND_ROLE) is not None:  # the sidebar: one line, details on hover
            return title, "", "", flags
        return (title, index.data(PREVIEW_ROLE) or "", index.data(DATE_ROLE) or "", flags)

    def _title_font(self, option: QStyleOptionViewItem) -> QFont:
        font = QFont(option.font)
        font.setWeight(QFont.Weight.DemiBold if not self.wrap and not self.compact
                       else QFont.Weight.Normal)
        return font

    def _small_font(self, option: QStyleOptionViewItem) -> QFont:
        font = QFont(option.font)
        font.setPointSizeF(font.pointSizeF() * 0.9)
        return font

    def _title_height(self, option: QStyleOptionViewItem, title: str, width: int) -> int:
        metrics = QFontMetrics(self._title_font(option))
        if not self.wrap:
            return metrics.height()
        flags = Qt.TextFlag.TextWordWrap
        return metrics.boundingRect(0, 0, max(width, 40), 10_000, flags, title).height()

    def sizeHint(self, option: QStyleOptionViewItem, index: Any) -> QSize:
        if index.data(KIND_ROLE) == "group":
            height = QFontMetrics(self._small_font(option)).height()
            return QSize(self.view.viewport().width(), height + 2 * self.PAD.height() + 4)
        title, preview, _, _ = self._texts(index)
        width = self.view.viewport().width() - 2 * self.PAD.width()
        if index.data(KIND_ROLE) == "session":
            width -= self.INDENT
        height = self._title_height(option, title, width)
        if preview:
            height += 2 + QFontMetrics(self._small_font(option)).height()
        return QSize(width, height + 2 * self.PAD.height() + 2)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: Any) -> None:
        kind = index.data(KIND_ROLE)
        if kind == "group":
            self._paint_group(painter, option, index)
            return
        if kind == "session":
            option = self._paint_branch(painter, option, index)
        self._paint_row(painter, option, index)

    def _paint_group(self, painter: QPainter, option: QStyleOptionViewItem, index: Any) -> None:
        """A project heading: its name, then a chevron that says whether it is folded."""
        colors = theme.current()
        folded = "folded" in (index.data(FLAGS_ROLE) or set())
        font = self._small_font(option)
        font.setWeight(QFont.Weight.DemiBold)
        name = index.data(Qt.ItemDataRole.DisplayRole)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(font)
        painter.setPen(QColor(colors.muted))
        metrics = QFontMetrics(font)
        top = option.rect.top() + self.PAD.height() + 2
        left = option.rect.left() + self.PAD.width()
        painter.drawText(left, top, metrics.horizontalAdvance(name) + 1, metrics.height(),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        x = left + metrics.horizontalAdvance(name) + 7
        y = top + metrics.height() / 2
        r = metrics.height() * 0.16
        painter.setPen(QPen(QColor(colors.muted), 1.3, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        chevron = QPainterPath()
        if folded:  # pointing right
            chevron.moveTo(x, y - r * 1.4)
            chevron.lineTo(x + r * 1.2, y)
            chevron.lineTo(x, y + r * 1.4)
        else:  # pointing down
            chevron.moveTo(x - r * 0.2, y - r * 0.6)
            chevron.lineTo(x + r * 1.2, y + r * 0.8)
            chevron.lineTo(x + r * 2.6, y - r * 0.6)
        painter.drawPath(chevron)
        painter.restore()

    def _paint_branch(self, painter: QPainter, option: QStyleOptionViewItem, index: Any
                      ) -> QStyleOptionViewItem:
        """A sub-session's guide line, one thin line down its conversation's sub-sessions,
        and a dot while it works; the row's option, moved right of them."""
        colors = theme.current()
        rect = option.rect
        x = rect.left() + self.INDENT // 2
        below = index.model().index(index.row() + 1, 0)
        last = not below.isValid() or below.data(KIND_ROLE) != "session"
        painter.save()
        painter.setPen(QPen(QColor(colors.line), 1))
        painter.drawLine(x, rect.top(), x, rect.center().y() + 6 if last else rect.bottom())
        working = "working" in (index.data(FLAGS_ROLE) or set())
        if working:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors.accent))
            painter.drawEllipse(QRectF(rect.right() - 16, rect.top() + self.PAD.height() + 5,
                                       6, 6))
        painter.restore()
        moved = QStyleOptionViewItem(option)
        moved.rect = rect.adjusted(self.INDENT, 0, -12 if working else 0, 0)  # clear of the dot
        return moved

    def _paint_row(self, painter: QPainter, option: QStyleOptionViewItem, index: Any) -> None:
        colors = theme.current()
        title, preview, date, flags = self._texts(index)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(option.rect).adjusted(0, 1, 0, -1)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors.select if self.compact else
                                    colors.accent_soft if selected else colors.card))
            painter.drawRoundedRect(box, 8, 8)
        inner = option.rect.adjusted(self.PAD.width(), self.PAD.height() + 1,
                                     -self.PAD.width(), -self.PAD.height() - 1)
        small = self._small_font(option)
        small_metrics = QFontMetrics(small)
        left = inner.left()
        right = inner.right()
        if date:
            painter.setFont(small)
            painter.setPen(QColor(colors.muted))
            date_width = small_metrics.horizontalAdvance(date)
            painter.drawText(right - date_width, inner.top(), date_width + 1,
                             QFontMetrics(self._title_font(option)).height(),
                             Qt.AlignmentFlag.AlignVCenter, date)
            right -= date_width + 8
        title_font = self._title_font(option)
        painter.setFont(title_font)
        muted = "archived" in flags
        painter.setPen(QColor(colors.accent_text if selected and not self.compact else
                              colors.muted if muted else colors.text))
        title_height = self._title_height(option, title, right - left)
        if self.wrap:
            painter.drawText(left, inner.top(), right - left, title_height,
                             Qt.TextFlag.TextWordWrap, title)
        else:
            painter.drawText(left, inner.top(), right - left, title_height,
                             Qt.AlignmentFlag.AlignVCenter,
                             QFontMetrics(title_font).elidedText(
                                 title, Qt.TextElideMode.ElideRight, right - left))
        if preview:
            painter.setFont(small)
            painter.setPen(QColor(colors.muted))
            painter.drawText(inner.left(), inner.top() + title_height + 2, inner.width(),
                             small_metrics.height(), Qt.AlignmentFlag.AlignVCenter,
                             small_metrics.elidedText(preview, Qt.TextElideMode.ElideRight,
                                                      inner.width()))
        painter.restore()


class ConversationList(QListWidget):
    """A click or Enter chooses an entry: `chosen`, once per click. (itemActivated is not
    used: where a single click activates, as on KDE, it comes with itemClicked.) Right-click,
    the Menu key or Shift+F10 asks for an entry's menu."""

    chosen = Signal(QListWidgetItem)
    menu_requested = Signal(QListWidgetItem, QPoint)

    def __init__(self) -> None:
        super().__init__()
        self.itemClicked.connect(self.chosen)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
                entry := self.currentItem()) is not None:
            self.chosen.emit(entry)
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        if event.reason() == QContextMenuEvent.Reason.Mouse:
            entry, at = self.itemAt(event.pos()), event.globalPos()
        else:
            entry = self.currentItem()
            at = self.viewport().mapToGlobal(
                self.visualItemRect(entry).center()) if entry else QPoint()
        if entry is not None:
            self.menu_requested.emit(entry, at)
