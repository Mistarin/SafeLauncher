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

_DUPLICATE_REPAIR_DONE: set = set()

class IdentityReconciler:
    def __init__(self, session, games, profiles):
        self.session = session
        self.games, self.profiles = games, profiles

    @property
    def conn(self):
        return self.session.conn

    @property
    def db_path(self):
        return self.session.db_path

    profile_identity = staticmethod(profile_identity)

    def _merge_profile_identity_alias(self, source_identity: str, target_identity: str, app_id: str) -> None:
        """Move one legacy profile alias onto a canonical identity.

        Profile history is append-only for ordinary removal, but an identity
        alias is not a separate game.  Merge it before deleting the alias so
        favorites, playtime baselines, and causal favorite markers survive.
        """
        if not source_identity or not target_identity or source_identity == target_identity:
            return
        source = self.conn.execute(
            """SELECT display_name, favorite, favorite_changed_at,
                      favorite_change_id, playtime_baseline_seconds, last_played
               FROM profile_games WHERE identity_key = ?""",
            (source_identity,),
        ).fetchone()
        if not source:
            return
        self.profiles.merge_profile_game({
            "identity_key": target_identity,
            "app_id": app_id,
            "name": source[0] or fallback_game_name("", source_identity),
            "display_name": source[0] or "",
            "favorite": bool(source[1]),
            "favorite_changed_at": source[2] or 0,
            "favorite_change_id": source[3] or "",
            "playtime_baseline_seconds": source[4] or 0,
            "last_played": source[5] or 0,
        })
        with self.conn:
            self.conn.execute(
                "DELETE FROM profile_games WHERE identity_key = ?",
                (source_identity,),
            )

    def consolidate_duplicate_games(self, *, force: bool = False) -> int:
        """Consolidate rows that resolve to the same portable game identity.

        Older cloud-only materialization treated ``local:<slug>`` as a title,
        then derived ``local:local-<slug>`` on the next pass.  It could also
        leave a non-Steam placeholder beside the later-known Steam identity.
        This repair runs once per persistent database during startup and can be
        forced by a sync pass.  It keeps the best row, merges dependent
        achievement/session ledgers, preserves profile history, and only then
        removes redundant rows.
        """
        repair_key = self.db_path
        if not force and repair_key != ":memory:" and repair_key in _DUPLICATE_REPAIR_DONE:
            return 0

        removed = 0
        try:
            with _ACHIEVEMENT_DB_LOCK, self.conn:
                # Normalize legacy local profile-history keys first.  The
                # profile table is append-only by design, but old identity
                # spellings are aliases rather than separate games.
                history_rows = self.conn.execute(
                    """SELECT identity_key, app_id, display_name, favorite,
                              favorite_changed_at, favorite_change_id,
                              playtime_baseline_seconds, last_played
                       FROM profile_games"""
                ).fetchall()
                for row in history_rows:
                    old_identity = str(row[0] or "").strip()
                    app_id = str(row[1] or "").strip()
                    if app_id or not old_identity.casefold().startswith("local:"):
                        continue
                    canonical_identity = local_profile_identity(old_identity)
                    if canonical_identity == old_identity:
                        continue
                    self.profiles.merge_profile_game({
                        "identity_key": canonical_identity,
                        "app_id": "",
                        "name": preferred_game_name("", row[2]) or fallback_game_name("", old_identity),
                        "display_name": row[2] or "",
                        "favorite": bool(row[3]),
                        "favorite_changed_at": row[4] or 0,
                        "favorite_change_id": row[5] or "",
                        "playtime_baseline_seconds": row[6] or 0,
                        "last_played": row[7] or 0,
                    })
                    self.conn.execute(
                        "DELETE FROM profile_games WHERE identity_key = ?",
                        (old_identity,),
                    )

                # A cloud-only record may have been materialized before the
                # backend knew its Steam AppID.  Reconcile only records that
                # are unmistakably placeholders: archived, pathless, and
                # executable-less.  An installed local game with the same
                # title must never be merged solely by name.
                games_before_alias_repair = self.games.get_all_games()
                steam_games = [
                    game for game in games_before_alias_repair
                    if str(game.steam_id or "").strip() not in ("", "0", "None")
                ]
                local_aliases = [
                    game for game in games_before_alias_repair
                    if not str(game.steam_id or "").strip()
                    and bool(game.is_archived)
                    and not str(game.path or "").strip()
                    and not str(game.executable or "").strip()
                ]
                profile_names = {
                    str(row[0]): str(row[2] or "")
                    for row in self.conn.execute(
                        "SELECT identity_key, app_id, display_name FROM profile_games"
                    ).fetchall()
                }
                preferred_steam_row_ids = set()
                for local_game in local_aliases:
                    local_identity = self.profile_identity(local_game.name, "")
                    local_name = display_name_key(
                        profile_names.get(local_identity) or local_game.name,
                        "",
                    )
                    if not local_name:
                        continue
                    matches = []
                    for steam_game in steam_games:
                        app_id = str(steam_game.steam_id or "").strip()
                        steam_identity = self.profile_identity(steam_game.name, app_id)
                        steam_name = display_name_key(
                            profile_names.get(steam_identity) or steam_game.name,
                            app_id,
                        )
                        if steam_name and steam_name == local_name:
                            matches.append((steam_game, app_id, steam_identity))
                    if len(matches) != 1:
                        continue
                    steam_game, app_id, steam_identity = matches[0]
                    preferred_steam_row_ids.add(int(steam_game.id))
                    self._merge_profile_identity_alias(
                        local_identity,
                        steam_identity,
                        app_id,
                    )
                    with self.conn:
                        self.conn.execute(
                            "UPDATE games SET steam_id = ? WHERE id = ?",
                            (app_id, local_game.id),
                        )

                games_by_identity = {}
                for game in self.games.get_all_games():
                    identity = self.profile_identity(game.name, game.steam_id)
                    games_by_identity.setdefault(identity, []).append(game)

                duplicate_groups = [
                    (identity, rows)
                    for identity, rows in games_by_identity.items()
                    if len(rows) > 1
                ]
                for identity, rows in duplicate_groups:
                    def _rank(game):
                        installed = bool(not game.is_archived and game.path)
                        meaningful = bool(meaningful_game_name(game.name, game.steam_id))
                        has_steam_identity = bool(str(game.steam_id or "").strip())
                        preferred_steam_row = int(game.id) in preferred_steam_row_ids
                        return (installed, preferred_steam_row, has_steam_identity, meaningful, bool(game.path), -int(game.id))

                    ordered = sorted(rows, key=_rank, reverse=True)
                    canonical = ordered[0]
                    duplicates = ordered[1:]
                    app_id = str(canonical.steam_id or "").strip()
                    if not app_id:
                        app_id = next((str(game.steam_id or "").strip() for game in rows if game.steam_id), "")

                    title = next(
                        (
                            meaningful_game_name(game.name, app_id)
                            for game in ordered
                            if meaningful_game_name(game.name, app_id)
                        ),
                        fallback_game_name(app_id, identity),
                    )[:MAX_GAME_NAME_LENGTH]

                    def _first_text(*values):
                        for value in values:
                            text = str(value or "").strip()
                            if text:
                                return text
                        return ""

                    path_source = next((game for game in ordered if game.path), canonical)
                    executable_source = next((game for game in ordered if game.executable), canonical)
                    banner = _first_text(canonical.banner_url, *(game.banner_url for game in duplicates))[:1024]
                    tags = _first_text(canonical.tags, *(game.tags for game in duplicates))[:2048]
                    build_id = _first_text(canonical.build_id, *(game.build_id for game in duplicates))
                    proton_path = _first_text(canonical.proton_path, *(game.proton_path for game in duplicates))
                    collection = _first_text(canonical.collection, *(game.collection for game in duplicates))
                    version_override = _first_text(canonical.version_override, *(game.version_override for game in duplicates))
                    patch_notes_url = _first_text(canonical.patch_notes_url, *(game.patch_notes_url for game in duplicates))
                    icon_url = _first_text(canonical.icon_url, *(game.icon_url for game in duplicates))[:1024]
                    env_vars = _first_text(canonical.env_vars, *(game.env_vars for game in duplicates)) or "{}"
                    mode = _first_text(path_source.mode, canonical.mode, *(game.mode for game in duplicates))[:32]
                    install_dates = [int(game.install_date or 0) for game in rows if int(game.install_date or 0) > 0]
                    install_date = min(install_dates) if install_dates else 0

                    with self.conn:
                        self.conn.execute(
                            """UPDATE games SET
                               name = ?, path = ?, executable = ?, mode = ?,
                               banner_url = ?, steam_id = ?,
                               playtime_seconds = ?, is_favorite = ?,
                               last_played = ?, tags = ?, build_id = ?,
                               proton_path = ?, collection = ?, install_date = ?,
                               version_override = ?, patch_notes_url = ?,
                               is_archived = ?, icon_url = ?, env_vars = ?, build_date = ?
                               WHERE id = ?""",
                            (
                                title,
                                _first_text(canonical.path, path_source.path),
                                _first_text(canonical.executable, executable_source.executable),
                                mode,
                                banner,
                                app_id or _first_text(canonical.steam_id),
                                max(int(game.playtime_seconds or 0) for game in rows),
                                int(any(bool(game.is_favorite) for game in rows)),
                                max(int(game.last_played or 0) for game in rows),
                                tags,
                                build_id,
                                proton_path,
                                collection,
                                install_date,
                                version_override,
                                patch_notes_url,
                                int(all(bool(game.is_archived) for game in rows)),
                                icon_url,
                                env_vars,
                                max(int(game.build_date or 0) for game in rows),
                                canonical.id,
                            ),
                        )

                        for duplicate in duplicates:
                            achievement_rows = self.conn.execute(
                                """SELECT app_id, api_name, display_name, description,
                                          icon_path, icongray_path, unlocked, unlock_time,
                                          hidden, unlock_provenance, unlock_verified,
                                          unlock_source_format, unlock_source_path,
                                          notification_sent
                                   FROM achievements WHERE game_id = ?""",
                                (duplicate.id,),
                            ).fetchall()
                            for achievement in achievement_rows:
                                current = self.conn.execute(
                                    """SELECT id, app_id, display_name, description,
                                              icon_path, icongray_path, unlocked, unlock_time,
                                              hidden, unlock_provenance, unlock_verified,
                                              unlock_source_format, unlock_source_path,
                                              notification_sent
                                       FROM achievements
                                       WHERE game_id = ? AND api_name = ?""",
                                    (canonical.id, achievement[1]),
                                ).fetchone()
                                if not current:
                                    self.conn.execute(
                                        """INSERT INTO achievements
                                           (game_id, app_id, api_name, display_name, description,
                                            icon_path, icongray_path, unlocked, unlock_time, hidden,
                                            unlock_provenance, unlock_verified, unlock_source_format,
                                            unlock_source_path, notification_sent)
                                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                        (canonical.id, *achievement),
                                    )
                                    continue
                                current_time = float(current[7] or 0)
                                incoming_time = float(achievement[7] or 0)
                                if current_time > 0 and incoming_time > 0:
                                    unlock_time = min(current_time, incoming_time)
                                else:
                                    unlock_time = max(current_time, incoming_time)
                                provenance = _first_text(current[9], achievement[9]) or "unknown"
                                self.conn.execute(
                                    """UPDATE achievements SET
                                           app_id = COALESCE(NULLIF(app_id, ''), ?),
                                           display_name = COALESCE(NULLIF(display_name, ''), ?),
                                           description = COALESCE(NULLIF(description, ''), ?),
                                           icon_path = COALESCE(NULLIF(icon_path, ''), ?),
                                           icongray_path = COALESCE(NULLIF(icongray_path, ''), ?),
                                           unlocked = MAX(unlocked, ?),
                                           unlock_time = ?,
                                           hidden = MAX(hidden, ?),
                                           unlock_provenance = ?,
                                           unlock_verified = MAX(unlock_verified, ?),
                                           unlock_source_format = COALESCE(NULLIF(unlock_source_format, ''), ?),
                                           unlock_source_path = COALESCE(NULLIF(unlock_source_path, ''), ?),
                                           notification_sent = MAX(notification_sent, ?)
                                       WHERE id = ?""",
                                    (
                                        achievement[0], achievement[2], achievement[3],
                                        achievement[4], achievement[5], achievement[6],
                                        unlock_time, achievement[8], provenance,
                                        achievement[10], achievement[11], achievement[12],
                                        achievement[13], current[0],
                                    ),
                                )
                            self.conn.execute("DELETE FROM achievements WHERE game_id = ?", (duplicate.id,))

                            # session_id is globally unique: these rows already
                            # exist, and only their owning game needs to change.
                            self.conn.execute(
                                "UPDATE playtime_sessions SET game_id = ? WHERE game_id = ?",
                                (canonical.id, duplicate.id),
                            )
                            self.conn.execute("DELETE FROM games WHERE id = ?", (duplicate.id,))
                            removed += 1

                        total_sessions = self.conn.execute(
                            "SELECT COALESCE(SUM(duration_seconds), 0) FROM playtime_sessions WHERE game_id = ?",
                            (canonical.id,),
                        ).fetchone()[0]
                        self.conn.execute(
                            "UPDATE games SET playtime_seconds = MAX(playtime_seconds, ?) WHERE id = ?",
                            (int(total_sessions or 0), canonical.id),
                        )

                if removed:
                    logger.info("Consolidated %d duplicate local game rows", removed)
            if repair_key != ":memory:":
                _DUPLICATE_REPAIR_DONE.add(repair_key)
        except Exception as e:
            logger.error(f"Failed to consolidate duplicate game rows: {e}")
        return removed
