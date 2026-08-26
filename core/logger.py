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
        # Keep crash evidence if the XDG state directory is unavailable. The
        # fallback lives in a world-writable directory, so restrict it to the
        # current user: DEBUG logs contain paths and machine details.
        fallback_file = os.path.join(tempfile.gettempdir(), "safelauncher.log")
        try:
            fd = os.open(
                fallback_file,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o600,
            )
            os.close(fd)
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


_CRASH_LOG_MAX_BYTES = 5 * 1024 * 1024


def log_crash(traceback_str: str, context_info: str = "") -> None:
    """Append unhandled crash traceback to crash.log with system context.

    The report is dropped if crash.log already exceeds _CRASH_LOG_MAX_BYTES so
    a repeating crash loop cannot grow it without bound.
    """
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
            try:
                if os.path.getsize(crash_path) > _CRASH_LOG_MAX_BYTES:
                    sys.stderr.write(f"[SafeLauncher] Crash log too large, not appending: {crash_path}\n")
                    continue
            except OSError:
                pass
            with open(crash_path, "a", encoding="utf-8") as f:
                f.write(report)
            return
        except Exception:
            continue
    sys.stderr.write("Failed to record crash log in both primary and fallback locations\n")
