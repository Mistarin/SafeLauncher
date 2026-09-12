"""Profile-page appearance tokens.

The profile page deliberately owns its appearance independently from the
launcher shell.  Themes describe one shared backdrop and translucent surface
tokens; individual cards never create their own blur or decorative effects.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileTheme:
    key: str
    label: str
    backdrop: tuple[str, str, str]
    bubbles: tuple[str, ...]
    panel_rgb: tuple[int, int, int]
    hero_alpha: int
    panel_alpha: int
    border_alpha: int
    accent: str


PROFILE_THEMES: tuple[ProfileTheme, ...] = (
    ProfileTheme(
        "grey", "Grey glass", ("#17181D", "#242731", "#111216"),
        ("#6B7280", "#475569", "#94A3B8"), (37, 39, 46), 214, 178, 42, "#7EB6FF",
    ),
    ProfileTheme(
        "aurora", "Aurora glass", ("#0D1B25", "#153B3A", "#15172B"),
        ("#42D6B4", "#5D8CFF", "#B57CFF"), (24, 42, 48), 214, 168, 48, "#71E2C2",
    ),
    ProfileTheme(
        "sunset", "Sunset glass", ("#21151D", "#4A2630", "#17151E"),
        ("#FF8C69", "#FFB86B", "#D46BBA"), (55, 29, 37), 218, 172, 48, "#FF9E83",
    ),
    ProfileTheme(
        "bubble", "Bubble glass", ("#131D36", "#30204C", "#121522"),
        ("#6EA8FF", "#C084FC", "#67E8F9", "#F0ABFC"), (31, 35, 66), 216, 170, 50, "#A7C7FF",
    ),
)

_THEMES = {theme.key: theme for theme in PROFILE_THEMES}


def normalize_profile_theme(value: object) -> str:
    key = str(value or "").strip().lower()
    return key if key in _THEMES else "grey"


def get_profile_theme(value: object = "grey") -> ProfileTheme:
    return _THEMES[normalize_profile_theme(value)]


def profile_theme_choices() -> tuple[tuple[str, str], ...]:
    return tuple((theme.label, theme.key) for theme in PROFILE_THEMES)


def _rgba(rgb: tuple[int, int, int], alpha: int) -> str:
    return f"rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, {max(0, min(255, alpha))})"


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = str(value or "").lstrip("#")
    if len(value) != 6:
        return (128, 136, 152)
    try:
        return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return (128, 136, 152)


def theme_rgba(value: str, alpha: int) -> str:
    """Convert a theme hex token into a Qt stylesheet rgba() value."""
    return _rgba(_hex_rgb(value), alpha)


def profile_theme_preview_style(value: object = "grey") -> str:
    """Return a compact shared-backdrop preview for Settings."""
    theme = get_profile_theme(value)
    return (
        "QFrame#profileThemePreview {"
        f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
        f"stop:0 {theme.backdrop[0]}, stop:0.52 {theme.backdrop[1]}, stop:1 {theme.backdrop[2]});"
        f"border: 1px solid {_rgba(_hex_rgb(theme.bubbles[0]), 75)};"
        "border-radius: 10px; }"
        "QFrame#profileThemePreview QFrame#themePreviewPanel {"
        f"background: {_rgba(theme.panel_rgb, theme.panel_alpha)};"
        f"border: 1px solid {_rgba(theme.panel_rgb, theme.border_alpha)}; border-radius: 8px; }}"
    )
