import os
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMenu, QLineEdit,
    QMainWindow, QDialog, QGraphicsDropShadowEffect, QScrollArea, QWidget, QSlider
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QPixmap

from ui.icons import get_app_icon, get_icon, LOGO_PATH


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
        self.compact = True
        self.setFixedWidth(48)
        self.active_filter = "all"
        self.active_collection = ""
        self.collections = []

        self.setStyleSheet("""
            QFrame {
                background: #161618;
                border: none;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(4)

        # Collapse / Expand button
        self.btn_collapse = QPushButton("")
        self.btn_collapse.setIcon(get_icon("ph.caret-double-right-bold", color="#FFFFFF"))
        self.btn_collapse.setIconSize(QSize(13, 13))
        self.btn_collapse.setFixedSize(36, 26)
        self.btn_collapse.setToolTip("Expand collections panel")
        self.btn_collapse.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #A1A1A6;
                border: none;
                border-radius: 6px;
                padding: 0;
                text-align: center;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.05);
                color: #FFFFFF;
            }
        """)
        self.btn_collapse.clicked.connect(self.toggle_compact)
        layout.addWidget(self.btn_collapse)
        layout.addSpacing(2)

        # ── SECTION 1: LIBRARY (Hidden in collection-only mode, kept for compatibility) ──
        self.lbl_lib = QLabel("LIBRARY")
        self.lbl_lib.setStyleSheet("color: #FFFFFF; font-size: 10px; font-weight: 700; padding: 6px 0 2px 8px; background: transparent; letter-spacing: 0.8px;")
        self.lbl_lib.setVisible(False)
        layout.addWidget(self.lbl_lib)

        nav_style = """
            QPushButton {
                background: transparent;
                color: #D4D4D8;
                text-align: left;
                padding: 6px 10px;
                border: none;
                border-radius: 7px;
                font-size: 12px;
                font-weight: 500;
                min-height: 18px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.05);
                color: #FFFFFF;
            }
            QPushButton:checked {
                background: rgba(255, 255, 255, 0.10);
                color: #FFFFFF;
                font-weight: 600;
            }
        """

        self.nav_all = QPushButton("All Games")
        self.nav_all.setIcon(get_icon("ph.squares-four-bold", color="#FFFFFF"))
        self.nav_all.setCheckable(True)
        self.nav_all.setChecked(True)
        self.nav_all.setStyleSheet(nav_style)
        self.nav_all.setVisible(False)
        self.nav_all.clicked.connect(lambda: self._on_filter_click("all"))
        layout.addWidget(self.nav_all)

        self.nav_installed = QPushButton("Installed")
        self.nav_installed.setIcon(get_icon("ph.check-circle-bold", color="#FFFFFF"))
        self.nav_installed.setCheckable(True)
        self.nav_installed.setStyleSheet(nav_style)
        self.nav_installed.setVisible(False)
        self.nav_installed.clicked.connect(lambda: self._on_filter_click("installed"))
        layout.addWidget(self.nav_installed)

        self.nav_favorites = QPushButton("Favorites")
        self.nav_favorites.setIcon(get_icon("ph.star-bold", color="#FFFFFF"))
        self.nav_favorites.setCheckable(True)
        self.nav_favorites.setStyleSheet(nav_style)
        self.nav_favorites.setVisible(False)
        self.nav_favorites.clicked.connect(lambda: self._on_filter_click("favorites"))
        layout.addWidget(self.nav_favorites)

        self.nav_archived = QPushButton("Archived")
        self.nav_archived.setIcon(get_icon("ph.archive-bold", color="#FFFFFF"))
        self.nav_archived.setCheckable(True)
        self.nav_archived.setToolTip("Games removed to archive (playtime & data preserved)")
        self.nav_archived.setStyleSheet(nav_style)
        self.nav_archived.setVisible(False)
        self.nav_archived.clicked.connect(lambda: self._on_filter_click("archived"))
        layout.addWidget(self.nav_archived)

        # ── SECTION 2: COLLECTIONS ──────────────────────────────────────────
        col_hdr_layout = QHBoxLayout()
        col_hdr_layout.setContentsMargins(6, 4, 4, 2)
        self.lbl_col = QLabel("COLLECTIONS")
        self.lbl_col.setStyleSheet("color: #FFFFFF; font-size: 10px; font-weight: 700; background: transparent; letter-spacing: 0.8px;")
        self.lbl_col.setVisible(False)
        col_hdr_layout.addWidget(self.lbl_col)
        col_hdr_layout.addStretch()

        self.btn_add_col = QPushButton("+")
        self.btn_add_col.setVisible(False)
        self.btn_add_col.clicked.connect(self.add_collection_requested.emit)
        layout.addLayout(col_hdr_layout)

        # Scrollable Collections List without intrusive scrollbars
        self.col_scroll = QScrollArea()
        self.col_scroll.setWidgetResizable(True)
        self.col_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.col_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.col_scroll.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 3px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.12);
                border-radius: 1.5px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.25);
            }
        """)
        self.col_container = QWidget()
        self.col_container.setStyleSheet("background: transparent;")
        self.col_layout = QVBoxLayout(self.col_container)
        self.col_layout.setContentsMargins(0, 0, 0, 0)
        self.col_layout.setSpacing(2)
        self.col_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.col_scroll.setWidget(self.col_container)
        layout.addWidget(self.col_scroll, 1)

        # ── SECTION 3: PREFERENCES & SIZE SLIDER (Kept for compatibility, hidden) ──
        self.lbl_pref = QLabel("PREFERENCES")
        self.lbl_pref.setStyleSheet("color: #FFFFFF; font-size: 10px; font-weight: 700; padding: 6px 0 2px 8px; background: transparent; letter-spacing: 0.8px;")
        self.lbl_pref.setVisible(False)
        layout.addWidget(self.lbl_pref)

        self.btn_settings = QPushButton("Settings")
        self.btn_settings.setIcon(get_icon("ph.gear-bold", color="#FFFFFF"))
        self.btn_settings.setStyleSheet(nav_style)
        self.btn_settings.setVisible(False)
        layout.addWidget(self.btn_settings)

        # Card size zoom slider (moved to Settings)
        self.zoom_box = QWidget()
        self.zoom_box.setStyleSheet("background: transparent;")
        self.zoom_box.setVisible(False)
        zb_layout = QVBoxLayout(self.zoom_box)
        zb_layout.setContentsMargins(8, 4, 8, 4)
        zb_layout.setSpacing(4)

        self.lbl_size = QLabel("Card Size")
        self.lbl_size.setStyleSheet("color: #FFFFFF; font-size: 10px; font-weight: 600;")
        zb_layout.addWidget(self.lbl_size)

        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(140, 280)
        self.size_slider.setValue(200)
        self.size_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.size_slider.setStyleSheet("""
            QSlider::groove:horizontal { height: 3px; background: rgba(255, 255, 255, 0.08); border-radius: 1.5px; }
            QSlider::sub-page:horizontal { background: #3F3F46; border-radius: 1.5px; }
            QSlider::handle:horizontal { background: #FFFFFF; width: 12px; height: 12px; margin: -4.5px 0; border-radius: 6px; }
            QSlider::handle:horizontal:hover { background: #E5E5EA; }
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
        """Rebuild dynamic collection entries in the left panel with right-aligned grey counts."""
        self._raw_collections = list(collections_with_counts)
        while self.col_layout.count() > 0:
            item = self.col_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._collection_buttons.clear()

        for name, count in collections_with_counts:
            btn = QPushButton()
            btn.setProperty("col_name", name)
            btn.setProperty("col_count", count)
            btn.setCheckable(True)
            btn.setChecked(self.active_collection == name)
            btn.setToolTip(f"{name} ({count} games)")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    border: none;
                    border-radius: 6px;
                    min-height: 26px;
                }
                QPushButton:checked {
                    background: rgba(255, 255, 255, 0.12);
                }
                QPushButton:hover {
                    background: rgba(255, 255, 255, 0.06);
                }
            """)
            btn_layout = QHBoxLayout(btn)
            btn_layout.setContentsMargins(6, 2, 6, 2)
            btn_layout.setSpacing(6)

            icon_lbl = QLabel()
            icon_lbl.setPixmap(get_icon("ph.folder-simple-bold", color="#FFFFFF").pixmap(15, 15))
            icon_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            btn_layout.addWidget(icon_lbl)

            name_lbl = QLabel(name)
            name_lbl.setStyleSheet("color: #D4D4D8; font-size: 12px; font-weight: 500; background: transparent;")
            name_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            btn_layout.addWidget(name_lbl)

            btn_layout.addStretch()

            count_lbl = QLabel(str(count))
            count_lbl.setStyleSheet("color: #71717A; font-size: 11px; font-weight: 500; background: transparent;")
            count_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            btn_layout.addWidget(count_lbl)

            btn.name_lbl = name_lbl
            btn.count_lbl = count_lbl
            btn.icon_lbl = icon_lbl

            if self.compact:
                name_lbl.setVisible(False)
                count_lbl.setVisible(False)
                btn.setFixedSize(36, 28)
            else:
                name_lbl.setVisible(True)
                count_lbl.setVisible(True)
                btn.setFixedHeight(28)

            btn.clicked.connect(lambda _, n=name, b=btn: self._on_collection_click(n, b))
            self.col_layout.addWidget(btn)
            self._collection_buttons.append(btn)

        # Row button after the last collection to add a new collection (single plus icon)
        self.btn_add_col_row = QPushButton("New Collection" if not self.compact else "")
        self.btn_add_col_row.setIcon(get_icon("ph.plus-bold", color="#FFFFFF"))
        self.btn_add_col_row.setIconSize(QSize(12, 12))
        self.btn_add_col_row.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_col_row.setToolTip("Create a new Collection")
        self.btn_add_col_row.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #A1A1AA;
                text-align: center;
                padding: 4px 8px;
                border: none;
                border-radius: 6px;
                font-size: 11px;
                font-weight: 500;
                margin-top: 4px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.06);
                color: #FFFFFF;
            }
        """)
        self.btn_add_col_row.clicked.connect(self.add_collection_requested.emit)
        if self.compact:
            self.btn_add_col_row.setFixedSize(36, 26)
        else:
            self.btn_add_col_row.setFixedHeight(26)
        self.col_layout.addWidget(self.btn_add_col_row, alignment=Qt.AlignmentFlag.AlignCenter if self.compact else Qt.AlignmentFlag.AlignHCenter)

    def toggle_compact(self):
        self.set_compact(not self.compact)

    def set_compact(self, compact: bool):
        self.compact = bool(compact)
        self.setFixedWidth(48 if self.compact else 152)
        self.layout().setContentsMargins(4 if self.compact else 8, 8, 4 if self.compact else 8, 8)
        self.lbl_col.setVisible(not self.compact)
        self.btn_add_col.setVisible(False)

        if self.compact:
            self.btn_collapse.setText("")
            self.btn_collapse.setFixedSize(36, 26)
            self.btn_collapse.setIcon(get_icon("ph.caret-double-right-bold", color="#FFFFFF"))
            self.btn_collapse.setToolTip("Expand collections panel")
            for btn in self._collection_buttons:
                if hasattr(btn, "name_lbl"):
                    btn.name_lbl.setVisible(False)
                if hasattr(btn, "count_lbl"):
                    btn.count_lbl.setVisible(False)
                btn.setFixedSize(36, 28)
            if hasattr(self, "btn_add_col_row") and self.btn_add_col_row:
                self.btn_add_col_row.setText("")
                self.btn_add_col_row.setFixedSize(36, 26)
        else:
            self.btn_collapse.setText("Collapse")
            self.btn_collapse.setFixedSize(136, 26)
            self.btn_collapse.setIcon(get_icon("ph.caret-double-left-bold", color="#FFFFFF"))
            self.btn_collapse.setToolTip("Collapse collections panel")
            for btn in self._collection_buttons:
                if hasattr(btn, "name_lbl"):
                    btn.name_lbl.setVisible(True)
                if hasattr(btn, "count_lbl"):
                    btn.count_lbl.setVisible(True)
                btn.setFixedHeight(28)
                btn.setMinimumWidth(0)
                btn.setMaximumWidth(16777215)
            if hasattr(self, "btn_add_col_row") and self.btn_add_col_row:
                self.btn_add_col_row.setText("New Collection")
                self.btn_add_col_row.setFixedHeight(26)
                self.btn_add_col_row.setMinimumWidth(0)
                self.btn_add_col_row.setMaximumWidth(16777215)

        self.compact_changed.emit(self.compact)


class HeaderBar(QFrame):
    """Modern header bar with brand title, tools menu, search input, and window actions."""

    search_changed = pyqtSignal(str)
    filter_requested = pyqtSignal(str)
    settings_requested = pyqtSignal()
    toggle_collections_requested = pyqtSignal()
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
        self.setFixedHeight(46)
        self.setStyleSheet("""
            QFrame {
                background: #121214;
                border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(10)

        # Brand identity: small app logo + slim font SafeLauncher
        if os.path.exists(LOGO_PATH):
            self.logo_lbl = QLabel()
            self.logo_lbl.setPixmap(QPixmap(LOGO_PATH).scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self.logo_lbl.setFixedSize(18, 18)
            self.logo_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            layout.addWidget(self.logo_lbl)

        brand = QLabel("SafeLauncher")
        brand.setFont(QFont("Segoe UI", 11, QFont.Weight.Normal))
        brand.setStyleSheet("color: #E4E4E7; background: transparent; letter-spacing: 0.3px; font-weight: 400;")
        brand.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(brand)

        header_btn_style = """
            QPushButton {
                background: transparent;
                color: #C4C4C8;
                border: none;
                border-radius: 5px;
                padding: 0 8px;
                font-size: 11px;
                font-weight: 500;
                text-align: center;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.06);
                color: #FFFFFF;
            }
            QPushButton::menu-indicator { image: none; }
        """

        menu_style = """
            QMenu {
                background-color: #18181B;
                color: #F4F4F5;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item {
                padding: 7px 16px;
                border-radius: 5px;
                font-weight: 500;
                font-size: 12px;
                color: #F4F4F5;
            }
            QMenu::item:selected {
                background-color: rgba(255, 255, 255, 0.08);
                color: #FFFFFF;
            }
            QMenu::separator {
                height: 1px;
                background: rgba(255, 255, 255, 0.06);
                margin: 4px 6px;
            }
        """

        # ── View Dropdown Menu with Library Submenu ──
        self.btn_view = QPushButton("View ▾")
        self.btn_view.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_view.setFixedHeight(28)
        self.btn_view.setStyleSheet(header_btn_style)

        self.view_menu = QMenu(self)
        self.view_menu.setStyleSheet(menu_style)

        self.lib_menu = self.view_menu.addMenu(get_icon("ph.books-bold", color="#FFFFFF"), "Library")
        self.lib_menu.setStyleSheet(menu_style)
        act_lib_all = self.lib_menu.addAction(get_icon("ph.squares-four-bold", color="#FFFFFF"), "All Games")
        act_lib_all.triggered.connect(lambda: self.filter_requested.emit("all"))
        act_lib_inst = self.lib_menu.addAction(get_icon("ph.check-circle-bold", color="#FFFFFF"), "Installed")
        act_lib_inst.triggered.connect(lambda: self.filter_requested.emit("installed"))
        act_lib_fav = self.lib_menu.addAction(get_icon("ph.star-bold", color="#FFFFFF"), "Favorites")
        act_lib_fav.triggered.connect(lambda: self.filter_requested.emit("favorites"))
        act_lib_arch = self.lib_menu.addAction(get_icon("ph.archive-bold", color="#FFFFFF"), "Archived")
        act_lib_arch.triggered.connect(lambda: self.filter_requested.emit("archived"))

        self.view_menu.addSeparator()
        act_toggle_col = self.view_menu.addAction(get_icon("ph.folders-bold", color="#FFFFFF"), "Collapse Collections Panel")
        act_toggle_col.triggered.connect(self.toggle_collections_requested.emit)

        self.btn_view.setMenu(self.view_menu)
        layout.addWidget(self.btn_view)

        # ── Tools Dropdown Menu ──
        self.btn_tools = QPushButton("Tools ▾")
        self.btn_tools.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tools.setFixedHeight(28)
        self.btn_tools.setStyleSheet(header_btn_style)

        self.tools_menu = QMenu(self)
        self.tools_menu.setStyleSheet(menu_style)

        act_sync = self.tools_menu.addAction(get_icon("ph.arrows-clockwise-bold", color="#FFFFFF"), "Sync Sandbox Library")
        act_sync.triggered.connect(self.sync_requested.emit)

        act_inst = self.tools_menu.addAction(get_icon("ph.archive-bold", color="#FFFFFF"), "Install Game Archive (.zip/.tar)")
        act_inst.triggered.connect(self.install_archive_requested.emit)

        act_upd = self.tools_menu.addAction(get_icon("ph.arrows-clockwise-bold", color="#FFFFFF"), "Check for Steam Updates")
        act_upd.triggered.connect(self.check_updates_requested.emit)

        self.tools_menu.addSeparator()

        act_box = self.tools_menu.addAction(get_icon("ph.folder-open-bold", color="#FFFFFF"), "Open Sandbox Directory")
        act_box.triggered.connect(self.open_sandbox_requested.emit)

        act_disk = self.tools_menu.addAction(get_icon("ph.chart-pie-slice-bold", color="#FFFFFF"), "Disk Space Manager")
        act_disk.triggered.connect(self.disk_manager_requested.emit)

        self.tools_menu.addSeparator()

        act_exp = self.tools_menu.addAction(get_app_icon("export", color="#FFFFFF"), "Export Game Save Backup (.zip)")
        act_exp.triggered.connect(self.export_save_requested.emit)

        act_imp = self.tools_menu.addAction(get_app_icon("import", color="#FFFFFF"), "Import Game Save Backup (.zip)")
        act_imp.triggered.connect(self.import_save_requested.emit)

        self.tools_menu.addSeparator()

        act_settings = self.tools_menu.addAction(get_icon("ph.gear-bold", color="#FFFFFF"), "Settings...")
        act_settings.triggered.connect(self.settings_requested.emit)

        self.btn_tools.setMenu(self.tools_menu)
        layout.addWidget(self.btn_tools)

        # Retained for backwards-compatibility; hidden as search now resides in each view toolbar
        self.search_input = QLineEdit()
        self.search_input.setVisible(False)
        self.search_input.textChanged.connect(self.search_changed.emit)

        layout.addStretch()

        # Window Control Buttons
        control_style = """
            QPushButton {
                background: transparent;
                color: #8E8E93;
                border: none;
                border-radius: 6px;
                padding: 0;
                margin: 0;
                text-align: center;
                font-weight: 500;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.08);
                color: #FFFFFF;
            }
            QPushButton#windowClose:hover {
                background: rgba(255, 69, 58, 0.2);
                color: #FF453A;
            }
        """

        self.btn_min = QPushButton()
        self.btn_min.setObjectName("windowMinimize")
        self.btn_min.setIcon(get_app_icon("minimize", color="#8E8E93"))
        self.btn_min.setIconSize(QSize(11, 11))
        self.btn_min.setFixedSize(30, 30)
        self.btn_min.setToolTip("Minimize window")
        self.btn_min.setStyleSheet(control_style)
        self.btn_min.clicked.connect(self.main_window.showMinimized)
        layout.addWidget(self.btn_min)

        self.btn_max = QPushButton()
        self.btn_max.setObjectName("windowMaximize")
        self.btn_max.setIcon(get_app_icon("maximize", color="#8E8E93"))
        self.btn_max.setIconSize(QSize(11, 11))
        self.btn_max.setFixedSize(30, 30)
        self.btn_max.setToolTip("Maximize window")
        self.btn_max.setStyleSheet(control_style)
        self.btn_max.clicked.connect(self.main_window._toggle_maximize)
        layout.addWidget(self.btn_max)

        self.btn_close = QPushButton()
        self.btn_close.setObjectName("windowClose")
        self.btn_close.setIcon(get_app_icon("close", color="#8E8E93"))
        self.btn_close.setIconSize(QSize(11, 11))
        self.btn_close.setFixedSize(30, 30)
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
            handle = self.main_window.windowHandle()
            if handle is not None:
                if self.main_window.isMaximized():
                    cursor_x = event.globalPosition().x()
                    normal_geom = self.main_window.normalGeometry()
                    normal_width = normal_geom.width() if normal_geom.isValid() and normal_geom.width() > 0 else 1180
                    self.main_window.showNormal()
                    new_x = max(0, int(cursor_x - normal_width / 2))
                    self.main_window.move(new_x, max(0, int(event.globalPosition().y() - 25)))
                if handle.startSystemMove():
                    event.accept()
                    return
            self.drag_pos = event.globalPosition().toPoint() - self.main_window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self.drag_pos:
            handle = self.main_window.windowHandle()
            if handle is not None and handle.startSystemMove():
                self.drag_pos = None
                event.accept()
                return
            if self.main_window.isMaximized():
                self.main_window.showNormal()
            self.main_window.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_max_restore()
            event.accept()


# Backward compatibility alias
CustomTitleBar = HeaderBar


class DialogTitleBar(QFrame):
    """Clean custom titlebar for frameless dialogs with logo and window controls."""
    def __init__(self, dialog: QDialog, title: str):
        super().__init__(dialog)
        self.dialog = dialog
        self.drag_pos = None
        # Dialogs must always start in their requested normal geometry. Some
        # window managers inherit the parent's maximized state for frameless
        # modal windows unless it is explicitly cleared.
        self.dialog.setWindowState(self.dialog.windowState() & ~Qt.WindowState.WindowMaximized)
        self.setFixedHeight(40)
        self.setStyleSheet("""
            QFrame {
                background: #161618;
                border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 10, 0)
        layout.setSpacing(8)

        if os.path.exists(LOGO_PATH):
            self.logo_lbl = QLabel()
            self.logo_lbl.setPixmap(QPixmap(LOGO_PATH).scaled(16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self.logo_lbl.setFixedSize(16, 16)
            self.logo_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            layout.addWidget(self.logo_lbl)

        self.title_label = QLabel(title)
        self.title_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Normal))
        self.title_label.setStyleSheet("color: #E4E4E7; background: transparent; font-weight: 400;")
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.title_label)

        layout.addStretch()

        control_style = """
            QPushButton {
                background: transparent;
                color: #8E8E93;
                border: none;
                border-radius: 4px;
                padding: 0;
                margin: 0;
                text-align: center;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.08);
                color: #FFFFFF;
            }
            QPushButton#dialogClose:hover {
                background: rgba(255, 69, 58, 0.2);
                color: #FF453A;
            }
        """

        self.btn_min = QPushButton()
        self.btn_min.setObjectName("dialogMinimize")
        self.btn_min.setIcon(get_app_icon("minimize", color="#8E8E93"))
        self.btn_min.setIconSize(QSize(10, 10))
        self.btn_min.setFixedSize(26, 26)
        self.btn_min.setToolTip("Minimize")
        self.btn_min.setStyleSheet(control_style)
        self.btn_min.clicked.connect(self.dialog.showMinimized)
        layout.addWidget(self.btn_min)

        self.btn_max = QPushButton()
        self.btn_max.setObjectName("dialogMaximize")
        self.btn_max.setIcon(get_app_icon("maximize", color="#8E8E93"))
        self.btn_max.setIconSize(QSize(10, 10))
        self.btn_max.setFixedSize(26, 26)
        self.btn_max.setToolTip("Maximize")
        self.btn_max.setStyleSheet(control_style)
        self.btn_max.clicked.connect(self._toggle_max_restore)
        layout.addWidget(self.btn_max)

        self.btn_close = QPushButton()
        self.btn_close.setObjectName("dialogClose")
        self.btn_close.setIcon(get_app_icon("close", color="#8E8E93"))
        self.btn_close.setIconSize(QSize(10, 10))
        self.btn_close.setFixedSize(26, 26)
        self.btn_close.setToolTip("Close")
        self.btn_close.setStyleSheet(control_style)
        self.btn_close.clicked.connect(self.dialog.reject)
        layout.addWidget(self.btn_close)

        self._sync_window_controls()

    def _sync_window_controls(self):
        maximized = self.dialog.isMaximized()
        self.btn_max.setIcon(get_app_icon("restore" if maximized else "maximize", color="#8E8E93"))
        self.btn_max.setToolTip("Restore" if maximized else "Maximize")

    def _toggle_max_restore(self):
        if self.dialog.isMaximized():
            self.dialog.showNormal()
        else:
            self.dialog.showMaximized()
        self._sync_window_controls()

    def set_title(self, title: str):
        if hasattr(self, 'title_label'):
            self.title_label.setText(title)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.dialog.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
            self.drag_pos = event.globalPosition().toPoint() - self.dialog.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self.drag_pos:
            handle = self.dialog.windowHandle()
            if handle is not None and handle.startSystemMove():
                self.drag_pos = None
                event.accept()
                return
            self.dialog.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()
