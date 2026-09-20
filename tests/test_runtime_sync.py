import sqlite3
import tempfile
import unittest
from pathlib import Path

from fc27.errors import FC27Error
from fc27.runtime import RuntimeManager


REQUIRED = ("coins", "club", "storage", "unassigned", "tradepile")


def item(item_id, card_id, location, tradeable=True, cost=None):
    return {
        "item_id": item_id,
        "card_ea_id": card_id,
        "location": location,
        "tradeable": tradeable,
        "loan_uses_remaining": None,
        "acquisition_cost": cost,
    }


def results(*items, coins=10000):
    grouped = {area: [] for area in REQUIRED}
    for value in items:
        grouped[value["location"]].append(value)
    output = {}
    for area in REQUIRED:
        output[area] = {
            "area": area,
            "page_count": 1,
            "item_count": len(grouped[area]),
            "complete": True,
            "items": grouped[area],
            "listings": [],
        }
    output["coins"]["coin_balance"] = coins
    return output


class RuntimeSyncTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        manager = RuntimeManager(Path(self.directory.name) / "accounts")
        manager.activate({"persona_id": "123", "platform": "ps5", "club_name": "Club"})
        self.runtime = manager.active

    def tearDown(self):
        self.directory.cleanup()

    def commit(self, payload):
        sync_id = self.runtime.begin_sync("login_full")
        for value in payload.values():
            self.runtime.record_sync_part(sync_id, value)
        return self.runtime.commit_full_sync(sync_id, payload, REQUIRED)

    def test_complete_sync_adds_current_items_and_account_state(self):
        summary = self.commit(
            results(
                item(1, 101, "club", cost=500),
                item(2, 102, "storage", tradeable=False),
                item(3, 103, "unassigned"),
                item(4, 104, "tradepile"),
                coins=12345,
            )
        )
        self.assertEqual(summary["item_count"], 4)
        state = self.runtime.account_summary()
        self.assertEqual(state["coin_balance"], 12345)
        self.assertEqual(state["last_full_sync_id"], summary["sync_id"])
        query = self.runtime.query_items({"limit": 2})
        self.assertEqual(query["count"], 2)
        self.assertEqual(query["total_count"], 4)

    def test_second_sync_records_move_remove_add_and_preserves_local_fields(self):
        first = self.commit(results(item(1, 101, "club", cost=500), item(2, 102, "storage")))
        connection = sqlite3.connect(self.runtime.path)
        connection.execute("UPDATE club_items SET protected = 1, acquisition_cost = 777 WHERE item_id = 1")
        connection.commit()
        connection.close()
        second = self.commit(results(item(1, 101, "tradepile", cost=1), item(3, 103, "club")))
        self.assertEqual(second["added"], 1)
        self.assertEqual(second["removed"], 1)
        rows = {row["item_id"]: row for row in self.runtime.query_items({})["items"]}
        self.assertEqual(rows[1]["location"], "tradepile")
        self.assertEqual(rows[1]["protected"], 1)
        self.assertEqual(rows[1]["acquisition_cost"], 777)
        connection = sqlite3.connect(self.runtime.path)
        changes = connection.execute(
            "SELECT item_id, change_type FROM inventory_changes WHERE sync_id = ? ORDER BY item_id",
            (second["sync_id"],),
        ).fetchall()
        connection.close()
        self.assertEqual(changes, [(1, "moved"), (2, "removed"), (3, "added")])
        self.assertNotEqual(first["sync_id"], second["sync_id"])

    def test_identical_sync_reuses_current_club_state_version(self):
        payload = results(
            item(1, 101, "club", cost=500),
            item(2, 102, "storage", tradeable=False),
            coins=10000,
        )
        first = self.commit(payload)
        second = self.commit(payload)

        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertEqual(second["sync_id"], first["sync_id"])
        self.assertEqual(self.runtime.account_summary()["last_full_sync_id"], first["sync_id"])
        connection = sqlite3.connect(self.runtime.path)
        complete_syncs = connection.execute(
            "SELECT sync_id FROM sync_runs WHERE status = 'complete' ORDER BY sync_id"
        ).fetchall()
        connection.close()
        self.assertEqual(complete_syncs, [(first["sync_id"],)])

    def test_coin_only_change_does_not_advance_club_state_version(self):
        first = self.commit(results(item(1, 101, "club"), coins=10000))
        second = self.commit(results(item(1, 101, "club"), coins=9750))

        self.assertFalse(second["changed"])
        self.assertEqual(second["sync_id"], first["sync_id"])
        state = self.runtime.account_summary()
        self.assertEqual(state["last_full_sync_id"], first["sync_id"])
        self.assertEqual(state["coin_balance"], 9750)

    def test_incomplete_sync_does_not_remove_previous_items(self):
        self.commit(results(item(1, 101, "club"), item(2, 102, "storage")))
        sync_id = self.runtime.begin_sync("login_full")
        partial = results(item(1, 101, "club"))["club"]
        self.runtime.record_sync_part(sync_id, partial)
        with self.assertRaises(FC27Error) as context:
            self.runtime.commit_full_sync(sync_id, {"club": partial}, REQUIRED)
        self.runtime.fail_sync(sync_id, context.exception)
        self.assertEqual(context.exception.code, "SYNC_INCOMPLETE")
        self.assertEqual(self.runtime.query_items({})["count"], 2)

    def test_duplicate_item_across_areas_is_rejected(self):
        payload = results(item(1, 101, "club"))
        payload["storage"]["items"].append(item(1, 101, "storage"))
        payload["storage"]["item_count"] = 1
        sync_id = self.runtime.begin_sync("login_full")
        for value in payload.values():
            self.runtime.record_sync_part(sync_id, value)
        with self.assertRaises(FC27Error) as context:
            self.runtime.commit_full_sync(sync_id, payload, REQUIRED)
        self.assertEqual(context.exception.code, "DUPLICATE_ITEM_ACROSS_AREAS")


if __name__ == "__main__":
    unittest.main()
