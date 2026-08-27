"""
Cloud Account manager dialog — profile, quota, and per-game save versions.

Opened from Settings → Cloud ("Open Account Manager…"). Shows the signed-in
identity, a visual quota bar against the server-enforced budget, and lets the
user inspect/delete historical save generations hosted on the Convex backend.
Network work happens on daemon threads; results marshal back via signals.
"""

import os
import threading
import time

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QPushButton,
    QProgressBar, QListWidget, QListWidgetItem, QMessageBox, QSplitter,
    QComboBox,
)

from ui.components.sidebar import DialogTitleBar, add_soft_shadow
from core.logger import get_logger

logger = get_logger("AccountDialog")

try:
    from ui.dialogs.save_conflict_dialog import format_bytes
except ImportError:  # pragma: no cover - standalone safety
    def format_bytes(size_bytes: int) -> str:
        size = float(size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


def _relative_time(ts: float) -> str:
    if ts <= 0:
        return "never"
    delta = time.time() - ts
    for label, span in (("min", 60), ("hr", 3600), ("day", 86400)):
        if delta < span:
            unit = 1 if label == "min" else (3600 if label == "hr" else 86400)
            value = max(1, int(delta // unit))
            plural = "" if value == 1 else "s"
            return f"{value} {label}{plural} ago"
    weeks = int(delta // 604800)
    return f"{weeks} week{'s' if weeks != 1 else ''} ago"


class AccountDialog(QDialog):
    """Frameless profile manager for SafeLauncher cloud saves."""

    _data_ready = pyqtSignal(object)   # {'ok': {...}} | {'error': str}
    _op_done = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SafeLauncher Account")
        self.setFixedSize(780, 560)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self._games = []
        self._quota = {}
        self._busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(DialogTitleBar(self, "Cloud Account"))

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(20, 16, 20, 16)
        body_layout.setSpacing(12)

        # --- identity header -------------------------------------------------
        header_row = QHBoxLayout()
        self.lbl_avatar = QLabel("?")
        self.lbl_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_avatar.setFixedSize(44, 44)
        self._style_avatar("?", ok=False)
        header_row.addWidget(self.lbl_avatar)

        ident_col = QVBoxLayout()
        ident_col.setSpacing(2)
        self.lbl_email = QLabel("Not signed in")
        self.lbl_email.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        self.lbl_email.setStyleSheet("color: #F5F7FA;")
        ident_col.addWidget(self.lbl_email)
        self.lbl_subject = QLabel("")
        self.lbl_subject.setStyleSheet("color: #6F7682; font-size: 11px;")
        ident_col.addWidget(self.lbl_subject)
        header_row.addLayout(ident_col)
        header_row.addStretch()

        self.combo_backend = QComboBox()
        self.combo_backend.addItem("Backend: Local folder sync", "local")
        self.combo_backend.addItem("Backend: Convex account", "convex")
        from core.cloud_save_sync import cloud_mode
        self.combo_backend.setCurrentIndex(1 if cloud_mode() == "convex" else 0)
        self.combo_backend.currentIndexChanged.connect(self._on_backend_changed)
        self.combo_backend.setMinimumWidth(220)
        header_row.addWidget(self.combo_backend)
        body_layout.addLayout(header_row)

        # --- quota bar --------------------------------------------------------
        quota_box = QWidget()
        quota_layout = QVBoxLayout(quota_box)
        quota_layout.setContentsMargins(0, 0, 0, 0)
        quota_layout.setSpacing(4)
        self.bar_quota = QProgressBar()
        self.bar_quota.setFixedHeight(14)
        self.bar_quota.setTextVisible(False)
        self.bar_quota.setRange(0, 1000)
        self.bar_quota.setValue(0)
        self.bar_quota.setStyleSheet("""
            QProgressBar {
                background: #18181B;
                border: 1px solid #27272A;
                border-radius: 7px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #3B9FE8, stop:1 #7C5CFF);
                border-radius: 6px;
            }
        """)
        quota_layout.addWidget(self.bar_quota)
        self.lbl_quota_text = QLabel("Connect an account to see cloud usage.")
        self.lbl_quota_text.setStyleSheet("color: #9CA3AF; font-size: 11px;")
        quota_layout.addWidget(self.lbl_quota_text)
        body_layout.addWidget(quota_box)

        # --- games / versions -------------------------------------------------
        self.games_split = QSplitter(Qt.Orientation.Horizontal)
        self.games_split.setChildrenCollapsible(False)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        lbl_games = QLabel("Games in your cloud")
        lbl_games.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        lbl_games.setStyleSheet("color: #FFFFFF;")
        left_layout.addWidget(lbl_games)
        self.lst_games = QListWidget()
        self.lst_games.currentRowChanged.connect(self._on_game_selected)
        self.lst_games.setStyleSheet(
            "QListWidget { background:#121214; border:1px solid #27272A; border-radius:6px; color:#E5E7EB; }"
            "QListWidget::item { padding:7px; }"
            "QListWidget::item:selected { background:#3B9FE8; color:white; }"
        )
        left_layout.addWidget(self.lst_games)
        self.games_split.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        lbl_versions = QLabel("Stored versions")
        lbl_versions.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        lbl_versions.setStyleSheet("color: #FFFFFF;")
        right_layout.addWidget(lbl_versions)
        self.lst_versions = QListWidget()
        self.lst_versions.setStyleSheet(
            "QListWidget { background:#121214; border:1px solid #27272A; border-radius:6px; color:#E5E7EB; }"
            "QListWidget::item { padding:7px; }"
        )
        right_layout.addWidget(self.lst_versions)
        btn_delete = QPushButton("Delete Selected Generation")
        btn_delete.setStyleSheet(
            "QPushButton { background:#27272A; color:#F05D6C; border:1px solid #3F3F46;"
            "border-radius:5px; padding:6px 12px; }"
            "QPushButton:hover { border-color:#F05D6C; }"
        )
        btn_delete.clicked.connect(self._delete_selected_version)
        right_layout.addWidget(btn_delete)
        self.games_split.addWidget(right_panel)
        self.games_split.setStretchFactor(0, 3)
        self.games_split.setStretchFactor(1, 2)
        body_layout.addWidget(self.games_split, 1)

        # --- footer actions ----------------------------------------------------
        footer = QHBoxLayout()
        footer.setSpacing(8)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.reload)
        footer.addWidget(btn_refresh)
        self.btn_auth_toggle = QPushButton("Sign In…")
        self.btn_auth_toggle.clicked.connect(self._auth_action)
        footer.addWidget(self.btn_auth_toggle)
        footer.addStretch()

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_close.setDefault(True)
        footer.addWidget(btn_close)
        body_layout.addLayout(footer)

        root.addWidget(body)
        add_soft_shadow(self)

        self._data_ready.connect(self._apply_data)
        self._op_done.connect(self._apply_op)
        self.reload()

    # ------------------------------------------------------------------ #
    # Data loading                                                        #
    # ------------------------------------------------------------------ #

    def reload(self):
        if self._busy:
            return
        self._busy = True
        self.lbl_quota_text.setText("Loading…")
        threading.Thread(target=self._load_worker, daemon=True,
                         name="SafeLauncher-AccountLoad").start()

    def _load_worker(self):
        try:
            from core import clerk_auth
            status = clerk_auth.get_status()
            if not status.get("signed_in"):
                self._data_ready.emit({"ok": None})   # signed-out state
                return
            from core.cloud_backend import ConvexSaveBackend
            backend = ConvexSaveBackend()
            listing = backend.list_games()
            overview = backend.account()
            self._data_ready.emit({
                "ok": {
                    "email": status.get("email"),
                    "listing": listing,
                    "overview": overview,
                },
            })
        except Exception as e:
            logger.warning(f"Account data load failed: {e}")
            self._data_ready.emit({"error": str(e)})

    def _apply_data(self, payload: dict):
        self._busy = False
        if "error" in payload:
            self.lbl_email.setText("Cloud unreachable")
            self.lbl_quota_text.setText(payload["error"])
            self.btn_auth_toggle.setText("Retry")
            return

        snapshot = payload.get("ok")
        if snapshot is None:
            self._render_signed_out()
            return

        overview = snapshot["overview"]
        listing = snapshot["listing"]
        self._quota = {"used": listing.get("bytesUsed", 0),
                       "total": listing.get("quotaBytes", 1)}
        email = overview.get("email") or snapshot.get("email") or "cloud account"
        self.lbl_email.setText(email)
        self.lbl_subject.setText(f"Subject: {overview.get('subject', '')[:24]}… · Quota enforced server-side")
        self._style_avatar(email[0].upper() if email else "?", ok=True)

        pct = min(1.0, self._quota["used"] / max(1, self._quota["total"]))
        self.bar_quota.setValue(int(pct * 1000))
        chunk_color = "#3B9FE8" if pct < 0.75 else ("#EAB308" if pct < 0.92 else "#EF4444")
        self.bar_quota.setStyleSheet(self.bar_quota.styleSheet().replace(
            "stop:0 #3B9FE8", f"stop:0 {chunk_color}"))
        free = self._quota["total"] - self._quota["used"]
        self.lbl_quota_text.setText(
            f"{format_bytes(self._quota['used'])} of {format_bytes(self._quota['total'])} "
            f"used ({format_bytes(max(0, free))} free) · max {format_bytes(overview.get('maxSaveBytes', 0))} per save · "
            f"keeping last {overview.get('keepVersions', '?')} generations"
        )

        self.btn_auth_toggle.setText("Sign Out")
        self._populate_games(listing.get("games", []))

    def _render_signed_out(self):
        self.lbl_email.setText("Not signed in")
        self.lbl_subject.setText("")
        self._style_avatar("?", ok=False)
        self.bar_quota.setValue(0)
        self.lbl_quota_text.setText(
            "Sign in to store your saves (encrypted end-to-end from this PC) "
            "and manage them across machines."
        )
        self.btn_auth_toggle.setText("Sign In…")
        self._populate_games([])

    def _populate_games(self, games):
        self._games = games
        self.lst_games.clear()
        self.lst_versions.clear()
        if not games:
            item = QListWidgetItem("No saves uploaded yet.")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.lst_games.addItem(item)
            return
        for g in sorted(games, key=lambda x: x.get("latestSourceMtime", 0), reverse=True):
            count = len(g.get("versions", []))
            size_txt = format_bytes(g.get("totalBytes", 0))
            item = QListWidgetItem(
                f"{g.get('displayName', g.get('nameKey'))}\n"
                f"{count} generation(s) · {size_txt} · updated {_relative_time(g.get('latestSourceMtime', 0))}"
            )
            item.setData(Qt.ItemDataRole.UserRole, g.get("nameKey"))
            self.lst_games.addItem(item)

    # ------------------------------------------------------------------ #
    # Interactions                                                        #
    # ------------------------------------------------------------------ #

    def _on_game_selected(self, row: int):
        self.lst_versions.clear()
        if row < 0 or row >= len(self._games):
            return
        game = sorted(self._games, key=lambda x: x.get("latestSourceMtime", 0), reverse=True)[row]
        for v in reversed(game.get("versions", [])):
            item = QListWidgetItem(
                f"v{v['version']} — {_relative_time(v.get('createdAt', 0))} · "
                f"{format_bytes(v['sizeBytes'])} · content {v.get('sourceMaxMtime', 0)}"
            )
            item.setData(Qt.ItemDataRole.UserRole, v["version"])
            self.lst_versions.addItem(item)
        if not self.lst_versions.count():
            QListWidgetItem("(pending upload…)", self.lst_versions)

    def _delete_selected_version(self):
        game_item = self.lst_games.currentItem()
        ver_item = self.lst_versions.currentItem()
        if not game_item or not ver_item:
            QMessageBox.information(self, "Nothing selected",
                                    "Pick a game and a stored generation first.")
            return
        name_key = game_item.data(Qt.ItemDataRole.UserRole)
        version = ver_item.data(Qt.ItemDataRole.UserRole)
        confirm = QMessageBox.question(
            self, "Delete generation",
            f"Permanently delete generation v{version} of '{name_key}'?\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        if self._busy:
            return
        self._busy = True

        def _work():
            try:
                from core.cloud_backend import ConvexSaveBackend
                deleted = ConvexSaveBackend().delete_generation(name_key, version)
                self._op_done.emit({"deleted": deleted, "name": name_key})
            except Exception as e:
                self._op_done.emit({"error": str(e)})

        threading.Thread(target=_work, daemon=True,
                         name="SafeLauncher-SaveDelete").start()

    def _apply_op(self, payload: dict):
        self._busy = False
        if "error" in payload:
            QMessageBox.warning(self, "Delete failed", payload["error"])
            return
        self._show_toast_like(payload["name"])
        self.reload()

    def _show_toast_like(self, name_key: str):
        self.lbl_quota_text.setText(f"Deleted old generation for '{name_key}'.")

    def _auth_action(self):
        from core import clerk_auth
        if clerk_auth.get_status().get("signed_in"):
            confirm = QMessageBox.question(
                self, "Sign out",
                "Remove this device's session token?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm == QMessageBox.StandardButton.Yes:
                clerk_auth.clear_stored_session()
                self.reload()
        else:
            self.btn_auth_toggle.setEnabled(False)
            threading.Thread(target=self._login_worker, daemon=True,
                             name="SafeLauncher-DlgLogin").start()

    def _login_worker(self):
        try:
            from core import clerk_auth
            tokens = clerk_auth.login()
            self._data_ready.emit({"ok_login": tokens.get("email", "signed in")})
        except Exception as e:
            self._data_ready.emit({"error": f"Sign-in failed: {e}"})

    def _on_backend_changed(self, index: int):
        from core.cloud_save_sync import set_cloud_mode
        set_cloud_mode(self.combo_backend.itemData(index) or "local")

    def _style_avatar(self, text: str, ok: bool):
        self.lbl_avatar.setText(text)
        color = "#3B9FE8" if ok else "#4B5563"
        self.lbl_avatar.setStyleSheet(
            "QLabel { background: %s; border-radius: 22px; color: white; "
            "font-size: 18px; font-weight: bold; }" % color
        )
