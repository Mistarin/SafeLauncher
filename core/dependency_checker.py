"""Check and optionally install SafeLauncher's Python dependencies."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import re
import subprocess
import sys
from pathlib import Path


_REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\s*==\s*([^\s#]+))?")

# Some PyQt wheels expose several runtime components through the main PyQt6
# import, without leaving separate usable metadata in every environment.
_IMPORT_NAMES = {
    "PyQt6-Qt6": "PyQt6",
    "PyQt6-sip": "PyQt6",
    "python-xlib": "Xlib",
}


def requirements_path() -> Path:
    return Path(__file__).resolve().parent.parent / "requirements.txt"


def missing_requirements(path: Path | None = None) -> list[str]:
    """Return requirement lines whose distribution is not installed.

    Newer installed versions are accepted; automatic startup prompts should
    not downgrade a working environment just because requirements are pinned.
    """
    path = path or requirements_path()
    missing: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "http:" , "https:")):
            continue
        match = _REQUIREMENT.match(line)
        if not match:
            continue
        name, _required_version = match.groups()
        import_name = _IMPORT_NAMES.get(name, name.replace("-", "_"))
        try:
            importlib.metadata.version(name)
            installed = True
        except importlib.metadata.PackageNotFoundError:
            installed = importlib.util.find_spec(import_name) is not None
        if not installed:
            missing.append(line)
    return missing


def install_requirements(path: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Install the project requirements into the interpreter running the app."""
    if sys.prefix == sys.base_prefix:
        raise RuntimeError(
            "SafeLauncher is running with the system Python, which is managed by the OS.\n\n"
            "Create and use the project virtual environment instead:\n"
            "python -m venv .venv\n"
            ".venv/bin/python -m pip install -r requirements.txt\n"
            ".venv/bin/python main.py"
        )
    path = path or requirements_path()
    return subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(path)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
