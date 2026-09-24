"""Qt-thread and lifecycle coverage for the resource-state bridge."""

from __future__ import annotations

import threading
import time
import unittest

from PyQt6.QtWidgets import QApplication

from core.request_contracts import RequestKey, ResourceStatus
from core.request_manager import RequestManager
from ui.resource_binding import ResourceBinding, ResourceBindingRegistry, bind_request


class ResourceBindingTests(unittest.TestCase):
    def test_registry_only_owns_binding_lifetimes(self):
        registry = ResourceBindingRegistry()
        key = RequestKey("data", "one")
        binding = object()
        registry[key] = binding
        self.assertIn(key, registry)
        self.assertIs(registry[key], binding)
        self.assertEqual(registry.take_all(), (binding,))
        self.assertNotIn(key, registry)

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _process_until(self, predicate, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        self.app.processEvents()
        return bool(predicate())

    def test_states_are_delivered_on_the_qt_owner_thread(self):
        manager = RequestManager(max_workers=1)
        binding = ResourceBinding(manager, RequestKey("data", "binding"))
        seen = []
        binding.state_changed.connect(
            lambda result: seen.append((result, threading.get_ident()))
        )
        gui_thread = threading.get_ident()
        try:
            handle = manager.request(binding.key, lambda _token: "value")
            self.assertEqual(handle.future.result(timeout=2).status, ResourceStatus.READY)
            self.assertTrue(
                self._process_until(
                    lambda: any(item[0].status == ResourceStatus.READY for item in seen)
                )
            )
            ready, callback_thread = [
                item for item in seen if item[0].status == ResourceStatus.READY
            ][-1]
            self.assertEqual(ready.value, "value")
            self.assertEqual(callback_thread, gui_thread)
        finally:
            binding.close()
            manager.shutdown()

    def test_close_cancels_page_request_and_suppresses_late_state(self):
        manager = RequestManager(max_workers=1)
        binding = ResourceBinding(
            manager,
            RequestKey("data", "cancelled-binding"),
            cancel_on_close=True,
        )
        seen = []
        binding.state_changed.connect(seen.append)
        started = threading.Event()
        try:
            handle = manager.request(
                binding.key,
                lambda token: (
                    started.set(),
                    token.wait(2),
                    token.raise_if_cancelled(),
                    "late",
                )[-1],
            )
            self.assertTrue(started.wait(2))
            binding.close()
            self.assertEqual(handle.future.result(timeout=2).status, ResourceStatus.CANCELLED)
            self._process_until(lambda: True, timeout=0.05)
            self.assertFalse(any(item.status == ResourceStatus.READY for item in seen))
        finally:
            binding.close()
            manager.shutdown()

    def test_closing_one_shared_binding_does_not_cancel_other_subscriber(self):
        manager = RequestManager(max_workers=1)
        key = RequestKey("data", "shared-binding")
        first = ResourceBinding(manager, key, cancel_on_close=True)
        second = ResourceBinding(manager, key, cancel_on_close=True)
        started = threading.Event()
        try:
            handle = manager.request(
                key,
                lambda token: (
                    started.set(),
                    token.wait(0.1),
                    token.raise_if_cancelled(),
                    "shared",
                )[-1],
            )
            self.assertTrue(started.wait(2))
            first.close()
            result = handle.future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.READY)
            self.assertEqual(result.value, "shared")
        finally:
            first.close()
            second.close()
            manager.shutdown()

    def test_request_binding_rejects_later_generation_for_same_key(self):
        manager = RequestManager(max_workers=1)
        key = RequestKey("data", "generation-bound")
        seen = []
        binding = None
        try:
            first = manager.request(key, lambda _token: "first")
            binding = bind_request(manager, first, seen.append, cancel_on_close=False)
            binding.state_changed.connect(lambda result: seen.append(result))
            self.assertEqual(first.future.result(timeout=2).status, ResourceStatus.READY)
            self.assertTrue(self._process_until(lambda: any(
                result.status == ResourceStatus.READY and result.value == "first"
                for result in seen
            )))

            manager.invalidate(key)
            second = manager.request(key, lambda _token: "second")
            self.assertEqual(second.future.result(timeout=2).status, ResourceStatus.READY)
            self._process_until(lambda: True, timeout=0.05)
            self.assertFalse(any(
                result.status == ResourceStatus.READY and result.value == "second"
                for result in seen
            ))
        finally:
            if binding is not None:
                binding.close()
            manager.shutdown()


if __name__ == "__main__":
    unittest.main()
