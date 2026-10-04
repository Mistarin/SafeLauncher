from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget, QScrollArea, QGridLayout
from PyQt6.QtGui import QFont
from core.security_diagnostics import inspect_security_health
from core.logger import get_logger
from dataclasses import dataclass
from typing import Callable
logger = get_logger("SettingsPage")


@dataclass(frozen=True)
class SecurityPageInputs:
    add_section_divider: Callable
    copy_probe_details: Callable
    execute_live_probe: Callable
    polish_settings_grid: Callable
    wrap_settings_sections: Callable


class SecuritySettingsPage(QScrollArea):
    """Build this form only; workflow callbacks are explicitly supplied."""
    def __init__(self, inputs: SecurityPageInputs, parent=None):
        super().__init__(parent)
        scroll = self
        scroll.setWidgetResizable(True)
        scroll.setObjectName("settingsScroll")

        page = QWidget()
        page.setObjectName("settingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(12)

        report = inspect_security_health()

        # Clean, flat status banner with white text and solid colored background (no borders/corners)
        if report.overall_status == "healthy":
            bg_color = "#166534"  # Solid dark green
            status_text = "Container Isolation Active: System and personal files are protected inside sandbox."
        elif report.overall_status == "warning":
            bg_color = "#9a3412"  # Solid dark orange
            status_text = f"Container Warning: {report.summary}"
        else:
            bg_color = "#991b1b"  # Solid dark red
            status_text = "Container Critical: Firejail sandbox is missing. Games will run unconfined."

        header_banner = QLabel(status_text)
        header_banner.setWordWrap(True)
        header_banner.setStyleSheet(f"""
            QLabel {{
                background-color: {bg_color};
                color: #ffffff;
                padding: 12px 14px;
                font-size: 12px;
                font-weight: 600;
            }}
        """)
        layout.addWidget(header_banner)

        # Status Table
        sec_subsys = QLabel("Subsystem Inspection")
        sec_subsys.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_subsys.setStyleSheet("color: #ffffff; padding-bottom: 2px; margin-top: 4px;")
        layout.addWidget(sec_subsys)
        inputs.add_section_divider(layout)

        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setContentsMargins(0, 4, 0, 4)

        fj_status = f"Active ({report.firejail_version})" if report.firejail_installed else "Missing"
        grid.addWidget(QLabel("Firejail Sandbox:"), 0, 0)
        grid.addWidget(QLabel(fj_status), 0, 1)

        grid.addWidget(QLabel("Kernel Namespaces:"), 1, 0)
        grid.addWidget(QLabel(report.userns_detail), 1, 1)

        bw_status = f"Available ({report.bwrap_version})" if report.bwrap_installed else "Missing"
        grid.addWidget(QLabel("Bubblewrap:"), 2, 0)
        grid.addWidget(QLabel(bw_status), 2, 1)

        grid.addWidget(QLabel("Prefix Isolation:"), 3, 0)
        grid.addWidget(QLabel("Active (Z: host drive removed, user folders isolated)"), 3, 1)

        layout.addLayout(grid)
        inputs.polish_settings_grid(grid)

        # GPU Caches
        sec_gpu = QLabel("GPU Shader Cache Whitelists")
        sec_gpu.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_gpu.setStyleSheet("color: #ffffff; padding-bottom: 2px; margin-top: 6px;")
        layout.addWidget(sec_gpu)
        inputs.add_section_divider(layout)

        gpu_grid = QGridLayout()
        gpu_grid.setSpacing(6)
        gpu_grid.setContentsMargins(0, 4, 0, 4)

        for i, cache in enumerate(report.gpu_caches):
            gpu_grid.addWidget(QLabel(f"{cache['name']} ({cache['path']}):"), i, 0)
            status_str = f"Available ({cache['size_formatted']})" if cache['exists'] else "Not created yet"
            gpu_grid.addWidget(QLabel(status_str), i, 1)

        layout.addLayout(gpu_grid)
        inputs.polish_settings_grid(gpu_grid)

        # Live probe test
        sec_probe = QLabel("Sandbox Verification")
        sec_probe.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_probe.setStyleSheet("color: #ffffff; padding-bottom: 2px; margin-top: 6px;")
        layout.addWidget(sec_probe)
        inputs.add_section_divider(layout)

        probe_actions = QHBoxLayout()
        btn_run_test = QPushButton("Run Sandbox Isolation Test")
        btn_run_test.setMinimumWidth(220)
        btn_run_test.clicked.connect(lambda: inputs.execute_live_probe(btn_run_test))
        probe_actions.addWidget(btn_run_test)
        self.btn_copy_probe_details = QPushButton("Copy technical details")
        self.btn_copy_probe_details.setAccessibleName("Copy sandbox verification technical details")
        self.btn_copy_probe_details.setToolTip("Copy the last sandbox verification error for troubleshooting")
        self.btn_copy_probe_details.clicked.connect(inputs.copy_probe_details)
        self.btn_copy_probe_details.setVisible(False)
        probe_actions.addWidget(self.btn_copy_probe_details)
        probe_actions.addStretch(1)
        layout.addLayout(probe_actions)

        self.probe_output_lbl = QLabel("")
        self.probe_output_lbl.setWordWrap(True)
        self.probe_output_lbl.setStyleSheet("font-size: 11px; padding: 4px 0px;")
        layout.addWidget(self.probe_output_lbl)

        layout.addStretch()
        inputs.wrap_settings_sections(layout)
        scroll.setWidget(page)
