"""Profile and social workflows mixed into the application window shell.

This module keeps profile navigation and remote profile delivery separate
from the main window's library and game-session orchestration.
"""

from core.cache_policy import cache_policy
from core.logger import get_logger
from core.profile_models import HANDLE_RE
from core.profile_resource_service import ProfileResourceService
from core.profile_service import get_profile_service_url
from core.request_contracts import RequestKey, RequestPriority, ResourceStatus
from ui.resource_binding import bind_resource


logger = get_logger("UI.Profile")


class MainWindowProfileMixin:
    """Methods for friends, public profile reads, and profile reconciliation."""

    def _open_public_profile_prompt(self):
        """Open the social hub focused on finding another profile."""
        self._open_friends_popup(focus_find=True)

    def _open_friends_popup(self, focus_find: bool = False) -> None:
        """Show the single custom friends surface used by header navigation."""
        from ui.dialogs.friends_dialog import FriendsDialog

        existing = getattr(self, "_friends_dialog", None)
        if existing is not None:
            try:
                if existing.isVisible():
                    existing.raise_()
                    existing.activateWindow()
                    return
            except RuntimeError:
                self._friends_dialog = None

        dialog = FriendsDialog(
            self.settings,
            self.central_auth,
            self,
            worker_registry=self.worker_supervisor,
            request_manager=self.request_manager,
            focus_find=focus_find,
        )
        self._friends_dialog = dialog

        def open_profile(handle: str) -> None:
            dialog.close()
            self._open_public_profile_handle(handle)

        def open_owner() -> None:
            dialog.close()
            self._open_achievement_profile()

        dialog.open_profile_requested.connect(open_profile)
        dialog.open_owner_profile_requested.connect(open_owner)
        try:
            dialog.exec()
        finally:
            if self._friends_dialog is dialog:
                self._friends_dialog = None

    def _open_public_profile_handle(self, handle: str):
        """Fetch and display a public profile without opening another window."""
        if not self._automatic_network_allowed() and self.request_manager is None:
            from PyQt6.QtWidgets import QMessageBox

            QMessageBox.information(
                self,
                "Public Profile",
                "Offline mode is enabled. Public profiles are unavailable until online mode is restored.",
            )
            return
        value = str(handle or "").strip().lstrip("@").lower()
        if not HANDLE_RE.fullmatch(value):
            from PyQt6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "Public Profile", "That is not a valid SafeLauncher profile username.")
            return
        service_url = get_profile_service_url()
        profile_resources = self.profile_page.profile_resources
        configured = profile_resources.configured(service_url)
        if not configured:
            from PyQt6.QtWidgets import QMessageBox

            QMessageBox.information(
                self,
                "Public Profile Service",
                "The central profile gateway is not configured for this build. Set SAFELAUNCHER_PROFILE_SERVICE_URL only for an explicit development gateway.",
            )
            return
        self.profile_page.footer_status.setText("Loading public profile…")

        def _fetch_public_profile():
            return profile_resources.fetch_public(value, service_url)

        if self.request_manager is not None:
            self._public_profile_generation += 1
            generation = self._public_profile_generation
            key = RequestKey(
                "public-profile",
                f"{ProfileResourceService.endpoint_fingerprint(service_url)}:{value}",
            )
            loader = lambda token: (token.raise_if_cancelled(), _fetch_public_profile())[1]
            if self._public_profile_binding is not None:
                self._public_profile_binding.close()
                self._public_profile_binding.deleteLater()
                self._public_profile_binding = None
            if getattr(self.request_manager, "cache", None) is not None:
                handle = self.request_manager.request_cached(
                    key,
                    loader,
                    max_age_seconds=cache_policy("public-profile").max_age_seconds,
                    priority=RequestPriority.NORMAL,
                    generation=generation,
                    timeout_seconds=20,
                    content_type="application/json",
                )
            else:
                handle = self.request_manager.request(
                    key,
                    loader,
                    priority=RequestPriority.NORMAL,
                    generation=generation,
                    timeout_seconds=20,
                )
            self._public_profile_binding = bind_resource(
                self.request_manager,
                key,
                lambda result, generation=generation, key=key: self._on_managed_public_profile_state(
                    generation, key, result
                ),
                self,
                cancel_on_close=True,
            )
            return

        worker = self._profile_remote_tasks.start(
            "SafeLauncher-OpenPublicProfile",
            _fetch_public_profile,
            self._on_public_profile_loaded,
        )
        worker.error_occurred.connect(self._on_public_profile_error)

    def _on_public_profile_loaded(self, document: dict):
        self._show_profile_page()
        self.profile_page.show_public(document)

    def _on_managed_public_profile_state(self, generation: int, key: RequestKey, result) -> None:
        if generation != self._public_profile_generation:
            return
        if result.status in {ResourceStatus.READY, ResourceStatus.STALE} and isinstance(result.value, dict):
            self._on_public_profile_loaded(result.value)
            if result.status == ResourceStatus.STALE:
                self.profile_page.footer_status.setText(
                    "Showing cached public profile; refresh will retry when online."
                )
        elif result.status not in {ResourceStatus.CANCELLED, ResourceStatus.LOADING}:
            self._on_public_profile_error(str(result.error or "Public profile could not be loaded."))

    def _on_public_profile_error(self, error: str):
        from PyQt6.QtWidgets import QMessageBox

        QMessageBox.warning(self, "Public Profile", str(error))

    def _on_profile_changed(self):
        """Persist profile presentation metadata through the private ledger."""
        self._update_header_identity()
        if hasattr(self, "profile_page"):
            self.profile_page.mark_local_data_changed()
        self._sync_profile_metadata_async()

    def _on_private_profile_changed(self):
        """Persist a publish-state or handle change without republishing."""
        self._update_header_identity()
        self._sync_profile_metadata_async()

    def _sync_profile_metadata_async(self):
        db_path = getattr(self.db, "db_path", None)
        self._profile_sync_generation += 1
        generation = self._profile_sync_generation
        handle = self.cloud_metadata_service.request_profile(
            db_path,
            force=True,
            priority=RequestPriority.CRITICAL,
            generation=generation,
            tag="profile_change",
        )
        handle.future.add_done_callback(
            lambda future, generation=generation: self._profile_sync_done.emit(
                (generation, future)
            )
        )

    def _on_managed_profile_sync_done(self, payload: object) -> None:
        """Refresh the local projection after private cloud reconciliation."""
        generation, future = payload
        if generation != self._profile_sync_generation:
            return
        try:
            result = future.result()
        except Exception as error:
            logger.debug("Profile metadata sync failed: %s", error)
            return
        if result.status == ResourceStatus.READY and result.value:
            self._refresh_library()
            if (
                hasattr(self, "profile_page")
                and self.profile_page.isVisible()
                and getattr(self.profile_page, "_mode", "owner") == "owner"
            ):
                self.profile_page.show_owner()


__all__ = ["MainWindowProfileMixin"]
