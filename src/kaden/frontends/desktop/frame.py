"""The frameless window's own edges: dragging, resizing and rounded corners.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QCursor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QApplication, QFrame, QScrollBar, QWidget

from kaden.frontends.desktop import theme


class DragArea(QFrame):
    """Where the window is grabbed: dragging moves it, a double-click maximizes or restores
    it, as a title bar would. Buttons and fields inside it work as usual."""

    def mousePressEvent(self, event: QMouseEvent) -> None:
        handle = self.window().windowHandle()
        if event.button() == Qt.MouseButton.LeftButton and handle is not None:
            handle.startSystemMove()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        window = self.window()
        if window.isMaximized():
            window.showNormal()
        else:
            window.showMaximized()


class WindowEdges(QObject):
    """Resizing a window without a frame: near an edge the cursor shows the direction, and
    pressing there hands the resize to the window system."""

    MARGIN = 6  # px inside the frame's edge
    GRIP = 8  # px of the shadow outside it
    OVER_SCROLL_BAR = 2  # px that still resize where a scroll bar lies along the edge

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.window = window
        self.cursor_set = False
        QApplication.instance().installEventFilter(self)

    def edges(self, global_pos: QPoint, over: QWidget | None = None) -> Qt.Edge:
        """The edges `global_pos` is on; `over` is the widget under it. The frame is the
        window without its shadow (RoundedCorners). A scroll bar keeps all but the
        outermost pixels, or it could hardly be grabbed."""
        if self.window.isMaximized() or self.window.isFullScreen():
            return Qt.Edge(0)
        pos = self.window.mapFromGlobal(global_pos)
        frame = self.window.contentsRect()
        if not frame.adjusted(-self.GRIP, -self.GRIP, self.GRIP, self.GRIP).contains(pos):
            return Qt.Edge(0)
        m = self.OVER_SCROLL_BAR if isinstance(over, QScrollBar) else self.MARGIN
        found = Qt.Edge(0)
        if pos.x() <= frame.left() + m:
            found |= Qt.Edge.LeftEdge
        elif pos.x() >= frame.right() - m + 1:
            found |= Qt.Edge.RightEdge
        if pos.y() <= frame.top() + m:
            found |= Qt.Edge.TopEdge
        elif pos.y() >= frame.bottom() - m + 1:
            found |= Qt.Edge.BottomEdge
        return found

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind not in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress):
            return False
        if not isinstance(watched, QWidget) or watched.window() is not self.window:
            return False
        edges = self.edges(event.globalPosition().toPoint(), watched)  # type: ignore[attr-defined]
        if kind == QEvent.Type.MouseMove:
            self._show_cursor(edges)
            return False
        handle = self.window.windowHandle()
        if edges and handle is not None and event.button() == Qt.MouseButton.LeftButton:  # type: ignore[attr-defined]
            handle.startSystemResize(edges)
            return True
        return False

    def _show_cursor(self, edges: Qt.Edge) -> None:
        shape = _EDGE_CURSORS.get(edges)
        if shape is None and self.cursor_set:
            QApplication.restoreOverrideCursor()
            self.cursor_set = False
        elif shape is not None:
            if self.cursor_set:
                QApplication.changeOverrideCursor(QCursor(shape))
            else:
                QApplication.setOverrideCursor(QCursor(shape))
                self.cursor_set = True


class RoundedCorners(QWidget):
    """Rounds a frameless window's corners and gives it a shadow: it lies over the whole
    window, lets the mouse through, keeps a margin around the window's contents and clears
    what is outside the rounded frame, so the desktop shows there whatever the widgets
    underneath painted, with the shadow over it. Square and without a margin while
    maximized or full screen. Made once the window's contents are."""

    RADIUS = 12  # px
    SHADOW = 24  # px around the frame
    SHADOW_DROP = 4  # px the shadow lies below the frame
    SHADOW_ALPHA = 0.08, 0.22  # at the frame, by day and by night

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        window.installEventFilter(self)
        self._fit()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.Resize, QEvent.Type.WindowStateChange,
                            QEvent.Type.Show):
            self._fit()
        elif event.type() == QEvent.Type.Paint:  # a translucent window has no background
            QPainter(self.parentWidget()).fillRect(self.parentWidget().rect(),
                                                   QColor(theme.current().bg))
        return False

    def _fit(self) -> None:
        window = self.parentWidget()
        framed = not (window.isMaximized() or window.isFullScreen())
        margin = self.SHADOW if framed else 0
        if window.contentsMargins().left() != margin:
            window.setContentsMargins(margin, margin, margin, margin)
        self.setGeometry(window.rect())
        self.setVisible(framed)
        self.raise_()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QRectF(self.parentWidget().contentsRect())
        whole = QPainterPath()
        whole.addRect(QRectF(self.rect()))
        inside = QPainterPath()
        inside.addRoundedRect(frame, self.RADIUS, self.RADIUS)
        outside = whole - inside
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.fillPath(outside, Qt.GlobalColor.black)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.save()
        painter.setClipPath(outside)
        reach = self.SHADOW - self.SHADOW_DROP  # how far the shadow spreads
        cast = frame.translated(0, self.SHADOW_DROP)
        peak = self.SHADOW_ALPHA[theme.current().dark]
        under = QPainterPath()  # what the frame leaves showing of the shadow's own shape
        under.addRoundedRect(cast, self.RADIUS, self.RADIUS)
        painter.fillPath(under, QColor(0, 0, 0, round(255 * peak)))
        for step in range(reach):
            alpha = peak * (1 - step / reach) ** 2
            painter.setPen(QPen(QColor(0, 0, 0, round(255 * alpha)), 1))
            grown = cast.adjusted(-step - 0.5, -step - 0.5, step + 0.5, step + 0.5)
            painter.drawRoundedRect(grown, self.RADIUS + step, self.RADIUS + step)
        painter.restore()
        painter.setPen(QPen(QColor(theme.current().line), 1))
        painter.drawRoundedRect(frame.adjusted(0.5, 0.5, -0.5, -0.5),
                                self.RADIUS, self.RADIUS)


_E = Qt.Edge
_EDGE_CURSORS = {
    _E.LeftEdge: Qt.CursorShape.SizeHorCursor, _E.RightEdge: Qt.CursorShape.SizeHorCursor,
    _E.TopEdge: Qt.CursorShape.SizeVerCursor, _E.BottomEdge: Qt.CursorShape.SizeVerCursor,
    _E.TopEdge | _E.LeftEdge: Qt.CursorShape.SizeFDiagCursor,
    _E.BottomEdge | _E.RightEdge: Qt.CursorShape.SizeFDiagCursor,
    _E.TopEdge | _E.RightEdge: Qt.CursorShape.SizeBDiagCursor,
    _E.BottomEdge | _E.LeftEdge: Qt.CursorShape.SizeBDiagCursor,
}
