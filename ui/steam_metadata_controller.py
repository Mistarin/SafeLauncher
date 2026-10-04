"""Steam metadata subscriptions with explicit presentation callbacks."""

from PyQt6.QtCore import QObject
from core.logger import get_logger
from core.request_contracts import RequestKey, RequestPriority, ResourceStatus
from core.steam_ids import normalize_steam_app_id
from core.game_names import is_placeholder_game_name
from ui.resource_binding import bind_resource, bind_request

logger = get_logger("UI.SteamMetadata")


class SteamMetadataController(QObject):
    def __init__(self, *, manager, service, coordinator, library_service,
                 on_build, on_failure, on_offline, on_tags, on_network_status,
                 refresh_library, on_description, accepts_work, parent=None):
        super().__init__(parent)
        self.request_manager, self.service, self.coordinator = manager, service, coordinator
        self.library_service = library_service
        self.on_build, self.on_failure, self.on_offline = on_build, on_failure, on_offline
        self.on_tags, self.on_network_status = on_tags, on_network_status
        self.refresh_library, self.on_description = refresh_library, on_description
        self.accepts_work = accepts_work
        self.name_bindings = {}
        self._description_binding = None
        self._description_generation = 0
        self._subscription_generation = 0
        self._closed = False

    def request_build(
        self,
        game_id: int,
        steam_id: str,
        local_build_id: str,
        local_build_date: int,
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
        schedule: bool = True,
    ) -> bool:
        """Subscribe one game to a shared cached public Steam build."""
        if self.request_manager is None or self._closed or not self.accepts_work():
            return False
        app_id = normalize_steam_app_id(steam_id)
        if not app_id:
            return False
        plan = self.coordinator.prepare_build(
            game_id,
            app_id,
            local_build_id,
            local_build_date,
            priority=priority,
        )
        key = plan.key
        if self.coordinator.binding("build", key) is None:
            binding = bind_resource(
                self.request_manager,
                key,
                lambda result, key=key, generation=self._subscription_generation:
                    self.handle_build_state(key, result)
                    if generation == self._subscription_generation else None,
                self,
                cancel_on_close=True,
            )
            self.coordinator.attach_binding("build", plan, binding)

        if not schedule:
            return True

        try:
            self.service.request_build(
                app_id,
                priority=priority,
                tag="game_update_check",
            )
        except Exception as exc:
            logger.debug("Managed Steam build request failed to start for %s: %s", app_id, exc)
            self.on_failure(int(game_id), str(exc))
        return True


    def handle_build_state(self, key: RequestKey, result) -> None:
        if self._closed or not self.accepts_work():
            return
        if result.status in {ResourceStatus.READY, ResourceStatus.STALE}:
            # ``READY`` can still be a cache hit.  Preserve that provenance so
            # an offline launch never presents an old comparison as a live
            # Steam answer.  ``updated_at`` is the cache's stored timestamp for
            # cached results and the completion timestamp for live results.
            source = "cached" if bool(getattr(result, "from_cache", False)) else "live"
            checked_at = float(getattr(result, "updated_at", 0.0) or 0.0)
            value = result.value
            if isinstance(value, (tuple, list)) and len(value) >= 2:
                latest_build_id = str(value[0] or "")
                try:
                    latest_build_date = int(value[1] or 0)
                except (TypeError, ValueError, OverflowError):
                    latest_build_date = 0
                if not latest_build_id:
                    for game_id in tuple(self.coordinator.build_games(key)):
                        self.on_failure(
                            game_id,
                            "Steam returned no public branch build for this AppID",
                        )
                    return
                for game_id, (local_build_id, local_build_date) in tuple(
                    self.coordinator.build_games(key).items()
                ):
                    if local_build_id:
                        is_update = latest_build_id != local_build_id
                    elif local_build_date > 0:
                        is_update = latest_build_date > local_build_date
                    else:
                        self.on_failure(
                            game_id,
                            "No installed Steam build reference; enter a current Build ID or date",
                        )
                        continue
                    self.on_build(
                        game_id,
                        latest_build_id,
                        latest_build_date,
                        is_update,
                        source=source,
                        checked_at=checked_at,
                    )
            return
        if result.status == ResourceStatus.OFFLINE:
            self.on_network_status(
                True,
                "No internet connection. Showing the last known game-version result when available.",
            )
            for game_id in tuple(self.coordinator.build_games(key)):
                self.on_offline(game_id)
        elif result.status not in {ResourceStatus.READY, ResourceStatus.STALE, ResourceStatus.CANCELLED}:
            reason = str(result.error or "Steam build check failed")
            for game_id in tuple(self.coordinator.build_games(key)):
                self.on_failure(game_id, reason)


    def request_tags(
        self,
        game_id: int,
        game_name: str,
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
    ) -> bool:
        """Subscribe one game to a shared cached Steam tag lookup."""
        if self.request_manager is None or self._closed or not self.accepts_work():
            return False
        identity = str(game_name or "").strip().casefold()
        if not identity:
            return False
        plan = self.coordinator.prepare_tags(
            game_id,
            game_name,
            priority=priority,
        )
        key = plan.key
        if self.coordinator.binding("tag", key) is None:
            binding = bind_resource(
                self.request_manager,
                key,
                lambda result, key=key, generation=self._subscription_generation:
                    self.handle_tag_state(key, result)
                    if generation == self._subscription_generation else None,
                self,
                cancel_on_close=True,
            )
            self.coordinator.attach_binding("tag", plan, binding)

        try:
            self.service.request_tags(
                game_name,
                priority=priority,
                tag="game_tags",
            )
        except Exception as exc:
            logger.debug("Managed Steam tag request failed to start for %s: %s", game_name, exc)
            self.on_tags(int(game_id), [], "")
        return True


    def handle_tag_state(self, key: RequestKey, result) -> None:
        if self._closed or not self.accepts_work():
            return
        if result.status in {ResourceStatus.READY, ResourceStatus.STALE}:
            value = result.value
            if isinstance(value, (tuple, list)) and len(value) >= 2:
                tags = value[0] if isinstance(value[0], list) else []
                app_id = str(value[1] or "")
                for game_id in self.coordinator.tag_game_ids(key):
                    self.on_tags(game_id, tags, app_id)
        elif result.status in {
            ResourceStatus.ERROR,
            ResourceStatus.OFFLINE,
            ResourceStatus.UNAVAILABLE,
            ResourceStatus.AUTHENTICATION_REQUIRED,
            ResourceStatus.PERMISSION_DENIED,
            ResourceStatus.CONFLICT,
        }:
            for game_id in self.coordinator.tag_game_ids(key):
                self.on_tags(game_id, [], "")


    def close_bindings(self) -> None:
        """Cancel this subscription generation without permanently closing the controller."""
        self._subscription_generation += 1
        self._description_generation += 1
        for binding in self.coordinator.close():
            binding.close()
            binding.deleteLater()
        for binding in tuple(getattr(self, "name_bindings", {}).values()):
            try:
                binding.close()
                binding.deleteLater()
            except RuntimeError:
                pass
        getattr(self, "name_bindings", {}).clear()
        if self._description_binding is not None:
            self._description_binding.close()
            self._description_binding.deleteLater()
            self._description_binding = None


    def resolve_missing_names(self, *, priority: RequestPriority = RequestPriority.BACKGROUND) -> None:
        """Repair archived/cloud-only Steam titles through cached App Details."""
        if self.request_manager is None or self._closed or not self.accepts_work():
            return
        for game in self.library_service.read_games():
            app_id = str(game.steam_id or "").strip()
            if not app_id or app_id == "0" or not is_placeholder_game_name(game.name, app_id):
                continue
            key = self.service.app_details_key(app_id)
            if key in self.name_bindings:
                continue
            try:
                handle = self.service.request_app_details(
                    app_id,
                    priority=priority,
                    tag="repair_game_name",
                )
                binding = bind_request(
                    self.request_manager,
                    handle,
                    lambda result, key=key, generation=self._subscription_generation:
                        self.handle_name_state(key, result)
                        if generation == self._subscription_generation else None,
                    self,
                    cancel_on_close=True,
                )
                self.name_bindings[key] = binding
            except Exception as exc:
                logger.debug("Managed Steam App Details request failed for %s: %s", app_id, exc)


    def handle_name_state(self, key: RequestKey, result) -> None:
        if self._closed or not self.accepts_work():
            return
        """Apply only verified Steam titles to matching placeholder records."""
        if result.status in {ResourceStatus.READY, ResourceStatus.STALE}:
            details = result.value if isinstance(result.value, dict) else {}
            name = str(details.get("name", "") or "").strip()
            if name:
                app_id = key.identity.rsplit(":", 1)[-1]
                changed = False
                for game in self.library_service.read_games():
                    if str(game.steam_id or "").strip() != app_id:
                        continue
                    changed = self.library_service.apply_metadata_name(game.id, name) or changed
                if changed:
                    self.refresh_library()
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
            binding = self.name_bindings.pop(key, None)
            if binding is not None:
                try:
                    binding.close()
                    binding.deleteLater()
                except RuntimeError:
                    pass


    def request_description(self, game_id, name, app_id):
        if self._closed or not self.accepts_work():
            return
        self._description_generation += 1
        generation = self._description_generation
        if self._description_binding is not None:
            self._description_binding.close()
            self._description_binding.deleteLater()
            self._description_binding = None
        handle = self.service.request_description(name, app_id)
        self._description_binding = bind_request(
            self.request_manager, handle,
            lambda result: self.handle_description(game_id, generation, result),
            self, cancel_on_close=True,
        )

    def handle_description(self, game_id, generation, result):
        if self._closed or generation != self._description_generation or not self.accepts_work():
            return
        if result.status == ResourceStatus.LOADING:
            return
        details = result.value if isinstance(result.value, dict) else {}
        description = details.get("short_description") or details.get("detailed_description") or ""
        tags = []
        for group in (details.get("genres", []), details.get("categories", [])):
            if isinstance(group, list):
                tags.extend(str(item.get("description", "")) for item in group if isinstance(item, dict) and item.get("description"))
        self.on_description(game_id, description, list(dict.fromkeys(tags))[:6])

    def dispose(self):
        self._closed = True
        self.close_bindings()
