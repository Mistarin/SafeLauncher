from concurrent.futures import Future
from types import SimpleNamespace
import unittest

from PyQt6.QtWidgets import QApplication

from core.request_contracts import ResourceStatus
from ui.network_monitor_controller import NetworkMonitorController


class _RequestManager:
    def __init__(self, result):
        self.result = result
        self.requests = []
        self.cancelled = 0

    def request(self, key, loader, **kwargs):
        self.requests.append((key, kwargs))
        future = Future()
        future.set_result(self.result)
        return SimpleNamespace(future=future)

    def cancel_matching(self, predicate):
        self.cancelled += 1


class NetworkMonitorControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def _monitor(self, result, *, allowed=True, test_mode=False):
        manager = _RequestManager(result)
        monitor = NetworkMonitorController(
            manager,
            network_allowed=lambda: allowed,
            offline_test_mode=test_mode,
            initial_delay_ms=60_000,
        )
        emitted = []
        monitor.result_ready.connect(emitted.append)
        return monitor, manager, emitted

    def test_failed_probe_gates_optional_requests_and_reports_transition(self):
        result = SimpleNamespace(
            status=ResourceStatus.READY,
            value=(False, "offline"),
        )
        monitor, manager, emitted = self._monitor(result)
        monitor.probe_now()

        self.assertTrue(monitor.transient_unavailable)
        self.assertEqual(manager.cancelled, 1)
        self.assertEqual(len(emitted), 1)
        self.assertFalse(emitted[0]["reachable"])
        self.assertFalse(emitted[0]["had_previous"])
        self.assertEqual(manager.requests[0][1]["metadata"]["allow_offline"], True)
        monitor.deleteLater()

    def test_recovery_clears_transient_gate_and_reports_previous_state(self):
        manager = _RequestManager(
            SimpleNamespace(status=ResourceStatus.READY, value=(False, "offline"))
        )
        monitor = NetworkMonitorController(
            manager,
            network_allowed=lambda: True,
            initial_delay_ms=60_000,
        )
        emitted = []
        monitor.result_ready.connect(emitted.append)
        monitor.probe_now()
        manager.result = SimpleNamespace(
            status=ResourceStatus.READY,
            value=(True, ""),
        )
        monitor.probe_now()

        self.assertFalse(monitor.transient_unavailable)
        self.assertEqual(len(emitted), 2)
        self.assertTrue(emitted[1]["had_previous"])
        self.assertFalse(emitted[1]["previous_reachable"])
        self.assertTrue(emitted[1]["was_transient_unavailable"])
        monitor.deleteLater()

    def test_stop_invalidates_in_flight_completion(self):
        manager = _RequestManager(
            SimpleNamespace(status=ResourceStatus.READY, value=(False, "offline"))
        )
        # Hold the future so stop can invalidate its generation first.
        pending = Future()
        manager.request = lambda *args, **kwargs: SimpleNamespace(future=pending)
        monitor = NetworkMonitorController(
            manager,
            network_allowed=lambda: True,
            initial_delay_ms=60_000,
        )
        emitted = []
        monitor.result_ready.connect(emitted.append)
        monitor.probe_now()
        monitor.stop()
        pending.set_result(
            SimpleNamespace(status=ResourceStatus.READY, value=(False, "offline"))
        )
        self._app.processEvents()

        self.assertEqual(emitted, [])
        self.assertFalse(monitor.transient_unavailable)
        monitor.deleteLater()

    def test_offline_test_mode_and_policy_prevent_requests(self):
        ready = SimpleNamespace(status=ResourceStatus.READY, value=(True, ""))
        monitor, manager, emitted = self._monitor(ready, test_mode=True)
        monitor.start()
        monitor.probe_now()
        self.assertFalse(monitor.timer.isActive())
        self.assertEqual(manager.requests, [])
        monitor.offline_test_mode = False
        monitor.network_allowed = lambda: False
        monitor.probe_now()
        self.assertEqual(manager.requests, [])
        self.assertEqual(emitted, [])
        monitor.deleteLater()


if __name__ == "__main__":
    unittest.main()
