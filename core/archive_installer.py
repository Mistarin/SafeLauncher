"""Archive preflight and inspection primitives used by the installation UI."""

from dataclasses import dataclass
import os
import shutil
import tarfile
import zipfile
from pathlib import Path
from .archive_extractor import find_executables


@dataclass(frozen=True)
class ExecutableCandidate:
    relative_path: str
    size_bytes: int
    kind: str


@dataclass(frozen=True)
class ArchiveInspection:
    archive_path: str
    bytes_total: int
    file_count: int
    top_level: tuple[str, ...]
    required_bytes: int
    free_bytes: int
    duplicate_path: str = ""

    @property
    def enough_space(self) -> bool:
        return self.free_bytes >= self.required_bytes


class ArchiveInstaller:
    def inspect(self, archive_path: str, destination_root: str) -> ArchiveInspection:
        archive_path = os.path.abspath(archive_path)
        required = os.path.getsize(archive_path)
        count = 0
        names = []
        lower = archive_path.lower()
        if lower.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as archive:
                members = archive.infolist()
                count = len(members)
                names = [member.filename for member in members]
                required = sum(member.file_size for member in members)
        elif lower.endswith((".tar", ".tar.gz", ".tgz")):
            with tarfile.open(archive_path, "r:*") as archive:
                members = archive.getmembers()
                count = len(members)
                names = [member.name for member in members]
                required = sum(member.size for member in members)
        top = tuple(sorted({name.split("/", 1)[0] for name in names if name}))
        stem = Path(archive_path).name
        for suffix in (".tar.gz", ".tgz", ".zip", ".7z", ".tar"):
            if stem.lower().endswith(suffix):
                stem = stem[:-len(suffix)]
                break
        duplicate = os.path.join(destination_root, stem) if os.path.exists(os.path.join(destination_root, stem)) else ""
        free = shutil.disk_usage(destination_root if os.path.exists(destination_root) else os.path.dirname(destination_root)).free
        return ArchiveInspection(archive_path, os.path.getsize(archive_path), count, top, int(required * 1.10), free, duplicate)

    def candidates(self, directory: str) -> list[ExecutableCandidate]:
        result = []
        for relative in find_executables(directory):
            full = os.path.join(directory, relative)
            try:
                size = os.path.getsize(full)
            lower_rel = relative.lower()
            if lower_rel.endswith(".sh"):
                kind = "Linux script"
            elif lower_rel.endswith(".bat"):
                kind = "Windows batch script"
            else:
                kind = "Windows executable"
            result.append(ExecutableCandidate(relative, size, kind))
        return result
