#!/usr/bin/env python3
"""Run a deterministic subset of SafeLauncher's existing smoke sections.

The historical ``test.py`` is intentionally kept as the complete local
regression command.  This runner selects its top-level try blocks for CI so
GitHub can report and time out core, cloud, achievement, and UI failures
independently without maintaining a second copy of the tests.
"""

from __future__ import annotations

import argparse
import ast
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
TEST_FILE = ROOT / "test.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Top-level try-block ordinals in test.py.  Using ordinals instead of source
# line numbers lets contributors add imports/comments without silently
# invalidating CI phase selection. Bootstrap sections create the shared
# runner/database/Qt objects required by later UI-oriented sections.
BOOTSTRAP = (0, 1, 2, 3, 4, 5)
PHASES = {
    "core": BOOTSTRAP[:5],
    "cloud": BOOTSTRAP + (11, 12, 13, 14, 15),
    "achievements": BOOTSTRAP + (6, 16),
    "ui": BOOTSTRAP + (6, 7, 8, 9, 10, 19, 20),
}


def _top_level_sections() -> list[ast.AST]:
    source = TEST_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TEST_FILE))
    return [node for node in tree.body if isinstance(node, ast.Try)]


def run_phase(name: str, timeout_seconds: int = 300) -> int:
    selected_lines = PHASES[name]
    sections = _top_level_sections()
    missing = [index for index in selected_lines if index >= len(sections)]
    if missing:
        print(f"✗ {name}: test.py sections are missing: {missing}", file=sys.stderr)
        return 2

    # Match test.py's deterministic defaults before executing any selected
    # section.  These are intentionally not inherited from a developer shell.
    os.environ["SAFELAUNCHER_DISABLE_UPDATE_CHECK"] = "1"
    os.environ["SAFELAUNCHER_OFFLINE_TEST_MODE"] = "1"
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Keep smoke credentials, databases, and QSettings isolated from a
    # developer's real profile (and from read-only CI home directories).
    smoke_data_home = tempfile.mkdtemp(prefix="safelauncher-smoke-")
    os.environ["XDG_DATA_HOME"] = smoke_data_home
    os.environ["XDG_CONFIG_HOME"] = smoke_data_home
    # Some selected cloud sections instantiate dialogs without including the
    # historical UI bootstrap section. Create one deterministic offscreen Qt
    # application for every phase.
    from PyQt6.QtWidgets import QApplication
    from ui.dialogs.settings_dialog import UserSettingsDialog
    smoke_qapp = QApplication.instance() or QApplication([])

    # The first import/argument preamble is required by every selected block.
    preamble = [
        node for node in ast.parse(TEST_FILE.read_text(encoding="utf-8"), filename=str(TEST_FILE)).body
        if node.lineno <= 20
    ]
    module = ast.Module(body=preamble + [sections[index] for index in selected_lines], type_ignores=[])
    ast.fix_missing_locations(module)

    started = time.monotonic()
    print(f"::group::SafeLauncher smoke phase: {name}")
    print(f"Running {len(selected_lines)} existing test sections from test.py")

    def _timeout_handler(signum, frame):
        raise TimeoutError(
            f"Smoke phase '{name}' exceeded {timeout_seconds}s; "
            "a worker, dialog, or external call may be stuck"
        )

    alarm_enabled = hasattr(signal, "SIGALRM") and timeout_seconds > 0
    if alarm_enabled:
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        code = compile(module, str(TEST_FILE), "exec")
        namespace = {
            "__name__": "__smoke_phase__", "__file__": str(TEST_FILE),
            "QApplication": QApplication, "UserSettingsDialog": UserSettingsDialog,
        }
        exec(code, namespace, namespace)
    except SystemExit as exc:
        code_value = exc.code if isinstance(exc.code, int) else 1
        if code_value:
            print(f"✗ Smoke phase '{name}' failed with exit code {code_value}", file=sys.stderr)
            return code_value
    except BaseException:
        import traceback
        traceback.print_exc()
        print(f"✗ Smoke phase '{name}' failed", file=sys.stderr)
        return 1
    finally:
        if alarm_enabled:
            signal.setitimer(signal.ITIMER_REAL, 0)
        print("::endgroup::")

    elapsed = time.monotonic() - started
    print(f"✓ Smoke phase '{name}' passed in {elapsed:.1f}s")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=[*PHASES, "all"], help="Smoke phase to run")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    if args.phase == "all":
        for phase in PHASES:
            status = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), phase,
                 "--timeout-seconds", str(args.timeout_seconds)],
                cwd=str(ROOT),
            ).returncode
            if status:
                return status
        return 0
    return run_phase(args.phase, args.timeout_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
