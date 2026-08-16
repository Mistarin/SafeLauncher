"""SafeLauncher Design System & Modern Dark SaaS Theme.

Inspired by Apple, Linear, and high-end developer tools.
Restrained, calm, precise, and highly polished dark aesthetic.
"""

# ── Core Palette ─────────────────────────────────────────────────────────────
BG_APP = "#0D0F14"
SURFACE = "#14171D"
SURFACE_ELEVATED = "#1A1E26"
BORDER = "#252A33"
TEXT_PRIMARY = "#F5F7FA"
TEXT_SECONDARY = "#A7ADB8"
TEXT_MUTED = "#6F7682"

# ── Brand Accent ─────────────────────────────────────────────────────────────
ACCENT_PRIMARY = "#3B9FE8"
ACCENT_HOVER = "#55ACED"
ACCENT_PRESSED = "#2789D0"
ACCENT_SUBTLE_BG = "#0D2A40"

# ── Semantic Colors ──────────────────────────────────────────────────────────
SEMANTIC_SUCCESS = "#35C98A"
SEMANTIC_WARNING = "#E5A93D"
SEMANTIC_ERROR = "#F05D6C"
SEMANTIC_FAVORITE = "#F5C451"
SEMANTIC_UTILITY = "#E5E7EB"

# ── Typography ───────────────────────────────────────────────────────────────
FONT_FAMILY = '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Inter", "Segoe UI", "Geist", Roboto, Helvetica, Arial, sans-serif'
FONT_FAMILY_MONO = '"SF Mono", "Geist Mono", "JetBrains Mono", Menlo, Consolas, monospace'


def get_application_stylesheet() -> str:
    """Generate the complete, cohesive dark SaaS global stylesheet."""
    return f"""
    * {{
        font-family: {FONT_FAMILY};
        outline: none;
    }}

    QMainWindow, QDialog, QWidget#centralWidget, QWidget#libraryCentralPanel {{
        background-color: {BG_APP};
        color: {TEXT_PRIMARY};
    }}

    /* ── Scrollbars ── */
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 0px;
    }}
    QScrollBar::handle:vertical {{
        background: {BORDER};
        min-height: 24px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {TEXT_MUTED};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: none;
        height: 0px;
    }}

    QScrollBar:horizontal {{
        background: transparent;
        height: 8px;
        margin: 0px;
    }}
    QScrollBar::handle:horizontal {{
        background: {BORDER};
        min-width: 24px;
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {TEXT_MUTED};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: none;
        width: 0px;
    }}

    /* ── Menus & Context Menus ── */
    QMenu {{
        background-color: {SURFACE_ELEVATED};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 4px;
    }}
    QMenu::item {{
        background: transparent;
        padding: 6px 12px 6px 8px;
        border-radius: 4px;
        font-size: 12px;
        color: {TEXT_PRIMARY};
    }}
    QMenu::item:selected {{
        background-color: {BORDER};
        color: {TEXT_PRIMARY};
    }}
    QMenu::item:disabled {{
        color: {TEXT_MUTED};
    }}
    QMenu::separator {{
        height: 1px;
        background-color: {BORDER};
        margin: 4px 6px;
    }}

    /* ── Tooltips ── */
    QToolTip {{
        background-color: {SURFACE_ELEVATED};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 4px 8px;
        font-size: 11px;
    }}

    /* ── Inputs & Text Edits ── */
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {{
        background-color: {SURFACE};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 6px 10px;
        font-size: 12px;
        selection-background-color: {ACCENT_SUBTLE_BG};
        selection-color: {TEXT_PRIMARY};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {{
        border: 1px solid {ACCENT_PRIMARY};
        background-color: {SURFACE};
    }}
    QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
        background-color: {BG_APP};
        color: {TEXT_MUTED};
        border: 1px solid {BORDER};
    }}

    /* ── Combo Boxes ── */
    QComboBox {{
        background-color: {SURFACE};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 6px 12px;
        font-size: 12px;
        min-height: 20px;
    }}
    QComboBox:hover {{
        border: 1px solid {TEXT_MUTED};
    }}
    QComboBox:focus {{
        border: 1px solid {ACCENT_PRIMARY};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 24px;
        border-left: none;
    }}
    QComboBox::down-arrow {{
        image: none;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid {TEXT_SECONDARY};
        width: 0px;
        height: 0px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {SURFACE_ELEVATED};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 4px;
        selection-background-color: {BORDER};
        selection-color: {TEXT_PRIMARY};
    }}

    /* ── Checkboxes ── */
    QCheckBox {{
        color: {TEXT_PRIMARY};
        font-size: 12px;
        spacing: 8px;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {BORDER};
        border-radius: 4px;
        background-color: {SURFACE};
    }}
    QCheckBox::indicator:hover {{
        border: 1px solid {TEXT_MUTED};
    }}
    QCheckBox::indicator:checked {{
        background-color: {ACCENT_PRIMARY};
        border: 1px solid {ACCENT_PRIMARY};
    }}

    /* ── Tables & Lists ── */
    QTableWidget, QTableView, QListWidget, QListView, QTreeWidget, QTreeView {{
        background-color: {SURFACE};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 8px;
        gridline-color: {BORDER};
        selection-background-color: {BORDER};
        selection-color: {TEXT_PRIMARY};
        font-size: 12px;
    }}
    QHeaderView::section {{
        background-color: {SURFACE_ELEVATED};
        color: {TEXT_SECONDARY};
        border: none;
        border-bottom: 1px solid {BORDER};
        border-right: 1px solid {BORDER};
        padding: 6px 10px;
        font-size: 11px;
        font-weight: 600;
    }}

    /* ── Tabs ── */
    QTabWidget::pane {{
        border: 1px solid {BORDER};
        background: {SURFACE};
        border-radius: 8px;
    }}
    QTabBar::tab {{
        background: transparent;
        color: {TEXT_SECONDARY};
        padding: 8px 16px;
        font-size: 12px;
        font-weight: 500;
        border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:hover {{
        color: {TEXT_PRIMARY};
    }}
    QTabBar::tab:selected {{
        color: {ACCENT_PRIMARY};
        border-bottom: 2px solid {ACCENT_PRIMARY};
        font-weight: 600;
    }}

    /* ── Sliders ── */
    QSlider::groove:horizontal {{
        background: {BORDER};
        height: 4px;
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {ACCENT_PRIMARY};
        height: 4px;
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {TEXT_PRIMARY};
        width: 14px;
        margin-top: -5px;
        margin-bottom: -5px;
        border-radius: 7px;
    }}
    QSlider::handle:horizontal:hover {{
        background: {ACCENT_HOVER};
    }}

    /* ── Splitters ── */
    QSplitter::handle {{
        background: transparent;
    }}
    QSplitter::handle:horizontal {{
        width: 0px;
    }}
    QSplitter::handle:vertical {{
        height: 0px;
    }}
    """


# ── Button Component Styles ──────────────────────────────────────────────────

def btn_primary_style() -> str:
    """Refined blue accent background, white text, subtle transitions, no glow."""
    return f"""
        QPushButton {{
            background-color: {ACCENT_PRIMARY};
            color: #FFFFFF;
            border: 1px solid {ACCENT_PRIMARY};
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            padding: 7px 16px;
            letter-spacing: 0.2px;
        }}
        QPushButton:hover {{
            background-color: {ACCENT_HOVER};
            border-color: {ACCENT_HOVER};
        }}
        QPushButton:pressed {{
            background-color: {ACCENT_PRESSED};
            border-color: {ACCENT_PRESSED};
        }}
        QPushButton:disabled {{
            background-color: {SURFACE_ELEVATED};
            color: {TEXT_MUTED};
            border-color: {BORDER};
        }}
    """


def btn_secondary_style() -> str:
    """Dark elevated surface, light text, subtle border."""
    return f"""
        QPushButton {{
            background-color: {SURFACE_ELEVATED};
            color: {TEXT_PRIMARY};
            border: 1px solid {BORDER};
            border-radius: 6px;
            font-size: 12px;
            font-weight: 500;
            padding: 7px 14px;
        }}
        QPushButton:hover {{
            background-color: {BORDER};
            border-color: {TEXT_MUTED};
            color: {TEXT_PRIMARY};
        }}
        QPushButton:pressed {{
            background-color: {SURFACE};
            border-color: {BORDER};
        }}
        QPushButton:disabled {{
            color: {TEXT_MUTED};
            border-color: {BORDER};
        }}
    """


def btn_tertiary_style() -> str:
    """Mostly transparent, secondary text, accent appears on hover."""
    return f"""
        QPushButton {{
            background-color: transparent;
            color: {TEXT_SECONDARY};
            border: 1px solid transparent;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 500;
            padding: 6px 12px;
        }}
        QPushButton:hover {{
            background-color: {SURFACE_ELEVATED};
            color: {TEXT_PRIMARY};
            border-color: {BORDER};
        }}
        QPushButton:pressed {{
            background-color: {BORDER};
        }}
        QPushButton:disabled {{
            color: {TEXT_MUTED};
        }}
    """


def btn_destructive_style() -> str:
    """Subtle restrained error action rather than aggressive bright red."""
    return f"""
        QPushButton {{
            background-color: rgba(240, 93, 108, 0.08);
            color: {SEMANTIC_ERROR};
            border: 1px solid rgba(240, 93, 108, 0.25);
            border-radius: 6px;
            font-size: 12px;
            font-weight: 500;
            padding: 7px 14px;
        }}
        QPushButton:hover {{
            background-color: rgba(240, 93, 108, 0.16);
            border-color: {SEMANTIC_ERROR};
            color: #FFFFFF;
        }}
        QPushButton:pressed {{
            background-color: rgba(240, 93, 108, 0.24);
        }}
        QPushButton:disabled {{
            background-color: transparent;
            color: {TEXT_MUTED};
            border-color: {BORDER};
        }}
    """


def search_input_style() -> str:
    """Sleek, modern search bar for header and library filter."""
    return f"""
        QLineEdit {{
            background-color: {SURFACE};
            color: {TEXT_PRIMARY};
            border: 1px solid {BORDER};
            border-radius: 6px;
            padding: 6px 12px 6px 32px;
            font-size: 12px;
        }}
        QLineEdit:focus {{
            border: 1px solid {ACCENT_PRIMARY};
            background-color: {SURFACE};
        }}
        QLineEdit::placeholder {{
            color: {TEXT_MUTED};
        }}
    """
