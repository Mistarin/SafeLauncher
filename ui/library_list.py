"""Operational list presentation for the library view."""

import os
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import QListWidget, QListWidgetItem
from core.disk_utils import get_dir_size, format_size


class LibraryListView(QListWidget):
    game_clicked = pyqtSignal(int)
    game_double_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setUniformItemSizes(False)
        self.setStyleSheet("""
            QListWidget { background: #101014; color: #f4f4f5; border: none; padding: 8px; }
            QListWidget::item { padding: 10px; border-bottom: 1px solid #27272a; }
            QListWidget::item:selected { background: #1e3a8a; color: #ffffff; }
        """)
        self.itemClicked.connect(lambda item: self.game_clicked.emit(int(item.data(Qt.ItemDataRole.UserRole))))
        self.itemDoubleClicked.connect(lambda item: self.game_double_clicked.emit(int(item.data(Qt.ItemDataRole.UserRole))))

    def set_games(self, games: list[tuple], selected_ids: set[int] | None = None):
        selected_ids = selected_ids or set()
        self.clear()
        for game in games:
            game_id, name, path, executable, mode, _, steam_id = game[:7]
            tags = game[10] if len(game) > 10 else ""
            missing = not (path and os.path.isdir(path) and (not executable or os.path.isfile(os.path.join(path, executable))))
            status = "⚠ Missing" if missing else "✓ Installed"
            size = format_size(get_dir_size(path)) if path else "0 B"
            text = f"{name}    [{status}]\n{mode}  •  {size}  •  {executable or 'no executable'}"
            if steam_id:
                text += f"  •  AppID {steam_id}"
            if tags:
                text += f"\n{tags}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, game_id)
            item.setData(Qt.ItemDataRole.ToolTipRole, path or "")
            item.setSelected(game_id in selected_ids)
            self.addItem(item)

    def selected_game_ids(self) -> set[int]:
        return {int(item.data(Qt.ItemDataRole.UserRole)) for item in self.selectedItems()}
