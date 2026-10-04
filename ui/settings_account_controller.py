"""Settings account reads subscribe directly; never block a scheduler worker."""
from PyQt6.QtCore import QObject
from core.cloud_account_service import CloudAccountSnapshot
from core.request_contracts import ResourceStatus
from ui.resource_binding import bind_request
from ui.dialogs.save_conflict_dialog import format_bytes


class SettingsAccountController(QObject):
    def __init__(self, service, manager, *, allowed, show_status, start_task, parent=None):
        super().__init__(parent)
        self.service, self.manager = service, manager
        self.allowed, self.show_status, self.start_task = allowed, show_status, start_task
        self._generation = 0
        self._binding = None
        self._closed = False

    @staticmethod
    def message(overview, listing):
        quota = overview.get('quotaBytes')
        capacity = format_bytes(quota) if quota is not None else 'capacity unavailable'
        return (f"Connected ({format_bytes(overview.get('bytesUsed', 0))} / {capacity} used · "
                f"{len(listing.get('games', overview.get('games', [])))} game(s) · "
                f"{overview.get('concurrentDevices', 0)} concurrent device(s) online)")

    def refresh(self):
        if self._closed:
            return
        self._generation += 1
        generation = self._generation
        if self._binding is not None:
            self._binding.close()
            self._binding = None
        if not self.allowed():
            self.show_status('Offline mode — cloud account checks are disabled.')
            return
        if self.manager is not None and self.service.request_manager is not None:
            try:
                handle = self.service.request_snapshot(tag='settings_account_status')
                self._binding = bind_request(self.manager, handle,
                    lambda result: self._deliver(generation, result), self, cancel_on_close=True)
            except Exception as error:
                self.show_status(str(error))
            return

        def probe():
            try:
                return self.message(self.service.account(), {})
            except Exception as error:
                from core.cloud_backend import describe_cloud_error
                return describe_cloud_error(error)

        self.start_task('SafeLauncher-AccountProbe', probe,
            lambda message: self.show_status(message)
            if not self._closed and generation == self._generation else None)

    def _deliver(self, generation, result):
        if self._closed or generation != self._generation:
            return
        if result.status in {ResourceStatus.IDLE, ResourceStatus.LOADING}:
            return
        if result.usable:
            try:
                snapshot = CloudAccountSnapshot.from_payload(result.value)
            except (TypeError, ValueError):
                self.show_status('Cloud account data was invalid; refresh to try again.')
                return
            self.show_status(self.message(snapshot.overview, snapshot.listing))
        else:
            self.show_status(str(result.error or 'Cloud account data unavailable'))

    def dispose(self):
        self._closed = True
        self._generation += 1
        if self._binding is not None:
            self._binding.close()
            self._binding = None
