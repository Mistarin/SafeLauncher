from __future__ import annotations

import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QComboBox, QDialog, QPushButton, QWidget

from ui.gamepad_navigation_controller import GamepadNavigationController


class GamepadNavigationControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QWidget()
        self.window.resize(360, 180)
        self.left = QPushButton("Left", self.window)
        self.left.setGeometry(20, 50, 100, 40)
        self.right = QPushButton("Right", self.window)
        self.right.setGeometry(220, 50, 100, 40)
        self.window.show()
        self.window.activateWindow()
        self.app.processEvents()
        self.controller = GamepadNavigationController(
            game_running=lambda: False,
            parent=self.window,
        )

    def tearDown(self):
        self.controller.stop()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_directional_navigation_moves_to_nearest_control(self):
        self.left.setFocus()
        self.app.processEvents()

        self.controller._focus_spatial_neighbor(
            self.window,
            self.left,
            Qt.Key.Key_Right,
        )

        self.assertIs(self.app.focusWidget(), self.right)

    def test_south_button_activates_focused_control(self):
        activated = []
        self.right.clicked.connect(lambda: activated.append(True))
        self.right.setFocus()
        self.app.processEvents()

        self.controller._activate_focused_widget()

        self.assertEqual(activated, [True])

    def test_input_is_ignored_during_gameplay(self):
        activated = []
        self.right.clicked.connect(lambda: activated.append(True))
        self.right.setFocus()
        self.controller.game_running = lambda: True

        self.controller._handle_input_event(5, 0x01, 0x130, 1)

        self.assertEqual(activated, [])

    def test_game_start_cancels_held_navigation_repeat(self):
        self.controller._held_directions[(7, 1)] = Qt.Key.Key_Right
        self.controller._repeat_timer.start()

        self.controller.set_gameplay_active(True)

        self.assertFalse(self.controller._repeat_timer.isActive())
        self.assertEqual(self.controller._held_directions, {})

    def test_east_button_rejects_the_active_dialog(self):
        dialog = QDialog(self.window)
        dialog.setModal(True)
        dialog.show()
        dialog.activateWindow()
        self.app.processEvents()
        rejected = []
        dialog.rejected.connect(lambda: rejected.append(True))

        self.controller._send_key(Qt.Key.Key_Escape)

        self.assertEqual(rejected, [True])
        dialog.deleteLater()

    def test_popup_list_remains_navigable_inside_its_parent_window(self):
        combo = QComboBox(self.window)
        combo.addItems(["First", "Second", "Third"])
        combo.setGeometry(20, 110, 120, 30)
        combo.showPopup()
        self.app.processEvents()

        focus = self.controller._focused_widget(self.window)

        self.assertIsNotNone(focus)
        self.assertIsNot(focus, combo)
        combo.hidePopup()


if __name__ == "__main__":
    unittest.main()
