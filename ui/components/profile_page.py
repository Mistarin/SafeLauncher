"""Full-page owner and public profile presentation."""

from __future__ import annotations

import hashlib
import math
import os
import time
from typing import Any

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal, QSettings, QSignalBlocker, QStandardPaths, QTimer
from PyQt6.QtGui import QColor, QPainter, QPixmap, QLinearGradient
from PyQt6.QtWidgets import (
    QColorDialog, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QPlainTextEdit,
    QProgressBar, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from core.profile_models import (
    BACKGROUND_PRESETS, MAX_BIO_LENGTH, build_public_projection,
    HANDLE_RE, load_profile_settings, normalize_avatar_asset_id, normalize_avatar_id,
    normalize_background, normalize_panel_theme_id, normalize_username_handle, panel_theme_key,
    normalize_public_document, normalize_social_snapshot, profile_username_suggestion, save_profile_settings,
    steam_app_id,
)
from core.profile_service import ProfileServiceError, get_profile_service_url, is_local_service_url
from core.profile_resource_service import ProfileResourceService
from core.cache_policy import cache_policy
from core.profile_avatar_catalog import (
    is_valid_avatar_bytes,
    load_cached_avatar_catalog,
    normalize_avatar_catalog,
    read_cached_avatar,
    retire_cached_avatar,
    retire_cached_avatar_catalog,
    save_cached_avatar,
    save_cached_avatar_catalog,
)
from core.central_auth import CentralAuthError, CentralAuthSession
from core.secret_store import delete_secret, get_secret
from core.safe_thread import TaskSupervisor
from core.network_policy import automatic_network_allowed
from core.request_contracts import RequestKey, RequestPriority, ResourceStatus
from core.resource_cache import ResourceCache
from core.presentation_cache import PresentationCache
from ui.resource_binding import (
    ResourceBinding,
    ResourceBindingRegistry,
    bind_resource,
    bind_request,
)
from ui.icons import get_icon
from ui.dialogs.profile_avatar_dialog import ProfileAvatarCatalogDialog
from ui.dialogs.profile_username_dialog import ProfileUsernameDialog
from ui.theme import (
    ACCENT_PRIMARY, ACCENT_HOVER, ACCENT_PRESSED, BORDER, SEMANTIC_ERROR, SEMANTIC_SUCCESS,
    SURFACE, SURFACE_ELEVATED, TEXT_MUTED, TEXT_PRIMARY, TEXT_SECONDARY,
)
from ui.profile_theme import get_profile_theme, normalize_profile_theme, profile_theme_choices, theme_rgba

PROFILE_CARD_HEIGHT = 196
PROFILE_ARTWORK_HEIGHT = 104
MAX_PROFILE_BACKGROUND_BYTES = 4 * 1024 * 1024
MAX_PROFILE_BACKGROUND_PIXELS = 32_000_000
MAX_PROFILE_AVATAR_CACHE_ITEMS = 16
MAX_PROFILE_ARTWORK_CACHE_ITEMS = 32
MAX_PROFILE_AVATAR_BYTES = 512 * 1024


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


class ProfileGameRow(QFrame):
    """Compact one-line game entry used by the expanded profile library."""

    clicked = pyqtSignal(dict)

    def __init__(self, game: dict[str, Any], parent=None):
        super().__init__(parent)
        self.game = dict(game)
        self._pixmap = QPixmap()
        self.setObjectName("profileGameRow")
        self.setFixedHeight(76)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Open game profile")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 7, 12, 7)
        layout.setSpacing(12)

        self.artwork = QLabel("Steam hero")
        self.artwork.setObjectName("profileGameRowArtwork")
        self.artwork.setFixedSize(108, 60)
        self.artwork.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.artwork.setStyleSheet(f"background:{SURFACE}; color:{TEXT_MUTED}; border:none; font-size:10px;")
        self.artwork.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.artwork)

        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(3)
        self.name = QLabel(str(self.game.get("name", "Game")))
        self.name.setObjectName("profileGameRowName")
        self.name.setTextFormat(Qt.TextFormat.PlainText)
        self.name.setWordWrap(False)
        self.name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        info.addWidget(self.name)

        app_id = str(self.game.get("app_id", "") or "")
        self.app_id = QLabel(f"Steam AppID {app_id}" if app_id else "Steam game")
        self.app_id.setObjectName("profileGameRowAppId")
        self.app_id.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        info.addWidget(self.app_id)

        achievement = self.game.get("achievements") if isinstance(self.game.get("achievements"), dict) else {}
        unlocked = max(0, int(achievement.get("unlocked_count", 0) or 0))
        total = max(unlocked, int(achievement.get("total_count", 0) or 0))
        playtime = max(0, int(self.game.get("playtime_seconds", 0) or 0))
        self.meta = QLabel(
            f"{ProfileGameCard._format_hours(playtime)}  ·  "
            f"{unlocked}/{total} achievements" if total else
            f"{ProfileGameCard._format_hours(playtime)}  ·  No achievements"
        )
        self.meta.setObjectName("profileGameRowMeta")
        self.meta.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        info.addWidget(self.meta)
        layout.addLayout(info, 1)

        arrow = QLabel("›")
        arrow.setObjectName("profileGameRowArrow")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(arrow)

        for child in (self.artwork, self.name, self.app_id, self.meta, arrow):
            child.installEventFilter(self)

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
            self.artwork.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        ))

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


class ProfilePageWidget(QWidget):
    """Persistent profile page with an explicit owner/public mode boundary."""

    back_requested = pyqtSignal()
    open_profile_handle_requested = pyqtSignal(str)
    profile_changed = pyqtSignal()
    private_profile_changed = pyqtSignal()
    avatar_pixmap_changed = pyqtSignal(object)
    auth_progress = pyqtSignal(str)
    # Settings owns the visible account controls.  Keep the state and the
    # operations on the profile page so authentication/publish/resync cannot
    # drift into separate implementations.
    profile_action_state_changed = pyqtSignal(bool, bool, bool, bool)
    profile_action_status_changed = pyqtSignal(str, bool)

    def __init__(self, db, settings: QSettings | None = None, parent=None, worker_registry=None, auth_session=None, request_manager=None, resource_cache: ResourceCache | None = None):
        super().__init__(parent)
        self.db = db
        self.settings = settings or QSettings("SafeLauncher", "SafeLauncher")
        self.central_auth = auth_session or CentralAuthSession()
        self.profile_resources = ProfileResourceService(self.central_auth)
        self._profile_context_identity = ""
        self._tasks = TaskSupervisor(self, worker_registry=worker_registry)
        self.request_manager = request_manager
        self.resource_cache = resource_cache
        self._resource_bindings = ResourceBindingRegistry()
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
        self._resyncing_private = False
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
        self._social_action_buttons: list[QPushButton] = []
        self._pending_social_success_message = ""
        self._auth_in_flight = False
        self._game_cards: dict[str, list[ProfileGameCard]] = {}
        self._artwork_cache = PresentationCache(MAX_PROFILE_ARTWORK_CACHE_ITEMS)
        self._artwork_cache_stored_at: dict[str, float] = {}
        self._artwork_inflight: set[str] = set()
        self._profile_background_inflight: set[str] = set()
        self._profile_background_pixmap = QPixmap()
        self._profile_background_blurred = QPixmap()
        self._profile_background_blur_size = (0, 0)
        legacy_avatar_catalog = load_cached_avatar_catalog() or []
        self._avatar_catalog: list[dict[str, Any]] = []
        self._avatar_catalog_stale = False
        if self.resource_cache is not None:
            catalog_key = self._profile_resource_key("profile-avatar-catalog", "catalog")
            shared_catalog = self._profile_cache_entry(
                catalog_key,
                self._avatar_catalog_cache_value_is_valid,
            )
            if shared_catalog is not None:
                self._avatar_catalog = normalize_avatar_catalog(shared_catalog.value) or []
                self._avatar_catalog_stale = not shared_catalog.is_fresh(
                    cache_policy("profile-avatar-catalog").max_age_seconds
                )
                if self.resource_cache.directory is not None:
                    retire_cached_avatar_catalog()
            elif legacy_avatar_catalog:
                self._avatar_catalog = legacy_avatar_catalog
                self.resource_cache.put(
                    catalog_key,
                    legacy_avatar_catalog,
                    content_type=cache_policy("profile-avatar-catalog").content_type,
                )
                if self.resource_cache.directory is not None:
                    retire_cached_avatar_catalog()
        else:
            self._avatar_catalog = legacy_avatar_catalog
        self._avatar_catalog_loading = False
        self._avatar_dialog: ProfileAvatarCatalogDialog | None = None
        self._avatar_pixmaps = PresentationCache(MAX_PROFILE_AVATAR_CACHE_ITEMS)
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
            QFrame#profileSection {{ background: {panel_soft}; border: 1px solid {border}; border-radius: 16px; }}
            QFrame#profileGameCard {{ background: {game_card}; border: 1px solid {card_border}; border-radius: 12px; }}
            QFrame#profileGameCard:hover {{ background: {hover_surface}; border-color: {theme.accent}; }}
            QFrame#profileGameRow {{ background: {game_card}; border: 1px solid {card_border}; border-radius: 10px; }}
            QFrame#profileGameRow:hover {{ background: {hover_surface}; border-color: {theme.accent}; }}
            QFrame#profileSeeMoreCard {{ background: {see_more}; border: 1px dashed {border}; border-radius: 12px; }}
            QFrame#profileSeeMoreCard:hover {{ background: {hover_surface}; border-color: {theme.accent}; }}
            QListWidget#profileList {{ background: {list_surface}; border: none; }}
            QLineEdit#profileEditorInput, QComboBox#profileEditorInput, QPlainTextEdit#profileEditorInput {{ background: {editor_surface}; border-color: {border}; }}
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
            QPushButton#profileBannerEdit {{ background: transparent; border: none; border-radius: 6px; }}
            QPushButton#profileBannerEdit:hover {{ background: rgba(255, 255, 255, 0.10); }}
            QPushButton#profileSecondary {{
                background: {SURFACE_ELEVATED}; color: {TEXT_PRIMARY}; border: none;
                border-radius: 6px; padding: 0 12px; min-height: 32px; max-height: 32px;
                font-size: 12px; font-weight: 600;
            }}
            QPushButton#profileSecondary:hover {{ background: {BORDER}; }}
            QPushButton#profileSecondary:pressed {{ background: {ACCENT_PRIMARY}; }}
            QPushButton#profileSecondary:disabled {{ background: {SURFACE}; color: {TEXT_MUTED}; }}
            QPushButton#profilePrimary {{
                background: {ACCENT_PRIMARY}; color: #FFFFFF; border: none;
                border-radius: 6px; padding: 0 14px; min-height: 32px; max-height: 32px;
                font-size: 12px; font-weight: 600;
            }}
            QPushButton#profilePrimary:hover {{ background: {ACCENT_HOVER}; }}
            QPushButton#profilePrimary:pressed {{ background: {ACCENT_PRESSED}; }}
            QFrame#profileGameCard {{ background: {SURFACE_ELEVATED}; border: none; border-radius: 8px; }}
            QFrame#profileGameCard:hover {{ background: #252A34; }}
            QFrame#profileGameRow {{ background: {SURFACE_ELEVATED}; border: none; border-radius: 8px; }}
            QFrame#profileGameRow:hover {{ background: #252A34; }}
            QLabel#profileGameRowName {{ color: {TEXT_PRIMARY}; font-size: 13px; font-weight: 700; }}
            QLabel#profileGameRowAppId {{ color: {TEXT_MUTED}; font-size: 10px; }}
            QLabel#profileGameRowMeta {{ color: {TEXT_SECONDARY}; font-size: 11px; }}
            QLabel#profileGameRowArrow {{ color: {TEXT_MUTED}; font-size: 24px; font-weight: 400; min-width: 16px; }}
            QLineEdit#profileGamesSearch {{ background: {SURFACE_ELEVATED}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER}; border-radius: 6px; padding: 0 10px; min-height: 34px; max-height: 34px; }}
            QLineEdit#profileGamesSearch:focus {{ border-color: {ACCENT_PRIMARY}; }}
            QFrame#profileSeeMoreCard {{ background: {SURFACE}; border: 1px dashed {BORDER}; border-radius: 8px; }}
            QFrame#profileSeeMoreCard:hover {{ background: {SURFACE_ELEVATED}; border-color: {ACCENT_PRIMARY}; }}
            QFrame#profileHero {{ border: none; }}
            QLabel#profileEyebrow {{ color: {TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
            QLabel#profileName {{ color: {TEXT_PRIMARY}; font-size: 28px; font-weight: 700; }}
            QLabel#profileHandle {{ color: {TEXT_SECONDARY}; font-size: 12px; }}
            QLabel#profileBio {{ color: {TEXT_PRIMARY}; font-size: 13px; }}
            QLabel#profileMuted {{ color: {TEXT_SECONDARY}; font-size: 12px; }}
            QLabel#profileEditorLabel {{ color: {TEXT_SECONDARY}; font-size: 12px; font-weight: 600; }}
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
        self.handle_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.handle_label.setToolTip("Select and copy this username to share your profile")
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

        self.editor = QFrame()
        self.editor.setObjectName("profileSection")
        editor_layout = QVBoxLayout(self.editor)
        editor_layout.setContentsMargins(18, 16, 18, 16)
        editor_layout.setSpacing(12)

        def editor_label(text: str, *, top: bool = False) -> QLabel:
            label = QLabel(text)
            label.setObjectName("profileEditorLabel")
            label.setFixedWidth(112)
            label.setAlignment(
                Qt.AlignmentFlag.AlignRight
                | (Qt.AlignmentFlag.AlignTop if top else Qt.AlignmentFlag.AlignVCenter)
            )
            return label

        def style_editor_button(button: QPushButton, object_name: str, width: int) -> None:
            button.setObjectName(object_name)
            button.setFixedSize(width, 32)
            button.setCursor(Qt.CursorShape.PointingHandCursor)

        editor_title = QLabel("Edit profile")
        editor_title.setStyleSheet(f"color:{TEXT_PRIMARY}; font-size:14px; font-weight:700;")
        editor_layout.addWidget(editor_title)
        name_row = QHBoxLayout()
        name_row.setSpacing(12)
        name_row.addWidget(editor_label("Display name"))
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("profileEditorInput")
        self.name_edit.setFixedHeight(32)
        self.name_edit.setMaxLength(64)
        name_row.addWidget(self.name_edit, 1)
        editor_layout.addLayout(name_row)
        handle_row = QHBoxLayout()
        handle_row.setSpacing(12)
        handle_row.addWidget(editor_label("Username"))
        self.handle_edit = QLineEdit()
        self.handle_edit.setObjectName("profileEditorInput")
        self.handle_edit.setFixedHeight(32)
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
        bio_row.setSpacing(12)
        bio_row.addWidget(editor_label("Bio", top=True), 0, Qt.AlignmentFlag.AlignTop)
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
        avatar_row.setSpacing(8)
        avatar_row.addWidget(editor_label("Avatar"))
        self.btn_avatar = QPushButton("Choose profile picture")
        style_editor_button(self.btn_avatar, "profileSecondary", 174)
        self.btn_avatar.clicked.connect(self._choose_avatar)
        avatar_row.addWidget(self.btn_avatar)
        self.btn_remove_avatar = QPushButton("Use initials")
        style_editor_button(self.btn_remove_avatar, "profileSecondary", 112)
        self.btn_remove_avatar.clicked.connect(self._remove_avatar)
        avatar_row.addWidget(self.btn_remove_avatar)
        self.avatar_selection_label = QLabel("Cloud catalog")
        self.avatar_selection_label.setObjectName("profileMuted")
        avatar_row.addWidget(self.avatar_selection_label)
        avatar_row.addStretch()
        editor_layout.addLayout(avatar_row)
        background_row = QHBoxLayout()
        background_row.setSpacing(8)
        background_row.addWidget(editor_label("Background"))
        self.background_combo = QComboBox()
        self.background_combo.setObjectName("profileEditorInput")
        self.background_combo.setFixedHeight(32)
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
        style_editor_button(self.btn_custom_color, "profileSecondary", 78)
        self.btn_custom_color.clicked.connect(self._choose_color)
        background_row.addWidget(self.btn_custom_color)
        editor_layout.addLayout(background_row)

        panel_theme_row = QHBoxLayout()
        panel_theme_row.setSpacing(8)
        panel_theme_row.addWidget(editor_label("Panel visuals"))
        self.panel_theme_combo = QComboBox()
        self.panel_theme_combo.setObjectName("profileEditorInput")
        self.panel_theme_combo.setFixedHeight(32)
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
        hero_background_row.setSpacing(8)
        hero_background_row.addWidget(editor_label("Hero AppID"))
        self.background_app_id_edit = QLineEdit()
        self.background_app_id_edit.setObjectName("profileEditorInput")
        self.background_app_id_edit.setFixedHeight(32)
        self.background_app_id_edit.setMaxLength(16)
        self.background_app_id_edit.setPlaceholderText("e.g. 1321440")
        self.background_app_id_edit.setToolTip("Enter a Steam AppID to use its hero artwork as your profile background.")
        self.background_app_id_edit.returnPressed.connect(self._use_steam_hero_background)
        self.background_app_id_edit.editingFinished.connect(self._preview_steam_hero_from_app_id)
        hero_background_row.addWidget(self.background_app_id_edit, 1)
        self.btn_use_hero_background = QPushButton("Use hero")
        style_editor_button(self.btn_use_hero_background, "profileSecondary", 96)
        self.btn_use_hero_background.clicked.connect(self._use_steam_hero_background)
        hero_background_row.addWidget(self.btn_use_hero_background)
        editor_layout.addWidget(self.background_hero_controls)
        self.background_hero_controls.setVisible(False)
        self.editor_hint = QLabel(
            "Only the information shown on your public profile is shared. Private launcher data stays private. "
            "Public publishing is separate from private game-save cloud synchronization."
        )
        self.editor_hint.setObjectName("profileMuted")
        self.editor_hint.setWordWrap(True)
        editor_layout.addWidget(self.editor_hint)
        editor_profile_actions = QHBoxLayout()
        self.btn_editor_publish = QPushButton("Publish profile")
        style_editor_button(self.btn_editor_publish, "profileSecondary", 158)
        self.btn_editor_publish.setIcon(get_icon("ph.upload-simple-bold", color=TEXT_PRIMARY))
        self.btn_editor_publish.setIconSize(QSize(15, 15))
        self.btn_editor_publish.setToolTip("Publish this profile or remove it from the public service")
        self.btn_editor_publish.clicked.connect(self._publish_from_editor)
        editor_profile_actions.addWidget(self.btn_editor_publish)
        self.editor_profile_status = QLabel()
        self.editor_profile_status.setObjectName("profileMuted")
        self.editor_profile_status.setWordWrap(True)
        editor_profile_actions.addWidget(self.editor_profile_status, 1)
        editor_layout.addLayout(editor_profile_actions)
        editor_actions = QHBoxLayout()
        editor_actions.addStretch()
        self.btn_cancel_edit = QPushButton("Cancel")
        style_editor_button(self.btn_cancel_edit, "profileSecondary", 104)
        self.btn_cancel_edit.clicked.connect(self._cancel_edit)
        editor_actions.addWidget(self.btn_cancel_edit)
        self.btn_save_edit = QPushButton("Save changes")
        style_editor_button(self.btn_save_edit, "profilePrimary", 128)
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
        self.games_all_search = QLineEdit()
        self.games_all_search.setObjectName("profileGamesSearch")
        self.games_all_search.setPlaceholderText("Search games by name or Steam AppID…")
        self.games_all_search.setClearButtonEnabled(True)
        self.games_all_search.textChanged.connect(self._filter_all_games)
        all_layout.addWidget(self.games_all_search)
        self.games_all_rows = QVBoxLayout()
        self.games_all_rows.setContentsMargins(0, 0, 0, 0)
        self.games_all_rows.setSpacing(8)
        all_layout.addLayout(self.games_all_rows)
        self.games_all_empty = QLabel("No games match your search.")
        self.games_all_empty.setObjectName("profileMuted")
        self.games_all_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.games_all_empty.setVisible(False)
        all_layout.addWidget(self.games_all_empty)
        all_layout.addStretch()
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
        self.friend_handle_edit.setPlaceholderText("Enter a username, e.g. @player")
        self.friend_handle_edit.setToolTip("Enter the username shown on another SafeLauncher profile")
        self.friend_handle_edit.setMaxLength(40)
        self.friend_handle_edit.returnPressed.connect(self._send_friend_request)
        self.friend_controls.addWidget(self.friend_handle_edit, 1)
        self.btn_add_friend = QPushButton("Add friend")
        self.btn_add_friend.setToolTip("Send a friend request to this username")
        self.btn_add_friend.clicked.connect(self._send_friend_request)
        self.friend_controls.addWidget(self.btn_add_friend)
        self.btn_refresh_friends = QPushButton("Refresh")
        self.btn_refresh_friends.setIcon(get_icon("ph.arrows-clockwise-bold", color=TEXT_SECONDARY))
        self.btn_refresh_friends.clicked.connect(self._refresh_social)
        self.friend_controls.addWidget(self.btn_refresh_friends)
        friends_layout.addLayout(self.friend_controls)
        self.btn_public_add_friend = QPushButton("Add friend")
        self.btn_public_add_friend.setToolTip("Send a friend request to this profile")
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
        self.friends_title = QLabel("Your friends (0)")
        self.friends_title.setObjectName("profileMuted")
        friends_layout.addWidget(self.friends_title)
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
        self._pending_social_success_message = ""
        self._local_refresh_timer.stop()
        self._local_refresh_pending = False
        self.mode_label.setText("OWNER VIEW")
        self._profile_settings = load_profile_settings(self.settings, fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"))
        self._rotate_profile_context(
            f"owner:{self._profile_settings.get('public_handle', '')}:{int(self.central_auth.signed_in)}"
        )
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
        self._pending_social_success_message = ""
        self._local_refresh_timer.stop()
        self._local_refresh_pending = False
        self._editing = False
        self._document = normalized
        self._rotate_profile_context(f"public:{normalized.get('handle', '')}")
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

    @staticmethod
    def _social_cache_value_is_valid(value: Any) -> bool:
        if isinstance(value, dict) and "social" in value:
            value = value.get("social")
        return normalize_social_snapshot(value) is not None

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

    def profile_action_state(self) -> tuple[bool, bool, bool, bool]:
        """Return the account-management state consumed by Settings.

        Account actions are available independently of whether the profile
        page is currently displaying the owner or somebody else's public
        profile.  The local settings are deliberately reloaded in public
        mode, because ``_profile_settings`` then represents the displayed
        document rather than the signed-in owner's local profile.
        """
        local = self._profile_settings
        if self._mode != "owner":
            local = load_profile_settings(
                self.settings,
                fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
            )
        return (
            True,
            bool(self.central_auth.signed_in),
            bool(local.get("published")),
            bool(self._auth_in_flight or self._publishing or self._resyncing_private),
        )

    def _emit_profile_action_state(self) -> None:
        self._update_editor_publish_control(
            self._mode == "owner",
            self.central_auth.signed_in,
            bool(self._profile_settings.get("published")),
        )
        self.profile_action_state_changed.emit(*self.profile_action_state())

    def _set_profile_action_status(self, message: str, error: bool = False) -> None:
        """Publish action feedback to Settings and retain it in owner footer."""
        if self._mode == "owner":
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};" if error else "")
            self.footer_status.setText(str(message or ""))
        self.profile_action_status_changed.emit(str(message or ""), bool(error))

    def toggle_profile_auth(self) -> None:
        """Sign in or out from the Settings account controls."""
        if self.central_auth.signed_in:
            self._sign_out()
        else:
            self._sign_in()

    def publish_profile(self) -> None:
        """Publish or unpublish the owner's public profile."""
        self._publish()

    def _publish_from_editor(self) -> None:
        """Commit editor changes before publishing the public projection."""
        if self._editing and not bool(self._profile_settings.get("published")):
            self._save_edit()
            if self._editing:
                return
        self.publish_profile()

    def resync_profile(self) -> None:
        """Force a private profile metadata resynchronization."""
        self._resync_private()

    def _set_admin_controls(self, enabled: bool) -> None:
        signed_in = self.central_auth.signed_in
        published = bool(self._profile_settings.get("published"))
        self.btn_banner_edit.setVisible(enabled and not self._editing)
        self._update_editor_publish_control(enabled, signed_in, published)
        self._update_social_controls(enabled, published, signed_in)
        self._emit_profile_action_state()

    def _update_editor_publish_control(self, owner_enabled: bool, signed_in: bool, published: bool) -> None:
        """Keep the editor's public-visibility action in sync with account state."""
        if not hasattr(self, "btn_editor_publish"):
            return
        busy = bool(self._auth_in_flight or self._publishing or self._resyncing_private)
        self.btn_editor_publish.setVisible(owner_enabled and self._editing)
        self.btn_editor_publish.setEnabled(owner_enabled and not busy)
        self.btn_editor_publish.setText("Unpublish profile" if published else "Publish profile")
        self.btn_editor_publish.setIcon(get_icon(
            "ph.eye-slash-bold" if published else "ph.upload-simple-bold",
            color=TEXT_PRIMARY,
        ))
        if not signed_in:
            self.editor_profile_status.setText("Sign in is required before publishing.")
        elif published:
            self.editor_profile_status.setText("Public profile is live. Game-save cloud sync is separate.")
        else:
            self.editor_profile_status.setText("This profile is private until you publish it.")

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

    def _profile_resource_key(self, resource: str, identity: str, variant: str = "") -> RequestKey:
        """Build a profile resource key through the shared service boundary."""
        return self.profile_resources.request_key(resource, identity, variant)

    def _profile_artwork_key(self, url: str) -> RequestKey:
        identity = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:24]
        return self._profile_resource_key("profile-artwork", identity, "image")

    def _profile_background_key(self, app_id: str) -> RequestKey:
        return self._profile_resource_key("profile-background", app_id, "steam-hero")

    def _profile_cache_entry(self, key: RequestKey, validator):
        """Read a profile entry through the same validation boundary as refreshes."""
        if self.resource_cache is None:
            return None
        entry = self.resource_cache.get(key)
        if entry is None:
            return None
        try:
            valid = bool(validator(entry.value))
        except Exception:
            valid = False
        if not valid:
            self.resource_cache.invalidate(key)
            return None
        return entry

    def _load_profile_background_bytes(self, app_id: str) -> bytes:
        app_id = steam_app_id(app_id)
        if not app_id:
            return b""
        if self.resource_cache is not None:
            entry = self._profile_cache_entry(
                self._profile_background_key(app_id),
                self._profile_background_cache_value_is_valid,
            )
            if entry is not None:
                if not entry.is_fresh(cache_policy("profile-background").max_age_seconds):
                    # Keep rendering usable stale artwork, but let the manager
                    # own the refresh and deduplication decision.
                    self._queue_profile_background(app_id)
                if self.resource_cache.directory is not None:
                    try:
                        os.unlink(self._profile_background_cache_path(app_id))
                    except OSError:
                        pass
                return entry.value
        path = self._profile_background_cache_path(app_id)
        if not path:
            return b""
        try:
            with open(path, "rb") as stream:
                data = stream.read(MAX_PROFILE_BACKGROUND_BYTES + 1)
            if self._profile_background_cache_value_is_valid(data):
                if self.resource_cache is not None:
                    self.resource_cache.put(
                        self._profile_background_key(app_id),
                        data,
                        content_type=cache_policy("profile-background").content_type,
                    )
                    if self.resource_cache.directory is not None:
                        try:
                            os.unlink(path)
                        except OSError:
                            pass
                return data
        except OSError:
            pass
        return b""

    @staticmethod
    def _profile_background_cache_value_is_valid(value: Any) -> bool:
        if not isinstance(value, bytes) or not 0 < len(value) <= MAX_PROFILE_BACKGROUND_BYTES:
            return False
        return ProfilePageWidget._decode_profile_background(value) is not None

    @staticmethod
    def _profile_artwork_cache_value_is_valid(value: Any) -> bool:
        if not isinstance(value, bytes) or not 0 < len(value) <= 4 * 1024 * 1024:
            return False
        pixmap = QPixmap()
        return pixmap.loadFromData(value) and pixmap.width() > 0 and pixmap.height() > 0

    def _presentation_artwork(self, url: str) -> bytes | None:
        """Return decoded presentation artwork only while its remote entry is fresh."""
        data = self._artwork_cache.get(url)
        stored_at = self._artwork_cache_stored_at.get(url)
        if data is not None and stored_at is not None:
            if time.time() - stored_at <= cache_policy("profile-artwork").max_age_seconds:
                return data
        if data is not None:
            self._artwork_cache.pop(url, None)
        self._artwork_cache_stored_at.pop(url, None)
        return None

    def _remember_presentation_artwork(
        self,
        url: str,
        data: bytes,
        *,
        stored_at: float | None = None,
    ) -> None:
        self._artwork_cache[url] = data
        self._artwork_cache_stored_at[url] = float(time.time() if stored_at is None else stored_at)
        for cached_url in tuple(self._artwork_cache_stored_at):
            if cached_url not in self._artwork_cache:
                self._artwork_cache_stored_at.pop(cached_url, None)

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

        if self.request_manager is not None:
            key = self._profile_background_key(app_id)
            if self.resource_cache is None and self.request_manager.state(key).status in {
                ResourceStatus.LOADING,
                ResourceStatus.READY,
                ResourceStatus.STALE,
            }:
                return
            self._profile_background_inflight.add(app_id)
            spec = self.profile_resources.request_spec(
                key,
                lambda token, app_id=app_id: self._download_steam_artwork(app_id, token, (2, 6)),
                priority=RequestPriority.NORMAL,
                timeout_seconds=15,
                tag="profile-background",
            )
            handle = self.request_manager.cached_request(
                spec,
                self.resource_cache,
                max_age_seconds=cache_policy("profile-background").max_age_seconds,
                cache_validator=self._profile_background_cache_value_is_valid,
                content_type=cache_policy("profile-background").content_type,
            ) if self.resource_cache is not None else self.request_manager.request(
                key,
                spec.loader,
                priority=spec.priority,
                timeout_seconds=spec.timeout_seconds,
            )
            self._bind_resource(
                key,
                lambda result, app_id=app_id, key=key: self._on_profile_background_state(app_id, result, key),
            )
            return

        self._profile_background_inflight.add(app_id)

        def work():
            return self.profile_resources.download_steam_artwork(app_id, timeout=(2, 6))

        worker = self._tasks.start(
            "SafeLauncher-ProfileBackground",
            work,
            lambda result, expected_app_id=app_id: self._profile_background_loaded(expected_app_id, result),
        )
        worker.error_occurred.connect(
            lambda _error, expected_app_id=app_id: self._profile_background_failed(expected_app_id)
        )

    def _download_steam_artwork(self, app_id: str, token, timeout: tuple[int, int]) -> bytes:
        return self.profile_resources.download_steam_artwork(app_id, token, timeout)

    def _on_profile_background_state(self, app_id: str, result: Any, key: RequestKey | None = None) -> None:
        if result.status in {ResourceStatus.READY, ResourceStatus.STALE} and result.value:
            self._profile_background_loaded(
                str(app_id),
                result.value,
                persist_legacy_cache=False,
                persist_shared_cache=result.status == ResourceStatus.READY,
            )
            if result.status == ResourceStatus.STALE and result.error is not None and key is not None:
                self._finish_profile_resource(key)
        elif result.status in {
            ResourceStatus.ERROR,
            ResourceStatus.OFFLINE,
            ResourceStatus.UNAVAILABLE,
            ResourceStatus.AUTHENTICATION_REQUIRED,
            ResourceStatus.PERMISSION_DENIED,
            ResourceStatus.CONFLICT,
            ResourceStatus.CANCELLED,
        }:
            self._profile_background_failed(str(app_id))

    def _profile_background_failed(self, app_id: str) -> None:
        self._profile_background_inflight.discard(app_id)

    def _profile_background_loaded(
        self,
        app_id: str,
        data: Any,
        *,
        persist_legacy_cache: bool = True,
        persist_shared_cache: bool = True,
    ) -> None:
        self._profile_background_inflight.discard(app_id)
        if not isinstance(data, bytes) or not data:
            return
        pixmap = self._decode_profile_background(data)
        if pixmap is None:
            return
        if persist_shared_cache and self.resource_cache is not None:
            self.resource_cache.put(
                self._profile_background_key(app_id),
                data,
                content_type="image/jpeg",
            )
        if persist_legacy_cache and self.resource_cache is None:
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
                color = stops[0] if isinstance(stops, list) and stops else "#202024"
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
            self.footer_status.clear()
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
    def _clear_grid(layout) -> None:
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
        self._clear_grid(self.games_all_rows)
        self._game_cards = {}
        self._game_rows = []
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
        presentation = self._presentation_artwork(url)
        if presentation is not None:
            card.set_artwork_bytes(presentation)
        elif self.resource_cache is not None:
            cached = self._profile_cache_entry(
                self._profile_artwork_key(url),
                self._profile_artwork_cache_value_is_valid,
            )
            if cached is not None:
                self._remember_presentation_artwork(
                    url,
                    cached.value,
                    stored_at=cached.stored_at,
                )
                card.set_artwork_bytes(cached.value)
                if (
                    not cached.is_fresh(cache_policy("profile-artwork").max_age_seconds)
                    and pending is not None
                    and url not in self._artwork_inflight
                ):
                    pending[app_id] = url
            elif pending is not None and url not in self._artwork_inflight:
                pending[app_id] = url
        elif pending is not None and url not in self._artwork_inflight:
            pending[app_id] = url

    def _show_all_games(self) -> None:
        """Open the complete library and lazily fetch its remaining artwork."""
        self.games_stack.setCurrentIndex(1)
        if self._all_games_populated:
            return
        self._all_games_populated = True
        self._clear_grid(self.games_all_rows)
        self._game_rows = []
        pending: dict[str, str] = {}
        for game in self._profile_games:
            self._add_game_row(game, pending)
        self._filter_all_games(self.games_all_search.text())
        self._queue_artwork_download(pending)

    def _add_game_row(self, game: dict[str, Any], pending: dict[str, str] | None = None) -> None:
        app_id = str(game.get("app_id", ""))
        row = ProfileGameRow(game)
        row.clicked.connect(self._open_game_detail)
        self._game_rows.append(row)
        self._game_cards.setdefault(app_id, []).append(row)
        self.games_all_rows.addWidget(row)
        url = str(game.get("artwork_url", "") or "")
        if not url:
            return
        presentation = self._presentation_artwork(url)
        if presentation is not None:
            row.set_artwork_bytes(presentation)
        elif self.resource_cache is not None:
            cached = self._profile_cache_entry(self._profile_artwork_key(url), self._profile_artwork_cache_value_is_valid)
            if cached is not None:
                self._remember_presentation_artwork(url, cached.value, stored_at=cached.stored_at)
                row.set_artwork_bytes(cached.value)
                if not cached.is_fresh(cache_policy("profile-artwork").max_age_seconds) and pending is not None and url not in self._artwork_inflight:
                    pending[app_id] = url
            elif pending is not None and url not in self._artwork_inflight:
                pending[app_id] = url
        elif pending is not None and url not in self._artwork_inflight:
            pending[app_id] = url

    def _filter_all_games(self, text: str) -> None:
        needle = str(text or "").strip().casefold()
        visible = 0
        for row in getattr(self, "_game_rows", []):
            game = row.game
            searchable = f"{game.get('name', '')} {game.get('app_id', '')}".casefold()
            matches = not needle or needle in searchable
            row.setVisible(matches)
            visible += int(matches)
        if hasattr(self, "games_all_empty"):
            self.games_all_empty.setVisible(bool(getattr(self, "_game_rows", [])) and visible == 0)

    def _queue_artwork_download(self, pending: dict[str, str]) -> None:
        if not pending or not automatic_network_allowed(self.settings):
            return

        self._artwork_inflight.update(pending.values())

        if self.request_manager is not None:
            for app_id, url in pending.items():
                key = self._profile_artwork_key(url)
                if self.resource_cache is None:
                    state = self.request_manager.state(key)
                    if state.status in {ResourceStatus.LOADING, ResourceStatus.READY, ResourceStatus.STALE}:
                        continue
                spec = self.profile_resources.request_spec(
                    key,
                    lambda token, app_id=app_id: self._download_steam_artwork(app_id, token, (2, 5)),
                    priority=RequestPriority.BACKGROUND,
                    timeout_seconds=15,
                    tag="profile-artwork",
                )
                handle = self.request_manager.cached_request(
                    spec,
                    self.resource_cache,
                    max_age_seconds=cache_policy("profile-artwork").max_age_seconds,
                    cache_validator=self._profile_artwork_cache_value_is_valid,
                    content_type=cache_policy("profile-artwork").content_type,
                ) if self.resource_cache is not None else self.request_manager.request(
                    key,
                    spec.loader,
                    priority=spec.priority,
                    timeout_seconds=spec.timeout_seconds,
                )
                self._bind_resource(
                    key,
                    lambda result, app_id=app_id, url=url, key=key: self._on_profile_artwork_state(
                        app_id, url, result, key
                    ),
                )
            return

        def work():
            downloaded = {}
            for app_id, url in pending.items():
                data = self.profile_resources.download_steam_artwork(app_id, timeout=(2, 5))
                if data:
                    # Keep the canonical URL as the cache key even when a
                    # fallback endpoint supplied the bytes. That way all
                    # cards for this AppID can use the same memory entry.
                    downloaded[app_id] = (url, data)
            return downloaded

        worker = self._tasks.start(
            "SafeLauncher-ProfileArtwork",
            work,
            lambda result, urls=set(pending.values()): self._artwork_loaded(result, urls),
        )
        worker.error_occurred.connect(
            lambda _error, urls=set(pending.values()): self._artwork_request_failed(urls)
        )

    def _on_profile_artwork_state(
        self,
        app_id: str,
        url: str,
        result: Any,
        key: RequestKey | None = None,
    ) -> None:
        if result.status in {ResourceStatus.READY, ResourceStatus.STALE} and result.value:
            self._artwork_loaded_with_options(
                {str(app_id): (str(url), result.value)},
                {str(url)},
                persist_cache=result.status == ResourceStatus.READY,
                stored_at=result.updated_at,
            )
            if result.status == ResourceStatus.STALE and result.error is not None and key is not None:
                self._finish_profile_resource(key)
        elif result.status in {
            ResourceStatus.ERROR,
            ResourceStatus.OFFLINE,
            ResourceStatus.UNAVAILABLE,
            ResourceStatus.AUTHENTICATION_REQUIRED,
            ResourceStatus.PERMISSION_DENIED,
            ResourceStatus.CONFLICT,
            ResourceStatus.CANCELLED,
        }:
            self._artwork_request_failed({str(url)})

    def _artwork_request_failed(self, urls: set[str]) -> None:
        self._artwork_inflight.difference_update(urls)

    def _bind_resource(self, key: RequestKey, callback) -> ResourceBinding:
        """Subscribe one page-scoped resource to a GUI-thread callback."""
        previous = self._resource_bindings.pop(key, None)
        if previous is not None:
            previous.close()
            previous.deleteLater()
        binding = bind_resource(
            self.request_manager,
            key,
            callback,
            self,
            cancel_on_close=True,
        )
        self._resource_bindings[key] = binding
        return binding

    def _close_resource_bindings(self) -> None:
        for binding in self._resource_bindings.take_all():
            binding.close()
            binding.deleteLater()

    def _artwork_loaded(self, result: Any, urls: set[str]) -> None:
        self._artwork_loaded_with_options(result, urls, persist_cache=True)

    def _artwork_loaded_with_options(
        self,
        result: Any,
        urls: set[str],
        *,
        persist_cache: bool,
        stored_at: float | None = None,
    ) -> None:
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
            self._remember_presentation_artwork(url, data, stored_at=stored_at)
            if persist_cache and self.resource_cache is not None:
                self.resource_cache.put(self._profile_artwork_key(url), data, content_type="image/jpeg")
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
        self.friends_title.setVisible(owner_enabled)
        self.friend_handle_edit.setEnabled(owner_ready and not self._social_loading and not self._social_mutating)
        self.btn_add_friend.setEnabled(owner_ready and not self._social_loading and not self._social_mutating)
        self.btn_refresh_friends.setEnabled(owner_ready and not self._social_loading and not self._social_mutating)
        self.btn_public_add_friend.setEnabled(public_ready and not self._social_mutating)
        for button in self._social_action_buttons:
            button.setEnabled(owner_ready and not self._social_loading and not self._social_mutating)

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
            button.setObjectName("profileSocialAction")
            button.setToolTip({
                "View profile": "Open this profile",
                "Remove": "Remove this person from your friends",
                "Block": "Block this profile and remove the relationship",
                "Accept": "Accept the friend request",
                "Decline": "Decline the friend request",
                "Cancel": "Cancel the friend request",
                "Unblock": "Allow this profile to contact you again",
            }.get(caption, caption))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(callback)
            self._social_action_buttons.append(button)
            layout.addWidget(button)
        return row

    def _render_social(self) -> None:
        self._social_action_buttons.clear()
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
            self.friends_hint.setText("Your friend list is private. Share your username or enter someone else’s username to send a request.")

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
            unblock.setObjectName("profileSocialAction")
            unblock.setToolTip("Allow this profile to contact you again")
            unblock.setCursor(Qt.CursorShape.PointingHandCursor)
            unblock.clicked.connect(lambda checked=False, h=blocked_handle: self._unblock_profile(h))
            self._social_action_buttons.append(unblock)
            row_layout.addWidget(unblock)
            self.blocked_layout.addWidget(row)

        self.incoming_title.setText(f"Incoming requests ({len(incoming) if isinstance(incoming, list) else 0})")
        self.outgoing_title.setText(f"Outgoing requests ({len(outgoing) if isinstance(outgoing, list) else 0})")
        self.blocked_title.setText(f"Blocked profiles ({len(blocked) if isinstance(blocked, list) else 0})")
        count = len(friends) if isinstance(friends, list) else 0
        self.friends_title.setText(f"Your friends ({count})")
        if not friends:
            empty = QLabel("No friends yet. Add someone using their @username above.")
            empty.setObjectName("profileMuted")
            empty.setWordWrap(True)
            self.friends_list.addWidget(empty)
        self.friends_status.setText(f"{count} friend{'s' if count != 1 else ''}")
        self._update_social_controls(True, bool(self._profile_settings.get("published")), self.central_auth.signed_in)

    def _start_managed_remote(
        self,
        key: RequestKey,
        loader,
        on_ready,
        on_error,
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
        timeout_seconds: float | None = 20,
        cache_policy_name: str | None = None,
        cache_validator=None,
    ):
        """Run a profile-service operation through the shared manager.

        RequestManager callbacks execute on request threads. This signal keeps
        all profile mutations and widget updates on the Qt GUI thread.
        """
        if self.request_manager is None:
            return None
        spec = self.profile_resources.request_spec(
            key,
            lambda token: (token.raise_if_cancelled(), loader())[1],
            priority=priority,
            timeout_seconds=timeout_seconds,
            tag="profile-operation",
        )
        if self.resource_cache is not None and cache_policy_name:
            policy = cache_policy(cache_policy_name)
            handle = self.request_manager.cached_request(
                spec,
                self.resource_cache,
                max_age_seconds=policy.max_age_seconds,
                stale_while_revalidate=policy.stale_while_revalidate,
                cache_validator=cache_validator,
                content_type=policy.content_type,
            )
        else:
            handle = self.request_manager.submit(spec)
        previous = self._resource_bindings.pop(key, None)
        if previous is not None:
            previous.close()

        def _deliver(result):
            if result.status in {ResourceStatus.IDLE, ResourceStatus.LOADING}:
                return
            if result.status == ResourceStatus.STALE:
                binding = self._resource_bindings.pop(key, None)
                if binding is not None:
                    binding.close()
                if result.value is not None:
                    on_ready(result.value)
                return
            binding = self._resource_bindings.pop(key, None)
            if binding is not None:
                binding.close()
            if result.status == ResourceStatus.READY:
                on_ready(result.value)
                return
            if result.status == ResourceStatus.CANCELLED:
                return
            if result.status == ResourceStatus.OFFLINE:
                on_error(ProfileServiceError("Offline mode is enabled.", "offline"))
                return
            category = result.error_category
            on_error(ProfileServiceError(
                str(result.error or "Remote request failed."),
                category,
            ))

        self._resource_bindings[key] = bind_request(
            self.request_manager,
            handle,
            _deliver,
            self,
            cancel_on_close=True,
        )
        return handle

    def _rotate_profile_context(self, identity: str) -> None:
        """Cancel page-scoped work when the viewed account/profile changes."""
        identity = str(identity or "").strip()
        if identity == self._profile_context_identity:
            return
        self._profile_context_identity = identity
        self.profile_resources.invalidate_context()
        self._close_resource_bindings()
        self._artwork_inflight.clear()
        self._profile_background_inflight.clear()
        self._avatar_inflight.clear()

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
        if not handle or not self.central_auth.signed_in:
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
            return self.profile_resources.get_owner_social(service_url)
        # A false local flag may be stale after private-cloud reconciliation;
        # force the public service to confirm the owner before using cache.
        managed = self._start_managed_remote(
            self._profile_resource_key("profile-social", handle),
            _fetch_social,
            lambda result, expected_handle=handle: self._social_refresh_done(result, expected_handle),
            lambda error, expected_handle=handle: self._social_refresh_done(
                ProfileServiceError(str(error), "social_refresh_failed"), expected_handle
            ),
            cache_policy_name="profile-social",
            cache_validator=self._social_cache_value_is_valid,
        ) if bool(local_settings.get("published")) else None
        if managed is not None:
            return
        worker = self._tasks.start(
            "SafeLauncher-RefreshFriends", _fetch_social,
            lambda result, expected_handle=handle: self._social_refresh_done(result, expected_handle),
        )
        worker.error_occurred.connect(lambda error, expected_handle=handle: self._social_refresh_done(
            ProfileServiceError(error, "social_refresh_failed"), expected_handle
        ))

    def _social_refresh_done(self, result: Any, expected_handle: str = "") -> None:
        self._social_loading = False
        if self._mode != "owner":
            return
        handle, _, _ = self._local_owner_identity()
        owner_handle = (
            str(result.get("profile", {}).get("handle", "") or "").strip().lower()
            if isinstance(result, dict) and isinstance(result.get("profile"), dict)
            else ""
        )
        if expected_handle and not owner_handle and (handle != expected_handle or self._social_handle != expected_handle):
            return
        pending = self._pending_social_success_message
        self._pending_social_success_message = ""
        if isinstance(result, Exception):
            self.friends_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            message = (
                "Your public profile is not available for this account. Publish it again before using Friends."
                if getattr(result, "code", "") == "profile_required" else
                f"The list could not be refreshed: {result}"
            )
            self.friends_status.setText(f"{pending} {message}" if pending else f"Friends could not be refreshed: {message}")
            self._update_social_controls(True, bool(self._profile_settings.get("published")), self.central_auth.signed_in)
            return
        owner = result.get("profile") if isinstance(result, dict) else None
        social = result.get("social") if isinstance(result, dict) and "social" in result else result
        if isinstance(owner, dict):
            owner_handle = str(owner.get("handle", "") or "").strip().lower()
            if owner_handle:
                local_settings = load_profile_settings(
                    self.settings,
                    fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
                )
                self._profile_settings = save_profile_settings(
                    self.settings,
                    {**local_settings, "public_handle": owner_handle, "published": True},
                    mark_changed=False,
                )
                self._social_handle = owner_handle
        snapshot = normalize_social_snapshot(social)
        if snapshot is None:
            self.friends_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.friends_status.setText(
                f"{pending} The list could not be refreshed because the service response was invalid."
                if pending else "Friends could not be refreshed because the service response was invalid."
            )
            return
        self._social_snapshot = snapshot
        self._social_handle = owner_handle or handle
        self._update_social_controls(True, bool(self._profile_settings.get("published")), self.central_auth.signed_in)
        self.friends_status.setStyleSheet("")
        self._render_social()
        if pending:
            self.friends_status.setStyleSheet(f"color:{SEMANTIC_SUCCESS};")
            self.friends_status.setText(pending)

    def _start_social_mutation(self, operation, success_message: str) -> None:
        if self._social_mutating or self._social_loading:
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
            return operation(self.profile_resources, owner_handle, service_url)
        mutation_key = self._profile_resource_key(
            "profile-social-mutation",
            f"{owner_handle}:{self._social_handle}:{success_message}",
        )
        if self._start_managed_remote(
            mutation_key,
            _run_mutation,
            lambda result: self._social_mutation_done(result, success_message),
            lambda error: self._social_mutation_done(
                ProfileServiceError(str(error), "social_operation_failed"), success_message
            ),
        ) is not None:
            return
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
            self.friends_status.setText("Enter a valid SafeLauncher username.")
            return
        owner_handle, _, _ = self._local_owner_identity()
        if target_handle == owner_handle:
            self.friends_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.friends_status.setText("You cannot send a friend request to yourself.")
            return
        self._start_social_mutation(
            lambda service, handle, url: service.social_operation(
                "send_friend_request", handle, target_handle=target_handle, service_url=url
            ),
            "Friend request sent.",
        )

    def _respond_to_request(self, request_id: str, action: str) -> None:
        if not request_id:
            return
        self._start_social_mutation(
            lambda service, handle, url: service.social_operation(
                "respond_friend_request", handle, request_id=request_id, action=action, service_url=url
            ),
            {"accept": "Friend request accepted.", "decline": "Friend request declined.", "cancel": "Friend request canceled."}.get(action, "Friend request updated."),
        )

    def _remove_friend(self, friend_handle: str) -> None:
        if QMessageBox.question(
            self,
            "Remove friend",
            f"Remove @{friend_handle} from your friends?\n\nThis ends the friendship for both of you. You can send a new request later.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._start_social_mutation(
            lambda service, handle, url: service.social_operation(
                "remove_friend", handle, target_handle=friend_handle, service_url=url
            ),
            "Friend removed.",
        )

    def _block_profile(self, friend_handle: str) -> None:
        if QMessageBox.question(
            self,
            "Block profile",
            f"Block @{friend_handle}?\n\nThis also removes any friendship or pending request.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._start_social_mutation(
            lambda service, handle, url: service.social_operation(
                "block_user", handle, target_handle=friend_handle, service_url=url
            ),
            "Profile blocked.",
        )

    def _unblock_profile(self, blocked_handle: str) -> None:
        self._start_social_mutation(
            lambda service, handle, url: service.social_operation(
                "unblock_user", handle, target_handle=blocked_handle, service_url=url
            ),
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
        if success_message == "Friend request sent.":
            self.friend_handle_edit.clear()
        if self._mode == "owner":
            owner_handle, _, _ = self._local_owner_identity()
            if self.request_manager is not None and owner_handle:
                self.request_manager.invalidate(
                    self._profile_resource_key(
                        "profile-social",
                        owner_handle,
                    )
                )
            self._pending_social_success_message = success_message
            self._refresh_social()
        else:
            self.btn_public_add_friend.setEnabled(False)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._mode == "owner":
            self._refresh_social()

    def hideEvent(self, event) -> None:
        """Cancel profile-only remote work when navigation leaves this page."""
        self._close_resource_bindings()
        self._artwork_inflight.clear()
        self._profile_background_inflight.clear()
        super().hideEvent(event)

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

    def _avatar_cache_value_is_valid(self, value: Any, expected_hash: str) -> bool:
        """Reject corrupt or catalog-mismatched disk entries before rendering."""
        if not is_valid_avatar_bytes(value, expected_hash):
            return False
        return self._decode_avatar(value) is not None

    @staticmethod
    def _avatar_catalog_cache_value_is_valid(value: Any) -> bool:
        """Reject malformed persisted catalogs before opening the picker."""
        normalized = normalize_avatar_catalog(value)
        return bool(normalized)

    def _avatar_reference(self, value: Any) -> str:
        """Resolve legacy local/profile aliases to the canonical asset number."""
        normalized = normalize_avatar_id(value)
        if not normalized:
            return ""
        item = self._avatar_catalog_item(normalized)
        asset_id = normalize_avatar_asset_id(item.get("asset_id")) if item else None
        return str(asset_id) if asset_id is not None else normalized

    def current_avatar_pixmap(self) -> QPixmap:
        """Return the currently rendered owner avatar for shared UI surfaces."""
        pixmap = self.avatar.pixmap()
        return QPixmap(pixmap) if pixmap is not None and not pixmap.isNull() else QPixmap()

    def _emit_owner_avatar(self, pixmap: QPixmap | None = None) -> None:
        """Publish only the saved owner avatar, never a public-view preview."""
        if self._mode != "owner" or self._editing:
            return
        self.avatar_pixmap_changed.emit(
            QPixmap(pixmap) if pixmap is not None and not pixmap.isNull() else QPixmap()
        )

    def _set_avatar(self, value: Any) -> None:
        avatar_id = self._avatar_reference(value)
        pixmap = self._avatar_pixmaps.get(avatar_id) if avatar_id else None
        if pixmap is not None and not pixmap.isNull():
            self.avatar.setPixmap(pixmap.scaled(128, 128, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
            self.avatar.setStyleSheet("border-radius:64px; background:#202024;")
            self._cache_avatar_pixmap(avatar_id, pixmap)
            self._emit_owner_avatar(pixmap)
            return
        self.avatar.clear()
        self.avatar.setText("SL")
        self.avatar.setStyleSheet(f"border-radius:64px; background:{SURFACE_ELEVATED}; color:{ACCENT_PRIMARY}; font-size:30px; font-weight:800;")
        self._emit_owner_avatar()
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

        # Production path: each avatar is a cacheable resource.  The old
        # batch worker below remains for manager-less embedders, but bytes no
        # longer need a second feature-specific disk cache when the shared
        # ResourceCache is available.
        if self.request_manager is not None and self.resource_cache is not None:
            for avatar_id in requested:
                item = self._avatar_catalog_item(avatar_id) or {}
                expected_hash = str(item.get("sha256", "") or "")
                key = self._profile_resource_key(
                    "profile-avatar", avatar_id, expected_hash
                )
                cache_validator = lambda value, expected_hash=expected_hash: self._avatar_cache_value_is_valid(
                    value, expected_hash
                )
                cached = self.resource_cache.get(key)
                if cached is None and expected_hash:
                    legacy_data = read_cached_avatar(avatar_id, expected_hash)
                    if legacy_data:
                        self.resource_cache.put(
                            key,
                            legacy_data,
                            content_type=cache_policy("profile-avatar").content_type,
                        )
                        if self.resource_cache.directory is not None:
                            retire_cached_avatar(avatar_id, expected_hash)
                        cached = self.resource_cache.get(key)
                elif cached is not None and expected_hash and self.resource_cache.directory is not None:
                    retire_cached_avatar(avatar_id, expected_hash)
                had_cache = cached is not None and cache_validator(cached.value)
                spec = self.profile_resources.request_spec(
                    key,
                    lambda token, avatar_id=avatar_id, expected_hash=expected_hash: self._load_avatar_resource(
                        avatar_id, service_url, token, expected_hash
                    ),
                    priority=RequestPriority.NORMAL,
                    timeout_seconds=30,
                    tag="profile-avatar",
                )
                handle = self.request_manager.cached_request(
                    spec,
                    self.resource_cache,
                    max_age_seconds=cache_policy("profile-avatar").max_age_seconds,
                    cache_validator=cache_validator,
                    content_type=cache_policy("profile-avatar").content_type,
                )
                self._bind_resource(
                    key,
                    lambda result, avatar_id=avatar_id, key=key, had_cache=had_cache:
                    self._on_avatar_resource_state(avatar_id, key, had_cache, result),
                )
            return

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
                downloaded.update(self.profile_resources.fetch_avatar_batch(missing, service_url))
            return downloaded

        if self._start_managed_remote(
            self._profile_resource_key("profile-avatar-batch", ",".join(requested)),
            work,
            lambda result, expected_ids=set(requested): self._avatar_batch_loaded(expected_ids, result),
            lambda _error, expected_ids=set(requested): self._avatar_batch_failed(expected_ids),
            priority=RequestPriority.NORMAL,
            timeout_seconds=30,
        ) is not None:
            return

        worker = self._tasks.start(
            "SafeLauncher-ProfileAvatarBatch",
            work,
            lambda result, expected_ids=set(requested): self._avatar_batch_loaded(expected_ids, result),
        )
        worker.error_occurred.connect(
            lambda _error, expected_ids=set(requested): self._avatar_batch_failed(expected_ids)
        )

    def _load_avatar_resource(
        self,
        avatar_id: str,
        service_url: str,
        token,
        expected_hash: str = "",
    ) -> bytes:
        token.raise_if_cancelled()
        values = self.profile_resources.fetch_avatar_batch(
            [avatar_id],
            service_url,
            max_items=1,
            max_total_bytes=MAX_PROFILE_AVATAR_BYTES,
        )
        token.raise_if_cancelled()
        data = values.get(avatar_id, b"")
        if not is_valid_avatar_bytes(data, expected_hash):
            raise ProfileServiceError(
                "The profile service returned an invalid avatar.",
                "invalid_avatar_response",
            )
        return data

    def _finish_profile_resource(self, key: RequestKey) -> None:
        binding = self._resource_bindings.pop(key, None)
        if binding is not None:
            binding.close()
            binding.deleteLater()

    def _on_avatar_resource_state(
        self,
        avatar_id: str,
        key: RequestKey,
        had_cache: bool,
        result: Any,
    ) -> None:
        if result.status in {ResourceStatus.STALE, ResourceStatus.READY} and isinstance(result.value, bytes):
            self._avatar_batch_loaded({avatar_id}, {avatar_id: result.value}, persist_legacy_cache=False)
            if result.status == ResourceStatus.READY:
                self._finish_profile_resource(key)
            return
        if result.status == ResourceStatus.OFFLINE and had_cache:
            # RequestManager emits the stale cached value immediately after
            # the offline state when one exists.
            return
        if result.status in {
            ResourceStatus.ERROR,
            ResourceStatus.OFFLINE,
            ResourceStatus.UNAVAILABLE,
            ResourceStatus.AUTHENTICATION_REQUIRED,
            ResourceStatus.PERMISSION_DENIED,
            ResourceStatus.CONFLICT,
            ResourceStatus.CANCELLED,
        }:
            self._avatar_batch_failed({avatar_id})
            self._finish_profile_resource(key)

    def _avatar_batch_failed(self, avatar_ids: set[str]) -> None:
        self._avatar_inflight.difference_update(avatar_ids)

    def _avatar_batch_loaded(
        self,
        avatar_ids: set[str],
        result: Any,
        *,
        persist_legacy_cache: bool = True,
    ) -> None:
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
            if persist_legacy_cache:
                save_cached_avatar(avatar_id, digest, data)
            if self._avatar_dialog is not None and self._avatar_dialog.isVisible():
                self._avatar_dialog.set_thumbnail(avatar_id, pixmap)
            current_avatar = self._draft_avatar_id if self._editing else (
                self._document.get("avatar_asset_id") or self._document.get("avatar_id")
            )
            if self._avatar_reference(current_avatar) == avatar_id:
                self._set_avatar(avatar_id)

    def _avatar_catalog_loaded(self, result: Any, *, persist_legacy_cache: bool = True) -> None:
        self._avatar_catalog_loading = False
        self.btn_avatar.setEnabled(True)
        if isinstance(result, Exception):
            self._avatar_catalog_stale = bool(self._avatar_catalog)
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText(f"Could not load profile pictures: {result}")
            return
        if not isinstance(result, list) or not result:
            self._avatar_catalog_stale = bool(self._avatar_catalog)
            self.footer_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.footer_status.setText("The cloud profile picture catalog is empty.")
            return
        self._avatar_catalog = result
        self._avatar_catalog_stale = False
        if persist_legacy_cache:
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
        if self._avatar_dialog is None:
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
        # Published usernames may be changed. The cloud keeps every previous
        # username as an alias, so existing shared links remain valid.
        self.handle_edit.setReadOnly(False)
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
            "You can change this username. Previous usernames remain valid profile links."
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
        if self._mode != "owner" or not self._editing:
            return
        candidate = normalize_username_handle(self.handle_edit.text())
        current_handle = str(self._profile_settings.get("public_handle", "") or "").strip().lower()
        if candidate and candidate == current_handle:
            self._handle_availability = True
            self.handle_hint.setText("This is your current username. Previous usernames remain valid links.")
            self.handle_hint.setStyleSheet(f"color:{TEXT_SECONDARY};")
            return
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
        if self._mode != "owner" or not self._editing:
            return
        candidate = normalize_username_handle(self.handle_edit.text())
        current_handle = str(self._profile_settings.get("public_handle", "") or "").strip().lower()
        if candidate and candidate == current_handle:
            self._handle_check_inflight = False
            self._handle_availability = True
            return
        if not candidate or not automatic_network_allowed(self.settings):
            return
        serial = self._handle_check_serial
        self._handle_check_inflight = True

        def work():
            return self.profile_resources.check_handle_availability(candidate)

        if self._start_managed_remote(
            self._profile_resource_key("profile-handle-availability", candidate),
            work,
            lambda result, expected_serial=serial, expected_handle=candidate: self._handle_availability_done(
                result, expected_serial, expected_handle
            ),
            lambda error, expected_serial=serial, expected_handle=candidate: self._handle_availability_error(
                str(error), expected_serial, expected_handle
            ),
            timeout_seconds=15,
        ) is not None:
            return

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

    def _reconcile_authenticated_profile(self) -> dict[str, Any] | None:
        """Find or migrate the profile belonging to the current Auth0 identity.

        The legacy token is read only for this one migration call. It is
        deleted only after Convex confirms the identity claim, so an
        interrupted migration cannot strand an existing profile.
        """
        local = load_profile_settings(
            self.settings,
            fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
        )
        handle = str(local.get("public_handle", "") or "").strip().lower()
        legacy_token = str(get_secret("profile_owner_token") or "").strip()
        remote, claimed = self.profile_resources.reconcile_authenticated_profile(handle, legacy_token)
        if claimed and not delete_secret("profile_owner_token"):
            self.auth_progress.emit("Signed in; the old migration token could not be removed locally.")
        return remote

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
            dialog = ProfileUsernameDialog(suggestion, self)
            if dialog.exec() != dialog.DialogCode.Accepted:
                return False
            handle = normalize_username_handle(dialog.value)
            if not handle:
                return False
            suggestion = handle
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
        if self._auth_in_flight:
            return
        if not automatic_network_allowed(self.settings):
            self._set_profile_action_status("Offline mode — central sign-in is unavailable.", True)
            return
        self._auth_in_flight = True
        self._emit_profile_action_state()
        self._set_profile_action_status("Opening central sign-in…")

        def work():
            self.central_auth.device_login(
                progress=lambda message: self.auth_progress.emit(message),
            )
            # Device login and profile provisioning are separate operations.
            # Auth0 can successfully issue the API token while the optional
            # first-login identity lookup is unavailable or rejects a token
            # whose audience is the profile API.  Do not turn that secondary
            # lookup failure into a false authentication failure: the session
            # is already signed in and the user can finish setup locally.
            profile_sync_failed = False
            try:
                remote = self._reconcile_authenticated_profile()
            except (CentralAuthError, ProfileServiceError):
                remote = None
                profile_sync_failed = True

            identity = {}
            identity_lookup_failed = False
            if remote is None:
                try:
                    identity = self.central_auth.userinfo()
                except CentralAuthError:
                    identity_lookup_failed = True
            return {
                "remote": remote,
                # Existing profiles already have a stable handle. Identity
                # claims are used only transiently for a first-login hint and
                # are never retained in the result after setup.
                "identity": identity,
                "profile_sync_failed": profile_sync_failed,
                "identity_lookup_failed": identity_lookup_failed,
            }

        if self._start_managed_remote(
            self._profile_resource_key("profile-sign-in", "central-account"),
            work,
            self._sign_in_done,
            lambda error: self._sign_in_error(str(error)),
            priority=RequestPriority.CRITICAL,
            timeout_seconds=180,
        ) is not None:
            return
        worker = self._tasks.start("SafeLauncher-CentralSignIn", work, self._sign_in_done)
        worker.error_occurred.connect(self._sign_in_error)

    def _sign_in_error(self, error: str) -> None:
        self._sign_in_done(CentralAuthError(str(error), "sign_in_failed"))

    def _sign_in_done(self, result: Any) -> None:
        self._auth_in_flight = False
        if isinstance(result, Exception):
            self._set_admin_controls(self._mode == "owner")
            self._set_profile_action_status(f"Central sign-in failed: {result}", True)
            return
        payload = result if isinstance(result, dict) else {}
        remote = payload.get("remote") if isinstance(payload.get("remote"), dict) else None
        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        profile_sync_failed = bool(payload.get("profile_sync_failed"))
        identity_lookup_failed = bool(payload.get("identity_lookup_failed"))
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
                status_message = "Signed in. Choose a profile username before publishing."
            if profile_sync_failed or identity_lookup_failed:
                status_message += " Profile details could not be refreshed yet; retry profile sync when online."
        if self._mode == "owner":
            self.show_owner()
        else:
            self._profile_settings = load_profile_settings(
                self.settings,
                fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
            )
            self._emit_profile_action_state()
        self.private_profile_changed.emit()
        self._set_profile_action_status(status_message)

    def _sign_out(self) -> None:
        if self._auth_in_flight:
            return
        self._publish_timer.stop()
        self.central_auth.clear()
        self._profile_context_identity = ""
        self._publish_dirty = False
        if self._mode == "owner":
            self.show_owner()
        else:
            self._emit_profile_action_state()
        self._set_profile_action_status("Signed out. Your existing public profile remains visible.")

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
        self._update_editor_publish_control(True, self.central_auth.signed_in, bool(self._profile_settings.get("published")))

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
        if handle != current_handle and not HANDLE_RE.fullmatch(handle):
            handle = normalize_username_handle(handle)
        if not handle or not HANDLE_RE.fullmatch(handle):
            QMessageBox.warning(
                self,
                "Username",
                "Use 3–32 lowercase letters, numbers, dots, underscores, or hyphens.",
            )
            return
        if handle != current_handle:
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
                    "color": stops[0] if isinstance(stops, list) and stops else "#202024",
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
            # A stale catalog is immediately usable, but it must not suppress
            # a manager-owned refresh. This keeps the picker responsive while
            # allowing a newer catalog to arrive in the background.
            if self._avatar_catalog_stale and automatic_network_allowed(self.settings):
                self._request_avatar_catalog()
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
        self._request_avatar_catalog()

    def _request_avatar_catalog(self) -> None:
        """Refresh the avatar catalog through the shared request manager."""
        if self._avatar_catalog_loading:
            return
        self._avatar_catalog_loading = True
        self.btn_avatar.setEnabled(False)
        self.footer_status.setStyleSheet("")
        self.footer_status.setText("Loading cloud profile pictures…")
        service_url = get_profile_service_url()
        catalog_key = self._profile_resource_key("profile-avatar-catalog", "catalog")
        if self.request_manager is not None and self.resource_cache is not None:
            cache_validator = self._avatar_catalog_cache_value_is_valid
            cached = self._profile_cache_entry(
                catalog_key,
                cache_validator,
            )
            had_cache = cached is not None
            spec = self.profile_resources.request_spec(
                catalog_key,
                lambda token: (
                    token.raise_if_cancelled(),
                    self.profile_resources.list_avatar_catalog(service_url),
                )[1],
                priority=RequestPriority.NORMAL,
                timeout_seconds=20,
                tag="profile-avatar-catalog",
            )
            self.request_manager.cached_request(
                spec,
                self.resource_cache,
                max_age_seconds=cache_policy("profile-avatar-catalog").max_age_seconds,
                cache_validator=cache_validator,
                content_type=cache_policy("profile-avatar-catalog").content_type,
            )
            self._bind_resource(
                catalog_key,
                lambda result, key=catalog_key, had_cache=had_cache:
                self._on_avatar_catalog_state(key, had_cache, result),
            )
            return

        def work():
            return self.profile_resources.list_avatar_catalog(service_url)

        if self._start_managed_remote(
            catalog_key,
            work,
            self._avatar_catalog_loaded,
            lambda error: self._avatar_catalog_loaded(
                ProfileServiceError(str(error), "catalog_fetch_failed")
            ),
            priority=RequestPriority.NORMAL,
            timeout_seconds=20,
        ) is not None:
            return
        worker = self._tasks.start("SafeLauncher-ProfileAvatarCatalog", work, self._avatar_catalog_loaded)
        worker.error_occurred.connect(lambda error: self._avatar_catalog_loaded(ProfileServiceError(str(error), "catalog_fetch_failed")))

    def _on_avatar_catalog_state(self, key: RequestKey, had_cache: bool, result: Any) -> None:
        if result.status in {ResourceStatus.STALE, ResourceStatus.READY} and isinstance(result.value, list):
            self._avatar_catalog_loaded(result.value, persist_legacy_cache=False)
            if result.status == ResourceStatus.STALE and result.error is not None:
                # Offline stale data remains usable and should be retried on a
                # later online interaction rather than being marked fresh.
                self._avatar_catalog_stale = True
                self._finish_profile_resource(key)
            if result.status == ResourceStatus.READY:
                self._finish_profile_resource(key)
            return
        if result.status == ResourceStatus.OFFLINE and had_cache:
            return
        if result.status in {
            ResourceStatus.ERROR,
            ResourceStatus.OFFLINE,
            ResourceStatus.UNAVAILABLE,
            ResourceStatus.AUTHENTICATION_REQUIRED,
            ResourceStatus.PERMISSION_DENIED,
            ResourceStatus.CONFLICT,
            ResourceStatus.CANCELLED,
        }:
            self._avatar_catalog_loaded(
                ProfileServiceError(str(result.error or "Profile picture catalog unavailable."), "catalog_fetch_failed")
            )
            self._finish_profile_resource(key)

    def _remove_avatar(self) -> None:
        if self._mode != "owner" or not self._editing:
            return
        self._draft_avatar_id = ""
        self.avatar_selection_label.setText("Using initials")
        self._set_avatar(None)

    def _choose_color(self) -> None:
        current = self._editor_background()
        initial = QColor(str(current.get("color") or current.get("stops", ["#202024"])[0]))
        color = QColorDialog.getColor(initial, self, "Choose profile background")
        if color.isValid():
            with QSignalBlocker(self.background_combo):
                self.background_combo.setCurrentIndex(self.background_combo.findData("custom"))
            self._preview_editor_background({"kind": "solid", "color": color.name().upper()})

    def _publish(self) -> None:
        # Settings can be opened while a public profile is being viewed. The
        # operation always uses the local owner profile, never the document
        # currently displayed in the page.
        self._profile_settings = load_profile_settings(
            self.settings,
            fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
        )
        if not self.central_auth.signed_in:
            self._set_profile_action_status("Sign in to manage your public profile.", True)
            self._sign_in()
            return
        published = bool(self._profile_settings.get("published"))
        if published:
            self._unpublish()
            return
        service_url = get_profile_service_url()
        if not service_url.startswith(("http://", "https://")):
            self._set_profile_action_status(
                "The central profile gateway is not configured for this build.",
                True,
            )
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
            self._set_profile_action_status("Offline mode — public profile publishing is paused.", True)
            return
        service_url = get_profile_service_url()
        profile_settings = load_profile_settings(
            self.settings,
            fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
        )
        handle = str(profile_settings.get("public_handle", "") or "")
        if not service_url.startswith(("http://", "https://")) or not handle or not self.central_auth.signed_in:
            self._set_profile_action_status("Sign in before publishing the public profile.", True)
            return
        db_path = getattr(self.db, "db_path", None)
        self._publishing = True
        self._publish_dirty = False
        self._emit_profile_action_state()
        self._set_profile_action_status("Publishing public profile…")

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
            legacy_token = str(get_secret("profile_owner_token") or "").strip()
            result = self.profile_resources.publish(
                document,
                handle,
                legacy_token=legacy_token,
                service_url=service_url,
            )
            if result.get("legacy_claimed"):
                # Delete only after the service confirms the atomic server-side claim.
                delete_secret("profile_owner_token")
            return result

        if self._start_managed_remote(
            self._profile_resource_key("profile-publish", handle),
            work,
            self._publish_done,
            self._publish_error,
            priority=RequestPriority.CRITICAL,
            timeout_seconds=45,
        ) is not None:
            return
        worker = self._tasks.start("SafeLauncher-PublishProfile", work, self._publish_done)
        worker.error_occurred.connect(self._publish_error)

    def _publish_error(self, error: Any) -> None:
        # Managed requests may deliver the original exception while older
        # compatibility workers deliver only text. Preserve typed profile
        # errors whenever possible so handle conflicts remain actionable.
        if isinstance(error, Exception):
            self._publish_done(error)
        else:
            self._publish_done(ProfileServiceError(str(error), "publish_failed"))

    def _publish_done(self, result: Any) -> None:
        self._publishing = False
        self._emit_profile_action_state()
        if isinstance(result, Exception):
            if isinstance(result, ProfileServiceError) and result.code in {
                "handle_taken",
                "exists",
                "profile_exists",
            }:
                self._handle_availability = False
                self._set_profile_action_status(
                    "That username is already in use. Open Edit profile, choose another username, and publish again.",
                    True,
                )
            elif isinstance(result, ProfileServiceError) and result.code == "profile_exists_for_identity":
                self._set_profile_action_status(
                    "This central account already owns a profile. Refresh your profile connection before publishing again.",
                    True,
                )
            elif isinstance(result, ProfileServiceError) and result.code == "conflict":
                self._set_profile_action_status(
                    "Your profile changed elsewhere. Refresh it, review your edits, and publish again.",
                    True,
                )
            else:
                self._set_profile_action_status(str(result), True)
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
        self._emit_profile_action_state()
        # A background sync may finish while the user is viewing someone
        # else's public page. Never replace that page with the owner's local
        # projection as a side effect of a statistics update.
        if self._mode == "owner":
            if document is not None:
                self._document = document
            if self.isVisible():
                self._render(self._document or build_public_projection(self.db, self._profile_settings))
        self._set_profile_action_status(
            f"Published. Share username @{self._profile_settings.get('public_handle')}.",
        )
        self.private_profile_changed.emit()
        if self._publish_dirty:
            self._publish_timer.start()

    def _unpublish(self) -> None:
        self._publish_timer.stop()
        if not automatic_network_allowed(self.settings):
            self._set_profile_action_status("Offline mode — public profile changes are unavailable.", True)
            return
        service_url = get_profile_service_url()
        if not service_url.startswith(("http://", "https://")) or not self.central_auth.signed_in:
            self._set_profile_action_status("Sign in to manage your public profile.", True)
            return
        self._publishing = True
        self._emit_profile_action_state()
        self._set_profile_action_status("Unpublishing public profile…")
        def work():
            legacy_token = str(get_secret("profile_owner_token") or "").strip()
            handle = str(self._profile_settings.get("public_handle", "") or "")
            result = self.profile_resources.unpublish(
                handle,
                legacy_token=legacy_token,
                service_url=service_url,
            )
            if result.get("legacy_claimed"):
                delete_secret("profile_owner_token")
            return result.get("response")
        if self._start_managed_remote(
            self._profile_resource_key(
                "profile-unpublish",
                str(self._profile_settings.get("public_handle", "")),
            ),
            work,
            self._unpublish_done,
            lambda error: self._unpublish_error(str(error)),
            priority=RequestPriority.CRITICAL,
            timeout_seconds=45,
        ) is not None:
            return
        worker = self._tasks.start("SafeLauncher-UnpublishProfile", work, self._unpublish_done)
        worker.error_occurred.connect(self._unpublish_error)

    def _unpublish_error(self, error: str) -> None:
        self._unpublish_done(ProfileServiceError(str(error), "unpublish_failed"))

    def _unpublish_done(self, result: Any) -> None:
        self._publishing = False
        if isinstance(result, Exception):
            self._emit_profile_action_state()
            self._set_profile_action_status(str(result), True)
            return
        self._profile_settings = save_profile_settings(self.settings, {**self._profile_settings, "published": False}, mark_changed=False)
        self._public_revision = 0
        self.settings.setValue("profile_public_revision", 0)
        self.settings.sync()
        if self._mode == "owner":
            self.show_owner()
        else:
            self._emit_profile_action_state()
        self._set_profile_action_status(
            "Profile unpublished. Your central sign-in remains available for publishing again."
        )
        self.private_profile_changed.emit()

    def _resync_private(self) -> None:
        if not automatic_network_allowed(self.settings):
            self._set_profile_action_status("Offline mode — private profile resync is paused.", True)
            return
        if self._resyncing_private:
            return
        self._resyncing_private = True
        self._emit_profile_action_state()
        db_path = getattr(self.db, "db_path", None)
        def work():
            from database import GameDatabase
            from core.cloud_metadata_sync import CloudMetadataSync
            worker_db = GameDatabase(db_path) if db_path else GameDatabase()
            try:
                return CloudMetadataSync.sync_profile(worker_db, force=True)
            finally:
                worker_db.close()
        self._set_profile_action_status("Resyncing private profile data…")
        if self._start_managed_remote(
            self._profile_resource_key("profile-private-resync", str(db_path or "default")),
            work,
            self._resync_done,
            lambda _error: self._resync_done(False),
            priority=RequestPriority.CRITICAL,
            timeout_seconds=60,
        ) is not None:
            return
        worker = self._tasks.start("SafeLauncher-ProfileResync", work, self._resync_done)
        worker.error_occurred.connect(lambda error: self._resync_done(False))

    def _resync_done(self, result: Any) -> None:
        self._resyncing_private = False
        self._emit_profile_action_state()
        if isinstance(result, Exception) or not result:
            self._set_profile_action_status(
                "Private profile resync failed; local data was preserved.",
                True,
            )
            return
        if self._mode == "owner":
            self.show_owner()
        self._set_profile_action_status("Private profile resynchronized.")

    def set_public_service_url(self, url: str) -> None:
        """Keep a localhost-only override for development and UI tests."""
        value = str(url or "").strip().rstrip("/")
        previous = get_profile_service_url()
        if is_local_service_url(value):
            self.settings.setValue("profile_service_url", value)
        else:
            self.settings.remove("profile_service_url")
        self.settings.sync()
        if previous != get_profile_service_url():
            self._profile_context_identity = ""
            self.profile_resources.invalidate_context()
            self._close_resource_bindings()

    def closeEvent(self, event) -> None:
        self._handle_check_timer.stop()
        self._local_refresh_timer.stop()
        self._publish_timer.stop()
        self._close_resource_bindings()
        self._tasks.cancel_all(250)
        super().closeEvent(event)
