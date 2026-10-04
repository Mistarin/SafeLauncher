"""UI-side lifecycle for deduplicated library artwork requests."""

from __future__ import annotations

import logging
import os

from core.artwork_resource_service import ArtworkTarget
from core.compatibility_worker_index import CompatibilityWorkerIndex
from core.request_contracts import RequestPriority, ResourceStatus
from ui.resource_binding import bind_resource
from ui.threads import BannerAutoFetcher


_TERMINAL = {
    ResourceStatus.READY,
    ResourceStatus.STALE,
    ResourceStatus.ERROR,
    ResourceStatus.OFFLINE,
    ResourceStatus.UNAVAILABLE,
    ResourceStatus.AUTHENTICATION_REQUIRED,
    ResourceStatus.PERMISSION_DENIED,
    ResourceStatus.CONFLICT,
    ResourceStatus.CANCELLED,
}


class ArtworkController:
    """Own artwork request subscriptions and compatibility fetch lifetimes.

    The controller handles request scheduling, per-game attempt guards,
    request bindings, and legacy fetch queue cleanup. Callbacks are the
    presentation edge: they apply resolved paths to the active library UI.
    """

    def __init__(
        self,
        service,
        coordinator,
        *,
        binding_parent=None,
        network_allowed,
        register_worker,
        on_auto_artwork,
        on_hero_artwork,
        on_icon_artwork,
        logger: logging.Logger | None = None,
        max_compat_fetchers: int = 3,
    ) -> None:
        self.service = service
        self.coordinator = coordinator
        self.binding_parent = binding_parent
        self.request_manager = service.request_manager
        self.client = service.client
        self.network_allowed = network_allowed
        self.register_worker = register_worker
        self.on_auto_artwork = on_auto_artwork
        self.on_hero_artwork = on_hero_artwork
        self.on_icon_artwork = on_icon_artwork
        self.logger = logger or logging.getLogger(__name__)
        self.max_compat_fetchers = max(1, int(max_compat_fetchers))
        self.compat_fetchers = CompatibilityWorkerIndex("artwork-active-fallback")
        self._pending_compat_fetchers = CompatibilityWorkerIndex("artwork-pending-fallback")
        self._compat_attempted: set[int] = set()

    def request_auto(
        self,
        game_id: int,
        game_name: str,
        exe_path: str,
        steam_id: str,
        *,
        priority: RequestPriority = RequestPriority.BACKGROUND,
    ) -> None:
        if self.request_manager is not None:
            self._request_managed_auto(game_id, game_name, exe_path, steam_id, priority)
            return

        game_id = int(game_id)
        if game_id in self._compat_attempted:
            return
        self._compat_attempted.add(game_id)
        fetcher = BannerAutoFetcher(
            game_id,
            game_name,
            self.client,
            exe_path=exe_path,
            steam_id=steam_id,
        )
        fetcher.banner_auto_downloaded.connect(self.on_auto_artwork)
        fetcher.finished.connect(lambda f=fetcher: self._cleanup_compat_fetcher(f))
        if len(self.compat_fetchers) < self.max_compat_fetchers:
            self.compat_fetchers.append(fetcher)
            self.register_worker(fetcher)
            fetcher.start()
        else:
            self._pending_compat_fetchers.append(fetcher)

    def request_hero(
        self,
        game_id: int,
        game_name: str,
        steam_id: str,
        exe_path: str,
        *,
        priority: RequestPriority,
    ) -> None:
        self._request_single("hero", game_id, game_name, steam_id, exe_path, priority)

    def request_icon(
        self,
        game_id: int,
        game_name: str,
        steam_id: str,
        exe_path: str,
        *,
        priority: RequestPriority,
    ) -> None:
        self._request_single("icon", game_id, game_name, steam_id, exe_path, priority)

    def _target(self, game_id: int, game_name: str, steam_id: str, exe_path: str):
        return ArtworkTarget(
            int(game_id),
            str(game_name or ""),
            str(steam_id or ""),
            str(exe_path or ""),
        )

    def _request_managed_auto(self, game_id, game_name, exe_path, steam_id, priority):
        target = self._target(game_id, game_name, steam_id, exe_path)
        plan = self.coordinator.prepare(
            "auto", target, priority=priority, mark_attempted=True
        )
        if plan is None:
            return
        key = plan.key
        if not plan.new_binding:
            current = self.request_manager.state(key)
            if current.usable and isinstance(current.value, (tuple, list)):
                self._apply_auto(game_id, current.value)
            return

        binding = bind_resource(
            self.request_manager,
            key,
            lambda result, request_key=key: self._on_auto_state(request_key, result),
            self.binding_parent,
            cancel_on_close=True,
        )
        self.coordinator.attach_binding(plan, binding)
        try:
            self.coordinator.request(plan)
        except Exception:
            self._discard("auto", key)
            self.logger.exception("Could not request automatic artwork for game %s", game_id)

    def _apply_auto(self, game_id: int, value) -> None:
        if not isinstance(value, (tuple, list)) or len(value) < 3:
            return
        self.on_auto_artwork(
            int(game_id), str(value[0] or ""), int(value[1] or 0), str(value[2] or "")
        )

    def _on_auto_state(self, key, result) -> None:
        if result.status in {ResourceStatus.READY, ResourceStatus.STALE}:
            for game_id in self.coordinator.game_ids("auto", key):
                self._apply_auto(game_id, result.value)
        elif result.status != ResourceStatus.LOADING:
            self.logger.debug(
                "Managed automatic artwork request failed for %s: %s",
                key,
                result.error or result.status.value,
            )
        if result.status in _TERMINAL:
            self._discard("auto", key)

    def _request_single(self, kind, game_id, game_name, steam_id, exe_path, priority):
        if self.request_manager is None:
            return
        target = self._target(game_id, game_name, steam_id, exe_path)
        plan = self.coordinator.prepare(
            kind, target, priority=priority, mark_attempted=True
        )
        if plan is None:
            return
        key = plan.key
        current_binding = self.coordinator.binding(kind, key)
        apply_path = self.on_hero_artwork if kind == "hero" else self.on_icon_artwork
        if current_binding is not None:
            current = self.request_manager.state(key)
            path = str(current.value or "") if current.usable else ""
            if path and os.path.exists(path):
                apply_path(int(game_id), path)
            return

        binding = bind_resource(
            self.request_manager,
            key,
            lambda result, request_key=key, artwork_kind=kind:
                self._on_single_state(artwork_kind, request_key, result),
            self.binding_parent,
            cancel_on_close=True,
        )
        self.coordinator.attach_binding(plan, binding)
        try:
            self.coordinator.request(plan)
        except Exception:
            self._discard(kind, key)
            self.logger.exception("Could not request %s artwork for game %s", kind, game_id)

    def _on_single_state(self, kind, key, result) -> None:
        if result.status not in {ResourceStatus.READY, ResourceStatus.STALE}:
            return
        path = str(result.value or "")
        if not path or not os.path.exists(path):
            return
        callback = self.on_hero_artwork if kind == "hero" else self.on_icon_artwork
        for game_id in self.coordinator.game_ids(kind, key):
            callback(game_id, path)

    def _discard(self, kind, key) -> None:
        binding = self.coordinator.discard(kind, key)
        if binding is not None:
            binding.close()
            binding.deleteLater()

    def _cleanup_compat_fetcher(self, fetcher) -> None:
        if fetcher in self.compat_fetchers:
            self.compat_fetchers.remove(fetcher)
        self._start_next_compat_fetcher()

    def _start_next_compat_fetcher(self) -> None:
        if not self.network_allowed():
            self._pending_compat_fetchers.clear()
            return
        while self._pending_compat_fetchers:
            if len(self.compat_fetchers) >= self.max_compat_fetchers:
                return
            fetcher = self._pending_compat_fetchers.pop(0)
            if fetcher.isInterruptionRequested():
                continue
            self.compat_fetchers.append(fetcher)
            self.register_worker(fetcher)
            fetcher.start()
            return

    def cancel_compatibility_fetches(self) -> None:
        """Stop queued and running manager-less fallback artwork work."""
        self._pending_compat_fetchers.clear()
        for fetcher in tuple(self.compat_fetchers):
            try:
                if fetcher.isRunning():
                    fetcher.requestInterruption()
            except RuntimeError:
                continue

    def close_bindings(self) -> None:
        for binding in self.coordinator.close():
            binding.close()
            binding.deleteLater()

    def shutdown(self) -> None:
        self.cancel_compatibility_fetches()
        self.close_bindings()


__all__ = ["ArtworkController"]
