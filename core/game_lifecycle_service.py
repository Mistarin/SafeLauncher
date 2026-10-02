"""Safe filesystem and local-library transitions for installed games."""

from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
import uuid

from core.archive_extractor import DEFAULT_SANDBOX_DIR
from core.logger import get_logger


logger = get_logger("GameLifecycle")


@dataclass(frozen=True)
class GameLifecycleResult:
    action: str
    game_id: int
    database_changed: bool
    message: str
    is_error: bool = False


class GameLifecycleService:
    """Coordinate safe file staging with the authoritative library service."""

    def __init__(self, library_service):
        self.library_service = library_service

    @staticmethod
    def _protected_paths() -> set[str]:
        return {
            os.path.realpath(os.path.abspath(os.sep)),
            os.path.realpath(os.path.expanduser("~")),
            os.path.realpath(os.path.expanduser(DEFAULT_SANDBOX_DIR)),
        }

    @staticmethod
    def remove_game_files(game_path: str) -> tuple[bool, str]:
        """Delete a registered game directory without following its root symlink."""
        raw_value = os.path.expanduser(str(game_path or "").strip())
        if not raw_value:
            return False, "The game has no directory recorded."
        raw_path = os.path.abspath(raw_value)
        if os.path.islink(raw_path):
            return False, "Refusing to delete a symlinked game directory. Remove the library record instead."

        target_path = os.path.realpath(raw_path)
        if target_path != raw_path:
            return False, "Refusing to delete a path containing a symlink. Remove the library record instead."
        if target_path in GameLifecycleService._protected_paths():
            return False, "Refusing to delete a protected system, home, or sandbox directory."
        if not os.path.lexists(target_path):
            return True, ""

        failures: list[str] = []

        def _handle_readonly(func, subpath, _exc_info):
            try:
                os.chmod(subpath, 0o700)
                func(subpath)
            except Exception as exc:
                failures.append(f"{subpath}: {exc}")

        try:
            if os.path.isdir(target_path):
                try:
                    shutil.rmtree(target_path, onexc=_handle_readonly)
                except TypeError:
                    shutil.rmtree(target_path, onerror=_handle_readonly)
            else:
                os.chmod(target_path, 0o700)
                os.unlink(target_path)
        except Exception as exc:
            failures.append(str(exc))

        if failures or os.path.lexists(target_path):
            detail = failures[0] if failures else "the directory still exists"
            logger.warning("Could not remove game files at '%s': %s", target_path, detail)
            return False, "Could not remove the game files. Check permissions and try again."
        logger.info("Removed game files from disk: %s", target_path)
        return True, ""

    @staticmethod
    def stage_game_files(game_path: str) -> tuple[str | None, str]:
        """Move files aside until the corresponding database mutation succeeds."""
        raw_value = os.path.expanduser(str(game_path or "").strip())
        if not raw_value:
            return None, ""
        raw_path = os.path.abspath(raw_value)
        if os.path.islink(raw_path):
            return None, "Refusing to move a symlinked game directory. Remove the library record instead."

        target_path = os.path.realpath(raw_path)
        if target_path != raw_path:
            return None, "Refusing to move a path containing a symlink. Remove the library record instead."
        if target_path in GameLifecycleService._protected_paths():
            return None, "Refusing to move a protected system, home, or sandbox directory."
        if not os.path.lexists(target_path):
            return None, ""

        staged_path = f"{target_path}.safelauncher-pending-{uuid.uuid4().hex}"
        try:
            shutil.move(target_path, staged_path)
        except Exception as exc:
            logger.warning("Could not stage game files at '%s': %s", target_path, exc)
            return None, "Could not prepare the game files safely. Check permissions and try again."
        return staged_path, ""

    @staticmethod
    def restore_staged_files(staged_path: str, original_path: str) -> tuple[bool, str]:
        """Restore staged installation files after a failed library mutation."""
        if not staged_path or not os.path.lexists(staged_path):
            return True, ""
        original_path = os.path.abspath(os.path.expanduser(str(original_path or "").strip()))
        if not original_path:
            return False, "The original game path is empty; staged files were preserved."
        try:
            if os.path.lexists(original_path):
                return False, "The original game path is no longer empty; staged files were preserved."
            shutil.move(staged_path, original_path)
            return True, ""
        except Exception as exc:
            logger.warning(
                "Could not restore staged game files from '%s' to '%s': %s",
                staged_path,
                original_path,
                exc,
            )
            return False, "Could not restore the staged game files; they were preserved for safety."

    def apply(self, game, action: str) -> GameLifecycleResult:
        """Apply an uninstall or full local-data removal as one safe workflow."""
        if not game:
            return GameLifecycleResult("", 0, False, "")
        action = str(action or "").strip().lower()
        if action not in {"uninstall", "delete_all_data"}:
            logger.warning("Ignoring unknown game lifecycle action: %r", action)
            return GameLifecycleResult(action, int(game[0]), False, "")

        game_id = int(game[0])
        game_name = str(game[1] or "Game")
        game_path = str(game[2] if len(game) > 2 else "")
        staged_path, stage_error = self.stage_game_files(game_path)
        if stage_error:
            return GameLifecycleResult(action, game_id, False, stage_error, True)

        try:
            mutation_ok = (
                self.library_service.archive_game(game_id)
                if action == "uninstall"
                else self.library_service.delete_all_game_data(game_id)
            )
        except Exception:
            logger.exception("Game lifecycle database mutation failed for %s", game_id)
            mutation_ok = False

        if not mutation_ok:
            action_label = "mark" if action == "uninstall" else "delete"
            restored, restore_error = self.restore_staged_files(staged_path, game_path)
            detail = restore_error if not restored else "The installation was left unchanged."
            return GameLifecycleResult(
                action,
                game_id,
                False,
                f"Could not {action_label} local data for '{game_name}'. Try again. {detail}",
                True,
            )

        if staged_path:
            deleted, error = self.remove_game_files(staged_path)
            if not deleted:
                restored, restore_error = self.restore_staged_files(staged_path, game_path)
                if action == "uninstall":
                    detail = (
                        "The files were restored; the record remains marked uninstalled."
                        if restored else f"The staged files were preserved: {restore_error or error}"
                    )
                    message = f"'{game_name}' was marked uninstalled, but its files could not be removed. {detail}"
                else:
                    detail = (
                        "The files were restored, but the local SafeLauncher record was deleted."
                        if restored else f"The staged files were preserved: {restore_error or error}"
                    )
                    message = f"Local data for '{game_name}' was deleted, but its files could not be removed. {detail}"
                return GameLifecycleResult(action, game_id, True, message, True)

        if action == "uninstall":
            message = f"Uninstalled '{game_name}'. The SafeLauncher record and statistics were preserved."
        else:
            message = f"Deleted all local data for '{game_name}'. Remote cloud save versions were kept."
        return GameLifecycleResult(action, game_id, True, message)


__all__ = ["GameLifecycleResult", "GameLifecycleService"]
