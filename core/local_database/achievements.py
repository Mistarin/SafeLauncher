import json
import math
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from core.logger import get_logger
from core.game_names import (
    MAX_GAME_NAME_LENGTH, fallback_game_name, display_name_key,
    is_identity_placeholder_name, local_profile_identity, meaningful_game_name,
    preferred_game_name,
)
from .records import GameRecord, GAME_COLUMNS, profile_identity
from .validation import (
    _ACHIEVEMENT_DB_LOCK, _ACHIEVEMENT_APP_RE, _valid_achievement_api_name,
    _normalise_achievement_provenance, _safe_achievement_timestamp,
)
logger = get_logger("Database")

class AchievementRepository:
    def __init__(self, session):
        self.session = session

    @property
    def conn(self):
        return self.session.conn

    @property
    def db_path(self):
        return self.session.db_path

    def save_achievement_schema(self, game_id: int, app_id: str, achievements: List[dict]) -> int:
        """Insert or update achievement schema definitions for a game, preserving existing unlocked state."""
        app_id = str(app_id or "").strip()
        if not _ACHIEVEMENT_APP_RE.fullmatch(app_id) or app_id == "0":
            return 0
        if not achievements:
            return 0
        inserted = 0
        valid_names = set()
        try:
            with _ACHIEVEMENT_DB_LOCK, self.conn:
                for ach in achievements:
                    api_name = _valid_achievement_api_name(ach.get("api_name", ""))
                    if not api_name:
                        continue
                    valid_names.add(api_name)
                    display_name = str(ach.get("display_name", api_name)).strip()
                    desc = str(ach.get("description", "")).strip()
                    icon_path = str(ach.get("icon_path") or ach.get("icon_url", "")).strip()
                    icongray_path = str(ach.get("icongray_path") or ach.get("icongray_url", "")).strip()
                    hidden = int(ach.get("hidden", 0))

                    self.conn.execute("""
                        INSERT INTO achievements (game_id, app_id, api_name, display_name, description, icon_path, icongray_path, hidden)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(game_id, api_name) DO UPDATE SET
                            app_id = excluded.app_id,
                            display_name = excluded.display_name,
                            description = excluded.description,
                            icon_path = CASE WHEN excluded.icon_path != '' THEN excluded.icon_path ELSE achievements.icon_path END,
                            icongray_path = CASE WHEN excluded.icongray_path != '' THEN excluded.icongray_path ELSE achievements.icongray_path END,
                            hidden = excluded.hidden
                    """, (game_id, str(app_id), api_name, display_name, desc, icon_path, icongray_path, hidden))
                    inserted += 1
                # A state file can be observed before the network/local
                # schema arrives. Promote only matching pending records.
                if valid_names:
                    placeholders = ",".join("?" for _ in valid_names)
                    self.conn.execute(
                        f"UPDATE achievement_profile SET validation_state = 'validated' WHERE app_id = ? AND api_name IN ({placeholders})",
                        (str(app_id), *sorted(valid_names)),
                    )
            # Rehydrate a newly added/re-added game from the account-wide
            # append-only ledger without allowing the schema to delete it.
            self.project_profile_achievements(game_id, app_id)
            return inserted
        except Exception as e:
            logger.error(f"Error saving achievement schema for game {game_id}: {e}")
            return 0

    def unlock_achievement(
        self, game_id: int, api_name: str, unlock_time: float = 0.0,
        *, provenance: str = "local_emulator", verified: bool = False,
        source_format: str = "", source_path: str = "",
    ) -> bool:
        """Mark a schema-backed achievement as unlocked with provenance."""
        api_name = _valid_achievement_api_name(api_name)
        if not api_name:
            return False
        provenance = _normalise_achievement_provenance(provenance)
        verified = provenance == "steam_verified"
        unlock_time = _safe_achievement_timestamp(unlock_time)
        try:
            app_row = self.conn.execute("SELECT app_id FROM achievements WHERE game_id = ? AND api_name = ?", (game_id, api_name)).fetchone()
            if app_row and app_row[0]:
                profile_row = self.conn.execute(
                    "SELECT provenance, verified FROM achievement_profile WHERE app_id = ? AND api_name = ?",
                    (str(app_row[0]), api_name),
                ).fetchone()
                if profile_row and bool(profile_row[1]):
                    provenance = _normalise_achievement_provenance(profile_row[0] or "steam_verified")
                    verified = provenance == "steam_verified"
            with _ACHIEVEMENT_DB_LOCK, self.conn:
                cursor = self.conn.execute("""
                    UPDATE achievements
                    SET unlocked = 1, unlock_time = ?, unlock_provenance = ?,
                        unlock_verified = ?, unlock_source_format = ?, unlock_source_path = ?, notification_sent = 0
                    WHERE game_id = ? AND api_name = ? AND unlocked = 0
                """, (unlock_time, provenance, int(bool(verified)), source_format or "", source_path or "", game_id, api_name))
                changed = cursor.rowcount > 0
            if changed:
                if app_row and app_row[0]:
                    self.merge_profile_unlocks(
                        str(app_row[0]), {api_name: unlock_time},
                        provenance=provenance, verified=verified,
                        validation_state="validated", source_format=source_format,
                        source_path=source_path,
                    )
            return changed
        except Exception as e:
            logger.error(f"Error unlocking achievement {api_name} for game {game_id}: {e}")
            return False

    def unlock_achievements_batch(
        self, game_id: int, unlocks: Dict[str, float], *, app_id: str = "",
        provenance: str = "local_emulator", verified: bool = False,
        source_format: str = "", source_path: str = "",
    ) -> int:
        """Record a batch, promoting only schema-backed names to visible rows.

        Unknown names are retained in the profile as ``pending_schema`` when
        an AppID is available, but never affect per-game counts.
        """
        if not unlocks:
            return 0
        provenance = _normalise_achievement_provenance(provenance)
        verified = provenance == "steam_verified"
        now = time.time()
        normalized = {}
        for api_name, raw_value in unlocks.items():
            name = _valid_achievement_api_name(api_name)
            if not name:
                continue
            raw_time = raw_value.get("unlock_time", 0) if isinstance(raw_value, dict) else raw_value
            try:
                stamp = float(raw_time or 0)
            except (TypeError, ValueError, OverflowError):
                stamp = 0.0
            normalized[name] = _safe_achievement_timestamp(stamp, now)
        if not normalized:
            return 0
        if app_id and (not _ACHIEVEMENT_APP_RE.fullmatch(str(app_id).strip()) or str(app_id).strip() == "0"):
            return 0
        try:
            schema_rows = self.conn.execute(
                "SELECT api_name, app_id FROM achievements WHERE game_id = ?", (game_id,)
            ).fetchall()
            known_names = {str(row[0]) for row in schema_rows}
            if not app_id:
                app_id = str(schema_rows[0][1] or "") if schema_rows else ""
            params = [
                (stamp, provenance, int(bool(verified)), source_format or "", source_path or "", game_id, api_name)
                for api_name, stamp in normalized.items() if api_name in known_names
            ]
            with _ACHIEVEMENT_DB_LOCK, self.conn:
                cursor = self.conn.executemany("""
                    UPDATE achievements
                    SET unlocked = 1, unlock_time = ?, unlock_provenance = ?,
                        unlock_verified = ?, unlock_source_format = ?, unlock_source_path = ?
                    WHERE game_id = ? AND api_name = ? AND unlocked = 0
                """, params)
                changed = cursor.rowcount
            if app_id:
                self.merge_profile_unlocks(
                    app_id, normalized, provenance=provenance, verified=verified,
                    validation_state="validated", source_format=source_format,
                    source_path=source_path, allowed_api_names=known_names,
                )
            return changed
        except Exception as e:
            logger.error(f"Error in batch achievement unlock for game {game_id}: {e}")
            return 0

    def claim_achievement_notification(self, game_id: int, api_name: str) -> bool:
        """Atomically claim the one user-facing notification for an unlock."""
        api_name = _valid_achievement_api_name(api_name)
        if not api_name:
            return False
        try:
            with _ACHIEVEMENT_DB_LOCK, self.conn:
                cursor = self.conn.execute("""
                    UPDATE achievements SET notification_sent = 1
                    WHERE game_id = ? AND api_name = ? AND unlocked = 1 AND notification_sent = 0
                """, (game_id, api_name))
                return cursor.rowcount > 0
        except Exception as e:
            logger.debug(f"Could not claim achievement notification {api_name}: {e}")
            return False

    def arm_achievement_notification(self, game_id: int, api_name: str) -> None:
        """Mark a schema-late live event as pending user notification."""
        api_name = _valid_achievement_api_name(api_name)
        if not api_name:
            return
        try:
            with _ACHIEVEMENT_DB_LOCK, self.conn:
                self.conn.execute(
                    "UPDATE achievements SET notification_sent = 0 WHERE game_id = ? AND api_name = ? AND unlocked = 1",
                    (game_id, api_name),
                )
        except Exception as e:
            logger.debug(f"Could not arm achievement notification {api_name}: {e}")

    def merge_profile_unlocks(
        self, app_id: str, unlocks: Dict[str, float], *, provenance: str = "local_emulator",
        verified: bool = False, validation_state: str = "validated", source_format: str = "",
        source_path: str = "", allowed_api_names: Optional[set[str]] = None,
    ) -> int:
        """Append observations to the account ledger without deleting proof."""
        app_id = str(app_id or "").strip()
        if not _ACHIEVEMENT_APP_RE.fullmatch(app_id) or app_id == "0" or not unlocks:
            return 0
        provenance = _normalise_achievement_provenance(provenance)
        verified = provenance == "steam_verified"
        now = time.time()
        changed = 0
        try:
            with _ACHIEVEMENT_DB_LOCK, self.conn:
                for api_name, raw_value in unlocks.items():
                    name = _valid_achievement_api_name(api_name)
                    if not name:
                        continue
                    is_record = isinstance(raw_value, dict)
                    value = raw_value if is_record else {}
                    incoming_provenance = _normalise_achievement_provenance(
                        value.get("provenance", provenance)
                    )
                    incoming_verified = incoming_provenance == "steam_verified"
                    incoming_state = str(value.get("validation_state", validation_state) or validation_state)
                    if allowed_api_names is not None and name not in allowed_api_names:
                        incoming_state = "pending_schema"
                    if incoming_state not in {"validated", "pending_schema"}:
                        incoming_state = "pending_schema"
                    raw_time = value.get("unlock_time", 0) if is_record else raw_value
                    try:
                        stamp = float(raw_time or 0) if raw_time else 0.0
                    except (TypeError, ValueError, OverflowError):
                        stamp = 0.0
                    stamp = _safe_achievement_timestamp(stamp, now)
                    existing = self.conn.execute(
                        "SELECT unlock_time, provenance, verified, validation_state, source_format, source_path FROM achievement_profile WHERE app_id = ? AND api_name = ?",
                        (app_id, name),
                    ).fetchone()
                    if existing:
                        old_time, old_provenance, old_verified, old_state, old_format, old_path = existing
                        from core.achievement_models import provenance_priority
                        old_stamp = _safe_achievement_timestamp(old_time, 0.0)
                        old_priority = provenance_priority(str(old_provenance or "unknown"))
                        new_priority = provenance_priority(incoming_provenance)
                        chosen_provenance = incoming_provenance if new_priority >= old_priority else str(old_provenance or incoming_provenance)
                        chosen_format = source_format or (value.get("source_format", "") if isinstance(value, dict) else "") or old_format or ""
                        chosen_path = source_path or (value.get("source_path", "") if isinstance(value, dict) else "") or old_path or ""
                        chosen_state = "validated" if str(old_state) == "validated" or incoming_state == "validated" else "pending_schema"
                        chosen_time = min(old_stamp, stamp) if old_stamp > 0 and stamp > 0 else max(old_stamp, stamp)
                        chosen_verified = chosen_provenance == "steam_verified"
                        self.conn.execute("""
                            UPDATE achievement_profile
                            SET unlock_time = ?, last_seen_at = ?, provenance = ?, verified = ?,
                                validation_state = ?, source_format = ?, source_path = ?
                            WHERE app_id = ? AND api_name = ?
                        """, (chosen_time, now, chosen_provenance, int(chosen_verified), chosen_state, chosen_format, chosen_path, app_id, name))
                        changed += int(
                            old_stamp != chosen_time
                            or str(old_provenance or "") != chosen_provenance
                            or bool(old_verified) != chosen_verified
                            or str(old_state or "") != chosen_state
                            or str(old_format or "") != chosen_format
                            or str(old_path or "") != chosen_path
                        )
                    else:
                        self.conn.execute("""
                            INSERT INTO achievement_profile
                              (app_id, api_name, unlock_time, first_seen_at, last_seen_at, provenance, verified, validation_state, source_format, source_path)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (app_id, name, stamp, now, now, incoming_provenance, int(incoming_verified), incoming_state, source_format or (value.get("source_format", "") if isinstance(value, dict) else ""), source_path or (value.get("source_path", "") if isinstance(value, dict) else "")))
                        changed += 1
            return changed
        except Exception as e:
            logger.error(f"Error merging achievement profile for AppID {app_id}: {e}")
            return 0

    def record_achievement_state(
        self, game_id: int, app_id: str, state: Dict[str, float], *,
        pending: Optional[Dict[str, float]] = None,
        provenance: str = "local_emulator", verified: bool = False,
        source_format: str = "", source_path: str = "",
        provenance_by_name: Optional[Dict[str, str]] = None,
        verified_by_name: Optional[Dict[str, bool]] = None,
    ) -> int:
        """Persist one resolver snapshot atomically at the two DB layers."""
        app_id = str(app_id or "").strip()
        if not _ACHIEVEMENT_APP_RE.fullmatch(app_id) or app_id == "0":
            return 0
        provenance = _normalise_achievement_provenance(provenance)
        verified = provenance == "steam_verified"
        state = state or {}
        pending = pending or {}
        provenance_by_name = provenance_by_name or {}
        verified_by_name = verified_by_name or {}
        try:
            schema_names = {
                str(row[0]) for row in self.conn.execute(
                    "SELECT api_name FROM achievements WHERE game_id = ?", (game_id,)
                ).fetchall()
            }
            known = {name: stamp for name, stamp in state.items() if name in schema_names}
            unknown = {name: stamp for name, stamp in state.items() if name not in schema_names}
            unknown.update(pending)
            total = 0
            # Update each known item with its own source. Official Steam data
            # must be able to upgrade a previous local/unverified projection.
            with _ACHIEVEMENT_DB_LOCK, self.conn:
                for api_name, raw_time in known.items():
                    name = _valid_achievement_api_name(api_name)
                    if not name:
                        continue
                    try:
                        stamp = float(raw_time or 0)
                    except (TypeError, ValueError, OverflowError):
                        stamp = 0.0
                    stamp = _safe_achievement_timestamp(stamp)
                    item_provenance = _normalise_achievement_provenance(
                        provenance_by_name.get(name, provenance) or provenance
                    )
                    item_verified = item_provenance == "steam_verified"
                    total += self.conn.execute("""
                        UPDATE achievements
                        SET unlocked = 1,
                            unlock_time = CASE WHEN unlock_time <= 0 THEN ? ELSE MIN(unlock_time, ?) END,
                            unlock_provenance = CASE WHEN ? OR unlock_verified = 0 THEN ? ELSE unlock_provenance END,
                            unlock_verified = MAX(unlock_verified, ?),
                            unlock_source_format = CASE WHEN ? != '' THEN ? ELSE unlock_source_format END,
                            unlock_source_path = CASE WHEN ? != '' THEN ? ELSE unlock_source_path END,
                            notification_sent = CASE WHEN unlocked = 1 THEN notification_sent ELSE 1 END
                        WHERE game_id = ? AND api_name = ?
                    """, (stamp, stamp, int(item_verified), item_provenance, int(item_verified), source_format or "", source_format or "", source_path or "", source_path or "", game_id, name)).rowcount
            observations = {
                name: {
                    "unlock_time": stamp,
                    "provenance": provenance_by_name.get(name, provenance),
                    "verified": _normalise_achievement_provenance(
                        provenance_by_name.get(name, provenance) or provenance
                    ) == "steam_verified",
                    "validation_state": "validated",
                    "source_format": source_format,
                    "source_path": source_path,
                }
                for name, stamp in known.items()
            }
            observations.update({
                name: {
                    "unlock_time": stamp,
                    "provenance": provenance_by_name.get(name, provenance),
                    "verified": _normalise_achievement_provenance(
                        provenance_by_name.get(name, provenance) or provenance
                    ) == "steam_verified",
                    "validation_state": "pending_schema",
                    "source_format": source_format,
                    "source_path": source_path,
                }
                for name, stamp in unknown.items()
            })
            if observations:
                total += self.merge_profile_unlocks(
                    app_id, observations, allowed_api_names=schema_names,
                )
            return total
        except Exception as e:
            logger.error(f"Error recording achievement state for game {game_id}: {e}")
            return 0

    def get_profile_unlocks(self, app_id: Optional[str] = None, *, include_pending: bool = False) -> Dict[str, Dict[str, float]]:
        """Return schema-validated append-only unlocks grouped by AppID."""
        try:
            if app_id:
                query = "SELECT app_id, api_name, unlock_time FROM achievement_profile WHERE app_id = ?"
                if not include_pending:
                    query += " AND validation_state = 'validated'"
                rows = self.conn.execute(query, (str(app_id),)).fetchall()
            else:
                query = "SELECT app_id, api_name, unlock_time FROM achievement_profile"
                if not include_pending:
                    query += " WHERE validation_state = 'validated'"
                rows = self.conn.execute(query).fetchall()
            result: Dict[str, Dict[str, float]] = {}
            for sid, name, stamp in rows:
                result.setdefault(str(sid), {})[str(name)] = float(stamp or 0)
            return result
        except Exception as e:
            logger.error(f"Error reading achievement profile: {e}")
            return {}

    def get_profile_unlock_records(self, app_id: Optional[str] = None, *, include_pending: bool = True) -> Dict[str, Dict[str, dict]]:
        """Return provenance-aware profile records for cloud sync/diagnostics."""
        try:
            query = "SELECT app_id, api_name, unlock_time, provenance, verified, validation_state, source_format FROM achievement_profile"
            args: tuple = ()
            if app_id:
                query += " WHERE app_id = ?"
                args = (str(app_id),)
            if not include_pending:
                query += " AND " if args else " WHERE "
                query += "validation_state = 'validated'"
            result: Dict[str, Dict[str, dict]] = {}
            for sid, name, stamp, provenance, verified, validation_state, source_format in self.conn.execute(query, args).fetchall():
                result.setdefault(str(sid), {})[str(name)] = {
                    "unlock_time": float(stamp or 0),
                    "provenance": str(provenance or "unknown"),
                    "verified": _normalise_achievement_provenance(provenance) == "steam_verified",
                    "validation_state": str(validation_state or "pending_schema"),
                    "source_format": str(source_format or ""),
                }
            return result
        except Exception as e:
            logger.error(f"Error reading achievement profile records: {e}")
            return {}

    def collect_profile_from_games(self) -> int:
        """Promote all existing per-game unlock projections into the ledger."""
        rows = self.conn.execute("SELECT app_id, api_name, unlock_time, unlock_provenance, unlock_verified, unlock_source_format, unlock_source_path FROM achievements WHERE unlocked = 1 AND app_id != ''").fetchall()
        grouped: Dict[str, Dict[str, dict]] = {}
        for app_id, api_name, stamp, provenance, verified, source_format, source_path in rows:
            grouped.setdefault(str(app_id), {})[str(api_name)] = {
                "unlock_time": float(stamp or 0), "provenance": provenance or "local_emulator",
                "verified": _normalise_achievement_provenance(provenance) == "steam_verified", "validation_state": "validated",
                "source_format": source_format or "", "source_path": source_path or "",
            }
        total = 0
        for app_id, unlocks in grouped.items():
            total += self.merge_profile_unlocks(app_id, unlocks)
        return total

    def project_profile_achievements(self, game_id: int, app_id: str = "") -> int:
        """Apply profile unlocks to matching current game schema rows only."""
        app_id = str(app_id or "").strip()
        if not app_id:
            row = self.conn.execute("SELECT app_id FROM achievements WHERE game_id = ? LIMIT 1", (game_id,)).fetchone()
            app_id = str(row[0] or "") if row else ""
        unlocks = self.get_profile_unlock_records(app_id, include_pending=False)
        values = unlocks.get(app_id, {})
        if not values:
            return 0
        changed = 0
        try:
            with _ACHIEVEMENT_DB_LOCK, self.conn:
                for api_name, value in values.items():
                    changed += self.conn.execute("""
                        UPDATE achievements
                        SET unlocked = 1,
                            unlock_time = CASE WHEN unlock_time <= 0 THEN ? ELSE MIN(unlock_time, ?) END,
                            unlock_provenance = ?, unlock_verified = ?,
                            unlock_source_format = ?, notification_sent = 1
                        WHERE game_id = ? AND api_name = ?
                    """, (float(value.get("unlock_time", 0) or time.time()), float(value.get("unlock_time", 0) or time.time()), _normalise_achievement_provenance(value.get("provenance", "unknown")), int(_normalise_achievement_provenance(value.get("provenance", "unknown")) == "steam_verified"), value.get("source_format", ""), game_id, api_name)).rowcount
            return changed
        except Exception as e:
            logger.error(f"Error projecting achievement profile for game {game_id}: {e}")
            return 0

    def get_game_achievements(self, game_id: int) -> List[dict]:
        """Return all achievements for a game, ordered by unlocked status and name."""
        try:
            cursor = self.conn.execute("""
                SELECT id, game_id, app_id, api_name, display_name, description, icon_path, icongray_path, unlocked, unlock_time, hidden, unlock_provenance, unlock_verified, unlock_source_format
                FROM achievements
                WHERE game_id = ?
                ORDER BY unlocked DESC, unlock_time DESC, display_name ASC
            """, (game_id,))
            rows = cursor.fetchall()
            return [
                {
                    "id": r[0],
                    "game_id": r[1],
                    "app_id": r[2],
                    "api_name": r[3],
                    "display_name": r[4],
                    "description": r[5],
                    "icon_path": r[6],
                    "icongray_path": r[7],
                    "unlocked": bool(r[8]),
                    "unlock_time": float(r[9]),
                    "hidden": bool(r[10]),
                    "provenance": _normalise_achievement_provenance(r[11]),
                    "verified": _normalise_achievement_provenance(r[11]) == "steam_verified",
                    "source_format": r[13] or "",
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Error getting achievements for game {game_id}: {e}")
            return []

    def get_profile_achievement_rows(self) -> Dict[int, List[dict]]:
        """Return the small achievement projection needed by public profiles.

        Public profile rendering used to issue one full achievement query per
        game. Keep the existing detailed API for the achievements page, but
        provide this narrow bulk read for profile generation so a large local
        library does not create an N+1 query pattern.
        """
        try:
            rows = self.conn.execute("""
                SELECT game_id, app_id, api_name, display_name
                FROM achievements
                ORDER BY game_id, unlocked DESC, unlock_time DESC, display_name ASC
            """).fetchall()
            result: Dict[int, List[dict]] = {}
            for game_id, app_id, api_name, display_name in rows:
                result.setdefault(int(game_id), []).append({
                    "app_id": str(app_id or ""),
                    "api_name": str(api_name or ""),
                    "display_name": str(display_name or ""),
                })
            return result
        except Exception as e:
            logger.error(f"Error getting profile achievement rows: {e}")
            return {}

    def get_achievement_stats(self, game_id: int) -> Tuple[int, int, float]:
        """Return (unlocked_count, total_count, percentage)."""
        try:
            cursor = self.conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN unlocked = 1 THEN 1 ELSE 0 END) as unlocked
                FROM achievements
                WHERE game_id = ?
            """, (game_id,))
            row = cursor.fetchone()
            if not row or not row[0]:
                return 0, 0, 0.0
            total = int(row[0])
            unlocked = int(row[1] or 0)
            pct = round((unlocked / total) * 100.0, 1) if total > 0 else 0.0
            return unlocked, total, pct
        except Exception as e:
            logger.error(f"Error getting achievement stats for game {game_id}: {e}")
            return 0, 0, 0.0

    def get_global_achievement_stats(self) -> Dict[str, Any]:
        """Return global achievement statistics across all games."""
        try:
            cursor = self.conn.execute("""
                SELECT
                    COUNT(DISTINCT game_id) as total_games_with_achs,
                    COUNT(*) as total_achievements,
                    SUM(CASE WHEN unlocked = 1 THEN 1 ELSE 0 END) as total_unlocked
                FROM achievements
            """)
            row = cursor.fetchone()
            if not row or not row[1]:
                return {"games_count": 0, "total_achievements": 0, "total_unlocked": 0, "percentage": 0.0}
            games_cnt = int(row[0] or 0)
            total_achs = int(row[1] or 0)
            total_unlocked = int(row[2] or 0)
            pct = round((total_unlocked / total_achs) * 100.0, 1) if total_achs > 0 else 0.0
            return {
                "games_count": games_cnt,
                "total_achievements": total_achs,
                "total_unlocked": total_unlocked,
                "percentage": pct
            }
        except Exception as e:
            logger.error(f"Error getting global achievement stats: {e}")
            return {"games_count": 0, "total_achievements": 0, "total_unlocked": 0, "percentage": 0.0}

    def get_recent_unlocked_achievements(self, game_id: int, limit: int = 6) -> List[dict]:
        """Return the most recently unlocked achievements for a game."""
        try:
            cursor = self.conn.execute("""
                SELECT id, game_id, app_id, api_name, display_name, description, icon_path, icongray_path, unlocked, unlock_time, hidden, unlock_provenance, unlock_verified, unlock_source_format
                FROM achievements
                WHERE game_id = ? AND unlocked = 1
                ORDER BY unlock_time DESC, id DESC
                LIMIT ?
            """, (game_id, limit))
            rows = cursor.fetchall()
            return [
                {
                    "id": r[0],
                    "game_id": r[1],
                    "app_id": r[2],
                    "api_name": r[3],
                    "display_name": r[4],
                    "description": r[5],
                    "icon_path": r[6],
                    "icongray_path": r[7],
                    "unlocked": bool(r[8]),
                    "unlock_time": float(r[9]),
                    "hidden": bool(r[10]),
                    "provenance": _normalise_achievement_provenance(r[11]),
                    "verified": _normalise_achievement_provenance(r[11]) == "steam_verified",
                    "source_format": r[13] or "",
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Error getting recent unlocked achievements: {e}")
            return []

    def reset_game_achievements(self, game_id: int):
        """Reset unlocked status of all achievements for a game (for testing/re-locking)."""
        try:
            with self.conn:
                self.conn.execute("""
                    UPDATE achievements
                    SET unlocked = 0, unlock_time = 0, unlock_provenance = 'unknown',
                        unlock_verified = 0, unlock_source_format = '', unlock_source_path = '', notification_sent = 1
                    WHERE game_id = ?
                """, (game_id,))
        except Exception as e:
            logger.error(f"Error resetting achievements for game {game_id}: {e}")
