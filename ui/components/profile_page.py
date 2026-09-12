"""Full-page owner and public profile presentation."""

from __future__ import annotations

import hashlib
import math
import os
from typing import Any

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal, QSettings, QSignalBlocker, QStandardPaths, QTimer
from PyQt6.QtGui import QColor, QPainter, QPixmap, QLinearGradient
from PyQt6.QtWidgets import (
    QColorDialog, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QPlainTextEdit,
    QProgressBar, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
    QInputDialog,
)

from core.profile_models import (
    BACKGROUND_PRESETS, MAX_BIO_LENGTH, build_public_projection,
    HANDLE_RE, load_profile_settings, normalize_avatar_asset_id, normalize_avatar_id,
    normalize_background, normalize_panel_theme_id, normalize_username_handle, panel_theme_key,
    normalize_public_document, profile_username_suggestion, save_profile_settings,
    steam_app_id, steam_hero_url, steam_hero_urls,
)
from core.profile_service import ProfileServiceClient, ProfileServiceError, get_profile_service_url, is_local_service_url
from core.profile_avatar_catalog import (
    load_cached_avatar_catalog,
    read_cached_avatar,
    save_cached_avatar,
    save_cached_avatar_catalog,
)
from core.central_auth import CentralAuthError, CentralAuthSession
from core.secret_store import delete_secret, get_secret
from core.safe_thread import TaskSupervisor
from core.network_policy import automatic_network_allowed
from ui.icons import get_icon
from ui.dialogs.profile_avatar_dialog import ProfileAvatarCatalogDialog
from ui.theme import (
    ACCENT_PRIMARY, BORDER, SEMANTIC_ERROR, SEMANTIC_SUCCESS,
    SURFACE, SURFACE_ELEVATED, TEXT_MUTED, TEXT_PRIMARY, TEXT_SECONDARY,
)
from ui.profile_theme import get_profile_theme, normalize_profile_theme, profile_theme_choices, theme_rgba

PROFILE_CARD_HEIGHT = 196
PROFILE_ARTWORK_HEIGHT = 104
MAX_PROFILE_BACKGROUND_BYTES = 4 * 1024 * 1024
MAX_PROFILE_BACKGROUND_PIXELS = 32_000_000
MAX_PROFILE_BACKGROUND_CACHE_ITEMS = 3
MAX_PROFILE_AVATAR_CACHE_ITEMS = 16
MAX_PROFILE_AVATAR_BYTES = 512 * 1024


def _download_profile_image(
    url: str,
    timeout: tuple[int, int],
    *,
    max_bytes: int = MAX_PROFILE_BACKGROUND_BYTES,
) -> bytes:
    """Download one fixed artwork URL with a bounded response body."""
    import requests

    response = None
    try:
        response = requests.get(
            url,
            headers={"Accept": "image/jpeg,image/*;q=0.8", "User-Agent": "SafeLauncher/1"},
            timeout=timeout,
            stream=True,
        )
        content_type = response.headers.get("Content-Type", "")
        if response.status_code != 200 or not content_type.startswith("image/"):
            return b""
        content_length = response.headers.get("Content-Length")
        try:
            if content_length and int(content_length) > max_bytes:
                return b""
        except (TypeError, ValueError, OverflowError):
            return b""
        chunks = []
        size = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            size += len(chunk)
            if size > max_bytes:
                return b""
            chunks.append(chunk)
        return b"".join(chunks) if size else b""
    except requests.RequestException:
        return b""
    finally:
        if response is not None:
            response.close()


class ProfileGameCard(QFrame):
    """Compact, clickable public-library card with playtime and achievements."""

    clicked = pyqtSignal(dict)

    def __init__(self, game: dict[str, Any], parent=None):
        super().__init__(parent)
        self.game = dict(game)
        self._pixmap = QPixmap()
        self.setObjectName("profileGameCard")
        self.setMinimumWidth(180)
        self.setFixedHeight(PROFILE_CARD_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Open game profile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 9)
        layout.setSpacing(6)

        self.artwork = QLabel("Steam hero")
        self.artwork.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.artwork.setFixedHeight(PROFILE_ARTWORK_HEIGHT)
        self.artwork.setStyleSheet(f"background:{SURFACE}; color:{TEXT_MUTED}; border:none; font-size:11px;")
        self.artwork.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.artwork)

        self.name = QLabel(str(self.game.get("name", "Game")))
        self.name.setTextFormat(Qt.TextFormat.PlainText)
        self.name.setWordWrap(True)
        self.name.setFixedHeight(34)
        self.name.setStyleSheet(f"color:{TEXT_PRIMARY}; font-size:12px; font-weight:700; border:none;")
        self.name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.name)

        achievement = self.game.get("achievements") if isinstance(self.game.get("achievements"), dict) else {}
        unlocked = max(0, int(achievement.get("unlocked_count", 0) or 0))
        total = max(unlocked, int(achievement.get("total_count", 0) or 0))
        playtime = max(0, int(self.game.get("playtime_seconds", 0) or 0))
        meta = QHBoxLayout()
        meta.setSpacing(6)
        self.playtime = QLabel(self._format_hours(playtime))
        self.playtime.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:10px; border:none;")
        self.playtime.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        meta.addWidget(self.playtime)
        self.achievement_count = QLabel(f"{unlocked}/{total} achievements" if total else "No achievements")
        self.achievement_count.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:10px; border:none;")
        self.achievement_count.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        meta.addWidget(self.achievement_count, 1)
        layout.addLayout(meta)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(round(float(achievement.get("percentage", 0) or 0) * 10))
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.setStyleSheet(
            f"QProgressBar {{ background:{SURFACE}; border:none; border-radius:2px; }}"
            f"QProgressBar::chunk {{ background:{ACCENT_PRIMARY}; border-radius:2px; }}"
        )
        self.progress.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.progress)
        for child in (self.artwork, self.name, self.playtime, self.achievement_count, self.progress):
            child.installEventFilter(self)

    @staticmethod
    def _format_hours(seconds: int) -> str:
        return f"{seconds / 3600:.1f} h"

    def set_artwork_bytes(self, data: bytes) -> None:
        pixmap = QPixmap()
        if data:
            pixmap.loadFromData(data)
        if not pixmap.isNull():
            self.set_artwork_pixmap(pixmap)

    def set_artwork_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.artwork.setText("")
        self._refresh_artwork()

    def _refresh_artwork(self) -> None:
        if self._pixmap.isNull():
            return
        self.artwork.setPixmap(self._pixmap.scaled(
            max(1, self.artwork.width()), max(1, self.artwork.height()),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        ))

    # Compatibility aliases for callers from older profile-page integrations.
    set_banner_bytes = set_artwork_bytes
    set_banner_pixmap = set_artwork_pixmap

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_artwork()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(dict(self.game))
            return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(dict(self.game))
            event.accept()
            return
        super().mousePressEvent(event)


class ProfileSeeMoreCard(QFrame):
    """Sixth-slot navigation card for profiles with a larger library."""

    clicked = pyqtSignal()

    def __init__(self, count: int, parent=None):
        super().__init__(parent)
        self.setObjectName("profileSeeMoreCard")
        self.setMinimumWidth(180)
        self.setFixedHeight(PROFILE_CARD_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("View the complete games library")
        self.setAccessibleName("View all profile games")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(7)
        layout.addStretch()
        icon = QLabel("+")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"color:{ACCENT_PRIMARY}; font-size:30px; font-weight:700; border:none;")
        icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(icon)
        title = QLabel("See more")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color:{TEXT_PRIMARY}; font-size:13px; font-weight:700; border:none;")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(title)
        detail = QLabel(f"View all {count} games")
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail.setStyleSheet(f"color:{TEXT_SECONDARY}; font-size:10px; border:none;")
        detail.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(detail)
        layout.addStretch()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class ProfilePageWidget(QWidget):
    """Persistent profile page with an explicit owner/public mode boundary."""

    back_requested = pyqtSignal()
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
        self._profile_theme_key = normalize_profile_theme(self.settings.value("profile_theme", "grey", type=str))
        self._editing = False
        self._draft_avatar_id = ""
        self._draft_background: dict[str, Any] | None = None
        self._draft_panel_theme_id = 1
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
        self._game_cards: dict[str, list[ProfileGameCard]] = {}
        self._artwork_cache: dict[str, bytes] = {}
        self._artwork_inflight: set[str] = set()
        self._profile_background_cache: dict[str, bytes] = {}
        self._profile_background_inflight: set[str] = set()
        self._profile_background_pixmap = QPixmap()
        self._profile_background_blurred = QPixmap()
        self._profile_background_blur_size = (0, 0)
        self._avatar_catalog: list[dict[str, Any]] = load_cached_avatar_catalog() or []
        self._avatar_catalog_loading = False
        self._avatar_dialog: ProfileAvatarCatalogDialog | None = None
        self._avatar_pixmaps: dict[str, QPixmap] = {}
        self._avatar_inflight: set[str] = set()
        self._selected_profile_game: dict[str, Any] | None = None
        self._games_return_index = 0
        self._profile_games: list[dict[str, Any]] = []
        self._all_games_populated = False
        self._handle_check_serial = 0
        self._handle_check_inflight = False
        self._handle_availability: bool | None = None
        self._handle_check_timer = QTimer(self)
        self._handle_check_timer.setSingleShot(True)
        self._handle_check_timer.setInterval(350)
        self._handle_check_timer.timeout.connect(self._check_handle_availability)
        self._local_refresh_pending = False
        self._local_refresh_timer = QTimer(self)
        self._local_refresh_timer.setSingleShot(True)
        self._local_refresh_timer.setInterval(100)
        self._local_refresh_timer.timeout.connect(self._refresh_local_projection)
        self._publish_timer = QTimer(self)
        self._publish_timer.setSingleShot(True)
        self._publish_timer.setInterval(1500)
        self._publish_timer.timeout.connect(self._publish_current_document)
        self.auth_progress.connect(self._on_auth_progress)
        self._build_ui()
        self.show_owner()

    def _profile_theme_style(self) -> str:
        """Build one surface vocabulary for the entire profile page.

        Normal themes use opaque dark-tinted surfaces. The dedicated
        Glassmorphism theme uses the same translucent dark gradient and white
        highlight borders as the compact game page.
        """
        theme = get_profile_theme(self._profile_theme_key)
        def shifted_rgb(delta: int) -> str:
            return "#%02X%02X%02X" % tuple(
                max(0, min(255, channel + delta)) for channel in theme.panel_rgb
            )

        if theme.is_glass:
            # Match CompactActionBar's intentionally translucent dark glass
            # recipe. The page background remains underneath these surfaces.
            panel = (
                "qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                "stop:0 rgba(28, 28, 34, 0.70), "
                "stop:0.04 rgba(22, 22, 26, 0.60), "
                "stop:0.65 rgba(18, 18, 22, 0.72), "
                "stop:1 rgba(14, 14, 18, 0.85))"
            )
            hero = panel
            panel_soft = "rgba(18, 18, 22, 0.72)"
            game_card = "rgba(20, 23, 29, 0.72)"
            see_more = "rgba(20, 20, 24, 0.72)"
            list_surface = "rgba(0, 0, 0, 0.23)"
            editor_surface = "rgba(0, 0, 0, 0.45)"
            progress_surface = "rgba(0, 0, 0, 0.35)"
            border = "rgba(255, 255, 255, 0.14)"
            card_border = "rgba(255, 255, 255, 0.08)"
            hover_surface = "rgba(255, 255, 255, 0.08)"
        else:
            panel = shifted_rgb(0)
            panel_soft = shifted_rgb(-5)
            hero = shifted_rgb(8)
            game_card = shifted_rgb(-10)
            see_more = shifted_rgb(-5)
            list_surface = "#121418"
            editor_surface = "#16191F"
            progress_surface = "#0F1116"
            border = theme_rgba(theme.bubbles[0], theme.border_alpha)
            card_border = theme_rgba(theme.bubbles[0], max(22, theme.border_alpha - 18))
            hover_surface = shifted_rgb(10)
        return f"""
            QFrame#profileHero {{ background: {hero}; border: 1px solid {border}; border-radius: 20px; }}
            QFrame#profileActionStrip {{ background: {panel}; border: 1px solid {border}; border-top: none; border-radius: 0 0 16px 16px; }}
            QFrame#profileSection {{ background: {panel_soft}; border: 1px solid {border}; border-radius: 16px; }}
            QFrame#profileGameCard {{ background: {game_card}; border: 1px solid {card_border}; border-radius: 12px; }}
            QFrame#profileGameCard:hover {{ background: {hover_surface}; border-color: {theme.accent}; }}
            QFrame#profileSeeMoreCard {{ background: {see_more}; border: 1px dashed {border}; border-radius: 12px; }}
            QFrame#profileSeeMoreCard:hover {{ background: {hover_surface}; border-color: {theme.accent}; }}
            QListWidget#profileList {{ background: {list_surface}; border: none; }}
            QLineEdit#profileEditorInput, QComboBox#profileEditorInput, QPlainTextEdit#profileEditorInput {{ background: {editor_surface}; border-color: {border}; }}
            QPushButton#profileActionButton:hover {{ background: {hover_surface}; }}
            QProgressBar {{ background: {progress_surface}; }}
            QProgressBar::chunk {{ background: {theme.accent}; }}
        """

    def set_profile_theme(self, value: object) -> None:
        """Apply a live Settings preview without touching profile data."""
        key = normalize_profile_theme(value)
        if key == self._profile_theme_key and hasattr(self, "_base_style"):
            return
        self._profile_theme_key = key
        if hasattr(self, "_base_style"):
            self._apply_profile_styles(self._document.get("background", {}))
            self.update()

    def _apply_profile_styles(self, background: Any = None) -> None:
        """Apply root styles and themed panel styles at their actual owner.

        Qt style-sheet rules attached to this custom QWidget do not reliably
        reach frames nested inside the scroll area's content widget. The
        column owns all profile panels, so applying the shared surface sheet
        there makes the opaque/glass distinction effective in real rendering.
        """
        self.setStyleSheet(self._base_style + self._background_style(background or {}))
        if hasattr(self, "column"):
            self.column.setStyleSheet(self._base_style + self._profile_theme_style())

    def commit_profile_theme(self, value: object) -> None:
        """Persist the owner theme and queue a public update when published."""
        was_owner_view = self._mode == "owner"
        theme_id = normalize_panel_theme_id(value)
        key = panel_theme_key(theme_id)
        current = load_profile_settings(
            self.settings,
            fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
        )
        self._profile_settings = save_profile_settings(
            self.settings,
            {**current, "panel_theme_id": theme_id, "profile_theme": key},
        )
        if was_owner_view:
            self._profile_theme_key = key
            self._document = build_public_projection(self.db, self._profile_settings)
            self._render(self._document)
        self.mark_local_data_changed()

    def profile_theme(self) -> str:
        return self._profile_theme_key

    def _build_ui(self) -> None:
        self.setObjectName("profilePage")
        self._base_style = f"""
            QWidget#profilePage {{ background: transparent; color: {TEXT_PRIMARY}; }}
            QScrollArea#profileScroll {{ background: transparent; border: none; }}
            QScrollArea#profileScroll > QWidget > QWidget {{ background: transparent; }}
            QWidget#profileCanvas, QWidget#profileColumn, QWidget#profileGamesLibrary, QWidget#profileGamesAllPage {{ background: transparent; }}
            QFrame#profileSection {{ background: {SURFACE}; border: none; }}
            QFrame#profileActionStrip {{ background: {SURFACE}; border: none; }}
            QPushButton#profileActionButton {{ border: none; border-radius: 6px; padding: 7px 11px; }}
            QPushButton#profileActionButton:hover {{ background: {SURFACE_ELEVATED}; }}
            QPushButton#profileActionButton:disabled {{ color: {TEXT_MUTED}; }}
            QPushButton#profileBannerEdit {{ background: transparent; border: none; border-radius: 6px; }}
            QPushButton#profileBannerEdit:hover {{ background: rgba(255, 255, 255, 0.10); }}
            QFrame#profileGameCard {{ background: {SURFACE_ELEVATED}; border: none; border-radius: 8px; }}
            QFrame#profileGameCard:hover {{ background: #252A34; }}
            QFrame#profileSeeMoreCard {{ background: {SURFACE}; border: 1px dashed {BORDER}; border-radius: 8px; }}
            QFrame#profileSeeMoreCard:hover {{ background: {SURFACE_ELEVATED}; border-color: {ACCENT_PRIMARY}; }}
            QFrame#profileHero {{ border: none; }}
            QLabel#profileEyebrow {{ color: {TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
            QLabel#profileName {{ color: {TEXT_PRIMARY}; font-size: 28px; font-weight: 700; }}
            QLabel#profileHandle {{ color: {TEXT_SECONDARY}; font-size: 12px; }}
            QLabel#profileBio {{ color: {TEXT_PRIMARY}; font-size: 13px; }}
            QLabel#profileMuted {{ color: {TEXT_SECONDARY}; font-size: 12px; }}
            QLabel#profileStatValue {{ color: {TEXT_PRIMARY}; font-size: 20px; font-weight: 700; }}
            QLabel#profileStatCaption {{ color: {TEXT_MUTED}; font-size: 11px; }}
            QListWidget#profileList {{ background: {SURFACE}; color: {TEXT_PRIMARY}; border: none; outline: none; }}
            QListWidget#profileList::item {{ padding: 8px 4px; border: none; }}
            QFrame#profileSocialRow {{ background: transparent; border: none; }}
            QLineEdit#profileEditorInput, QComboBox#profileEditorInput, QPlainTextEdit#profileEditorInput {{
                background: {SURFACE_ELEVATED}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER};
                border-radius: 6px; padding: 7px 9px; font-size: 12px;
            }}
            QLineEdit#profileEditorInput:focus, QComboBox#profileEditorInput:focus, QPlainTextEdit#profileEditorInput:focus {{ border-color: {ACCENT_PRIMARY}; }}
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
        root.addLayout(toolbar)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("profileScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.viewport().setStyleSheet("background: transparent;")
        content = QWidget()
        content.setObjectName("profileCanvas")
        content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        content.setAutoFillBackground(False)
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(22, 14, 22, 26)
        content_layout.addStretch(1)
        self.column = QWidget()
        self.column.setObjectName("profileColumn")
        self.column.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.column.setAutoFillBackground(False)
        self.column.setMaximumWidth(920)
        self.column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.column_layout = QVBoxLayout(self.column)
        self.column_layout.setContentsMargins(0, 0, 0, 0)
        # The hero, action strip, and stats form one continuous profile header.
        # Section spacing is added explicitly below so that only this boundary
        # is closed up; the rest of the page keeps its visual separation.
        self.column_layout.setSpacing(0)
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
        self.handle_label.setTextFormat(Qt.TextFormat.PlainText)
        identity.addWidget(self.handle_label)
        self.bio_label = QLabel()
        self.bio_label.setObjectName("profileBio")
        self.bio_label.setTextFormat(Qt.TextFormat.PlainText)
        self.bio_label.setWordWrap(True)
        self.bio_label.setVisible(False)
        identity.addWidget(self.bio_label)
        self.status_label = QLabel()
        self.status_label.setObjectName("profileMuted")
        self.status_label.setWordWrap(True)
        identity.addWidget(self.status_label)
        identity.addStretch()
        hero_layout.addLayout(identity, 1)
        self.btn_banner_edit = QPushButton()
        self.btn_banner_edit.setObjectName("profileBannerEdit")
        self.btn_banner_edit.setAccessibleName("Edit profile")
        self.btn_banner_edit.setToolTip("Edit profile")
        self.btn_banner_edit.setIcon(get_icon("ph.gear-six-bold", color=TEXT_SECONDARY))
        self.btn_banner_edit.setIconSize(QSize(18, 18))
        self.btn_banner_edit.setFixedSize(36, 36)
        self.btn_banner_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_banner_edit.clicked.connect(self._start_edit)
        hero_layout.addWidget(self.btn_banner_edit, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        # Compatibility alias for integrations that used the old bottom edit
        # button. The actual control now lives in the banner.
        self.btn_edit = self.btn_banner_edit
        self.column_layout.addWidget(self.hero)

        self.profile_action_strip = QFrame()
        self.profile_action_strip.setObjectName("profileActionStrip")
        action_layout = QHBoxLayout(self.profile_action_strip)
        action_layout.setContentsMargins(18, 9, 18, 9)
        action_layout.setSpacing(6)
        action_layout.addStretch(1)

        self.btn_auth = QPushButton("Sign in")
        self.btn_auth.setObjectName("profileActionButton")
        self.btn_auth.setAccessibleName("Profile sign in or sign out")
        self.btn_auth.setIcon(get_icon("ph.sign-in-bold", color="#FFFFFF"))
        self.btn_auth.setIconSize(QSize(16, 16))
        self.btn_auth.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_auth.clicked.connect(self._auth_button_clicked)
        action_layout.addWidget(self.btn_auth)
        # Compatibility aliases; there is intentionally only one auth widget.
        self.btn_sign_in = self.btn_auth
        self.btn_sign_out = self.btn_auth

        self.btn_publish = QPushButton("Publish profile")
        self.btn_publish.setObjectName("profileActionButton")
        self.btn_publish.setIcon(get_icon("ph.upload-simple-bold", color="#FFFFFF"))
        self.btn_publish.setIconSize(QSize(16, 16))
        self.btn_publish.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_publish.clicked.connect(self._publish)
        action_layout.addWidget(self.btn_publish)

        self.btn_resync = QPushButton("Resync")
        self.btn_resync.setObjectName("profileActionButton")
        self.btn_resync.setAccessibleName("Resync private profile data")
        self.btn_resync.setToolTip("Resync private profile data")
        self.btn_resync.setIcon(get_icon("ph.arrows-clockwise-bold", color=TEXT_SECONDARY))
        self.btn_resync.setIconSize(QSize(16, 16))
        self.btn_resync.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_resync.clicked.connect(self._resync_private)
        action_layout.addWidget(self.btn_resync)
        action_layout.addStretch(1)
        self.column_layout.addWidget(self.profile_action_strip)

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
        handle_row = QHBoxLayout()
        handle_row.addWidget(QLabel("Username"))
        self.handle_edit = QLineEdit()
        self.handle_edit.setObjectName("profileEditorInput")
        self.handle_edit.setMaxLength(40)
        self.handle_edit.setPlaceholderText("username")
        self.handle_edit.textChanged.connect(self._schedule_handle_availability)
        handle_row.addWidget(self.handle_edit, 1)
        editor_layout.addLayout(handle_row)
        self.handle_hint = QLabel("Your @username is used for your public profile and friend requests.")
        self.handle_hint.setObjectName("profileMuted")
        self.handle_hint.setWordWrap(True)
        editor_layout.addWidget(self.handle_hint)
        bio_row = QHBoxLayout()
        bio_row.addWidget(QLabel("Bio"), 0, Qt.AlignmentFlag.AlignTop)
        bio_column = QVBoxLayout()
        self.bio_edit = QPlainTextEdit()
        self.bio_edit.setObjectName("profileEditorInput")
        self.bio_edit.setPlaceholderText("Tell people a little about yourself")
        self.bio_edit.setFixedHeight(68)
        self.bio_edit.setTabChangesFocus(True)
        self.bio_edit.textChanged.connect(self._limit_bio)
        bio_column.addWidget(self.bio_edit)
        self.bio_count = QLabel(f"0/{MAX_BIO_LENGTH}")
        self.bio_count.setObjectName("profileMuted")
        self.bio_count.setAlignment(Qt.AlignmentFlag.AlignRight)
        bio_column.addWidget(self.bio_count)
        bio_row.addLayout(bio_column, 1)
        editor_layout.addLayout(bio_row)
        avatar_row = QHBoxLayout()
        avatar_row.addWidget(QLabel("Avatar"))
        self.btn_avatar = QPushButton("Choose profile picture")
        self.btn_avatar.clicked.connect(self._choose_avatar)
        avatar_row.addWidget(self.btn_avatar)
        self.btn_remove_avatar = QPushButton("Use initials")
        self.btn_remove_avatar.clicked.connect(self._remove_avatar)
        avatar_row.addWidget(self.btn_remove_avatar)
        self.avatar_selection_label = QLabel("Cloud catalog")
        self.avatar_selection_label.setObjectName("profileMuted")
        avatar_row.addWidget(self.avatar_selection_label)
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
        self.background_combo.addItem("Steam hero by AppID", "steam_hero")
        self.background_combo.currentIndexChanged.connect(self._on_background_mode_changed)
        background_row.addWidget(self.background_combo, 1)
        self.btn_custom_color = QPushButton("Color")
        self.btn_custom_color.clicked.connect(self._choose_color)
        background_row.addWidget(self.btn_custom_color)
        editor_layout.addLayout(background_row)

        panel_theme_row = QHBoxLayout()
        panel_theme_row.addWidget(QLabel("Panel visuals"))
        self.panel_theme_combo = QComboBox()
        self.panel_theme_combo.setObjectName("profileEditorInput")
        for label, key in profile_theme_choices():
            self.panel_theme_combo.addItem(label, key)
        self.panel_theme_combo.currentIndexChanged.connect(self._on_panel_theme_changed)
        panel_theme_row.addWidget(self.panel_theme_combo, 1)
        panel_theme_hint = QLabel("Shared style for stats, library, social, and profile cards.")
        panel_theme_hint.setObjectName("profileMuted")
        panel_theme_hint.setWordWrap(True)
        panel_theme_row.addWidget(panel_theme_hint, 2)
        editor_layout.addLayout(panel_theme_row)

        self.background_hero_controls = QWidget()
        hero_background_row = QHBoxLayout(self.background_hero_controls)
        hero_background_row.setContentsMargins(0, 0, 0, 0)
        hero_background_row.addWidget(QLabel("Hero AppID"))
        self.background_app_id_edit = QLineEdit()
        self.background_app_id_edit.setObjectName("profileEditorInput")
        self.background_app_id_edit.setMaxLength(16)
        self.background_app_id_edit.setPlaceholderText("e.g. 1321440")
        self.background_app_id_edit.setToolTip("Enter a Steam AppID to use its hero artwork as your profile background.")
        self.background_app_id_edit.returnPressed.connect(self._use_steam_hero_background)
        self.background_app_id_edit.editingFinished.connect(self._preview_steam_hero_from_app_id)
        hero_background_row.addWidget(self.background_app_id_edit, 1)
        self.btn_use_hero_background = QPushButton("Use hero")
        self.btn_use_hero_background.clicked.connect(self._use_steam_hero_background)
        hero_background_row.addWidget(self.btn_use_hero_background)
        editor_layout.addWidget(self.background_hero_controls)
        self.background_hero_controls.setVisible(False)
        self.editor_hint = QLabel("Only the information shown on your public profile is shared. Private launcher data stays private.")
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
            box.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value = QLabel("0")
            value.setObjectName("profileStatValue")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label = QLabel(caption)
            label.setObjectName("profileStatCaption")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(value)
            box.addWidget(label)
            self.stats_grid.addLayout(box, 0, index)
            self.stats_grid.setColumnStretch(index, 1)
            self.stat_labels[key] = value
        self.column_layout.addWidget(self.stats_section)
        self.column_layout.addSpacing(14)

        self.games_section = self._section("Games library")
        games_layout = self.games_section.layout()
        games_layout.setContentsMargins(18, 14, 18, 14)
        self.games_stack = QStackedWidget()

        self.games_library = QWidget()
        self.games_library.setObjectName("profileGamesLibrary")
        self.games_grid = QGridLayout(self.games_library)
        self.games_grid.setContentsMargins(0, 0, 0, 0)
        self.games_grid.setHorizontalSpacing(10)
        self.games_grid.setVerticalSpacing(10)
        self.games_stack.addWidget(self.games_library)

        self.games_all_page = QWidget()
        self.games_all_page.setObjectName("profileGamesAllPage")
        all_layout = QVBoxLayout(self.games_all_page)
        all_layout.setContentsMargins(0, 0, 0, 0)
        all_toolbar = QHBoxLayout()
        self.btn_games_all_back = QPushButton("Preview")
        self.btn_games_all_back.setIcon(get_icon("ph.arrow-left-bold", color=TEXT_SECONDARY))
        self.btn_games_all_back.clicked.connect(lambda: self.games_stack.setCurrentIndex(0))
        all_toolbar.addWidget(self.btn_games_all_back)
        self.games_all_title = QLabel("All games")
        self.games_all_title.setStyleSheet(f"color:{TEXT_PRIMARY}; font-size:14px; font-weight:700;")
        all_toolbar.addWidget(self.games_all_title)
        all_toolbar.addStretch()
        all_layout.addLayout(all_toolbar)
        self.games_all_grid = QGridLayout()
        self.games_all_grid.setContentsMargins(0, 0, 0, 0)
        self.games_all_grid.setHorizontalSpacing(10)
        self.games_all_grid.setVerticalSpacing(10)
        all_layout.addLayout(self.games_all_grid)
        self.games_stack.addWidget(self.games_all_page)

        self.game_detail = QWidget()
        detail_layout = QVBoxLayout(self.game_detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(10)
        detail_toolbar = QHBoxLayout()
        self.btn_games_back = QPushButton("Back to games")
        self.btn_games_back.setIcon(get_icon("ph.arrow-left-bold", color=TEXT_SECONDARY))
        self.btn_games_back.clicked.connect(self._return_to_games)
        detail_toolbar.addWidget(self.btn_games_back)
        detail_toolbar.addStretch()
        self.game_detail_app_id = QLabel()
        self.game_detail_app_id.setObjectName("profileMuted")
        detail_toolbar.addWidget(self.game_detail_app_id)
        detail_layout.addLayout(detail_toolbar)

        detail_body = QHBoxLayout()
        detail_body.setSpacing(16)
        self.game_detail_artwork = QLabel("Steam hero")
        self.game_detail_artwork.setFixedSize(270, 155)
        self.game_detail_artwork.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.game_detail_artwork.setStyleSheet(f"background:{SURFACE}; color:{TEXT_MUTED}; border:none;")
        detail_body.addWidget(self.game_detail_artwork, 0, Qt.AlignmentFlag.AlignTop)
        detail_info = QVBoxLayout()
        detail_info.setSpacing(6)
        self.game_detail_name = QLabel()
        self.game_detail_name.setObjectName("profileName")
        self.game_detail_name.setTextFormat(Qt.TextFormat.PlainText)
        self.game_detail_name.setStyleSheet(f"color:{TEXT_PRIMARY}; font-size:20px; font-weight:700;")
        self.game_detail_name.setWordWrap(True)
        detail_info.addWidget(self.game_detail_name)
        self.game_detail_playtime = QLabel()
        self.game_detail_playtime.setObjectName("profileMuted")
        detail_info.addWidget(self.game_detail_playtime)
        self.game_detail_achievements = QLabel()
        self.game_detail_achievements.setObjectName("profileMuted")
        detail_info.addWidget(self.game_detail_achievements)
        self.game_detail_progress = QProgressBar()
        self.game_detail_progress.setRange(0, 1000)
        self.game_detail_progress.setTextVisible(False)
        self.game_detail_progress.setFixedHeight(7)
        self.game_detail_progress.setStyleSheet(
            f"QProgressBar {{ background:{SURFACE}; border:none; border-radius:3px; }}"
            f"QProgressBar::chunk {{ background:{ACCENT_PRIMARY}; border-radius:3px; }}"
        )
        detail_info.addWidget(self.game_detail_progress)
        detail_info.addStretch()
        detail_body.addLayout(detail_info, 1)
        detail_layout.addLayout(detail_body)

        detail_title = QLabel("Unlocked achievements")
        detail_title.setStyleSheet(f"color:{TEXT_PRIMARY}; font-size:13px; font-weight:700;")
        detail_layout.addWidget(detail_title)
        self.game_detail_achievement_list = QListWidget()
        self.game_detail_achievement_list.setObjectName("profileList")
        self.game_detail_achievement_list.setMinimumHeight(70)
        self.game_detail_achievement_list.setMaximumHeight(230)
        detail_layout.addWidget(self.game_detail_achievement_list)
        self.games_stack.addWidget(self.game_detail)
        games_layout.addWidget(self.games_stack)
        self.column_layout.addWidget(self.games_section)
        self.column_layout.addSpacing(14)

        self.favorite_section = self._section("Favorite games")
        favorite_layout = self.favorite_section.layout()
        favorite_layout.setContentsMargins(18, 14, 18, 14)
        self.favorite_list = QListWidget()
        self.favorite_list.setObjectName("profileList")
        self.favorite_list.setMinimumHeight(66)
        self.favorite_list.setMaximumHeight(210)
        favorite_layout.addWidget(self.favorite_list)
        self.column_layout.addWidget(self.favorite_section)
        self.column_layout.addSpacing(14)

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
        self.column_layout.addSpacing(14)

        self.achievement_section = self._section("Recent achievements")
        achievement_layout = self.achievement_section.layout()
        achievement_layout.setContentsMargins(18, 14, 18, 14)
        self.achievement_list = QListWidget()
        self.achievement_list.setObjectName("profileList")
        self.achievement_list.setMinimumHeight(66)
        self.achievement_list.setMaximumHeight(250)
        achievement_layout.addWidget(self.achievement_list)
        self.column_layout.addWidget(self.achievement_section)
        self.column_layout.addSpacing(14)
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
        self._local_refresh_timer.stop()
        self._local_refresh_pending = False
        self.mode_label.setText("OWNER VIEW")
        self._profile_settings = load_profile_settings(self.settings, fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"))
        self._profile_theme_key = panel_theme_key(self._profile_settings.get("panel_theme_id"))
        self._document = build_public_projection(self.db, self._profile_settings)
        if self._profile_settings.get("public_handle") != self._social_handle:
            self._social_snapshot = self._empty_social_snapshot()
            self._social_handle = ""
        self.editor.setVisible(self._editing)
        self.btn_edit.setVisible(not self._editing)
        self._set_admin_controls(True)
        self._render(self._document)
        if self.isVisible() and automatic_network_allowed(self.settings):
            self._refresh_social()
        elif self.isVisible():
            self.friends_status.setText("Offline mode — friends are not refreshed.")

    def mark_local_data_changed(self) -> None:
        """Coalesce bursts of local game events into one UI/public refresh."""
        if self._editing:
            return
        self._local_refresh_pending = True
        self._profile_settings = load_profile_settings(self.settings, fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"))
        if self._mode == "owner" and self.isVisible():
            self._local_refresh_timer.start()
        if (
            automatic_network_allowed(self.settings)
            and self._profile_settings.get("published")
            and self.central_auth.signed_in
        ):
            if self._publishing:
                self._publish_dirty = True
            else:
                self._publish_timer.start()

    def _refresh_local_projection(self) -> None:
        """Refresh visible local profile data once after an event burst."""
        if not self._local_refresh_pending or self._editing:
            return
        self._local_refresh_pending = False
        if self._mode != "owner" or not self.isVisible():
            return
        self._profile_settings = load_profile_settings(
            self.settings,
            fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
        )
        self._profile_theme_key = panel_theme_key(self._profile_settings.get("panel_theme_id"))
        self._document = build_public_projection(self.db, self._profile_settings)
        self._render(self._document)

    def show_public(self, document: dict[str, Any]) -> bool:
        normalized = normalize_public_document(document)
        if normalized is None:
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText("This public profile is invalid or unavailable.")
            return False
        self._mode = "public"
        self._local_refresh_timer.stop()
        self._local_refresh_pending = False
        self._editing = False
        self._document = normalized
        self._profile_theme_key = panel_theme_key(normalized.get("panel_theme_id"))
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
        self.profile_action_strip.setVisible(enabled)
        self.btn_auth.setVisible(enabled)
        self.btn_auth.setEnabled(enabled and not self._auth_in_flight)
        self.btn_auth.setText("Sign out" if signed_in else "Sign in")
        self.btn_auth.setIcon(get_icon(
            "ph.sign-out-bold" if signed_in else "ph.sign-in-bold",
            color="#FFFFFF",
        ))
        self.btn_auth.setToolTip(
            "Sign out of the central profile service."
            if signed_in else "Sign in to manage and publish your public profile."
        )
        self.btn_banner_edit.setVisible(enabled and not self._editing)
        self.btn_publish.setVisible(enabled)
        self.btn_publish.setEnabled(signed_in and not self._auth_in_flight)
        self.btn_publish.setText("Unpublish profile" if published else "Publish profile")
        self.btn_publish.setIcon(get_icon(
            "ph.eye-slash-bold" if published else "ph.upload-simple-bold",
            color="#FFFFFF",
        ))
        self.btn_publish.setToolTip(
            "Sign in to manage the public profile."
            if not signed_in else (
                "Remove this profile from the public service."
                if published else "Publish this profile to the central service."
            )
        )
        self.btn_resync.setVisible(enabled)
        self.btn_resync.setEnabled(enabled and not self._auth_in_flight)
        self._update_social_controls(enabled, published, signed_in)

    def _auth_button_clicked(self) -> None:
        """Toggle the central profile session from the single profile action."""
        if self.central_auth.signed_in:
            self._sign_out()
        else:
            self._sign_in()

    def _background_style(self, background: dict[str, Any]) -> str:
        # The shared backdrop is painted once by ProfilePageWidget. Keeping
        # the canvas transparent makes it visible between all translucent
        # surfaces and prevents profile background choices from hiding glass.
        return "QWidget#profileCanvas { background: transparent; }"

    def _profile_background_cache_path(self, app_id: str) -> str:
        """Return a private cache path for a validated Steam AppID."""
        app_id = steam_app_id(app_id)
        if not app_id:
            return ""
        root = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
        if not root:
            return ""
        directory = os.path.join(root, "profile_heroes")
        try:
            os.makedirs(directory, mode=0o700, exist_ok=True)
        except OSError:
            return ""
        return os.path.join(directory, f"steam_{app_id}.jpg")

    def _load_profile_background_bytes(self, app_id: str) -> bytes:
        app_id = steam_app_id(app_id)
        if not app_id:
            return b""
        cached = self._profile_background_cache.pop(app_id, None)
        if cached:
            self._profile_background_cache[app_id] = cached
            return cached
        path = self._profile_background_cache_path(app_id)
        if not path:
            return b""
        try:
            with open(path, "rb") as stream:
                data = stream.read(MAX_PROFILE_BACKGROUND_BYTES + 1)
            if 0 < len(data) <= MAX_PROFILE_BACKGROUND_BYTES:
                self._cache_profile_background_bytes(app_id, data)
                return data
        except OSError:
            pass
        return b""

    def _cache_profile_background_bytes(self, app_id: str, data: bytes) -> None:
        """Keep only a small in-memory LRU-like window; disk remains the cache."""
        self._profile_background_cache.pop(app_id, None)
        self._profile_background_cache[app_id] = data
        while len(self._profile_background_cache) > MAX_PROFILE_BACKGROUND_CACHE_ITEMS:
            self._profile_background_cache.pop(next(iter(self._profile_background_cache)), None)

    @staticmethod
    def _decode_profile_background(data: bytes) -> QPixmap | None:
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return None
        if (
            pixmap.width() <= 0
            or pixmap.height() <= 0
            or pixmap.width() * pixmap.height() > MAX_PROFILE_BACKGROUND_PIXELS
        ):
            return None
        return pixmap

    def _set_profile_background_pixmap(self, pixmap: QPixmap) -> None:
        self._profile_background_pixmap = pixmap if not pixmap.isNull() else QPixmap()
        self._profile_background_blurred = QPixmap()
        self._profile_background_blur_size = (0, 0)
        self.update()

    def _set_profile_background(self, value: Any) -> None:
        """Resolve a profile background and queue only its fixed Steam URL."""
        background = normalize_background(value)
        if background.get("kind") != "steam_hero":
            self._set_profile_background_pixmap(QPixmap())
            return
        app_id = steam_app_id(background.get("app_id"))
        data = self._load_profile_background_bytes(app_id)
        if data:
            pixmap = self._decode_profile_background(data)
            if pixmap is not None:
                self._set_profile_background_pixmap(pixmap)
                return
        self._set_profile_background_pixmap(QPixmap())
        self._queue_profile_background(app_id)

    def _queue_profile_background(self, app_id: str) -> None:
        app_id = steam_app_id(app_id)
        if (
            not app_id
            or app_id in self._profile_background_inflight
            or not automatic_network_allowed(self.settings)
        ):
            return
        self._profile_background_inflight.add(app_id)

        def work():
            for candidate in steam_hero_urls(app_id):
                data = _download_profile_image(candidate, (2, 6))
                if data:
                    return data
            return b""

        worker = self._tasks.start(
            "SafeLauncher-ProfileBackground",
            work,
            lambda result, expected_app_id=app_id: self._profile_background_loaded(expected_app_id, result),
        )
        worker.error_occurred.connect(
            lambda _error, expected_app_id=app_id: self._profile_background_failed(expected_app_id)
        )

    def _profile_background_failed(self, app_id: str) -> None:
        self._profile_background_inflight.discard(app_id)

    def _profile_background_loaded(self, app_id: str, data: Any) -> None:
        self._profile_background_inflight.discard(app_id)
        if not isinstance(data, bytes) or not data:
            return
        pixmap = self._decode_profile_background(data)
        if pixmap is None:
            return
        self._cache_profile_background_bytes(app_id, data)
        cache_path = self._profile_background_cache_path(app_id)
        if cache_path:
            temp_path = f"{cache_path}.tmp"
            try:
                with open(temp_path, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_path, cache_path)
            except OSError:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
        current = normalize_background(self._document.get("background", {}))
        if current.get("kind") == "steam_hero" and current.get("app_id") == app_id:
            self._set_profile_background_pixmap(pixmap)

    def _editor_background(self) -> dict[str, Any]:
        """Return the background currently being edited, without mutating settings."""
        if self._editing and self._draft_background is not None:
            return normalize_background(self._draft_background)
        return normalize_background(self._profile_settings.get("background"))

    def _editor_preview_document(self) -> dict[str, Any]:
        """Build a public-shaped preview from the in-flight editor values."""
        value = dict(self._profile_settings)
        value.update({
            "display_name": self.name_edit.text() or self._profile_settings.get("display_name", "Player"),
            "public_handle": self.handle_edit.text() or self._profile_settings.get("public_handle", ""),
            "bio": self.bio_edit.toPlainText(),
            "avatar_id": self._draft_avatar_id,
            "avatar_asset_id": normalize_avatar_asset_id(self._draft_avatar_id),
            "panel_theme_id": self._draft_panel_theme_id,
            "background": self._editor_background(),
        })
        return build_public_projection(self.db, value)

    def _preview_editor_background(self, value: Any) -> None:
        """Preview a background while keeping the persisted profile untouched."""
        if self._mode != "owner" or not self._editing:
            return
        self._draft_background = normalize_background(value)
        self._render(self._editor_preview_document())

    def _on_background_mode_changed(self, _index: int = -1) -> None:
        selected = self.background_combo.currentData()
        visible = self._editing and selected == "steam_hero"
        self.background_hero_controls.setVisible(visible)
        # _populate_editor calls this after restoring the combo under a signal
        # blocker. Only a real combo change should replace the draft or trigger
        # a repaint, preventing recursive render/populate cycles.
        if not self._editing or _index < 0:
            return
        if selected in BACKGROUND_PRESETS:
            self._preview_editor_background(BACKGROUND_PRESETS[selected])
        elif selected == "custom":
            current = self._editor_background()
            if current.get("kind") != "solid":
                stops = current.get("stops")
                color = stops[0] if isinstance(stops, list) and stops else "#20242C"
                current = {"kind": "solid", "color": color}
            self._preview_editor_background(current)
        elif selected == "steam_hero" and self._editor_background().get("kind") == "steam_hero":
            self._preview_editor_background(self._editor_background())

    def _preview_steam_hero_from_app_id(self) -> None:
        """Preview a valid hero when the AppID field is submitted."""
        if (
            self._mode != "owner"
            or not self._editing
            or self.background_combo.currentData() != "steam_hero"
        ):
            return
        app_id = steam_app_id(self.background_app_id_edit.text())
        if app_id:
            self._preview_editor_background({"kind": "steam_hero", "app_id": app_id})

    def _on_panel_theme_changed(self, _index: int = -1) -> None:
        if not self._editing:
            return
        self._draft_panel_theme_id = normalize_panel_theme_id(self.panel_theme_combo.currentData())
        self.set_profile_theme(panel_theme_key(self._draft_panel_theme_id))

    def _use_steam_hero_background(self) -> None:
        if self._mode != "owner" or not self._editing:
            return
        app_id = steam_app_id(self.background_app_id_edit.text())
        if not app_id:
            QMessageBox.warning(self, "Profile background", "Enter a valid numeric Steam AppID.")
            return
        background = normalize_background({"kind": "steam_hero", "app_id": app_id})
        self._draft_background = background
        with QSignalBlocker(self.background_combo):
            self.background_combo.setCurrentIndex(self.background_combo.findData("steam_hero"))
        self._preview_editor_background(background)
        self._on_background_mode_changed()
        self.footer_status.setText(
            "Steam hero selected. It will be downloaded when online and saved with your profile changes."
        )

    def paintEvent(self, event) -> None:
        background = normalize_background(self._document.get("background", {}))
        painter = QPainter(self)
        if not painter.isActive():
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        width, height = self.width(), self.height()

        # Backgrounds are the bottom-most layer. They intentionally do not
        # read ProfileTheme: panel visuals must never replace a user's chosen
        # background or make public profiles look different by viewer.
        if background.get("kind") == "solid":
            painter.fillRect(self.rect(), QColor(str(background.get("color", "#121214"))))
        else:
            values = background.get("stops")
            stops = tuple(values[:3]) if isinstance(values, list) and len(values) >= 2 else ("#1C3158", "#121214")
            try:
                angle = max(0, min(360, int(background.get("angle", 135))))
            except (TypeError, ValueError, OverflowError):
                angle = 135
            radians = math.radians(angle)
            diagonal = max(1.0, math.hypot(width, height))
            center_x, center_y = width / 2.0, height / 2.0
            delta_x = math.cos(radians) * diagonal / 2.0
            delta_y = math.sin(radians) * diagonal / 2.0
            gradient = QLinearGradient(
                center_x - delta_x,
                center_y - delta_y,
                center_x + delta_x,
                center_y + delta_y,
            )
            gradient.setColorAt(0.0, QColor(stops[0]))
            if len(stops) >= 3:
                gradient.setColorAt(0.52, QColor(stops[1]))
                gradient.setColorAt(1.0, QColor(stops[2]))
            else:
                gradient.setColorAt(1.0, QColor(stops[1]))
            painter.fillRect(self.rect(), gradient)

        if background.get("kind") == "steam_hero" and not self._profile_background_pixmap.isNull() and width > 0 and height > 0:
            if self._profile_background_blur_size != (width, height):
                scaled = self._profile_background_pixmap.scaled(
                    width,
                    height,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                small = scaled.scaled(
                    max(1, width // 28),
                    max(1, height // 28),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._profile_background_blurred = small.scaled(
                    width,
                    height,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._profile_background_blur_size = (width, height)
            # Keep the hero recognizable while retaining readable text and
            # dark glass surfaces above it.
            painter.setOpacity(0.78)
            painter.drawPixmap(0, 0, self._profile_background_blurred)
            painter.setOpacity(1.0)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 118))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.end()

    def _render(self, document: dict[str, Any]) -> None:
        # Keep the document used by paintEvent in sync with the last render.
        # This is important for async hero downloads and editor previews.
        self._document = dict(document) if isinstance(document, dict) else {}
        document = self._document
        self._apply_profile_styles(document.get("background", {}))
        self._set_profile_background(document.get("background", {}))
        self.name_label.setText(str(document.get("display_name", "Player")))
        handle = str(document.get("handle", "") or "")
        self.handle_label.setText(f"@{handle}" if handle else "Username not set")
        bio = str(document.get("bio", "") or "").strip()
        self.bio_label.setText(bio)
        self.bio_label.setVisible(bool(bio))
        stats = document.get("stats", {}) if isinstance(document.get("stats"), dict) else {}
        self.stat_labels["games_count"].setText(str(stats.get("games_count", 0)))
        self.stat_labels["favorite_count"].setText(str(stats.get("favorite_count", 0)))
        seconds = max(0, int(stats.get("playtime_seconds", 0) or 0))
        self.stat_labels["playtime_seconds"].setText(self._format_hours(seconds))
        unlocked = int(stats.get("achievements_unlocked", 0) or 0)
        known = int(stats.get("achievements_known", 0) or 0)
        self.stat_labels["achievements_unlocked"].setText(f"{unlocked}/{known}" if known else str(unlocked))
        self._set_avatar(document.get("avatar_asset_id") or document.get("avatar_id"))
        if self._mode == "owner":
            published = bool(self._profile_settings.get("published"))
            handle = str(document.get("handle", "") or "").strip()
            self.public_badge.setText("PUBLIC PROFILE" if published else "PRIVATE PROFILE")
            self.status_label.setText(
                f"Published · Public · @{handle}" if published and handle
                else "Published · Public" if published
                else "Unpublished · Private"
            )
            self.btn_publish.setText("Unpublish profile" if published else "Publish profile")
            self.btn_publish.setIcon(get_icon(
                "ph.eye-slash-bold" if published else "ph.upload-simple-bold",
                color="#FFFFFF",
            ))
            self.footer_status.setText("Public publishing is separate from private game-save cloud synchronization.")
            if not self._editing:
                self._populate_editor()
        else:
            self.public_badge.setText("PUBLIC PROFILE")
            handle = str(document.get("handle", "") or "").strip()
            self.status_label.setText(f"Published · Public · @{handle}" if handle else "Published · Public")
            self.footer_status.setText("This is a public projection. Private launcher data is not shown.")
        self._render_games(document)
        self._fill_list(self.favorite_list, document.get("favorite_games"), lambda item: f"♥  {item.get('name', 'Favorite game')}")
        self._fill_list(self.achievement_list, document.get("recent_achievements"), lambda item: f"★  {item.get('name', 'Achievement')}  ·  {item.get('game', 'Game')}")
        self._render_social()

    @staticmethod
    def _clear_grid(layout: QGridLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _render_games(self, document: dict[str, Any]) -> None:
        self._selected_profile_game = None
        self.games_stack.setCurrentIndex(0)
        self._clear_grid(self.games_grid)
        self._clear_grid(self.games_all_grid)
        self._game_cards = {}
        raw_games = document.get("games", []) if isinstance(document.get("games"), list) else []
        games = [game for game in raw_games if isinstance(game, dict) and str(game.get("app_id", ""))]
        self._profile_games = games
        self._all_games_populated = False
        pending: dict[str, str] = {}
        self.games_all_title.setText(f"All games ({len(games)})")
        if not games:
            empty = QLabel("No Steam games are visible on this profile yet.")
            empty.setObjectName("profileMuted")
            empty.setWordWrap(True)
            self.games_grid.addWidget(empty, 0, 0, 1, 3)
            return

        for index, game in enumerate(games[:5]):
            self._add_game_card(game, index, self.games_grid, pending)
        if len(games) > 5:
            see_more = ProfileSeeMoreCard(len(games))
            see_more.clicked.connect(self._show_all_games)
            row, column = divmod(5, 3)
            self.games_grid.addWidget(see_more, row, column)
        for column in range(3):
            self.games_grid.setColumnStretch(column, 1)
            self.games_all_grid.setColumnStretch(column, 1)
        # The preview is the initial viewport. Full-library artwork is lazy
        # loaded when the user opens "See more", preventing a large profile
        # from starting dozens of image requests during navigation.
        self._queue_artwork_download(pending)

    def _add_game_card(
        self,
        game: dict[str, Any],
        index: int,
        layout: QGridLayout,
        pending: dict[str, str] | None = None,
    ) -> None:
        """Create one library card and optionally schedule its artwork.

        The complete library is populated only when opened. Keeping widget
        creation lazy matters more than image-download laziness for profiles
        with many games, because Qt layout/style work happens on the GUI
        thread.
        """
        app_id = str(game.get("app_id", ""))
        card = ProfileGameCard(game)
        card.clicked.connect(self._open_game_detail)
        self._game_cards.setdefault(app_id, []).append(card)
        row, column = divmod(index, 3)
        layout.addWidget(card, row, column)
        url = str(game.get("artwork_url", "") or "")
        if not url:
            return
        if url in self._artwork_cache:
            card.set_artwork_bytes(self._artwork_cache[url])
        elif pending is not None and url not in self._artwork_inflight:
            pending[app_id] = url

    def _show_all_games(self) -> None:
        """Open the complete library and lazily fetch its remaining artwork."""
        self.games_stack.setCurrentIndex(1)
        if self._all_games_populated:
            return
        self._all_games_populated = True
        self._clear_grid(self.games_all_grid)
        pending: dict[str, str] = {}
        for index, game in enumerate(self._profile_games):
            self._add_game_card(game, index, self.games_all_grid)
            app_id = str(game.get("app_id", ""))
            url = str(game.get("artwork_url", "") or "")
            if url and url not in self._artwork_cache and url not in self._artwork_inflight:
                pending[app_id] = url
        self._queue_artwork_download(pending)

    def _queue_artwork_download(self, pending: dict[str, str]) -> None:
        if not pending or not automatic_network_allowed(self.settings):
            return

        self._artwork_inflight.update(pending.values())

        def work():
            downloaded = {}
            for app_id, url in pending.items():
                for candidate in steam_hero_urls(app_id):
                    data = _download_profile_image(candidate, (2, 5))
                    if data:
                        # Keep the canonical URL as the cache key even when a
                        # fallback endpoint supplied the bytes. That way all
                        # cards for this AppID can use the same memory entry.
                        downloaded[app_id] = (url, data)
                        break
            return downloaded

        worker = self._tasks.start(
            "SafeLauncher-ProfileArtwork",
            work,
            lambda result, urls=set(pending.values()): self._artwork_loaded(result, urls),
        )
        worker.error_occurred.connect(
            lambda _error, urls=set(pending.values()): self._artwork_request_failed(urls)
        )

    def _artwork_request_failed(self, urls: set[str]) -> None:
        self._artwork_inflight.difference_update(urls)

    def _artwork_loaded(self, result: Any, urls: set[str]) -> None:
        if not isinstance(result, dict):
            self._artwork_request_failed(urls)
            return
        self._artwork_inflight.difference_update(urls)
        for app_id, value in result.items():
            if not isinstance(value, tuple) or len(value) != 2:
                continue
            url, data = value
            if not isinstance(url, str) or not isinstance(data, bytes) or not data:
                continue
            self._artwork_cache[url] = data
            # A request can outlive a profile navigation. The bytes are
            # immutable, validated Steam CDN artwork, so applying them to a
            # current card with the same AppID is safe and avoids leaving a
            # placeholder forever after a fast owner/public refresh.
            for card in self._game_cards.get(str(app_id), []):
                if str(card.game.get("artwork_url", "")) == url:
                    card.set_artwork_bytes(data)
            if self._selected_profile_game and str(self._selected_profile_game.get("app_id", "")) == str(app_id):
                pixmap = QPixmap()
                pixmap.loadFromData(data)
                self._set_detail_artwork(pixmap)

    def _set_detail_artwork(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            self.game_detail_artwork.clear()
            self.game_detail_artwork.setText("Steam hero")
            return
        self.game_detail_artwork.setText("")
        self.game_detail_artwork.setPixmap(pixmap.scaled(
            self.game_detail_artwork.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def _return_to_games(self) -> None:
        self.games_stack.setCurrentIndex(self._games_return_index)

    def _open_game_detail(self, game: dict[str, Any]) -> None:
        app_id = str(game.get("app_id", "") or "")
        if not app_id:
            return
        self._selected_profile_game = dict(game)
        self.game_detail_name.setText(str(game.get("name", f"Steam App {app_id}")))
        self.game_detail_app_id.setText(f"Steam AppID {app_id}")
        self.game_detail_playtime.setText(
            f"Playtime  ·  {ProfileGameCard._format_hours(max(0, int(game.get('playtime_seconds', 0) or 0)))}"
        )
        achievement = game.get("achievements") if isinstance(game.get("achievements"), dict) else {}
        unlocked = max(0, int(achievement.get("unlocked_count", 0) or 0))
        total = max(unlocked, int(achievement.get("total_count", 0) or 0))
        percentage = max(0.0, min(100.0, float(achievement.get("percentage", 0) or 0)))
        self.game_detail_achievements.setText(f"Achievements  ·  {unlocked}/{total} unlocked  ·  {percentage:.1f}%")
        self.game_detail_progress.setValue(round(percentage * 10))
        self.game_detail_achievement_list.clear()
        recent = achievement.get("recent", []) if isinstance(achievement.get("recent"), list) else []
        if not recent:
            self.game_detail_achievement_list.addItem(QListWidgetItem("No unlocked achievements published yet."))
        else:
            for item in recent:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "Achievement"))
                self.game_detail_achievement_list.addItem(QListWidgetItem(f"✓  {name}"))
        card = self._game_cards.get(app_id, [])
        pixmap = next((item._pixmap for item in card if not item._pixmap.isNull()), QPixmap())
        self._set_detail_artwork(pixmap)
        self._games_return_index = self.games_stack.currentIndex() if self.games_stack.currentIndex() in (0, 1) else 0
        self.games_stack.setCurrentIndex(2)
        self.scroll.verticalScrollBar().setValue(0)

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
                ("View profile", lambda checked=False, h=friend_handle: self.open_profile_handle_requested.emit(h)),
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
                ("View profile", lambda checked=False, h=friend_handle: self.open_profile_handle_requested.emit(h)),
                ("Cancel", lambda checked=False, r=request_id: self._respond_to_request(r, "cancel")),
            ]))
        for item in friends if isinstance(friends, list) else []:
            if not isinstance(item, dict):
                continue
            friend_handle = str(item.get("handle", ""))
            self.friends_list.addWidget(self._social_row(item, [
                ("View profile", lambda checked=False, h=friend_handle: self.open_profile_handle_requested.emit(h)),
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
        if not automatic_network_allowed(self.settings):
            self.friends_status.setText("Offline mode — friends are not refreshed.")
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
        if not automatic_network_allowed(self.settings):
            self.friends_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.friends_status.setText("Offline mode — friend changes are unavailable.")
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

    @staticmethod
    def _decode_avatar(data: bytes) -> QPixmap | None:
        if not isinstance(data, bytes) or not data or len(data) > MAX_PROFILE_AVATAR_BYTES:
            return None
        pixmap = QPixmap()
        if not pixmap.loadFromData(ProfilePageWidget._png_without_iccp(data), "PNG"):
            return None
        if pixmap.width() <= 0 or pixmap.height() <= 0 or pixmap.width() * pixmap.height() > 4_000_000:
            return None
        return pixmap

    @staticmethod
    def _png_without_iccp(data: bytes) -> bytes:
        """Remove broken embedded ICC profiles before handing PNGs to Qt.

        The catalog files are developer-provided and remain byte-for-byte
        hashable in the cache. This decode-only normalization prevents Qt's
        noisy ``iCCP: known incorrect sRGB profile`` warning without changing
        the signed catalog bytes or the cloud representation.
        """
        signature = b"\x89PNG\r\n\x1a\n"
        if not data.startswith(signature):
            return data
        chunks = [signature]
        offset = len(signature)
        try:
            while offset + 12 <= len(data):
                length = int.from_bytes(data[offset:offset + 4], "big")
                end = offset + 12 + length
                if end > len(data):
                    return data
                chunk_type = data[offset + 4:offset + 8]
                if chunk_type != b"iCCP":
                    chunks.append(data[offset:end])
                offset = end
                if chunk_type == b"IEND":
                    return b"".join(chunks)
        except (TypeError, ValueError, OverflowError):
            return data
        return data

    def _cache_avatar_pixmap(self, avatar_id: str, pixmap: QPixmap) -> None:
        self._avatar_pixmaps.pop(avatar_id, None)
        self._avatar_pixmaps[avatar_id] = pixmap
        while len(self._avatar_pixmaps) > MAX_PROFILE_AVATAR_CACHE_ITEMS:
            self._avatar_pixmaps.pop(next(iter(self._avatar_pixmaps)), None)

    def _avatar_catalog_item(self, avatar_id: str) -> dict[str, Any] | None:
        normalized = normalize_avatar_id(avatar_id)
        asset_id = normalize_avatar_asset_id(avatar_id)
        return next(
            (
                item for item in self._avatar_catalog
                if str(item.get("id", "")) == normalized
                or str(item.get("legacy_id", "")) == normalized
                or (asset_id is not None and normalize_avatar_asset_id(item.get("asset_id")) == asset_id)
            ),
            None,
        )

    def _avatar_reference(self, value: Any) -> str:
        """Resolve legacy local/profile aliases to the canonical asset number."""
        normalized = normalize_avatar_id(value)
        if not normalized:
            return ""
        item = self._avatar_catalog_item(normalized)
        asset_id = normalize_avatar_asset_id(item.get("asset_id")) if item else None
        return str(asset_id) if asset_id is not None else normalized

    def _set_avatar(self, value: Any) -> None:
        avatar_id = self._avatar_reference(value)
        pixmap = self._avatar_pixmaps.get(avatar_id) if avatar_id else None
        if pixmap is not None and not pixmap.isNull():
            self.avatar.setPixmap(pixmap.scaled(128, 128, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
            self.avatar.setStyleSheet("border-radius:64px; background:#20242C;")
            self._cache_avatar_pixmap(avatar_id, pixmap)
            return
        self.avatar.clear()
        self.avatar.setText("SL")
        self.avatar.setStyleSheet(f"border-radius:64px; background:{SURFACE_ELEVATED}; color:{ACCENT_PRIMARY}; font-size:30px; font-weight:800;")
        if avatar_id:
            self._queue_avatar_image(avatar_id)

    def _queue_avatar_image(self, avatar_id: str, dialog: ProfileAvatarCatalogDialog | None = None) -> None:
        """Compatibility entrypoint that joins the shared batch request."""
        self._queue_avatar_batch([avatar_id])

    def _queue_avatar_batch(self, avatar_ids: Any) -> None:
        requested: list[str] = []
        seen: set[str] = set()
        for value in list(avatar_ids or []):
            avatar_id = self._avatar_reference(value)
            if avatar_id and avatar_id not in seen:
                requested.append(avatar_id)
                seen.add(avatar_id)
        requested = [avatar_id for avatar_id in requested[:128] if avatar_id not in self._avatar_inflight]
        if not requested:
            return
        self._avatar_inflight.update(requested)
        service_url = get_profile_service_url()

        def work():
            downloaded: dict[str, bytes] = {}
            missing: list[str] = []
            for avatar_id in requested:
                catalog_item = self._avatar_catalog_item(avatar_id)
                expected_hash = str(catalog_item.get("sha256", "")) if catalog_item else ""
                cached = read_cached_avatar(avatar_id, expected_hash) if expected_hash else b""
                if cached:
                    downloaded[avatar_id] = cached
                else:
                    missing.append(avatar_id)
            if missing and automatic_network_allowed(self.settings):
                with ProfileServiceClient(service_url) as client:
                    downloaded.update(client.fetch_avatar_batch(missing))
            return downloaded

        worker = self._tasks.start(
            "SafeLauncher-ProfileAvatarBatch",
            work,
            lambda result, expected_ids=set(requested): self._avatar_batch_loaded(expected_ids, result),
        )
        worker.error_occurred.connect(
            lambda _error, expected_ids=set(requested): self._avatar_batch_failed(expected_ids)
        )

    def _avatar_batch_failed(self, avatar_ids: set[str]) -> None:
        self._avatar_inflight.difference_update(avatar_ids)

    def _avatar_batch_loaded(self, avatar_ids: set[str], result: Any) -> None:
        self._avatar_inflight.difference_update(avatar_ids)
        if not isinstance(result, dict):
            return
        for avatar_id, data in result.items():
            avatar_id = normalize_avatar_id(avatar_id)
            if avatar_id not in avatar_ids or not isinstance(data, bytes):
                continue
            expected = self._avatar_catalog_item(avatar_id)
            digest = hashlib.sha256(data).hexdigest()
            if expected and digest != str(expected.get("sha256", "")):
                continue
            pixmap = self._decode_avatar(data)
            if pixmap is None:
                continue
            self._cache_avatar_pixmap(avatar_id, pixmap)
            save_cached_avatar(avatar_id, digest, data)
            if self._avatar_dialog is not None and self._avatar_dialog.isVisible():
                self._avatar_dialog.set_thumbnail(avatar_id, pixmap)
            current_avatar = self._draft_avatar_id if self._editing else (
                self._document.get("avatar_asset_id") or self._document.get("avatar_id")
            )
            if self._avatar_reference(current_avatar) == avatar_id:
                self._set_avatar(avatar_id)

    def _avatar_catalog_loaded(self, result: Any) -> None:
        self._avatar_catalog_loading = False
        self.btn_avatar.setEnabled(True)
        if isinstance(result, Exception):
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText(f"Could not load profile pictures: {result}")
            return
        if not isinstance(result, list) or not result:
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText("The cloud profile picture catalog is empty.")
            return
        self._avatar_catalog = result
        save_cached_avatar_catalog(result)
        if self._mode == "owner" and not self._editing:
            current = self._profile_settings.get("avatar_id")
            item = self._avatar_catalog_item(current)
            asset_id = normalize_avatar_asset_id(item.get("asset_id")) if item else None
            if asset_id is not None and self._profile_settings.get("avatar_asset_id") != asset_id:
                self._profile_settings = save_profile_settings(
                    self.settings,
                    {**self._profile_settings, "avatar_asset_id": asset_id, "avatar_id": str(asset_id)},
                    mark_changed=False,
                )
        self._show_avatar_catalog()

    def _show_avatar_catalog(self) -> None:
        if not self._avatar_catalog:
            return
        dialog = ProfileAvatarCatalogDialog(self._avatar_catalog, self._avatar_reference(self._draft_avatar_id), self)
        self._avatar_dialog = dialog
        dialog.catalog_avatar_ids.connect(
            lambda ids, expected_dialog=dialog: self._load_avatar_thumbnails(expected_dialog, ids)
        )
        dialog.visible_avatar_ids.connect(
            lambda ids, expected_dialog=dialog: self._load_avatar_thumbnails(expected_dialog, ids)
        )
        try:
            if dialog.exec() == dialog.DialogCode.Accepted:
                selected = self._avatar_reference(dialog.selected_avatar_id)
                if selected:
                    self._draft_avatar_id = selected
                    item = self._avatar_catalog_item(selected)
                    self.avatar_selection_label.setText(str(item.get("label", selected)) if item else selected)
                    self._set_avatar(selected)
        finally:
            if self._avatar_dialog is dialog:
                self._avatar_dialog = None
            dialog.deleteLater()

    def _load_avatar_thumbnails(self, dialog: ProfileAvatarCatalogDialog, avatar_ids: Any) -> None:
        if self._avatar_dialog is not dialog or not dialog.isVisible():
            return
        self._queue_avatar_batch(list(avatar_ids))

    def _populate_editor(self) -> None:
        if self._mode != "owner":
            return
        with QSignalBlocker(self.name_edit), QSignalBlocker(self.handle_edit), QSignalBlocker(self.bio_edit):
            self.name_edit.setText(self._profile_settings.get("display_name", "Player"))
            self.handle_edit.setText(str(self._profile_settings.get("public_handle", "") or ""))
            self.bio_edit.setPlainText(str(self._profile_settings.get("bio", "") or ""))
        handle = self.handle_edit.text()
        published = bool(self._profile_settings.get("published"))
        self.handle_edit.setReadOnly(published)
        self._handle_check_timer.stop()
        self._handle_check_serial += 1
        self._handle_check_inflight = False
        self._handle_availability = True if published and handle else None
        self._update_bio_count()
        panel_theme_id = normalize_panel_theme_id(
            self._draft_panel_theme_id if self._editing else self._profile_settings.get("panel_theme_id")
        )
        self._draft_panel_theme_id = panel_theme_id
        with QSignalBlocker(self.panel_theme_combo):
            self.panel_theme_combo.setCurrentIndex(
                max(0, self.panel_theme_combo.findData(panel_theme_key(panel_theme_id)))
            )
        selected_avatar = self._avatar_reference(
            self._draft_avatar_id if self._editing else (
                self._profile_settings.get("avatar_asset_id") or self._profile_settings.get("avatar_id")
            )
        )
        avatar_item = self._avatar_catalog_item(selected_avatar) if selected_avatar else None
        self.avatar_selection_label.setText(
            str(avatar_item.get("label", selected_avatar)) if avatar_item else (selected_avatar or "Using initials")
        )
        self.handle_hint.setText(
            "Published usernames are locked so shared profile links keep working."
            if published else
            "Use 3–32 lowercase letters, numbers, dots, underscores, or hyphens. Availability is checked before publishing."
        )
        background = self._editor_background()
        background_kind = background.get("kind")
        with QSignalBlocker(self.background_combo), QSignalBlocker(self.background_app_id_edit):
            if background_kind == "steam_hero":
                self.background_combo.setCurrentIndex(self.background_combo.findData("steam_hero"))
                self.background_app_id_edit.setText(str(background.get("app_id", "")))
            else:
                self.background_app_id_edit.clear()
                found = False
                for index in range(self.background_combo.count()):
                    candidate = BACKGROUND_PRESETS.get(self.background_combo.itemData(index))
                    if candidate and candidate == background:
                        self.background_combo.setCurrentIndex(index)
                        found = True
                        break
                if not found:
                    self.background_combo.setCurrentIndex(self.background_combo.findData("custom"))
        self._on_background_mode_changed()

    def _update_bio_count(self) -> None:
        """Keep the editor counter based on the actual bounded text."""
        text = self.bio_edit.toPlainText()
        self.bio_count.setText(f"{len(text)}/{MAX_BIO_LENGTH}")

    def _limit_bio(self) -> None:
        """Bound pasted/input bio text without moving the user's cursor."""
        text = self.bio_edit.toPlainText()
        if len(text) > MAX_BIO_LENGTH:
            cursor = self.bio_edit.textCursor()
            position = min(cursor.position(), MAX_BIO_LENGTH)
            with QSignalBlocker(self.bio_edit):
                self.bio_edit.setPlainText(text[:MAX_BIO_LENGTH])
            cursor.setPosition(position)
            self.bio_edit.setTextCursor(cursor)
        self._update_bio_count()

    def _schedule_handle_availability(self, _text: str = "") -> None:
        """Debounce username checks so typing does not create request storms."""
        self._handle_check_timer.stop()
        self._handle_check_serial += 1
        self._handle_availability = None
        if self._mode != "owner" or not self._editing or self.handle_edit.isReadOnly():
            return
        candidate = normalize_username_handle(self.handle_edit.text())
        if not candidate:
            self.handle_hint.setText("Use 3–32 lowercase letters, numbers, dots, underscores, or hyphens.")
            self.handle_hint.setStyleSheet("")
            return
        if not automatic_network_allowed(self.settings):
            self.handle_hint.setText("Offline mode — the username will be checked when you publish.")
            self.handle_hint.setStyleSheet(f"color:{TEXT_SECONDARY};")
            return
        self.handle_hint.setText("Checking username availability…")
        self.handle_hint.setStyleSheet(f"color:{TEXT_SECONDARY};")
        self._handle_check_timer.start()

    def _check_handle_availability(self) -> None:
        if self._mode != "owner" or not self._editing or self.handle_edit.isReadOnly():
            return
        candidate = normalize_username_handle(self.handle_edit.text())
        if not candidate or not automatic_network_allowed(self.settings):
            return
        serial = self._handle_check_serial
        self._handle_check_inflight = True

        def work():
            with self._central_profile_client() as client:
                return client.check_handle_availability(candidate)

        worker = self._tasks.start(
            "SafeLauncher-ProfileHandleAvailability",
            work,
            lambda result, expected_serial=serial, expected_handle=candidate: self._handle_availability_done(
                result, expected_serial, expected_handle
            ),
        )
        worker.error_occurred.connect(
            lambda error, expected_serial=serial, expected_handle=candidate: self._handle_availability_error(
                error, expected_serial, expected_handle
            )
        )

    def _handle_availability_done(self, result: Any, serial: int, handle: str) -> None:
        if serial != self._handle_check_serial or handle != normalize_username_handle(self.handle_edit.text()):
            return
        self._handle_check_inflight = False
        available = isinstance(result, bool) and result
        self._handle_availability = available
        if available:
            self.handle_hint.setText("Username is available and will become stable after publishing.")
            self.handle_hint.setStyleSheet(f"color:{SEMANTIC_SUCCESS};")
        else:
            self.handle_hint.setText("That username is already taken. Choose another one.")
            self.handle_hint.setStyleSheet(f"color:{SEMANTIC_ERROR};")

    def _handle_availability_error(self, _error: str, serial: int, handle: str) -> None:
        if serial != self._handle_check_serial or handle != normalize_username_handle(self.handle_edit.text()):
            return
        self._handle_check_inflight = False
        self._handle_availability = None
        self.handle_hint.setText("Could not check availability; the server will verify it when you publish.")
        self.handle_hint.setStyleSheet(f"color:{TEXT_SECONDARY};")

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

    def _setup_username(self, identity: dict[str, Any] | None = None) -> bool:
        """Collect the first public handle without persisting OIDC claims."""
        identity = identity if isinstance(identity, dict) else {}
        local = load_profile_settings(
            self.settings,
            fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
        )
        if str(local.get("public_handle", "") or ""):
            return True
        suggestion = profile_username_suggestion(identity)
        while True:
            value, accepted = QInputDialog.getText(
                self,
                "Choose your profile handle",
                "This handle is used in your public profile URL. You can change it before publishing:",
                text=suggestion,
            )
            if not accepted:
                return False
            handle = normalize_username_handle(value)
            if not handle:
                QMessageBox.warning(
                    self,
                    "Invalid profile handle",
                    "Use at least 3 characters: lowercase letters, numbers, dots, underscores, or hyphens.",
                )
                continue
            display_name = str(local.get("display_name", "Player") or "Player")
            identity_name = str(identity.get("name") or identity.get("nickname") or "").strip()
            if display_name == "Player" and identity_name:
                display_name = " ".join(identity_name.split())[:64] or display_name
            self._profile_settings = save_profile_settings(self.settings, {
                **local,
                "display_name": display_name,
                "public_handle": handle,
                "published": False,
            })
            self.profile_changed.emit()
            return True

    def _sign_in(self) -> None:
        if self._auth_in_flight or self._mode != "owner":
            return
        if not automatic_network_allowed(self.settings):
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText("Offline mode — central sign-in is unavailable.")
            return
        self._auth_in_flight = True
        self._set_admin_controls(True)
        self.footer_status.setStyleSheet("")
        self.footer_status.setText("Opening central sign-in…")

        def work():
            self.central_auth.device_login(
                progress=lambda message: self.auth_progress.emit(message),
            )
            remote = self._reconcile_authenticated_profile()
            return {
                "remote": remote,
                # Existing profiles already have a stable handle. Avoid an
                # unnecessary identity request and never retain claims after
                # the first-login suggestion has been derived.
                "identity": {} if remote is not None else self.central_auth.userinfo(),
            }

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
        payload = result if isinstance(result, dict) else {}
        remote = payload.get("remote") if isinstance(payload.get("remote"), dict) else None
        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        if remote is not None:
            remote_settings = {
                **self._profile_settings,
                "display_name": remote.get("display_name", self._profile_settings.get("display_name", "Player")),
                "bio": remote.get("bio", self._profile_settings.get("bio", "")),
                "avatar_id": remote.get("avatar_id"),
                "avatar_asset_id": remote.get("avatar_asset_id"),
                "panel_theme_id": remote.get("panel_theme_id"),
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
            if self._setup_username(identity):
                status_message = f"Signed in as @{self._profile_settings.get('public_handle')}. Publish this profile to make it public."
            else:
                status_message = "Signed in. Choose a profile handle before publishing."
        self.show_owner()
        self.private_profile_changed.emit()
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
        self._draft_avatar_id = self._avatar_reference(
            self._profile_settings.get("avatar_asset_id") or self._profile_settings.get("avatar_id")
        )
        self._draft_background = normalize_background(self._profile_settings.get("background"))
        self._draft_panel_theme_id = normalize_panel_theme_id(self._profile_settings.get("panel_theme_id"))
        self.set_profile_theme(panel_theme_key(self._draft_panel_theme_id))
        self.editor.setVisible(True)
        self.btn_edit.setVisible(False)
        self._populate_editor()

    def _cancel_edit(self) -> None:
        self._editing = False
        self._draft_avatar_id = ""
        self._draft_background = None
        self._draft_panel_theme_id = normalize_panel_theme_id(self._profile_settings.get("panel_theme_id"))
        self.show_owner()

    def _save_edit(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Profile", "Enter a display name.")
            return
        handle = self.handle_edit.text().strip().lower()
        current_handle = str(self._profile_settings.get("public_handle", "") or "").lower()
        if bool(self._profile_settings.get("published")):
            handle = current_handle
        elif not HANDLE_RE.fullmatch(handle):
            handle = normalize_username_handle(handle)
        if not handle or not HANDLE_RE.fullmatch(handle):
            QMessageBox.warning(
                self,
                "Username",
                "Use 3–32 lowercase letters, numbers, dots, underscores, or hyphens.",
            )
            return
        if not bool(self._profile_settings.get("published")):
            if self._handle_check_inflight:
                QMessageBox.information(
                    self,
                    "Checking username",
                    "Wait for the username availability check to finish, then save again.",
                )
                return
            if self._handle_availability is False:
                QMessageBox.warning(
                    self,
                    "Username unavailable",
                    "That username is already taken. Choose another one.",
                )
                return
        selected = self.background_combo.currentData()
        if selected == "steam_hero":
            app_id = steam_app_id(self.background_app_id_edit.text())
            if not app_id:
                QMessageBox.warning(self, "Profile background", "Enter a valid numeric Steam AppID.")
                return
            background = normalize_background({"kind": "steam_hero", "app_id": app_id})
        elif selected == "custom":
            background = self._editor_background()
            if background.get("kind") != "solid":
                stops = background.get("stops")
                background = {
                    "kind": "solid",
                    "color": stops[0] if isinstance(stops, list) and stops else "#20242C",
                }
        else:
            background = BACKGROUND_PRESETS.get(selected, BACKGROUND_PRESETS["midnight"])
        value = dict(self._profile_settings)
        value.update({
            "display_name": name,
            "public_handle": handle,
            "bio": self.bio_edit.toPlainText(),
            "avatar_id": self._draft_avatar_id,
            "avatar_asset_id": normalize_avatar_asset_id(self._draft_avatar_id),
            "panel_theme_id": self._draft_panel_theme_id,
            "background": background,
        })
        was_published = bool(self._profile_settings.get("published"))
        normalized = save_profile_settings(self.settings, value)
        self._profile_settings = normalized
        self._editing = False
        self._draft_avatar_id = ""
        self._draft_background = None
        self._draft_panel_theme_id = normalized.get("panel_theme_id", 1)
        self.profile_changed.emit()
        self.show_owner()
        self.footer_status.setStyleSheet(f"color:{SEMANTIC_SUCCESS};")
        if was_published and self.central_auth.signed_in and automatic_network_allowed(self.settings):
            self.mark_local_data_changed()
            self.footer_status.setText("Profile changes saved. Public profile update queued.")
        else:
            self.footer_status.setText("Profile changes saved locally. Publish to update the public profile.")

    def _choose_avatar(self) -> None:
        if self._mode != "owner" or not self._editing:
            return
        if self._avatar_catalog:
            self._show_avatar_catalog()
            return
        if not automatic_network_allowed(self.settings):
            QMessageBox.information(
                self,
                "Profile pictures unavailable offline",
                "Connect to the internet once to download the SafeLauncher profile picture catalog.",
            )
            return
        if self._avatar_catalog_loading:
            return
        self._avatar_catalog_loading = True
        self.btn_avatar.setEnabled(False)
        self.footer_status.setStyleSheet("")
        self.footer_status.setText("Loading cloud profile pictures…")

        def work():
            with ProfileServiceClient(get_profile_service_url()) as client:
                return client.list_avatar_catalog()

        worker = self._tasks.start("SafeLauncher-ProfileAvatarCatalog", work, self._avatar_catalog_loaded)
        worker.error_occurred.connect(lambda error: self._avatar_catalog_loaded(ProfileServiceError(str(error), "catalog_fetch_failed")))

    def _remove_avatar(self) -> None:
        if self._mode != "owner" or not self._editing:
            return
        self._draft_avatar_id = ""
        self.avatar_selection_label.setText("Using initials")
        self._set_avatar(None)

    def _choose_color(self) -> None:
        current = self._editor_background()
        initial = QColor(str(current.get("color") or current.get("stops", ["#20242C"])[0]))
        color = QColorDialog.getColor(initial, self, "Choose profile background")
        if color.isValid():
            with QSignalBlocker(self.background_combo):
                self.background_combo.setCurrentIndex(self.background_combo.findData("custom"))
            self._preview_editor_background({"kind": "solid", "color": color.name().upper()})

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
        handle = self._profile_settings.get("public_handle")
        if not handle:
            if not self._setup_username({}):
                return
            handle = self._profile_settings.get("public_handle")
        self._profile_settings = save_profile_settings(self.settings, {**self._profile_settings, "public_handle": handle}, mark_changed=False)
        self._publish_current_document()

    def _publish_current_document(self) -> None:
        if self._publishing:
            return
        if not automatic_network_allowed(self.settings):
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText("Offline mode — public profile publishing is paused.")
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
        if not automatic_network_allowed(self.settings):
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText("Offline mode — public profile changes are unavailable.")
            return
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
        if not automatic_network_allowed(self.settings):
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText("Offline mode — private profile resync is paused.")
            return
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
        self._handle_check_timer.stop()
        self._local_refresh_timer.stop()
        self._publish_timer.stop()
        self._tasks.cancel_all(250)
        super().closeEvent(event)
