from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFormLayout, QWidget, QScrollArea, QCheckBox
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QKeySequence
from PyQt6.QtWidgets import QKeySequenceEdit
from core.plugins.gpu_screen_recorder import GpuRecorderService, WlScreenrecService, DEFAULT_RECORDINGS_DIR
from ui.components.check_field import CheckField as QCheckBox
from ui.components.sort_combo import SortComboBox
from core.logger import get_logger
from dataclasses import dataclass
from typing import Callable
logger = get_logger("SettingsPage")


@dataclass(frozen=True)
class PluginsPageInputs:
    add_section_divider: Callable
    browse_recordings_dir: Callable
    copy_install_command: Callable
    on_install_option_changed: Callable
    open_install_notice: Callable
    polish_settings_form: Callable
    wrap_settings_sections: Callable
    gpu_config: object
    info_hint: Callable
    screenshot_hotkey: str


class PluginsSettingsPage(QScrollArea):
    """Build this form only; workflow callbacks are explicitly supplied."""
    def __init__(self, inputs: PluginsPageInputs, parent=None):
        super().__init__(parent)
        scroll = self
        scroll.setWidgetResizable(True)
        scroll.setObjectName("settingsScroll")

        page = QWidget()
        page.setObjectName("settingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)

        sec_title = QLabel("Hardware Accelerated Video Recording")
        sec_title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        sec_title.setStyleSheet("color: #ffffff; padding-bottom: 2px;")
        layout.addWidget(sec_title)
        inputs.add_section_divider(layout)

        desc = inputs.info_hint(
            "GPU Screen Recorder is a high-performance Linux recorder using NVIDIA NVENC, AMD VAAPI, or Intel QuickSync.",
            tooltip="Recording runs through the detected local backend. SafeLauncher does not upload recordings to private cloud storage.",
        )
        layout.addWidget(desc)

        # Main Plugin Toggle (Default: False)
        self.chk_plugin_enabled = QCheckBox("Enable GPU Screen Recorder Addon")
        self.chk_plugin_enabled.setChecked(inputs.gpu_config.enabled)
        self.chk_plugin_enabled.setStyleSheet("font-weight: bold; font-size: 13px; color: #ffffff;")
        layout.addWidget(self.chk_plugin_enabled)

        # Status Banner
        backend = WlScreenrecService.get_backend_type()
        if backend == "gpu-screen-recorder":
            bg_col = "#166534"
            status_text = f"Installed & Ready: GPU Screen Recorder ({WlScreenrecService.get_executable_path()})"
        elif backend == "wl-screenrec":
            bg_col = "#166534"
            status_text = f"Installed & Ready: wl-screenrec ({WlScreenrecService.get_executable_path()})"
        elif backend == "ffmpeg":
            bg_col = "#1e3a8a"
            status_text = f"Active: Built-in Universal ffmpeg fallback ({WlScreenrecService.get_executable_path()})"
        else:
            bg_col = "#9a3412"
            status_text = "No recorder backend found. Install gpu-screen-recorder via paru, yay, or flatpak to enable hardware NVENC recording."

        status_hint = inputs.info_hint(
            status_text,
            tooltip="Recorder backend detection status. SafeLauncher uses the available local recorder only.",
        )
        status_label = status_hint.findChild(QLabel, "propertyHintText")
        if status_label is not None:
            status_label.setObjectName("settingsPluginStatus")
            status_label.setStyleSheet(
                f"QLabel#settingsPluginStatus {{ background: transparent; color: {bg_col}; "
                "font-size: 11px; font-weight: 600; }"
            )
        layout.addWidget(status_hint)

        # Installation Helper (if gpu-screen-recorder not installed)
        if backend != "gpu-screen-recorder":
            install_row = QHBoxLayout()
            self.install_option_combo = SortComboBox()
            self.install_option_combo.setMinimumWidth(0)
            for label, cmd in WlScreenrecService.get_install_options():
                self.install_option_combo.addItem(label, cmd)
            self.install_option_combo.currentIndexChanged.connect(inputs.on_install_option_changed)
            install_row.addWidget(self.install_option_combo)

            self.install_cmd_box = QLineEdit(WlScreenrecService.get_install_options()[0][1])
            self.install_cmd_box.setReadOnly(True)
            self.install_cmd_box.setMinimumWidth(0)
            install_row.addWidget(self.install_cmd_box)

            btn_copy_cmd = QPushButton("Copy Command")
            btn_copy_cmd.clicked.connect(inputs.copy_install_command)
            install_row.addWidget(btn_copy_cmd)

            btn_install_now = QPushButton("Install via Helper")
            btn_install_now.setProperty("settingsButtonRole", "primary")
            btn_install_now.setStyleSheet("QPushButton { background: #3B9FE8; color: #ffffff; border: none; } QPushButton:hover { background: #2789D0; }")
            btn_install_now.clicked.connect(inputs.open_install_notice)
            install_row.addWidget(btn_install_now)

            layout.addLayout(install_row)

        # Configuration Form
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.combo_mode = SortComboBox()
        self.combo_mode.addItem("Manual (Record on Hotkey / Button)", "manual")
        self.combo_mode.addItem("Automatic (Record While Playing)", "auto_game")
        self.combo_mode.addItem("Instant Replay Buffer (Shadowplay)", "replay_buffer")
        idx_m = self.combo_mode.findData(inputs.gpu_config.mode)
        if idx_m >= 0:
            self.combo_mode.setCurrentIndex(idx_m)
        form.addRow("Recording Mode:", self.combo_mode)

        self.combo_recording_monitor = SortComboBox()
        monitors = GpuRecorderService.get_available_monitors()
        selected_rm_idx = 0
        cur_target_screen = getattr(inputs.gpu_config, "target_screen", "screen") or "screen"
        for i, (m_val, m_lbl) in enumerate(monitors):
            self.combo_recording_monitor.addItem(m_lbl, m_val)
            if m_val == cur_target_screen:
                selected_rm_idx = i
        self.combo_recording_monitor.setCurrentIndex(selected_rm_idx)
        form.addRow("Recording Screen / Display:", self.combo_recording_monitor)

        self.combo_replay = SortComboBox()
        self.combo_replay.addItem("30 Seconds", 30)
        self.combo_replay.addItem("60 Seconds (Default)", 60)
        self.combo_replay.addItem("120 Seconds (2 min)", 120)
        self.combo_replay.addItem("300 Seconds (5 min)", 300)
        idx_r = self.combo_replay.findData(inputs.gpu_config.history_seconds)
        if idx_r >= 0:
            self.combo_replay.setCurrentIndex(idx_r)
        form.addRow("Replay Buffer Size:", self.combo_replay)

        self.combo_bitrate = SortComboBox()
        self.combo_bitrate.addItem("8 Mbps (Compact 1080p)", "8M")
        self.combo_bitrate.addItem("12 Mbps (Standard 1080p 60fps)", "12M")
        self.combo_bitrate.addItem("20 Mbps (High Quality 1440p)", "20M")
        self.combo_bitrate.addItem("30 Mbps (Ultra 4K)", "30M")
        idx_b = self.combo_bitrate.findData(inputs.gpu_config.bitrate)
        if idx_b >= 0:
            self.combo_bitrate.setCurrentIndex(idx_b)
        form.addRow("Video Bitrate:", self.combo_bitrate)

        self.combo_codec = SortComboBox()
        self.combo_codec.addItem("Auto (Hardware Detect)", "auto")
        self.combo_codec.addItem("H.264 / AVC (Broadest Compatibility)", "avc")
        self.combo_codec.addItem("HEVC / H.265 (Efficient)", "hevc")
        self.combo_codec.addItem("AV1 (Next-Gen GPU)", "av1")
        idx_c = self.combo_codec.findData(inputs.gpu_config.codec)
        if idx_c >= 0:
            self.combo_codec.setCurrentIndex(idx_c)
        form.addRow("Video Codec:", self.combo_codec)

        # Audio Configuration & Dual Devices (Output + Input)
        self.chk_audio = QCheckBox("Record Audio")
        self.chk_audio.setChecked(inputs.gpu_config.audio)
        form.addRow("Audio Master:", self.chk_audio)

        self.combo_audio_output = SortComboBox()
        out_devices = GpuRecorderService.get_audio_output_devices()
        selected_out_idx = 0
        current_out = getattr(inputs.gpu_config, "audio_device", "default") or "default"
        for i, (dev_name, dev_label) in enumerate(out_devices):
            self.combo_audio_output.addItem(dev_label, dev_name)
            if dev_name == current_out:
                selected_out_idx = i
        self.combo_audio_output.setCurrentIndex(selected_out_idx)
        form.addRow("Audio Output (Game / Desktop):", self.combo_audio_output)

        self.combo_audio_input = SortComboBox()
        in_devices = GpuRecorderService.get_audio_input_devices()
        selected_in_idx = 0
        current_in = getattr(inputs.gpu_config, "microphone_device", "") or ""
        for i, (dev_name, dev_label) in enumerate(in_devices):
            self.combo_audio_input.addItem(dev_label, dev_name)
            if dev_name == current_in:
                selected_in_idx = i
        self.combo_audio_input.setCurrentIndex(selected_in_idx)
        form.addRow("Audio Input (Microphone):", self.combo_audio_input)

        # ── Hotkeys (custom key sequence) ──────────────────────────────────
        capture_hint = inputs.info_hint(
            "Click a field and press the key combination you want to bind",
            tooltip="Keyboard shortcuts are captured by the field below and saved with the recorder settings.",
        )
        form.addRow("", capture_hint)

        self.edit_hotkey = QKeySequenceEdit()
        self.edit_hotkey.setKeySequence(QKeySequence(inputs.gpu_config.capture_hotkey or "F9"))
        self.edit_hotkey.setStyleSheet(
            "QKeySequenceEdit { background: #1C1C1F; color: #ffffff; border: none;"
            " border-bottom: 1px solid #303036; border-radius: 6px;"
            " padding: 0 11px; min-height: 36px; font-size: 12px; }"
        )
        capture_mode_hint = inputs.info_hint("")
        capture_mode_label = capture_mode_hint.findChild(QLabel, "propertyHintText")
        if inputs.gpu_config.mode == "replay_buffer":
            capture_mode_label.setText("Saves an instant replay clip of the last buffered minutes")
        else:
            capture_mode_label.setText("Starts / stops manual video recording")
        capture_mode_label.setToolTip("Explains what the selected recording shortcut does.")
        capture_vbox = QVBoxLayout()
        capture_vbox.setSpacing(3)
        capture_vbox.addWidget(self.edit_hotkey)
        capture_vbox.addWidget(capture_mode_hint)
        form.addRow("Record / Clip Hotkey:", capture_vbox)

        self.edit_screenshot_hotkey = QKeySequenceEdit()
        self.edit_screenshot_hotkey.setKeySequence(QKeySequence(inputs.screenshot_hotkey or "F12"))
        self.edit_screenshot_hotkey.setStyleSheet(
            "QKeySequenceEdit { background: #1C1C1F; color: #ffffff; border: none;"
            " border-bottom: 1px solid #303036; border-radius: 6px;"
            " padding: 0 11px; min-height: 36px; font-size: 12px; }"
        )
        form.addRow("Screenshot Hotkey:", self.edit_screenshot_hotkey)

        self.chk_overlay = QCheckBox("Show In-Game Notification Overlay (Floating HUD)")
        self.chk_overlay.setChecked(getattr(inputs.gpu_config, "in_game_overlay", True))
        form.addRow("In-Game Overlay:", self.chk_overlay)

        out_row = QHBoxLayout()
        self.output_dir_input = QLineEdit(inputs.gpu_config.output_dir or DEFAULT_RECORDINGS_DIR)
        out_row.addWidget(self.output_dir_input)
        browse_out = QPushButton("Browse")
        browse_out.clicked.connect(inputs.browse_recordings_dir)
        out_row.addWidget(browse_out)
        form.addRow("Recordings Folder:", out_row)

        inputs.polish_settings_form(form)
        layout.addLayout(form)
        layout.addStretch()
        inputs.wrap_settings_sections(layout)

        scroll.setWidget(page)
