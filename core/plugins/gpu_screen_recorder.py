"""Hardware-accelerated and universal GPU video recording engine for SafeLauncher.

Supports:
1. gpu-screen-recorder (High performance Shadowplay clone for NVIDIA/AMD/Intel on KDE/Wayland/X11)
2. ffmpeg (Universal built-in hardware NVENC/VAAPI fallback for all Linux systems)
3. wl-screenrec (Optional backend for wlroots / Hyprland)
"""

import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List

from core.logger import get_logger
from core.host_process import host_process_env

logger = get_logger("GpuRecorderPlugin")

DEFAULT_RECORDINGS_DIR = os.path.expanduser("~/Videos/SafeLauncher")


@dataclass
class GpuRecorderConfig:
    enabled: bool = False
    mode: str = "manual"  # "manual", "auto_game", "replay_buffer"
    codec: str = "auto"  # "auto", "avc", "hevc", "av1"
    bitrate: str = "12M"  # "8M", "12M", "20M", "30M"
    target_screen: str = "screen"  # "screen", "focused", "HDMI-A-1", "DP-1"
    audio: bool = True
    audio_device: str = "default_output"  # Output device (Desktop / Game audio)
    microphone_device: str = ""  # Input device (Microphone / Voice), empty if none
    history_seconds: int = 60
    output_dir: str = DEFAULT_RECORDINGS_DIR
    capture_hotkey: str = "F9"
    replay_hotkey: str = "F10"
    in_game_overlay: bool = True


# Backwards compatibility alias
WlScreenrecConfig = GpuRecorderConfig


class GpuRecorderService:
    """Manages GPU hardware recording, ffmpeg recording, and instant replay buffer clips."""
    _instance = None

    def __init__(self, config: Optional[GpuRecorderConfig] = None):
        self.config = config or GpuRecorderConfig()
        self.process: Optional[subprocess.Popen] = None
        self.active_output_path: Optional[str] = None
        self.is_replay_mode: bool = False
        self.active_backend: str = "none"

    @classmethod
    def instance(cls) -> "GpuRecorderService":
        if cls._instance is None:
            cls._instance = GpuRecorderService()
        return cls._instance

    @staticmethod
    def _flatpak_available() -> bool:
        """True when gpu-screen-recorder is installed as a Flathub flatpak."""
        if not shutil.which("flatpak"):
            return False
        try:
            probe = subprocess.run(
                ["flatpak", "info", "com.dec05eba.gpu_screen_recorder"],
                capture_output=True, timeout=8,
                env={**os.environ, "FLATPAK_SKIP_UPDATE_CHECK": "1"},
            )
            return probe.returncode == 0
        except Exception:
            return False

    @staticmethod
    def get_backend_type() -> str:
        """Detect best available recording backend for the current desktop environment."""
        if shutil.which("gpu-screen-recorder"):
            return "gpu-screen-recorder"
        # Flathub install: same CLI, wrapped in `flatpak run`.
        if GpuRecorderService._flatpak_available():
            return "gpu-screen-recorder"

        # Only use wl-screenrec if running under a supported wlroots compositor (Hyprland, Sway, River, Wayfire)
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        is_wlroots = any(comp in desktop for comp in ("hyprland", "sway", "river", "wayfire")) or "SWAYSOCK" in os.environ or "HYPRLAND_INSTANCE_SIGNATURE" in os.environ
        if is_wlroots and shutil.which("wl-screenrec"):
            return "wl-screenrec"

        if shutil.which("ffmpeg"):
            return "ffmpeg"

        return "none"

    @classmethod
    def get_command_prefix(cls) -> List[str]:
        """ argv prefix for spawning gpu-screen-recorder (empty for native installs)."""
        if shutil.which("gpu-screen-recorder"):
            return []
        if cls._flatpak_available():
            return ["flatpak", "run", "--command=gpu-screen-recorder",
                    "com.dec05eba.gpu_screen_recorder"]
        return []

    @classmethod
    def get_executable_path(cls) -> Optional[str]:
        backend = cls.get_backend_type()
        if backend == "none":
            return None
        prefix = cls.get_command_prefix()
        if prefix:
            return " ".join(prefix)
        return shutil.which(backend)

    @classmethod
    def is_installed(cls) -> bool:
        return cls.get_backend_type() != "none"

    @staticmethod
    def get_install_command() -> str:
        return "paru -S gpu-screen-recorder"

    @staticmethod
    def get_install_options() -> List[tuple[str, str]]:
        """Install paths the settings UI offers, in preference order."""
        return [
            ("paru (AUR)", "paru -S gpu-screen-recorder"),
            ("yay (AUR)", "yay -S gpu-screen-recorder"),
            ("flatpak (Flathub)", "flatpak install flathub com.dec05eba.gpu_screen_recorder"),
        ]

    @staticmethod
    def get_available_monitors() -> List[tuple[str, str]]:
        """List monitors recognized by gpu-screen-recorder or system."""
        monitors = [
            ("screen", "Current / Active Screen (Default)"),
            ("focused", "Focused Game Window"),
        ]
        if shutil.which("gpu-screen-recorder"):
            try:
                out = subprocess.check_output(["gpu-screen-recorder", "--list-monitors"], text=True, stderr=subprocess.DEVNULL, timeout=4)
                for line in out.strip().splitlines():
                    if "|" in line:
                        m_name, m_res = line.split("|", 1)
                        monitors.append((m_name.strip(), f"{m_name.strip()} ({m_res.strip()})"))
            except Exception:
                pass
        return monitors

    @staticmethod
    def get_audio_output_devices() -> List[tuple[str, str]]:
        """List available PulseAudio / PipeWire audio output monitor devices (Game / Desktop Audio)."""
        devices = [("default_output", "Default Desktop / Game Audio")]
        if shutil.which("pactl"):
            try:
                out = subprocess.check_output(["pactl", "list", "sources"], text=True, stderr=subprocess.DEVNULL, timeout=4)
                current_name = ""
                current_desc = ""
                for line in out.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("Name:"):
                        current_name = stripped.split(":", 1)[1].strip()
                    elif stripped.startswith("Description:") and current_name:
                        current_desc = stripped.split(":", 1)[1].strip()
                        if ".monitor" in current_name or "sink" in current_name:
                            devices.append((current_name, f"Output: {current_desc}"))
                        current_name = ""
            except Exception as e:
                logger.debug(f"Failed to query audio output devices via pactl: {e}")
        return devices

    @staticmethod
    def get_audio_input_devices() -> List[tuple[str, str]]:
        """List available PulseAudio / PipeWire audio input devices (Microphone / Voice)."""
        devices = [
            ("", "None (Microphone Disabled)"),
            ("default_input", "Default System Microphone")
        ]
        if shutil.which("pactl"):
            try:
                out = subprocess.check_output(["pactl", "list", "sources"], text=True, stderr=subprocess.DEVNULL, timeout=4)
                current_name = ""
                current_desc = ""
                for line in out.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("Name:"):
                        current_name = stripped.split(":", 1)[1].strip()
                    elif stripped.startswith("Description:") and current_name:
                        current_desc = stripped.split(":", 1)[1].strip()
                        if ".monitor" not in current_name:
                            devices.append((current_name, f"Mic: {current_desc}"))
                        current_name = ""
            except Exception as e:
                logger.debug(f"Failed to query audio input devices via pactl: {e}")
        return devices

    # Backwards compatibility alias
    get_audio_devices = get_audio_output_devices

    @staticmethod
    def launch_terminal_installer(parent=None) -> bool:
        """Launches an interactive terminal to install gpu-screen-recorder (paru/yay/flatpak)."""
        if shutil.which("paru"):
            cmd_str = "paru -S gpu-screen-recorder"
        elif shutil.which("yay"):
            cmd_str = "yay -S gpu-screen-recorder"
        elif shutil.which("flatpak"):
            cmd_str = "flatpak install flathub com.dec05eba.gpu_screen_recorder"
        else:
            cmd_str = "sudo pacman -S gpu-screen-recorder"
        terminal_candidates = [
            ["konsole", "-e", "bash", "-c", f"{cmd_str}; echo; read -p 'Press Enter to close...'"],
            ["alacritty", "-e", "bash", "-c", f"{cmd_str}; echo; read -p 'Press Enter to close...'"],
            ["xterm", "-e", "bash", "-c", f"{cmd_str}; echo; read -p 'Press Enter to close...'"],
        ]
        for term_cmd in terminal_candidates:
            if shutil.which(term_cmd[0]):
                try:
                    subprocess.Popen(term_cmd, env=host_process_env())
                    return True
                except Exception as e:
                    logger.error(f"Failed to spawn terminal {term_cmd[0]}: {e}")
        return False

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def build_command(self, output_path: str, is_replay: bool = False, backend: Optional[str] = None) -> List[str]:
        """Build execution arguments for active recorder engine with output/input audio mixing."""
        active_backend = backend or self.get_backend_type()

        if active_backend == "gpu-screen-recorder":
            target_w = self.config.target_screen or "screen"
            cmd = self.get_command_prefix() + ["gpu-screen-recorder", "-w", target_w, "-f", "60", "-c", "mp4"]

            # Codec
            if self.config.codec and self.config.codec != "auto":
                codec_flag = "h264" if self.config.codec == "avc" else self.config.codec
                cmd.extend(["-k", codec_flag])

            # Quality mapping
            q_map = {"8M": "medium", "12M": "high", "20M": "very_high", "30M": "ultra"}
            quality = q_map.get(self.config.bitrate, "very_high")
            cmd.extend(["-q", quality])

            if self.config.audio:
                out_dev = self.config.audio_device or "default_output"
                if out_dev == "default":
                    out_dev = "default_output"
                cmd.extend(["-a", out_dev])

                if self.config.microphone_device:
                    mic_dev = self.config.microphone_device
                    if mic_dev == "default":
                        mic_dev = "default_input"
                    cmd.extend(["-a", mic_dev])

            if is_replay:
                cmd.extend(["-r", str(self.config.history_seconds), "-o", os.path.dirname(output_path)])
            else:
                cmd.extend(["-o", output_path])
            return cmd

        elif active_backend in ("wl-screenrec", "none"):
            cmd = ["wl-screenrec", "-f", output_path]
            if self.config.audio:
                if self.config.audio_device:
                    cmd.extend(["--audio-device", self.config.audio_device])
                else:
                    cmd.append("--audio")
            if self.config.codec and self.config.codec != "auto":
                cmd.extend(["--codec", self.config.codec])
            if self.config.bitrate:
                cmd.extend(["--bitrate", self.config.bitrate])
            if is_replay:
                cmd.extend(["--history", str(self.config.history_seconds)])
            return cmd

        else:  # ffmpeg universal fallback with hardware NVENC auto-detection & multi-audio mixing
            vcodec = "h264_nvenc"
            if self.config.codec == "hevc":
                vcodec = "hevc_nvenc"
            elif self.config.codec == "av1":
                vcodec = "av1_nvenc"
            elif self.config.codec == "avc":
                vcodec = "h264_nvenc"

            bitrate_val = self.config.bitrate or "12M"
            cmd = [
                "ffmpeg", "-y", "-f", "x11grab", "-framerate", "60",
                "-i", ":0.0"
            ]

            out_audio = self.config.audio_device if (self.config.audio and self.config.audio_device) else ""
            in_mic = self.config.microphone_device if (self.config.audio and self.config.microphone_device) else ""
            if out_audio == "default_output":
                out_audio = "default"
            if in_mic == "default_input":
                in_mic = "default"

            if out_audio and in_mic:
                cmd.extend(["-f", "pulse", "-i", out_audio])
                cmd.extend(["-f", "pulse", "-i", in_mic])
                cmd.extend([
                    "-filter_complex", "[1:a][2:a]amix=inputs=2:duration=longest[aout]",
                    "-map", "0:v", "-map", "[aout]",
                    "-c:v", vcodec, "-b:v", bitrate_val,
                    "-maxrate", bitrate_val, "-bufsize", "24M",
                    "-c:a", "aac", "-b:a", "192k"
                ])
            elif out_audio or in_mic:
                single_dev = out_audio or in_mic
                cmd.extend(["-f", "pulse", "-i", single_dev])
                cmd.extend([
                    "-c:v", vcodec, "-b:v", bitrate_val,
                    "-maxrate", bitrate_val, "-bufsize", "24M",
                    "-c:a", "aac", "-b:a", "192k"
                ])
            else:
                cmd.extend([
                    "-c:v", vcodec, "-b:v", bitrate_val,
                    "-maxrate", bitrate_val, "-bufsize", "24M"
                ])

            cmd.append(output_path)
            return cmd

    def start_recording(self, game_name: str = "Gameplay", is_replay: bool = False) -> bool:
        if not self.is_installed():
            logger.warning("Cannot start recording: No recorder backend found.")
            return False

        if self.is_running():
            logger.info("Recording is already in progress.")
            return True

        target_dir = os.path.expanduser(self.config.output_dir)
        os.makedirs(target_dir, exist_ok=True)
        # Normalise: lower-case, replace non-alphanumeric runs with underscore
        normalised = re.sub(r"[^a-z0-9]+", "_", game_name.strip().lower()).strip("_") or "gameplay"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{normalised}_{ts}.mp4"
        self.active_output_path = os.path.join(target_dir, filename)
        self.is_replay_mode = is_replay
        self.active_backend = self.get_backend_type()

        cmd = self.build_command(self.active_output_path, is_replay=is_replay)
        logger.info(f"Starting recorder ({self.active_backend}): {' '.join(cmd)}")

        try:
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=host_process_env()
            )
            time.sleep(0.3)
            if self.process.poll() is not None:
                logger.error(f"Recording process failed to start (exit code {self.process.returncode})")
                self.process = None
                return False

            logger.info(f"Recording started successfully -> {self.active_output_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to start recording process: {e}")
            self.process = None
            return False

    def save_replay_clip(self) -> bool:
        """Trigger replay clip save via signal."""
        if not self.is_running() or not self.is_replay_mode:
            logger.warning("No active replay buffer to save.")
            return False

        try:
            logger.info("Sending SIGUSR1 signal to dump replay buffer...")
            self.process.send_signal(signal.SIGUSR1)
            return True
        except Exception as e:
            logger.error(f"Failed to send replay signal: {e}")
            return False

    def stop_recording(self) -> Optional[str]:
        """Gracefully stop recording and finalize video file."""
        if not self.is_running():
            return None

        logger.info("Stopping recording process...")
        try:
            if self.active_backend == "ffmpeg":
                try:
                    if self.process.stdin:
                        self.process.stdin.write(b"q\n")
                        self.process.stdin.flush()
                except Exception:
                    pass
                self.process.send_signal(signal.SIGINT)
            else:
                self.process.send_signal(signal.SIGINT)

            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        except Exception as e:
            logger.warning(f"Error while stopping recorder: {e}")
            try:
                self.process.kill()
            except Exception:
                pass

        self.process = None
        out = self.active_output_path
        self.active_output_path = None
        self.is_replay_mode = False
        return out


# Backwards compatibility alias
WlScreenrecService = GpuRecorderService
