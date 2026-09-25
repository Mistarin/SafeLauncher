import unittest

from core.save_history import (
    history_device_label,
    history_device_metadata,
    history_registered_device_label,
    history_device_text,
    normalize_history_entries,
)


class SaveHistoryDeviceTests(unittest.TestCase):
    def test_device_section_uses_upload_device(self):
        self.assertEqual(
            history_device_label({
                "source": "cloud",
                "createdDeviceName": "Desktop",
                "uploadedDeviceName": "Steam Deck",
            }),
            "Steam Deck",
        )

    def test_device_section_falls_back_for_legacy_and_local_records(self):
        self.assertEqual(history_device_label({"source": "cloud"}), "Unknown device")
        self.assertEqual(history_device_label({"source": "fork"}), "This PC")

    def test_registered_device_label_accepts_cloud_api_shapes(self):
        self.assertEqual(history_registered_device_label({"deviceName": "Desktop"}), "Desktop")
        self.assertEqual(history_registered_device_label({"name": "Steam Deck"}), "Steam Deck")
        self.assertEqual(history_registered_device_label({}), "Unknown device")

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
        self.assertEqual(text, "Unavailable (older cloud version)")
        self.assertNotIn("opaque-secret-looking-id", text)

    def test_local_fork_is_identified_as_local(self):
        self.assertEqual(history_device_text({"source": "fork"}), "This PC (local backup)")

    def test_device_name_is_single_line_and_bounded(self):
        text = history_device_text({"source": "cloud", "deviceName": "  Desk\n\t" + "x" * 200})
        self.assertTrue(text.startswith("Created and uploaded on Desk"))
        self.assertNotIn("\n", text)
        self.assertLessEqual(len(text), 120)

    def test_history_prefers_upload_time_then_creation_time(self):
        entries = normalize_history_entries([
            {"source": "cloud", "version": 1, "createdAt": 2_000_000_000_000, "sourceMaxMtime": 9_000},
            {"source": "cloud", "version": 2, "createdAt": 1_000_000_000_000, "uploadedAt": 3_000_000_000_000},
        ])
        self.assertEqual([entry.version for entry in entries], ["2", "1"])
        self.assertEqual(entries[0].event_at, 3_000_000_000)

    def test_cloud_and_local_entries_share_one_stable_timeline(self):
        entries = normalize_history_entries([
            {"source": "fork", "path": "/tmp/save_forks/a.zip", "mtime": 200, "size_bytes": 4},
            {"source": "cloud", "version": 3, "createdAt": 300_000, "sizeBytes": 8},
            {"source": "cloud", "version": 3, "createdAt": 300_000, "sizeBytes": 8},
        ])
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].source, "cloud")
        self.assertEqual(entries[1].source, "fork")
        self.assertEqual(entries[0].date_key, "1970-01-04")

    def test_missing_timestamp_is_sorted_after_dated_entries(self):
        entries = normalize_history_entries([
            {"source": "cloud", "version": 1},
            {"source": "cloud", "version": 2, "createdAt": 100_000},
        ])
        self.assertEqual([entry.version for entry in entries], ["2", "1"])
        self.assertEqual(entries[-1].date_label, "Date unavailable")


if __name__ == "__main__":
    unittest.main()
