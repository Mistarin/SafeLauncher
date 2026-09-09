"""Full-page owner and public profile presentation."""

from __future__ import annotations

import secrets
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal, QSettings, QTimer
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (
    QColorDialog, QFileDialog, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget, QApplication,
)

from core.profile_assets import AvatarError, normalize_avatar
from core.profile_models import (
    BACKGROUND_PRESETS, build_public_projection, generate_profile_handle,
    load_profile_settings, normalize_background, normalize_public_document,
    save_profile_settings,
)
from core.profile_service import ProfileServiceClient, ProfileServiceError, get_profile_service_url
from core.secret_store import get_secret, set_secret
from core.safe_thread import TaskSupervisor
from ui.icons import get_icon
from ui.theme import (
    ACCENT_PRIMARY, BG_APP, BORDER, SEMANTIC_ERROR, SEMANTIC_SUCCESS,
    SURFACE, SURFACE_ELEVATED, TEXT_MUTED, TEXT_PRIMARY, TEXT_SECONDARY,
)


class ProfilePageWidget(QWidget):
    """Persistent profile page with an explicit owner/public mode boundary."""

    back_requested = pyqtSignal()
    open_public_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    profile_changed = pyqtSignal()
    private_profile_changed = pyqtSignal()

    def __init__(self, db, settings: QSettings | None = None, parent=None, worker_registry=None):
        super().__init__(parent)
        self.db = db
        self.settings = settings or QSettings("SafeLauncher", "SafeLauncher")
        self._tasks = TaskSupervisor(self, worker_registry=worker_registry)
        self._mode = "owner"
        self._profile_settings: dict[str, Any] = {}
        self._document: dict[str, Any] = {}
        self._editing = False
        self._draft_avatar = None
        self._public_revision = int(self.settings.value("profile_public_revision", 0, type=int) or 0)
        self._publishing = False
        self._publish_dirty = False
        self._publish_timer = QTimer(self)
        self._publish_timer.setSingleShot(True)
        self._publish_timer.setInterval(1500)
        self._publish_timer.timeout.connect(self._publish_current_document)
        self._build_ui()
        self.show_owner()

    def _build_ui(self) -> None:
        self.setObjectName("profilePage")
        self._base_style = f"""
            QWidget#profilePage {{ background: transparent; color: {TEXT_PRIMARY}; }}
            QScrollArea#profileScroll {{ background: transparent; border: none; }}
            QScrollArea#profileScroll > QWidget > QWidget {{ background: transparent; }}
            QWidget#profileCanvas {{ background: transparent; }}
            QFrame#profileSection {{ background: {SURFACE}; border: none; }}
            QFrame#profileHero {{ border: none; }}
            QLabel#profileEyebrow {{ color: {TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
            QLabel#profileName {{ color: {TEXT_PRIMARY}; font-size: 28px; font-weight: 750; }}
            QLabel#profileHandle {{ color: {TEXT_SECONDARY}; font-size: 12px; }}
            QLabel#profileMuted {{ color: {TEXT_SECONDARY}; font-size: 12px; }}
            QLabel#profileStatValue {{ color: {TEXT_PRIMARY}; font-size: 20px; font-weight: 750; }}
            QLabel#profileStatCaption {{ color: {TEXT_MUTED}; font-size: 11px; }}
            QListWidget#profileList {{ background: {SURFACE}; color: {TEXT_PRIMARY}; border: none; outline: none; }}
            QListWidget#profileList::item {{ padding: 8px 4px; border: none; }}
            QLineEdit#profileEditorInput, QComboBox#profileEditorInput {{
                background: {SURFACE_ELEVATED}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER};
                border-radius: 6px; padding: 7px 9px; font-size: 12px;
            }}
            QLineEdit#profileEditorInput:focus, QComboBox#profileEditorInput:focus {{ border-color: {ACCENT_PRIMARY}; }}
        """
        self.setStyleSheet(self._base_style)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(16, 10, 16, 8)
        toolbar.setSpacing(8)
        self.btn_back = QPushButton("Library")
        self.btn_back.setIcon(get_icon("ph.arrow-left-bold", color=TEXT_SECONDARY))
        self.btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_back.clicked.connect(self.back_requested.emit)
        toolbar.addWidget(self.btn_back)
        toolbar.addStretch()
        self.mode_label = QLabel("OWNER VIEW")
        self.mode_label.setObjectName("profileEyebrow")
        toolbar.addWidget(self.mode_label)
        self.btn_open_public = QPushButton("Open public profile")
        self.btn_open_public.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_public.clicked.connect(self.open_public_requested.emit)
        toolbar.addWidget(self.btn_open_public)
        self.btn_settings = QPushButton()
        self.btn_settings.setIcon(get_icon("ph.gear-bold", color=TEXT_SECONDARY))
        self.btn_settings.setToolTip("Open Settings")
        self.btn_settings.setFixedSize(32, 30)
        self.btn_settings.clicked.connect(self.settings_requested.emit)
        toolbar.addWidget(self.btn_settings)
        root.addLayout(toolbar)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("profileScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        content = QWidget()
        content.setObjectName("profileCanvas")
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(22, 14, 22, 26)
        content_layout.addStretch(1)
        self.column = QWidget()
        self.column.setMaximumWidth(920)
        self.column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.column_layout = QVBoxLayout(self.column)
        self.column_layout.setContentsMargins(0, 0, 0, 0)
        self.column_layout.setSpacing(14)
        content_layout.addWidget(self.column, 10)
        content_layout.addStretch(1)
        self.scroll.setWidget(content)
        root.addWidget(self.scroll, 1)

        self.hero = QFrame()
        self.hero.setObjectName("profileHero")
        self.hero.setMinimumHeight(220)
        hero_layout = QHBoxLayout(self.hero)
        hero_layout.setContentsMargins(30, 28, 30, 28)
        hero_layout.setSpacing(22)
        self.avatar = QLabel()
        self.avatar.setFixedSize(128, 128)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_layout.addWidget(self.avatar, alignment=Qt.AlignmentFlag.AlignVCenter)
        identity = QVBoxLayout()
        identity.setSpacing(5)
        self.public_badge = QLabel("PUBLIC PROFILE")
        self.public_badge.setObjectName("profileEyebrow")
        identity.addWidget(self.public_badge)
        self.name_label = QLabel()
        self.name_label.setObjectName("profileName")
        self.name_label.setWordWrap(True)
        identity.addWidget(self.name_label)
        self.handle_label = QLabel()
        self.handle_label.setObjectName("profileHandle")
        identity.addWidget(self.handle_label)
        self.status_label = QLabel()
        self.status_label.setObjectName("profileMuted")
        self.status_label.setWordWrap(True)
        identity.addWidget(self.status_label)
        identity.addStretch()
        hero_layout.addLayout(identity, 1)
        self.column_layout.addWidget(self.hero)

        self.editor = QFrame()
        self.editor.setObjectName("profileSection")
        editor_layout = QVBoxLayout(self.editor)
        editor_layout.setContentsMargins(18, 16, 18, 16)
        editor_layout.setSpacing(10)
        editor_title = QLabel("Edit profile")
        editor_title.setStyleSheet(f"color:{TEXT_PRIMARY}; font-size:14px; font-weight:700;")
        editor_layout.addWidget(editor_title)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Display name"))
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("profileEditorInput")
        self.name_edit.setMaxLength(64)
        name_row.addWidget(self.name_edit, 1)
        editor_layout.addLayout(name_row)
        avatar_row = QHBoxLayout()
        avatar_row.addWidget(QLabel("Avatar"))
        self.btn_avatar = QPushButton("Choose image")
        self.btn_avatar.clicked.connect(self._choose_avatar)
        avatar_row.addWidget(self.btn_avatar)
        self.btn_remove_avatar = QPushButton("Remove")
        self.btn_remove_avatar.clicked.connect(self._remove_avatar)
        avatar_row.addWidget(self.btn_remove_avatar)
        avatar_row.addStretch()
        editor_layout.addLayout(avatar_row)
        background_row = QHBoxLayout()
        background_row.addWidget(QLabel("Background"))
        self.background_combo = QComboBox()
        self.background_combo.setObjectName("profileEditorInput")
        self.background_combo.addItem("Midnight", "midnight")
        self.background_combo.addItem("Ember", "ember")
        self.background_combo.addItem("Forest", "forest")
        self.background_combo.addItem("Violet", "violet")
        self.background_combo.addItem("Slate", "slate")
        self.background_combo.addItem("Custom solid color", "custom")
        background_row.addWidget(self.background_combo, 1)
        self.btn_custom_color = QPushButton("Color")
        self.btn_custom_color.clicked.connect(self._choose_color)
        background_row.addWidget(self.btn_custom_color)
        editor_layout.addLayout(background_row)
        service_row = QHBoxLayout()
        service_row.addWidget(QLabel("Profile service"))
        self.service_url_edit = QLineEdit()
        self.service_url_edit.setObjectName("profileEditorInput")
        self.service_url_edit.setPlaceholderText("https://your-profile-service.convex.site")
        service_row.addWidget(self.service_url_edit, 1)
        editor_layout.addLayout(service_row)
        self.editor_hint = QLabel("The profile service stores only this public projection. Private save data stays on your configured cloud.")
        self.editor_hint.setObjectName("profileMuted")
        self.editor_hint.setWordWrap(True)
        editor_layout.addWidget(self.editor_hint)
        editor_actions = QHBoxLayout()
        editor_actions.addStretch()
        self.btn_cancel_edit = QPushButton("Cancel")
        self.btn_cancel_edit.clicked.connect(self._cancel_edit)
        editor_actions.addWidget(self.btn_cancel_edit)
        self.btn_save_edit = QPushButton("Save changes")
        self.btn_save_edit.setObjectName("profilePrimary")
        self.btn_save_edit.clicked.connect(self._save_edit)
        editor_actions.addWidget(self.btn_save_edit)
        editor_layout.addLayout(editor_actions)
        self.column_layout.addWidget(self.editor)

        self.stats_section = self._section("Profile stats")
        self.stats_grid = QGridLayout()
        self.stats_section.layout().addLayout(self.stats_grid)
        self.stats_grid.setContentsMargins(18, 8, 18, 16)
        self.stats_grid.setHorizontalSpacing(18)
        self.stat_labels = {}
        stat_items = (("games_count", "Games"), ("favorite_count", "Favorites"), ("playtime_seconds", "Playtime"), ("achievements_unlocked", "Achievements"))
        for index, (key, caption) in enumerate(stat_items):
            box = QVBoxLayout()
            value = QLabel("0")
            value.setObjectName("profileStatValue")
            label = QLabel(caption)
            label.setObjectName("profileStatCaption")
            box.addWidget(value)
            box.addWidget(label)
            self.stats_grid.addLayout(box, 0, index)
            self.stat_labels[key] = value
        self.column_layout.addWidget(self.stats_section)

        self.favorite_section = self._section("Favorite games")
        favorite_layout = self.favorite_section.layout()
        favorite_layout.setContentsMargins(18, 14, 18, 14)
        self.favorite_list = QListWidget()
        self.favorite_list.setObjectName("profileList")
        self.favorite_list.setMinimumHeight(66)
        self.favorite_list.setMaximumHeight(210)
        favorite_layout.addWidget(self.favorite_list)
        self.column_layout.addWidget(self.favorite_section)

        self.achievement_section = self._section("Recent achievements")
        achievement_layout = self.achievement_section.layout()
        achievement_layout.setContentsMargins(18, 14, 18, 14)
        self.achievement_list = QListWidget()
        self.achievement_list.setObjectName("profileList")
        self.achievement_list.setMinimumHeight(66)
        self.achievement_list.setMaximumHeight(250)
        achievement_layout.addWidget(self.achievement_list)
        self.column_layout.addWidget(self.achievement_section)

        bottom = QHBoxLayout()
        self.btn_edit = QPushButton("Edit profile")
        self.btn_edit.setIcon(get_icon("ph.pencil-simple-bold", color="#FFFFFF"))
        self.btn_edit.clicked.connect(self._start_edit)
        bottom.addWidget(self.btn_edit)
        self.btn_publish = QPushButton("Publish profile")
        self.btn_publish.setIcon(get_icon("ph.upload-simple-bold", color="#FFFFFF"))
        self.btn_publish.clicked.connect(self._publish)
        bottom.addWidget(self.btn_publish)
        self.btn_copy_handle = QPushButton("Copy handle")
        self.btn_copy_handle.clicked.connect(self._copy_handle)
        bottom.addWidget(self.btn_copy_handle)
        self.btn_resync = QPushButton("Resync private data")
        self.btn_resync.setIcon(get_icon("ph.arrows-clockwise-bold", color=TEXT_SECONDARY))
        self.btn_resync.clicked.connect(self._resync_private)
        bottom.addWidget(self.btn_resync)
        bottom.addStretch()
        self.btn_rotate = QPushButton("Rotate owner token")
        self.btn_rotate.clicked.connect(self._rotate_token)
        bottom.addWidget(self.btn_rotate)
        self.column_layout.addLayout(bottom)
        self.footer_status = QLabel()
        self.footer_status.setObjectName("profileMuted")
        self.footer_status.setWordWrap(True)
        self.column_layout.addWidget(self.footer_status)

    @staticmethod
    def _section(title: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("profileSection")
        layout = QVBoxLayout(frame)
        layout.setSpacing(8)
        label = QLabel(title)
        label.setStyleSheet(f"color:{TEXT_PRIMARY}; font-size:14px; font-weight:700;")
        layout.addWidget(label)
        return frame

    def show_owner(self) -> None:
        self._mode = "owner"
        self.mode_label.setText("OWNER VIEW")
        self._profile_settings = load_profile_settings(self.settings, fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"))
        self._document = build_public_projection(self.db, self._profile_settings)
        self.editor.setVisible(self._editing)
        self.btn_edit.setVisible(not self._editing)
        self._set_admin_controls(True)
        self._render(self._document)

    def mark_local_data_changed(self) -> None:
        """Refresh local stats and coalesce a public update after game events."""
        if self._mode != "owner" or self._editing:
            return
        self._profile_settings = load_profile_settings(self.settings, fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"))
        if self.isVisible():
            self._document = build_public_projection(self.db, self._profile_settings)
            self._render(self._document)
        if self._profile_settings.get("published") and get_secret("profile_owner_token"):
            if self._publishing:
                self._publish_dirty = True
            else:
                self._publish_timer.start()

    def show_public(self, document: dict[str, Any]) -> bool:
        normalized = normalize_public_document(document)
        if normalized is None:
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText("This public profile is invalid or unavailable.")
            return False
        self._mode = "public"
        self._editing = False
        self._document = normalized
        self.mode_label.setText("PUBLIC VIEW")
        self.editor.setVisible(False)
        self._set_admin_controls(False)
        self._render(normalized)
        return True

    def _set_admin_controls(self, enabled: bool) -> None:
        has_owner_token = bool(get_secret("profile_owner_token"))
        published = bool(self._profile_settings.get("published"))
        self.btn_edit.setVisible(enabled and not self._editing)
        self.btn_publish.setVisible(enabled)
        self.btn_publish.setEnabled(not published or has_owner_token)
        self.btn_publish.setToolTip(
            "This profile is managed on another device; its owner token is not synced."
            if published and not has_owner_token else ""
        )
        self.btn_resync.setVisible(enabled)
        self.btn_rotate.setVisible(enabled and bool(self._profile_settings.get("public_handle")) and has_owner_token)
        self.btn_copy_handle.setVisible(enabled and published)
        self.btn_open_public.setVisible(enabled)
        self.btn_settings.setVisible(True)

    def _background_style(self, background: dict[str, Any]) -> str:
        background = normalize_background(background)
        if background.get("kind") == "solid":
            return (
                f"QWidget#profileCanvas {{ background: {background['color']}; }}"
                f" QFrame#profileHero {{ background: {background['color']}; }}"
            )
        stops = background.get("stops", ["#1C3158", BG_APP])
        return (
            "QWidget#profileCanvas, QFrame#profileHero { background: "
            f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {stops[0]}, stop:1 {stops[-1]}); }}"
        )

    def _render(self, document: dict[str, Any]) -> None:
        self.setStyleSheet(self._base_style + self._background_style(document.get("background", {})))
        self.name_label.setText(str(document.get("display_name", "Player")))
        handle = str(document.get("handle", "") or "")
        self.handle_label.setText(f"@{handle}" if handle else "Not published yet")
        stats = document.get("stats", {}) if isinstance(document.get("stats"), dict) else {}
        self.stat_labels["games_count"].setText(str(stats.get("games_count", 0)))
        self.stat_labels["favorite_count"].setText(str(stats.get("favorite_count", 0)))
        seconds = max(0, int(stats.get("playtime_seconds", 0) or 0))
        self.stat_labels["playtime_seconds"].setText(self._format_hours(seconds))
        unlocked = int(stats.get("achievements_unlocked", 0) or 0)
        known = int(stats.get("achievements_known", 0) or 0)
        self.stat_labels["achievements_unlocked"].setText(f"{unlocked}/{known}" if known else str(unlocked))
        self._set_avatar(document.get("avatar"))
        if self._mode == "owner":
            published = bool(self._profile_settings.get("published"))
            has_owner_token = bool(get_secret("profile_owner_token"))
            self.public_badge.setText("PUBLIC PROFILE" if published else "LOCAL PROFILE")
            if published and has_owner_token:
                self.status_label.setText("Published and visible by handle.")
            elif published:
                self.status_label.setText("Published from another device; public management is unavailable here.")
            else:
                self.status_label.setText("Only you can see this profile until it is published.")
            self.btn_publish.setText("Unpublish profile" if published else "Publish profile")
            self.btn_copy_handle.setVisible(published)
            self.footer_status.setText("Public publishing is separate from private game-save cloud synchronization.")
            self._populate_editor()
        else:
            self.public_badge.setText("PUBLIC PROFILE")
            self.status_label.setText("Read-only profile")
            self.footer_status.setText("This is a public projection. Private launcher data is not shown.")
        self._fill_list(self.favorite_list, document.get("favorite_games"), lambda item: f"♥  {item.get('name', 'Favorite game')}")
        self._fill_list(self.achievement_list, document.get("recent_achievements"), lambda item: f"★  {item.get('name', 'Achievement')}  ·  {item.get('game', 'Game')}")

    @staticmethod
    def _format_hours(seconds: int) -> str:
        hours = seconds / 3600
        return f"{hours:.1f} h"

    @staticmethod
    def _fill_list(widget: QListWidget, values: Any, formatter) -> None:
        widget.clear()
        values = values if isinstance(values, list) else []
        if not values:
            widget.addItem(QListWidgetItem("Nothing public yet."))
            return
        for item in values:
            if isinstance(item, dict):
                widget.addItem(QListWidgetItem(formatter(item)))

    def _set_avatar(self, avatar: Any) -> None:
        pixmap = QPixmap()
        if isinstance(avatar, dict):
            try:
                import base64
                pixmap.loadFromData(base64.b64decode(str(avatar.get("data_b64", "")), validate=True), "JPEG")
            except Exception:
                pixmap = QPixmap()
        if not pixmap.isNull():
            self.avatar.setPixmap(pixmap.scaled(128, 128, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
            self.avatar.setStyleSheet("border-radius:64px; background:#20242C;")
        else:
            self.avatar.clear()
            self.avatar.setText("SL")
            self.avatar.setStyleSheet(f"border-radius:64px; background:{SURFACE_ELEVATED}; color:{ACCENT_PRIMARY}; font-size:30px; font-weight:800;")

    def _populate_editor(self) -> None:
        if self._mode != "owner":
            return
        self.name_edit.setText(self._profile_settings.get("display_name", "Player"))
        self.service_url_edit.setText(get_profile_service_url())
        background = normalize_background(self._profile_settings.get("background"))
        found = False
        for index in range(self.background_combo.count()):
            candidate = BACKGROUND_PRESETS.get(self.background_combo.itemData(index))
            if candidate and candidate == background:
                self.background_combo.setCurrentIndex(index)
                found = True
                break
        if not found:
            self.background_combo.setCurrentIndex(self.background_combo.findData("custom"))

    def _start_edit(self) -> None:
        if self._mode != "owner":
            return
        self._editing = True
        self._draft_avatar = self._profile_settings.get("avatar")
        self.editor.setVisible(True)
        self.btn_edit.setVisible(False)
        self._populate_editor()

    def _cancel_edit(self) -> None:
        self._editing = False
        self._draft_avatar = None
        self.show_owner()

    def _save_edit(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Profile", "Enter a display name.")
            return
        selected = self.background_combo.currentData()
        if selected == "custom":
            background = self._profile_settings.get("background", {})
        else:
            background = BACKGROUND_PRESETS.get(selected, BACKGROUND_PRESETS["midnight"])
        value = dict(self._profile_settings)
        value.update({
            "display_name": name,
            "avatar": self._draft_avatar,
            "background": background,
        })
        normalized = save_profile_settings(self.settings, value)
        service_url = self.service_url_edit.text().strip().rstrip("/")
        self.settings.setValue("profile_service_url", service_url)
        self.settings.sync()
        self._profile_settings = normalized
        self._editing = False
        self._draft_avatar = None
        self.profile_changed.emit()
        self.show_owner()
        self.footer_status.setStyleSheet(f"color:{SEMANTIC_SUCCESS};")
        self.footer_status.setText("Profile changes saved locally. Publish to update the public profile.")

    def _choose_avatar(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose profile picture", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)")
        if not path:
            return
        try:
            self._draft_avatar = normalize_avatar(path)
            self._set_avatar(self._draft_avatar)
        except AvatarError as exc:
            QMessageBox.warning(self, "Profile picture", str(exc))

    def _remove_avatar(self) -> None:
        self._draft_avatar = None
        self._set_avatar(None)

    def _choose_color(self) -> None:
        current = normalize_background(self._profile_settings.get("background"))
        initial = QColor(str(current.get("color") or current.get("stops", ["#20242C"])[0]))
        color = QColorDialog.getColor(initial, self, "Choose profile background")
        if color.isValid():
            draft_name = self.name_edit.text()
            draft_service_url = self.service_url_edit.text()
            self.background_combo.setCurrentIndex(self.background_combo.findData("custom"))
            self._profile_settings["background"] = {"kind": "solid", "color": color.name().upper()}
            self._render(build_public_projection(self.db, {
                **self._profile_settings,
                "display_name": draft_name or self._profile_settings.get("display_name", "Player"),
                "avatar": self._draft_avatar,
                "background": self._profile_settings["background"],
            }))
            # Rendering also refreshes the owner editor. Restore the in-flight
            # draft so choosing a color does not discard typed fields.
            self.name_edit.setText(draft_name)
            self.service_url_edit.setText(draft_service_url)

    def _publish(self) -> None:
        if self._mode != "owner":
            return
        published = bool(self._profile_settings.get("published"))
        if published:
            if not get_secret("profile_owner_token"):
                self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
                self.footer_status.setText("This profile is managed on another device; its owner token is not available here.")
                return
            self._unpublish()
            return
        service_url = get_profile_service_url()
        if not service_url.startswith(("http://", "https://")):
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText("Set a Profile service URL in Edit profile before publishing.")
            return
        handle = self._profile_settings.get("public_handle") or generate_profile_handle()
        token = get_secret("profile_owner_token") or secrets.token_urlsafe(32)
        if not set_secret("profile_owner_token", token):
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText("SafeLauncher could not securely store the profile owner token.")
            return
        self._profile_settings = save_profile_settings(self.settings, {**self._profile_settings, "public_handle": handle}, mark_changed=False)
        self._publish_current_document()

    def _copy_handle(self) -> None:
        handle = str(self._profile_settings.get("public_handle", "") or "")
        if not handle:
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(handle)
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_SUCCESS};")
            self.footer_status.setText("Public profile handle copied to the clipboard.")

    def _publish_current_document(self) -> None:
        if self._mode != "owner" or self._publishing:
            return
        service_url = get_profile_service_url()
        handle = str(self._profile_settings.get("public_handle", "") or "")
        token = get_secret("profile_owner_token")
        if not service_url.startswith(("http://", "https://")) or not handle or not token:
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText("Public profile publishing is not configured.")
            return
        profile_settings = dict(self._profile_settings)
        db_path = getattr(self.db, "db_path", None)
        self._publishing = True
        self._publish_dirty = False
        self.btn_publish.setEnabled(False)
        self.footer_status.setText("Publishing public profile…")

        def work():
            from database import GameDatabase
            worker_db = None
            try:
                # SQLite connections are thread-affine. Use an independent
                # connection for production databases so achievement and
                # playtime projection never blocks the GUI thread.
                if db_path and db_path != ":memory:":
                    worker_db = GameDatabase(db_path)
                    document = build_public_projection(worker_db, profile_settings)
                else:
                    # In-memory databases are used only by tests/embedders;
                    # they have no second connection that could see the data.
                    document = build_public_projection(self.db, profile_settings)
            finally:
                if worker_db is not None:
                    worker_db.close()
            client = ProfileServiceClient(service_url, token)
            try:
                if self._public_revision:
                    response = client.update(handle, document, self._public_revision)
                else:
                    response = client.create(handle, document)
            except ProfileServiceError as exc:
                if exc.code not in {"profile_exists", "revision_conflict"}:
                    raise
                remote = client.fetch(handle)
                response = client.update(handle, document, int(remote.get("revision", 0) or 0))
            return {"response": response, "document": document}

        worker = self._tasks.start("SafeLauncher-PublishProfile", work, self._publish_done)
        worker.error_occurred.connect(lambda error: self._publish_done(ProfileServiceError(error, "publish_failed")))

    def _publish_done(self, result: Any) -> None:
        self._publishing = False
        self.btn_publish.setEnabled(True)
        if isinstance(result, Exception):
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText(str(result))
            return
        result = result if isinstance(result, dict) else {}
        response = result.get("response") if isinstance(result.get("response"), dict) else result
        document = result.get("document") if isinstance(result.get("document"), dict) else None
        self._public_revision = int(response.get("revision", self._public_revision or 1))
        self.settings.setValue("profile_public_revision", self._public_revision)
        self.settings.sync()
        self._profile_settings = save_profile_settings(self.settings, {**self._profile_settings, "published": True}, mark_changed=False)
        if document is not None:
            self._document = document
        if self.isVisible():
            self._render(self._document or build_public_projection(self.db, self._profile_settings))
        self.footer_status.setStyleSheet(f"color:{SEMANTIC_SUCCESS};")
        self.footer_status.setText(f"Published. Share profile handle @{self._profile_settings.get('public_handle')}.")
        self.private_profile_changed.emit()
        if self._publish_dirty:
            self._publish_timer.start()

    def _unpublish(self) -> None:
        self._publish_timer.stop()
        service_url = get_profile_service_url()
        handle = str(self._profile_settings.get("public_handle", "") or "")
        token = get_secret("profile_owner_token")
        if not service_url or not handle or not token:
            self._profile_settings = save_profile_settings(self.settings, {**self._profile_settings, "published": False}, mark_changed=False)
            self._public_revision = 0
            self.settings.setValue("profile_public_revision", 0)
            self.settings.sync()
            self.show_owner()
            self.private_profile_changed.emit()
            return
        self.btn_publish.setEnabled(False)
        self.footer_status.setText("Unpublishing public profile…")
        worker = self._tasks.start("SafeLauncher-UnpublishProfile", lambda: ProfileServiceClient(service_url, token).delete(handle), self._unpublish_done)
        worker.error_occurred.connect(lambda error: self._unpublish_done(ProfileServiceError(error, "unpublish_failed")))

    def _unpublish_done(self, result: Any) -> None:
        self.btn_publish.setEnabled(True)
        if isinstance(result, Exception):
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText(str(result))
            return
        self._profile_settings = save_profile_settings(self.settings, {**self._profile_settings, "published": False}, mark_changed=False)
        self._public_revision = 0
        self.settings.setValue("profile_public_revision", 0)
        self.settings.sync()
        self.show_owner()
        self.footer_status.setText("Profile unpublished. The owner token remains available for publishing again.")
        self.private_profile_changed.emit()

    def _rotate_token(self) -> None:
        handle = str(self._profile_settings.get("public_handle", "") or "")
        old = get_secret("profile_owner_token")
        if not handle or not old:
            return
        if QMessageBox.question(self, "Rotate owner token", "The old token will stop working immediately. Continue?") != QMessageBox.StandardButton.Yes:
            return
        new = secrets.token_urlsafe(32)
        service_url = get_profile_service_url()
        if not service_url.startswith(("http://", "https://")):
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText("Set a Profile service URL before rotating the owner token.")
            return
        self.btn_rotate.setEnabled(False)
        worker = self._tasks.start(
            "SafeLauncher-RotateProfileToken",
            lambda: ProfileServiceClient(service_url, old).rotate_token(handle, new),
            lambda result: self._rotate_done(result, new),
        )
        worker.error_occurred.connect(lambda error: self._rotate_done(ProfileServiceError(error, "rotate_failed"), new))

    def _rotate_done(self, result: Any, new_token: str) -> None:
        self.btn_rotate.setEnabled(True)
        if isinstance(result, Exception):
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText(str(result))
            return
        if set_secret("profile_owner_token", new_token):
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_SUCCESS};")
            self.footer_status.setText("Owner token rotated and securely stored.")
        else:
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText("The service rotated the token, but local secure storage failed. Save the new token before closing.")

    def _resync_private(self) -> None:
        self.btn_resync.setEnabled(False)
        db_path = getattr(self.db, "db_path", None)
        def work():
            from database import GameDatabase
            from core.cloud_metadata_sync import CloudMetadataSync
            worker_db = GameDatabase(db_path) if db_path else GameDatabase()
            try:
                return CloudMetadataSync.sync_profile(worker_db, force=True)
            finally:
                worker_db.close()
        self.footer_status.setText("Resyncing private profile data…")
        worker = self._tasks.start("SafeLauncher-ProfileResync", work, self._resync_done)
        worker.error_occurred.connect(lambda error: self._resync_done(False))

    def _resync_done(self, result: Any) -> None:
        self.btn_resync.setEnabled(True)
        if isinstance(result, Exception) or not result:
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText("Private profile resync failed; local data was preserved.")
            return
        self.show_owner()
        self.footer_status.setStyleSheet(f"color:{SEMANTIC_SUCCESS};")
        self.footer_status.setText("Private profile resynchronized.")

    def set_public_service_url(self, url: str) -> None:
        self.settings.setValue("profile_service_url", str(url or "").strip().rstrip("/"))
        self.settings.sync()

    def closeEvent(self, event) -> None:
        self._tasks.cancel_all(250)
        super().closeEvent(event)
