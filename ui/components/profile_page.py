"""Full-page owner and public profile presentation."""

from __future__ import annotations

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
    HANDLE_RE, load_profile_settings, normalize_background,
    normalize_public_document, save_profile_settings,
)
from core.profile_service import ProfileServiceClient, ProfileServiceError, get_profile_service_url, is_local_service_url
from core.central_auth import CentralAuthError, CentralAuthSession
from core.secret_store import delete_secret, get_secret
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
    open_profile_handle_requested = pyqtSignal(str)
    profile_changed = pyqtSignal()
    private_profile_changed = pyqtSignal()
    auth_progress = pyqtSignal(str)

    def __init__(self, db, settings: QSettings | None = None, parent=None, worker_registry=None, auth_session=None):
        super().__init__(parent)
        self.db = db
        self.settings = settings or QSettings("SafeLauncher", "SafeLauncher")
        self.central_auth = auth_session or CentralAuthSession()
        self._tasks = TaskSupervisor(self, worker_registry=worker_registry)
        self._mode = "owner"
        self._profile_settings: dict[str, Any] = {}
        self._document: dict[str, Any] = {}
        self._editing = False
        self._draft_avatar = None
        self._public_revision = int(self.settings.value("profile_public_revision", 0, type=int) or 0)
        self._publishing = False
        self._publish_dirty = False
        self._social_snapshot: dict[str, Any] = {
            "friends": [],
            "incoming_requests": [],
            "outgoing_requests": [],
            "blocked_handles": [],
        }
        self._social_handle = ""
        self._social_loading = False
        self._social_mutating = False
        self._auth_in_flight = False
        self._publish_timer = QTimer(self)
        self._publish_timer.setSingleShot(True)
        self._publish_timer.setInterval(1500)
        self._publish_timer.timeout.connect(self._publish_current_document)
        self.auth_progress.connect(self._on_auth_progress)
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
            QFrame#profileSocialRow {{ background: transparent; border: none; }}
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
        self.name_label.setTextFormat(Qt.TextFormat.PlainText)
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
        self.service_url_edit.setPlaceholderText("https://profilegateway.vercel.app")
        self.service_url_edit.setReadOnly(True)
        self.service_url_edit.setToolTip("The official SafeLauncher profile gateway is used for public profiles.")
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

        self.friends_section = self._section("Friends")
        friends_layout = self.friends_section.layout()
        friends_layout.setContentsMargins(18, 14, 18, 14)
        self.friends_hint = QLabel()
        self.friends_hint.setObjectName("profileMuted")
        self.friends_hint.setWordWrap(True)
        friends_layout.addWidget(self.friends_hint)
        self.friend_controls = QHBoxLayout()
        self.friend_handle_edit = QLineEdit()
        self.friend_handle_edit.setObjectName("profileEditorInput")
        self.friend_handle_edit.setPlaceholderText("Paste a profile handle")
        self.friend_handle_edit.setMaxLength(40)
        self.friend_handle_edit.returnPressed.connect(self._send_friend_request)
        self.friend_controls.addWidget(self.friend_handle_edit, 1)
        self.btn_add_friend = QPushButton("Add friend")
        self.btn_add_friend.clicked.connect(self._send_friend_request)
        self.friend_controls.addWidget(self.btn_add_friend)
        self.btn_refresh_friends = QPushButton("Refresh")
        self.btn_refresh_friends.setIcon(get_icon("ph.arrows-clockwise-bold", color=TEXT_SECONDARY))
        self.btn_refresh_friends.clicked.connect(self._refresh_social)
        self.friend_controls.addWidget(self.btn_refresh_friends)
        friends_layout.addLayout(self.friend_controls)
        self.btn_public_add_friend = QPushButton("Add friend")
        self.btn_public_add_friend.clicked.connect(self._send_friend_request)
        friends_layout.addWidget(self.btn_public_add_friend)
        self.incoming_title = QLabel("Incoming requests")
        self.incoming_title.setObjectName("profileMuted")
        friends_layout.addWidget(self.incoming_title)
        self.incoming_layout = QVBoxLayout()
        self.incoming_layout.setSpacing(2)
        friends_layout.addLayout(self.incoming_layout)
        self.outgoing_title = QLabel("Outgoing requests")
        self.outgoing_title.setObjectName("profileMuted")
        friends_layout.addWidget(self.outgoing_title)
        self.outgoing_layout = QVBoxLayout()
        self.outgoing_layout.setSpacing(2)
        friends_layout.addLayout(self.outgoing_layout)
        self.friends_list = QVBoxLayout()
        self.friends_list.setSpacing(2)
        friends_layout.addLayout(self.friends_list)
        self.blocked_title = QLabel("Blocked profiles")
        self.blocked_title.setObjectName("profileMuted")
        friends_layout.addWidget(self.blocked_title)
        self.blocked_layout = QVBoxLayout()
        self.blocked_layout.setSpacing(2)
        friends_layout.addLayout(self.blocked_layout)
        self.friends_status = QLabel()
        self.friends_status.setObjectName("profileMuted")
        self.friends_status.setWordWrap(True)
        friends_layout.addWidget(self.friends_status)
        self.column_layout.addWidget(self.friends_section)

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
        self.btn_sign_in = QPushButton("Sign in")
        self.btn_sign_in.setIcon(get_icon("ph.sign-in-bold", color="#FFFFFF"))
        self.btn_sign_in.clicked.connect(self._sign_in)
        bottom.addWidget(self.btn_sign_in)
        self.btn_sign_out = QPushButton("Sign out")
        self.btn_sign_out.clicked.connect(self._sign_out)
        bottom.addWidget(self.btn_sign_out)
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
        if self._profile_settings.get("public_handle") != self._social_handle:
            self._social_snapshot = self._empty_social_snapshot()
            self._social_handle = ""
        self.editor.setVisible(self._editing)
        self.btn_edit.setVisible(not self._editing)
        self._set_admin_controls(True)
        self._render(self._document)
        if self.isVisible():
            self._refresh_social()

    def mark_local_data_changed(self) -> None:
        """Refresh local stats and coalesce a public update after game events."""
        if self._editing:
            return
        self._profile_settings = load_profile_settings(self.settings, fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"))
        if self._mode == "owner" and self.isVisible():
            self._document = build_public_projection(self.db, self._profile_settings)
            self._render(self._document)
        if self._profile_settings.get("published") and self.central_auth.signed_in:
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
        self._social_snapshot = self._empty_social_snapshot()
        self._social_handle = ""
        self.mode_label.setText("PUBLIC VIEW")
        self.editor.setVisible(False)
        self._set_admin_controls(False)
        self._render(normalized)
        return True

    @staticmethod
    def _empty_social_snapshot() -> dict[str, Any]:
        return {
            "friends": [],
            "incoming_requests": [],
            "outgoing_requests": [],
            "blocked_handles": [],
        }

    def _local_owner_identity(self) -> tuple[str, str, str]:
        settings = load_profile_settings(
            self.settings,
            fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
        )
        return (
            str(settings.get("public_handle", "") or "").strip().lower(),
            "",
            get_profile_service_url(),
        )

    def _set_admin_controls(self, enabled: bool) -> None:
        signed_in = self.central_auth.signed_in
        published = bool(self._profile_settings.get("published"))
        self.btn_sign_in.setVisible(enabled)
        self.btn_sign_in.setEnabled(enabled and not signed_in and not self._auth_in_flight)
        self.btn_sign_out.setVisible(enabled and signed_in)
        self.btn_sign_out.setEnabled(not self._auth_in_flight)
        self.btn_edit.setVisible(enabled and not self._editing)
        self.btn_publish.setVisible(enabled)
        self.btn_publish.setEnabled(signed_in and not self._auth_in_flight)
        self.btn_publish.setToolTip(
            "Sign in to manage the public profile."
            if not signed_in else ""
        )
        self.btn_resync.setVisible(enabled)
        self.btn_copy_handle.setVisible(enabled and published)
        self.btn_open_public.setVisible(enabled)
        self.btn_settings.setVisible(True)
        self._update_social_controls(enabled, published, signed_in)

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
            signed_in = self.central_auth.signed_in
            self.public_badge.setText("PUBLIC PROFILE" if published else "LOCAL PROFILE")
            if published and signed_in:
                self.status_label.setText("Published and visible by handle.")
            elif published:
                self.status_label.setText("Published profile; sign in to manage it from this device.")
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
        self._render_social()

    def _update_social_controls(self, owner_enabled: bool, published: bool, authenticated: bool) -> None:
        """Keep social controls aligned with the owner/public view boundary."""
        owner_ready = owner_enabled and published and authenticated
        public_target = self._mode == "public" and bool(self._document.get("handle"))
        local_handle, _, local_service_url = self._local_owner_identity()
        local_settings = load_profile_settings(
            self.settings,
            fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
        )
        public_ready = (
            public_target
            and bool(local_settings.get("published"))
            and bool(local_handle)
            and authenticated
            and local_service_url.startswith(("http://", "https://"))
            and local_handle != str(self._document.get("handle", "") or "").lower()
        )
        self.friend_handle_edit.setVisible(owner_enabled)
        self.btn_add_friend.setVisible(owner_enabled)
        self.btn_refresh_friends.setVisible(owner_enabled)
        self.btn_public_add_friend.setVisible(public_target)
        self.incoming_title.setVisible(owner_enabled)
        self.incoming_layout.setEnabled(owner_enabled)
        self.outgoing_title.setVisible(owner_enabled)
        self.outgoing_layout.setEnabled(owner_enabled)
        self.friends_list.setEnabled(owner_enabled)
        self.blocked_title.setVisible(owner_enabled)
        self.blocked_layout.setEnabled(owner_enabled)
        self.friend_handle_edit.setEnabled(owner_ready and not self._social_mutating)
        self.btn_add_friend.setEnabled(owner_ready and not self._social_mutating)
        self.btn_refresh_friends.setEnabled(owner_ready and not self._social_loading and not self._social_mutating)
        self.btn_public_add_friend.setEnabled(public_ready and not self._social_mutating)

    @staticmethod
    def _clear_social_layout(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _social_row(self, summary: dict[str, Any], actions: list[tuple[str, Any]]) -> QFrame:
        row = QFrame()
        row.setObjectName("profileSocialRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(6)
        name = str(summary.get("display_name", "Player"))
        handle = str(summary.get("handle", ""))
        label = QLabel(f"{name}  ·  @{handle}")
        label.setObjectName("profileMuted")
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(label, 1)
        for caption, callback in actions:
            button = QPushButton(caption)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(callback)
            layout.addWidget(button)
        return row

    def _render_social(self) -> None:
        if self._mode == "public":
            self.friends_hint.setText("Friend lists are private. You can send a request to this profile if you have configured your own published profile.")
            self.friends_status.clear()
            self._clear_social_layout(self.incoming_layout)
            self._clear_social_layout(self.outgoing_layout)
            self._clear_social_layout(self.friends_list)
            self._clear_social_layout(self.blocked_layout)
            self.friends_section.setVisible(True)
            return

        handle, _, service_url = self._local_owner_identity()
        self.friends_section.setVisible(True)
        if not bool(self._profile_settings.get("published")):
            self.friends_hint.setText("Publish your profile to add friends and receive requests. Your friend list is private.")
        elif not self.central_auth.signed_in or not service_url.startswith(("http://", "https://")):
            self.friends_hint.setText("Sign in to use friends. Your friend list is private.")
        else:
            self.friends_hint.setText("Your friend list is private. Share your handle or paste someone else’s handle to send a request.")

        self._clear_social_layout(self.incoming_layout)
        self._clear_social_layout(self.outgoing_layout)
        self._clear_social_layout(self.friends_list)
        self._clear_social_layout(self.blocked_layout)
        snapshot = self._social_snapshot
        incoming = snapshot.get("incoming_requests", [])
        outgoing = snapshot.get("outgoing_requests", [])
        friends = snapshot.get("friends", [])
        blocked = snapshot.get("blocked_handles", [])
        for item in incoming if isinstance(incoming, list) else []:
            if not isinstance(item, dict):
                continue
            request_id = str(item.get("request_id", ""))
            friend_handle = str(item.get("handle", ""))
            self.incoming_layout.addWidget(self._social_row(item, [
                ("View", lambda checked=False, h=friend_handle: self.open_profile_handle_requested.emit(h)),
                ("Accept", lambda checked=False, r=request_id: self._respond_to_request(r, "accept")),
                ("Decline", lambda checked=False, r=request_id: self._respond_to_request(r, "decline")),
                ("Block", lambda checked=False, h=friend_handle: self._block_profile(h)),
            ]))
        for item in outgoing if isinstance(outgoing, list) else []:
            if not isinstance(item, dict):
                continue
            request_id = str(item.get("request_id", ""))
            friend_handle = str(item.get("handle", ""))
            self.outgoing_layout.addWidget(self._social_row(item, [
                ("View", lambda checked=False, h=friend_handle: self.open_profile_handle_requested.emit(h)),
                ("Cancel", lambda checked=False, r=request_id: self._respond_to_request(r, "cancel")),
            ]))
        for item in friends if isinstance(friends, list) else []:
            if not isinstance(item, dict):
                continue
            friend_handle = str(item.get("handle", ""))
            self.friends_list.addWidget(self._social_row(item, [
                ("View", lambda checked=False, h=friend_handle: self.open_profile_handle_requested.emit(h)),
                ("Remove", lambda checked=False, h=friend_handle: self._remove_friend(h)),
            ]))
        for blocked_handle in blocked if isinstance(blocked, list) else []:
            blocked_handle = str(blocked_handle)
            row = QFrame()
            row.setObjectName("profileSocialRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 2, 0, 2)
            row_layout.addWidget(QLabel(f"@{blocked_handle}"), 1)
            unblock = QPushButton("Unblock")
            unblock.clicked.connect(lambda checked=False, h=blocked_handle: self._unblock_profile(h))
            row_layout.addWidget(unblock)
            self.blocked_layout.addWidget(row)

        self.incoming_title.setText(f"Incoming requests ({len(incoming) if isinstance(incoming, list) else 0})")
        self.outgoing_title.setText(f"Outgoing requests ({len(outgoing) if isinstance(outgoing, list) else 0})")
        self.blocked_title.setText(f"Blocked profiles ({len(blocked) if isinstance(blocked, list) else 0})")
        count = len(friends) if isinstance(friends, list) else 0
        self.friends_status.setText(f"{count} friend{'s' if count != 1 else ''}")

    def _refresh_social(self) -> None:
        if self._mode != "owner" or self._social_loading or self._social_mutating:
            return
        handle, _, service_url = self._local_owner_identity()
        local_settings = load_profile_settings(
            self.settings,
            fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
        )
        if not bool(local_settings.get("published")) or not handle or not self.central_auth.signed_in:
            self._social_snapshot = self._empty_social_snapshot()
            self._social_handle = ""
            self._render_social()
            return
        if not service_url.startswith(("http://", "https://")):
            self._social_snapshot = self._empty_social_snapshot()
            self._social_handle = ""
            self._render_social()
            return
        self._social_loading = True
        self._social_handle = handle
        self._update_social_controls(True, True, True)
        self.friends_status.setText("Loading friends…")
        def _fetch_social():
            with ProfileServiceClient(service_url, auth_session=self.central_auth) as client:
                return client.get_social(handle)
        worker = self._tasks.start(
            "SafeLauncher-RefreshFriends",
            _fetch_social,
            lambda result, expected_handle=handle: self._social_refresh_done(result, expected_handle),
        )
        worker.error_occurred.connect(
            lambda error, expected_handle=handle: self._social_refresh_done(
                ProfileServiceError(error, "social_refresh_failed"), expected_handle
            )
        )

    def _social_refresh_done(self, result: Any, expected_handle: str = "") -> None:
        self._social_loading = False
        if self._mode != "owner":
            return
        handle, _, _ = self._local_owner_identity()
        if expected_handle and (handle != expected_handle or self._social_handle != expected_handle):
            return
        self._update_social_controls(True, bool(self._profile_settings.get("published")), self.central_auth.signed_in)
        if isinstance(result, Exception):
            self.friends_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.friends_status.setText(f"Friends could not be refreshed: {result}")
            return
        snapshot = result if isinstance(result, dict) else None
        if snapshot is None:
            self.friends_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.friends_status.setText("Friends could not be refreshed because the service response was invalid.")
            return
        self._social_snapshot = snapshot
        self._social_handle = handle
        self.friends_status.setStyleSheet("")
        self._render_social()

    def _start_social_mutation(self, operation, success_message: str) -> None:
        if self._social_mutating:
            return
        owner_handle, _, service_url = self._local_owner_identity()
        local_settings = load_profile_settings(
            self.settings,
            fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
        )
        if not bool(local_settings.get("published")) or not owner_handle or not self.central_auth.signed_in:
            self.friends_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.friends_status.setText("Sign in and publish your profile before managing friends.")
            return
        if not service_url.startswith(("http://", "https://")):
            self.friends_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.friends_status.setText("The central profile gateway is not configured for this build.")
            return
        self._social_mutating = True
        self._update_social_controls(self._mode == "owner", True, True)
        self.friends_status.setStyleSheet("")
        self.friends_status.setText("Updating friends…")
        def _run_mutation():
            with ProfileServiceClient(service_url, auth_session=self.central_auth) as client:
                return operation(client, owner_handle)
        worker = self._tasks.start(
            "SafeLauncher-FriendOperation",
            _run_mutation,
            lambda result: self._social_mutation_done(result, success_message),
        )
        worker.error_occurred.connect(
            lambda error: self._social_mutation_done(ProfileServiceError(error, "social_operation_failed"), success_message)
        )

    def _send_friend_request(self) -> None:
        if self._mode == "public":
            target_handle = str(self._document.get("handle", "") or "").strip().lower()
        else:
            target_handle = self.friend_handle_edit.text().strip().lstrip("@").lower()
        if not HANDLE_RE.fullmatch(target_handle):
            self.friends_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.friends_status.setText("Enter a valid SafeLauncher profile handle.")
            return
        owner_handle, _, _ = self._local_owner_identity()
        if target_handle == owner_handle:
            self.friends_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.friends_status.setText("You cannot send a friend request to yourself.")
            return
        self._start_social_mutation(
            lambda client, handle: client.send_friend_request(handle, target_handle),
            "Friend request sent.",
        )

    def _respond_to_request(self, request_id: str, action: str) -> None:
        if not request_id:
            return
        self._start_social_mutation(
            lambda client, handle: client.respond_friend_request(handle, request_id, action),
            {"accept": "Friend request accepted.", "decline": "Friend request declined.", "cancel": "Friend request canceled."}.get(action, "Friend request updated."),
        )

    def _remove_friend(self, friend_handle: str) -> None:
        if QMessageBox.question(
            self,
            "Remove friend",
            f"Remove @{friend_handle} from your friends?",
        ) != QMessageBox.StandardButton.Yes:
            return
        self._start_social_mutation(
            lambda client, handle: client.remove_friend(handle, friend_handle),
            "Friend removed.",
        )

    def _block_profile(self, friend_handle: str) -> None:
        if QMessageBox.question(
            self,
            "Block profile",
            f"Block @{friend_handle}? This also removes any friendship or pending request.",
        ) != QMessageBox.StandardButton.Yes:
            return
        self._start_social_mutation(
            lambda client, handle: client.block_user(handle, friend_handle),
            "Profile blocked.",
        )

    def _unblock_profile(self, blocked_handle: str) -> None:
        self._start_social_mutation(
            lambda client, handle: client.unblock_user(handle, blocked_handle),
            "Profile unblocked.",
        )

    def _social_mutation_done(self, result: Any, success_message: str) -> None:
        self._social_mutating = False
        if isinstance(result, Exception):
            self.friends_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.friends_status.setText(str(result))
            self._update_social_controls(self._mode == "owner", bool(self._profile_settings.get("published")), self.central_auth.signed_in)
            return
        self.friends_status.setStyleSheet(f"color:{SEMANTIC_SUCCESS};")
        self.friends_status.setText(success_message)
        if self._mode == "owner":
            self._refresh_social()
        else:
            self.btn_public_add_friend.setEnabled(False)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._mode == "owner":
            self._refresh_social()

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

    def _on_auth_progress(self, message: str) -> None:
        """Render device-login progress emitted by the worker thread."""
        self.footer_status.setStyleSheet("")
        self.footer_status.setText(str(message or "Waiting for central sign-in…"))

    def _central_profile_client(self) -> ProfileServiceClient:
        return ProfileServiceClient(get_profile_service_url(), auth_session=self.central_auth)

    def _reconcile_authenticated_profile(self) -> dict[str, Any] | None:
        """Find or migrate the profile belonging to the current Auth0 identity.

        The legacy token is read only for this one migration call. It is
        deleted only after Convex confirms the identity claim, so an
        interrupted migration cannot strand an existing profile.
        """
        with self._central_profile_client() as client:
            remote = client.current_profile()
            if remote is not None:
                return remote

            local = load_profile_settings(
                self.settings,
                fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
            )
            handle = str(local.get("public_handle", "") or "").strip().lower()
            legacy_token = str(get_secret("profile_owner_token") or "").strip()
            if not handle or not legacy_token:
                return None
            try:
                client.claim_legacy_profile(handle, legacy_token)
            except ProfileServiceError as exc:
                # An invalid/missing old token is recoverable: the user is
                # still signed in and may create a new profile. Other
                # failures should remain visible to the caller.
                if exc.code not in {"not_found", "unauthorized", "claimed", "identity_has_profile"}:
                    raise
                return None
            if not delete_secret("profile_owner_token"):
                # The server-side claim is complete. The old token cannot
                # authorize the claimed identity after its hash is removed.
                self.auth_progress.emit("Signed in; the old migration token could not be removed locally.")
            return client.current_profile()

    def _sign_in(self) -> None:
        if self._auth_in_flight or self._mode != "owner":
            return
        self._auth_in_flight = True
        self._set_admin_controls(True)
        self.footer_status.setStyleSheet("")
        self.footer_status.setText("Opening central sign-in…")

        def work():
            self.central_auth.device_login(
                progress=lambda message: self.auth_progress.emit(message),
            )
            return self._reconcile_authenticated_profile()

        worker = self._tasks.start("SafeLauncher-CentralSignIn", work, self._sign_in_done)
        worker.error_occurred.connect(self._sign_in_error)

    def _sign_in_error(self, error: str) -> None:
        self._sign_in_done(CentralAuthError(str(error), "sign_in_failed"))

    def _sign_in_done(self, result: Any) -> None:
        self._auth_in_flight = False
        if isinstance(result, Exception):
            self._set_admin_controls(self._mode == "owner")
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText(f"Central sign-in failed: {result}")
            return
        remote = result if isinstance(result, dict) else None
        if remote is not None:
            remote_settings = {
                **self._profile_settings,
                "display_name": remote.get("display_name", self._profile_settings.get("display_name", "Player")),
                "avatar": remote.get("avatar"),
                "background": remote.get("background"),
                "public_handle": remote.get("handle", self._profile_settings.get("public_handle", "")),
                "published": True,
            }
            self._profile_settings = save_profile_settings(self.settings, remote_settings, mark_changed=False)
            self._public_revision = int(remote.get("revision", 0) or 0)
            self.settings.setValue("profile_public_revision", self._public_revision)
            self.settings.sync()
            status_message = "Signed in and connected to your public profile."
        else:
            status_message = "Signed in. Publish this profile to make it public."
        self.show_owner()
        self.footer_status.setStyleSheet(f"color:{SEMANTIC_SUCCESS};")
        self.footer_status.setText(status_message)

    def _sign_out(self) -> None:
        if self._auth_in_flight:
            return
        self._publish_timer.stop()
        self.central_auth.clear()
        self._publish_dirty = False
        self.show_owner()
        self.footer_status.setStyleSheet("")
        self.footer_status.setText("Signed out. Your existing public profile remains visible.")

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

    def _publish(self) -> None:
        if self._mode != "owner":
            return
        if not self.central_auth.signed_in:
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText("Sign in to manage your public profile.")
            self._sign_in()
            return
        published = bool(self._profile_settings.get("published"))
        if published:
            self._unpublish()
            return
        service_url = get_profile_service_url()
        if not service_url.startswith(("http://", "https://")):
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText("The central profile gateway is not configured for this build.")
            return
        handle = self._profile_settings.get("public_handle") or generate_profile_handle()
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
        if self._publishing:
            return
        service_url = get_profile_service_url()
        profile_settings = load_profile_settings(
            self.settings,
            fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
        )
        handle = str(profile_settings.get("public_handle", "") or "")
        if not service_url.startswith(("http://", "https://")) or not handle or not self.central_auth.signed_in:
            if self._mode == "owner":
                self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
                self.footer_status.setText("Sign in before publishing the public profile.")
            return
        db_path = getattr(self.db, "db_path", None)
        self._publishing = True
        self._publish_dirty = False
        self.btn_publish.setEnabled(False)
        if self._mode == "owner":
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
            with ProfileServiceClient(service_url, auth_session=self.central_auth) as client:
                remote = client.current_profile()
                if remote is None:
                    legacy_token = str(get_secret("profile_owner_token") or "").strip()
                    if legacy_token:
                        try:
                            client.claim_legacy_profile(handle, legacy_token)
                        except ProfileServiceError as exc:
                            if exc.code not in {"not_found", "unauthorized", "claimed", "identity_has_profile"}:
                                raise
                        else:
                            # Delete only after the atomic server-side claim.
                            delete_secret("profile_owner_token")
                        remote = client.current_profile()

                if remote is None:
                    try:
                        response = client.create_profile(document)
                    except ProfileServiceError as exc:
                        if exc.code not in {"exists", "profile_exists_for_identity"}:
                            raise
                        remote = client.current_profile()
                        if remote is None:
                            raise
                        document["handle"] = remote["handle"]
                        response = client.update_profile(document, int(remote.get("revision", 0) or 0))
                else:
                    document["handle"] = remote["handle"]
                    revision = int(remote.get("revision", 0) or 0)
                    try:
                        response = client.update_profile(document, revision)
                    except ProfileServiceError as exc:
                        if exc.code != "conflict":
                            raise
                        fresh = client.current_profile()
                        if fresh is None:
                            raise
                        document["handle"] = fresh["handle"]
                        response = client.update_profile(document, int(fresh.get("revision", 0) or 0))
                return {"response": response, "document": document}

        worker = self._tasks.start("SafeLauncher-PublishProfile", work, self._publish_done)
        worker.error_occurred.connect(self._publish_error)

    def _publish_error(self, error: str) -> None:
        self._publish_done(ProfileServiceError(str(error), "publish_failed"))

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
        local_settings = load_profile_settings(
            self.settings,
            fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
        )
        published_handle = str(document.get("handle", "") or local_settings.get("public_handle", "")) if document else local_settings.get("public_handle", "")
        self._profile_settings = save_profile_settings(self.settings, {**local_settings, "public_handle": published_handle, "published": True}, mark_changed=False)
        # A background sync may finish while the user is viewing someone
        # else's public page. Never replace that page with the owner's local
        # projection as a side effect of a statistics update.
        if self._mode == "owner":
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
        if not service_url.startswith(("http://", "https://")) or not self.central_auth.signed_in:
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText("Sign in to manage your public profile.")
            return
        self.btn_publish.setEnabled(False)
        self.footer_status.setText("Unpublishing public profile…")
        def work():
            with ProfileServiceClient(service_url, auth_session=self.central_auth) as client:
                if client.current_profile() is None:
                    legacy_token = str(get_secret("profile_owner_token") or "").strip()
                    handle = str(self._profile_settings.get("public_handle", "") or "")
                    if legacy_token and handle:
                        try:
                            client.claim_legacy_profile(handle, legacy_token)
                        except ProfileServiceError as exc:
                            if exc.code not in {"not_found", "unauthorized", "claimed", "identity_has_profile"}:
                                raise
                        else:
                            delete_secret("profile_owner_token")
                return client.delete_profile()
        worker = self._tasks.start("SafeLauncher-UnpublishProfile", work, self._unpublish_done)
        worker.error_occurred.connect(self._unpublish_error)

    def _unpublish_error(self, error: str) -> None:
        self._unpublish_done(ProfileServiceError(str(error), "unpublish_failed"))

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
        self.footer_status.setText("Profile unpublished. Your central sign-in remains available for publishing again.")
        self.private_profile_changed.emit()

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
        """Keep a localhost-only override for development and UI tests."""
        value = str(url or "").strip().rstrip("/")
        if is_local_service_url(value):
            self.settings.setValue("profile_service_url", value)
        else:
            self.settings.remove("profile_service_url")
        self.settings.sync()

    def closeEvent(self, event) -> None:
        self._tasks.cancel_all(250)
        super().closeEvent(event)
