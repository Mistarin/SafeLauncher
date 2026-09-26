"""Custom friends and profile-discovery hub."""

from __future__ import annotations

from typing import Any, Callable

from PyQt6.QtCore import Qt, QSettings, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QMessageBox, QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from core.network_policy import automatic_network_allowed
from core.profile_models import HANDLE_RE, load_profile_settings, normalize_social_snapshot, save_profile_settings
from core.profile_service import ProfileServiceError, get_profile_service_url
from core.profile_resource_service import ProfileResourceService
from core.cache_policy import cache_policy
from core.safe_thread import TaskSupervisor
from core.request_contracts import RequestPriority, ResourceStatus
from ui.resource_binding import ResourceBinding, bind_request
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
                 parent=None, worker_registry=None, focus_find: bool = False, request_manager=None):
        super().__init__("Friends", parent)
        self.settings = settings or QSettings("SafeLauncher", "SafeLauncher")
        self.auth_session = auth_session
        self._tasks = TaskSupervisor(self, worker_registry=worker_registry)
        self.request_manager = request_manager
        self.profile_resources = ProfileResourceService(self.auth_session)
        self._resource_bindings: dict[str, ResourceBinding] = {}
        self._focus_find = bool(focus_find)
        self._social_loading = False
        self._social_mutating = False
        self._snapshot: dict[str, Any] = normalize_social_snapshot({})
        self._handle = ""
        self._social_action_buttons: list[QPushButton] = []
        self._pending_success_message = ""
        self._technical_error = ""

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
        self.btn_copy_technical = QPushButton("Copy technical details")
        self.btn_copy_technical.setAccessibleName("Copy friends technical details")
        self.btn_copy_technical.setToolTip("Copy the last friends-service error for troubleshooting")
        self.btn_copy_technical.clicked.connect(self._copy_technical_details)
        self.btn_copy_technical.setVisible(False)
        header_layout.addWidget(self.btn_copy_technical)
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
        service_ready = bool(self._handle and signed_in and configured and automatic_network_allowed(self.settings))
        ready = bool(service_ready and published)
        # Refresh is also allowed when the local publication flag is stale so
        # the authoritative public-profile service can repair the local cache.
        self.btn_refresh.setEnabled(service_ready and not self._social_loading and not self._social_mutating)
        self.btn_add.setEnabled(ready and not self._social_loading and not self._social_mutating)
        self.btn_find.setEnabled(bool(automatic_network_allowed(self.settings)))
        for button in self._social_action_buttons:
            button.setEnabled(ready and not self._social_loading and not self._social_mutating)
        if not automatic_network_allowed(self.settings):
            message = "Offline mode — friends are unavailable."
        elif not signed_in:
            message = "Sign in from My profile to manage friends."
        elif not published or not self._handle:
            message = "Public profile status will be checked when you refresh."
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
            button.setObjectName("friendsRowAction")
            button.setToolTip({
                "View": "Open this profile",
                "View profile": "Open this profile",
                "Remove": "Remove this person from your friends",
                "Block": "Block this profile and remove the relationship",
                "Accept": "Accept the friend request",
                "Decline": "Decline the friend request",
                "Cancel": "Cancel the friend request",
                "Unblock": "Allow this profile to contact you again",
            }.get(caption, caption))
            button.clicked.connect(callback)
            self._social_action_buttons.append(button)
            row_layout.addWidget(button)
        layout.insertWidget(max(0, layout.count() - 1), row)

    def _render(self) -> None:
        self._social_action_buttons.clear()
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
                    ("Remove", lambda h=handle: self._confirm_remove_friend(h)),
                ])
        if not friends:
            self._add_info(self.friends_layout, "No friends yet. Use Find Friends to add someone by @handle.")

        for item in incoming if isinstance(incoming, list) else []:
            if isinstance(item, dict):
                request_id = str(item.get("request_id", ""))
                handle = str(item.get("handle", ""))
                self._add_row(self.requests_layout, item, [
                    ("View", lambda h=handle: self.open_profile_requested.emit(h)),
                    ("Accept", lambda r=request_id: self._mutate(
                        "respond_friend_request", "Friend request accepted.",
                        request_id=r, action="accept"
                    )),
                    ("Decline", lambda r=request_id: self._mutate(
                        "respond_friend_request", "Request declined.",
                        request_id=r, action="decline"
                    )),
                    ("Block", lambda h=handle: self._confirm_block_profile(h)),
                ])
        for item in outgoing if isinstance(outgoing, list) else []:
            if isinstance(item, dict):
                request_id = str(item.get("request_id", ""))
                handle = str(item.get("handle", ""))
                self._add_row(self.requests_layout, item, [
                    ("View", lambda h=handle: self.open_profile_requested.emit(h)),
                    ("Cancel", lambda r=request_id: self._mutate(
                        "respond_friend_request", "Request canceled.",
                        request_id=r, action="cancel"
                    )),
                ])
        for handle in blocked if isinstance(blocked, list) else []:
            value = str(handle or "")
            self._add_row(self.requests_layout, {"display_name": "Blocked profile", "handle": value}, [
                ("Unblock", lambda h=value: self._mutate(
                    "unblock_user", "Profile unblocked.", target_handle=h
                )),
            ])
        if not incoming and not outgoing and not blocked:
            self._add_info(self.requests_layout, "No pending requests or blocked profiles.")

        self._update_gate()

    def _confirm_remove_friend(self, friend_handle: str) -> None:
        if QMessageBox.question(
            self,
            "Remove friend",
            f"Remove @{friend_handle} from your friends?\n\nThis ends the friendship for both of you. You can send a new request later.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._mutate("remove_friend", "Friend removed.", target_handle=friend_handle)

    def _confirm_block_profile(self, friend_handle: str) -> None:
        if QMessageBox.question(
            self,
            "Block profile",
            f"Block @{friend_handle}?\n\nThis also removes any friendship or pending request.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._mutate("block_user", "Profile blocked.", target_handle=friend_handle)

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
            return self.profile_resources.get_owner_social(service_url)

        profile = load_profile_settings(
            self.settings,
            fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
        )
        # When the local publication flag is false/stale, bypass any cached
        # social snapshot and ask the public service for the owner profile.
        managed = self._start_managed_remote(
            self.profile_resources.request_spec(
                self.profile_resources.request_key(
                    "profile-social", handle, service_url=service_url
                ),
                lambda token: (token.raise_if_cancelled(), work())[1],
                priority=RequestPriority.NORMAL,
                timeout_seconds=20,
                tag="friends-dialog-refresh",
            ),
            self._refresh_done,
            lambda error: self._refresh_done(ProfileServiceError(str(error), "social_refresh_failed")),
            cache_policy_name="profile-social",
            cache_validator=self._social_cache_value_is_valid,
        ) if bool(profile.get("published")) else None
        if managed is not None:
            return

        worker = self._tasks.start("SafeLauncher-FriendsDialogRefresh", work, self._refresh_done)
        worker.error_occurred.connect(lambda error: self._refresh_done(ProfileServiceError(error, "social_refresh_failed")))

    def _refresh_done(self, result: Any) -> None:
        self._social_loading = False
        if isinstance(result, Exception):
            pending = self._pending_success_message
            self._pending_success_message = ""
            self._update_gate()
            self.status_label.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            message = self._friendly_social_error(result, "refresh")
            self.status_label.setText(f"{pending} {message}".strip())
            self._set_technical_error(result)
            return
        pending = self._pending_success_message
        self._pending_success_message = ""
        self.status_label.setStyleSheet("")
        self._clear_technical_error()
        owner = result.get("profile") if isinstance(result, dict) else None
        social = result.get("social") if isinstance(result, dict) and "social" in result else result
        if isinstance(owner, dict):
            owner_handle = str(owner.get("handle", "") or "").strip().lower()
            if owner_handle:
                profile = load_profile_settings(
                    self.settings,
                    fallback_name=str(self.settings.value("user_name", "Player", type=str) or "Player"),
                )
                profile = {**profile, "public_handle": owner_handle, "published": True}
                save_profile_settings(self.settings, profile, mark_changed=False)
                self._handle = owner_handle
                self.identity_label.setText(
                    f"{profile.get('display_name', 'Player')}  ·  @{owner_handle}"
                )
        self._snapshot = normalize_social_snapshot(social)
        if self._snapshot is None:
            self.status_label.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.status_label.setText("Friends could not be refreshed because the service response was invalid.")
            self._update_gate()
            return
        self._render()
        if pending:
            self.status_label.setStyleSheet(f"color:{SEMANTIC_SUCCESS};")
            self.status_label.setText(pending)

    def _start_managed_remote(
        self,
        spec,
        on_ready,
        on_error,
        *,
        cache_policy_name: str = "",
        cache_validator=None,
    ):
        if self.request_manager is None:
            return None
        cache = getattr(self.request_manager, "cache", None)
        if cache is not None and cache_policy_name:
            policy = cache_policy(cache_policy_name)
            handle = self.request_manager.cached_request(
                spec,
                cache,
                max_age_seconds=policy.max_age_seconds,
                stale_while_revalidate=policy.stale_while_revalidate,
                cache_validator=cache_validator,
                content_type=policy.content_type,
            )
        else:
            handle = self.request_manager.submit(spec)
        request_id = handle.request_id

        def _deliver(result):
            if result.status in {ResourceStatus.IDLE, ResourceStatus.LOADING}:
                return
            if result.status == ResourceStatus.STALE:
                binding = self._resource_bindings.pop(request_id, None)
                if binding is not None:
                    binding.close()
                if result.value is not None:
                    on_ready(result.value)
                return
            binding = self._resource_bindings.pop(request_id, None)
            if binding is not None:
                binding.close()
            if result.status == ResourceStatus.READY:
                on_ready(result.value)
            elif result.status != ResourceStatus.CANCELLED:
                on_error(result.error or result.status.value)

        self._resource_bindings[request_id] = bind_request(
            self.request_manager,
            handle,
            _deliver,
            self,
            cancel_on_close=True,
        )
        return handle

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
        self._mutate(
            "send_friend_request", "Friend request sent.", target_handle=target
        )

    def _mutate(
        self,
        operation: str,
        success: str,
        *,
        target_handle: str = "",
        request_id: str = "",
        action: str = "",
    ) -> None:
        if self._social_mutating or not self._update_gate():
            return
        self._social_mutating = True
        self._update_gate()
        self.status_label.setStyleSheet("")
        self.status_label.setText("Updating friends…")
        owner = self._handle
        service_url = get_profile_service_url()

        def work():
            return self.profile_resources.social_operation(
                operation,
                owner,
                target_handle=target_handle,
                request_id=request_id,
                action=action,
                service_url=service_url,
            )

        if self._start_managed_remote(
            self.profile_resources.request_spec(
                self.profile_resources.request_key(
                    "profile-social-operation",
                    f"{operation}:{owner}:{target_handle}:{request_id}:{action}",
                    "friends-v1",
                    service_url,
                ),
                lambda token: (token.raise_if_cancelled(), work())[1],
                priority=RequestPriority.NORMAL,
                timeout_seconds=20,
                tag="friends-dialog-mutation",
            ),
            lambda result: self._mutation_done(result, success),
            lambda error: self._mutation_done(ProfileServiceError(str(error), "social_operation_failed"), success),
        ) is not None:
            return

        worker = self._tasks.start("SafeLauncher-FriendsDialogMutation", work, lambda result: self._mutation_done(result, success))
        worker.error_occurred.connect(lambda error: self._mutation_done(ProfileServiceError(error, "social_operation_failed"), success))

    def _mutation_done(self, result: Any, success: str) -> None:
        self._social_mutating = False
        if isinstance(result, Exception):
            self.status_label.setStyleSheet(f"color:{SEMANTIC_ERROR};")
            self.status_label.setText(self._friendly_social_error(result, "update"))
            self._set_technical_error(result)
            self._update_gate()
            return
        self._clear_technical_error()
        self.status_label.setStyleSheet(f"color:{SEMANTIC_SUCCESS};")
        self.status_label.setText(success)
        if success == "Friend request sent.":
            self.find_input.clear()
        if self.request_manager is not None and self._handle:
            self.request_manager.invalidate(
                self.profile_resources.request_key(
                    "profile-social",
                    self._handle,
                    service_url=get_profile_service_url(),
                )
            )
        self._pending_success_message = success
        self.refresh()

    @staticmethod
    def _friendly_social_error(error: Exception, operation: str) -> str:
        """Keep ordinary social errors actionable without exposing transport text."""
        code = str(getattr(error, "code", "") or "").lower()
        if code == "offline":
            return "Offline mode is enabled. Connect to refresh friends."
        if code in {"not_signed_in", "owner_token_missing"}:
            return "Sign in to SafeLauncher to use friends."
        if code == "profile_required":
            return "Your public profile is not available for this account. Publish it again before using Friends."
        if code == "unconfigured":
            return "The profile service is not configured yet."
        if code in {"unreachable", "social_refresh_failed", "social_operation_failed"}:
            return (
                "Friends could not be refreshed. Check your connection and try again."
                if operation == "refresh" else
                "That friend action could not be completed. Check your connection and try again."
            )
        return (
            "Friends could not be refreshed. Try again."
            if operation == "refresh" else
            "That friend action could not be completed. Try again."
        )

    def _set_technical_error(self, error: Exception) -> None:
        self._technical_error = str(error)
        self.btn_copy_technical.setVisible(bool(self._technical_error))

    @staticmethod
    def _social_cache_value_is_valid(value: Any) -> bool:
        if isinstance(value, dict) and "social" in value:
            value = value.get("social")
        return normalize_social_snapshot(value) is not None

    def _clear_technical_error(self) -> None:
        self._technical_error = ""
        self.btn_copy_technical.setVisible(False)

    def _copy_technical_details(self) -> None:
        if self._technical_error:
            QApplication.clipboard().setText(self._technical_error)
            self.btn_copy_technical.setToolTip("Technical details copied to the clipboard")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._focus_find:
            self.find_input.setFocus()
        self.refresh()

    def closeEvent(self, event) -> None:
        for binding in tuple(self._resource_bindings.values()):
            binding.close()
        self._resource_bindings.clear()
        self._tasks.cancel_all(100)
        super().closeEvent(event)
