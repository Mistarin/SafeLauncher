"""Profile-page panel appearance tokens.

The profile page deliberately owns its appearance independently from the
launcher shell. Themes describe shared dark translucent surface tokens only;
the profile background is a separate user-selected document value. Individual
cards never create their own blur or decorative effects.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.profile_models import normalize_panel_theme_id, panel_theme_key


@dataclass(frozen=True)
class ProfileTheme:
    theme_id: int
    key: str
    label: str
    backdrop: tuple[str, str, str]
    bubbles: tuple[str, ...]
    panel_rgb: tuple[int, int, int]
    hero_alpha: int
    panel_alpha: int
    border_alpha: int
    accent: str
    is_glass: bool = False


PROFILE_THEMES: tuple[ProfileTheme, ...] = (
    ProfileTheme(
        1, "grey", "Grey", ("#17181D", "#242731", "#111216"),
        ("#6B7280", "#475569", "#94A3B8"), (37, 39, 46), 255, 255, 42, "#7EB6FF",
    ),
    ProfileTheme(
        2, "aurora", "Aurora", ("#0D1B25", "#153B3A", "#15172B"),
        ("#42D6B4", "#5D8CFF", "#B57CFF"), (24, 42, 48), 255, 255, 48, "#71E2C2",
    ),
    ProfileTheme(
        3, "sunset", "Sunset", ("#21151D", "#4A2630", "#17151E"),
        ("#FF8C69", "#FFB86B", "#D46BBA"), (55, 29, 37), 255, 255, 48, "#FF9E83",
    ),
    ProfileTheme(
        4, "bubble", "Bubble", ("#131D36", "#30204C", "#121522"),
        ("#6EA8FF", "#C084FC", "#67E8F9", "#F0ABFC"), (31, 35, 66), 255, 255, 50, "#A7C7FF",
    ),
    ProfileTheme(
        5, "glassmorphism", "Glassmorphism", ("#121316", "#202329", "#101114"),
        ("#FFFFFF", "#D4D4D8", "#A1A1AA"), (28, 28, 34), 214, 178, 48, "#B9C2D0", True,
    ),
)

_THEMES = {theme.key: theme for theme in PROFILE_THEMES}
_THEMES_BY_ID = {theme.theme_id: theme for theme in PROFILE_THEMES}


def normalize_profile_theme(value: object) -> str:
    return panel_theme_key(value)


def get_profile_theme(value: object = "grey") -> ProfileTheme:
    return _THEMES_BY_ID[normalize_panel_theme_id(value)]


def profile_theme_choices() -> tuple[tuple[str, str], ...]:
    return tuple((theme.label, theme.key) for theme in PROFILE_THEMES)


def profile_theme_id(value: object = "grey") -> int:
    return get_profile_theme(value).theme_id


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
    """Return a compact panel preview on a neutral backdrop for Settings."""
    theme = get_profile_theme(value)
    return (
        "QFrame#profileThemePreview {"
        f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
        "stop:0 #121316, stop:0.52 #202329, stop:1 #101114);"
        f"border: 1px solid {_rgba(_hex_rgb(theme.bubbles[0]), 75)};"
        "border-radius: 10px; }"
        "QFrame#profileThemePreview QFrame#themePreviewPanel {"
        f"background: {_rgba(theme.panel_rgb, theme.panel_alpha)};"
        f"border: 1px solid {_rgba(theme.panel_rgb, theme.border_alpha)}; border-radius: 8px; }}"
    )
