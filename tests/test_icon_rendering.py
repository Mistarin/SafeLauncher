import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from ui.icons import draw_folder_pixmap, get_icon, get_icon_pixmap, icon_pixmap


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

    def test_collection_folder_drawing_is_not_scaled_twice_on_hidpi(self):
        with patch("ui.icons.QGuiApplication.primaryScreen", return_value=SimpleNamespace(devicePixelRatio=lambda: 2.0)):
            pixmap = draw_folder_pixmap(15, color="#FFFFFF")

        self.assertEqual(pixmap.devicePixelRatio(), 2.0)
        self.assertEqual((pixmap.width(), pixmap.height()), (30, 30))
        image = pixmap.toImage()
        painted_x = [
            x
            for x in range(image.width())
            for y in range(image.height())
            if image.pixelColor(x, y).alpha() > 0
        ]
        self.assertTrue(painted_x)
        self.assertGreaterEqual(min(painted_x), 1)
        self.assertLessEqual(max(painted_x), image.width() - 2)


if __name__ == "__main__":
    unittest.main()
