"""SafeLauncher command-line entrypoint."""

import os
import sys

from core.version import APP_VERSION, __version__


def main():
    if "--offline" in sys.argv:
        os.environ["SAFELAUNCHER_OFFLINE_MODE"] = "1"
    from core.cli import dispatch_command
    status = dispatch_command(sys.argv[1:])
    if status is not None:
        return status
    from ui.application_entrypoint import run_gui
    return run_gui(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
