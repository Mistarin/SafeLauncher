import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from ui.icons import get_icon, get_icon_pixmap, icon_pixmap


class IconRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_icon_pixmap_preserves_logical_size_on_scaled_display(self):
        with patch("ui.icons.QGuiApplication.primaryScreen", return_value=SimpleNamespace(devicePixelRatio=lambda: 2.0)):
            pixmap = get_icon_pixmap("ph.folder-simple-bold", 15, color="#FFFFFF")

        self.assertEqual(pixmap.devicePixelRatio(), 2.0)
        self.assertEqual(pixmap.width(), 30)
        self.assertEqual(pixmap.height(), 30)

    def test_generic_icon_pixmap_helper_uses_same_scaling_contract(self):
        with patch("ui.icons.QGuiApplication.primaryScreen", return_value=SimpleNamespace(devicePixelRatio=lambda: 2.0)):
            pixmap = icon_pixmap(get_icon("ph.folder-simple-bold"), 12)

        self.assertEqual(pixmap.devicePixelRatio(), 2.0)
        self.assertEqual(pixmap.width(), 24)


if __name__ == "__main__":
    unittest.main()
