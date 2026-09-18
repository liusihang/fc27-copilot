import tempfile
import unittest
from pathlib import Path

from fc27.actions import ActionDispatcher
from fc27.runtime import RuntimeManager


REQUIRED = ("coins", "club", "storage", "unassigned", "tradepile")


def item(item_id, card_id, location, cost=None):
    return {
        "item_id": item_id,
        "card_ea_id": card_id,
        "location": location,
        "tradeable": True,
        "loan_uses_remaining": -1,
        "acquisition_cost": cost,
    }


def sync_payload(items, coins, listings=None):
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
            "listings": (listings or []) if area == "tradepile" else [],
        }
    output["coins"]["coin_balance"] = coins
    return output


class FakeBridge:
    def __init__(self):
        self.calls = []
        self.responses = {}

    def call(self, method, params):
        self.calls.append((method, params))
        return {"ok": True, "data": self.responses.get(method, {})}


class ActionDispatcherTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        manager = RuntimeManager(Path(self.directory.name) / "accounts")
        manager.activate({"persona_id": "123", "platform": "pc", "club_name": "Club"})
        self.runtime = manager.active
        self.bridge = FakeBridge()
        self.commit(sync_payload([item(1, 100, "club", 500)], 10000), "fixture")

    def tearDown(self):
        self.directory.cleanup()

    def commit(self, payload, kind):
        sync_id = self.runtime.begin_sync(kind)
        for result in payload.values():
            self.runtime.record_sync_part(sync_id, result)
        return self.runtime.commit_full_sync(sync_id, payload, REQUIRED)

    def test_buy_now_identifies_item_and_records_coin_transaction(self):
        def sync_full(kind):
            return self.commit(
                sync_payload(
                    [item(1, 100, "club", 500), item(2, 200, "unassigned", 700)],
                    9300,
                ),
                kind,
            )

        result = ActionDispatcher(self.bridge, self.runtime, sync_full)(
            {
                "action_id": "buy-1",
                "type": "buy_now",
                "trade_id": 900,
                "expected_card_ea_id": 200,
                "max_price": 700,
            }
        )
        self.assertEqual(self.bridge.calls, [("buyNow", {"trade_id": 900, "buy_now_price": 700})])
        self.assertEqual(result["item"]["item_id"], 2)
        self.assertEqual(result["coin_delta"], -700)
        with self.runtime.connect() as connection:
            row = connection.execute("SELECT * FROM coin_transactions").fetchone()
        self.assertEqual(row["kind"], "purchase")
        self.assertEqual(row["price"], 700)
        self.assertEqual(row["coin_delta"], -700)

    def test_move_and_list_verify_full_sync_readback(self):
        def move_sync(kind):
            return self.commit(
                sync_payload([item(1, 100, "tradepile", 500)], 10000), kind
            )

        moved = ActionDispatcher(self.bridge, self.runtime, move_sync)(
            {
                "action_id": "move-1",
                "type": "move_item",
                "item_id": 1,
                "destination": "tradepile",
            }
        )
        self.assertEqual(moved["item"]["location"], "tradepile")

        listing = {
            "trade_id": 77,
            "item_id": 1,
            "starting_bid": 650,
            "buy_now_price": 700,
            "current_bid": 0,
            "status": "active",
            "expires": 3600,
        }

        def list_sync(kind):
            return self.commit(
                sync_payload([item(1, 100, "tradepile", 500)], 10000, [listing]),
                kind,
            )

        listed = ActionDispatcher(self.bridge, self.runtime, list_sync)(
            {
                "action_id": "list-1",
                "type": "list_item",
                "item_id": 1,
                "starting_bid": 650,
                "buy_now_price": 700,
                "duration": 3600,
            }
        )
        self.assertEqual(listed["listing"]["trade_id"], 77)
        self.assertEqual(listed["listing"]["buy_now_price"], 700)


if __name__ == "__main__":
    unittest.main()
