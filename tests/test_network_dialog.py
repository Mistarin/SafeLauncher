import unittest

from PyQt6.QtWidgets import QApplication

from ui.dialogs.network_dialog import NetworkUnavailableDialog


class NetworkUnavailableDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_offline_choice_remains_available_during_retry(self):
        dialog = NetworkUnavailableDialog()

        dialog.set_checking()
        dialog.set_retrying(True)

        self.assertFalse(dialog.btn_retry.isEnabled())
        self.assertTrue(dialog.btn_offline.isEnabled())
        self.assertEqual(dialog.btn_retry.text(), "Checking…")
        self.assertIn("switch to Offline Mode", dialog.message_label.text())

        dialog.set_retrying(False)
        dialog.set_failure("The internet connection could not be reached.")

        self.assertTrue(dialog.btn_retry.isEnabled())
        self.assertTrue(dialog.btn_offline.isEnabled())
        self.assertEqual(dialog.btn_retry.text(), "Retry")
        self.assertIn("still unavailable", dialog.message_label.text())
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
