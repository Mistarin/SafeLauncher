import unittest

from core.save_history import history_device_metadata, history_device_text


class SaveHistoryDeviceTests(unittest.TestCase):
    def test_explicit_creation_and_upload_devices_are_distinguished(self):
        entry = {
            "source": "cloud",
            "createdDeviceName": "Desktop",
            "uploadedDeviceName": "Steam Deck",
        }
        self.assertEqual(
            history_device_metadata(entry),
            {"created": "Desktop", "uploaded": "Steam Deck"},
        )
        self.assertEqual(
            history_device_text(entry),
            "Created on Desktop · uploaded from Steam Deck",
        )

    def test_single_compatibility_device_is_used_for_both_roles(self):
        self.assertEqual(
            history_device_text({"source": "cloud", "deviceName": "Desktop"}),
            "Created and uploaded on Desktop",
        )

    def test_legacy_cloud_entry_does_not_render_an_opaque_id(self):
        entry = {"source": "cloud", "createdDeviceId": "opaque-secret-looking-id"}
        text = history_device_text(entry)
        self.assertEqual(text, "Unavailable (older cloud generation)")
        self.assertNotIn("opaque-secret-looking-id", text)

    def test_local_fork_is_identified_as_local(self):
        self.assertEqual(history_device_text({"source": "fork"}), "This PC (local backup)")

    def test_device_name_is_single_line_and_bounded(self):
        text = history_device_text({"source": "cloud", "deviceName": "  Desk\n\t" + "x" * 200})
        self.assertTrue(text.startswith("Created and uploaded on Desk"))
        self.assertNotIn("\n", text)
        self.assertLessEqual(len(text), 120)


if __name__ == "__main__":
    unittest.main()
