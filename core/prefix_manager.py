"""Wine prefix inspection and recoverable maintenance operations."""

from dataclasses import dataclass
import inspect
import os
import shutil
import tarfile
from pathlib import Path


@dataclass(frozen=True)
class PrefixInfo:
    path: str
    size_bytes: int
    healthy: bool
    warnings: tuple[str, ...]
    user_count: int


class PrefixManager:
    def inspect(self, game_path: str) -> PrefixInfo:
        prefix = Path(game_path) / "prefix"
        size = 0
        warnings = []
        if not prefix.is_dir():
            return PrefixInfo(str(prefix), 0, False, ("Prefix has not been initialized yet.",), 0)
        for root, dirs, files in os.walk(prefix):
            for filename in files:
                try:
                    size += os.path.getsize(os.path.join(root, filename))
                except OSError:
                    pass
            for directory in dirs:
                full = os.path.join(root, directory)
                if os.path.islink(full):
                    warnings.append(f"Symlink in prefix: {full}")
        if not (prefix / "drive_c").is_dir():
            warnings.append("drive_c is missing")
        if not (prefix / "system.reg").is_file():
            warnings.append("system.reg is missing")
        users = prefix / "drive_c/users"
        count = len([item for item in users.iterdir() if item.is_dir()]) if users.is_dir() else 0
        return PrefixInfo(str(prefix), size, not warnings, tuple(warnings), count)

    def reset(self, game_path: str) -> None:
        prefix = Path(game_path) / "prefix"
        if prefix.exists():
            shutil.rmtree(prefix)

    def clear_shader_cache(self, game_path: str) -> int:
        root = Path(game_path) / "prefix"
        removed = 0
        for path in (root / "drive_c/users", root / "drive_c/windows/temp"):
            if not path.exists():
                continue
            for item in path.rglob("*shader*"):
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                    removed += 1
                elif item.is_file():
                    item.unlink(missing_ok=True)
                    removed += 1
        return removed

    def backup(self, game_path: str, destination: str) -> str:
        prefix = Path(game_path) / "prefix"
        if not prefix.is_dir():
            raise ValueError("No prefix exists to back up.")
        destination = os.path.abspath(destination)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with tarfile.open(destination, "w:gz") as archive:
            archive.add(prefix, arcname="prefix")
        return destination

    def restore(self, game_path: str, archive_path: str) -> None:
        prefix = Path(game_path) / "prefix"
        staging = prefix.with_name("prefix.restore")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)

        try:
            with tarfile.open(archive_path, "r:*") as archive:
                if "filter" in inspect.signature(tarfile.TarFile.extractall).parameters:
                    # 'data' rejects absolute paths, traversal, and out-of-tree
                    # symlinks during extraction itself. Pre-validation cannot do
                    # this reliably: resolution sees neither links that will be
                    # created nor those they point through mid-extract.
                    archive.extractall(staging, filter="data")
                else:
                    self._extract_members_safely(archive, staging)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        extracted = staging / "prefix"
        if not extracted.is_dir():
            shutil.rmtree(staging, ignore_errors=True)
            raise ValueError("Prefix backup is malformed: missing top-level 'prefix/' directory.")

        # Stage fully, then swap. The live prefix must survive a bad backup,
        # so nothing gets destroyed until the replacement is verified on disk.
        previous = prefix.with_name("prefix.old")
        if previous.exists():
            shutil.rmtree(previous)
        had_live_prefix = prefix.exists()
        if had_live_prefix:
            prefix.rename(previous)
        try:
            extracted.rename(prefix)
        except Exception:
            if had_live_prefix and previous.exists() and not prefix.exists():
                previous.rename(prefix)
            shutil.rmtree(staging, ignore_errors=True)
            raise

        shutil.rmtree(staging, ignore_errors=True)
        if previous.exists():
            shutil.rmtree(previous, ignore_errors=True)

    @staticmethod
    def _extract_members_safely(archive: "tarfile.TarFile", staging: Path) -> None:
        """Fallback extraction for runtimes without tarfile extraction filters."""
        staging_root = staging.resolve()
        safe_members = []
        for member in archive.getmembers():
            if not (member.isfile() or member.isdir()):
                # Links and device nodes are exactly what lets a hostile
                # archive redirect writes outside the staging tree; skip them.
                continue
            target = (staging / member.name).resolve()
            if target != staging_root and staging_root not in target.parents:
                raise ValueError("Prefix backup contains an unsafe path.")
            safe_members.append(member)
        archive.extractall(staging, members=safe_members)

    def migrate(self, game_path: str, destination_game_path: str) -> str:
        source = Path(game_path) / "prefix"
        target = Path(destination_game_path) / "prefix"
        if not source.is_dir():
            raise ValueError("No prefix exists to migrate.")
        if target.exists():
            raise FileExistsError(str(target))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, symlinks=True)
        return str(target)
