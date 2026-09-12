"""Custom friends and profile-discovery hub."""

from __future__ import annotations

from typing import Any, Callable

from PyQt6.QtCore import Qt, QSettings, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from core.network_policy import automatic_network_allowed
from core.profile_models import HANDLE_RE, load_profile_settings, normalize_social_snapshot
from core.profile_service import ProfileServiceClient, ProfileServiceError, get_profile_service_url
from core.safe_thread import TaskSupervisor
from ui.components.popup_shell import PopupDialog
from ui.icons import get_icon
from ui.theme import SEMANTIC_ERROR, SEMANTIC_SUCCESS, TEXT_PRIMARY, TEXT_SECONDARY


class FriendsDialog(PopupDialog):
    """One compact social surface for friends, requests, and profile search.

    The dialog only owns social presentation and service calls. Opening a
    public profile remains a MainWindow navigation operation through a signal,
    so there is never a second profile page with a separate lifecycle.
    """

    open_profile_requested = pyqtSignal(str)
    open_owner_profile_requested = pyqtSignal()

    def __init__(self, settings: QSettings | None = None, auth_session=None,
                 parent=None, worker_registry=None, focus_find: bool = False):
        super().__init__("Friends", parent)
        self.settings = settings or QSettings("SafeLauncher", "SafeLauncher")
        self.auth_session = auth_session
        self._tasks = TaskSupervisor(self, worker_registry=worker_registry)
        self._focus_find = bool(focus_find)
        self._social_loading = False
        self._social_mutating = False
        self._snapshot: dict[str, Any] = normalize_social_snapshot({})
        self._handle = ""

        self.setMinimumSize(650, 500)
        self.resize(760, 620)
        self.setStyleSheet(self.styleSheet() + """
            QDialog#safeLauncherPopup QFrame#friendsHeader,
            QDialog#safeLauncherPopup QFrame#friendsRow {
                background: rgba(255, 255, 255, 0.035);
                border: 1px solid rgba(255, 255, 255, 0.055);
                border-radius: 9px;
            }
            QDialog#safeLauncherPopup QFrame#friendsRow:hover {
                background: rgba(255, 255, 255, 0.065);
            }
            QDialog#safeLauncherPopup QTabWidget#friendsTabs::pane {
                background: transparent;
                border: none;
            }
            QDialog#safeLauncherPopup QTabBar::tab {
                background: transparent;
                color: #A1A1AA;
                border: none;
                border-bottom: 2px solid transparent;
                padding: 9px 12px;
            }
            QDialog#safeLauncherPopup QTabBar::tab:selected {
                color: #FFFFFF;
                border-bottom-color: #7EB6FF;
            }
            QDialog#safeLauncherPopup QLineEdit#friendsInput {
                background: rgba(0, 0, 0, 0.23);
                border: 1px solid rgba(255, 255, 255, 0.09);
                border-radius: 8px;
                padding: 8px 10px;
            }
        """)

        root = self.popup_layout(margins=(20, 16, 20, 18), spacing=12)
        header = QFrame()
        header.setObjectName("friendsHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 14, 10)
        header_layout.setSpacing(10)
        self.identity_label = QLabel("Friends")
        self.identity_label.setStyleSheet("font-size: 14px; font-weight: 700;")
        header_layout.addWidget(self.identity_label, 1)
        self.status_label = QLabel()
        self.status_label.setObjectName("popupHint")
        self.status_label.setWordWrap(True)
        header_layout.addWidget(self.status_label)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setIcon(get_icon("ph.arrows-clockwise-bold", color=TEXT_SECONDARY))
        self.btn_refresh.clicked.connect(self.refresh)
        header_layout.addWidget(self.btn_refresh)
        root.addWidget(header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("friendsTabs")
        self.friends_page, self.friends_layout = self._scroll_page()
        self.requests_page, self.requests_layout = self._scroll_page()
        self.find_page = QFrame()
        self.find_page.setObjectName("friendsFindPage")
        find_layout = QVBoxLayout(self.find_page)
        find_layout.setContentsMargins(8, 12, 8, 8)
        find_layout.setSpacing(10)
        find_hint = QLabel("Find someone by their visible username or @handle. Handles are stable once published.")
        find_hint.setObjectName("popupHint")
        find_hint.setWordWrap(True)
        find_layout.addWidget(find_hint)
        find_row = QHBoxLayout()
        self.find_input = QLineEdit()
        self.find_input.setObjectName("friendsInput")
        self.find_input.setPlaceholderText("username or @username")
        self.find_input.setMaxLength(40)
        self.find_input.returnPressed.connect(self._find_profile)
        find_row.addWidget(self.find_input, 1)
        self.btn_find = QPushButton("Find profile")
        self.btn_find.setIcon(get_icon("ph.magnifying-glass-bold", color="#FFFFFF"))
        self.btn_find.clicked.connect(self._find_profile)
        find_row.addWidget(self.btn_find)
        self.btn_add = QPushButton("Add friend")
        self.btn_add.setIcon(get_icon("ph.user-plus-bold", color="#FFFFFF"))
        self.btn_add.clicked.connect(self._add_friend)
        find_row.addWidget(self.btn_add)
        find_layout.addLayout(find_row)
        self.find_status = QLabel()
        self.find_status.setObjectName("popupHint")
        self.find_status.setWordWrap(True)
        find_layout.addWidget(self.find_status)
        find_layout.addStretch(1)

        self.tabs.addTab(self.friends_page, "Friends")
        self.tabs.addTab(self.requests_page, "Requests")
        self.tabs.addTab(self.find_page, "Find Friends")
        root.addWidget(self.tabs, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        self.btn_profile = QPushButton("My profile")
        self.btn_profile.setIcon(get_icon("ph.user-circle-bold", color=TEXT_SECONDARY))
        self.btn_profile.clicked.connect(self.open_owner_profile_requested.emit)
        footer.addWidget(self.btn_profile)
        self.btn_close = QPushButton("Close")
        self.btn_close.setObjectName("popupSecondary")
        self.btn_close.clicked.connect(self.reject)
        footer.addWidget(self.btn_close)
        root.addLayout(footer)

        self._load_identity()
        if self._focus_find:
            self.tabs.setCurrentIndex(2)

    def _scroll_page(self) -> tuple[QScrollArea, QVBoxLayout]:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)
        layout.addStretch(1)
        area.setWidget(body)
        # Return the layout; the initial stretch is removed before each render.
        return area, layout

    def _load_identity(self) -> None:
        profile = load_profile_settings(
            self.settings,
            fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
        )
        self._handle = str(profile.get("public_handle", "") or "").strip().lower()
        name = str(profile.get("display_name", "Player") or "Player")
        self.identity_label.setText(f"{name}  ·  @{self._handle}" if self._handle else name)
        self._update_gate(profile)

    def _update_gate(self, profile: dict[str, Any] | None = None) -> bool:
        if profile is None:
            profile = load_profile_settings(
                self.settings,
                fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
            )
        published = bool(profile.get("published"))
        signed_in = bool(getattr(self.auth_session, "signed_in", False))
        configured = get_profile_service_url().startswith(("http://", "https://"))
        ready = bool(self._handle and published and signed_in and configured and automatic_network_allowed(self.settings))
        self.btn_refresh.setEnabled(ready and not self._social_loading and not self._social_mutating)
        self.btn_add.setEnabled(ready and not self._social_mutating)
        self.btn_find.setEnabled(bool(automatic_network_allowed(self.settings)))
        if not automatic_network_allowed(self.settings):
            message = "Offline mode — friends are unavailable."
        elif not signed_in:
            message = "Sign in from My profile to manage friends."
        elif not published or not self._handle:
            message = "Publish your profile from My profile to manage friends."
        elif not configured:
            message = "The central profile service is not configured."
        else:
            message = f"{len(self._snapshot.get('friends', []))} friend(s)"
        self.status_label.setText(message)
        return ready

    @staticmethod
    def _clear_layout(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _add_row(self, layout: QVBoxLayout, summary: dict[str, Any], actions: list[tuple[str, Callable[[], None]]]) -> None:
        row = QFrame()
        row.setObjectName("friendsRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(10, 7, 10, 7)
        row_layout.setSpacing(6)
        name = str(summary.get("display_name", "Player") or "Player")
        handle = str(summary.get("handle", "") or "").strip().lstrip("@")
        label = QLabel(f"{name}  ·  @{handle}" if handle else name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setStyleSheet(f"color:{TEXT_PRIMARY};")
        row_layout.addWidget(label, 1)
        for caption, callback in actions:
            button = QPushButton(caption)
            button.clicked.connect(callback)
            row_layout.addWidget(button)
        layout.insertWidget(max(0, layout.count() - 1), row)

    def _render(self) -> None:
        self._clear_layout(self.friends_layout)
        self._clear_layout(self.requests_layout)
        friends = self._snapshot.get("friends", [])
        incoming = self._snapshot.get("incoming_requests", [])
        outgoing = self._snapshot.get("outgoing_requests", [])
        blocked = self._snapshot.get("blocked_handles", [])
        for item in friends if isinstance(friends, list) else []:
            if isinstance(item, dict):
                handle = str(item.get("handle", ""))
                self._add_row(self.friends_layout, item, [
                    ("View profile", lambda h=handle: self.open_profile_requested.emit(h)),
                    ("Remove", lambda h=handle: self._mutate(lambda c, owner: c.remove_friend(owner, h), "Friend removed.")),
                ])
        if not friends:
            self._add_info(self.friends_layout, "No friends yet. Use Find Friends to add someone by @handle.")

        for item in incoming if isinstance(incoming, list) else []:
            if isinstance(item, dict):
                request_id = str(item.get("request_id", ""))
                handle = str(item.get("handle", ""))
                self._add_row(self.requests_layout, item, [
                    ("View", lambda h=handle: self.open_profile_requested.emit(h)),
                    ("Accept", lambda r=request_id: self._mutate(lambda c, owner: c.respond_friend_request(owner, r, "accept"), "Friend request accepted.")),
                    ("Decline", lambda r=request_id: self._mutate(lambda c, owner: c.respond_friend_request(owner, r, "decline"), "Request declined.")),
                    ("Block", lambda h=handle: self._mutate(lambda c, owner: c.block_user(owner, h), "Profile blocked.")),
                ])
        for item in outgoing if isinstance(outgoing, list) else []:
            if isinstance(item, dict):
                request_id = str(item.get("request_id", ""))
                handle = str(item.get("handle", ""))
                self._add_row(self.requests_layout, item, [
                    ("View", lambda h=handle: self.open_profile_requested.emit(h)),
                    ("Cancel", lambda r=request_id: self._mutate(lambda c, owner: c.respond_friend_request(owner, r, "cancel"), "Request canceled.")),
                ])
        for handle in blocked if isinstance(blocked, list) else []:
            value = str(handle or "")
            self._add_row(self.requests_layout, {"display_name": "Blocked profile", "handle": value}, [
                ("Unblock", lambda h=value: self._mutate(lambda c, owner: c.unblock_user(owner, h), "Profile unblocked.")),
            ])
        if not incoming and not outgoing and not blocked:
            self._add_info(self.requests_layout, "No pending requests or blocked profiles.")

        self._update_gate()

    @staticmethod
    def _add_info(layout: QVBoxLayout, message: str) -> None:
        label = QLabel(message)
        label.setObjectName("popupHint")
        label.setWordWrap(True)
        layout.insertWidget(max(0, layout.count() - 1), label)

    def refresh(self) -> None:
        self._load_identity()
        if not self._update_gate():
            self._render()
            return
        if self._social_loading or self._social_mutating:
            return
        self._social_loading = True
        self._update_gate()
        self.status_label.setText("Refreshing friends…")
        handle = self._handle
        service_url = get_profile_service_url()

        def work():
            with ProfileServiceClient(service_url, auth_session=self.auth_session) as client:
                return client.get_social(handle)

        worker = self._tasks.start("SafeLauncher-FriendsDialogRefresh", work, self._refresh_done)
        worker.error_occurred.connect(lambda error: self._refresh_done(ProfileServiceError(error, "social_refresh_failed")))

    def _refresh_done(self, result: Any) -> None:
        self._social_loading = False
        if isinstance(result, Exception):
            self.status_label.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.status_label.setText(str(result))
            self._update_gate()
            return
        self.status_label.setStyleSheet("")
        self._snapshot = normalize_social_snapshot(result if isinstance(result, dict) else {})
        self._render()

    def _target(self) -> str:
        value = self.find_input.text().strip().lstrip("@").lower()
        if not HANDLE_RE.fullmatch(value):
            self.find_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.find_status.setText("Enter a valid username (3–32 lowercase letters, numbers, dots, underscores, or hyphens).")
            return ""
        if value == self._handle:
            self.find_status.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.find_status.setText("You cannot add yourself.")
            return ""
        return value

    def _find_profile(self) -> None:
        target = self._target()
        if target:
            self.find_status.setStyleSheet("")
            self.find_status.setText(f"Opening @{target}…")
            self.open_profile_requested.emit(target)

    def _add_friend(self) -> None:
        target = self._target()
        if not target or not self._update_gate():
            return
        self._mutate(lambda client, owner: client.send_friend_request(owner, target), "Friend request sent.")

    def _mutate(self, operation: Callable[[ProfileServiceClient, str], Any], success: str) -> None:
        if self._social_mutating or not self._update_gate():
            return
        self._social_mutating = True
        self._update_gate()
        self.status_label.setStyleSheet("")
        self.status_label.setText("Updating friends…")
        owner = self._handle
        service_url = get_profile_service_url()

        def work():
            with ProfileServiceClient(service_url, auth_session=self.auth_session) as client:
                return operation(client, owner)

        worker = self._tasks.start("SafeLauncher-FriendsDialogMutation", work, lambda result: self._mutation_done(result, success))
        worker.error_occurred.connect(lambda error: self._mutation_done(ProfileServiceError(error, "social_operation_failed"), success))

    def _mutation_done(self, result: Any, success: str) -> None:
        self._social_mutating = False
        if isinstance(result, Exception):
            self.status_label.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.status_label.setText(str(result))
            self._update_gate()
            return
        self.status_label.setStyleSheet(f"color:{SEMANTIC_SUCCESS};")
        self.status_label.setText(success)
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._focus_find:
            self.find_input.setFocus()
        self.refresh()
