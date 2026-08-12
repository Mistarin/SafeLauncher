"""Wine prefix inspection and recoverable maintenance operations."""

from dataclasses import dataclass
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
        with tarfile.open(archive_path, "r:*") as archive:
            for member in archive.getmembers():
                target = (staging / member.name).resolve()
                if staging.resolve() not in target.parents and target != staging.resolve():
                    raise ValueError("Prefix backup contains an unsafe path.")
            archive.extractall(staging)
        extracted = staging / "prefix"
        if prefix.exists():
            shutil.rmtree(prefix)
        extracted.rename(prefix)
        staging.rmdir()

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
