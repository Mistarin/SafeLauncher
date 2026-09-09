"""Account-wide launcher profile and resync controls."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget, QListWidgetItem, QMessageBox
from ui.components.popup_shell import PopupDialog
from core.safe_thread import TaskSupervisor


class AchievementProfileDialog(PopupDialog):
    _sync_done = pyqtSignal(object)

    def __init__(self, db, parent=None):
        super().__init__("Achievement Profile", parent)
        self.db = db
        self._busy = False
        self._tasks = TaskSupervisor(self)
        self._sync_done.connect(self._on_sync_done)
        root = self.popup_layout(margins=(22, 18, 22, 18), spacing=12)
        intro = QLabel("Favorites, playtime, and last-played state sync across devices. Achievements are append-only and remain unlocked even when a game or older save is removed.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#A1A1AA; font-size:12px;")
        root.addWidget(intro)
        stats = QHBoxLayout()
        self.lbl_apps, self.lbl_unlocked, self.lbl_favorites, self.lbl_playtime = QLabel(), QLabel(), QLabel(), QLabel()
        for label in (self.lbl_apps, self.lbl_unlocked, self.lbl_favorites, self.lbl_playtime):
            label.setStyleSheet("color:#F4F4F5; font-size:14px; font-weight:700; padding:8px;")
            stats.addWidget(label)
        root.addLayout(stats)
        self.list = QListWidget()
        self.list.setStyleSheet("QListWidget { background:#121214; border:1px solid #27272A; border-radius:8px; color:#E5E7EB; } QListWidget::item { padding:9px; }")
        root.addWidget(self.list, 1)
        actions = QHBoxLayout()
        self.btn_resync = QPushButton("Resync All")
        self.btn_resync.setToolTip("Union local game state with the cloud profile; never removes unlocks")
        self.btn_resync.clicked.connect(self._resync)
        actions.addWidget(self.btn_resync)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._load)
        actions.addWidget(refresh)
        actions.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        root.addLayout(actions)
        self.setMinimumSize(680, 500)
        self._load()

    def _load(self):
        profile = self.db.get_profile_unlocks()
        profile_games = {item["identity_key"]: item for item in self.db.get_profile_games()}
        games = {}
        for game in self.db.get_all_games():
            sid = str(game.steam_id or "").strip()
            if sid:
                games.setdefault(sid, []).append(game.name)
        self.list.clear()
        total = 0
        for app_id in sorted(profile, key=lambda value: int(value) if value.isdigit() else value):
            count = len(profile[app_id])
            total += count
            names = ", ".join(games.get(app_id, [])) or "No installed game linked"
            item = QListWidgetItem(f"AppID {app_id} · {count} unlocked\n{names}")
            item.setData(Qt.ItemDataRole.UserRole, app_id)
            self.list.addItem(item)
        for identity, item in sorted(profile_games.items()):
            if item.get("favorite") or item.get("playtime_baseline_seconds") or item.get("last_played"):
                hours = item.get("playtime_baseline_seconds", 0) / 3600
                self.list.addItem(QListWidgetItem(
                    f"{identity} · {'Favorite' if item.get('favorite') else 'Not favorite'} · {hours:.1f} h"
                ))
        if not profile:
            self.list.addItem(QListWidgetItem("No profile unlocks recorded yet."))
        self.lbl_apps.setText(f"Apps: {len(profile)}")
        self.lbl_unlocked.setText(f"Unlocked: {total}")
        self.lbl_favorites.setText(f"Favorites: {sum(1 for x in profile_games.values() if x.get('favorite'))}")
        total_hours = sum(int(x.get("playtime_baseline_seconds", 0) or 0) for x in profile_games.values()) / 3600
        self.lbl_playtime.setText(f"Playtime: {total_hours:.1f} h")

    def _resync(self):
        if self._busy:
            return
        self._busy = True
        self.btn_resync.setEnabled(False)
        self.btn_resync.setText("Resyncing…")
        db_path = getattr(self.db, "db_path", None)

        def work():
            from database import GameDatabase
            from core.cloud_metadata_sync import CloudMetadataSync
            worker_db = GameDatabase(db_path) if db_path else GameDatabase()
            try:
                return CloudMetadataSync.sync_profile(worker_db, force=True)
            finally:
                worker_db.close()

        self._sync_worker = self._tasks.start("AchievementProfileResync", work, self._sync_done.emit)

    def _on_sync_done(self, ok):
        self._busy = False
        self.btn_resync.setEnabled(True)
        self.btn_resync.setText("Resync All")
        self._load()
        if not ok:
            QMessageBox.warning(self, "Profile Resync", "The profile could not be synchronized. Local unlocks were preserved.")

    def closeEvent(self, event):
        self._tasks.cancel_all(250)
        super().closeEvent(event)
