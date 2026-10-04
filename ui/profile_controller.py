"""Explicitly composed public-social and private profile workflows."""

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QMessageBox

from core.cache_policy import cache_policy
from core.logger import get_logger
from core.profile_models import HANDLE_RE
from core.profile_resource_service import ProfileResourceService
from core.profile_service import get_profile_service_url
from core.request_contracts import RequestKey, RequestPriority, ResourceStatus
from core.safe_thread import TaskSupervisor
from ui.resource_binding import bind_resource


logger = get_logger("UI.Profile")


class ProfileController(QObject):
    """Own profile request generations without inheriting window state.

    Public reads use the gateway resource service. Private reconciliation uses
    the separate cloud metadata service and never republishes a public profile.
    """

    _sync_done = pyqtSignal(object)

    def __init__(self, *, page, resources, settings, auth, manager, metadata_service,
                 db_path, worker_registry, show_profile, open_owner, refresh_library,
                 refresh_identity, network_allowed, accepts_work, parent=None):
        super().__init__(parent)
        self.page, self.resources = page, resources
        self.settings, self.auth, self.manager = settings, auth, manager
        self.metadata_service, self.db_path = metadata_service, db_path
        self.worker_registry = worker_registry
        self.show_profile, self.open_owner = show_profile, open_owner
        self.refresh_library, self.refresh_identity = refresh_library, refresh_identity
        self.network_allowed, self.accepts_work = network_allowed, accepts_work
        self.dialog_parent = parent
        self._public_generation = 0
        self._private_generation = 0
        self._binding = None
        self._private_handle = None
        self._friends = None
        self._closed = False
        self._legacy_tasks = None
        self._sync_done.connect(self.handle_private_result)

    def open_friends(self, focus_find=False):
        if self._closed or not self.accepts_work():
            return
        from ui.dialogs.friends_dialog import FriendsDialog
        if self._friends is not None:
            self._friends.raise_()
            self._friends.activateWindow()
            return
        dialog = FriendsDialog(
            self.settings, self.auth, self.dialog_parent,
            worker_registry=self.worker_registry, request_manager=self.manager,
            focus_find=focus_find,
        )
        self._friends = dialog
        dialog.open_profile_requested.connect(lambda handle: (dialog.close(), self.open_public(handle)))
        dialog.open_owner_profile_requested.connect(lambda: (dialog.close(), self.open_owner()))
        try:
            dialog.exec()
        finally:
            if self._friends is dialog:
                self._friends = None

    def open_public(self, handle):
        if self._closed or not self.accepts_work():
            return
        if not self.network_allowed() and self.manager is None:
            QMessageBox.information(self.dialog_parent, "Public Profile", "Offline mode is enabled. Public profiles are unavailable until online mode is restored.")
            return
        value = str(handle or "").strip().lstrip("@").lower()
        if not HANDLE_RE.fullmatch(value):
            QMessageBox.warning(self.dialog_parent, "Public Profile", "That is not a valid SafeLauncher profile username.")
            return
        service_url = get_profile_service_url()
        if not self.resources.configured(service_url):
            QMessageBox.information(self.dialog_parent, "Public Profile Service", "The central profile gateway is not configured for this build. Set SAFELAUNCHER_PROFILE_SERVICE_URL only for an explicit development gateway.")
            return
        self.page.footer_status.setText("Loading public profile…")
        self._public_generation += 1
        generation = self._public_generation
        self._close_public_binding()
        fetch = lambda: self.resources.fetch_public(value, service_url)
        if self.manager is None:
            if self._legacy_tasks is None:
                self._legacy_tasks = TaskSupervisor(self, worker_registry=self.worker_registry)
            worker = self._legacy_tasks.start("SafeLauncher-OpenPublicProfile", fetch,
                                               lambda document: self._show_document(generation, document))
            worker.error_occurred.connect(lambda error: self._show_error(generation, error))
            return
        key = RequestKey("public-profile", f"{ProfileResourceService.endpoint_fingerprint(service_url)}:{value}")
        loader = lambda token: (token.raise_if_cancelled(), fetch())[1]
        options = dict(priority=RequestPriority.NORMAL, generation=generation, timeout_seconds=20)
        if getattr(self.manager, "cache", None) is not None:
            self.manager.request_cached(key, loader, max_age_seconds=cache_policy("public-profile").max_age_seconds,
                                        content_type="application/json", **options)
        else:
            self.manager.request(key, loader, **options)
        self._binding = bind_resource(
            self.manager, key, lambda result: self.handle_public_result(generation, result),
            self, cancel_on_close=True,
        )

    def _show_document(self, generation, document):
        if self._closed or generation != self._public_generation or not self.accepts_work():
            return
        self.show_profile()
        self.page.show_public(document)

    def _show_error(self, generation, error):
        if not self._closed and generation == self._public_generation and self.accepts_work():
            QMessageBox.warning(self.dialog_parent, "Public Profile", str(error))

    def handle_public_result(self, generation, result):
        if self._closed or generation != self._public_generation or not self.accepts_work():
            return
        if result.status in {ResourceStatus.READY, ResourceStatus.STALE} and isinstance(result.value, dict):
            self._show_document(generation, result.value)
            if result.status == ResourceStatus.STALE:
                self.page.footer_status.setText("Showing cached public profile; refresh will retry when online.")
        elif result.status not in {ResourceStatus.CANCELLED, ResourceStatus.LOADING}:
            self._show_error(generation, result.error or "Public profile could not be loaded.")

    def profile_changed(self, *, private_only=False):
        if self._closed or not self.accepts_work():
            return
        self.refresh_identity()
        if not private_only:
            self.page.mark_local_data_changed()
        self.sync_private()

    def sync_private(self):
        if self._closed or not self.accepts_work():
            return
        self._private_generation += 1
        generation = self._private_generation
        self._private_handle = self.metadata_service.request_profile(
            self.db_path, force=True, priority=RequestPriority.CRITICAL,
            generation=generation, tag="profile_change",
        )
        self._private_handle.future.add_done_callback(lambda future: self._emit_private(generation, future))

    def _emit_private(self, generation, future):
        try:
            self._sync_done.emit((generation, future))
        except RuntimeError:
            pass

    @pyqtSlot(object)
    def handle_private_result(self, payload):
        generation, future = payload
        if self._closed or generation != self._private_generation or not self.accepts_work():
            return
        self._private_handle = None
        try:
            result = future.result()
        except Exception as error:
            logger.debug("Profile metadata sync failed: %s", error)
            return
        if result.status == ResourceStatus.READY and result.value:
            self.refresh_library()
            if self.page.isVisible() and getattr(self.page, "_mode", "owner") == "owner":
                self.page.show_owner()

    def _close_public_binding(self):
        if self._binding is not None:
            self._binding.close()
            self._binding.deleteLater()
            self._binding = None

    def dispose(self):
        if self._closed:
            return
        self._closed = True
        self._public_generation += 1
        self._private_generation += 1
        self._close_public_binding()
        if self._private_handle is not None:
            self._private_handle.cancel()
            self._private_handle = None
        if self._friends is not None:
            self._friends.close()
