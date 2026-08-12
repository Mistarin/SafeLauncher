"""Runtime inventory and health checks kept independent from the Qt UI."""

from dataclasses import dataclass
import os
import platform
import shutil
import struct
from pathlib import Path


def host_architecture() -> str:
    machine = platform.machine().lower()
    return "ARM64" if machine in {"aarch64", "arm64", "armv8l"} else "x86_64" if machine in {"x86_64", "amd64", "x64"} else machine


def _elf_architecture(path: str) -> str:
    try:
        with open(path, "rb") as stream:
            header = stream.read(20)
        if header[:4] != b"\x7fELF":
            return "unknown"
        machine = struct.unpack("<H", header[18:20])[0]
        return {62: "x86_64", 183: "ARM64", 3: "x86", 40: "ARM"}.get(machine, str(machine))
    except OSError:
        return "unknown"


@dataclass(frozen=True)
class RuntimeRecord:
    name: str
    path: str
    kind: str
    architecture: str
    status: str
    size_bytes: int
    version: str
    modified: float

    @property
    def size_text(self) -> str:
        size = float(self.size_bytes)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{self.size_bytes} B"


class RuntimeInventory:
    """Scans only known runtime roots and exposes safe maintenance operations."""

    def __init__(self, roots=None):
        home = Path(os.path.expanduser("~"))
        self.roots = [Path(p) for p in (roots or (
            home / ".local/share/umu",
            home / ".local/share/Steam/compatibilitytools.d",
            home / ".steam/steam/compatibilitytools.d",
            Path("/usr/share/steam/compatibilitytools.d"),
            Path("/usr/lib/steam/compatibilitytools.d"),
        ))]

    @staticmethod
    def _size(path: Path) -> int:
        total = 0
        try:
            for root, _, files in os.walk(path):
                for filename in files:
                    try:
                        total += os.path.getsize(os.path.join(root, filename))
                    except OSError:
                        pass
        except OSError:
            pass
        return total

    @staticmethod
    def _version(path: Path) -> str:
        for name in ("version", "VERSION", "toolmanifest.vdf"):
            candidate = path / name
            if candidate.is_file():
                try:
                    line = candidate.read_text(errors="replace").splitlines()[0]
                    return line[:120]
                except (OSError, IndexError):
                    pass
        return path.name

    def scan(self) -> list[RuntimeRecord]:
        result = []
        host = host_architecture()
        seen = set()
        for root in self.roots:
            if not root.is_dir():
                continue
            try:
                children = list(root.iterdir())
            except OSError:
                continue
            for path in children:
                if not path.is_dir() or path in seen:
                    continue
                seen.add(path)
                name = path.name
                lower = name.lower()
                if "steamrt" in lower or "pressure-vessel" in lower:
                    kind = "Steam Runtime"
                elif "umu-proton" in lower:
                    kind = "UMU-Proton"
                elif lower.startswith(("ge-proton", "proton-ge")):
                    kind = "GE-Proton"
                elif lower.startswith("proton"):
                    kind = "System Proton"
                else:
                    continue
                manifest = path / "toolmanifest.vdf"
                arch = host
                for binary in (path / "proton", path / "pressure-vessel/bin/pressure-vessel-wrap", path / "dist/bin/wine"):
                    if binary.is_file():
                        arch = _elf_architecture(str(binary))
                        if arch != "unknown":
                            break
                compatible = arch in ("unknown", host)
                status = "installed" if manifest.exists() or kind == "Steam Runtime" else "corrupted"
                if not compatible:
                    status = "incompatible"
                try:
                    modified = path.stat().st_mtime
                except OSError:
                    modified = 0
                result.append(RuntimeRecord(name, str(path), kind, arch, status, self._size(path), self._version(path), modified))
        return sorted(result, key=lambda item: (item.kind, item.name.lower()))

    def verify(self, path: str) -> tuple[bool, str]:
        record = next((item for item in self.scan() if os.path.realpath(item.path) == os.path.realpath(path)), None)
        if not record:
            return False, "Runtime is outside known runtime directories or does not exist."
        if record.status == "incompatible":
            return False, f"Runtime architecture {record.architecture} is incompatible with host {host_architecture()}."
        if record.status == "corrupted":
            return False, "toolmanifest.vdf is missing. Reinstall or repair this runtime."
        return True, "Runtime manifest and architecture look valid."

    def remove(self, path: str) -> bool:
        target = Path(path).resolve()
        allowed = [root.resolve() for root in self.roots if root.exists()]
        if not any(target.parent == root or root in target.parents for root in allowed):
            raise ValueError("Refusing to remove a runtime outside known runtime directories.")
        shutil.rmtree(target)
        return True
