import tempfile
import unittest
from pathlib import Path

from settings_store import SettingsConflictError, SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SettingsStore(Path(self.tempdir.name) / "state.db")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_settings_are_versioned_and_conflicts_are_rejected(self):
        initial = self.store.get_settings()
        updated = self.store.update_settings({"idle_timeout_seconds": 30}, initial.revision)
        self.assertEqual(updated.revision, 2)
        self.assertEqual(updated.idle_timeout_seconds, 30)
        with self.assertRaises(SettingsConflictError):
            self.store.update_settings({"idle_timeout_seconds": 20}, initial.revision)

    def test_manual_labels_are_persisted(self):
        self.store.set_label("run-1", "Lumi")
        self.assertEqual(self.store.get_label("run-1"), "Lumi")
        self.assertEqual(self.store.get_label("missing"), "unknown")
