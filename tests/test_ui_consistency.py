import unittest

from PyQt6.QtWidgets import QApplication, QFormLayout, QLineEdit, QLabel, QToolButton

from ui.components.popup_shell import PopupDialog


class PopupPropertyConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_property_form_aligns_labels_and_expands_fields(self):
        dialog = PopupDialog("Consistency")
        form = QFormLayout()
        value = QLineEdit()
        form.addRow("A long property name:", value)

        dialog.polish_property_form(form)
        label = form.itemAt(0, QFormLayout.ItemRole.LabelRole).widget()

        self.assertEqual(label.objectName(), "propertyLabel")
        self.assertEqual(label.alignment().value, 0x82)  # right + vertical center
        self.assertGreaterEqual(label.minimumWidth(), 160)
        self.assertEqual(value.sizePolicy().horizontalPolicy().name, "Expanding")
        dialog.close()

    def test_info_hint_is_muted_and_accessible(self):
        hint = PopupDialog.info_hint(
            "Credentials stay in the local secret store.",
            tooltip="Credentials are never included in request keys, logs, or caches.",
        )
        icon = hint.findChild(QToolButton, "propertyInfo")
        text = hint.findChild(QLabel, "propertyHintText")

        self.assertIsNotNone(icon)
        self.assertEqual(icon.toolTip(), "Credentials are never included in request keys, logs, or caches.")
        self.assertIsNotNone(text)
        self.assertEqual(text.accessibleDescription(), "Credentials are never included in request keys, logs, or caches.")
        hint.deleteLater()


if __name__ == "__main__":
    unittest.main()
