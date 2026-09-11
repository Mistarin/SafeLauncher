"""Fault-tolerant QThread wrapper for SafeLauncher.

Wraps thread run execution in top-level try/except guards to prevent uncaught
background thread exceptions from crashing Qt event loops or destroying thread handles.
"""

import atexit
import time
import traceback
import weakref
from typing import Callable, Any

from PyQt6.QtCore import QCoreApplication, QObject, QThread, pyqtSignal
from core.logger import get_logger

logger = get_logger("SafeQThread")

_LIVE_THREADS = weakref.WeakSet()
_SHUTDOWN_HOOK_APP = None
_ATEXIT_HOOK_REGISTERED = False
_SHUTDOWN_IN_PROGRESS = False


def _discard_live_thread(worker_ref) -> None:
    worker = worker_ref()
    if worker is not None:
        _LIVE_THREADS.discard(worker)


def _is_running(worker: QThread) -> bool:
    """Read a QThread state without letting a late Qt deletion escape."""
    try:
        return bool(worker.isRunning())
    except RuntimeError:
        return False


def _request_cancel(worker: QThread) -> None:
    """Use the strongest cooperative cancellation API a worker exposes."""
    try:
        if hasattr(worker, "request_cancel"):
            worker.request_cancel()
        else:
            worker.requestInterruption()
    except RuntimeError:
        pass


def _shutdown_live_threads() -> None:
    """Stop every SafeQThread before Qt starts deleting its QObject tree.

    A worker can be owned by a dialog that is no longer visible, so a window's
    feature-specific close handler cannot be the only lifecycle boundary. Qt
    aborts when a QThread QObject is destroyed while its native thread is
    still running. The application shutdown hook is the final safety net for
    those workers; normal owners still stop them earlier and remain responsive.
    """
    global _SHUTDOWN_IN_PROGRESS
    if _SHUTDOWN_IN_PROGRESS:
        return
    _SHUTDOWN_IN_PROGRESS = True

    # Keep checking the weak registry instead of waiting on one initial
    # snapshot. A completion callback can retire one worker while another
    # owner is still unwinding and a late queued callback can briefly expose a
    # second worker. The final Qt shutdown boundary must cover both cases.
    deadline = time.monotonic() + 30.0
    deadline_reported = False
    try:
        while True:
            running = [worker for worker in list(_LIVE_THREADS) if _is_running(worker)]
            if not running:
                break

            for worker in running:
                _request_cancel(worker)

            now = time.monotonic()
            if now >= deadline and not deadline_reported:
                deadline_reported = True
                logger.error(
                    "SafeLauncher shutdown is waiting for non-cooperative worker(s): %s",
                    ", ".join(worker.__class__.__name__ for worker in running),
                )

            # Once Qt begins tearing down its object tree, returning while a
            # native QThread is alive is never safe. The 30-second threshold
            # is diagnostic only; the final wait remains unbounded so this
            # hook cannot turn a slow cancellation into Qt's fatal warning.
            for worker in running:
                try:
                    worker.wait(250)
                except RuntimeError:
                    pass
    finally:
        _SHUTDOWN_IN_PROGRESS = False


def _register_live_thread(worker: QThread) -> None:
    """Retain a process-wide view of workers for the final Qt shutdown hook."""
    global _SHUTDOWN_HOOK_APP, _ATEXIT_HOOK_REGISTERED
    _LIVE_THREADS.add(worker)
    if not _ATEXIT_HOOK_REGISTERED:
        # Test runners and embedding applications sometimes destroy the Qt
        # application without calling QApplication.quit(). The atexit guard
        # covers that path as well as the normal aboutToQuit path.
        atexit.register(_shutdown_live_threads)
        _ATEXIT_HOOK_REGISTERED = True
    worker_ref = weakref.ref(worker)
    try:
        worker.destroyed.connect(lambda _object=None, ref=worker_ref: _discard_live_thread(ref))
        # A finished QThread is no longer a shutdown hazard. Removing it here
        # also prevents the weak registry from retaining a large collection of
        # already-finished wrappers until application teardown.
        worker.finished.connect(lambda ref=worker_ref: _discard_live_thread(ref))
    except RuntimeError:
        pass
    _install_app_shutdown_hook()


def _install_app_shutdown_hook() -> None:
    """Attach the Qt shutdown hook, including for workers built pre-QApplication."""
    global _SHUTDOWN_HOOK_APP
    app = QCoreApplication.instance()
    if app is None or _SHUTDOWN_HOOK_APP is app:
        return
    try:
        app.aboutToQuit.connect(_shutdown_live_threads)
        _SHUTDOWN_HOOK_APP = app
    except RuntimeError:
        pass


class SafeQThread(QThread):
    """QThread subclass that isolates uncaught exceptions during run() and guarantees safe lifecycle teardown."""
    error_occurred = pyqtSignal(str)
    lifecycle_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lifecycle_state = "created"
        self._cancel_requested = False
        _register_live_thread(self)

    def safe_run(self):
        """Override this method in subclasses instead of run()."""
        pass

    def run(self):
        self._set_lifecycle_state("running")
        try:
            self.safe_run()
            self._set_lifecycle_state(
                "cancelled" if self._cancel_requested or self.isInterruptionRequested() else "completed"
            )
        except Exception as e:
            tb_str = traceback.format_exc()
            logger.error(f"Uncaught exception in background worker {self.__class__.__name__}: {e}\n{tb_str}")
            try:
                self.error_occurred.emit(str(e))
            except Exception:
                pass
            self._set_lifecycle_state("failed")

    def start(self, *args, **kwargs):
        """Install shutdown ownership at the last safe point before execution."""
        _install_app_shutdown_hook()
        return super().start(*args, **kwargs)

    def _set_lifecycle_state(self, state: str) -> None:
        self._lifecycle_state = state
        try:
            self.lifecycle_changed.emit(state)
        except RuntimeError:
            pass

    @property
    def lifecycle_state(self) -> str:
        return self._lifecycle_state

    def request_cancel(self) -> None:
        """Request cooperative cancellation without terminating native code."""
        self._cancel_requested = True
        self._set_lifecycle_state("stopping")
        self.requestInterruption()

    def stop(self, timeout_ms: int = 1500):
        """Gracefully request interruption and wait up to timeout_ms."""
        self.request_cancel()
        if not _is_running(self):
            return True
        if timeout_ms is None:
            self.wait()
            return True
        return bool(self.wait(max(0, int(timeout_ms))))

    def __del__(self):
        """Ensure native C++ thread is stopped before Python garbage collection destroys the wrapper."""
        try:
            if _is_running(self):
                self.requestInterruption()
                # Worker implementations use bounded I/O and are expected to
                # observe interruption. Waiting without a deadline is
                # preferable to allowing Qt to destroy a live QThread (which
                # aborts the process). The application-level hook normally
                # handles this before __del__ is reached.
                if QThread.currentThread() != self:
                    self.wait()
        except Exception:
            pass


class FunctionWorker(SafeQThread):
    """Run one bounded application task with normal QThread ownership.

    Small orchestration tasks previously used ad-hoc daemon threads. They had
    no parent, could not participate in shutdown, and could emit into a window
    that had already closed. This adapter keeps task code simple while giving
    each run a Qt lifetime and a queued completion signal.
    """

    completed = pyqtSignal(object)

    def __init__(self, work: Callable[[], Any], parent=None):
        super().__init__(parent)
        self._work = work

    def safe_run(self):
        result = self._work()
        if not self.isInterruptionRequested():
            self.completed.emit(result)


class TaskSupervisor(QObject):
    """Own one-shot UI tasks for the lifetime of a widget or window.

    A caller supplies only work and a GUI-thread completion callback. The
    supervisor retains QThread instances, logs unexpected failures, and offers
    cooperative shutdown without unsafe ``QThread.terminate()`` calls.
    """

    def __init__(self, owner, task_logger=None, worker_registry=None):
        super().__init__(owner)
        self._workers: list[FunctionWorker] = []
        self._task_logger = task_logger or logger
        self._shutdown_started = False
        # Optional application-wide registry. Feature widgets can retain the
        # convenience API while the owning window still sees every worker
        # during its cooperative shutdown sequence.
        self._worker_registry = worker_registry

    def start(self, name: str, work: Callable[[], Any], on_complete=None) -> FunctionWorker:
        if self._shutdown_started:
            raise RuntimeError("Cannot start a task after its supervisor began shutting down")

        # Do not parent the native QThread to a transient dialog. A dialog can
        # be discarded by Python or WA_DeleteOnClose while a task is still in
        # flight (for example when a second Settings dialog replaces the first
        # one). The supervisor's Python list is the ownership boundary; it can
        # request cancellation and wait before releasing the worker.
        worker = FunctionWorker(work)
        worker.setObjectName(name)
        if on_complete is not None:
            worker.completed.connect(on_complete)
        worker.error_occurred.connect(
            lambda error, task=name: self._task_logger.warning(
                "Background task %s failed: %s", task, error
            )
        )

        worker_ref = weakref.ref(worker)

        def _retire(ref=worker_ref):
            w = ref()
            if w is None:
                return
            if w in self._workers:
                self._workers.remove(w)
            try:
                w.deleteLater()
            except RuntimeError:
                # A late queued finished callback can run after an embedding
                # application's QObject tree has already deleted the wrapper.
                pass

        worker.finished.connect(_retire)
        self._workers.append(worker)
        if self._worker_registry is not None:
            self._worker_registry.register(worker, name)
        worker.start()
        return worker

    def cancel_all(self, wait_ms: int = 100) -> None:
        for worker in list(self._workers):
            if _is_running(worker):
                _request_cancel(worker)
                try:
                    worker.wait(max(0, int(wait_ms)))
                except RuntimeError:
                    pass

    def has_running_tasks(self) -> bool:
        return any(_is_running(worker) for worker in self._workers)

    def running_workers(self) -> list[FunctionWorker]:
        """Return a snapshot for owners that need deferred widget teardown."""
        return [worker for worker in self._workers if _is_running(worker)]

    def shutdown(self, wait_ms: int = 30000) -> None:
        """Cancel and reap every task before the supervisor can disappear."""
        if self._shutdown_started:
            return
        self._shutdown_started = True
        deadline = time.monotonic() + max(0.0, wait_ms / 1000.0)
        for worker in list(self._workers):
            if _is_running(worker):
                _request_cancel(worker)

        deadline_reported = False
        while True:
            running = [worker for worker in list(self._workers) if _is_running(worker)]
            if not running:
                return
            now = time.monotonic()
            if now >= deadline and not deadline_reported:
                deadline_reported = True
                self._task_logger.error(
                    "Task supervisor is waiting for non-cooperative task(s): %s",
                    ", ".join(worker.objectName() or worker.__class__.__name__ for worker in running),
                )
            for worker in running:
                try:
                    worker.wait(250)
                except RuntimeError:
                    pass

    def __del__(self):
        # This is intentionally a final safety net for owners that are
        # dropped without close()/reject() (common in tests and integrations).
        try:
            self.shutdown(30000)
        except Exception:
            pass


class WorkerSupervisor(QObject):
    """Single owner for application-owned QThread lifecycles.

    Semantic lists such as metadata workers and achievement workers are useful
    to their features, but shutdown must not depend on every feature updating
    the same list correctly.  This supervisor is the authoritative registry.
    Normal feature-level waits are deliberately bounded so the UI stays
    responsive. Final shutdown uses cooperative cancellation and an unbounded
    reap because forcing a Python/Qt thread to stop can corrupt save or
    database operations, while releasing a live QThread is fatal in Qt.
    """

    worker_registered = pyqtSignal(object)
    worker_finished = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: list[QThread] = []
        self._names: dict[QThread, str] = {}

    def register(self, worker: QThread, name: str = "") -> bool:
        if worker is None or worker in self._workers:
            return False
        # WorkerSupervisor is the lifetime owner. Detach the QThread from a
        # window/dialog QObject tree so a forgotten close path cannot make Qt
        # destroy a live native thread while tearing down that tree. The
        # supervisor's Python list and the process-wide SafeQThread registry
        # retain it until it has finished.
        try:
            if worker.parent() is not None:
                worker.setParent(None)
        except RuntimeError:
            return False
        worker_name = name or worker.objectName() or worker.__class__.__name__
        self._workers.append(worker)
        self._names[worker] = worker_name
        worker_ref = weakref.ref(worker)
        def _finished(ref=worker_ref):
            worker_obj = ref()
            if worker_obj is not None:
                self._on_finished(worker_obj)

        worker.finished.connect(_finished)
        # Qt's documented cleanup pattern for QThread subclasses. The
        # supervisor removes its Python references first; deleteLater then
        # releases the QObject on its owning thread after the native thread
        # has stopped, preventing both QObject accumulation and live-thread
        # destruction during parent teardown.
        worker.finished.connect(worker.deleteLater)
        self.worker_registered.emit(worker)
        return True

    def _on_finished(self, worker: QThread) -> None:
        self._names.pop(worker, None)
        if worker in self._workers:
            self._workers.remove(worker)
        self.worker_finished.emit(worker)

    def workers(self, running_only: bool = False) -> list[QThread]:
        workers = list(self._workers)
        if running_only:
            workers = [worker for worker in workers if _is_running(worker)]
        return workers

    def describe(self, worker: QThread) -> str:
        return self._names.get(worker, worker.objectName() or worker.__class__.__name__)

    def cancel_all(self) -> None:
        for worker in self.workers(running_only=True):
            _request_cancel(worker)

    def wait(self, timeout_ms: int = 100) -> list[QThread]:
        for worker in self.workers(running_only=True):
            try:
                worker.wait(max(0, int(timeout_ms)))
            except RuntimeError:
                continue
        return self.workers(running_only=True)

    def has_running_workers(self) -> bool:
        return bool(self.workers(running_only=True))

    def shutdown(self) -> None:
        """Cancel and synchronously reap workers before this owner disappears."""
        self.cancel_all()
        while self.has_running_workers():
            self.wait(250)

    def __del__(self):
        # MainWindow normally reaches this boundary with an empty registry,
        # but integrations/tests can drop the supervisor directly. Never let
        # QObject parent destruction release a live QThread.
        try:
            self.shutdown()
        except Exception:
            pass
