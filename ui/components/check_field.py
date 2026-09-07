"""Consistent modern check field used throughout SafeLauncher."""

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton


class CheckField(QCheckBox):
    """Native checkbox behavior with a unified custom checkmark indicator."""

    INDICATOR_SIZE = 18

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(hint.width() + 2, max(hint.height(), self.INDICATOR_SIZE + 6))

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        indicator = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator, option, self
        )
        size = self.INDICATOR_SIZE
        indicator.setRect(2, (self.height() - size) // 2, size, size)

        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        checked = self.isChecked()
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus)

        if checked:
            fill = QColor("#3B9FE8") if enabled else QColor("#46515C")
            border = fill
        else:
            fill = QColor("#1B1B1F") if enabled else QColor("#161619")
            border = QColor("#555561") if enabled else QColor("#36363D")
            if hovered:
                border = QColor("#7B7B88")

        painter.setPen(QPen(border, 1.2))
        painter.setBrush(fill)
        painter.drawRoundedRect(indicator.adjusted(0, 0, -1, -1), 5, 5)

        if checked:
            painter.setPen(QPen(QColor("#FFFFFF"), 2.0, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            x, y = indicator.x(), indicator.y()
            painter.drawLine(x + 4, y + 9, x + 8, y + 13)
            painter.drawLine(x + 8, y + 13, x + 15, y + 5)

        if focused:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#7FC7FF"), 1))
            painter.drawRoundedRect(indicator.adjusted(-2, -2, 1, 1), 7, 7)

        text_rect = self.rect().adjusted(size + 10, 0, 0, 0)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         self.text())
