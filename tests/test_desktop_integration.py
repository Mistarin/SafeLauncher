import os
import tempfile
import unittest
from unittest.mock import patch

from core import desktop_integration


class DesktopIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.temp_dir.name, "home")
        os.makedirs(os.path.join(self.home, "Desktop"))
        self.data = os.path.join(self.temp_dir.name, "data")
        self.config = os.path.join(self.temp_dir.name, "config")
        self.environment = patch.dict(os.environ, {
            "HOME": self.home,
            "XDG_DATA_HOME": self.data,
            "XDG_CONFIG_HOME": self.config,
            "APPIMAGE": "",
        }, clear=False)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp_dir.cleanup()

    def test_install_and_remove_are_reversible_and_keep_unrelated_files(self):
        unrelated = os.path.join(self.home, "Desktop", "Other.desktop")
        with open(unrelated, "w", encoding="utf-8") as handle:
            handle.write("other")

        installed, _ = desktop_integration.install_safelauncher_desktop_entry()
        self.assertTrue(installed)
        self.assertTrue(desktop_integration.is_desktop_entry_installed())
        self.assertTrue(desktop_integration.is_desktop_shortcut_installed())
        with open(desktop_integration.get_desktop_file_path(), encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("X-SafeLauncher-Managed=true", content)
        self.assertIn("Exec=", content)

        removed, _ = desktop_integration.remove_safelauncher_desktop_entry()
        self.assertTrue(removed)
        self.assertFalse(os.path.exists(desktop_integration.get_desktop_file_path()))
        self.assertFalse(os.path.exists(desktop_integration.get_desktop_shortcut_path()))
        self.assertTrue(os.path.exists(unrelated))

    def test_startup_is_independent_from_menu_entry(self):
        enabled, _ = desktop_integration.set_startup_enabled(True)
        self.assertTrue(enabled)
        self.assertTrue(desktop_integration.is_startup_enabled())
        with open(desktop_integration.get_autostart_file_path(), encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("NoDisplay=true", content)
        self.assertIn("X-GNOME-Autostart-enabled=true", content)
        self.assertNotIn("%U", content)

        removed, _ = desktop_integration.remove_safelauncher_desktop_entry()
        self.assertTrue(removed)
        self.assertTrue(desktop_integration.is_startup_enabled())

        disabled, _ = desktop_integration.set_startup_enabled(False)
        self.assertTrue(disabled)
        self.assertFalse(desktop_integration.is_startup_enabled())


if __name__ == "__main__":
    unittest.main()
