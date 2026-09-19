import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fc27.actions import ActionDispatcher
from fc27.errors import FC27Error
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


class SquadBridge:
    def __init__(self):
        self.calls = []
        self.formation_id = 9
        self.item_id = 1

    def data(self):
        return {
            "active_squad_id": 1,
            "status": 200,
            "squads": [
                {
                    "squad_id": 1,
                    "name": "Main",
                    "formation": {"id": self.formation_id},
                    "slots": [{"slot_index": 0, "item": {"item_id": self.item_id}}],
                    "tactics": [],
                }
            ],
            "catalog": {},
        }

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "getSquads":
            return {"ok": True, "data": self.data()}
        if method == "saveSquad":
            if params.get("formation_id") is not None:
                self.formation_id = params["formation_id"]
            for update in params.get("slot_updates") or []:
                if update["slot_index"] == 0:
                    self.item_id = update["item_id"]
            return {"ok": True, "data": {"status": 200}}
        return {"ok": True, "data": {"status": 200}}


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
        self.assertEqual(
            self.bridge.calls,
            [
                (
                    "buyNow",
                    {"trade_id": 900, "definition_id": 200, "buy_now_price": 700},
                )
            ],
        )
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
        self.bridge.responses["getTradepile"] = {
            "auctionInfo": [
                {
                    "tradeId": 77,
                    "startingBid": 650,
                    "buyNowPrice": 700,
                    "itemData": {"item_id": 1},
                }
            ]
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

    def test_listing_readback_requires_requested_prices(self):
        self.bridge.responses["getTradepile"] = {
            "auctionInfo": [
                {
                    "tradeId": 77,
                    "startingBid": 600,
                    "buyNowPrice": 750,
                    "itemData": {"item_id": 1},
                }
            ]
        }

        def list_sync(kind):
            listing = {
                "trade_id": 77,
                "item_id": 1,
                "starting_bid": 600,
                "buy_now_price": 750,
                "current_bid": 0,
                "status": "active",
                "expires": 3600,
            }
            return self.commit(
                sync_payload([item(1, 100, "tradepile", 500)], 10000, [listing]),
                kind,
            )

        dispatcher = ActionDispatcher(self.bridge, self.runtime, list_sync)
        with patch("fc27.actions.time.sleep"), self.assertRaisesRegex(
            FC27Error, "has no listing after readback"
        ):
            dispatcher(
                {
                    "action_id": "list-wrong-price",
                    "type": "list_item",
                    "item_id": 1,
                    "starting_bid": 650,
                    "buy_now_price": 700,
                    "duration": 3600,
                }
            )

    def test_delayed_listing_reconciliation_requires_exact_prices(self):
        with self.runtime.connect() as connection:
            for suffix, starting_bid in (("exact", 650), ("wrong", 600)):
                connection.execute(
                    """INSERT INTO action_batches(
                         batch_id, execution_mode, expected_sync_id, created_at, status
                       ) VALUES (?, 'suggest', 1, '2026-09-18T10:00:00Z', 'failed')""",
                    (f"batch-{suffix}",),
                )
                connection.execute(
                    """INSERT INTO actions(
                         action_id, batch_id, sequence_no, action_type,
                         idempotency_key, item_id, params_json, status, error_code
                       ) VALUES (?, ?, 0, 'list_item', ?, 1, ?, 'failed',
                                 'LISTING_READBACK_FAILED')""",
                    (
                        f"list-{suffix}",
                        f"batch-{suffix}",
                        f"key-{suffix}",
                        json.dumps(
                            {
                                "item_id": 1,
                                "starting_bid": starting_bid,
                                "buy_now_price": 700,
                            }
                        ),
                    ),
                )
            connection.execute(
                """INSERT INTO trade_listings(
                     trade_id, item_id, starting_bid, buy_now_price, current_bid,
                     status, last_seen_at
                   ) VALUES (77, 1, 650, 700, 0, 'active',
                             '2026-09-18T10:01:00Z')"""
            )
            connection.commit()

        self.assertEqual(self.runtime.reconcile_listing_actions(), ["list-exact"])
        with self.runtime.connect() as connection:
            exact = connection.execute(
                "SELECT status, result_json FROM actions WHERE action_id = 'list-exact'"
            ).fetchone()
            wrong = connection.execute(
                "SELECT status FROM actions WHERE action_id = 'list-wrong'"
            ).fetchone()
        self.assertEqual(exact["status"], "complete")
        self.assertTrue(json.loads(exact["result_json"])["reconciled"])
        self.assertEqual(wrong["status"], "failed")

    def test_squad_save_requires_hash_and_verifies_readback(self):
        bridge = SquadBridge()
        dispatcher = ActionDispatcher(bridge, self.runtime, lambda kind: None)
        current = dispatcher._read_squad(1)["squad"]
        result = dispatcher(
            {
                "action_id": "squad-1",
                "type": "save_squad",
                "squad_id": 1,
                "expected_squad_hash": current["squad_hash"],
                "formation_id": 8,
                "slot_updates": [],
            }
        )
        self.assertEqual(result["after"]["formation"]["id"], 8)
        with self.assertRaises(FC27Error) as stale:
            dispatcher(
                {
                    "action_id": "squad-2",
                    "type": "save_squad",
                    "squad_id": 1,
                    "expected_squad_hash": current["squad_hash"],
                    "formation_id": 9,
                    "slot_updates": [],
                }
            )
        self.assertEqual(stale.exception.code, "STALE_SQUAD_STATE")


if __name__ == "__main__":
    unittest.main()
