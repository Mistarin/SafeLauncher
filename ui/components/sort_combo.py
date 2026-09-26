"""Shared sort combo box with a stable, platform-independent chevron."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QComboBox, QProxyStyle, QStyle


class _SortComboStyle(QProxyStyle):
    """Suppress the platform arrow; SortComboBox draws its own chevron."""

    def drawPrimitive(self, element, option, painter, widget=None) -> None:  # noqa: N802
        if element == QStyle.PrimitiveElement.PE_IndicatorArrowDown:
            return
        super().drawPrimitive(element, option, painter, widget)


class SortComboBox(QComboBox):
    """Keep native popup behavior while rendering a consistent arrow."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyle(_SortComboStyle())

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().paintEvent(event)
        painter = QPainter(self)
        if not painter.isActive():
            return
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(
                QColor("#A1A1AA"),
                1.35,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            if self.hasFocus() or self.underMouse():
                pen.setColor(QColor("#E4E4E7"))
            painter.setPen(pen)
            center_x = float(self.width() - 10)
            center_y = float(self.height() / 2)
            painter.drawLine(
                QPointF(center_x - 3.0, center_y - 1.5),
                QPointF(center_x, center_y + 1.5),
            )
            painter.drawLine(
                QPointF(center_x, center_y + 1.5),
                QPointF(center_x + 3.0, center_y - 1.5),
            )
        finally:
            painter.end()
