"""Shared sort combo box with a stable, platform-independent chevron."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QComboBox


class SortComboBox(QComboBox):
    """Keep native popup behavior while rendering a consistent arrow."""

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().paintEvent(event)
        painter = QPainter(self)
        if not painter.isActive():
            return
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(
                QColor("#8E8E93"),
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
