"""Left sidebar navigation and top custom title bar for SafeLauncher."""

from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMenu, QLineEdit,
    QMainWindow, QDialog, QGraphicsDropShadowEffect, QScrollArea, QWidget, QSlider
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
    """Vertical navigation sidebar containing Library categories, Collections, and Preferences."""
    compact_changed = pyqtSignal(bool)
    filter_selected = pyqtSignal(str)          # 'all', 'installed', 'favorites', 'archived'
    collection_selected = pyqtSignal(str)      # Collection name (or '' for all collections)
    add_collection_requested = pyqtSignal()
    size_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.compact = False
        self.setFixedWidth(216)
        self.active_filter = "all"
        self.active_collection = ""
        self.collections = []

        self.setStyleSheet("""
            QFrame {
                background: #0D0F14;
                border-right: 1px solid #252A33;
            }
            QPushButton {
                background: transparent;
                color: #A7ADB8;
                border: 1px solid transparent;
                border-radius: 6px;
                padding: 7px 10px;
                text-align: left;
                font-weight: 500;
                font-size: 12px;
                min-height: 20px;
            }
            QPushButton:hover {
                background: #14171D;
                color: #F5F7FA;
                border: 1px solid #252A33;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 12)
        layout.setSpacing(4)

        # Collapse / Expand button
        self.btn_collapse = QPushButton("Hide panel")
        self.btn_collapse.setIcon(get_icon("ph.caret-double-left-bold", color="#6F7682"))
        self.btn_collapse.setIconSize(QSize(14, 14))
        self.btn_collapse.setFixedSize(196, 28)
        self.btn_collapse.setToolTip("Collapse sidebar")
        self.btn_collapse.setStyleSheet("""
            QPushButton {
                background: #14171D;
                color: #A7ADB8;
                border: 1px solid #252A33;
                border-radius: 6px;
                padding: 0;
                text-align: center;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover {
                background: #1A1E26;
                color: #F5F7FA;
                border-color: #6F7682;
            }
        """)
        self.btn_collapse.clicked.connect(self.toggle_compact)
        layout.addWidget(self.btn_collapse)
        layout.addSpacing(6)

        # ── SECTION 1: LIBRARY ──────────────────────────────────────────────
        self.lbl_lib = QLabel("LIBRARY")
        self.lbl_lib.setStyleSheet("color: #6F7682; font-size: 10px; font-weight: 600; padding-left: 6px; background: transparent; letter-spacing: 0.5px;")
        layout.addWidget(self.lbl_lib)

        nav_style = """
            QPushButton {
                color: #A7ADB8;
                text-align: left;
                padding: 7px 10px;
                border: 1px solid transparent;
                border-radius: 6px;
            }
            QPushButton:hover {
                background: #14171D;
                color: #F5F7FA;
                border: 1px solid #252A33;
            }
            QPushButton:checked {
                background: #0D2A40;
                color: #3B9FE8;
                font-weight: 600;
                border: 1px solid rgba(59, 159, 232, 0.3);
            }
        """

        self.nav_all = QPushButton("All Games")
        self.nav_all.setIcon(get_icon("ph.squares-four-bold", color="#3B9FE8"))
        self.nav_all.setCheckable(True)
        self.nav_all.setChecked(True)
        self.nav_all.setStyleSheet(nav_style)
        self.nav_all.clicked.connect(lambda: self._on_filter_click("all"))
        layout.addWidget(self.nav_all)

        self.nav_installed = QPushButton("Installed")
        self.nav_installed.setIcon(get_icon("ph.check-circle-bold", color="#35C98A"))
        self.nav_installed.setCheckable(True)
        self.nav_installed.setStyleSheet(nav_style)
        self.nav_installed.clicked.connect(lambda: self._on_filter_click("installed"))
        layout.addWidget(self.nav_installed)

        self.nav_favorites = QPushButton("Favorites")
        self.nav_favorites.setIcon(get_icon("ph.star-bold", color="#F5C451"))
        self.nav_favorites.setCheckable(True)
        self.nav_favorites.setStyleSheet(nav_style)
        self.nav_favorites.clicked.connect(lambda: self._on_filter_click("favorites"))
        layout.addWidget(self.nav_favorites)

        self.nav_archived = QPushButton("Archived")
        self.nav_archived.setIcon(get_icon("ph.archive-bold", color="#A7ADB8"))
        self.nav_archived.setCheckable(True)
        self.nav_archived.setToolTip("Games removed to archive (playtime & data preserved)")
        self.nav_archived.setStyleSheet(nav_style)
        self.nav_archived.clicked.connect(lambda: self._on_filter_click("archived"))
        layout.addWidget(self.nav_archived)

        layout.addSpacing(10)

        # ── SECTION 2: COLLECTIONS ──────────────────────────────────────────
        col_hdr_layout = QHBoxLayout()
        col_hdr_layout.setContentsMargins(6, 0, 0, 0)
        self.lbl_col = QLabel("COLLECTIONS")
        self.lbl_col.setStyleSheet("color: #6F7682; font-size: 10px; font-weight: 600; background: transparent; letter-spacing: 0.5px;")
        col_hdr_layout.addWidget(self.lbl_col)
        col_hdr_layout.addStretch()

        self.btn_add_col = QPushButton("+")
        self.btn_add_col.setFixedSize(22, 22)
        self.btn_add_col.setToolTip("Add new Collection")
        self.btn_add_col.setStyleSheet("""
            QPushButton {
                background: #14171D;
                color: #A7ADB8;
                border: 1px solid #252A33;
                border-radius: 4px;
                font-size: 13px;
                font-weight: 600;
                padding: 0;
                text-align: center;
            }
            QPushButton:hover {
                color: #F5F7FA;
                background: #1A1E26;
                border-color: #3B9FE8;
            }
        """)
        self.btn_add_col.clicked.connect(self.add_collection_requested.emit)
        col_hdr_layout.addWidget(self.btn_add_col)
        layout.addLayout(col_hdr_layout)

        # Scrollable Collections List
        self.col_scroll = QScrollArea()
        self.col_scroll.setWidgetResizable(True)
        self.col_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.col_container = QWidget()
        self.col_container.setStyleSheet("background: transparent;")
        self.col_layout = QVBoxLayout(self.col_container)
        self.col_layout.setContentsMargins(0, 0, 0, 0)
        self.col_layout.setSpacing(2)
        self.col_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.col_scroll.setWidget(self.col_container)
        layout.addWidget(self.col_scroll, 1)

        layout.addSpacing(6)

        # ── SECTION 3: PREFERENCES & SIZE SLIDER ───────────────────────────
        self.lbl_pref = QLabel("PREFERENCES")
        self.lbl_pref.setStyleSheet("color: #6F7682; font-size: 10px; font-weight: 600; padding-left: 6px; background: transparent; letter-spacing: 0.5px;")
        layout.addWidget(self.lbl_pref)

        self.btn_settings = QPushButton("Settings")
        self.btn_settings.setIcon(get_icon("ph.gear-bold", color="#A7ADB8"))
        self.btn_settings.setStyleSheet(nav_style)
        layout.addWidget(self.btn_settings)

        # Card size zoom slider
        self.zoom_box = QWidget()
        self.zoom_box.setStyleSheet("background: transparent;")
        zb_layout = QVBoxLayout(self.zoom_box)
        zb_layout.setContentsMargins(6, 4, 6, 4)
        zb_layout.setSpacing(4)

        self.lbl_size = QLabel("Card Size")
        self.lbl_size.setStyleSheet("color: #6F7682; font-size: 10px; font-weight: 600;")
        zb_layout.addWidget(self.lbl_size)

        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(140, 280)
        self.size_slider.setValue(200)
        self.size_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.size_slider.setStyleSheet("""
            QSlider::groove:horizontal { height: 4px; background: #252A33; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #3B9FE8; border-radius: 2px; }
            QSlider::handle:horizontal { background: #F5F7FA; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }
            QSlider::handle:horizontal:hover { background: #55ACED; }
        """)
        self.size_slider.valueChanged.connect(self.size_changed.emit)
        zb_layout.addWidget(self.size_slider)
        layout.addWidget(self.zoom_box)

        self._last_counts = (0, 0, 0, 0)
        self._raw_collections = []
        self._filter_buttons = [self.nav_all, self.nav_installed, self.nav_favorites, self.nav_archived]
        self._collection_buttons = []
        for btn in self._filter_buttons + [self.btn_settings]:
            btn.setIconSize(QSize(17, 17))

    def _on_filter_click(self, filter_mode: str):
        self.active_filter = filter_mode
        self.active_collection = ""
        for btn in self._filter_buttons:
            btn.setChecked(False)
        for btn in self._collection_buttons:
            btn.setChecked(False)

        if filter_mode == "all":
            self.nav_all.setChecked(True)
        elif filter_mode == "installed":
            self.nav_installed.setChecked(True)
        elif filter_mode == "favorites":
            self.nav_favorites.setChecked(True)
        elif filter_mode == "archived":
            self.nav_archived.setChecked(True)

        self.filter_selected.emit(filter_mode)

    def _on_collection_click(self, col_name: str, target_btn: QPushButton):
        self.active_filter = ""
        self.active_collection = col_name
        for btn in self._filter_buttons:
            btn.setChecked(False)
        for btn in self._collection_buttons:
            btn.setChecked(btn is target_btn)
        self.collection_selected.emit(col_name)

    def update_counts(self, all_c: int, inst_c: int, fav_c: int, arch_c: int):
        """Update count labels next to library navigation items."""
        self._last_counts = (all_c, inst_c, fav_c, arch_c)
        if not self.compact:
            self.nav_all.setText(f"All Games  ({all_c})")
            self.nav_installed.setText(f"Installed  ({inst_c})")
            self.nav_favorites.setText(f"Favorites  ({fav_c})")
            self.nav_archived.setText(f"Archived  ({arch_c})")
        else:
            self.nav_all.setText("")
            self.nav_installed.setText("")
            self.nav_favorites.setText("")
            self.nav_archived.setText("")

    def update_collections_list(self, collections_with_counts: list):
        """Rebuild dynamic collection entries in the left panel."""
        self._raw_collections = list(collections_with_counts)
        while self.col_layout.count() > 0:
            item = self.col_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._collection_buttons.clear()

        if not collections_with_counts:
            empty = QLabel("No collections yet")
            empty.setStyleSheet("color: #64748b; font-size: 11px; padding: 12px 6px; font-style: italic; background: transparent;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setVisible(not self.compact)
            self.col_layout.addWidget(empty)
            return

        for name, count in collections_with_counts:
            btn = QPushButton(f"{name}  ({count})" if not self.compact else "")
            btn.setProperty("col_name", name)
            btn.setProperty("col_count", count)
            btn.setIcon(get_icon("ph.folder-simple-bold", color="#22d3ee"))
            btn.setIconSize(QSize(16, 16))
            btn.setCheckable(True)
            btn.setChecked(self.active_collection == name)
            btn.setToolTip(f"{name} ({count} games)")
            btn.setStyleSheet("""
                QPushButton { color: #e2e8f0; text-align: left; padding: 7px 12px; }
                QPushButton:checked { background: #164e63; color: #22d3ee; font-weight: bold; }
                QPushButton:hover { background: #083344; color: #67e8f9; }
            """)
            btn.clicked.connect(lambda _, n=name, b=btn: self._on_collection_click(n, b))
            self.col_layout.addWidget(btn)
            self._collection_buttons.append(btn)

    def toggle_compact(self):
        self.set_compact(not self.compact)

    def set_compact(self, compact: bool):
        self.compact = bool(compact)
        self.setFixedWidth(56 if self.compact else 216)
        self.lbl_lib.setVisible(not self.compact)
        self.lbl_col.setVisible(not self.compact)
        self.lbl_pref.setVisible(not self.compact)
        self.btn_add_col.setVisible(not self.compact)
        self.zoom_box.setVisible(not self.compact)

        if self.compact:
            self.nav_all.setText("")
            self.nav_installed.setText("")
            self.nav_favorites.setText("")
            self.nav_archived.setText("")
            self.btn_settings.setText("")
            for btn in self._collection_buttons:
                btn.setText("")
        else:
            all_c, inst_c, fav_c, arch_c = getattr(self, '_last_counts', (0, 0, 0, 0))
            self.nav_all.setText(f"All Games  ({all_c})")
            self.nav_installed.setText(f"Installed  ({inst_c})")
            self.nav_favorites.setText(f"Favorites  ({fav_c})")
            self.nav_archived.setText(f"Archived  ({arch_c})")
            self.btn_settings.setText("Settings")
            for btn in self._collection_buttons:
                name = btn.property("col_name") or ""
                count = btn.property("col_count") if btn.property("col_count") is not None else 0
                btn.setText(f"{name}  ({count})")

        self.btn_collapse.setText("" if self.compact else "Hide panel")
        self.btn_collapse.setFixedSize(36 if self.compact else 196, 28)
        self.btn_collapse.setIcon(get_icon(
            "ph.caret-double-right-bold" if self.compact else "ph.caret-double-left-bold",
            color="#64748b",
        ))
        self.compact_changed.emit(self.compact)


class CustomTitleBar(QFrame):
    """Custom top title bar with Brand, Tools Dropdown, Rounded Search Bar, and Window Controls."""
    search_changed = pyqtSignal(str)
    sync_requested = pyqtSignal()
    install_archive_requested = pyqtSignal()
    check_updates_requested = pyqtSignal()
    open_sandbox_requested = pyqtSignal()
    export_save_requested = pyqtSignal()
    import_save_requested = pyqtSignal()
    disk_manager_requested = pyqtSignal()

    def __init__(self, main_window: QMainWindow):
        super().__init__(main_window)
        self.main_window = main_window
        self.drag_pos = None
        self.setFixedHeight(54)
        self.setStyleSheet("""
            QFrame {
                background: #080a0e;
                border: none;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(12)

        # Brand / Logo identity
        brand = QLabel("SafeLauncher")
        brand.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        brand.setStyleSheet("color: #ffffff; background: transparent; padding-right: 2px;")
        layout.addWidget(brand)

        # ── Tools Dropdown Menu (next to SafeLauncher in header to the right) ──
        self.btn_tools = QPushButton("Tools ▾")
        self.btn_tools.setIcon(get_icon("ph.wrench-bold", color="#a5b4fc"))
        self.btn_tools.setIconSize(QSize(15, 15))
        self.btn_tools.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tools.setFixedHeight(32)
        self.btn_tools.setStyleSheet("""
            QPushButton {
                background: #1e1b4b;
                color: #c7d2fe;
                border: none;
                border-radius: 6px;
                padding: 0 12px;
                font-size: 12px;
                font-weight: bold;
                text-align: center;
            }
            QPushButton:hover {
                background: #312e81;
                color: #ffffff;
            }
            QPushButton::menu-indicator { image: none; }
        """)

        self.tools_menu = QMenu(self)
        self.tools_menu.setStyleSheet("""
            QMenu {
                background-color: #0f1117;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                padding: 6px;
            }
            QMenu::item {
                padding: 8px 18px;
                border-radius: 4px;
                font-weight: 600;
                font-size: 12px;
            }
            QMenu::item:selected {
                background-color: #1e1b4b;
                color: #a5b4fc;
            }
            QMenu::separator {
                height: 1px;
                background: #1e293b;
                margin: 4px 8px;
            }
        """)

        act_sync = self.tools_menu.addAction(get_icon("ph.arrows-clockwise-bold", color="#38bdf8"), "Sync Sandbox Library")
        act_sync.triggered.connect(self.sync_requested.emit)

        act_inst = self.tools_menu.addAction(get_icon("ph.archive-bold", color="#a855f7"), "Install Game Archive (.zip/.tar)")
        act_inst.triggered.connect(self.install_archive_requested.emit)

        act_upd = self.tools_menu.addAction(get_icon("ph.arrows-clockwise-bold", color="#4ade80"), "Check for Steam Updates")
        act_upd.triggered.connect(self.check_updates_requested.emit)

        self.tools_menu.addSeparator()

        act_box = self.tools_menu.addAction(get_icon("ph.folder-open-bold", color="#facc15"), "Open Sandbox Directory")
        act_box.triggered.connect(self.open_sandbox_requested.emit)

        act_disk = self.tools_menu.addAction(get_icon("ph.chart-pie-slice-bold", color="#fb923c"), "Disk Space Manager")
        act_disk.triggered.connect(self.disk_manager_requested.emit)

        self.tools_menu.addSeparator()

        act_exp = self.tools_menu.addAction(get_app_icon("export"), "Export Game Save Backup (.zip)")
        act_exp.triggered.connect(self.export_save_requested.emit)

        act_imp = self.tools_menu.addAction(get_app_icon("import"), "Import Game Save Backup (.zip)")
        act_imp.triggered.connect(self.import_save_requested.emit)

        self.btn_tools.setMenu(self.tools_menu)
        layout.addWidget(self.btn_tools)

        # ── Nicely Rounded Search Bar with Embedded Magnifier ────────────────
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search library...")
        self.search_input.setFixedWidth(280)
        self.search_input.setFixedHeight(34)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.addAction(get_icon("ph.magnifying-glass-bold", color="#64748b"), QLineEdit.ActionPosition.LeadingPosition)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background: #11141c;
                color: #ffffff;
                border: none;
                border-radius: 17px;
                padding: 0 14px 0 34px;
                font-size: 12px;
            }
            QLineEdit:focus {
                background: #171b26;
            }
        """)
        self.search_input.textChanged.connect(self.search_changed.emit)
        layout.addWidget(self.search_input)

        layout.addStretch()

        # Window Control Buttons (Minimize, Maximize, Close)
        control_style = """
            QPushButton {
                background: #11141c;
                color: #94a3b8;
                border: none;
                border-radius: 6px;
                padding: 0;
                margin: 0;
                text-align: center;
                font-weight: bold;
            }
            QPushButton:hover { background: #1e293b; color: #ffffff; }
            QPushButton#windowClose:hover { background: #dc2626; color: #ffffff; }
        """

        self.btn_min = QPushButton()
        self.btn_min.setObjectName("windowMinimize")
        self.btn_min.setIcon(get_app_icon("minimize", color="#94a3b8"))
        self.btn_min.setIconSize(QSize(13, 13))
        self.btn_min.setFixedSize(32, 32)
        self.btn_min.setToolTip("Minimize window")
        self.btn_min.setStyleSheet(control_style)
        self.btn_min.clicked.connect(self.main_window.showMinimized)
        layout.addWidget(self.btn_min)

        self.btn_max = QPushButton()
        self.btn_max.setObjectName("windowMaximize")
        self.btn_max.setIcon(get_app_icon("maximize", color="#94a3b8"))
        self.btn_max.setIconSize(QSize(13, 13))
        self.btn_max.setFixedSize(32, 32)
        self.btn_max.setToolTip("Maximize window")
        self.btn_max.setStyleSheet(control_style)
        self.btn_max.clicked.connect(self._toggle_max_restore)
        layout.addWidget(self.btn_max)

        self.btn_close = QPushButton()
        self.btn_close.setObjectName("windowClose")
        self.btn_close.setIcon(get_app_icon("close", color="#94a3b8"))
        self.btn_close.setIconSize(QSize(13, 13))
        self.btn_close.setFixedSize(32, 32)
        self.btn_close.setToolTip("Close SafeLauncher")
        self.btn_close.setStyleSheet(control_style)
        self.btn_close.clicked.connect(self.main_window.close)
        layout.addWidget(self.btn_close)

    def _toggle_max_restore(self):
        if self.main_window.isMaximized():
            self.main_window.showNormal()
        else:
            self.main_window.showMaximized()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.main_window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self.drag_pos:
            if self.main_window.isMaximized():
                self.main_window.showNormal()
            self.main_window.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_max_restore()
            event.accept()


class DialogTitleBar(QFrame):
    """Clean custom titlebar for frameless dialogs."""
    def __init__(self, dialog: QDialog, title: str):
        super().__init__(dialog)
        self.dialog = dialog
        self.drag_pos = None
        self.setFixedHeight(48)
        self.setStyleSheet("""
            QFrame {
                background: #0f1117;
                border: none;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 14, 0)

        self.title_label = QLabel(title)
        self.title_label.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.title_label.setStyleSheet("color: #ffffff; background: transparent;")
        layout.addWidget(self.title_label)

        layout.addStretch()

        btn_close = QPushButton()
        btn_close.setIcon(get_app_icon("close", color="#94a3b8"))
        btn_close.setIconSize(QSize(14, 14))
        btn_close.setFixedSize(28, 28)
        btn_close.setStyleSheet("""
            QPushButton { background: transparent; border: none; border-radius: 4px; padding: 0; text-align: center; }
            QPushButton:hover { background: #dc2626; }
        """)
        btn_close.clicked.connect(self.dialog.reject)
        layout.addWidget(btn_close)

    def set_title(self, title: str):
        if hasattr(self, 'title_label'):
            self.title_label.setText(title)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.dialog.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self.drag_pos:
            self.dialog.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()
