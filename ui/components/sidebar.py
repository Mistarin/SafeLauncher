from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMenu, QLineEdit, QMainWindow, QDialog, QGraphicsDropShadowEffect
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QFont, QColor

from ui.icons import get_app_icon, get_icon


def add_soft_shadow(widget, blur=18, y=4, alpha=80):
    """Utility helper to attach a soft subtle drop shadow to a Qt widget."""
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setYOffset(y)
    shadow.setXOffset(0)
    shadow.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(shadow)


class LeftSidebarWidget(QFrame):
    """Vertical navigation sidebar containing launcher actions, category links, and status info."""
    compact_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.compact = False
        self.setFixedWidth(216)
        self.setStyleSheet("""
            QFrame {
                background: rgba(16, 16, 20, 0.88);
                border-right: 1px solid rgba(255, 255, 255, 0.08);
            }
            QPushButton {
                background: transparent;
                color: #a1a1aa;
                border: 1px solid transparent;
                border-radius: 6px;
                padding: 10px 14px;
                text-align: left;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.06);
                color: #ffffff;
            }
            QPushButton:checked {
                background: #272730;
                color: #ffffff;
                border: 1px solid #3f3f46;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 18)
        layout.setSpacing(5)

        # Sidebar controls
        brand_row = QHBoxLayout()

        self.btn_collapse = QPushButton()
        self.btn_collapse.setText("Hide panel")
        self.btn_collapse.setIcon(get_icon("ph.caret-double-left", color="#a1a1aa"))
        self.btn_collapse.setIconSize(QSize(16, 16))
        self.btn_collapse.setFixedSize(188, 30)
        self.btn_collapse.setToolTip("Collapse sidebar")
        self.btn_collapse.setStyleSheet("""
            QPushButton { background: #08090b; color: #c9ccd2; border: none; border-radius: 8px; padding: 0; text-align: center; font-size: 11px; font-weight: bold; }
            QPushButton:hover { background: #17191e; color: #ffffff; }
        """)
        self.btn_collapse.clicked.connect(self.toggle_compact)
        add_soft_shadow(self.btn_collapse, blur=16, y=3, alpha=100)
        brand_row.addWidget(self.btn_collapse)
        layout.addLayout(brand_row)

        layout.addSpacing(5)

        # Navigation Header Label
        lbl_nav = QLabel("NAVIGATION")
        lbl_nav.setStyleSheet("color: #52525b; font-size: 10px; font-weight: bold; padding-left: 4px; background: transparent;")
        layout.addWidget(lbl_nav)

        # Vertical Navigation Stack
        self.nav_library = QPushButton(" My Library")
        self.nav_library.setIcon(get_icon("ph.books", color="#d5d7dc"))
        self.nav_library.setCheckable(True)
        self.nav_library.setChecked(True)
        self.nav_library.setStyleSheet("""
            QPushButton {
                background: #25282e;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                padding: 11px 14px;
                text-align: left;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover { background: #25282e; }
            QPushButton:checked { background: #25282e; color: #ffffff; border: none; }
        """)
        layout.addWidget(self.nav_library)

        self.nav_updates = QPushButton(" Check for Updates")
        self.nav_updates.setIcon(get_icon("ph.arrows-clockwise", color="#d5d7dc"))
        self.nav_updates.setToolTip("Check all Steam games for updates")
        layout.addWidget(self.nav_updates)

        self.nav_sandbox = QPushButton(" Open Sandbox")
        self.nav_sandbox.setIcon(get_icon("ph.folder-open", color="#d5d7dc"))
        layout.addWidget(self.nav_sandbox)

        self.nav_install_zip = QPushButton(" Install Archive")
        self.nav_install_zip.setIcon(get_icon("ph.archive", color="#d5d7dc"))
        self.nav_install_zip.setToolTip("Install a game from a ZIP, 7z, TAR, TAR.GZ, or TGZ archive")
        layout.addWidget(self.nav_install_zip)

        self.nav_sync = QPushButton(" Sync Library")
        self.nav_sync.setIcon(get_icon("ph.arrows-clockwise", color="#d5d7dc"))
        self.nav_sync.setToolTip("Sync Sandbox Library")
        layout.addWidget(self.nav_sync)

        self.nav_disk = QPushButton(" Disk Manager")
        self.nav_disk.setIcon(get_icon("ph.magnifying-glass", color="#d5d7dc"))
        self.nav_disk.setToolTip("Inspect sandbox storage and game sizes")
        layout.addWidget(self.nav_disk)

        self.btn_saves = QPushButton(" Saves Manager")
        self.btn_saves.setIcon(get_icon("ph.floppy-disk", color="#d5d7dc"))
        
        saves_menu = QMenu(self)
        saves_menu.setStyleSheet("""
            QMenu {
                background-color: #1a1a1a;
                color: #ffffff;
                border: 1px solid #333333;
                border-radius: 6px;
                padding: 6px;
            }
            QMenu::item {
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 12px;
            }
            QMenu::item:selected {
                background-color: #1e293b;
                color: #ffffff;
            }
        """)
        self.act_export = saves_menu.addAction(get_app_icon("export"), " Export Game Save")
        self.act_import = saves_menu.addAction(get_app_icon("import"), " Import Game Save")
        self.btn_saves.setMenu(saves_menu)
        layout.addWidget(self.btn_saves)

        layout.addSpacing(14)
        lbl_tools = QLabel("PREFERENCES")
        lbl_tools.setStyleSheet("color: #52525b; font-size: 10px; font-weight: bold; padding-left: 4px; background: transparent;")
        layout.addWidget(lbl_tools)

        self.btn_settings = QPushButton(" Settings")
        self.btn_settings.setIcon(get_icon("ph.gear", color="#d5d7dc"))
        layout.addWidget(self.btn_settings)

        layout.addStretch()

        # Installed Games Counter badge in sidebar
        self.stat_label = QLabel("0 Games Installed")
        self.stat_label.setStyleSheet("color: #71717a; font-size: 11px; font-weight: bold; padding: 6px; background: transparent;")
        layout.addWidget(self.stat_label)

        self._section_labels = (lbl_nav, lbl_tools)
        self._navigation_buttons = (
            (self.nav_library, "My Library"),
            (self.nav_updates, "Check for Updates"),
            (self.nav_sandbox, "Open Sandbox"),
            (self.nav_install_zip, "Install Archive"),
            (self.nav_sync, "Sync Library"),
            (self.nav_disk, "Disk Manager"),
            (self.btn_saves, "Saves Manager"),
            (self.btn_settings, "Settings"),
        )
        for button, _ in self._navigation_buttons:
            button.setIconSize(QSize(21, 21))
            add_soft_shadow(button, blur=14, y=3, alpha=55)

    def toggle_compact(self):
        self.set_compact(not self.compact)

    def set_compact(self, compact: bool):
        """Switch between the full navigation and an icon-only rail."""
        self.compact = bool(compact)
        self.setFixedWidth(72 if self.compact else 216)
        for label in self._section_labels:
            label.setVisible(not self.compact)
        self.stat_label.setVisible(not self.compact)

        for button, label in self._navigation_buttons:
            button.setText("" if self.compact else f" {label}")
            button.setToolTip(label if self.compact else "")
            button.setMinimumHeight(42 if self.compact else 0)
            button.setStyleSheet("""
                QPushButton { text-align: center; padding: 9px; }
                QPushButton:hover { background: rgba(255, 255, 255, 0.06); color: #ffffff; }
                QPushButton:checked { background: #272730; color: #ffffff; border: 1px solid #3f3f46; }
            """ if self.compact else "")

        self.btn_collapse.setText("" if self.compact else "Hide panel")
        self.btn_collapse.setFixedSize(44 if self.compact else 188, 26 if self.compact else 30)
        self.btn_collapse.setIcon(get_icon(
            "ph.caret-double-right" if self.compact else "ph.caret-double-left",
            color="#a1a1aa",
        ))
        self.btn_collapse.setToolTip("Expand sidebar" if self.compact else "Collapse sidebar")
        self.compact_changed.emit(self.compact)


class CustomTitleBar(QFrame):
    """Custom top title bar containing search input, window dragging, double-click maximize, and app control buttons."""
    search_changed = pyqtSignal(str)

    def __init__(self, main_window: QMainWindow):
        super().__init__(main_window)
        self.main_window = main_window
        self.drag_pos = None
        self.setFixedHeight(58)
        self.setStyleSheet("""
            QFrame {
                background: #0c0e12;
                border-bottom: 1px solid #242832;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 0, 14, 0)
        layout.setSpacing(8)

        # Keep the app identity in the chrome so the content header can stay focused.
        brand = QLabel("SafeLauncher")
        brand.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        brand.setStyleSheet("color: #ffffff; background: transparent; padding-right: 4px;")
        layout.addWidget(brand)

        # Universal Search Bar with embedded search icon
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search library...")
        self.search_input.setFixedWidth(240)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background: #18181b;
                color: #ffffff;
                border: 1px solid #272730;
                border-radius: 8px;
                padding: 6px 12px 6px 12px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #3f3f46;
                background: #1f1f23;
            }
        """)
        self.search_input.textChanged.connect(self.search_changed.emit)
        layout.addWidget(self.search_input)

        layout.addStretch()

        # Keep the title-bar controls visible while using the app's vector
        # icons. Text remains as a fallback when the optional icon provider is
        # unavailable in a packaged build.
        control_style = """
            QPushButton {
                background: #20242c;
                color: #f4f4f5;
                border: 1px solid #3b414c;
                border-radius: 5px;
                padding: 0;
                font-weight: bold;
            }
            QPushButton:hover { background: #343b47; border-color: #626b7a; }
            QPushButton#windowClose:hover { background: #dc2626; border-color: #f87171; }
        """

        self.btn_min = QPushButton()
        self.btn_min.setObjectName("windowMinimize")
        self.btn_min.setIcon(get_app_icon("minimize", color="#f4f4f5"))
        self.btn_min.setIconSize(QSize(14, 14))
        if self.btn_min.icon().isNull():
            self.btn_min.setText("-")
        self.btn_min.setFixedSize(32, 32)
        self.btn_min.setToolTip("Minimize window")
        self.btn_min.setAccessibleName("Minimize window")
        self.btn_min.setStyleSheet(control_style)
        self.btn_min.clicked.connect(self.main_window.showMinimized)
        layout.addWidget(self.btn_min)

        self.btn_max = QPushButton()
        self.btn_max.setObjectName("windowMaximize")
        self.btn_max.setIcon(get_app_icon("maximize", color="#f4f4f5"))
        self.btn_max.setIconSize(QSize(14, 14))
        if self.btn_max.icon().isNull():
            self.btn_max.setText("[]")
        self.btn_max.setFixedSize(32, 32)
        self.btn_max.setToolTip("Maximize window")
        self.btn_max.setAccessibleName("Maximize window")
        self.btn_max.setStyleSheet(control_style)
        self.btn_max.clicked.connect(self.main_window._toggle_maximize)
        layout.addWidget(self.btn_max)

        self.btn_close = QPushButton()
        self.btn_close.setObjectName("windowClose")
        self.btn_close.setIcon(get_app_icon("close", color="#f4f4f5"))
        self.btn_close.setIconSize(QSize(14, 14))
        if self.btn_close.icon().isNull():
            self.btn_close.setText("X")
        self.btn_close.setFixedSize(32, 32)
        self.btn_close.setToolTip("Close window")
        self.btn_close.setAccessibleName("Close window")
        self.btn_close.setStyleSheet(control_style)
        self.btn_close.clicked.connect(self.main_window.close)
        layout.addWidget(self.btn_close)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            widget = self.childAt(event.position().toPoint())
            if not isinstance(widget, (QPushButton, QLineEdit)):
                handle = self.main_window.windowHandle()
                if handle and hasattr(handle, "startSystemMove"):
                    handle.startSystemMove()
                else:
                    self.drag_pos = event.globalPosition().toPoint() - self.main_window.frameGeometry().topLeft()
                event.accept()

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.MouseButton.LeftButton) and getattr(self, 'drag_pos', None) is not None:
            self.main_window.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            widget = self.childAt(event.position().toPoint())
            if not isinstance(widget, (QPushButton, QLineEdit)):
                self.main_window._toggle_maximize()
                event.accept()


class DialogTitleBar(QFrame):
    """Custom top drag bar for modal dialogs with title and close button."""
    def __init__(self, dialog: QDialog, title: str):
        super().__init__(dialog)
        self.dialog = dialog
        self.drag_pos = None
        self.setFixedHeight(38)
        self.setStyleSheet("""
            QFrame {
                background: #090909;
                border-bottom: 1px solid #222222;
            }
            QLabel {
                color: #ffffff;
                font-weight: bold;
                font-size: 13px;
                background: transparent;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 8, 0)

        self.title_label = QLabel(title)
        layout.addWidget(self.title_label)
        layout.addStretch()

        btn_close = QPushButton()
        btn_close.setIcon(get_app_icon("close"))
        btn_close.setFixedSize(26, 26)
        btn_close.setStyleSheet("QPushButton { background: transparent; border-radius: 4px; padding: 0px; } QPushButton:hover { background: #c62828; }")
        btn_close.clicked.connect(self.dialog.reject)
        layout.addWidget(btn_close)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            widget = self.childAt(event.position().toPoint())
            if not isinstance(widget, QPushButton):
                handle = self.dialog.windowHandle()
                if handle and hasattr(handle, "startSystemMove"):
                    handle.startSystemMove()
                else:
                    self.drag_pos = event.globalPosition().toPoint() - self.dialog.frameGeometry().topLeft()
                event.accept()

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.MouseButton.LeftButton) and getattr(self, 'drag_pos', None) is not None:
            self.dialog.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_pos = None
        super().mouseReleaseEvent(event)
