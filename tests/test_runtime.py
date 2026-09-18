import sqlite3
import tempfile
import unittest
from pathlib import Path

from fc27.errors import FC27Error
from fc27.runtime import RuntimeManager


class RuntimeManagerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.manager = RuntimeManager(Path(self.directory.name) / "accounts")

    def tearDown(self):
        self.directory.cleanup()

    def test_creates_one_database_per_persona(self):
        first = self.manager.activate(
            {"persona_id": "persona-123", "platform": "ps5", "club_id": 10, "club_name": "First"}
        )
        second = self.manager.activate(
            {"persona_id": "persona-456", "platform": "pc", "club_id": 20, "club_name": "Second"}
        )
        self.assertTrue(Path(first["runtime_path"]).exists())
        self.assertNotEqual(first["runtime_path"], second["runtime_path"])
        self.assertEqual(second["persona_id"], "persona-456")

    def test_reuses_identity_row_and_updates_club_name(self):
        self.manager.activate(
            {"persona_id": "123", "platform": "ps5", "club_id": 10, "club_name": "Old"}
        )
        summary = self.manager.activate(
            {"persona_id": "123", "platform": "ps5", "club_id": 10, "club_name": "New"}
        )
        self.assertEqual(summary["club_name"], "New")

    def test_restores_the_only_existing_persona(self):
        self.manager.activate(
            {"persona_id": "123", "platform": "pc", "club_id": 10, "club_name": "Club"}
        )
        restored = RuntimeManager(Path(self.directory.name) / "accounts")
        self.assertEqual(restored.status()["persona_id"], "123")

    def test_does_not_guess_when_multiple_personas_exist(self):
        self.manager.activate({"persona_id": "123", "platform": "pc"})
        self.manager.activate({"persona_id": "456", "platform": "ps5"})
        restored = RuntimeManager(Path(self.directory.name) / "accounts")
        self.assertIsNone(restored.status())

    def test_rejects_path_like_persona_id(self):
        with self.assertRaises(FC27Error) as context:
            self.manager.activate({"persona_id": "../other", "platform": "ps5"})
        self.assertEqual(context.exception.code, "INVALID_PERSONA_ID")

    def test_rejects_database_bound_to_another_persona(self):
        summary = self.manager.activate({"persona_id": "123", "platform": "ps5"})
        connection = sqlite3.connect(summary["runtime_path"])
        connection.execute("UPDATE account_state SET persona_id = '999'")
        connection.commit()
        connection.close()
        with self.assertRaises(FC27Error) as context:
            self.manager.activate({"persona_id": "123", "platform": "ps5"})
        self.assertEqual(context.exception.code, "ACCOUNT_MISMATCH")


if __name__ == "__main__":
    unittest.main()
