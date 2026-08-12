"""Central persistent logging infrastructure for SafeLauncher.

Provides a configured Python logger writing formatted, timestamped output to both
stdout and a rotating log file in ~/.local/state/safelauncher/safelauncher.log.
"""

import os
import sys
import logging
import tempfile
from logging.handlers import RotatingFileHandler

_XDG_STATE_HOME = os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
LOG_DIR = os.path.join(_XDG_STATE_HOME, "safelauncher")
LOG_FILE = os.path.join(LOG_DIR, "safelauncher.log")
CRASH_FILE = os.path.join(LOG_DIR, "crash.log")

_initialized = False


def setup_logging() -> logging.Logger:
    """Initialize persistent logging handlers once."""
    global _initialized
    logger = logging.getLogger("SafeLauncher")

    if _initialized:
        return logger

    os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s:%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console output handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Rotating file log handler (max 5 MB per file, up to 3 backups)
    try:
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        # Keep crash evidence if the XDG state directory is unavailable.
        fallback_file = os.path.join(tempfile.gettempdir(), "safelauncher.log")
        try:
            file_handler = RotatingFileHandler(
                fallback_file, maxBytes=5 * 1024 * 1024, backupCount=1, encoding="utf-8"
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
            sys.stderr.write(f"[SafeLauncher] Primary log unavailable; using {fallback_file}: {e}\n")
        except Exception as fallback_error:
            sys.stderr.write(f"[SafeLauncher] Failed to initialize file logger: {fallback_error}\n")

    _initialized = True
    logger.info("SafeLauncher persistent logging system initialized.")
    logger.info(f"Log path: {LOG_FILE}")
    return logger


def get_logger(name: str = "SafeLauncher") -> logging.Logger:
    """Get named child logger instance."""
    if not _initialized:
        setup_logging()
    return logging.getLogger(f"SafeLauncher.{name}") if name != "SafeLauncher" else logging.getLogger("SafeLauncher")


def log_crash(traceback_str: str, context_info: str = "") -> None:
    """Append unhandled crash traceback to crash.log with system context."""
    import time
    t_stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    context_line = f"CONTEXT: {context_info}\n" if context_info else ""
    report = (
        f"\n{'='*70}\nCRASH REPORT - {t_stamp}\n"
        f"{context_line}"
        f"{'='*70}\n{traceback_str}\n"
    )
    for crash_path in (CRASH_FILE, os.path.join(tempfile.gettempdir(), "safelauncher-crash.log")):
        try:
            os.makedirs(os.path.dirname(crash_path), mode=0o700, exist_ok=True)
            with open(crash_path, "a", encoding="utf-8") as f:
                f.write(report)
            return
        except Exception:
            continue
    sys.stderr.write("Failed to record crash log in both primary and fallback locations\n")
