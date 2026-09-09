"""Shared date formatting for user-facing SafeLauncher dates."""

from datetime import datetime

from PyQt6.QtCore import QSettings


DATE_FORMAT_OPTIONS = (
    ("YYYY-MM-DD", "%Y-%m-%d", "yyyy-MM-dd"),
    ("DD.MM.YYYY", "%d.%m.%Y", "dd.MM.yyyy"),
    ("DD/MM/YYYY", "%d/%m/%Y", "dd/MM/yyyy"),
    ("MM/DD/YYYY", "%m/%d/%Y", "MM/dd/yyyy"),
    ("Mon DD, YYYY", "%b %d, %Y", "MMM dd, yyyy"),
)
DEFAULT_DATE_FORMAT_KEY = DATE_FORMAT_OPTIONS[0][0]


def date_format_choices():
    """Return the stable setting key, Python format, and Qt format choices."""
    return DATE_FORMAT_OPTIONS


def get_date_format_key() -> str:
    settings = QSettings("SafeLauncher", "SafeLauncher")
    selected = settings.value("date_format", DEFAULT_DATE_FORMAT_KEY, type=str)
    valid = {choice[0] for choice in DATE_FORMAT_OPTIONS}
    return selected if selected in valid else DEFAULT_DATE_FORMAT_KEY


def python_date_format(format_key: str = "") -> str:
    selected = format_key or get_date_format_key()
    for key, python_format, _qt_format in DATE_FORMAT_OPTIONS:
        if key == selected:
            return python_format
    return DATE_FORMAT_OPTIONS[0][1]


def qt_date_format(format_key: str = "") -> str:
    selected = format_key or get_date_format_key()
    for key, _python_format, qt_format in DATE_FORMAT_OPTIONS:
        if key == selected:
            return qt_format
    return DATE_FORMAT_OPTIONS[0][2]


def format_timestamp(timestamp: int, format_key: str = "", fallback: str = "Unknown date") -> str:
    """Format a Unix timestamp using the current user preference."""
    try:
        if not timestamp or int(timestamp) <= 0:
            return fallback
        return datetime.fromtimestamp(int(timestamp)).strftime(python_date_format(format_key))
    except (TypeError, ValueError, OSError, OverflowError):
        return fallback


def format_datetime_timestamp(
    timestamp: int, time_format: str = "%H:%M", format_key: str = "", fallback: str = "Unknown date"
) -> str:
    """Format a timestamp with the selected date style and a fixed time style."""
    date_text = format_timestamp(timestamp, format_key, fallback)
    if date_text == fallback:
        return fallback
    try:
        time_text = datetime.fromtimestamp(int(timestamp)).strftime(time_format)
    except (TypeError, ValueError, OSError, OverflowError):
        return date_text
    return f"{date_text} {time_text}"
